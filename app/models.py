"""
Data models for the athletics competition management app.

Vsaka entiteta (School, Pupil, Discipline, CompetitionEvent, Record,
StartList) je PYTHON RAZRED, ki nosi vso svojo logiko: pretvorbo v JSON
(to_dict), pa tudi CRUD pomožne metode, da endpointi v main.py ostanejo
tanki in da se vsi objekti obnašajo enako (enaki gumbi, enaka pravila).
"""

from datetime import datetime
from enum import Enum
import re
from typing import Optional, List

from sqlalchemy import (
    Column, Integer, String, Text, Float, DateTime, Boolean,
    ForeignKey, UniqueConstraint, Table,
)
from sqlalchemy.orm import relationship
from sqlalchemy.ext.declarative import declarative_base

Base = declarative_base()


class GradeEnum(str, Enum):
    """Grade identifiers (e.g., '2.r', '3.r')."""
    GRADE_2 = "2.r"
    GRADE_3 = "3.r"


class DisciplineType(str, Enum):
    """Discipline gender: 'M' for males, 'Ž' for females."""
    M = "M"
    Z = "Ž"


class SchoolStatus(str, Enum):
    """Active vs inactive school status."""
    ACTIVE = "active"
    INACTIVE = "inactive"


# =====================================================================
# RAZREDI (vsak objekt ima svojo logiko — to_dict + CRUD pripomočki)
# =====================================================================

class Pupil(Base):
    """Pupil information."""

    __tablename__ = "pupils"

    id = Column(Integer, primary_key=True, index=True)
    first_name = Column(String(100), nullable=False, index=True)
    last_name = Column(String(100), nullable=False, index=True)
    school_id = Column(Integer, ForeignKey("schools.id"), nullable=False, index=True)
    grade = Column(String(20), nullable=False)  # e.g. '2.r' or '3.r'
    gender = Column(String(2), nullable=False)  # 'M' or 'Ž'
    disciplines = Column(String(50), nullable=False, default="", server_default="")  # vpisane discipline (ID-ji 1-4, npr. '1 2'; največ 2)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    school = relationship("School", back_populates="pupils")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "first_name": self.first_name,
            "last_name": self.last_name,
            "school_id": self.school_id,
            "school_name": self.school.name if self.school else None,
            "grade": self.grade,
            "gender": self.gender,
            "disciplines": self.disciplines or "",
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def validate(first_name, last_name, grade, gender) -> Optional[str]:
        """Vrne napako ali None."""
        if not first_name or len(first_name.strip()) < 2:
            return "Ime mora imeti vsaj 2 znaka."
        if not last_name or len(last_name.strip()) < 2:
            return "Priimek mora imeti vsaj 2 znaka."
        if grade not in ("2.r", "3.r"):
            return "Razred mora biti 2.r ali 3.r."
        if gender.upper() not in ("M", "Ž"):
            return "Spol mora biti M ali Ž."
        return None

    def __repr__(self):
        return f"<Pupil {self.id}: {self.first_name} {self.last_name}, {self.grade} {self.gender}>"


def parse_email_list(raw) -> List[str]:
    """Razdeli polje z več e-poštnimi naslovi (ločeni z vejico/podpičjem/presledki/novo vrstico)."""
    if not raw:
        return []
    seen: List[str] = []
    for part in re.split(r"[,;\s]+", str(raw)):
        p = part.strip().rstrip(",").strip().lower()
        if p and p not in seen:
            seen.append(p)
    return seen


class School(Base):
    """School information."""

    __tablename__ = "schools"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False, index=True)
    email = Column(String(200), nullable=False, unique=True)  # Arnes email
    status = Column(String(20), default=SchoolStatus.ACTIVE.value, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    __table_args__ = (
        UniqueConstraint("name", "email", name="uk_school_name_email"),
    )

    pupils = relationship("Pupil", back_populates="school")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "email": self.email,
            "status": self.status,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def validate(name, email) -> Optional[str]:
        if not name or len(name.strip()) == 0:
            return "Naziv šole je obvezen."
        ems = parse_email_list(email)
        if not ems:
            return "Vnesite vsaj en e-poštni naslov."
        for e in ems:
            if "@" not in e or "." not in e.split("@")[-1]:
                return f"E-poštni naslov ni veljaven: {e}"
        return None

    def __repr__(self):
        return f"<School {self.id}: {self.name} ({self.status})>"


class Discipline(Base):
    """Discipline information (field event)."""

    __tablename__ = "disciplines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)  # e.g. 'tek 60m M'
    gender = Column(String(2), nullable=False)  # 'M' or 'Ž'
    applicable_grades = Column(String(100), nullable=False, default="2.r,3.r")
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    events = relationship("CompetitionEvent", back_populates="discipline")
    records = relationship("Record", back_populates="discipline")
    start_lists = relationship("StartList", back_populates="discipline")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "gender": self.gender,
            "applicable_grades": self.applicable_grades,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }

    @staticmethod
    def validate(name, gender) -> Optional[str]:
        if not name or len(name.strip()) == 0:
            return "Naziv discipline je obvezen."
        if gender.upper() not in ("M", "Ž"):
            return "Spol mora biti M ali Ž."
        return None

    def __repr__(self):
        return f"<Discipline {self.id}: {self.name} ({self.gender})>"


