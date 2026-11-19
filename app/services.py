"""
Service / repository sloj.

Vsaka entiteta (School, Discipline, Pupil, Event, Record, StartList) ima svoj
razred s temeljnimi CRUD operacijami. Endpointi v main.py samo pokličejo te
razrede — to pomeni, da se vsa entiteta obnašata ENAKO: ena metoda, ena
pravila, eni formati. Če nekaj popraviš pri eni entiteti, velja za vse.
"""

from typing import Optional, List
from sqlalchemy.orm import Session

from app.models import (
    School, SchoolStatus, Discipline, Pupil, CompetitionEvent,
    Record, StartList,
)


class SchoolService:
    model = School

    @staticmethod
    def all(db: Session, include_inactive=False) -> List[dict]:
        rows = db.query(School).all()
        if include_inactive:
            return [s.to_dict() for s in rows]
        return [s.to_dict() for s in rows if s.status == SchoolStatus.ACTIVE.value]

    @staticmethod
    def create(db: Session, name: str, email: str) -> dict:
        name = name.strip()
        email = email.strip()
        err = School.validate(name, email)
        if err:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=err)
        existing = db.query(School).filter((School.name == name), (School.email == email)).first()
        if existing:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=f"Šola '{name}' s tem e-naslovom že obstaja.")
        school = School(name=name, email=email, status=SchoolStatus.ACTIVE.value)
        db.add(school)
        db.commit()
        return school.to_dict()

    @staticmethod
    def update(db: Session, school_id: int, name=None, email=None, status=None) -> dict:
        from fastapi import HTTPException
        school = db.query(School).get(school_id)
        if not school:
            raise HTTPException(status_code=404, detail="Šola ne obstaja.")
        if name:
            school.name = name.strip()
        if email:
            err = School.validate(school.name, email)
            if err:
                raise HTTPException(status_code=400, detail=err)
            school.email = email.strip()
        if status:
            if status not in (SchoolStatus.ACTIVE.value, SchoolStatus.INACTIVE.value):
                raise HTTPException(status_code=400, detail="Status mora biti 'active' ali 'inactive'.")
            school.status = status
        from datetime import datetime
        school.updated_at = datetime.utcnow()
        db.commit()
        return school.to_dict()

    @staticmethod
    def deactivate(db: Session, school_id: int) -> dict:
        from fastapi import HTTPException
        school = db.query(School).get(school_id)
        if not school:
            raise HTTPException(status_code=404, detail="Šola ne obstaja.")
        school.status = SchoolStatus.INACTIVE.value
        from datetime import datetime
        school.updated_at = datetime.utcnow()
        db.commit()
        return {"message": f"Šola '{school.name}' je bila deaktivirana."}

    @staticmethod
    def reactivate_all(db: Session) -> dict:
        count = db.query(School).filter(School.status == SchoolStatus.INACTIVE.value).update(
            {School.status: SchoolStatus.ACTIVE.value})
        db.commit()
        return {"message": f"Število ponovno aktiviranih šol: {count}."}


