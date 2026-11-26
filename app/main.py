"""
Main FastAPI application entry point.
"""

from fastapi import FastAPI, Depends, HTTPException, Form, File, Query, UploadFile
from fastapi.responses import HTMLResponse, FileResponse, JSONResponse
from fastapi.security import APIKeyHeader
from fastapi.staticfiles import StaticFiles
from typing import List, Optional
import os
import json
from datetime import datetime
from pathlib import Path

from app.database import init_db, get_session
from app.models import (
    School, Discipline, Pupil, CompetitionEvent, Record, StartList,
    SchoolStatus, DisciplineType, GradeEnum, parse_email_list,
)
from app.settings import app_config
from app.services import SchoolService, DisciplineService, PupilService, EventService, RecordService, StartListService

# Initialize database
init_db()

app = FastAPI(
    title="Tekmovanja - Primary School Athletics Manager",
    description="A simple Windows desktop app for managing primary-school athletics competitions.",
    version="0.1.0"
)


@app.exception_handler(Exception)
async def _splosna_napaka(request, exc):
    """Neujeto strežniško izjemo (npr. napaka pri uvozu rekordov, DB integrity)
    spremenimo iz slepe 'Napaka 500' v berljiv JSON s pravim vzrokom, in izpišemo
    traceback v konzolo, da vidimo kaj je bilo narobe. HTTPException (400/404/...)
    ima svoj privzet handler in tu ne vplivamo nanj."""
    import traceback
    traceback.print_exc()
    return JSONResponse(status_code=400, content={
        "detail": f"Napaka na strežniku: {type(exc).__name__}: {exc}",
    })

# Get session dependency — generator, da se session VEDNO zapre po requestu
# (brez tega se QueuePool izčrpa in app po nekaj klicih zmrzne)
def get_db(session_locator=get_session):
    db = session_locator()
    try:
        yield db
    finally:
        db.close()



def _ensure_start_lists(db):
    """Nedestruktivno: za vsako disciplino × applicable grade, ki ŠE NIMA štartne
    liste, jo doda z do 15 učenci pravega spola+razreda. Idempotentno (že obstoječe
    pusti pri miru). Ob zagonu tako doda manjkajoče tudi na obstoječi bazi — s tem
    Windows klon po `git pull` dobi štartne liste brez brisanja podatkov."""
    discs = db.query(Discipline).all()
    for d in discs:
        grades = [g.strip() for g in (d.applicable_grades or "").split(",") if g.strip()]
        obstoj = {(sl.grade, sl.gender) for sl in db.query(StartList).filter(StartList.discipline_id == d.id).all()}
        for grade in grades:
            for spol in ("M", "Ž"):
                if (grade, spol) in obstoj:
                    continue
                kandidati = db.query(Pupil).filter(
                    Pupil.gender == spol, Pupil.grade == grade
                ).order_by(Pupil.id).all()
                if not kandidati:
                    continue
                nova = StartList(discipline_id=d.id, grade=grade, gender=spol, event_date=datetime.now())
                nova.pupils = kandidati[:15]
                db.add(nova)



def _backfill_record_snapshots(db):
    """V obstoječe rekorde zapiše posnetek učenca (ime/razred/šola) — da rekord
    po brisanju učenca oz. podatkov še vedno pokaže ime. Idempotentno."""
    changed = False
    for r in db.query(Record).all():
        if r.pupil and (not r.pupil_name or not r.pupil_grade or not r.pupil_gender):
            r.pupil_name = f"{r.pupil.first_name} {r.pupil.last_name}"
            r.pupil_grade = r.pupil.grade
            r.pupil_gender = r.pupil.gender
            r.school_name = r.pupil.school.name if r.pupil.school else None
            changed = True
    if changed:
        db.commit()


def _seed_defaults():
    """Ob prvem zagonu (prazna baza) napolni šole in discipline, da lahko uporabnik
    takoj preizkusi app.

    UČENCI, REZULTATI, REKORDI (demo podatki) se ustvarijo SAMO v TESTIH:
    ko je okoljska varovalka TEKMOVANJA_SEED=1. V produkciji se podatki vnašajo
    izključno skozi aplikacijo in baza se ohrani — ob vsakem zagonu ob prazni
    tabeli učencev se torej 120 demo učencev NE vstavi znova (tisto je dostikrat
    prisililo Mateja, da jih je po git pullu + run.py spet brisal).
    """
    from app.models import School, Discipline, Pupil, CompetitionEvent, Record, SchoolStatus

    # Direktna seja (ne prek Depends generatorja); jo zapremo v finally.
    db = get_session()
    try:
        # --- Šole (iz prvi_prompt.md / settings.py), idempotentno ---
        obstojece = {s.name for s in db.query(School).all()}
        for ime, email in app_config.default_schools.items():
            if ime not in obstojece:
                db.add(School(name=ime, email=email, status=SchoolStatus.ACTIVE.value))
        db.commit()

        # --- Discipline: privzete ustvarimo SAMO ob prvem zagonu (prazna tabela). ---
        # Če uporabnik disciplino izbriše, se ob restartu NE sme vrniti.
        if db.query(Discipline).count() == 0:
            vse_disc = {d.name: d for d in db.query(Discipline).all()}
            for novo_ime, razredi in app_config.default_disciplines.items():
                if novo_ime in vse_disc:
                    continue  # že obstaja z ikono
                cilj = vse_disc.get(novo_ime)
                gender = "Ž" if novo_ime.rstrip().endswith("♀") else "M"
                # poišči staro ime brez ikone (npr. "tek 60m M" ← "tek 60m ♂")
                staro = None
                if novo_ime.endswith("♂"):
                    staro = novo_ime[:-1].rstrip() + " M"
                    staro = staro.replace(" M", " M")
                elif novo_ime.endswith("♀"):
                    staro = novo_ime[:-1].rstrip() + " Ž"
                staro_alt = None
                if novo_ime.endswith("♂"):
                    staro_alt = novo_ime[:-2].rstrip() + " M"
                elif novo_ime.endswith("♀"):
                    staro_alt = novo_ime[:-2].rstrip() + " ž"
                obstojec = vse_disc.get(staro) or vse_disc.get(staro_alt)
                if obstojec:
                    obstojec.name = novo_ime
                    obstojec.gender = gender
                    obstojec.applicable_grades = ",".join(razredi)
                    vse_disc[novo_ime] = obstojec
                else:
                    nova = Discipline(name=novo_ime, gender=gender,
                                      applicable_grades=",".join(razredi))
                    db.add(nova)
                    vse_disc[novo_ime] = nova
            db.commit()

            # Discipline so 4 športi — nevtralni spol (katerikoli); spol+razred iz vrstice
            # določita tekmovalno skupino (šport × spol × razred).
            for _ime in app_config.default_disciplines:
                _dd = db.query(Discipline).filter(Discipline.name == _ime).first()
                if _dd:
                    _dd.gender = ""
            db.commit()

        # --- Učenci + rezultati + rekordi (demo podatki) ---
        # Sejejo se SAMO v testih (TEKMOVANJA_SEED=1). V produkciji uporabnik vnese
        # učence/rekorde izključno skozi aplikacijo; baza se ohrani (ob prazni tabeli
        # se 120 demo učencev ob vsakem zagonu ne vstavi znova).
        if os.environ.get("TEKMOVANJA_SEED") == "1" and db.query(Pupil).count() == 0:
            sole = {s.name: s.id for s in db.query(School).all()}
            disc = {d.name: d.id for d in db.query(Discipline).all()}

            # 120 učencev: 60 fantov (M) + 60 deklet (Ž) — dovolj, da ima VSAKA
            # disciplina SVOJIH 15 učencev (ne istih 15 za vse discipline).
            base_imena_m = ["Luka", "Jan", "Tim", "Žan", "Gal", "Miha", "Jakob", "Blaž",
                            "Nejc", "Tilen", "Rok", "Matej", "David", "Filip", "Žiga",
                            "Anže", "Jure", "Nik", "Lovro", "Aljaž", "Tadej", "Urban",
                            "Vid", "Lan", "Oskar", "Maj", "Aleks", "Domen", "Boris", "Enej"]
            base_imena_z = ["Ana", "Maja", "Eva", "Nina", "Pia", "Lara", "Zala", "Nika",
                            "Tjaša", "Klara", "Sara", "Ema", "Mia", "Lana", "Hana",
                            "Lucija", "Teja", "Brina", "Neža", "Zoja", "Iza", "Alja",
                            "Ana Marija", "Eva Marija", "Kaja", "Lina", "Maruša", "Ula", "Vita", "Nela"]
            # Ponovi imena za 2. krog (novi priimki → navidez novi učenci)
            imena_m = (base_imena_m * 2)[:60]
            imena_z = (base_imena_z * 2)[:60]
            priimki = ["Zupan", "Horvat", "Rozman", "Jerina", "Mežnar", "Krajnc",
                       "Novak", "Vidmar", "Kovač", "Zajc", "Peršič", "Mlakar",
                       "Šubic", "Golob", "Eržen", "Klemenčič", "Košir", "Rupnik",
                       "Železnik", "Ceglar", "Orožen", "Bogataj", "Kalan", "Štrukelj",
                       "Lah", "Pogačnik", "Vovk", "Dežman", "Juvan", "Turk",
                       "Hribernik", "Resman", "Dolenc", "Pečnik", "Zemljič", "Kosec"]
            # 60 priimkov: 36 vit + ponovi zaostanek
            priimki = (priimki * 2)[:60]
            shole_lista = list(sole.values())

            def _dodaj_ucence(imena, spol, start_index):
                out = []
                for i in range(60):
                    prk = priimki[(start_index + i) % len(priimki)]
                    u = Pupil(
                        first_name=imena[i],
                        last_name=prk + (" II" if i >= 30 else ""),
                        school_id=shole_lista[i % len(shole_lista)],
                        grade=("2.r" if i % 2 == 0 else "3.r"),
                        gender=spol,
                    )
                    db.add(u)
                    out.append(u)
                return out

            ucenci_m = _dodaj_ucence(imena_m, "M", 0)
            ucenci_z = _dodaj_ucence(imena_z, "Ž", 0)
            db.commit()

            # Za VSAKO disciplino: 15 SVOJIH učencev (različen pas) dobi rezultat
            import random
            rnd = random.Random(42)
            from datetime import datetime as _dt

            # Rezultate dobi LE del nastopajočih (ne vsi 15 na disciplino), iz OBEH
            # spolskih bazenov — da ima vsaka tekmovalna skupina (šport × spol × razred
            # = 16) svoje rezultate. Disciplina = šport (spolna nevtralna, gender="");
            # spol učenca iz stolpca določi skupino. Vsak šport dobi svoj pas 15 fantov
            # in 15 deklet (ne istih 15 za vse discipline).
            REZULTATOV_PO_DISCIPLINI = 9
            vse_discipline = list(disc.items())  # 4 športi
            for idx, (disc_ime, disc_id) in enumerate(vse_discipline):
                for bazen in (ucenci_m, ucenci_z):
                    pas = bazen[idx * 15: idx * 15 + 15]
                    for mesto, u in enumerate(pas[:REZULTATOV_PO_DISCIPLINI], start=1):
                        if disc_ime.startswith("skok v daljino") or disc_ime.startswith("met vorteksa"):
                            cas = round(rnd.uniform(2.5, 5.5), 2)
                        else:
                            cas = round(rnd.uniform(9.0, 16.0), 2)
                        db.add(CompetitionEvent(pupil_id=u.id, discipline_id=disc_id,
                                                time=cas, position=mesto))
            db.commit()

            # Rekordi: za VSAKO disciplino × spol × razred (16 skupin)
            from app.models import disciplina_smer
            for disc_ime, disc_id in disc.items():
                _smer = disciplina_smer(disc_ime)
                for spol in ("M", "Ž"):
                    for razred in ("2.r", "3.r"):
                        evts = db.query(CompetitionEvent).filter(
                            CompetitionEvent.discipline_id == disc_id
                        ).all()
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
                            db.add(Record(discipline_id=disc_id, pupil_id=best.pupil_id,
                                          time=best.time, date_recorded=_dt(2026, 5, 18),
                                          pupil_gender=spol,
                                          notes=f"Rekord tekmovanja ({razred}, {'fantje' if spol == 'M' else 'punce'})"))
            db.commit()

            # Štartne liste za vsako disciplino × razred ustvari _ensure_start_lists spodaj
            # (do 15 učencev pravega spola/razreda; deluje tudi na obstoječi bazi).

        # Nedestruktivno: na OBSTOJEČI bazi (Windows) doda manjkajoče štartne liste,
        # da jih uporabnik dobi takoj po `git pull`, brez brisanja podatkov.
        _ensure_start_lists(db)
        _backfill_record_snapshots(db)
        from app.services import _reassign_all_positions
        _reassign_all_positions(db)
        db.commit()

    except Exception as e:
        db.rollback()
        print(f"Seed opozorilo (ni usodno): {e}")
    finally:
        db.close()


