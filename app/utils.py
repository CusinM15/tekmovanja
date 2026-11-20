"""
Utility functions for the Tekmovanja app.
"""

import os
from datetime import datetime
from typing import List
from app.settings import app_config
from app.database import get_session
from app.models import School, Discipline, Pupil, CompetitionEvent, Record, StartList, SchoolStatus

DB = None


def get_db_session():
    """Get a database session."""
    global DB
    if DB is None:
        from app.database import get_session
        DB = get_session()
    return DB()


def generate_csv_template() -> str:
    """
    Generate a CSV template for school data collection.
    """
    # Based on the requirements in the prompt
    template = ("First Name,Last Name,School,Grade,Gender,Discipline(s),Event Date (MM/DD/YYYY),Time (seconds),Position (1-30),Notes\n"
                "e.g., Ana,Novak,OŠ Žirovnica,2.r,Ž,tek 60m Ž,05/20/2026,10.5,5,\n")
    return template


def get_active_schools() -> List[dict]:
    """Get list of active schools in dict format for API responses."""
    session = get_db_session()
    schools = session.query(School).filter(School.status == SchoolStatus.ACTIVE.value).all()
    return [
        {
            "id": s.id,
            "name": s.name,
            "email": s.email,
        }
        for s in schools
    ]


def get_active_disciplines() -> List[dict]:
    """Get list of disciplines that have historical events."""
    session = get_db_session()
    disciplines = session.query(Discipline).all()
    return [
        {
            "id": d.id,
            "name": d.name,
            "gender": d.gender,
            "applicable_grades": d.applicable_grades,
        }
        for d in disciplines
    ]


def get_applicable_disciplines_for_grade(grade: str) -> List[dict]:
    """Get disciplines applicable for a grade based on discipline records."""
    session = get_db_session()
    applicable = []
    grade_str = grade.strip()
    if grade_str not in ("2.r", "3.r"):
        return applicable
    
    disciplines = session.query(Discipline).all()
    for d in disciplines:
        # Simple check: if grade appears in applicable_grades string
        applicable_grades = d.applicable_grades.strip().split(",")
        if any(g.strip() == grade_str for g in applicable_grades):
            applicable.append({
                "id": d.id,
                "name": d.name,
                "gender": d.gender,
            })
    return applicable


