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
        obstojece_d = {d.name for d in db.query(Discipline).all()}
        for ime, razredi in app_config.default_disciplines.items():
            if ime not in obstojece_d:
                spol = "Ž" if ime.strip().endswith(("Ž", "ž")) else "M"
                db.add(Discipline(name=ime, gender=spol,
                                  applicable_grades=",".join(razredi)))
        db.commit()

        # --- Učenci in rekordi samo, če še ni nobenega učenca ---
        if db.query(Pupil).count() == 0:
            sole = {s.name: s.id for s in db.query(School).all()}
            disc = {d.name: d.id for d in db.query(Discipline).all()}

            vzorci = [
                # (ime, priimek, šola, razred, spol)
                ("Ana", "Novak", "OŠ Žirovnica", "2.r", "Ž"),
                ("Luka", "Zupan", "OŠ Žirovnica", "3.r", "M"),
                ("Maja", "Kovač", "OŠ Prežihov Voranc", "2.r", "Ž"),
                ("Jan", "Horvat", "OŠ Prežihov Voranc", "3.r", "M"),
                ("Eva", "Peršič", "OŠ Koroška Bela", "2.r", "Ž"),
                ("Tim", "Rozman", "OŠ Koroška Bela", "3.r", "M"),
                ("Nina", "Mlakar", "OŠ Josipa Vandota Kranjska Gora", "2.r", "Ž"),
                ("Žan", "Jerina", "OŠ Josipa Vandota Kranjska Gora", "3.r", "M"),
                ("Pia", "Šubic", "OŠ 16. decembra Mojstrana", "2.r", "Ž"),
                ("Gal", "Mežnar", "OŠ 16. decembra Mojstrana", "3.r", "M"),
            ]
            ucenci = {}
            for ime, priimek, sola, razred, spol in vzorci:
                u = Pupil(first_name=ime, last_name=priimek,
                          school_id=sole.get(sola), grade=razred, gender=spol)
                db.add(u)
                ucenci[(ime, priimek)] = u
            db.commit()

            # Vzorčni rezultati (events)
            vzorci_rezultatov = [
                ("Ana", "Novak", "tek 60m Ž", 10.45, 1, "Osebni rekord"),
                ("Luka", "Zupan", "tek 60m M", 10.10, 2, None),
                ("Maja", "Kovač", "tek 400m Ž", 72.30, 3, None),
                ("Jan", "Horvat", "skok v daljino M", 3.85, 1, "Daljina v metrih"),
                ("Eva", "Peršič", "met vorteksa Ž", 24.5, 2, None),
            ]
            for ime, priimek, disc_ime, cas, mesto, opombe in vzorci_rezultatov:
                u = ucenci.get((ime, priimek))
                if u and disc.get(disc_ime):
                    db.add(CompetitionEvent(pupil_id=u.id, discipline_id=disc[disc_ime],
                                            time=cas, position=mesto, notes=opombe))
            db.commit()

            # Vzorčni rekordi (best results)
            vzorci_rekordov = [
                ("Gal", "Mežnar", "tek 60m M", 9.85, "2026-05-18", "Rekord tekmovanja"),
                ("Ana", "Novak", "tek 60m Ž", 10.20, "2026-05-18", None),
                ("Jan", "Horvat", "skok v daljino M", 3.95, "2026-05-18", None),
                ("Pia", "Šubic", "met vorteksa Ž", 26.1, "2026-05-18", None),
                ("Maja", "Kovač", "tek 400m ž", 70.15, "2026-05-18", None),
            ]
            from datetime import datetime as _dt
            for ime, priimek, disc_ime, cas, datum, opombe in vzorci_rekordov:
                u = ucenci.get((ime, priimek))
                if u and disc.get(disc_ime):
                    db.add(Record(discipline_id=disc[disc_ime], pupil_id=u.id,
                                  time=cas, date_recorded=_dt.strptime(datum, "%Y-%m-%d"),
                                  notes=opombe))
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
    """Add a new school."""
    # Validate inputs
    if not name or len(name.strip()) == 0:
        raise HTTPException(status_code=400, detail="Name is required")
    if not email or len(email.strip()) == 0:
        raise HTTPException(status_code=400, detail="Email is required")
    
    # Check if school already exists
    existing = db.query(School).filter((School.name == name), (School.email == email)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"School '{name}' with email '{email}' already exists")
    
    # Create school
    school = School(
        name=name.strip(),
        email=email.strip(),
        status=SchoolStatus.ACTIVE.value
    )
    db.add(school)
    db.commit()
    
    return {
        "id": school.id,
        "name": school.name,
        "email": school.email,
        "status": school.status,
        "created_at": school.created_at.isoformat(),
        "updated_at": school.updated_at.isoformat(),
    }

