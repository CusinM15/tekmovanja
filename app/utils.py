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
    """Enota rezultata glede na disciplino: skok v daljino in met vorteksa → cm, drugo → s."""
    n = (ime or "").replace("♂", "").replace("♀", "").lower()
    if "skok v daljino" in n or "vorteksa" in n:
        return "cm"
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
        "   Discipline: ID-je 1-4 iz tabele, ločene s presledkom npr. 1 2 4. VAŽNO: vsak\n"
        "   učenec naj nastopa v NAJVEČ 2 disciplinah — če jih vpišeš več, se po uvozu\n"
        "   upošteva zgolj prvi dve).\n"
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


def results_pdf_bytes(events, title="Tekmovanja — rezultati", subtitle="Tekmovanje v atletiki za učence 2. in 3. razreda", schools=None, pupils=None, records=None, logo_path=None, slika_path=None, rekordi_title="SUPER STANKO", kraj_datum="Jesenice, 2. 10. 2025", sola_ime="OSNOVNA ŠOLA TONETA ČUFARJA JESENICE"):
    """PDF z rezultati (reportlab) — 20 strani:
        1.  uvodna stran: šola + logotip/slika + ime tekmovanja + podnaslov + kraj/datum
            (po vzoru Rezultati 2025.docx),
        2.  rezultati po šolah (razvrstitev po točkah),
        3–18. vsaka tekmovalna skupina (4 discipline × M/Ž × 2.r/3.r = 16) na svoji strani,
        19–20. rekordi (2.r, nato 3.r) — SUPER STANKO.
    """
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, BaseDocTemplate, PageTemplate, Frame, Paragraph, Spacer, Table, TableStyle, PageBreak
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buf = BytesIO()

    def _footer(canvas, doc):
        # datum/kraj tekmovanja v nogi (footer) vsake strani
        if not kraj_datum:
            return
        canvas.saveState()
        canvas.setFont(font, 9)
        canvas.setFillColor(colors.HexColor("#888888"))
        canvas.drawCentredString(A4[0] / 2.0, 0.9 * cm, kraj_datum)
        canvas.restoreState()

    # OPOMBA: reportlab 5.1.0 ne sproži onPage ob SimpleDocTemplate -> uporabimo
    # BaseDocTemplate + izrecno PageTemplate (footer (kraj/datum) na vsaki strani).
    _frame = Frame(1.5 * cm, 1.5 * cm, A4[0] - 3 * cm, A4[1] - 3 * cm, id="stran")
    doc = BaseDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm,
                          leftMargin=1.5 * cm, rightMargin=1.5 * cm)
    doc.addPageTemplates([PageTemplate(id="stran", frames=[_frame], onPage=_footer)])

    def _sliki_flow(logo_p, slika_p, max_h=5 * cm, max_logo_w=5 * cm, max_slika_w=6.5 * cm):
        # Logotip in slika na prvi strani (poravnano na sredino, ohranjena razmerja).
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

    def _tab(data, sirine):
        t = Table(data, repeatRows=1, colWidths=sirine)
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
        return t

    def _center(tekst, st=styles["Heading2"]):
        from copy import copy
        cz = copy(st)
        cz.alignment = 1  # center
        return Paragraph(tekst, cz)

    import os as _os
    from reportlab.lib.utils import ImageReader
    from reportlab.platypus import Image as RLImage

    def _im(path, max_w, max_h):
        if not path or not _os.path.exists(path):
            return None
        try:
            ir = ImageReader(path)
            iw, ih = ir.getSize()
            if iw <= 0 or ih <= 0:
                return None
            sc = min(max_w / iw, max_h / ih)
            return RLImage(path, width=iw * sc, height=ih * sc)
        except Exception:
            return None

    def spol_ime(s):
        return "fantje" if s == "M" else "dekleta"

    schools = schools or []
    pupils = pupils or []
    records = records or []

    # 16 skupin (4 × M/Ž × 2.r/3.r).
    # Skupine gradimo IZ DEJANSKIH DISCIPLIN V BAZI (Discipline), ne iz
    # "kdor trenutno ima event". Discipline preživijo gumb "Počisti podatke
    # tekmovanja"; sveži (CompetitionEvent) rezultati pa se na skupino vežejo po
    # DISCIPLINE_ID (ne po krhkem ujemanju imena). Tako je PDF VEDNO 20 strani
    # (uvodna + točke šol + 16 skupin, tudi prazne + SUPER STANKO 2.r/3.r) in
    # "fresh" rezultati, vneseni/uvezeni v app, se na njih vedno pokažejo.
    from app.settings import app_config as _cfg
    from app.database import DBSession as _db
    from app.models import Discipline as _Disc
    _prvi_disc = {k.lower() for k in (getattr(_cfg, "default_disciplines", None) or {})}
    _disci = _db.query(_Disc).all()
    # disciplino iz baze najdemo po normaliziranem imenu (velike/male, presledki
    # na robu) — variacije v imenu ne smejo izbrisati skupine.
    by_norm = {}
    for _d in _disci:
        by_norm[(_d.name or "").lower().strip()] = _d.id
    skupine = []
    for _ime in (getattr(_cfg, "default_disciplines", None) or {}):
        _did = by_norm.get((_ime or "").lower().strip())
        if _did is None:
            continue
        for spol in ("M", "Ž"):
            for raz in ("2.r", "3.r"):
                skupine.append((_did, raz, spol))
    _skupine_ids = {g[0] for g in skupine}
    # evente omejimo na te discipline (sirote/stari športi ne dodajajo strani)
    events = [ev for ev in events
              if ev.discipline and ev.discipline_id in _skupine_ids]

    def _disc_ime(ev, did, prefix="Disciplina ID"):
        # disciplina morda nima nobenega eventa (prazna skupina po "Počisti")
        # -> ev je None; ime vzamemo iz Discipline tabele (_disci že v slogu).
        if ev is not None and ev.discipline:
            ime = ev.discipline.name
        else:
            ime = next((d.name for d in _disci if getattr(d, "id", None) == did),
                       f"{prefix} {did}")
        return (ime or "").replace("♀", "").replace("♂", "").strip()

    # Prijavljeni (registrirani) učenci po skupini (discipline × razred × spol)
    # iz štartnih list — ti naj se na PDF prikažejo TUDI če še nimajo rezultata.
    from app.database import DBSession as _db2
    from app.models import StartList as _SL
    prijavljeni = {}   # (did, raz, spol) -> {pupil_id: pupil}
    try:
        for sl in _db2.query(_SL).all():
            kljuc = (sl.discipline_id, sl.grade, sl.gender)
            m = prijavljeni.setdefault(kljuc, {})
            for p in sl.pupils:
                m[p.id] = p
    except Exception:
        prijavljeni = {}

    elems = []

    # ── 1. UVODNA STRAN ────────────────────────────────────────────
    # zgornji pas: logotip levo zgoraj + ime šole (manjši font) zraven
    logo_img = _im(logo_path, 2.5 * cm, 3.6 * cm)
    ime_sola = Paragraph(sola_ime.rstrip(),
                         ParagraphStyle("cov_sola", fontName=font, fontSize=13,
                                        leading=16, textColor=colors.HexColor("#1F3864"),
                                        alignment=0))
    nav = Table([[logo_img if logo_img else "", ime_sola]],
                colWidths=[3.3 * cm, 11.0 * cm], hAlign="LEFT")
    nav.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "LEFT"),
        ("LEFTPADDING", (0, 0), (-1, -1), 0),
        ("RIGHTPADDING", (0, 0), (-1, -1), 0),
        ("TOPPADDING", (0, 0), (-1, -1), 0),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 0),
    ]))
    elems.append(nav)
    elems.append(Spacer(1, 0.7 * cm))
    # naslov na sredini
    elems.append(Paragraph(title,
        ParagraphStyle("cov_t", fontName=font, fontSize=34, leading=40,
                       textColor=colors.black, alignment=1)))
    # 2 prazni vrstici (za naslovom)
    elems.append(Spacer(1, 1.0 * cm))
    elems.append(Spacer(1, 1.0 * cm))
    # fotografija na sredini
    foto = _im(slika_path, 10.0 * cm, 9.0 * cm)
    if foto:
        elems.append(Spacer(1, 0.3 * cm))
        elems.append(foto)
    # ena prazna vrstica
    elems.append(Spacer(1, 0.6 * cm))
    # podnaslov na sredini (datum je v footerju)
    if subtitle:
        elems.append(Paragraph(subtitle,
            ParagraphStyle("cov_s", fontName=font, fontSize=20, leading=24,
                           textColor=colors.HexColor("#262626"), alignment=1)))
    elems.append(PageBreak())

    # ── 2. REZULTATI PO ŠOLAH (po točkah) ───────────────────────────
    from app.services import points_for_position
    pts = {}
    for ev in events:
        if not ev.pupil or not ev.pupil.school:
            continue
        sid = ev.pupil.school.id
        row = pts.setdefault(sid, {"school_name": ev.pupil.school.name, "points": 0})
        row["points"] += points_for_position(ev.position if ev.position else 0)
    elems.append(Paragraph("Rezultati po šolah", styles["Heading2"]))
    elems.append(Paragraph("Razvrstitev po seštevku točk (1. mesto = 15 točk, 2. = 12, …, 12. = 1).",
                           styles["Normal"]))
    elems.append(Spacer(1, 0.3 * cm))
    vrstice = [[_cell_par("Mesto", font, size=9),
                _cell_par("Šola", font, size=9),
                _cell_par("Točke", font, size=9)]]
    for i, row in enumerate(sorted(pts.values(), key=lambda x: (-x["points"], x["school_name"])), 1):
        vrstice.append([_cell_par(str(i) + ".", font, size=9),
                        _cell_par(row["school_name"], font, size=9),
                        _cell_par(str(row["points"]), font, size=9)])
    elems.append(_tab(vrstice, [2.2 * cm, 10.0 * cm, 2.6 * cm]))
    elems.append(PageBreak())

    # ── 3–18. REZULTATI PO SKUPINAH (16 skupin, ena stran na skupino) ──
    for did, raz, spol in skupine:
        ime = _disc_ime(next((ev for ev in events if ev.discipline and ev.discipline_id == did), None), did)
        rez = {ev.pupil_id: ev for ev in events
               if ev.discipline_id == did and ev.pupil
               and ev.pupil.grade == raz and ev.pupil.gender == spol}
        _prijav = prijavljeni.get((did, raz, spol), {})
        from app.models import disciplina_smer
        # Prijavljeni učenci (registrirani) te skupine, tudi tisti brez rezultata.
        ucenci = []
        for ev in rez.values():
            ucenci.append((ev.pupil_id, ev.pupil, ev))
        for pid, p in _prijav.items():
            if pid not in rez:
                ucenci.append((pid, p, None))
        # sortiraj: tisti z rezultatom po mestu (najboljši 1. na vrhu), brez rezultata zadaj
        def _sort(k):
            pid, p, ev = k
            if ev is None or ev.time is None:
                return (1, 0)
            return (0, ev.time if disciplina_smer(ime) == "lower" else -ev.time)
        ucenci.sort(key=_sort)
        elems.append(Paragraph(f"{ime} — {spol_ime(spol)}, {raz}", styles["Heading2"]))
        if not ucenci:
            elems.append(Spacer(1, 0.3 * cm))
            elems.append(Paragraph("Ni nastopajočih niti rezultatov za to skupino.", styles["Normal"]))
            elems.append(PageBreak())
            continue
        elems.append(Spacer(1, 0.2 * cm))
        data = [[_cell_par("Mesto", font, size=9),
                 _cell_par("Učenec", font, size=9),
                 _cell_par("Šola", font, size=9),
                 _cell_par("Čas / dolžina", font, size=9),
                 _cell_par("Opombe", font, size=9)]]
        for i, (pid, p, ev) in enumerate(ucenci, 1):
            if ev is not None:
                prikaz_mesta = str(i) + "."
                cas_val = f"{ev.time:.2f} {enota_za_disciplino(ime)}" if ev.time is not None else "—"
                opombe = (ev.notes or "") or ""
            else:
                # prijavljen, a brez rezultata — mesto in čas pustimo prazna (—)
                prikaz_mesta = "—"
                cas_val = "—"
                opombe = "prijavljen"
            data.append([
                _cell_par(prikaz_mesta, font, size=9),
                _cell_par(f"{p.first_name} {p.last_name}" if p else "—", font, size=9),
                _cell_par((p.school.name if p and p.school else "") or "—", font, size=9),
                _cell_par(cas_val, font, size=9),
                _cell_par(opombe, font, size=9),
            ])
        elems.append(_tab(data, [1.6 * cm, 4.6 * cm, 4.6 * cm, 2.8 * cm, 2.4 * cm]))
        elems.append(PageBreak())

    # ── 19–20. REKORDI (2.r, nato 3.r) — SUPER STANKO ───────────────
    for raz in ("2.r", "3.r"):
        elems.append(Paragraph(rekordi_title, styles["Title"]))
        elems.append(Spacer(1, 0.2 * cm))
        elems.append(Paragraph(f"Rekordi — {raz} razred (vse discipline)", styles["Heading2"]))
        elems.append(Spacer(1, 0.3 * cm))
        rek_raz = [r for r in records if getattr(r, "pupil_grade", None) == raz]
        if not rek_raz:
            elems.append(Paragraph("Ni še zabeleženih rekordov.", styles["Normal"]))
            elems.append(PageBreak())
            continue
        def _rim(r):
            ime = (r.discipline.name if r.discipline else f"ID {r.discipline_id}")
            return ime.replace("♀", "").replace("♂", "").strip()
        from app.models import disciplina_smer
        def _rrec_key(r):
            ime = _rim(r)
            smer = disciplina_smer(ime)
            t = r.time
            if t is None:
                return (ime, 1, 0)
            return (ime, 0, t if smer == "lower" else -t)
        razvr = sorted(rek_raz, key=_rrec_key)
        data = [[_cell_par("Disciplina", font, size=9),
                 _cell_par("Spol", font, size=9),
                 _cell_par("Učenec", font, size=9),
                 _cell_par("Šola", font, size=9),
                 _cell_par("Rezultat", font, size=9)]]
        for r in razvr:
            spol = getattr(r, "pupil_gender", None) or (r.pupil.gender if r.pupil else None)
            leto = f" ({r.date_recorded.year})" if r.date_recorded else ""
            data.append([
                _cell_par(_rim(r), font, size=9),
                _cell_par((spol_ime(spol) if spol in ("M", "Ž") else (spol or "—")), font, size=9),
                _cell_par((r.pupil_name or "—"), font, size=9),
                _cell_par((r.school_name or "—"), font, size=9),
                _cell_par(f"{r.time:.2f} {enota_za_disciplino(_rim(r))}{leto}" if r.time is not None else "—", font, size=9),
            ])
        elems.append(_tab(data, [3.8 * cm, 1.9 * cm, 4.4 * cm, 4.4 * cm, 3.0 * cm]))
        elems.append(PageBreak())

    doc.build(elems)
    return buf.getvalue()