class DisciplineService:
    model = Discipline

    ICONS = {"M": "♂", "Ž": "♀", "ž": "♀"}

    @staticmethod
    def display_name(discipline) -> str:
        """Vrne ime s spolno ikono (npr. 'tek 60m ♂'). Če ime že ima ikono, ga pusti."""
        name = (discipline.name or "").strip()
        if not name:
            return name
        # že ima ikono → nespremenjeno
        if name.endswith("♂") or name.endswith("♀"):
            return name
        # stara oblika s črko (npr. 'tek 60m M' / 'tek 60m Ž') → dodaj ikono
        for let, icon in DisciplineService.ICONS.items():
            if name.endswith(" " + let) or name.endswith(let):
                return name[:-1].rstrip() + " " + icon
        # iz gender stolpca → dodaj ikono na konec
        if discipline.gender and discipline.gender.upper() in DisciplineService.ICONS:
            icon = DisciplineService.ICONS[discipline.gender.upper()]
            return name + " " + icon
        return name

    @staticmethod
    def all(db: Session) -> List[dict]:
        rows = db.query(Discipline).all()
        out = []
        for d in rows:
            data = d.to_dict()
            data["name"] = DisciplineService.display_name(d)
            out.append(data)
        return out

    @staticmethod
    def create(db: Session, name: str, gender: str = "M", applicable_grades: Optional[str] = None) -> dict:
        from fastapi import HTTPException
        name = name.strip()
        err = Discipline.validate(name, gender)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if applicable_grades:
            applicable_grades = applicable_grades.strip()
        else:
            applicable_grades = "2.r,3.r"
        existing = db.query(Discipline).filter(Discipline.name == name).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Disciplina '{name}' že obstaja.")
        disc = Discipline(name=name, gender=gender.upper(), applicable_grades=applicable_grades)
        db.add(disc)
        db.commit()
        data = disc.to_dict()
        data["name"] = DisciplineService.display_name(disc)
        return data

    @staticmethod
    def update(db: Session, discipline_id: int, name=None, gender=None, applicable_grades=None) -> dict:
        from fastapi import HTTPException
        disc = db.query(Discipline).get(discipline_id)
        if not disc:
            raise HTTPException(status_code=404, detail="Disciplina ne obstaja.")
        if name:
            disc.name = name.strip()
        if gender:
            gender = gender.strip().upper()
            if gender not in ("M", "Ž"):
                raise HTTPException(status_code=400, detail="Spol mora biti M ali Ž.")
            disc.gender = gender
        if applicable_grades:
            disc.applicable_grades = applicable_grades.strip()
        from datetime import datetime
        disc.updated_at = datetime.utcnow()
        db.commit()
        data = disc.to_dict()
        data["name"] = DisciplineService.display_name(disc)
        return data

    @staticmethod
    def remove(db: Session, discipline_id: int) -> dict:
        from fastapi import HTTPException
        disc = db.query(Discipline).get(discipline_id)
        if not disc:
            raise HTTPException(status_code=404, detail="Disciplina ne obstaja.")
        # ne brišemo zgodovinskih rezultatov; samo označimo atribut? Ni status polja —
        # zato ga pustimo v bazi, vendar ga izključimo iz seznamov z flag nastavimo.
        # Po spec: odstranitev ne sme izbrisati zgodovinskih rezultatov.
        db.delete(disc)
        db.commit()
        return {"message": f"Disciplina '{disc.name}' odstranjena."}


class PupilService:
    model = Pupil

    @staticmethod
    def all(db: Session) -> List[dict]:
        return [p.to_dict() for p in db.query(Pupil).all()]

    @staticmethod
    def create(db: Session, first_name, last_name, school_id, grade="2.r", gender="M") -> dict:
        from fastapi import HTTPException
        first_name = first_name.strip()
        last_name = last_name.strip()
        err = Pupil.validate(first_name, last_name, grade, gender)
        if err:
            raise HTTPException(status_code=400, detail=err)
        school = db.query(School).get(school_id)
        if not school:
            raise HTTPException(status_code=404, detail="Šola ne obstaja.")
        existing = db.query(Pupil).filter(
            Pupil.school_id == school_id,
            Pupil.first_name == first_name,
            Pupil.last_name == last_name,
            Pupil.grade == grade,
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail=f"Učenec '{first_name} {last_name}' že obstaja v tej šoli.")
        p = Pupil(first_name=first_name, last_name=last_name, school_id=school_id,
                  grade=grade, gender=gender.upper())
        db.add(p)
        db.commit()
        return p.to_dict()

    @staticmethod
    def update(db: Session, pupil_id: int, first_name=None, last_name=None, school_id=None,
               grade=None, gender=None) -> dict:
        from fastapi import HTTPException
        p = db.query(Pupil).get(pupil_id)
        if not p:
            raise HTTPException(status_code=404, detail="Učenec ne obstaja.")
        if first_name and first_name.strip():
            p.first_name = first_name.strip()
        if last_name and last_name.strip():
            p.last_name = last_name.strip()
        if school_id is not None:
            school = db.query(School).get(school_id)
            if not school:
                raise HTTPException(status_code=404, detail="Šola ne obstaja.")
            p.school_id = school_id
        if grade and grade.strip():
            if grade.strip() not in ("2.r", "3.r"):
                raise HTTPException(status_code=400, detail="Razred mora biti 2.r ali 3.r.")
            p.grade = grade.strip()
        if gender and gender.strip():
            g = gender.strip().upper()
            if g not in ("M", "Ž"):
                raise HTTPException(status_code=400, detail="Spol mora biti M ali Ž.")
            p.gender = g
        from datetime import datetime
        p.updated_at = datetime.utcnow()
        db.commit()
        return p.to_dict()

    @staticmethod
    def delete(db: Session, pupil_id: int) -> dict:
        from fastapi import HTTPException
        p = db.query(Pupil).get(pupil_id)
        if not p:
            raise HTTPException(status_code=404, detail="Učenec ne obstaja.")
        # izbriši njegove rezultate in rekorde, ohrani tuje
        db.query(CompetitionEvent).filter(CompetitionEvent.pupil_id == pupil_id).delete()
        db.query(Record).filter(Record.pupil_id == pupil_id).delete()
        # odstrani iz štartnih list
        for sl in db.query(StartList).all():
            if p in sl.pupils:
                sl.pupils.remove(p)
        db.delete(p)
        db.commit()
        return {"message": f"Učenec {p.first_name} {p.last_name} izbrisan."}