# Po inicializaciji baze napolni privzete podatke (samo ob prvem zagonu)
_seed_defaults()

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"

@app.get("/", summary="Home", include_in_schema=False)
async def home():
    """UI stran (static/index.html)."""
    index_file = STATIC_DIR / "index.html"
    if index_file.exists():
        return FileResponse(index_file, media_type="text/html")
    return {"message": "Tekmovanja - Athletics Competition Manager"}

# Static files (CSS/JS so inline v index.html; mount za morebitne dodatke)
app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# === SCHOOL MANAGEMENT ===

@app.get("/schools", response_model=List[dict], summary="List schools")
def list_schools(db=Depends(get_db), include_inactive=False):
    """List all schools. If include_inactive=True, include inactive schools too."""
    schools = db.query(School).all()
    if include_inactive:
        return [s.to_dict() for s in schools]
    # Default: only active schools
    return [s.to_dict() for s in schools if s.status == SchoolStatus.ACTIVE.value]

@app.post("/schools", response_model=dict, summary="Add school")
def add_school(
    name: str = Form(...),
    email: str = Form(...),
    db=Depends(get_db)
):
    """Add a new school (prek SchoolService)."""
    return SchoolService.create(db, name, email)

@app.put("/schools/{school_id}", response_model=dict, summary="Update school")
def update_school(
    school_id: int,
    name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    db=Depends(get_db)
):
    """Update school details (prek SchoolService)."""
    return SchoolService.update(db, school_id, name=name, email=email, status=status)

@app.delete("/schools/{school_id}", response_model=dict, summary="Deactivate school")
def deactivate_school(
    school_id: int,
    db=Depends(get_db)
):
    """Deactivate a school (prek SchoolService). Does not delete data."""
    return SchoolService.deactivate(db, school_id)

@app.delete("/schools", response_model=dict, summary="Reactivate school (bulk reactivate)")
def reactivate_all_inactive(
    db=Depends(get_db)
):
    """Reactivate all inactive schools (prek SchoolService)."""
    return SchoolService.reactivate_all(db)

# === DISCIPLINE MANAGEMENT ===

@app.get("/disciplines", response_model=List[dict], summary="List disciplines")
def list_disciplines(db=Depends(get_db)):
    """List all disciplines."""
    return DisciplineService.all(db)

@app.post("/disciplines", response_model=dict, summary="Add discipline")
def add_discipline(
    name: str = Form(...),
    gender: str = Form("M"),  # default to male
    applicable_grades: Optional[str] = None,  # e.g., "2.r,3.r"
    db=Depends(get_db)
):
    """Add a new discipline (prek DisciplineService)."""
    return DisciplineService.create(db, name, gender, applicable_grades)

@app.put("/disciplines/{discipline_id}", response_model=dict, summary="Update discipline")
def update_discipline(
    discipline_id: int,
    name: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    applicable_grades: Optional[str] = Form(None),
    db=Depends(get_db)
):
    """Update discipline details (prek DisciplineService)."""
    return DisciplineService.update(db, discipline_id, name=name, gender=gender,
                                    applicable_grades=applicable_grades)

@app.delete("/disciplines/{discipline_id}", response_model=dict, summary="Remove discipline (inactivate only)")
def remove_discipline(
    discipline_id: int,
    db=Depends(get_db)
):
    """Remove a discipline (prek DisciplineService). Does not delete historical results."""
    return DisciplineService.remove(db, discipline_id)

# === PUPIL MANAGEMENT ===

@app.get("/pupils", response_model=List[dict], summary="List pupils")
def list_pupils(db=Depends(get_db)):
    """List all pupils."""
    pupils = db.query(Pupil).all()
    return [p.to_dict() for p in pupils]

