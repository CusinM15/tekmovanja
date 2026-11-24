"""Enkratni popravek obstoječe tekmovanja.db: za met vorteksa in skok v daljino
(disciplina_smer == 'higher', večji == boljši) morajo biti:
  - mesta razvrščena padajoče (najdaljši 1. mesto),
  - rekord = najDALJŠI rezultat (prej seed ni upošteval smeri in je vzel najmanjši).

Tek (lower, manjši == boljši) ostane nespremenjen.

Poženemo: ./venv/bin/python _fix_throw_direction.py
"""
import os
import sqlite3
from pathlib import Path

DB = str(Path(__file__).resolve().parent / "tekmovanja.db")
os.environ["TEKMOVANJA_DB"] = DB

from app.database import get_session, get_engine
from app.models import Discipline, CompetitionEvent, Pupil, Record, disciplina_smer
from app.services import _reassign_all_positions

db = get_session()
try:
    # 1) mesta preračunamo po novi logiki (higher → padajoče)
    _reassign_all_positions(db)
    db.commit()

    # 2) rezultati: za VSAKO disciplino × spol × razred poišči najboljšega PO SMERI
    db.query(Record).delete()  # seed rekordi so bili narobe za skok/met — vse presejemo
    db.commit()
    disc = {d.id: d.name for d in db.query(Discipline).all()}
    porabljeni = 0
    for disc_id, name in disc.items():
        _smer = disciplina_smer(name)
        evts = db.query(CompetitionEvent).filter(CompetitionEvent.discipline_id == disc_id).all()
        for spol in ("M", "Ž"):
            for razred in ("2.r", "3.r"):
                best = None
                for e in evts:
                    pp = db.query(Pupil).get(e.pupil_id)
                    if pp and pp.grade == razred and pp.gender == spol:
                        if best is None:
                            best = e
                        elif (_smer == "higher" and e.time > best.time) or \
                             (_smer == "lower" and e.time < best.time):
                            best = e
                if best:
                    db.add(Record(
                        discipline_id=disc_id, pupil_id=best.pupil_id, time=best.time,
                        date_recorded=best.created_at,
                        pupil_name=f"{best.pupil.first_name} {best.pupil.last_name}",
                        pupil_grade=razred, pupil_gender=spol,
                        school_name=best.pupil.school.name if best.pupil.school else None,
                        notes=f"Rekord tekmovanja ({razred}, {'fantje' if spol == 'M' else 'punce'})"))
                    porabljeni += 1
    db.commit()
    print(f"rekordov znova ustvarjenih: {porabljeni}")
finally:
    db.close()

# Preverba
con = sqlite3.connect(DB); c = con.cursor()
for name in ("met vorteksa", "skok v daljino", "tek 60m"):
    print("\n==", name)
    for r in c.execute(
        "SELECT r.pupil_gender,r.pupil_grade,r.time FROM records r "
        "JOIN disciplines d ON d.id=r.discipline_id WHERE d.name=? ORDER BY r.pupil_gender,r.pupil_grade",
        (name,)):
        print("  record:", r)
    print("  positions (M 2.r):", list(c.execute(
        "SELECT e.time,e.position FROM competition_events e "
        "JOIN disciplines d ON d.id=e.discipline_id JOIN pupils p ON p.id=e.pupil_id "
        "WHERE d.name=? AND p.gender='M' AND p.grade='2.r' ORDER BY e.time", (name,))))
con.close()
print("\nOK")