def start_list_pdf_bytes(start_list, pupils, discipline_name, grade,
                         ime_sole="", tekmovanje_title="Tekmovanje v atletiki",
                         logo_path=None, slika_path=None):
    """Pravi PDF s štartno listo.

    Glava: (1) ime šole + logotip na vrhu, (2) nato druga slika (malo večja),
    (3) pod njo naslov tekmovanja, (4) tabela štartne liste,
    na samem dnu pa datum pošiljanja.
    """
    import os
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle
    from reportlab.platypus import Image as RLImage
    from reportlab.lib.utils import ImageReader
    from reportlab.lib.styles import getSampleStyleSheet
    from datetime import datetime

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4, topMargin=1.5 * cm, bottomMargin=1.5 * cm)
    styles = getSampleStyleSheet()
    font = _reg_fonts()
    for s in ("Title", "Heading2", "Normal"):
        styles[s].fontName = font

    elems = []

    # ── GLAVA: šola (ime) + logotip ───────────────────────────────
    if ime_sole:
        elems.append(Paragraph(ime_sole, styles["Title"]))
        elems.append(Spacer(1, 0.15 * cm))

    def _img(path, max_w, max_h=4.5 * cm):
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

    l = _img(logo_path, 4 * cm, 3 * cm)
    if l is not None:
        t_logo = Table([[l]], colWidths=[4 * cm], hAlign="CENTER")
        t_logo.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                    ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        elems.append(t_logo)
        elems.append(Spacer(1, 0.3 * cm))

    # druga slika — malo večja od logotipa (naslovna fotografija)
    s_img = _img(slika_path, 6.5 * cm, 5 * cm)
    if s_img is not None:
        t_slika = Table([[s_img]], colWidths=[6.5 * cm], hAlign="CENTER")
        t_slika.setStyle(TableStyle([("ALIGN", (0, 0), (-1, -1), "CENTER"),
                                     ("VALIGN", (0, 0), (-1, -1), "MIDDLE")]))
        elems.append(t_slika)
        elems.append(Spacer(1, 0.3 * cm))

    # naslov tekmovanja pod slikami
    elems.append(Paragraph(tekmovanje_title, styles["Heading2"]))
    elems.append(Spacer(1, 0.4 * cm))

    # disciplina / razred
    elems.append(Paragraph(f"{discipline_name} — {grade} razred", styles["Title"]))
    elems.append(Spacer(1, 0.3 * cm))

    data = [[_cell_par("#", font, size=10),
             _cell_par("Učenec", font, size=10),
             _cell_par("Razred", font, size=10),
             _cell_par("Šola", font, size=10),
             _cell_par("Rezultat", font, size=10)]]
    for i, pupil in enumerate(pupils, start=1):
        data.append([
            _cell_par(str(i), font, size=10),
            _cell_par(f"{pupil.first_name} {pupil.last_name}", font, size=10),
            _cell_par(pupil.grade or "", font, size=10),
            # Šola razširjena + Paragraph: "OŠ Josipa Vandota Kranjska Gora" se
            # zlomi znotraj celice in ne prelije čez rob.
            _cell_par(pupil.school.name if pupil.school else "", font, size=10),
            _cell_par("", font, size=10),  # prostor za vpis rezultata
        ])
    if len(data) == 1:
        data.append([_cell_par("1", font, size=10),
                     _cell_par("Ni učencev na listi.", font, size=10),
                     _cell_par("", font, size=10),
                     _cell_par("", font, size=10),
                     _cell_par("", font, size=10)])

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

    # ── čisto spodaj: datum pošiljanja ─────────────────────────────
    elems.append(Spacer(1, 0.8 * cm))
    elems.append(Paragraph(f"Datum pošiljanja: {datetime.now().strftime('%d.%m.%Y %H:%M')}",
                           styles["Normal"]))

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


