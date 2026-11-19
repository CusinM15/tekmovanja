"""
Data models for the athletics competition management app.
"""

from datetime import datetime
from enum import Enum
from typing import Optional, List
from pydantic import BaseModel

# Import from SQLAlchemy ORM
try:
    from sqlalchemy import Column, Integer, String, Text, Float, DateTime, Boolean, ForeignKey, UniqueConstraint, Table
    from sqlalchemy.orm import relationship
    from sqlalchemy.ext.declarative import declarative_base
except ImportError:
    # Fallback for testing without SQLAlchemy
    from typing import Any
    Base = object

Base = declarative_base()


class GradeEnum(str, Enum):
    """Grade identifiers (e.g., '2.r', '3.r')."""
    pass


class DisciplineType(str, Enum):
    """Discipline gender: 'M' for males, 'Ž' or 'ž' for females."""
    M = "M"
    Z = "Ž"


class SchoolStatus(str, Enum):
    """Active vs inactive school status."""
    ACTIVE = "active"
    INACTIVE = "inactive"


class Pupil(Base):
    """Pupil information."""
    __tablename__ = "pupils"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False, index=True)
    last_name = Column(String(100), nullable=False, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False, index=True)
    grade = Column(String(20), nullable=False)  # e.g., '2.r' or '3.r'
    gender = Column(String(2), nullable=False)  # 'M' or 'Ž'
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    school = relationship("School")

    def __repr__(self):
        return f"<Pupil {self.id}: {self.first_name} {self.last_name}, {self.grade} {self.gender}>"


class School(Base):
    """School information."""
    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    email = Column(String(200), nullable=False, unique=True)  # Arnes email account
    status = Column(String(20), default=SchoolStatus.ACTIVE.value, nullable=False)  # active/inactive
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        # Unique constraint for school name + email combination
        UniqueConstraint("name", "email", name="uk_school_name_email"),
    )

    def __repr__(self):
        return f"<School {self.id}: {self.name} ({self.status})>"


class Discipline(Base):
    """Discipline information (field event)."""
    __tablename__ = "disciplines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)  # e.g., 'tek 60m M'
    gender = Column(String(2), nullable=False)  # 'M' or 'Ž' for applicable gender
    applicable_grades = Column(String(100), nullable=False, default="2.r,3.r")  # comma-separated list
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    def __repr__(self):
        return f"<Discipline {self.id}: {self.name} ({self.gender})>"


class CompetitionEvent(Base):
    """A single event (run) for a pupil in a discipline."""
    __tablename__ = "competition_events"

    id = Column(Integer, primary_key=True, index=True)
    pupil_id = Column(Integer, ForeignKey("pupils.id"), nullable=False, index=True)
    discipline_id = Column(Integer, ForeignKey("disciplines.id"), nullable=False, index=True)
    time = Column(Float, nullable=False)  # time in seconds (decimal), e.g., 10.23
    position = Column(Integer, nullable=False)  # final placing
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    pupil = relationship("Pupil")
    discipline = relationship("Discipline")

    def __repr__(self):
        return f"<Event {self.id}: pupil={self.pupil_id}, discipline={self.discipline_id}, time={self.time}s>"


class Record(Base):
    """Historical best records for a discipline. Keeps ties."""
    __tablename__ = "records"

    id = Column(Integer, primary_key=True, index=True)
    discipline_id = Column(Integer, ForeignKey("disciplines.id"), nullable=False, index=True)
    pupil_id = Column(Integer, ForeignKey("pupils.id"), nullable=False, index=True)
    time = Column(Float, nullable=False)  # best time in seconds
    date_recorded = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text, nullable=True)
    # Historical records never change; new ties get new records
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    pupil = relationship("Pupil")
    discipline = relationship("Discipline")

    def __repr__(self):
        return f"<Record {self.id}: discipline={self.discipline_id}, pupil={self.pupil_id}, time={self.time}s>"


# Povezovalna tabela: štartna lista <-> učenci
start_list_pupils = Table(
    "start_list_pupils",
    Base.metadata,
    Column("start_list_id", Integer, ForeignKey("start_lists.id"), primary_key=True),
    Column("pupil_id", Integer, ForeignKey("pupils.id"), primary_key=True),
)


class StartList(Base):
    """Start list for a discipline and grade combination."""
    __tablename__ = "start_lists"

    id = Column(Integer, primary_key=True, index=True)
    discipline_id = Column(Integer, ForeignKey("disciplines.id"), nullable=False, index=True)
    grade = Column(String(20), nullable=False)  # e.g., '2.r'
    event_date = Column(DateTime, nullable=False)  # when the start list was created
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    discipline = relationship("Discipline")
    pupils = relationship("Pupil", secondary=start_list_pupils)

    # Unique constraint: one start list per discipline+grade per date
    __table_args__ = (
        # UniqueConstraint("discipline_id", "grade", "event_date", name="uk_startlist_discipline_grade_date"),
    )

    def __repr__(self):
        return f"<StartList {self.id}: discipline={self.discipline_id}, grade={self.grade}, date={self.event_date}>"
