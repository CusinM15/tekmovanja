"""
Database setup and session management using SQLAlchemy.
"""

import os
from pathlib import Path
from typing import Optional
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool
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
        # Za teste se lahko pot preusmeri z okoljsko spremenljivko TEKMOVANJA_DB.
        project_dir = Path(__file__).resolve().parent.parent
        path = os.environ.get("TEKMOVANJA_DB") or str(project_dir / "tekmovanja.db")
        # SQLite + NullPool: vsak request dobi svežo povezavo, ki se po uporabi
        # dobesedno ZAPRE (ne vrača v deljen pool). Na več threadih to nikoli
        # ne zamrzne — QueuePool (5+10) se je prej izčrpal po ~15 requestih in
        # server je tiho ZMRZNIL (zato so uporabniku "gumbi ne delali").
        # NullPool = ni deljene povezave = ni deadlocka / ni izčrpanja limita.
        _engine = create_engine(
            f"sqlite:///{path}",
            echo=False,
            future=True,
            poolclass=NullPool,
            connect_args={"check_same_thread": False, "timeout": 30},  # 30s čakanja na ključavnico (SQLite busy)
        )
    return _engine


def get_database_url() -> str:
    """Get database URL from config."""
    path = app_config.sqlite_path if app_config.sqlite_path else ":memory:"
    # Use SQLite with file path if provided, otherwise in-memory
    return f"sqlite:///{path}"


def _migrate_missing_columns(engine) -> None:
    """create_all() doda samo manjkajoče TABELE, ne pa tudi manjkajočih STOLPCEV
    na obstoječi bazi (npr. če je bila baza narejena s starejšo verzijo appa).
    Tu vsak manjkajoč stolpec modela dodamo z ALTER TABLE, da se app ne podre
    s 'no such column' tudi na stari bazi."""
    from sqlalchemy import inspect, text
    from sqlalchemy.types import String, Text, Integer, Float, Boolean, DateTime
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in Base.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            obstojeci = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in obstojeci:
                    continue
                t = col.type
                if isinstance(t, (String,)): sql_type = "VARCHAR(%s)" % (t.length or 255)
                elif isinstance(t, Text): sql_type = "TEXT"
                elif isinstance(t, Integer): sql_type = "INTEGER"
                elif isinstance(t, Float): sql_type = "FLOAT"
                elif isinstance(t, Boolean): sql_type = "BOOLEAN"
                elif isinstance(t, DateTime): sql_type = "DATETIME"
                else: sql_type = "TEXT"
                # SQLite ADD COLUMN dovoli samo KONSTANTNO privzeto vrednost in nikoli
                # CURRENT_TIMESTAMP. Zato: string/število -> DEFAULT + NOT NULL;
                # DateTime (created_at, event_date, ...) -> dodamo kot NULLABLE
                # (stare vrstice imajo NULL, app to obvlada; nove dobi Python default).
                dflt = None
                if col.default is not None:
                    d = getattr(col.default, "arg", col.default)
                    if callable(d):
                        dflt = None
                    elif isinstance(d, str):
                        dflt = "'%s'" % d.replace("'", "''")
                    elif isinstance(d, (int, float, bool)):
                        dflt = str(d)
                elif not col.nullable and not isinstance(t, DateTime):
                    if isinstance(t, (String, Text)): dflt = "''"
                    elif isinstance(t, (Integer, Float, Boolean)): dflt = "0"
                ddl = 'ALTER TABLE "%s" ADD COLUMN "%s" %s' % (table.name, col.name, sql_type)
                if dflt is not None:
                    ddl += " DEFAULT %s" % dflt
                    if not col.nullable:
                        ddl += " NOT NULL"
                try:
                    conn.execute(text(ddl))
                    print(f"[migracija] tabela {table.name}: dodan stolpec {col.name}")
                except Exception as e:
                    print(f"[migracija] OPUSTIMO {table.name}.{col.name}: {e}")


def init_db() -> None:
    """Initialize database by creating tables from all models.

    Podatki se OHRANIJO med zagoni: create_all samo doda manjkajoče tabele,
    nikoli ne briše obstoječih (drop_all je odstranjen namerno). Manjkajoče
    stolpce na stari bazi doda _migrate_missing_columns.
    """
    from app.models import Base
    engine = get_engine()
    Base.metadata.create_all(engine)
    _migrate_missing_columns(engine)


def get_session() -> scoped_session:
    """Get a SQLAlchemy session."""
    engine = get_engine()
    SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False, future=True)
    session = SessionLocal()
    return scoped_session(SessionLocal)


# Global session factory
DBSession = get_session()