def razpis_pdf_bytes(title="STANKOV MEMORIAL", podnaslov="Razpis za prijavo učencev 2. in 3. razreda", sola_ime="OSNOVNA ŠOLA TONETA ČUFARJA JESENICE", kraj_datum="Jesenice, 2. 10. 2025"):
    """Generira psevdo-razpis PDF (reportlab), ki se pripne k vabilom, dokler
    uporabnik na strani Nastavitve ne naloži lastnega PDF razpisa.

    Vrne bytes PDF-ja, ki ga lahko pošljemo kot prilogo (filename='razpis.pdf')."""
    from io import BytesIO
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import cm
    from reportlab.lib import colors
    from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
    from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle

    buf = BytesIO()
    doc = SimpleDocTemplate(buf, pagesize=A4,
                            topMargin=2 * cm, bottomMargin=2 * cm,
                            leftMargin=2 * cm, rightMargin=2 * cm)
    font = _reg_fonts()
    styles = getSampleStyleSheet()
    styles["Title"].fontName = font
    styles["Heading2"].fontName = font
    styles["Normal"].fontName = font

    def _st(tekst, name="Normal", **kw):
        st = ParagraphStyle(name, fontName=font, fontSize=kw.get("fontSize", 11),
                            leading=kw.get("leading", 15),
                            alignment=kw.get("alignment", 0),
                            spaceAfter=kw.get("spaceAfter", 8))
        return Paragraph(tekst, st)

    elems = []
    # glava: ime šole (bela na temno modri) + naslov razpisa
    head = Paragraph(sola_ime.upper(),
                     ParagraphStyle("hd", fontName=font, fontSize=13, leading=16,
                                    textColor=colors.white, alignment=1,
                                    backColor=colors.HexColor("#1F3864")))
    from reportlab.platypus import Table, TableStyle as _TS
    t = Table([[head]], colWidths=[A4[0] - 4 * cm])
    t.setStyle(_TS([("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#1F3864")),
                    ("TOPPADDING", (0, 0), (-1, -1), 8),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 8)]))
    elems.append(t)
    elems.append(Spacer(1, 0.6 * cm))
    elems.append(_st(title, "t1", fontSize=24, leading=28, alignment=1, spaceAfter=6))
    elems.append(_st(podnaslov, "t2", fontSize=14, leading=20, alignment=1, spaceAfter=8, ))
    elems.append(_st(kraj_datum or "", "kd", fontSize=12, leading=16, alignment=1, spaceAfter=14))
    elems.append(Spacer(1, 0.4 * cm))
    besedilo = (
        "Spoštovani ravnatelj in organizatorji športa,\n\n"
        "vabimo vas, da prijavite svoje učence 2. in 3. razreda na šolsko atletsko "
        "tekmovanje. Tekmuje se v štirih disciplinah:\n\n"
        "• tek 60 m\n"
        "• tek 400 m\n"
        "• skok v daljino\n"
        "• met vorteksa\n\n"
        "Učenci se prijavijo v kategoriji po razredu (2. oz. 3. razred) in spolu. "
        "Rok ter podrobnejša navodila najdete v priloženem vabilu in na strani "
        "tekmovanja.\n\n"
        "Prosimo, da prijave izpolnite v priloženi predlogi in nam jo vrnete do "
        "navedenega roka.\n\n"
        "Lep pozdrav,\n"
        + (sola_ime or "Organizator tekmovanja")
    )
    elems.append(_st(besedilo))
    doc.build(elems)
    return buf.getvalue()