@app.post("/pupils", response_model=dict, summary="Add pupil")
def add_pupil(
    first_name: str = Form(...),
    last_name: str = Form(...),
    school_id: int = Form(...),
    grade: str = Form("2.r"),  # default
    gender: str = Form("M"),
    db=Depends(get_db)
):
    """Add a new pupil."""
    # Validate inputs
    if not first_name.strip() or len(first_name.strip()) == 0:
        raise HTTPException(status_code=400, detail="First name is required")
    if not last_name.strip() or len(last_name.strip()) == 0:
        raise HTTPException(status_code=400, detail="Last name is required")
    
    # Check school exists
    school = db.query(School).get(school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    # Validate grade and gender
    if not grade or len(grade.strip()) == 0:
        grade = "2.r"
    grade = grade.strip()
    if grade not in ("2.r", "3.r"):
        raise HTTPException(status_code=400, detail="Grade must be 2.r or 3.r")
    
    gender = gender.strip().upper()
    if gender not in ("M", "Ž"):
        raise HTTPException(status_code=400, detail="Gender must be M or Ž")
    
    # Check if pupil already exists for this school+name
    existing = db.query(Pupil).filter(
        (Pupil.school_id == school_id),
        (Pupil.first_name == first_name),
        (Pupil.last_name == last_name),
        (Pupil.grade == grade),
        (Pupil.gender == gender),
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Pupil {first_name} {last_name} already exists in this school")
    
    pupil = Pupil(
        first_name=first_name.strip(),
        last_name=last_name.strip(),
        school_id=school_id,
        grade=grade,
        gender=gender
    )
    db.add(pupil)
    db.commit()
    
    return {
        "id": pupil.id,
        "first_name": pupil.first_name,
        "last_name": pupil.last_name,
        "school_id": pupil.school_id,
        "grade": pupil.grade,
        "gender": pupil.gender,
        "created_at": pupil.created_at.isoformat(),
        "updated_at": pupil.updated_at.isoformat(),
    }

# === EVENT MANAGEMENT ===

@app.post("/events", response_model=dict, summary="Record event for pupil")
def record_event(
    pupil_id: int = Form(...),
    discipline_id: int = Form(...),
    time: float = Form(None),  # seconds
    position: int = Form(None),
    notes: Optional[str] = None,
    db=Depends(get_db)
):
    """Record a single event result (prek EventService)."""
    return EventService.create(db, pupil_id, discipline_id, time, position, notes)

@app.get("/events", response_model=List[dict], summary="List events")
def list_events(db=Depends(get_db)):
    """List all events."""
    return EventService.all(db)


@app.get("/school-points", response_model=List[dict], summary="Točke po šolah")
def school_points(db=Depends(get_db)):
    """Seštevek točk po šolah iz VSEH rezultatov (1. mesto=15, 2.=12, ..., 12.=1)."""
    from app.services import points_for_position
    pts = {}
    for ev in db.query(CompetitionEvent).all():
        if not ev.pupil or not ev.pupil.school:
            continue
        sid = ev.pupil.school.id
        row = pts.setdefault(sid, {"school_id": sid, "school_name": ev.pupil.school.name, "points": 0})
        row["points"] += points_for_position(ev.position)
    return sorted(pts.values(), key=lambda x: (-x["points"], x["school_name"]))

# === RECORD MANAGEMENT ===

@app.post("/check-record", response_model=dict, summary="Check if new time beats record")
def check_record(
    discipline_id: int = Form(...),
    pupil_id: int = Form(...),
    time: float = Form(...),
    db=Depends(get_db)
):
    """
    Check if a new time beats the discipline's record(s) for the pupil.
    Returns whether records should be created (and how many new records),
    and provides the current best record.
    """
    discipline = db.query(Discipline).get(discipline_id)
    pupil = db.query(Pupil).get(pupil_id)
    if not discipline or not pupil:
        raise HTTPException(status_code=404, detail="Discipline or pupil not found")
    
    # Get current records for this discipline
    records = db.query(Record).filter(
        Record.discipline_id == discipline_id,
        Record.pupil_id == pupil_id
    ).order_by(Record.date_recorded).all()
    
    # Best current record (lowest time value)
    current_best = None
    if records:
        current_best = min(records, key=lambda r: r.time)
    
    # Check if new time beats current best
    new_beats_best = False
    new_records_needed = []
    
    if current_best:
        if time < current_best.time - 0.001:  # beat by at least 0.001s (strict)
            new_records_needed.append(current_best)
            new_beats_best = True
        elif abs(time - current_best.time) < 0.001:  # tie within tolerance
            # Retain tied result as additional record
            new_records_needed.append(current_best)
    
    # Create new record(s)
    new_record = None
    if new_beats_best or (current_best and abs(time - current_best.time) < 0.001):
        # Create record for current best (if tied) or for beaten record
        for rec in new_records_needed:
            # For the beaten record, we still create the old record as historical
            # The new event will have the new time
            # Actually, the record should represent the best achieved time
            # If tied, keep both records
            event = db.query(CompetitionEvent).filter(
                CompetitionEvent.pupil_id == pupil_id,
                CompetitionEvent.discipline_id == discipline_id,
                CompetitionEvent.time == time,
                CompetitionEvent.updated_at > rec.date_recorded  # newer event
            ).first() if time < current_best.time else None
            
            # Just check logic; record creation happens after event is recorded
            pass
    
    # Return check result
    return {
        "current_best": {
            "id": current_best.id if current_best else None,
            "time": current_best.time if current_best else None,
            "date_recorded": current_best.date_recorded.isoformat() if current_best else None,
        },
        "new_time": time,
        "beats_best": new_beats_best,
        "records_to_add": len(new_records_needed),
        "message": "Records will be updated based on event results."
    }

@app.post("/create-records", response_model=dict, summary="Create record(s) from events")
def create_records(
    discipline_id: int = Form(...),
    event_ids: List[int] = Query(..., description="List of event IDs to evaluate for records"),
    db=Depends(get_db)
):
    """
    Create historical records for events that achieve or tie the best time
    for their discipline and pupil.
    """
    discipline = db.query(Discipline).get(discipline_id)
    if not discipline:
        raise HTTPException(status_code=404, detail="Discipline not found")
    
    events = db.query(CompetitionEvent).filter(CompetitionEvent.id.in_(event_ids)).all()
    pupils = {e.pupil_id for e in events}
    
    # For each pupil in these events
    records_created = 0
    for event in events:
        pupil = db.query(Pupil).get(event.pupil_id)
        if not pupil:
            continue
        
        # Get current records for this pupil in this discipline
        existing_records = db.query(Record).filter(
            Record.discipline_id == discipline_id,
            Record.pupil_id == pupil.id
        ).all()
        
        # Best time so far
        if existing_records:
            current_best_time = min(r.time for r in existing_records)
        else:
            current_best_time = float('inf')
        
        # Was this event competitive?
        if event.time < current_best_time - 0.001:  # strictly better
            record = Record(
                discipline_id=discipline_id,
                pupil_id=pupil.id,
                time=event.time,
                date_recorded=event.created_at,
            )
            db.add(record)
            records_created += 1
        elif abs(event.time - current_best_time) < 0.001:  # tie
            # Retain tied result as additional record
            record = Record(
                discipline_id=discipline_id,
                pupil_id=pupil.id,
                time=event.time,
                date_recorded=event.created_at,
            )
            db.add(record)
            records_created += 1
    
    db.commit()
    
    return {
        "records_created": records_created,
        "message": f"Created {records_created} new record(s)"
    }

@app.get("/records", response_model=List[dict], summary="List records")
def list_records(db=Depends(get_db)):
    """List all records."""
    records = db.query(Record).all()
    return [r.to_dict() for r in records]


@app.get("/records/csv-template", summary="Prenesi CSV predlogo za rekorde (attachment)")
def record_csv_template_download(db=Depends(get_db)):
    """Vrne prazno CSV predlogo za uvoz rekordov (attachment)."""
    from fastapi.responses import Response
    from app.utils import records_csv_template
    disc = db.query(Discipline).order_by(Discipline.id).all()
    content = records_csv_template(disc)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="predloga_rekordi.csv"'},
    )


@app.get("/records/csv-export", summary="Izvozi vse rekorde v CSV (attachment)")
def record_csv_export(db=Depends(get_db)):
    """Izvozi VSE rekorde v CSV (attachment)."""
    from fastapi.responses import Response
    from app.utils import records_to_csv
    records = db.query(Record).all()
    content = records_to_csv(records)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="rekordi.csv"'},
    )


