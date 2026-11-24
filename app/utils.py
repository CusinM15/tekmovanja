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


def enota_za_disciplino(ime):
    """Enota rezultata glede na disciplino: skok v daljino → cm, met vorteksa → m, drugo → s."""
    n = (ime or "").replace("♂", "").replace("♀", "").lower()
    if "skok v daljino" in n:
        return "cm"
    if "vorteksa" in n:
        return "m"
    return "s"


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
    template = (
        "*** NAVODILA — KAKO IZPOLNIŠ DATOTEKO ***\n"
        "1. V polje »ŠOLA« zgoraj vpiši ime svoje šole (SAMO ENKRAT).\n"
        "2. Vsako vrstico spodaj izpolni za ENEGA učenca (razred: 2 ali 3; spol: M ali Ž;\n"
        "   Discipline: ID-je 1-4 iz tabele, ločene s presledkom npr. 1 2 4).\n"
        "3. Datoteko shrani in vrni organizatorju.\n\n"
        "ŠOLA:\n<ime svoje šole>\n\n"
        "ID,Disciplina (šport)\n"
        "1,tek 60m\n2,tek 400m\n3,skok v daljino\n4,met vorteksa\n\n"
        "Ime, Priimek, Razred, Spol, Discipline (ID)\n"
    )
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
    Send email via SMTP (STARTTLS), uporabi nastavitve iz app_config.
    Deluje s Proton Bridge (self-signed cert → brez verifikacije, kot reklama skripta).
    Vrne dict: {"status": "sent", ...} ob uspehu, drugače dvigne napako.
    """
    import smtplib
    import ssl
    from email.message import EmailMessage
    from app.settings import app_config

    if not recipients or len(recipients) == 0:
        raise ValueError("Ni prejemnikov.")

    if not app_config.smtp_user or not app_config.smtp_password:
        raise RuntimeError("SMTP uporabniško ime ali geslo ni nastavljeno (Nastavitve e-pošte).")

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = ", ".join(recipients)
    msg.set_content(body)

    for filename, content, mime in (attachments or []):
        maintype, _, subtype = (mime or "application/octet-stream").partition("/")
        # Text priloge morajo biti bytes (str + maintype pade v Python 3.13)
        if isinstance(content, str):
            content = content.encode("utf-8")
        msg.add_attachment(
            content,
            maintype=maintype or "application",
            subtype=subtype or "octet-stream",
            filename=filename,
        )

    # Proton Bridge (in nekateri lokalni strežniki) imajo self-signed cert
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    host = app_config.smtp_host or "127.0.0.1"
    port = app_config.smtp_port or 1025
    with smtplib.SMTP(host, port, timeout=60) as s:
        if app_config.smtp_tls:
            s.starttls(context=ctx)
        s.login(app_config.smtp_user, app_config.smtp_password)
        s.send_message(msg)

    print(f"EMAIL poslan: {sender} → {', '.join(recipients)} | {subject}")
    return {"status": "sent", "message": f"Poslano na {len(recipients)} naslovov."}


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


def results_pdf_bytes(events, title="Tekmovanja — rezultati", subtitle="Tekmovanje v atletiki za učence 2. in 3. razreda", schools=None, pupils=None, records=None, logo_path=None, slika_path=None, rekordi_title="SUPER STANKO", kraj_datum=""):
    """Pravi PDF z rezultati (reportlab):
    - uvodna stran: naslov, podnaslov, datum, seznam šol, tabela 16 tekmovalnih skupin
      (šport × spol × razred) s številom učencev in rezultatov,
    - nato rezultati po vsaki skupini, razvrščeni po času (najboljši prvi),
    - zadnja stran: razvrstitev šol po točkah,
    - SUPER STANKO: rekordi, ena stran na razred (2. r vse discipline, 3. r vse discipline).
    """
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
    from datetime import datetime

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)

    def _sliki_flow(logo_p, slika_p, max_h=5 * cm, max_logo_w=5 * cm, max_slika_w=6.5 * cm):
        # Logoti in slika na prvi strani (poravnano na sredino, ohranjena razmerja).
        import os
        from reportlab.platypus import Image as RLImage
        from reportlab.lib.utils import ImageReader
        flow = []

        def _one(path, max_w):
            if not path or not os.path.exists(path):
                return None
            try:
                ir = ImageReader(path)
                iw, ih = ir.getSize()
                if iw <= 0 or ih <= 0:
                    return None
                scl = min(max_w / iw, max_h / ih)
                return RLImage(path, width=iw * scl, height=ih * scl)
            except Exception:
                return None

        l = _one(logo_p, max_logo_w)
        s = _one(slika_p, max_slika_w)
        if l is not None or s is not None:
            row = [l if l is not None else "", s if s is not None else ""]
            t = Table([row], colWidths=[max_logo_w, max_slika_w],
                      hAlign="CENTER")
            t.setStyle(TableStyle(
                [("ALIGN", (0, 0), (-1, -1), "CENTER"),
                 ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
            flow.append(t)
            flow.append(Spacer(1, 0.5 * cm))
        return flow

    styles = getSampleStyleSheet()
    font = _reg_fonts()
    styles["Title"].fontName = font
    styles["Heading2"].fontName = font
    styles["Normal"].fontName = font

    schools = schools or []
    pupils = pupils or []

    # 16 skupin: vsaka disciplina × razred × spol
    skupine = []  # (disciplina, razred, spol) — urejeno po disciplini, razredu, spolu
    disc_ids = sorted({ev.discipline_id for ev in events if ev.discipline_id})
    for did in disc_ids:
        for raz in ("2.r", "3.r"):
            for spol in ("M", "Ž"):
                skupine.append((did, raz, spol))

    # Poimenovanje spola
    def spol_ime(s):
        return "fantje" if s == "M" else "dekleta"

    # ── UVODNA STRAN ──────────────────────────────────────────────
    elems = []
    elems += _sliki_flow(logo_path, slika_path)
    elems.append(Paragraph(title, styles["Title"]))
    if subtitle:
        elems.append(Spacer(1, 0.15 * cm))
        elems.append(Paragraph(subtitle, styles["Heading2"]))
    elems.append(Spacer(1, 0.3 * cm))
    if kraj_datum:
        elems.append(Paragraph(kraj_datum, styles["Normal"]))
    elems.append(Spacer(1, 0.4 * cm))
    # PRVA STRAN = samo glava (logotip + naslov + podnaslov + kraj/datum),
    # vse ostalo (šole, pregled skupin, rezultati) se začne na 2. strani.
    elems.append(PageBreak())

    # Seznam šol
    if schools:
        elems.append(Paragraph("Sodelujoče šole:", styles["Heading2"]))
        for s in sorted(schools, key=lambda x: x.name):
            elems.append(Paragraph(f"• {s.name}", styles["Normal"]))
        elems.append(Spacer(1, 0.5 * cm))

    # Pregled skupin: št. učencev in št. rezultatov
    elems.append(Paragraph("Pregled tekmovalnih skupin:", styles["Heading2"]))
    skup = [[_cell_par("Disciplina", font, size=9),
             _cell_par("Razred", font, size=9),
             _cell_par("Spol", font, size=9),
             _cell_par("Učencev", font, size=9),
             _cell_par("Rezultatov", font, size=9)]]
    for did, raz, spol in skupine:
        disc = next((ev.discipline for ev in events if ev.discipline and ev.discipline_id == did), None)
        ime = (disc.name if disc else f"ID {did}").replace("♂", "").replace("♀", "").strip()
        st_uc = sum(1 for p in pupils if p.grade == raz and p.gender == spol)
        st_rez = sum(1 for ev in events
                     if ev.discipline_id == did and ev.pupil
                     and ev.pupil.grade == raz and ev.pupil.gender == spol)
        skup.append([_cell_par(ime, font, size=9),
                     _cell_par(raz, font, size=9),
                     _cell_par(spol_ime(spol), font, size=9),
                     _cell_par(str(st_uc), font, size=9),
                     _cell_par(str(st_rez), font, size=9)])
    t_sk = Table(skup, repeatRows=1,
                 colWidths=[5.0 * cm, 2.2 * cm, 2.5 * cm, 2.6 * cm, 3.0 * cm])
    t_sk.setStyle(TableStyle([
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
    elems.append(t_sk)
    elems.append(PageBreak())

    # ── REZULTATI PO SKUPINAH ─────────────────────────────────────
    for did, raz, spol in skupine:
        disc = next((ev.discipline for ev in events if ev.discipline and ev.discipline_id == did), None)
        ime = (disc.name if disc else f"Disciplina ID {did}").replace("♂", "").replace("♀", "").strip()
        # rezultati te skupine, razvrščeni po času (najboljši prvi)
        rez = [ev for ev in events
               if ev.discipline_id == did and ev.pupil
               and ev.pupil.grade == raz and ev.pupil.gender == spol]
        rez.sort(key=lambda ev: (ev.time if ev.time is not None else float("inf")))
        if not rez:
            continue  # skupina brez rezultatov se ne izpiše posebej
        naslov = f"{ime} — {spol_ime(spol)}, {raz}"
        elems.append(Paragraph(naslov, styles["Heading2"]))
        elems.append(Spacer(1, 0.2 * cm))
        data = [[_cell_par("Mesto", font, size=9),
                 _cell_par("Učenec", font, size=9),
                 _cell_par("Šola", font, size=9),
                 _cell_par("Čas / dolžina", font, size=9),
                 _cell_par("Opombe", font, size=9)]]
        for i, ev in enumerate(rez, 1):
            p = ev.pupil
            data.append([
                _cell_par(str(i) + ".", font, size=9),
                _cell_par(f"{p.first_name} {p.last_name}" if p else "—", font, size=9),
                _cell_par((p.school.name if p and p.school else "") or "—", font, size=9),
                _cell_par(f"{ev.time:.2f} {enota_za_disciplino(ime)}" if ev.time is not None else "—", font, size=9),
                _cell_par((ev.notes or "") or "", font, size=9),
            ])
        t = Table(data, repeatRows=1,
                  colWidths=[1.6 * cm, 4.6 * cm, 4.6 * cm, 2.8 * cm, 2.4 * cm])
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
        elems.append(Spacer(1, 0.5 * cm))

    # ── ZADNJA STRAN: REZULTATI PO ŠOLAH (točke) ──────────────────
    from app.services import points_for_position
    pts = {}
    for ev in events:
        if not ev.pupil or not ev.pupil.school:
            continue
        sid = ev.pupil.school.id
        row = pts.setdefault(sid, {"school_name": ev.pupil.school.name, "points": 0})
        row["points"] += points_for_position(ev.position)
    if pts:
        elems.append(PageBreak())
        elems.append(Paragraph("Rezultati po šolah", styles["Heading2"]))
        elems.append(Paragraph("Razvrstitev po seštevku točk (1. mesto = 15 točk, 2. = 12, …, 12. = 1)",
                               styles["Normal"]))
        elems.append(Spacer(1, 0.3 * cm))
        vrstice = [[_cell_par("Mesto", font, size=9),
                    _cell_par("Šola", font, size=9),
                    _cell_par("Točke", font, size=9)]]
        for i, row in enumerate(sorted(pts.values(), key=lambda x: (-x["points"], x["school_name"])), 1):
            vrstice.append([_cell_par(str(i) + ".", font, size=9),
                            _cell_par(row["school_name"], font, size=9),
                            _cell_par(str(row["points"]), font, size=9)])
        t_skole = Table(vrstice, repeatRows=1, colWidths=[2.2 * cm, 10.0 * cm, 2.6 * cm])
        t_skole.setStyle(TableStyle([
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
        elems.append(t_skole)
        elems.append(Spacer(1, 0.5 * cm))

    # ── SUPER STANKO: REKORDI, ENA STRAN NA RAZRED ─────────────────
    records = records or []
    if records:
        for raz in ("2.r", "3.r"):
            elems.append(PageBreak())
            elems.append(Paragraph(rekordi_title, styles["Title"]))
            elems.append(Spacer(1, 0.2 * cm))
            elems.append(Paragraph(f"Rekordi — {raz} razred (vse discipline)",
                                   styles["Heading2"]))
            elems.append(Spacer(1, 0.3 * cm))
            # rekordi tega razreda, urejeno po disciplini in času
            rek_raz = [r for r in records if getattr(r, "pupil_grade", None) == raz]
            if not rek_raz:
                elems.append(Paragraph("Ni še zabeleženih rekordov.", styles["Normal"]))
                continue
            def _disc_ime(r):
                # Ime discipline iz REKORDA samega (ali "ID n" pri neveljavnem);
                # odstranimo ikoni ♂/♀ — spol in razred imata svoja stolpca/glavo strani.
                ime = (r.discipline.name if r.discipline else f"ID {r.discipline_id}")
                return ime.replace("♂", "").replace("♀", "").strip()
            razvr = sorted(rek_raz, key=lambda r: (
                str(_disc_ime(r)),
                r.time if r.time is not None else float("inf"),
            ))
            data = [[_cell_par("Disciplina", font, size=9),
                     _cell_par("Spol", font, size=9),
                     _cell_par("Učenec", font, size=9),
                     _cell_par("Šola", font, size=9),
                     _cell_par("Rezultat", font, size=9)]]
            for r in razvr:
                spol = getattr(r, "pupil_gender", None) or (r.pupil.gender if r.pupil else None)
                leto = f" ({r.date_recorded.year})" if r.date_recorded else ""
                data.append([
                    _cell_par(_disc_ime(r), font, size=9),
                    _cell_par((spol_ime(spol) if spol in ("M", "Ž") else (spol or "—")), font, size=9),
                    _cell_par((r.pupil_name or "—"), font, size=9),
                    _cell_par((r.school_name or "—"), font, size=9),
                    _cell_par(f"{r.time:.2f} {enota_za_disciplino(_disc_ime(r))}{leto}" if r.time is not None else "—", font, size=9),
                ])
            t_stanko = Table(data, repeatRows=1,
                             colWidths=[3.8 * cm, 1.9 * cm, 4.4 * cm, 4.4 * cm, 3.0 * cm])
            t_stanko.setStyle(TableStyle([
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
            elems.append(t_stanko)
            elems.append(Spacer(1, 0.5 * cm))

    elems.append(Spacer(1, 0.6 * cm))
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

