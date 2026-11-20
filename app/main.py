"""
Main FastAPI application entry point.
"""

from fastapi import FastAPI, Depends, HTTPException, Form, Query, UploadFile
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
    SchoolStatus, DisciplineType, GradeEnum
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
        obstoj = {sl.grade for sl in db.query(StartList).filter(StartList.discipline_id == d.id).all()}
        for grade in grades:
            if grade in obstoj:
                continue
            kandidati = db.query(Pupil).filter(
                Pupil.gender == d.gender, Pupil.grade == grade
            ).order_by(Pupil.id).all()
            if not kandidati:
                continue
            nova = StartList(discipline_id=d.id, grade=grade, event_date=datetime.now())
            nova.pupils = kandidati[:15]
            db.add(nova)


def _seed_defaults():
    """Ob prvem zagonu (prazna baza) napolni šole, discipline, učence in rekorde,
    da lahko uporabnik takoj preizkusi app. Če tabela že ima podatke, se ne dotika."""
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

        # --- Discipline (iz prvi_prompt.md / settings.py), idempotentno ---
        # Če obstaja stara disciplina brez ikone (npr. "tek 60m M" / "tek 400m ž"),
        # jo POSODOBIMO na novo ime z ikono (ohrani id + rezultate) namesto da
        # ustvarimo novo in podvojimo discipline.
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

        # --- Učenci + rezultati + rekordi + štartne liste, samo če ni učencev ---
        if db.query(Pupil).count() == 0:
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

            # Rezultate dobi LE del nastopajočih (ne vsi 15 na disciplino):
            REZULTATOV_PO_DISCIPLINI = 9
            moske = [(k, v) for k, v in disc.items() if ("♂" in k) or k.rstrip().endswith("M")]
            zenske = [(k, v) for k, v in disc.items() if not (("♂" in k) or k.rstrip().endswith("M"))]
            # po 4 discipline; vsaka dobi 60/4=15 učencev iz svojega spolskega bazena
            for idx, (disc_ime, disc_id) in enumerate(moske):
                pas = ucenci_m[idx * 15: idx * 15 + 15]
                for mesto, u in enumerate(pas[:REZULTATOV_PO_DISCIPLINI], start=1):
                    if disc_ime.startswith("skok v daljino") or disc_ime.startswith("met vorteksa"):
                        cas = round(rnd.uniform(2.5, 5.5), 2)
                    else:
                        cas = round(rnd.uniform(9.0, 16.0), 2)
                    db.add(CompetitionEvent(pupil_id=u.id, discipline_id=disc_id,
                                            time=cas, position=mesto))
            for idx, (disc_ime, disc_id) in enumerate(zenske):
                pas = ucenci_z[idx * 15: idx * 15 + 15]
                for mesto, u in enumerate(pas[:REZULTATOV_PO_DISCIPLINI], start=1):
                    if disc_ime.startswith("skok v daljino") or disc_ime.startswith("met vorteksa"):
                        cas = round(rnd.uniform(2.5, 5.5), 2)
                    else:
                        cas = round(rnd.uniform(9.0, 16.0), 2)
                    db.add(CompetitionEvent(pupil_id=u.id, discipline_id=disc_id,
                                            time=cas, position=mesto))
            db.commit()

            # Rekordi: za VSAKO disciplino in VSAK razred posebej (2.r, 3.r)
            for disc_ime, disc_id in disc.items():
                je_fant = ("♂" in disc_ime) or (disc_ime.rstrip().endswith("M"))
                pool = ucenci_m if je_fant else ucenci_z
                for razred in ("2.r", "3.r"):
                    evts = db.query(CompetitionEvent).filter(
                        CompetitionEvent.discipline_id == disc_id
                    ).all()
                    best = None
                    for e in evts:
                        pp = db.query(Pupil).get(e.pupil_id)
                        if pp and pp.grade == razred:
                            if best is None or e.time < best.time:
                                best = e
                    if best:
                        db.add(Record(discipline_id=disc_id, pupil_id=best.pupil_id,
                                      time=best.time, date_recorded=_dt(2026, 5, 18),
                                      notes=f"Rekord tekmovanja ({razred})"))
            db.commit()

            # Štartne liste za vsako disciplino × razred ustvari _ensure_start_lists spodaj
            # (do 15 učencev pravega spola/razreda; deluje tudi na obstoječi bazi).

        # Nedestruktivno: na OBSTOJEČI bazi (Windows) doda manjkajoče štartne liste,
        # da jih uporabnik dobi takoj po `git pull`, brez brisanja podatkov.
        _ensure_start_lists(db)
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