@app.get("/records/txt-export", summary="Izvozi rekorde v TXT (tabela v CSV kodi)")
def record_txt_export(db=Depends(get_db)):
    """Izvozi rekorde v .txt datoteko, ki vsebuje tabelo v CSV kodi (ločilo ',',
    decimalna pika, UTF-8 BOM na začetku)."""
    from fastapi.responses import Response
    from app.utils import records_to_csv
    records = db.query(Record).all()
    content = records_to_csv(records)
    return Response(
        content=content.encode("utf-8"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="rekordi.txt"'},
    )


def _norm_razred_rekordov(g):
    g = (g or "").strip()
    return {"2": "2.r", "3": "3.r", "2.r": "2.r", "3.r": "3.r",
            "2. razred": "2.r", "3. razred": "3.r"}.get(g, g)


@app.post("/records/csv-import", response_model=dict, summary="Uvozi rekorde iz CSV")
async def record_csv_import(
    file: UploadFile = Form(...),
    db=Depends(get_db)
):
    """Uvozi rekorde iz CSV (delimiter ','). Vsako vrstico poveže z obstoječo
    disciplino (po imenu) in učencem (po imenu+priimku+razredu+spolu); podvojitvene
    preskoči. Vrne število uvoženih in morebitne napake."""
    import csv
    import re
    from io import StringIO
    if file.content_type not in ("text/csv", "application/csv", "text/plain",
                                 "application/vnd.ms-excel", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Datoteka ni CSV (content-type: {})".format(file.content_type))
    content = await file.read()
    text = content.decode("utf-8", errors="replace")

    reader = csv.reader(StringIO(text), delimiter=",")
    rows = list(reader)
    if not rows:
        raise HTTPException(status_code=400, detail="Prazna CSV datoteka.")
    if rows and rows[0]:
        rows[0][0] = rows[0][0].lstrip("\ufeff")

    # Poisči glavo (vrstico s »Disciplina« in »Ime Priimek« / »Učenec«)
    header_idx = None
    for idx, r in enumerate(rows):
        nizki = {c.strip().lower() for c in r if c.strip()}
        ima_ucenca = ("učenec" in nizki or "ucenec" in nizki
                      or ("ime priimek" in nizki
                          or ("ime" in nizki and "priimek" in nizki)))
        if "disciplina" in nizki and ima_ucenca:
            header_idx = idx
            break
    if header_idx is None:
        raise HTTPException(status_code=400, detail="CSV manjka glava: Disciplina,Leto,Ime Priimek,Šola,Razred,Spol,Rezultat")

    def norm_sport(nm):
        s = re.sub(r"[\s]*[♂♀]\s*$", "", (nm or "").strip()).strip().lower()
        # oblikovne različice: "tek na 60m"/"tek na 400m" === "tek 60m"/"tek 400m"
        s = re.sub(r"\btek na\b", "tek", s)
        return s

    vse_disc = db.query(Discipline).all()
    def poisci_disc(norm, gnorm):
        # Najprej po športu; potem po spolu (da se rekord M ne napiše v žensko disciplino).
        match = [d for d in vse_disc if norm_sport(d.name) == norm]
        if not match:
            return None
        for pref in (gnorm, ""):
            for d in match:
                if (d.gender or "") == pref:
                    return d
        return match[0]
    created, skipped = 0, 0
    errors = []
    for row in rows[header_idx + 1:]:
        cells = [((c or "").strip()) for c in row]
        if not any(cells):
            continue
        if len(cells) < 7:
            errors.append("vrstica manj kot 7 stolpcev: " + "|".join(cells))
            continue
        # Novo polje: Disciplina,Leto,Ime Priimek,Šola,Razred,Spol,Rezultat
        sport_raw, leto, ucenec, sola, razred, spol = (
            cells[0], cells[1], cells[2], cells[3], cells[4], cells[5])
        cas = cells[6]
        gspol = (spol or "").strip().upper()
        if gspol == "Z":
            gspol = "Ž"
        gnorm = gspol if gspol in ("M", "Ž") else ""

        # disciplina po športu IN spolu (da se rekord M ne napiše v žensko disciplino)
        disc = poisci_disc(norm_sport(sport_raw), gnorm)
        if not disc:
            errors.append(f"neznana disciplina: {sport_raw}")
            continue
        # sestavi ime+priimek (lahko 'Tim Novak' ali 'Tim,Novak')
        deli = re.split(r"[\s,]+", ucenec.strip())
        if len(deli) < 2:
            errors.append(f"učenec mora imeti ime in priimek: {ucenec}")
            continue
        ime, priimek = deli[0], " ".join(deli[1:])
        q = db.query(Pupil).filter(Pupil.first_name.ilike(ime), Pupil.last_name.ilike(priimek))
        grit = _norm_razred_rekordov(razred)
        if grit:
            q = q.filter(Pupil.grade == grit)
        gspol = (spol or "").strip().upper()
        if gspol == "Z":
            gspol = "Ž"
        if gspol in ("M", "Ž"):
            q = q.filter(Pupil.gender == gspol)
        pupil = q.first()
        if not pupil:
            # Učenca v bazi ni — sami ga ustvarimo iz vrstice, da lahko shranimo rekord.
            osmola = (sola or "").strip()
            school = None
            if osmola:
                school = db.query(School).filter(School.name.ilike(osmola)).first()
                # Variacija imena (npr. "OŠ Tone Čufar" vs. "OŠ Toneta Čufarja"): če
                # šola že obstaja pod drugim imenom, a z istim slug-emailom, jo PONOVNO
                # UPORABIMO namesto da bi ustvarili duplikat (UNIQUE schools.email bi
                # vrgel IntegrityError in pobavil sejo -> PendingRollbackError -> 500).
                base = re.sub(r"[^a-z0-9]+", "-", osmola.lower()).strip("-") or "sola"
                slug_email = f"{base}@example.si"
                if school is None:
                    school = db.query(School).filter(School.email == slug_email).first()
                if school is None:
                    try:
                        school = School(name=osmola, email=slug_email,
                                        status=SchoolStatus.ACTIVE.value)
                        db.add(school)
                        db.flush()
                    except Exception:
                        school = None
            gnorm = gspol if gspol in ("M", "Ž") else "M"
            if school is not None:
                pupil = Pupil(first_name=ime, last_name=priimek, school_id=school.id,
                              grade=grit or (razred or "").strip() or "2.r", gender=gnorm)
                db.add(pupil)
                db.flush()
            else:
                errors.append(f"učenec ni najden: {ucenec} (razred {razred})")
                continue
        try:
            t = float(cas.replace(",", "."))
        except ValueError:
            errors.append(f"neveljaven rezultat: {cas}")
            continue
        if t < 0:
            errors.append(f"negativen rezultat: {cas}")
            continue
        # leto rekorda -> datum (ob neznani konverziji doda napako)
        rec_date = None
        if leto.strip():
            l = leto.strip()
            try:
                if len(l) == 4 and l.isdigit():
                    rec_date = datetime(int(l), 1, 1)
                else:
                    raise ValueError
            except ValueError:
                errors.append(f"neveljavno leto (LLLL): {leto}")
                continue
        # preskoči podvojitve: isti učenec + disciplina + ISTO LETO
        # (spol+razred sta del učenca; dva različna rekorderja z istim časom ostaneta oba;
        # rekord istega učenca v drugem letu se NE šteje za podvojenega)
        if leto.strip():
            yr = int(leto)
            exists = db.query(Record).filter(
                Record.discipline_id == disc.id,
                Record.pupil_id == pupil.id,
                Record.date_recorded >= datetime(yr, 1, 1),
                Record.date_recorded < datetime(yr + 1, 1, 1),
            ).first()
        else:
            exists = db.query(Record).filter(
                Record.discipline_id == disc.id,
                Record.pupil_id == pupil.id,
            ).first()
        if exists:
            skipped += 1
            continue
        db.add(Record(
            discipline_id=disc.id,
            pupil_id=pupil.id,
            time=t,
            date_recorded=rec_date or datetime.utcnow(),
            pupil_name=f"{pupil.first_name} {pupil.last_name}",
            pupil_grade=pupil.grade,
            pupil_gender=pupil.gender,
            school_name=(sola) or (pupil.school.name if pupil.school else None),
        ))
        created += 1
    db.commit()
    return {
        "message": f"Uvoženo rekordov: {created} (preskočenih podvojenih: {skipped}).",
        "ok": len(errors) == 0,
        "errors": errors[:20],
        "created": created,
        "skipped": skipped,
    }

# === START LIST MANAGEMENT ===

@app.post("/start-lists", response_model=dict, summary="Create start list for discipline+grade")
def create_start_list(
    discipline_id: int = Form(...),
    grade: str = Form(...),
    gender: str = Form("M"),
    event_date: str = Form(...),  # ISO format datetime
    pupil_ids: str = Form(""),   # CSV števil učencev, npr. "1,5,9"
    db=Depends(get_db)
):
    """Create a start list for a discipline and grade, linked to pupils."""
    discipline = db.query(Discipline).get(discipline_id)
    ids = [int(x.strip()) for x in pupil_ids.split(",") if x.strip()]
    pupil_list = db.query(Pupil).filter(Pupil.id.in_(ids)).all() if ids else []
    
    if not discipline:
        raise HTTPException(status_code=404, detail="Discipline not found")
    if not pupil_list:
        raise HTTPException(status_code=400, detail="No pupils found for provided IDs")
    
    # Validate grade
    if grade not in ("2.r", "3.r"):
        raise HTTPException(status_code=400, detail="Grade must be 2.r or 3.r")
    
    # Decode event_date to datetime if needed
    from datetime import datetime
    try:
        event_dt = datetime.fromisoformat(event_date.replace('Z', '+00:00'))
    except:
        raise HTTPException(status_code=400, detail="Invalid date format. Use ISO format (YYYY-MM-DDTHH:MM:SS).")
    
    # Create start list linked to pupils
    start_list = StartList(
        discipline_id=discipline_id,
        grade=grade,
        gender=gender,
        event_date=event_dt,
    )
    start_list.pupils = pupil_list
    db.add(start_list)
    db.commit()
    
    return {
        "id": start_list.id,
        "discipline_id": discipline_id,
        "grade": grade,
        "event_date": event_date,
        "pupil_count": len(pupil_list),
        "created_at": start_list.created_at.isoformat(),
    }

@app.get("/start-lists", response_model=List[dict], summary="List start lists")
def list_start_lists(db=Depends(get_db)):
    """List all start lists."""
    lists = db.query(StartList).all()
    return [l.to_dict() for l in lists]

# === SCHOOL INVITATIONS ===


def _csv_template(school_name=None):
    """CSV predloga: 4 športi (ID 1-4), šola enkrat zgoraj, razred 2/3, spol M/Ž,
    stolpec Discipline = ID-ji 1-4 (ločeni z vejico, npr. 1,2,4); skupina = šport×spol×razred.

    Če je podano `school_name`, je ime šole v polju »ŠOLA« ŽE IZPOLNJENO (pošljemo
    vsaki šoli svojo predlogo) — šola naj le preveri, da je pravilno, in popravi
    samo, če ni. Brez imena ostane predloga z napovedjo `<ime svoje šole>`."""
    if school_name:
        sola_vrstica = f"ŠOLA:\n{school_name}\n\n"
        korak_sola = ("2. V polju »ŠOLA« je ime tvoje šole ŽE IZPOLNJENO: "
                      "samo preveri, da je pravilno, in ga popravi SAMO, če ni pravo.\n")
    else:
        sola_vrstica = "ŠOLA:\n<ime svoje šole>\n\n"
        korak_sola = ("2. V polje »ŠOLA« zgoraj vpiši ime svoje šole (SAMO ENKRAT).\n")
    navodila = (
        "*** NAVODILA — KAKO IZPOLNIŠ DATOTEKO ***\n"
        "1. V tej datoteki je VSE ŽE PRIPRAVLJENO — NIČESAR ne spreminjaj (glavo, legendo in navodila pusti pri miru).\n"
        + korak_sola +
        "3. K podatkom učencev (od 20. vrstice naprej) dodaš SAMO vrstice — vsaka vrsta = EN učenec, BREZ praznih vrstic:\n"
        "   Vnesi Ime in priimek (npr. Ana Novak) ter Razred (samo 2 ali 3).\n"
        "   Vnesi še Spol (M ali Ž) in Discipline (ID iz tabele 1-4 ločene s presledkom) — vsak NAJVEČ 2 disciplini.\n"
        "4. Ne spreminjaj glave, legende ali polja »ŠOLA«.\n"
        "5. Shrani datoteko in jo vrni organizatorju.\n"
        "\n"
    )
    return ("\ufeff" + navodila + sola_vrstica +
            "ID,Disciplina (šport)\n"
            "1,tek 60m\n2,tek 400m\n3,skok v daljino\n4,met vorteksa\n\n" +
            "Ime, Priimek, Razred, Spol, Discipline (ID)\n")


# 4 discipline = 4 športi (ID 1-4); spol+razred iz vrstice določita tekmovalno
# skupino (šport × spol × razred). CSV v stolpec Discipline navaja ID-je (1-4).
DEFAULT_DISCIPLINE_ORDER = ["tek 60m", "tek 400m", "skok v daljino", "met vorteksa"]


def _disciplina_po_id(db, disc_id):
    """Vrne disciplino za ID 1-4 (šport); ustvari jo, če še ni v bazi."""
    if not 1 <= disc_id <= 4:
        return None
    ime = DEFAULT_DISCIPLINE_ORDER[disc_id - 1]
    d = db.query(Discipline).filter(Discipline.name == ime).first()
    if not d:
        d = Discipline(name=ime, gender="", applicable_grades="2.r,3.r")
        db.add(d)
        db.flush()
    return d


@app.get("/send-invitations", response_model=dict, summary="Generate CSV template")
def generate_csv_template():
    """Generate blank CSV template for school data collection."""
    # CSV header based on required fields from documents
    # Based on prompt: "attach a blank CSV template" and "explain how to fill it in"
    # ID tabela disciplin (iz baze, da so ID-ji vedno pravilni) — šole vpisujejo ID discipline
    db = get_session()
    try:
        disc = db.query(Discipline).order_by(Discipline.id).all()
        active_schools = db.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()
    finally:
        db.close()

    legenda = "ID,Disciplina (za koga)\n"
    for d in disc:
        spol = "fantje" if d.gender == "M" else "punce"
        legenda += f"{d.id},{d.name} ({spol})\n"
    legenda += "\n"

    csv_content = _csv_template()
    
    # For file download
    from fastapi import File, UploadFile, Form
    # Return JSON with file path? In real app, serve as downloadable file
    # We'll just return as JSON for now; in production would serve file
    
    csv_info = {
        "template": csv_content,
        "active_schools": [s.name + " (" + ", ".join(parse_email_list(s.email)) + ")" for s in active_schools],
        "instructions": "1. Fill in pupil details\n" \
                        "2. Tick applicable disciplines for the grade\n" \
                        "3. Provide event date and results (time and position)\n" \
                        "4. Return via email to organizer"
    }
    return {
        "csv_template": csv_info["template"],
        "active_schools": csv_info["active_schools"],
        "instructions": csv_info["instructions"]
    }


@app.get("/csv-template", summary="Prenesi CSV predlogo (attachment)")
def csv_template_download():
    """Vrne prazno CSV predlogo za šole kot prenos datoteke (attachment) —
    zanesljivo deluje tudi v Firefoxu/LibreWolfu (za razliko od Blob + a.click())."""
    from fastapi.responses import Response
    content = _csv_template()
    return Response(
        content=content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="predloga_ucenci.csv"'},
    )


@app.post("/send-emails", response_model=dict, summary="Send invitations to active schools")
def send_invitations_emails(db=Depends(get_db)):
    """Send email invitations to all active schools. Vsaka šola dobi SVOJO CSV
    predlogo z ŽE izpolnjenim imenom svoje šole v polju »ŠOLA« (šola samo preveri,
    da je ime pravilno, in popravi le, če ni)."""
    from app.utils import send_email

    db_session = db  # reuse (prek Depends, samodejno zaprt)
    active_schools = db_session.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()

    if not active_schools:
        return {"message": "Ni aktivnih šol za pošiljanje.", "ok": False}

    subject = "Stankov memorial — prijava učencev"
    sender = app_config.smtp_from or app_config.smtp_user or "tekmovanja@arne.gov.si"

    # PDF razpisa: uporabnikov (naložen na Nastavitvah) ali, če ga ni, psevdo-razpis.
    from app import nastavitve as _nst
    razpis_priloga = None  # (filename, bytes, mimetype)
    _raz = _nst.get().get("razpis")
    if _raz and _nst.abs_path(_raz).exists():
        try:
            razpis_priloga = ("razpis.pdf", _nst.abs_path(_raz).read_bytes(), "application/pdf")
        except OSError:
            razpis_priloga = None
    if not razpis_priloga:
        from app.utils import razpis_pdf_bytes
        ns = _nst.get()
        razpis_priloga = ("razpis.pdf",
                          razpis_pdf_bytes(title=ns.get("tekmovanje_title", "STANKOV MEMORIAL"),
                                           sola_ime=ns.get("ime_sole", "OSNOVNA ŠOLA TONETA ČUFARJA JESENICE"),
                                           kraj_datum=ns.get("kraj_datum", "")),
                          "application/pdf")

    poslano = 0
    napake = []
    for s in active_schools:
        rec = parse_email_list(s.email)
        if not rec:
            napake.append(f"{s.name}: ni e-poštnega naslova")
            continue
        # vsaka šola svojo predlogo z že izpolnjenim imenom svoje šole
        csv_content = _csv_template(s.name)
        atts = [("prijava_ucencev.csv", csv_content, "text/csv")]
        if razpis_priloga:
            atts.append(razpis_priloga)
        body = ("Pozdravljeni,\n\n"
                "vabimo vas k prijavi učencev na Stankov memorial.\n\n"
                "Izpolnite priloženo CSV datoteko (odprite jo v Excelu) in nam "
                "vrnite izpolnjen CSV s podatki vseh učencev vaše šole. "
                "Na vrhu datoteke so navodila — ime vaše šole je že izpolnjeno, "
                "le preverite, da je pravilno. "
                "V prilogi je tudi razpis tekmovanja.\n\n"
                "Hvala in lep pozdrav,\n" + _posiljatelj_ime())
        try:
            send_email(
                subject=subject,
                sender=sender,
                recipients=rec,
                body=body,
                attachments=atts,
            )
            poslano += 1
        except Exception as e:
            napake.append(f"{s.name}: {_napaka_posiljanja(e)}")

    if napake and poslano == 0:
        return {"message": "Pošiljanje ni uspelo: " + "; ".join(napake), "ok": False}
    if napake:
        return {"message": f"Povabila poslana na {poslano} šol (napake: {'; '.join(napake)}).", "ok": True}
    return {"message": f"Povabila poslana na {poslano} šol.", "ok": True}


def _napaka_posiljanja(e):
    """Človeku prijazno sporočilo ob napaki pošiljanja e-pošte."""
    import socket
    if isinstance(e, socket.gaierror):
        return ("SMTP strežnik ni dosegljiv (DNS napaka). Preveri naslov strežnika "
                f"v Nastavitvah e-pošte ({app_config.smtp_host}:{app_config.smtp_port}) in internetno povezavo.")
    msg = str(e) or type(e).__name__
    return f"Pošiljanje ni uspelo: {msg}"



def _posiljatelj_ime():
    """Ime in priimek pošiljatelja za podpis: najprej iz polja 'Ime in priimek'
    (Nastavitve e-pošte); sicer iz SMTP uporabniškega imena (ime.priimek);
    sicer 'Organizator tekmovanja'."""
    ime = (getattr(app_config, "smtp_sender_name", None) or "").strip()
    if ime:
        return ime
    vir = (app_config.smtp_user or "").strip()
    if not vir:
        return "Organizator tekmovanja"
    ime = " ".join(w.capitalize() for w in vir.replace(".", " ").replace("_", " ").replace("-", " ").split() if w)
    # Fallback naj pokaže le nekaj, kar zgleda kot ime+priimek (vsaj 2 besedi);
    # sicer raje generičen podpis kot grd shortname (npr. "Matcus1").
    if " " not in ime:
        return "Organizator tekmovanja"
    return ime


# === CSV IMPORT ===


def _normalize_commaless_template(text: str):
    """Če je CSV predloga IZGUBILA VEJICE (ločila) — npr. potegnjena skozi
    Excel/urejevalnik, ki vejice 'poje' kot ločila, zato so polja zlepljena:
    'IDDisciplina (šport)', '1tek 60m', 'EnIz Mojstrane2M1 2' — predlogo prevedemo
    nazaj v VEJIČNO obliko, da jo obstoječi parser lahko uvozi.

    Kliče se samo kot FALLBACK (ko vejična glava ni najdena); pravilen vejični CSV
    se ne spreminja. Če ne prepoznamo glave in vsaj enega učenca, vrnemo None."""
    import re
    glava_re = re.compile(r"\bime\b.*\bpriimek\b.*\brazred\b.*\bspol\b")
    data_re = re.compile(
        r"^\s*(?P<ime>.+?)\s*(?P<raz>[23])\s*(?P<spol>[MŽ])\s*"
        r"(?P<d>[1-4](?:\s*[, ]\s*[1-4])?)\s*$"
    )
    ima_glavo = False
    ima_podatke = False
    out = []
    for linija in text.splitlines():
        l = linija.strip()
        if glava_re.search(l.lower()):
            ima_glavo = True
            out.append("Ime, Priimek, Razred, Spol, Discipline (ID)")
            continue
        if l:
            m = data_re.match(l)
            if m:
                delki = re.split(r"\s+", m.group("ime").strip(), maxsplit=1)
                if len(delki) == 2:  # ime + priimek
                    d = re.sub(r"\s+", " ", m.group("d").replace(",", " ")).strip()
                    ima_podatke = True
                    out.append(f"{delki[0]}, {delki[1]}, {m.group('raz')}, "
                               f"{m.group('spol').upper()}, {d}")
                    continue
        out.append(linija)
    return "\n".join(out) if (ima_glavo and ima_podatke) else None


@app.post("/import-csv", response_model=dict, summary="Import completed CSV")
async def import_csv(
    file: UploadFile = Form(...),
    db=Depends(get_db)
):
    """Import CSV file with competition data. Validates and prevents duplicates."""
    # Brskalniki pošljejo text/csv; Excel/Windows včasih druge MIME tipe — sprejmi vse običajne
    if file.content_type not in ("text/csv", "application/csv", "text/plain", "application/vnd.ms-excel", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Datoteka ni CSV (content-type: {})".format(file.content_type))
    
    # Read file content
    content = await file.read()
    text = content.decode("utf-8", errors="replace")
    
    # Parse CSV
    import csv
    from io import StringIO
    
    # Parse the CSV content
    reader = csv.reader(StringIO(text))
    rows = list(reader)
    
    if not rows or len(rows) < 1:
        raise HTTPException(status_code=400, detail="Empty CSV file")
    
    # Odstrani UTF-8 BOM iz prve celice prve vrstice (če obstaja)
    if rows and rows[0]:
        rows[0][0] = rows[0][0].lstrip("\ufeff")

    # Najdi vrstico z dejanskimi angleškimi headerji kode (šola je zdaj zgoraj v polju)
    header_idx = None
    for idx, r in enumerate(rows):
        nizki = {c.strip().lower() for c in r if c.strip()}
        ima_ime = ("first name" in nizki or "ime" in nizki)
        ima_priimek = ("last name" in nizki or "priimek" in nizki)
        ima_razred = ("grade" in nizki or "razred" in nizki)
        ima_spol = ("gender" in nizki or "spol" in nizki)
        if ima_ime and ima_priimek and (ima_razred or ima_spol):
            header_idx = idx
            break
    if header_idx is None:
        # Tolerantnost: če glava ni najdena, je vsebina morda IZGUBILA VEJICE
        # (Excel/urejevalnik 'poje' vejice kot ločila → polja zlepljena). Predlogo
        # normaliziramo nazaj v vejice in ponovno poiščemo glavo/učence. Pravi
        # vejični CSV se ne spremeni.
        _norm = _normalize_commaless_template(text)
        if _norm is not None:
            rows = list(csv.reader(StringIO(_norm)))
            if rows and rows[0]:
                rows[0][0] = rows[0][0].lstrip("\ufeff")
            for idx, r in enumerate(rows):
                nizki = {c.strip().lower() for c in r if c.strip()}
                ima_ime = ("first name" in nizki or "ime" in nizki)
                ima_priimek = ("last name" in nizki or "priimek" in nizki)
                ima_razred = ("grade" in nizki or "razred" in nizki)
                ima_spol = ("gender" in nizki or "spol" in nizki)
                if ima_ime and ima_priimek and (ima_razred or ima_spol):
                    header_idx = idx
                    break
    if header_idx is None:
        raise HTTPException(status_code=400, detail="CSV manjka glava: Ime, Priimek, Razred, Spol, Discipline (ID)")

    # Šola iz polja zgoraj »ŠOLA:« — šolo šole vpišejo SAMO ENKRAT za celo datoteko
    def _norm_grade(g):
        g = g.strip()
        return {"2": "2.r", "3": "3.r"}.get(g, g)

    sola = None
    for i in range(header_idx):
        if not rows[i] or not rows[i][0]:
            continue
        cel0 = (rows[i][0] or "").strip().lstrip("\ufeff")
        low = cel0.lower()
        if low.startswith("šola") or low.startswith("sola"):
            val = cel0
            for oznaka in ("šola:", "šola", "sola:", "sola"):
                if val.lower().startswith(oznaka):
                    val = val[len(oznaka):].strip()
                    break
            if not val and len(rows[i]) > 1:
                val = (rows[i][1] or "").strip()
            if not val and i + 1 < header_idx and rows[i + 1]:
                val = " ".join((c or "").strip() for c in rows[i + 1] if (c or "").strip())
            sola = val or None
            break

    # Headerji poenoteni: mala črka, brez oklepajev (npr. "Discipline (ID)" -> "discipline")
    def _norm(h):
        return h.strip().lower().split("(")[0].strip()

    headers = [_norm(h) for h in rows[header_idx]]

    def col(*keys):
        for k in keys:
            try:
                return headers.index(k)
            except ValueError:
                pass
        return -1

    idx_first = col("first name", "ime"); idx_last = col("last name", "priimek")
    idx_school = col("school", "šola"); idx_grade = col("grade", "razred")
    idx_gender = col("gender", "spol"); idx_disc = col("discipline")
    if -1 in (idx_first, idx_last):
        raise HTTPException(status_code=400, detail="CSV manjka stolpca 'First Name' ali 'Last Name'")
    if idx_school < 0 and not sola:
        raise HTTPException(status_code=400, detail="CSV manjka polje »ŠOLA« zgoraj (šola se vpiše enkrat na vrhu).")

    import re
    from datetime import datetime as _dt_now
    errors = []
    records = []
    start_entries = 0
    start_lists_created = 0
    # Omejitev: vsak učenec nastopa v NAJVEČ 2 disciplina (prvi dve po vrstnem
    # redu v datoteki). Tu štejemo, koliko disciplin že ima vsak učenec; dodatne
    # (3., 4., ...) po uvozu preprosto preskočimo (upoštevamo zgolj prvi dve).
    st_disc_po_ucencu = {}   # pupil_id -> [disc_id, ...] (vrstni red vpisa)

    # Preskoči glavo in slovenski primer vrstico ("Ime,Priimek,Šola...")
    data_rows = rows[header_idx+1:]
    for i, row in enumerate(data_rows, start=header_idx+2):
        if not row or not any(c.strip() for c in row):
            continue
        # Preskoči slovenski primer (prvi celica Ime ali priimek)
        prvi = (row[0].strip().lower() if row else "")
        if prvi in ("ime", "priimek"):
            continue
        if len(row) < 2:  # at least First Name and Last Name
            errors.append(f"Vrstica {i}: premalo stolpcev")
            continue

        # Extract field values
        first_name = row[idx_first].strip()
        last_name = (row[idx_last].strip() if idx_last >= 0 and idx_last < len(row) else "")
        # Šola: če glava nima stolpca School, uporabi šolo iz polja zgoraj (enotna za celo datoteko)
        school_name = (row[idx_school].strip() if idx_school >= 0 and idx_school < len(row) else (sola or ""))
        grade = _norm_grade(row[idx_grade].strip() if idx_grade >= 0 and idx_grade < len(row) else "")
        gender = (row[idx_gender].strip() if idx_gender >= 0 and idx_gender < len(row) else "").upper()

        # Validate required fields
        if not first_name or len(first_name) < 2:
            errors.append(f"Vrstica {i}: neveljavno ime: '{first_name}'")
            continue
        if not last_name or len(last_name) < 2:
            errors.append(f"Vrstica {i}: neveljaven priimek: '{last_name}'")
            continue
        if not grade or grade not in ("2.r", "3.r"):
            errors.append(f"Vrstica {i}: neveljaven razred: '{grade}'. Mora biti 2.r ali 3.r")
            continue
        if gender == "Z":
            gender = "Ž"
        if gender not in ("M", "Ž"):
            errors.append(f"Vrstica {i}: neveljaven spol: '{gender}'. Mora biti M ali Ž")
            continue


        # Get or create school
        school = db.query(School).filter(School.name == school_name).first()
        if not school:
            # Try to match by email or partial name
            partial_match = db.query(School).filter(School.name.like(f"%{school_name}%")).first()
            if partial_match:
                school = partial_match
            else:
                # Create inactive school if doesn't exist
                school = School(name=school_name.strip(), email="", status=SchoolStatus.INACTIVE.value)
                db.add(school)
                db.flush()  # flush, ne commit: cel uvoz je ena transakcija
        if school is None:
            continue  # ni mogoče, a varno
        school_id = school.id

        # Učenec: poišči ali ustvari (isti učenec je lahko v več vrsticah — za več disciplin)
        pupil = db.query(Pupil).filter(
            Pupil.school_id == school_id,
            Pupil.first_name == first_name,
            Pupil.last_name == last_name,
            Pupil.grade == grade,
            Pupil.gender == gender
        ).first()
        if not pupil:
            # Ista oseba, a z drugačnim razredom/spolom -> napaka (verjetno tipkarska napaka)
            ista_oseba = db.query(Pupil).filter(
                Pupil.school_id == school_id,
                Pupil.first_name == first_name,
                Pupil.last_name == last_name
            ).first()
            if ista_oseba:
                errors.append(f"Vrstica {i}: {first_name} {last_name} že obstaja pri šoli '{school_name}', a z drugim razredom/spolom ({ista_oseba.grade}, {ista_oseba.gender}). Popravi vrstico.")
                continue
            pupil = Pupil(
                first_name=first_name,
                last_name=last_name,
                school_id=school_id,
                grade=grade,
                gender=gender
            )
            db.add(pupil)
            db.flush()
            records.append({
                "row": i,
                "first_name": first_name,
                "last_name": last_name,
                "school_id": school_id,
                "grade": grade,
                "gender": gender,
                "pupil_id": pupil.id,
            })

        # Discipline = športi (ID 1-4) iz stolpca Discipline (ločeni z vejico, npr. 1,2,4);
        # tekmovalna skupina = (šport, spol, razred) — spol+razred iz vrstice.
        # Stolpec Discipline: vzamemo VSE celice od Discipline naprej — če je šola
        # vpisala "1,2,4" brez narekovajev, jo csv razbije na več celic; združimo,
        # da uvoz prebere VSE discipline (ne samo prve).
        disc_cell = " ".join((c or "").strip() for c in row[idx_disc:]) if idx_disc >= 0 and idx_disc < len(row) else ""
        disc_ids = []
        for del_id in re.split(r"[,; ]+", disc_cell):
            del_id = del_id.strip()
            if not del_id:
                continue
            try:
                did = int(del_id)
            except ValueError:
                errors.append(f"Vrstica {i}: disciplina '{del_id}' ni številka (ID 1-4).")
                continue
            if not 1 <= did <= 4:
                errors.append(f"Vrstica {i}: neveljaven ID discipline {did} (mora biti 1-4).")
                continue
            disc_ids.append(did)
        if not disc_ids:
            errors.append(f"Vrstica {i}: manjka disciplina — vpiši ID-je (1-4), ločene z vejico npr. 1,2,4.")
            continue
        for disc_id in disc_ids:
            # vsak učenec naj nastopa v NAJVEČ 2 disciplina — upoštevamo prvi dve
            sne = st_disc_po_ucencu.setdefault(pupil.id, [])
            if len(sne) >= 2:
                continue
            sne.append(disc_id)
            disc = _disciplina_po_id(db, disc_id)
            if disc is None:
                errors.append(f"Vrstica {i}: neznan ID discipline {disc_id}.")
                continue
            sl = db.query(StartList).filter(
                StartList.discipline_id == disc.id,
                StartList.grade == grade,
                StartList.gender == gender,
            ).order_by(StartList.event_date.desc(), StartList.id.desc()).first()
            if not sl:
                sl = StartList(discipline_id=disc.id, grade=grade, gender=gender, event_date=_dt_now.utcnow())
                db.add(sl)
                db.flush()
                start_lists_created += 1
            if pupil not in sl.pupils:
                sl.pupils.append(pupil)
                start_entries += 1

    if errors:
        db.rollback()  # sprosti pisalno ključavnico TAKOJ (ne šele po koncu response)
        return {
            "status": "error",
            "errors": errors,
            "records_processed": len(records),
            "message": "Uvoz ni uspel: popravite napake in poskusite znova."
        }

    # Commit: učenec + štartne liste + nastopi v eni transakciji
    db.commit()

    return {
        "status": "success",
        "records_created": len(records),
        "start_entries": start_entries,
        "start_lists_created": start_lists_created,
        "message": f"Uvoženih {len(records)} učencev, dodanih {start_entries} nastopov ({start_lists_created} novih štartnih list)."
    }

# === RESULTS PDF ===

@app.get("/results-pdf")
def generate_results_pdf(event_id: Optional[int] = None, discipline_id: Optional[int] = None,
                        db=Depends(get_db)):
    """Generira PRAVI PDF z rezultati (reportlab) in ga vrne kot prenos."""
    from app.utils import results_pdf_bytes
    from starlette.responses import Response

    if event_id:
        events = db.query(CompetitionEvent).filter(CompetitionEvent.id == event_id).all()
    elif discipline_id:
        events = db.query(CompetitionEvent).filter(CompetitionEvent.discipline_id == discipline_id).all()
    else:
        events = db.query(CompetitionEvent).all()

    schools = db.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()
    pupils = db.query(Pupil).all()
    records = db.query(Record).all()
    from app import nastavitve as _nast
    _s = _nast.get()
    _logo = str(_nast.abs_path(_s["logo"])) if _nast.abs_path(_s["logo"]).exists() else None
    _slika = str(_nast.abs_path(_s["slika"])) if _nast.abs_path(_s["slika"]).exists() else None

    pdf = results_pdf_bytes(events, title=_s["tekmovanje_title"],
                            subtitle="Tekmovanje v atletiki za učence 2. in 3. razreda",
                            schools=schools, pupils=pupils, records=records,
                            logo_path=_logo, slika_path=_slika,
                            rekordi_title=_s["rekordi_title"], kraj_datum=_s.get("kraj_datum", ""))
    from datetime import datetime as _dt
    filename = f"rezultati_{_dt.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store, no-cache, must-revalidate",
            "Pragma": "no-cache",
            "Expires": "0",
        }
    )

@app.post("/send-results", response_model=dict, summary="Email results PDF to active schools")
def email_results(
    db=Depends(get_db)
):
    """Generate results PDF and email to active schools."""
    from app.utils import results_pdf_bytes, send_email
    
    # Get all events (db prihaja prek Depends — samodejno zaprt)
    db_session = db
    events = db_session.query(CompetitionEvent).all()
    
    if not events:
        return {"message": "Ni rezultatov za pošiljanje.", "ok": False}
    
    # Pravi PDF (reportlab) — uvodna stran + rezultati po vseh 16 skupinah
    schools = db_session.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()
    pupils = db_session.query(Pupil).all()
    records = db_session.query(Record).all()
    from app import nastavitve as _nast
    _s = _nast.get()
    _logo = str(_nast.abs_path(_s["logo"])) if _nast.abs_path(_s["logo"]).exists() else None
    _slika = str(_nast.abs_path(_s["slika"])) if _nast.abs_path(_s["slika"]).exists() else None
    pdf = results_pdf_bytes(
        events,
        title=_s["tekmovanje_title"],
        subtitle="Tekmovanje v atletiki za učence 2. in 3. razreda",
        schools=schools,
        pupils=pupils,
        records=records,
        logo_path=_logo,
        slika_path=_slika,
        rekordi_title=_s["rekordi_title"],
        kraj_datum=_s.get("kraj_datum", ""),
    )

    # Get active schools
    active_schools = db_session.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()
    
    subject = "STANKOV MEMORIAL — rezultati"
    sender = app_config.smtp_from or app_config.smtp_user or "tekmovanja@arne.gov.si"
    
    body = ("Pozdravljeni,\n\n"
            "prilagamo rezultate tekmovanja Stankov memorial za vašo šolo.\n\n"
            "Lep pozdrav,\n" + _posiljatelj_ime())
    
    try:
        send_email(
            subject=subject,
            sender=sender,
            recipients=[e for s in active_schools for e in parse_email_list(s.email)],
            body=body,
            attachments=[("results.pdf", pdf, "application/pdf")],
        )
    except Exception as e:
        print(f"Email error: {e}")
        return {"message": _napaka_posiljanja(e), "ok": False}

    return {
        "message": f"Rezultati poslani na {len(active_schools)} šol.",
        "ok": True
    }

# === DATA DELETION ===

@app.get("/delete-data", response_model=dict, summary="Delete competition data")
def delete_data(db=Depends(get_db)):
    """
    Show what will be deleted for confirmation.
    Requires explicit confirmation from endpoint handler (in UI).
    """
    # This is a read-only endpoint showing what WOULD be deleted
    from app.models import Pupil, CompetitionEvent
    
    pupils = db.query(Pupil).all()
    events = db.query(CompetitionEvent).all()
    
    return {
        "message": "Selective data deletion is available.\n"
                   "This action will DELETE all Pupils, CompetitionEvents and StartLists\n"
                   "Schools, Disciplines and Records (recordi) will be preserved.\n"
                   "To execute deletion, call /delete-data/execute endpoint.",
        "count_pupils": len(pupils),
        "count_events": len(events),
        "schools_count": db.query(School).count(),
        "disciplines_count": db.query(Discipline).count(),
        "records_count": db.query(Record).count(),
    }

@app.post("/delete-data/execute", response_model=dict, summary="Execute delete data")
def execute_delete_data(db=Depends(get_db)):
    """
    Delete all competition data (pupils and events) while preserving schools, disciplines, and records.
    Requires confirmation step in UI.
    """
    from app.models import Pupil, CompetitionEvent, Record, StartList
    from sqlalchemy import text
    
    # Double-check user confirmation (in production, UI would have modal confirmation)
    # We'll add a simple confirmation parameter
    # In this API, we check for a confirmation header/token? Not implemented for simplicity
    
    # Get data to delete
    pupils = db.query(Pupil).all()
    events = db.query(CompetitionEvent).all()
    records = db.query(Record).all()
    
    if len(pupils) == 0 and len(events) == 0:
        return {"message": "No competition data to delete"}
    
    # Izbriši učence, njihove rezultate in štartne liste (+ članstva), da po
    # izbrisu ne ostanejo sirote v start_list_pupils in se ponoven CSV uvoz ne
    # zruši (UNIQUE constraint). REKORDE OHRANIMO: rekordi so samostojna tabela
    # (s posnetkom učenca), ki preživi izbris podatkov pred vsakim tekmovanjem.
    db.execute(text("DELETE FROM start_list_pupils"))
    db.query(StartList).delete()
    db.query(CompetitionEvent).delete()
    db.query(Pupil).delete()
    db.commit()
    
    return {
        "message": f"Successfully deleted {len(pupils)} pupil(s), {len(events)} event(s) and start lists. Records preserved."
    }

# === UTILITIES ===

"""
In production, we would import these from app.utils
"""

def send_email(
    subject: str,
    sender: str,
    recipients: List[str],
    body: str,
    attachments: List[tuple] = None,
):
    """
    Send email via SMTP.
    In production, this uses the SMTP settings from app_config.
    For development, this shows how it would work.
    """
    if not recipients or len(recipients) == 0:
        print("No recipients provided")
        return
    
    from email.mime.text import MIMEText
    from email.mime.multipart import MIMEMultipart
    import smtplib
    
    if not app_config.smtp_user or not app_config.smtp_password:
        print("SMTP credentials not configured. Cannot send email.")
        return
    
    # Create message
    msg = MIMEMultipart()
    msg['From'] = sender
    msg['To'] = ", ".join(recipients)
    msg['Subject'] = subject
    
    msg.attach(MIMEText(body, 'plain'))
    
    # Attach files
    if attachments:
        for filename, filepath, content_type in attachments:
            # Filepath should be actual file path or BytesIO
            data = filepath if hasattr(filepath, 'read') else None
            # For simplicity in this example, we skip actual file attachment
            pass
    
    # Send email (in production, would connect to SMTP)
    # For this demo, we just print the message
    print(f"EMAIL TO SENDER: \nFrom: {sender}\nTo: {', '.join(recipients)}\nSubject: {subject}\nBody:\n{body}\n\nAttachments:\n" + 
          ", ".join([f"'{f[0]}' ({f[2]})" for f in attachments]))
    
    return {"status": "queued", "message": "Email sent (demo)"}

def generate_results_pdf(events, discipline_id=None, school_id=None):
    """
    Generate results PDF. In production, use reportlab or weasyprint.
    For demo, returns HTML string.
    """
    from datetime import datetime
    from app.models import Discipline, Pupil
    
    # Include all related data
    event_list = []
    for ev in events:
        pupil = db.query(Pupil).get(ev.pupil_id) if ev.pupil_id else None
        discipl = db.query(Discipline).get(ev.discipline_id) if ev.discipline_id else None
        pupil_name = f"{pupil.first_name} {pupil.last_name}" if pupil else "Unknown"
        disc_name = discipl.name if discipl else "Unknown"
        event_info = f"Pupil: {pupil_name}, Discipline: {disc_name}, Time: {ev.time:.2f}s, Pos: {ev.position}, Notes: {ev.notes}"
        event_list.append(event_info)
    
    title = "Primary School Athletics Competition Results"
    date = datetime.utcnow().strftime("%Y-%m-%d")
    
    content = f"""
    <html>
    <head><title>{title}</title></head>
    <body>
        <h1>{title}</h1>
        <p><strong>Date:</strong> {date}</p>
        <p><strong>Organizer:</strong> Tekmovanja</p>
    <table border="1">
    <tr><th>Pupil</th><th>Discipline</th><th>Time (seconds)</th><th>Position</th><th>Notes</th></tr>
    """
    
    for ev in events:
        pupil = db.query(Pupil).get(ev.pupil_id)
        discipl = db.query(Discipline).get(ev.discipline_id)
        pupil_name = f"{pupil.first_name} {pupil.last_name}"
        disc_name = discipl.name
        row = f'<tr><td>{pupil_name}</td><td>{disc_name}</td><td>{ev.time:.2f}</td><td>{ev.position}</td><td>{ev.notes or ""}</td></tr>'
        content += row + "\n"
    
    content += "</table>"
    content += "<p><em>Generated automatically by Tekmovanja</em></p>"
    
    return content

# === UI CRUD PODPORA (preprost UI — dodaj/uredi/izbriši) ===

@app.put("/pupils/{pupil_id}", response_model=dict, summary="Update pupil")
def update_pupil(
    pupil_id: int,
    first_name: Optional[str] = Form(None),
    last_name: Optional[str] = Form(None),
    school_id: Optional[int] = Form(None),
    grade: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    db=Depends(get_db)
):
    """Update pupil details (prek PupilService)."""
    return PupilService.update(db, pupil_id, first_name=first_name, last_name=last_name,
                               school_id=school_id, grade=grade, gender=gender)


@app.delete("/pupils/{pupil_id}", response_model=dict, summary="Delete pupil")
def delete_pupil(pupil_id: int, db=Depends(get_db)):
    """Delete a pupil and their events/records (prek PupilService)."""
    return PupilService.delete(db, pupil_id)


@app.delete("/events/{event_id}", response_model=dict, summary="Delete event/result")
def delete_event(event_id: int, db=Depends(get_db)):
    """Delete a single competition event (result) — prek EventService."""
    return EventService.delete(db, event_id)


@app.put("/events/{event_id}", response_model=dict, summary="Update event/result")
def update_event(
    event_id: int,
    time: Optional[float] = Form(None),
    position: Optional[int] = Form(None),
    notes: Optional[str] = Form(None),
    discipline_id: Optional[int] = Form(None),
    db=Depends(get_db)
):
    """Update a competition event (result) — prek EventService."""
    return EventService.update(db, event_id, time=time, position=position,
                               notes=notes, discipline_id=discipline_id)


@app.post("/records", response_model=dict, summary="Add record manually")
def add_record(
    discipline_id: int = Form(...),
    pupil_id: int = Form(...),
    time: float = Form(...),
    date_recorded: Optional[str] = Form(None),  # ISO YYYY-MM-DD
    notes: Optional[str] = Form(None),
    db=Depends(get_db)
):
    """Add a record manually (best result)."""
    discipline = db.query(Discipline).get(discipline_id)
    pupil = db.query(Pupil).get(pupil_id)
    if not discipline or not pupil:
        raise HTTPException(status_code=404, detail="Discipline or pupil not found")
    if time < 0:
        raise HTTPException(status_code=400, detail="Time must be positive")

    rec_date = None
    if date_recorded:
        try:
            rec_date = datetime.strptime(date_recorded, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date, use YYYY-MM-DD")

    record = Record(
        discipline_id=discipline_id,
        pupil_id=pupil_id,
        time=float(time),
        date_recorded=rec_date or datetime.utcnow(),
        notes=notes.strip() if notes else None,
    )
    db.add(record)
    db.commit()
    return {
        "id": record.id,
        "discipline_id": record.discipline_id,
        "pupil_id": record.pupil_id,
        "time": record.time,
        "date_recorded": record.date_recorded.isoformat() if record.date_recorded else None,
        "notes": record.notes,
    }


@app.put("/records/{record_id}", response_model=dict, summary="Update record")
def update_record(
    record_id: int,
    time: Optional[float] = Form(None),
    date_recorded: Optional[str] = Form(None),
    notes: Optional[str] = Form(None),
    discipline_id: Optional[int] = Form(None),
    pupil_id: Optional[int] = Form(None),
    db=Depends(get_db)
):
    """Update a record (best result) — prek RecordService."""
    return RecordService.update(db, record_id, time=time, date_recorded=date_recorded,
                                notes=notes, discipline_id=discipline_id, pupil_id=pupil_id)


@app.delete("/records/{record_id}", response_model=dict, summary="Delete record")
def delete_record(record_id: int, db=Depends(get_db)):
    """Delete a record (best result) — prek RecordService."""
    return RecordService.delete(db, record_id)



# ================= NASTAVITVE TEKMOVANJA =================

@app.get("/api/nastavitve", response_model=dict, summary="Nastavitve tekmovanja")
def get_nastavitve():
    """Vrni ime tekmovanja, ime za rekorde in URL-je logotipa/slike (ali flags)."""
    from app import nastavitve
    d = nastavitve.get()
    n_l = d["logo"]; n_s = d["slika"]
    _raz = d.get("razpis")
    return {
        "ime_sole": d.get("ime_sole", ""),
        "tekmovanje_title": d["tekmovanje_title"],
        "rekordi_title": d["rekordi_title"],
        "kraj_datum": d.get("kraj_datum", ""),
        "logo": n_l,
        "slika": n_s,
        "razpis": _raz,
        "razpis_url": f"/static/{_raz}" if (_raz and (nastavitve.abs_path(_raz)).exists()) else None,
        "razpis_obstaja": bool(_raz and (nastavitve.abs_path(_raz)).exists()),
        "logo_url": f"/static/{n_l}" if (nastavitve.abs_path(n_l)).exists() else None,
        "slika_url": f"/static/{n_s}" if (nastavitve.abs_path(n_s)).exists() else None,
    }


@app.post("/api/nastavitve", response_model=dict, summary="Shrani nastavitve tekmovanja")
async def save_nastavitve(
    ime_sole: str = Form(""),
    tekmovanje_title: str = Form(""),
    rekordi_title: str = Form(""),
    kraj_datum: str = Form(""),
    logo: Optional[UploadFile] = File(None),
    slika: Optional[UploadFile] = File(None),
    razpis: Optional[UploadFile] = File(None),
):
    """Shrani imeni + (opcijsko) naložena logotip/slika/razpis (PDF)."""
    from app import nastavitve
    from app.database import get_session
    pod = {k: v.strip() for k, v in [("ime_sole", ime_sole),
                                     ("tekmovanje_title", tekmovanje_title),
                                     ("rekordi_title", rekordi_title),
                                     ("kraj_datum", kraj_datum)] if v and v.strip()}
    # mapa za naložene slike MORA obstajati PRED pisanjem (na svežem checkoutu ni v gitu)
    from app import nastavitve as _nst
    (_nst.abs_path("uploads")).mkdir(parents=True, exist_ok=True)
    # shrani naložene slike
    import uuid, re as _re
    for key, up in (("logo", logo), ("slika", slika), ("razpis", razpis)):
        if up is None:
            continue
        data = await up.read()
        if not data:
            continue
        ext = (up.filename or "").rsplit(".", 1)[-1].lower() or "png"
        ext = _re.sub(r"[^a-z0-9]", "", ext) or "png"
        name = f"{key}_{uuid.uuid4().hex[:8]}.{ext}"
        path = nastavitve.abs_path("uploads") / name
        path.write_bytes(data)
        pod[key] = f"uploads/{name}"
    nastavitve.save_update(pod)
    return get_nastavitve()


@app.get("/nastavitve-defaults-slike", summary="Obstajajo privzete slike?", include_in_schema=False)
def privzete_slike():
    from app import nastavitve
    return {"ok": nastavitve.obstajajo_privzete()}


@app.get("/api/smtp", response_model=dict, summary="Get SMTP settings")
def get_smtp_settings_api(db=Depends(get_db)):
    """Vrni shranjene SMTP nastavitve. Geslo vrnemo (lokalna app), da ga obrazec
    prikaže maskirano (******) in ga uporabnik lahko pokaže s klikom na 👁."""
    return {
        "host": app_config.smtp_host,
        "port": app_config.smtp_port,
        "user": app_config.smtp_user or "",
        "from_addr": app_config.smtp_from or "",
        "sender_name": app_config.smtp_sender_name or "",
        "password": app_config.smtp_password or "",
        "has_password": bool(app_config.smtp_password),
    }


@app.post("/api/smtp", response_model=dict, summary="Save SMTP settings")
def save_smtp_settings(
    host: str = Form("smtp.arnes.si"),
    port: int = Form(587),
    user: str = Form(...),
    password: str = Form(""),
    from_addr: str = Form(None),
    sender_name: str = Form(None),
    tls: int = Form(1),
    db=Depends(get_db)
):
    """Save SMTP credentials (stored locally, never in git).

    Če je password prazen, se ohrani obstoječi (label: "pusti prazno za
    nespremenjeno"); če ni shranjenega gesla, pa vrnemo napako."""
    app_config.smtp_host = host.strip()
    app_config.smtp_port = port
    app_config.smtp_tls = bool(tls)
    app_config.smtp_from = (from_addr or "").strip() or None
    app_config.smtp_sender_name = (sender_name or "").strip() or None
    if password:
        app_config.set_smtp_credentials(user.strip(), password)
    else:
        app_config.smtp_user = user.strip()
        if not app_config.smtp_password:
            return JSONResponse(
                status_code=400,
                content={"detail": "Vnesite geslo (ali shranite že nastavljeno geslo z vnosom novega)."},
            )
        app_config._save_config(app_config._full_config())
    return {"message": "Nastavitve e-pošte so shranjene."}


@app.get("/start-lists/{start_list_id}/pdf")
def start_list_pdf(start_list_id: int, db=Depends(get_db)):
    """Pravi PDF s štartno listo."""
    from app.utils import start_list_pdf_bytes
    from starlette.responses import Response

    start_list = db.query(StartList).get(start_list_id)
    if not start_list:
        raise HTTPException(status_code=404, detail="Start list not found")
    discipline_name = start_list.discipline.name if start_list.discipline else "—"
    from app import nastavitve as _nst
    _s = _nst.get()
    _logo = str(_nst.abs_path(_s["logo"])) if _nst.abs_path(_s["logo"]).exists() else None
    _slika = str(_nst.abs_path(_s["slika"])) if _nst.abs_path(_s["slika"]).exists() else None
    pdf = start_list_pdf_bytes(
        start_list, start_list.pupils, discipline_name, start_list.grade,
        ime_sole=_s.get("ime_sole", ""),
        tekmovanje_title=_s.get("tekmovanje_title", "Tekmovanje v atletiki"),
        logo_path=_logo, slika_path=_slika,
    )
    filename = f"startna_lista_{start_list_id}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
    )


@app.delete("/start-lists/{start_list_id}", response_model=dict, summary="Delete start list")
def delete_start_list(start_list_id: int, db=Depends(get_db)):
    """Delete a start list (prek StartListService)."""
    return StartListService.delete(db, start_list_id)


@app.get("/best-results", response_model=List[dict], summary="Best results per discipline+grade")
def best_results(db=Depends(get_db)):
    """Najboljši rezultati, združeni po disciplini IN razredu.

    Za vsako disciplino+razred ena vrstica z najboljšim časom; če imata dva ali
    več učencev enak najboljši čas, so vsi v isti vrstici.
    """
    records = db.query(Record).all()
    # Grupiraj po (discipline_id, pupil.grade)
    groups = {}
    for r in records:
        if not r.pupil or not r.discipline:
            continue
        key = (r.discipline_id, r.pupil.grade)
        groups.setdefault(key, []).append(r)

    out = []
    for (disc_id, grade), recs in groups.items():
        best_time = min(r.time for r in recs)
        best = [r for r in recs if abs(r.time - best_time) < 0.001]
        discipline = recs[0].discipline
        out.append({
            "discipline_id": disc_id,
            "discipline_name": discipline.name if discipline else "—",
            "grade": grade,
            "time": best_time,
            "pupils": [f"{r.pupil.first_name} {r.pupil.last_name}" for r in best],
            "record_ids": [r.id for r in best],
        })

    # Uredi po disciplini, nato razredu
    out.sort(key=lambda x: (x["discipline_name"], x["grade"]))
    return out


# === HEALTH CHECK ===

@app.get("/health")
def health():
    """Health check endpoint."""
    try:
        # Check database connectivity
        session = get_session()
        try:
            schools = session.query(School).first()
        finally:
            session.close()
        return {"status": "healthy", "message": "Database connected"}
    except Exception as e:
        return {"status": "unhealthy", "error": str(e)}