class CompetitionEvent(Base):
    """A single event (run) for a pupil in a discipline."""

    __tablename__ = "competition_events"

    id = Column(Integer, primary_key=True, index=True)
    pupil_id = Column(Integer, ForeignKey("pupils.id"), nullable=False, index=True)
    discipline_id = Column(Integer, ForeignKey("disciplines.id"), nullable=False, index=True)
    time = Column(Float, nullable=False)  # time in seconds
    position = Column(Integer, nullable=False)
    notes = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    pupil = relationship("Pupil")
    discipline = relationship("Discipline", back_populates="events")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "pupil_id": self.pupil_id,
            "pupil_name": f"{self.pupil.first_name} {self.pupil.last_name}" if self.pupil else None,
            "pupil_grade": self.pupil.grade if self.pupil else None,
            "pupil_gender": self.pupil.gender if self.pupil else None,
            "school_name": self.pupil.school.name if self.pupil and self.pupil.school else None,
            "discipline_id": self.discipline_id,
            "discipline_name": self.discipline.name if self.discipline else None,
            "time": self.time,
            "position": self.position,
            "notes": self.notes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }

    @staticmethod
    def validate(time, position=None) -> Optional[str]:
        if time is None or time < 0:
            return "Čas mora biti pozitivno število."
        if position is not None and not 1 <= position <= 30:
            return "Mesto mora biti med 1 in 30."
        return None

    def __repr__(self):
        return f"<Event {self.id}: pupil={self.pupil_id}, discipline={self.discipline_id}, time={self.time}s>"


class Record(Base):
    """Historical best records for a discipline. Keeps ties."""

    __tablename__ = "records"

    id = Column(Integer, primary_key=True, index=True)
    discipline_id = Column(Integer, ForeignKey("disciplines.id"), nullable=False, index=True)
    pupil_id = Column(Integer, ForeignKey("pupils.id"), nullable=False, index=True)
    time = Column(Float, nullable=False)
    date_recorded = Column(DateTime, default=datetime.utcnow, nullable=False)
    notes = Column(Text, nullable=True)
    # Posnetek učenca ob nastanku rekorda — da rekord (samostojna tabela) preživi
    # brisanje učencev oz. podatkov in še vedno pokaže ime/razred/šolo.
    pupil_name = Column(String, nullable=True)
    pupil_grade = Column(String, nullable=True)
    pupil_gender = Column(String, nullable=True)
    school_name = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)

    pupil = relationship("Pupil")
    discipline = relationship("Discipline", back_populates="records")

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "discipline_id": self.discipline_id,
            "discipline_name": self.discipline.name if self.discipline else None,
            "pupil_id": self.pupil_id,
            "pupil_name": self.pupil_name or (f"{self.pupil.first_name} {self.pupil.last_name}" if self.pupil else None),
            "pupil_grade": self.pupil_grade or (self.pupil.grade if self.pupil else None),
            "pupil_gender": self.pupil_gender or (self.pupil.gender if self.pupil else None),
            "school_name": self.school_name or (self.pupil.school.name if self.pupil and self.pupil.school else None),
            "time": self.time,
            "date_recorded": self.date_recorded.isoformat() if self.date_recorded else None,
            "notes": self.notes,
        }

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
    grade = Column(String(20), nullable=False)  # e.g. '2.r'
    gender = Column(String(2), nullable=False)  # 'M' ali 'Ž' — tekmovalna skupina (šport×spol×razred)
    event_date = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)

    discipline = relationship("Discipline", back_populates="start_lists")
    pupils = relationship("Pupil", secondary=start_list_pupils)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "discipline_id": self.discipline_id,
            "discipline_name": self.discipline.name if self.discipline else None,
            "grade": self.grade,
            "gender": self.gender,
            "event_date": self.event_date.isoformat() if self.event_date else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "pupil_count": len(self.pupils),
            "pupil_names": [f"{p.first_name} {p.last_name}" for p in self.pupils],
            "pupils": [p.to_dict() for p in self.pupils],
        }

    def __repr__(self):
        return f"<StartList {self.id}: discipline={self.discipline_id}, grade={self.grade}, date={self.event_date}>"


def disciplina_smer(ime):
    """Smer 'boljši' rezultat za disciplino:
    - 'lower'  -> boljši MANJŠI rezultat (tek na progo / čas),
    - 'higher' -> boljši VEČJI rezultat (skok, met / dolžina).
    Prepoznamo po imenu: ime se ZAČNE s 'tek' (npr. 'tek 60m') ali nosi oznako
    proge (60m/100m/400m) => lower; sicer (skok/met/višina/daljina) => higher.
    POZOR: ne smemo testirati 'tek' kot PODNIZ — 'met vorTEKsa' vsebuje 'tek'
    in bi napačno odda 'lower' (met je 'higher').
    """
    n = (ime or "").lower()
    if n.lstrip().startswith("tek"):
        return "lower"
    if __import__("re").search(r'\b\d+\s*m\b', n):
        return "lower"
    return "higher"
