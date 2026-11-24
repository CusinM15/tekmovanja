"""Enkratni backfill: regenerira seed podatke v obstoječi tekmovanja.db, da so
rezultati in rekordi napolnjeni za OBA spola (prej je bug pustil samo dekleta).

Varnostno: pred zagonom naredi makepot kopijo tekmovanja.db.bak.
Poženemo z: ./venv/bin/python _backfill_seed.py
"""
import os
import sqlite3
from pathlib import Path

DB = str(Path(__file__).resolve().parent / "tekmovanja.db")
os.environ["TEKMOVANJA_DB"] = DB

# 1) Pobriši SAMO seed-vrstice (učenci + dogodki + rekordi + štartne liste).
#    Šole in discipline (4 športi) OHRANIMO.
con = sqlite3.connect(DB)
c = con.cursor()
for t in ("start_list_pupils", "start_lists", "competition_events", "records", "pupils"):
    n = c.execute(f"DELETE FROM {t}").rowcount
    print(f"cleared {t}: {n}")
con.commit()
con.close()

# 2) Sproži init_db + _seed_defaults (tečeta ob importu) — re-seeda vse s popravljeno logiko.
import app.main  # noqa: F401

# 3) Preveri, da so zdaj fantje (M) + dekleta (Ž) povsod.
con = sqlite3.connect(DB)
c = con.cursor()
print("\n=== EVENTS po disciplini + spol ===")
for r in c.execute(
    "SELECT d.name, p.gender, COUNT(*) FROM competition_events e "
    "JOIN disciplines d ON d.id=e.discipline_id JOIN pupils p ON p.id=e.pupil_id "
    "GROUP BY d.id, p.gender ORDER BY d.id, p.gender"):
    print(" ", r)
print("=== RECORDS po disciplini + spol ===")
for r in c.execute(
    "SELECT d.name, r.pupil_gender, COUNT(*) FROM records r "
    "JOIN disciplines d ON d.id=r.discipline_id GROUP BY d.id, r.pupil_gender ORDER BY d.id, r.pupil_gender"):
    print(" ", r)
print("\nstevilo eventov:", c.execute("SELECT COUNT(*) FROM competition_events").fetchone()[0])
print("stevilo rekordov:", c.execute("SELECT COUNT(*) FROM records").fetchone()[0])
print("stevilo ucencev:", c.execute("SELECT COUNT(*) FROM pupils").fetchone()[0])
print("stevilo start_list:", c.execute("SELECT COUNT(*) FROM start_lists").fetchone()[0])
con.close()
print("OK")