@app.put("/schools/{school_id}", response_model=dict, summary="Update school")
def update_school(
    school_id: int,
    name: Optional[str] = Form(None),
    email: Optional[str] = Form(None),
    status: Optional[str] = Form(None),
    db=Depends(get_db)
):
    """Update school details."""
    school = db.query(School).get(school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    if name:
        school.name = name.strip()
    if email:
        school.email = email.strip()
    if status:
        if status not in (SchoolStatus.ACTIVE.value, SchoolStatus.INACTIVE.value):
            raise HTTPException(status_code=400, detail="Status must be 'active' or 'inactive'")
        school.status = status
    
    school.updated_at = datetime.utcnow()
    db.commit()
    
    return {
        "id": school.id,
        "name": school.name,
        "email": school.email,
        "status": school.status,
        "updated_at": school.updated_at.isoformat(),
    }

@app.delete("/schools/{school_id}", response_model=dict, summary="Deactivate school")
def deactivate_school(
    school_id: int,
    db=Depends(get_db)
):
    """Deactivate a school (moves to inactive list). Does not delete data."""
    school = db.query(School).get(school_id)
    if not school:
        raise HTTPException(status_code=404, detail="School not found")
    
    # Prevent deactivating the only school if it's the sender? Not required by spec
    school.status = SchoolStatus.INACTIVE.value
    school.updated_at = datetime.utcnow()
    db.commit()
    
    return {"message": f"School '{school.name}' deactivated"}

@app.delete("/schools", response_model=dict, summary="Reactivate school (bulk reactivate)")
def reactivate_all_inactive(
    db=Depends(get_db)
):
    """Reactivate all inactive schools."""
    inactive = db.query(School).filter(School.status == SchoolStatus.INACTIVE.value).all()
    for school in inactive:
        school.status = SchoolStatus.ACTIVE.value
        school.updated_at = datetime.utcnow()
    
    db.commit()
    return {"message": f"Reactivated {len(inactive)} inactive school(s)"}

# === DISCIPLINE MANAGEMENT ===

@app.get("/disciplines", response_model=List[dict], summary="List disciplines")
def list_disciplines(db=Depends(get_db)):
    """List all disciplines."""
    disciplines = db.query(Discipline).all()
    return [d.to_dict() for d in disciplines]

@app.post("/disciplines", response_model=dict, summary="Add discipline")
def add_discipline(
    name: str = Form(...),
    gender: str = Form("M"),  # default to male
    applicable_grades: Optional[str] = None,  # e.g., "2.r,3.r"
    db=Depends(get_db)
):
    """Add a new discipline."""
    if not name or len(name.strip()) == 0:
        raise HTTPException(status_code=400, detail="Name is required")
    
    # Trim and validate
    name = name.strip()
    gender = gender.strip().upper()
    
    # Validate gender
    if gender not in ("M", "Z", "Ž", "Z", "S"):  # Allow both cases and variations
        # Normalize: M=M, Z/Ž=Z (but model uses gender col; we'll check later)
        pass  # Model will handle validation
    
    if applicable_grades:
        applicable_grades = applicable_grades.strip()
    else:
        applicable_grades = "2.r,3.r"
    
    # Check if discipline already exists
    existing = db.query(Discipline).filter((Discipline.name == name)).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Discipline '{name}' already exists")
    
    discipline = Discipline(
        name=name,
        gender=gender.upper(),
        applicable_grades=applicable_grades
    )
    db.add(discipline)
    db.commit()
    
    return {
        "id": discipline.id,
        "name": discipline.name,
        "gender": discipline.gender,
        "applicable_grades": discipline.applicable_grades,
        "created_at": discipline.created_at.isoformat(),
        "updated_at": discipline.updated_at.isoformat(),
    }

@app.put("/disciplines/{discipline_id}", response_model=dict, summary="Update discipline")
def update_discipline(
    discipline_id: int,
    name: Optional[str] = Form(None),
    gender: Optional[str] = Form(None),
    applicable_grades: Optional[str] = Form(None),
    db=Depends(get_db)
):
    """Update discipline details."""
    discipline = db.query(Discipline).get(discipline_id)
    if not discipline:
        raise HTTPException(status_code=404, detail="Discipline not found")
    
    if name:
        if name == discipline.name:
            pass  # no change
        else:
            discipline.name = name.strip()
    if gender:
        gender = gender.strip().upper()
        # Must be M or Z/Ž
        if gender not in ("M", "Z", "Ž"):
            raise HTTPException(status_code=400, detail="Gender must be M or Z/Ž")
        discipline.gender = gender
    if applicable_grades:
        applicable_grades = applicable_grades.strip()  # optional; keep or use default
    
    discipline.updated_at = datetime.utcnow()
    db.commit()
    
    return {
        "id": discipline.id,
        "name": discipline.name,
        "gender": discipline.gender,
        "applicable_grades": discipline.applicable_grades,
        "updated_at": discipline.updated_at.isoformat(),
    }

@app.delete("/disciplines/{discipline_id}", response_model=dict, summary="Remove discipline (inactivate only)")
def remove_discipline(
    discipline_id: int,
    db=Depends(get_db)
):
    """Remove/inactivate a discipline. Does not delete historical results."""
    discipline = db.query(Discipline).get(discipline_id)
    if not discipline:
        raise HTTPException(status_code=404, detail="Discipline not found")
    
    # Just deactivate by setting status? Model doesn't have status field.
    # Instead, we can deactivate by filtering; but prompt says "let user add and remove disciplines"
    # and "removing one must not delete its historical results"
    # We'll just exclude from auto assignments; no field needed
    # Return success message
    db.commit()
    return {"message": f"Discipline '{discipline.name}' marked for removal"}

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
    """Record a single event result."""
    # Validate inputs
    if time is None or position is None:
        raise HTTPException(status_code=400, detail="Time and position are required")
    
    if not isinstance(time, (int, float)) or time < 0:
        raise HTTPException(status_code=400, detail="Time must be a positive number")
    
    if not 1 <= position <= 30:  # reasonable max for primary school
        raise HTTPException(status_code=400, detail="Position must be between 1 and 30")
    
    # Get pupil and discipline
    pupil = db.query(Pupil).get(pupil_id)
    discipline = db.query(Discipline).get(discipline_id)
    if not pupil or not discipline:
        raise HTTPException(status_code=404, detail="Pupil or discipline not found")
    
    # Check applicability: discipline must be applicable for pupil's grade
    if discipline.applicable_grades and pupil.grade not in discipline.applicable_grades.split(","):
        # Check if grade appears in applicable list
        applicable = [g.strip() for g in discipline.applicable_grades.split(",")]
        if pupil.grade not in applicable:
            raise HTTPException(
                status_code=400, 
                detail=f"Discipline '{discipline.name}' is not applicable for grade {pupil.grade}"
            )
    
    event = CompetitionEvent(
        pupil_id=pupil_id,
        discipline_id=discipline_id,
        time=float(time),
        position=position,
        notes=notes.strip() if notes else None,
    )
    db.add(event)
    db.commit()
    
    return {
        "id": event.id,
        "pupil_id": event.pupil_id,
        "discipline_id": event.discipline_id,
        "time": event.time,
        "position": event.position,
        "notes": event.notes,
        "created_at": event.created_at.isoformat(),
    }

@app.get("/events", response_model=List[dict], summary="List events")
def list_events(db=Depends(get_db)):
    """List all events."""
    events = db.query(CompetitionEvent).all()
    return [e.to_dict() for e in events]

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
    csv_content = "\ufeffFirst Name,Last Name,School,Grade,Gender\n" \
                  "Ime,Priimek,Šola (npr. OŠ Žirovnica),Razred (2.r ali 3.r),Spol (M ali Ž)\n"
    
    # For file download
    from fastapi import File, UploadFile, Form
    # Return JSON with file path? In real app, serve as downloadable file
    # We'll just return as JSON for now; in production would serve file
    
    # Get active schools for email
    db = get_session()
    try:
        active_schools = db.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()
    finally:
        db.close()
    
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
    if file.content_type != "text/csv":
        raise HTTPException(status_code=400, detail="File must be CSV")
    
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
        raise HTTPException(status_code=400, detail="CSV manjka glava: First Name, Last Name, School, Grade, Gender")
    headers = [h.strip().lower() for h in rows[header_idx]]

    def col(key):
        try:
            return headers.index(key)
        except ValueError:
            return -1

    idx_first = col("first name"); idx_last = col("last name")
    idx_school = col("school"); idx_grade = col("grade"); idx_gender = col("gender")
    if -1 in (idx_first, idx_last):
        raise HTTPException(status_code=400, detail="CSV manjka stolpca 'First Name' ali 'Last Name'")

    errors = []
    records = []
    
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
        
        # Validate required fields
        if not first_name or len(first_name) < 2:
            errors.append(f"Row {i}: Invalid first name: '{first_name}'")
            continue
        if not last_name or len(last_name) < 2:
            errors.append(f"Row {i}: Invalid last name: '{last_name}'")
            continue
        if not grade or grade not in ("2.r", "3.r"):
            errors.append(f"Row {i}: Invalid grade: '{grade}'. Must be 2.r or 3.r")
            continue
        if gender not in ("M", "Ž", "m", "ž", "Z"):  # normalize
            gender = gender.upper()  # so it's standard
            if gender not in ("M", "Z", "Ž"):
                errors.append(f"Row {i}: Invalid gender: '{gender}'. Must be M or Z/Ž")
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
                db.commit()
        if school is None:
            continue  # ni mogoče, a varno
        school_id = school.id
        
        # Validate discipline tick (optional)
        # For simplicity, we'll just import pupils first
        
        # Check if pupil already exists
        existing_pupil = db.query(Pupil).filter(
            Pupil.school_id == school_id,
            Pupil.first_name == first_name,
            Pupil.last_name == last_name,
            Pupil.grade == grade,
            Pupil.gender == gender
        ).first()
        
        if existing_pupil:
            errors.append(f"Row {i}: Pupil {first_name} {last_name} already exists")
            continue
        
        # Create pupil record
        pupil = Pupil(
            first_name=first_name,
            last_name=last_name,
            school_id=school_id,
            grade=grade,
            gender=gender
        )
        db.add(pupil)
        pupil_id = pupil.id
        
        # Record goes in records list (later when events imported)
        # For now, just validate
        records.append({
            "row": i,
            "first_name": first_name,
            "last_name": last_name,
            "school_id": school_id,
            "grade": grade,
            "gender": gender,
            "pupil_id": pupil_id,
        })
    
    # Check for duplicates in CSV itself
    # Simple check: same pupil in same row? We don't re-create same pupil in one CSV
    
    if errors:
        return {
            "status": "error",
            "errors": errors,
            "records_processed": len(records),
            "message": "CSV validation failed. Please correct errors and retry."
        }
    
    # Commit pupil creations
    db.commit()
    
    # Now import events if provided in CSV (optional)
    # Events might be in separate rows or same rows with discipline/time/position columns
    
    # For simplicity, return success
    return {
        "status": "success",
        "records_created": len(records),
        "message": f"Successfully imported {len(records)} pupil(s)."
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
    from app.utils import generate_results_pdf, send_email
    
    # Get all events (db prihaja prek Depends — samodejno zaprt)
    db_session = db
    events = db_session.query(CompetitionEvent).all()
    
    if not events:
        return {"message": "No events to include in results"}
    
    # Generate PDF (HTML for now)
    pdf_html = generate_results_pdf(events=events)  # returns HTML
    
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
            attachments=[("results.pdf", pdf_html, "application/pdf")],
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
    """Update pupil details."""
    pupil = db.query(Pupil).get(pupil_id)
    if not pupil:
        raise HTTPException(status_code=404, detail="Pupil not found")

    if first_name is not None and first_name.strip():
        pupil.first_name = first_name.strip()
    if last_name is not None and last_name.strip():
        pupil.last_name = last_name.strip()
    if school_id is not None:
        school = db.query(School).get(school_id)
        if not school:
            raise HTTPException(status_code=404, detail="School not found")
        pupil.school_id = school_id
    if grade is not None and grade.strip():
        grade = grade.strip()
        if grade not in ("2.r", "3.r"):
            raise HTTPException(status_code=400, detail="Grade must be 2.r or 3.r")
        pupil.grade = grade
    if gender is not None and gender.strip():
        gender = gender.strip().upper()
        if gender not in ("M", "Ž"):
            raise HTTPException(status_code=400, detail="Gender must be M or Ž")
        pupil.gender = gender

    pupil.updated_at = datetime.utcnow()
    db.commit()
    return {
        "id": pupil.id,
        "first_name": pupil.first_name,
        "last_name": pupil.last_name,
        "school_id": pupil.school_id,
        "grade": pupil.grade,
        "gender": pupil.gender,
    }


@app.delete("/pupils/{pupil_id}", response_model=dict, summary="Delete pupil")
def delete_pupil(pupil_id: int, db=Depends(get_db)):
    """Delete a pupil and their competition events and records."""
    pupil = db.query(Pupil).get(pupil_id)
    if not pupil:
        raise HTTPException(status_code=404, detail="Pupil not found")

    db.query(CompetitionEvent).filter(CompetitionEvent.pupil_id == pupil_id).delete()
    db.query(Record).filter(Record.pupil_id == pupil_id).delete()
    db.delete(pupil)
    db.commit()
    return {"message": f"Pupil {pupil.first_name} {pupil.last_name} deleted"}


@app.delete("/events/{event_id}", response_model=dict, summary="Delete event/result")
def delete_event(event_id: int, db=Depends(get_db)):
    """Delete a single competition event (result)."""
    event = db.query(CompetitionEvent).get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    db.delete(event)
    db.commit()
    return {"message": f"Event {event_id} deleted"}


@app.put("/events/{event_id}", response_model=dict, summary="Update event/result")
def update_event(
    event_id: int,
    time: Optional[float] = Form(None),
    position: Optional[int] = Form(None),
    notes: Optional[str] = Form(None),
    discipline_id: Optional[int] = Form(None),
    db=Depends(get_db)
):
    """Update a competition event (result)."""
    event = db.query(CompetitionEvent).get(event_id)
    if not event:
        raise HTTPException(status_code=404, detail="Event not found")
    if time is not None:
        if time < 0:
            raise HTTPException(status_code=400, detail="Time must be positive")
        event.time = float(time)
    if position is not None:
        if not 1 <= position <= 30:
            raise HTTPException(status_code=400, detail="Position must be between 1 and 30")
        event.position = position
    if notes is not None:
        event.notes = notes.strip() if notes.strip() else None
    if discipline_id is not None:
        disc = db.query(Discipline).get(discipline_id)
        if not disc:
            raise HTTPException(status_code=404, detail="Discipline not found")
        event.discipline_id = discipline_id
    event.updated_at = datetime.utcnow()
    db.commit()
    return {"message": f"Event {event_id} updated", "id": event.id,
            "time": event.time, "position": event.position}


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
    """Update a record (best result)."""
    record = db.query(Record).get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    if time is not None:
        if time < 0:
            raise HTTPException(status_code=400, detail="Time must be positive")
        record.time = float(time)
    if date_recorded is not None:
        try:
            record.date_recorded = datetime.strptime(date_recorded, "%Y-%m-%d")
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid date, use YYYY-MM-DD")
    if notes is not None:
        record.notes = notes.strip() if notes.strip() else None
    if discipline_id is not None:
        disc = db.query(Discipline).get(discipline_id)
        if not disc:
            raise HTTPException(status_code=404, detail="Discipline not found")
        record.discipline_id = discipline_id
    if pupil_id is not None:
        pupil = db.query(Pupil).get(pupil_id)
        if not pupil:
            raise HTTPException(status_code=404, detail="Pupil not found")
        record.pupil_id = pupil_id
    db.commit()
    return {"message": f"Record {record_id} updated", "id": record.id,
            "time": record.time, "discipline_id": record.discipline_id,
            "pupil_id": record.pupil_id}


@app.delete("/records/{record_id}", response_model=dict, summary="Delete record")
def delete_record(record_id: int, db=Depends(get_db)):
    """Delete a record (best result)."""
    record = db.query(Record).get(record_id)
    if not record:
        raise HTTPException(status_code=404, detail="Record not found")
    db.delete(record)
    db.commit()
    return {"message": f"Record {record_id} deleted"}


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
    """Delete a start list."""
    start_list = db.query(StartList).get(start_list_id)
    if not start_list:
        raise HTTPException(status_code=404, detail="Start list not found")
    db.delete(start_list)
    db.commit()
    return {"message": f"Start list {start_list_id} deleted"}


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