def check_record_eligible(pupil_id: int, discipline_id: int, time: float) -> tuple:
    """
    Check if a new time is eligible to create a record.
    Returns (is_new_record, list_of_existing_records_that_tie_or_are_beaten)
    """
    session = get_db_session()
    # Get records for pupil in discipline
    records = session.query(Record).filter(
        Record.pupil_id == pupil_id,
        Record.discipline_id == discipline_id
    ).order_by(Record.date_recorded).all()
    
    # Find current best
    current_best = None
    for r in records:
        if current_best is None or r.time < current_best.time:
            current_best = r
    
    # Compare with new time
    new_beats_best = False
    new_records_needed = []
    
    if current_best:
        if time < current_best.time - 0.001:  # strictly better
            new_records_needed.append(current_best)
            new_beats_best = True
        elif abs(time - current_best.time) < 0.001:  # tie within tolerance
            new_records_needed.append(current_best)
    
    return new_beats_best, new_records_needed


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
    For development/demo, prints the message.
    """
    from app.settings import app_config
    
    if not recipients or len(recipients) == 0:
        print("No recipients provided")
        return
    
    # Check if SMTP credentials are configured
    if not app_config.smtp_user or not app_config.smtp_password:
        print(f"SMTP credentials NOT CONFIGURED! Sender: {sender}")
        print(f"Recipients: {recipients}")
        print(f"Subject: {subject}")
        print(f"Body:\n{body}")
        return
    
    # In demo mode, just print the message
    print(f"\n=== EMAIL ===\nFrom: {sender}\nTo: {', '.join(recipients)}\nSubject: {subject}\nBody:\n{body}")
    if attachments:
        print(f"\nAttachments:\n{', '.join([f'{a[0]} ({a[2]})' for a in attachments])}")
    
    return {"status": "queued", "message": "Email sent (demo)"}


def generate_results_pdf(events, discipline_id=None, school_id=None):
    """
    Generate results PDF content.
    Returns HTML string that can be converted to PDF.
    """
    import datetime
    
    # Get related data
    session = get_db_session()
    
    # Events data
    event_rows = []
    for ev in events:
        pupil = session.query(Pupil).get(ev.pupil_id) if ev.pupil_id else None
        discipl = session.query(Discipline).get(ev.discipline_id) if ev.discipline_id else None
        
        pupil_name = f"{pupil.first_name} {pupil.last_name}" if pupil else "Unknown Pupil"
        disc_name = discipl.name if discipl else "Unknown Discipline"
        
        row = {
            "pupil": pupil_name,
            "discipline": disc_name,
            "time_seconds": f"{ev.time:.2f}",
            "position": ev.position,
            "notes": ev.notes or "",
        }
        event_rows.append(row)
    
    # Current date
    now = datetime.datetime.utcnow()
    
    # Generate HTML
    html = """
    <html>
    <head>
        <meta charset="UTF-8">
        <title>Tekmovanja Rezultati</title>
        <style>
            body { font-family: Arial, sans-serif; margin: 20px; }
            h1 { color: #2c3e50; }
            h2 { color: #3498db; }
            table { border-collapse: collapse; width: 100%; margin-top: 20px; }
            th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }
            th { background-color: #f2f2f2; }
            tr:nth-child(even) { background-color: #f9f9f9; }
            .footer { margin-top: 40px; font-size: 0.9em; color: #666; }
        </style>
    </head>
    <body>
        <h1>Tekmovanja Rezultati</h1>
        <p><strong>Datum:</strong> {}</p>
        <p><strong>Odaril:</strong> Tekmovanja</p>
        
        <h2>Rezultati</h2>
        <table>
            <tr>
                <th>Pupil</th>
                <th>Discipline</th>
                <th>Dolžina / čas (s)</th>
                <th>Pozicija</th>
                <th>Opis</th>
            </tr>
        """.format(now.strftime("%Y-%m-%d %H:%M"))
    
    for row in event_rows:
        html += (
            f"<tr>\n"
            f"    <td>{row['pupil']}</td>\n"
            f"    <td>{row['discipline']}</td>\n"
            f"    <td>{row['time_seconds']}</td>\n"
            f"    <td>{row['position']}</td>\n"
            f"    <td>{row['notes']}</td>\n"
            f"</tr>\n"
        )
    
    html += "</table>\n"
    
    html += """
        <div class="footer">
            <p>Zarni izvedenje <br> Tekmovanja Primarni šola</p>
        </div>
    </body>
    </html>
    """.format(now.strftime("%Y-%m-%d %H:%M"))
    
    return html


# === PRAVI PDF (reportlab) ===

def _slovene_font():
    """Poišče UniCode TrueType pisavo (DejaVuSans — ima Š Ž Č in ♂ ♀).
    Najprej repo-vgrajena (static/fonts/DejaVuSans.ttf — deluje tudi na Windowsu),
    nato sistemski DejaVu, nazadnje Windows Arial. Vrne pot ali None."""
    import os
    from pathlib import Path
    repo_font = Path(__file__).resolve().parent.parent / "static" / "fonts" / "DejaVuSans.ttf"
    kandidati = [
        str(repo_font),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSansCondensed.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ]
    for k in kandidati:
        if os.path.exists(k):
            return k
    return None


def _reg_fonts():
    """Registrira in vrne ime UniCode pisave za PDF-je.
    Z DejaVuSans so š/ž/č in ♂/♀ v PDF-jih popolno prikazani tudi na Windowsu
    (prej je tam padel na Helvetica in se znaki pokvarili)."""
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    f = _slovene_font()
    if not f or "DejaVu" not in f:
        return "Helvetica"
    if "DejaVuSans" not in pdfmetrics.getRegisteredFontNames():
        try:
            pdfmetrics.registerFont(TTFont("DejaVuSans", f))
        except Exception:
            return "Helvetica"
    return "DejaVuSans"


def _cell_par(text, font, size=10):
    """Besedilo v Paragraph — znotraj celice se lepo zvije (imena/šole ne prelijejo)
    in uporabi pravi UniCode font (š/ž/č/♂/♀)."""
    from reportlab.lib.styles import ParagraphStyle
    from reportlab.platypus import Paragraph
    st = ParagraphStyle("cellpar", fontName=font, fontSize=size,
                        leading=size * 1.3)
    return Paragraph(text if text is not None else "", st)


def results_pdf_bytes(events, title="Tekmovanja — rezultati"):
    """Pravi PDF s seznamom rezultatov. Vrne bajte."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from datetime import datetime

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    font = _reg_fonts()
    styles["Title"].fontName = font
    styles["Heading2"].fontName = font
    styles["Normal"].fontName = font

    elems = []
    elems.append(Paragraph(title, styles["Title"]))
    elems.append(Spacer(1, 0.4 * cm))
    elems.append(Paragraph(f"Datum izpisa: {datetime.now().strftime('%d.%m.%Y %H:%M')}", styles["Normal"]))
    elems.append(Spacer(1, 0.5 * cm))

    data = [["Učenec", "Razred", "Disciplina", "Čas / dolžina", "Mesto", "Opombe"]]
    for ev in events:
        pupil = ev.pupil
        disc = ev.discipline
        data.append([
            _cell_par(f"{pupil.first_name} {pupil.last_name}" if pupil else "—", font, size=9),
            _cell_par(pupil.grade if pupil else "", font, size=9),
            _cell_par(disc.name if disc else "—", font, size=9),
            _cell_par(f"{ev.time:.2f}" if ev.time is not None else "", font, size=9),
            _cell_par(str(ev.position) if ev.position is not None else "", font, size=9),
            _cell_par((ev.notes or "") or "", font, size=9),
        ])
    if len(data) == 1:
        data.append([_cell_par("Ni še vnesenih rezultatov.", font, size=9)]
                    + [_cell_par("", font, size=9) for _ in range(5)])

    # Širine seštevek <= ~17 cm (A4 brez robov) — učenci/opombe se zvijejo znotraj celice.
    t = Table(data, repeatRows=1, colWidths=[3.8 * cm, 1.5 * cm, 4.2 * cm, 2.6 * cm, 1.5 * cm, 2.6 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), font),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ("FONTSIZE", (0, 0), (-1, -1), 9),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    elems.append(t)
    elems.append(Spacer(1, 0.6 * cm))
    elems.append(Paragraph("Generated by Tekmovanja — Primary School Athletics Manager", styles["Normal"]))
    doc.build(elems)
    return buf.getvalue()


def start_list_pdf_bytes(start_list, pupils, discipline_name, grade):
    """Pravi PDF s štartno listo."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.lib.styles import getSampleStyleSheet
    from datetime import datetime

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    font = _reg_fonts()
    for s in ("Title", "Heading2", "Normal"):
        styles[s].fontName = font

    elems = []
    elems.append(Paragraph(f"Štartna lista — {discipline_name}", styles["Title"]))
    elems.append(Spacer(1, 0.3 * cm))
    elems.append(Paragraph(f"Razred: {grade} &nbsp; | &nbsp; Datum: {start_list.event_date.strftime('%d.%m.%Y') if start_list.event_date else '—'}", styles["Normal"]))
    elems.append(Spacer(1, 0.5 * cm))

    data = [["#", "Učenec", "Razred", "Šola", "Rezultat"]]
    for i, pupil in enumerate(pupils, start=1):
        data.append([
            _cell_par(str(i), font),
            _cell_par(f"{pupil.first_name} {pupil.last_name}", font),
            _cell_par(pupil.grade or "", font),
            # Šola razširjena + Paragraph: "OŠ Josipa Vandota Kranjska Gora" se
            # zlomi znotraj celice in ne prelije čez rob.
            _cell_par(pupil.school.name if pupil.school else "", font),
            _cell_par("", font),  # prostor za vpis rezultata
        ])
    if len(data) == 1:
        data.append([_cell_par("1", font), _cell_par("Ni učencev na listi.", font),
                     _cell_par("", font), _cell_par("", font), _cell_par("", font)])

    t = Table(data, repeatRows=1, colWidths=[1.0 * cm, 5.0 * cm, 1.6 * cm, 6.4 * cm, 3.0 * cm])
    t.setStyle(TableStyle([
        ("FONTNAME", (0, 0), (-1, 0), font),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2c3e50")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.5, colors.grey),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f2f2f2")]),
        ("FONTSIZE", (0, 0), (-1, -1), 10),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
    ]))
    elems.append(t)
    doc.build(elems)
    return buf.getvalue()


def create_start_list(discipline_id: int, grade: str, event_date, pupil_ids: List[int]):
    """
    Create a start list entry and associate pupils.
    """
    from app.database import get_db_session
    from app.models import StartList
    from datetime import datetime
    
    session = get_db_session()
    
    # Validate grade
    from app.models import SchoolStatus
    if grade not in ("2.r", "3.r"):
        raise ValueError("Grade must be 2.r or 3.r")
    
    # Parse event_date (ISO format or string)
    from dateutil import parser
    try:
        event_dt = parser.parse(event_date).astimezone()
    except:
        # Try simple datetime
        from datetime import datetime
        if isinstance(event_date, str):
            event_dt = datetime.fromisoformat(event_date.replace('Z', '+00:00'))
        else:
            event_dt = datetime.utcnow()
    
    # Check pupils exist
    pupils = session.query(Pupil).filter(Pupil.id.in_(pupil_ids)).all()
    if len(pupils) != len(pupil_ids):
        # Some pupils not found
        existing_ids = {p.id for p in pupils}
        missing_ids = set(pupil_ids) - existing_ids
        return {"error": f"Pupils with IDs {missing_ids} not found"}, None
    
    # Create start list
    start_list = StartList(
        discipline_id=discipline_id,
        grade=grade,
        event_date=event_dt,
    )
    session.add(start_list)
    session.commit()
    
    return {"message": f"Start list created for discipline {discipline_id}, grade {grade} with {len(pupils)} pupils"}, start_list