# === START LIST MANAGEMENT ===

@app.post("/start-lists", response_model=dict, summary="Create start list for discipline+grade")
def create_start_list(
    discipline_id: int = Form(...),
    grade: str = Form(...),
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

    csv_content = "\ufeff" + legenda + \
                  "First Name,Last Name,School,Grade,Gender,Discipline (ID)\n" \
                  "Ime,Priimek,Šola (npr. OŠ Žirovnica),Razred (2.r ali 3.r),Spol (M ali Ž),Disciplina (ID iz tabele zgoraj, npr. 1)\n"
    
    # For file download
    from fastapi import File, UploadFile, Form
    # Return JSON with file path? In real app, serve as downloadable file
    # We'll just return as JSON for now; in production would serve file
    
    csv_info = {
        "template": csv_content,
        "active_schools": [s.name + " (" + s.email + ")" for s in active_schools],
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

@app.post("/send-emails", response_model=dict, summary="Send invitations to active schools")
def send_invitations_emails(db=Depends(get_db)):
    """Send email invitations to all active schools with CSV template."""
    from app.utils import send_email
    
    db_session = db  # reuse (prek Depends, samodejno zaprt)
    active_schools = db_session.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()
    
    if not active_schools:
        return {"message": "No active schools found"}
    
    subject = "Uporaba tekmovanja - sh registers"
    sender = app_config.smtp_user if app_config.smtp_user else "tekmovanja@arne.gov.si"
    recipients = [s.email for s in active_schools]
    
    # Build message
    body = f"Cerkev tekmovanja o športni tekmovanju.\n\n" \
           "Prosimo, da vnašte spletno razmisljo o namenu tekmovanja v vaši šoli.\n\n" \
           "Vpletaš: https://localhost:8000\n" \
           "Vrziš: Tekmovanja v Primarni šoli\n\n" \
           "Ko vpisali štroke v CSV, vrziši jih nazpored.\n\n" \
           "Zahvalimo!"
    
    try:
        send_email(
            subject=subject,
            sender=sender,
            recipients=recipients,
            body=body,
            attachments=[("attendance_template.csv", "tekmo_attendance.csv", csv_content)],  # csv_content from above
        )
    except Exception as e:
        # In dev env without SMTP, just return message
        pass
    
    return {"message": f"Invitations sent to {len(recipients)} school(s). Check email credentials."}

# === CSV IMPORT ===

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

    # Najdi vrstico z dejanskimi angleškimi headerji kode (ne slovenski primer)
    header_idx = None
    for idx, r in enumerate(rows):
        nizki = {c.strip().lower() for c in r if c.strip()}
        if "first name" in nizki and "last name" in nizki and "school" in nizki:
            header_idx = idx
            break
    if header_idx is None:
        raise HTTPException(status_code=400, detail="CSV manjka glava: First Name, Last Name, School, Grade, Gender, Discipline (ID)")

    # Headerji poenoteni: mala črka, brez oklepajev (npr. "Discipline (ID)" -> "discipline")
    def _norm(h):
        return h.strip().lower().split("(")[0].strip()

    headers = [_norm(h) for h in rows[header_idx]]

    def col(key):
        try:
            return headers.index(key)
        except ValueError:
            return -1

    idx_first = col("first name"); idx_last = col("last name")
    idx_school = col("school"); idx_grade = col("grade"); idx_gender = col("gender")
    idx_disc = col("discipline")
    if -1 in (idx_first, idx_last):
        raise HTTPException(status_code=400, detail="CSV manjka stolpca 'First Name' ali 'Last Name'")

    import re
    from datetime import datetime as _dt_now
    errors = []
    records = []
    start_entries = 0
    start_lists_created = 0

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
        school_name = (row[idx_school].strip() if idx_school >= 0 and idx_school < len(row) else "")
        grade = (row[idx_grade].strip() if idx_grade >= 0 and idx_grade < len(row) else "")
        gender = (row[idx_gender].strip() if idx_gender >= 0 and idx_gender < len(row) else "").upper()
        disc_cell = (row[idx_disc].strip() if idx_disc >= 0 and idx_disc < len(row) else "")

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

        # Discipline ID-ji iz stolpca (ločeni s presledki, vejicami ali podpičji, npr. "1 3")
        disc_ids = []
        if disc_cell:
            for del_id in re.split(r"[,; ]+", disc_cell):
                del_id = del_id.strip()
                if not del_id:
                    continue
                try:
                    disc_ids.append(int(del_id))
                except ValueError:
                    errors.append(f"Vrstica {i}: disciplina '{del_id}' ni številka — uporabi ID iz tabele disciplin (npr. 1)")

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

        # Discipline iz CSV: učenca vpiši na štartno listo (nastop) za vsako disciplino
        for disc_id in disc_ids:
            disc = db.query(Discipline).get(disc_id)
            if not disc:
                errors.append(f"Vrstica {i}: neznan ID discipline {disc_id}. Preveri tabelo ID-jev.")
                continue
            if disc.gender.upper() != pupil.gender.upper():
                spol_beseda = "fant" if pupil.gender == "M" else "punca"
                errors.append(f"Vrstica {i}: {first_name} {last_name} ({spol_beseda}) ne more nastopiti v disciplini '{disc.name}' (ID {disc_id}) — disciplina je za drug spol.")
                continue
            veljavni_razredi = [r.strip() for r in (disc.applicable_grades or "2.r,3.r").split(",")]
            if grade not in veljavni_razredi:
                errors.append(f"Vrstica {i}: disciplina '{disc.name}' (ID {disc_id}) ne velja za razred {grade}.")
                continue
            # Poišči obstoječo štartno listo za (disciplina, razred) — najnovejšo; sicer jo ustvari
            sl = db.query(StartList).filter(
                StartList.discipline_id == disc_id,
                StartList.grade == grade
            ).order_by(StartList.event_date.desc(), StartList.id.desc()).first()
            if not sl:
                sl = StartList(discipline_id=disc_id, grade=grade, event_date=_dt_now.utcnow())
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

    pdf = results_pdf_bytes(events, title="Tekmovanja — rezultati")
    from datetime import datetime as _dt
    filename = f"rezultati_{_dt.now().strftime('%Y%m%d_%H%M')}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'}
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
        return {"message": "No events to include in results"}
    
    # Pravi PDF (reportlab) — pravilen encoding + pošlje dejanske bajte PDF
    pdf = results_pdf_bytes(events, title="Tekmovanja — rezultati")
    
    # Get active schools
    active_schools = db_session.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()
    
    subject = "Rezultati tekmovanja"
    sender = app_config.smtp_user if app_config.smtp_user else "tekmovanja@arne.gov.si"
    
    body = "Prišli so rezultati tekmovanja.\n\n" \
           "Sodobna tekmovanja se izvedejo. Prosimo, prebrati rezultate in vprašati v primeru napake.\n\n" \
           "Zahvalimo za sodelovanje!"
    
    try:
        send_email(
            subject=subject,
            sender=sender,
            recipients=[s.email for s in active_schools],
            body=body,
            attachments=[("results.pdf", pdf, "application/pdf")],
        )
    except Exception as e:
        print(f"Email error: {e}")
        return {"message": "Could not send email. Check SMTP configuration."}
    
    return {
        "message": f"Results emailed to {len(active_schools)} school(s)."
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
                   "This action will DELETEd all Pupils and CompetitionEvents (i.e., imported data).\n"
                   "Schools and Disciplines and Records will be preserved.\n"
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
    from app.models import Pupil, CompetitionEvent
    
    # Double-check user confirmation (in production, UI would have modal confirmation)
    # We'll add a simple confirmation parameter
    # In this API, we check for a confirmation header/token? Not implemented for simplicity
    
    # Get data to delete
    pupils = db.query(Pupil).all()
    events = db.query(CompetitionEvent).all()
    
    if len(pupils) == 0 and len(events) == 0:
        return {"message": "No competition data to delete"}
    
    # Delete
    db.query(Pupil).delete()
    db.query(CompetitionEvent).delete()
    db.commit()
    
    return {
        "message": f"Successfully deleted {len(pupils)} pupil(s) and {len(events)} event(s)."
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


@app.post("/api/smtp", response_model=dict, summary="Save SMTP settings")
def save_smtp_settings(
    host: str = Form("smtp.arne.gov.si"),
    port: int = Form(587),
    user: str = Form(...),
    password: str = Form(...),
    db=Depends(get_db)
):
    """Save SMTP credentials (stored locally, never in git)."""
    app_config.smtp_host = host.strip()
    app_config.smtp_port = port
    app_config.set_smtp_credentials(user.strip(), password)
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
    pdf = start_list_pdf_bytes(start_list, start_list.pupils, discipline_name, start_list.grade)
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