class EventService:
    model = CompetitionEvent

    @staticmethod
    def all(db: Session) -> List[dict]:
        return [e.to_dict() for e in db.query(CompetitionEvent).all()]

    @staticmethod
    def create(db: Session, pupil_id, discipline_id, time, position, notes=None) -> dict:
        from fastapi import HTTPException
        err = CompetitionEvent.validate(time, position)
        if err:
            raise HTTPException(status_code=400, detail=err)
        if time is None or position is None:
            raise HTTPException(status_code=400, detail="Čas in mesto sta obvezna.")
        pupil = db.query(Pupil).get(pupil_id)
        if not pupil:
            raise HTTPException(status_code=404, detail="Učenec ne obstaja.")
        discipline = db.query(Discipline).get(discipline_id)
        if not discipline:
            raise HTTPException(status_code=404, detail="Disciplina ne obstaja.")
        # Preveri, ali je disciplina primerna za razred učenca
        if discipline.applicable_grades:
            applicable = [g.strip() for g in discipline.applicable_grades.split(",")]
            if pupil.grade not in applicable:
                raise HTTPException(
                    status_code=400,
                    detail=f"Disciplina '{discipline.name}' ni primerna za razred {pupil.grade}",
                )
        ev = CompetitionEvent(pupil_id=pupil_id, discipline_id=discipline_id,
                              time=float(time), position=int(position), notes=notes)
        db.add(ev)
        db.commit()
        return ev.to_dict()

    @staticmethod
    def update(db: Session, event_id: int, time=None, position=None, notes=None,
               pupil_id=None, discipline_id=None) -> dict:
        from fastapi import HTTPException
        ev = db.query(CompetitionEvent).get(event_id)
        if not ev:
            raise HTTPException(status_code=404, detail="Rezultat ne obstaja.")
        if time is not None:
            if time < 0:
                raise HTTPException(status_code=400, detail="Čas mora biti pozitiven.")
            ev.time = float(time)
        if position is not None:
            if not 1 <= position <= 30:
                raise HTTPException(status_code=400, detail="Mesto mora biti med 1 in 30.")
            ev.position = int(position)
        if notes is not None:
            ev.notes = notes.strip() if notes.strip() else None
        if pupil_id is not None:
            if not db.query(Pupil).get(pupil_id):
                raise HTTPException(status_code=404, detail="Učenec ne obstaja.")
            ev.pupil_id = pupil_id
        if discipline_id is not None:
            if not db.query(Discipline).get(discipline_id):
                raise HTTPException(status_code=404, detail="Disciplina ne obstaja.")
            ev.discipline_id = discipline_id
        from datetime import datetime
        ev.updated_at = datetime.utcnow()
        db.commit()
        return ev.to_dict()

    @staticmethod
    def delete(db: Session, event_id: int) -> dict:
        from fastapi import HTTPException
        ev = db.query(CompetitionEvent).get(event_id)
        if not ev:
            raise HTTPException(status_code=404, detail="Rezultat ne obstaja.")
        db.delete(ev)
        db.commit()
        return {"message": f"Rezultat {event_id} izbrisan."}


