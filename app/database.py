"""
Database setup and session management using SQLAlchemy.
"""

import os
from pathlib import Path
from typing import Optional
from sqlalchemy import create_engine
from sqlalchemy.pool import StaticPool
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, scoped_session
from app.models import Base

# Get database path from settings
from app.settings import app_config

_engine = None


def get_engine():
    """Return ONE shared engine for the whole process.

    A shared engine is required for SQLite: a fresh create_engine() per
    call would give every request its own database (with ":memory:" every
    engine is a *separate* empty database — the classic in-memory pitfall:
    tables created by init_db() would be invisible to get_session()).
    For ":memory:" we use StaticPool so all threads share the single
    in-memory connection."""
    global _engine
    if _engine is None:
        # Trajna baza je VEDNO datoteka v mapi projekta. Ta presega morebiten
        # ":memory:" iz configa, da se podatki ne izgubijo ob osvežitvi/zaprtju.
        project_dir = Path(__file__).resolve().parent.parent
        path = str(project_dir / "tekmovanja.db")
        # StaticPool + check_same_thread=False: ena deljena povezava skozi cel
        # proces. SQLite to obožuje — ni QueuePool limita (5), ki ga je prej
        # izčrpalo po nekaj requestih in app je zmrznil.
        _engine = create_engine(
            f"sqlite:///{path}",
            echo=False,
            future=True,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    return _engine


def get_database_url() -> str:
    """Get database URL from config."""
    path = app_config.sqlite_path if app_config.sqlite_path else ":memory:"
    # Use SQLite with file path if provided, otherwise in-memory
    return f"sqlite:///{path}"


def init_db() -> None:
    """Initialize database by creating tables from all models.

    Podatki se OHRANIJO med zagoni: create_all samo doda manjkajoče tabele,
    nikoli ne briše obstoječih (drop_all je odstranjen namerno).
    """
    from app.models import Base
    engine = get_engine()
    Base.metadata.create_all(engine)


def get_session() -> scoped_session:
    """Get a SQLAlchemy session."""
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    session = SessionLocal()
    return scoped_session(SessionLocal)


# Global session factory
DBSession = get_session()