class RecordService:
    model = Record

    @staticmethod
    def all(db: Session) -> List[dict]:
        return [r.to_dict() for r in db.query(Record).all()]

    @staticmethod
    def create(db: Session, discipline_id, pupil_id, time, date_recorded=None, notes=None) -> dict:
        from fastapi import HTTPException
        from datetime import datetime
        if time is None or time < 0:
            raise HTTPException(status_code=400, detail="Čas mora biti pozitiven.")
        if not db.query(Discipline).get(discipline_id):
            raise HTTPException(status_code=404, detail="Disciplina ne obstaja.")
        if not db.query(Pupil).get(pupil_id):
            raise HTTPException(status_code=404, detail="Učenec ne obstaja.")
        rec_date = None
        if date_recorded:
            try:
                rec_date = datetime.strptime(date_recorded, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail="Napačen datum, uporabi YYYY-MM-DD.")
        rec = Record(discipline_id=discipline_id, pupil_id=pupil_id, time=float(time),
                     date_recorded=rec_date or datetime.utcnow(),
                     notes=notes.strip() if notes else None)
        db.add(rec)
        db.commit()
        return rec.to_dict()

    @staticmethod
    def update(db: Session, record_id: int, time=None, date_recorded=None, notes=None,
               discipline_id=None, pupil_id=None) -> dict:
        from fastapi import HTTPException
        from datetime import datetime
        rec = db.query(Record).get(record_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Rekord ne obstaja.")
        if time is not None:
            if time < 0:
                raise HTTPException(status_code=400, detail="Čas mora biti pozitiven.")
            rec.time = float(time)
        if date_recorded is not None:
            try:
                rec.date_recorded = datetime.strptime(date_recorded, "%Y-%m-%d")
            except ValueError:
                raise HTTPException(status_code=400, detail="Napačen datum, uporabi YYYY-MM-DD.")
        if notes is not None:
            rec.notes = notes.strip() if notes.strip() else None
        if discipline_id is not None:
            if not db.query(Discipline).get(discipline_id):
                raise HTTPException(status_code=404, detail="Disciplina ne obstaja.")
            rec.discipline_id = discipline_id
        if pupil_id is not None:
            if not db.query(Pupil).get(pupil_id):
                raise HTTPException(status_code=404, detail="Učenec ne obstaja.")
            rec.pupil_id = pupil_id
        db.commit()
        return rec.to_dict()

    @staticmethod
    def delete(db: Session, record_id: int) -> dict:
        from fastapi import HTTPException
        rec = db.query(Record).get(record_id)
        if not rec:
            raise HTTPException(status_code=404, detail="Rekord ne obstaja.")
        db.delete(rec)
        db.commit()
        return {"message": f"Rekord {record_id} izbrisan."}


class StartListService:
    model = StartList

    @staticmethod
    def all(db: Session) -> List[dict]:
        return [l.to_dict() for l in db.query(StartList).all()]

    @staticmethod
    def create(db: Session, discipline_id, grade, event_date, pupil_ids) -> dict:
        from fastapi import HTTPException
        from datetime import datetime
        disc = db.query(Discipline).get(discipline_id)
        if not disc:
            raise HTTPException(status_code=404, detail="Disciplina ne obstaja.")
        if grade not in ("2.r", "3.r"):
            raise HTTPException(status_code=400, detail="Razred mora biti 2.r ali 3.r.")
        pupil_list = db.query(Pupil).filter(Pupil.id.in_(pupil_ids)).all() if pupil_ids else []
        if not pupil_list:
            raise HTTPException(status_code=400, detail="Ni učencev za navedene ID-je.")
        try:
            event_dt = datetime.fromisoformat(event_date.replace('Z', '+00:00'))
        except Exception:
            raise HTTPException(status_code=400, detail="Napačen datum. Uporabi ISO (YYYY-MM-DDTHH:MM:SS).")
        sl = StartList(discipline_id=discipline_id, grade=grade, event_date=event_dt)
        sl.pupils = pupil_list
        db.add(sl)
        db.commit()
        return sl.to_dict()

    @staticmethod
    def delete(db: Session, start_list_id: int) -> dict:
        from fastapi import HTTPException
        sl = db.query(StartList).get(start_list_id)
        if not sl:
            raise HTTPException(status_code=404, detail="Štartna lista ne obstaja.")
        db.delete(sl)
        db.commit()
        return {"message": f"Štartna lista {start_list_id} izbrisana."}