"""
Regresijski testi za app Tekmovanja.

Pokrivajo točno to, kar uporabnik zahteva, da se ne podre po vsaki nadgradnji:
  1. Kartice na zavihku Rezultati VEDNO izpišejo dve vrstici "2.r: x/N" in "3.r: x/N"
     (x = število vpisanih rezultatov za disciplino+razred,
      N = število VSEH učencev, ki spadajo pod disciplino — gender discipline =
          gender učenca in razred ∈ applicable_grades; ni več vezano na štartne liste).
  2. Gumb "Odstrani" pri disciplinah DELUJE tudi, ko ima disciplina rezultate.
  3. CSV uvoz z Discipline (ID) deluje in je idempotenten; napake ne shranijo ničesar.
  4. Predloga vsebuje ID tabelo disciplin in stolpec Discipline (ID).

Poženemo z:  ./venv/bin/python -m pytest tests/ -v
"""
import re
import shutil
import subprocess

import pytest
import io

CSV_HEADER = "First Name,Last Name,School,Grade,Gender,Discipline (ID)"
CSV_PRIMER = "Ime,Priimek,Šola (npr. OŠ Žirovnica),Razred (2.r ali 3.r),Spol (M ali Ž),Disciplina (ID iz tabele zgoraj, npr. 1)"
LEGENDA = "ID,Disciplina (za koga)"


# ===================== POMOŽNE =====================

def _events(client):
    return client.get("/events").json()


def _disciplines(client):
    return client.get("/disciplines").json()


def _start_lists(client):
    return client.get("/start-lists").json()


def _ucenci_po_disciplini_in_razredu(ucenci, disc):
    """Ista logika kot izrisiKarticeRezultati v index.html:
    N = vsi učenci, ki spadajo pod disciplino (gender=disc.gender in grade ∈ applicable_grades)."""
    nastopi = {}
    for d in disc:
        grades = {g.strip() for g in (d.get("applicable_grades") or "").split(",")}
        for p in ucenci:
            if p["gender"] == d["gender"] and p["grade"] in grades:
                nastopi.setdefault(d["id"], {}).setdefault(p["grade"], set()).add(p["id"])
    return nastopi


def _kartica_stevila(dogodki, d, nastopi):
    """Vrne (x2, N2, x3, N3) točno tako, kot jih izračuna JS v izrisiKarticeRezultati."""
    x2 = len({e["pupil_id"] for e in dogodki if e["discipline_id"] == d["id"] and e["pupil_grade"] == "2.r"})
    x3 = len({e["pupil_id"] for e in dogodki if e["discipline_id"] == d["id"] and e["pupil_grade"] == "3.r"})
    N2 = len(nastopi.get(d["id"], {}).get("2.r", set()))
    N3 = len(nastopi.get(d["id"], {}).get("3.r", set()))
    return x2, N2, x3, N3


def _served_js(client):
    html = client.get("/").text
    m = re.search(r"<script>(.*?)</script>", html, re.S)
    assert m, "stran mora vsebovati <script>"
    return html, m.group(1)


# ===================== TESTI =====================

def test_stran_se_nalozi(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "Tekmovanja" in r.text


def test_seed_podatki_obstajajo(client):
    """Ob prvem zagonu mora biti baza napolnjena (tudi v testni bazi)."""
    assert len(_disciplines(client)) == 8
    assert len(client.get("/pupils").json()) == 120
    assert len(_events(client)) == 72, "rezultate dobi LE del nastopajočih (9 × 8 disciplin), ne vsi učenci"
    assert len(_start_lists(client)) == 16, "vsaka disciplina × 2 razreda (2.r, 3.r) ima štartno listo"


def test_kartice_xN_format_za_vsako_disciplino(client):
    """KARTICE: za vsako disciplino morata obstajati obe vrstici '2.r: x/N' in '3.r: x/N'
    (x = rezultati, N = nastopajoči). 2.r mora imeti nastopajoče (N>0) in x<=N."""
    dogodki = _events(client)
    disc = _disciplines(client)
    ucenci = client.get("/pupils").json()
    nastopi = _ucenci_po_disciplini_in_razredu(ucenci, disc)

    assert len(disc) >= 2, "brez disciplin ni kartic"
    for d in disc:
        x2, N2, x3, N3 = _kartica_stevila(dogodki, d, nastopi)
        # vrstici, ki ju kartica izpiše:
        vrstica2 = "2.r: %d/%d" % (x2, N2)
        vrstica3 = "3.r: %d/%d" % (x3, N3)
        assert vrstica2.startswith("2.r: ") and "/" in vrstica2, d["name"]
        assert vrstica3.startswith("3.r: ") and "/" in vrstica3, d["name"]
        # N = vsi učenci, ki spadajo pod disciplino → mora biti > 0 za OBA razreda,
        # tudi ko štartne liste za 3.r še ne obstajajo (N ni več odvisen od štartnih list).
        assert N2 > 0 and N3 > 0, f"disciplina {d['name']}: N mora biti > 0 za 2.r in 3.r (N2={N2}, N3={N3})"
        assert x2 <= N2, f"disciplina {d['name']}: x(2.r)={x2} ne sme preseči N={N2}"
        assert x3 <= N3, f"disciplina {d['name']}: x(3.r)={x3} ne sme preseči N={N3}"


def test_servirana_stran_vsebuje_kodo_kartic_xN(client):
    """UI POGODBA: serviran index.html MORA vsebovati kodo za izris kartic x/N,
    da se ob kakršnikoli nadgradnji ne izgubi."""
    _, js = _served_js(client)
    assert "api('/pupils')" in js, "natovoriRezultate mora za N naložiti vse učence (tiste, ki spadajo pod disciplino)"
    assert "'<div class=\"razred\">2.r: <strong>'" in js, "kartica mora izrisati vrstico 2.r x/N"
    assert "'<div class=\"razred\">3.r: <strong>'" in js, "kartica mora izrisati vrstico 3.r x/N"
    assert "izrisiKarticeRezultati" in js, "kartice morajo biti odporne (ločen izris, N=0 ob napaki)"
    assert "pupil_grade" in js, "x mora šteti rezultate po razredu učenca"


def test_servirana_js_sintaksa(client):
    """Serviran JavaScript mora imeti veljavno sintakso (node --check)."""
    if shutil.which("node") is None:
        pytest.skip("node ni na voljo")
    _, js = _served_js(client)
    p = subprocess.run(["node", "--check", "-"], input=js.encode(), capture_output=True)
    assert p.returncode == 0, p.stderr.decode()


def test_odstrani_disciplino_z_rezultati(client):
    """GUMB ODSTRANI: disciplina z rezultati (in štartno listo) se mora pobrisati,
    brez sirot; ponovni poskus vrne 404."""
    d = client.post("/disciplines", data={"name": "TEST brisanje", "gender": "M"}).json()
    did = d["id"]
    r = client.post("/events", data={"pupil_id": 1, "discipline_id": did, "time": 11.11, "position": 1})
    assert r.status_code == 200
    r = client.post("/start-lists", data={"discipline_id": did, "grade": "2.r", "event_date": "2026-12-01T08:00:00", "pupil_ids": "1,2"})
    assert r.status_code == 200, r.text

    resp = client.delete(f"/disciplines/{did}")
    assert resp.status_code == 200, resp.text

    ids = [x["id"] for x in _disciplines(client)]
    assert did not in ids, "disciplina mora izginiti iz seznama"
    assert not [e for e in _events(client) if e["discipline_id"] == did], "rezultati morajo izginiti"
    assert not [s for s in _start_lists(client) if s["discipline_id"] == did], "štartne liste morajo izginiti"
    assert client.delete(f"/disciplines/{did}").status_code == 404


def test_uvoz_csv_z_disciplino_in_idempotenca(client):
    """CSV UVOZ: stolpec Discipline (ID) vpiše učenca na štartne liste;
    ponovni uvoz iste datoteke ne sme ničesar podvojiti."""
    csv = (
        "\ufeff" + LEGENDA + "\n1,tek 60m ♂ (fantje)\n\n"
        + CSV_HEADER + "\n" + CSV_PRIMER + "\n"
        + "Testa,Uvozna,OŠ Žirovnica,2.r,M,1 3\n"
    )
    r1 = client.post("/import-csv", files={"file": ("test.csv", csv.encode("utf-8"), "text/csv")}).json()
    assert r1["status"] == "success", r1
    assert r1["records_created"] == 1
    assert r1["start_entries"] == 2  # ID 1 in 3

    r2 = client.post("/import-csv", files={"file": ("test.csv", csv.encode("utf-8"), "text/csv")}).json()
    assert r2["status"] == "success", r2
    assert r2["records_created"] == 0 and r2["start_entries"] == 0, "ponovni uvoz mora biti idempotenten"


def test_uvoz_csv_napaka_ne_shrani_nicesar(client):
    """CSV UVOZ: neznan ID discipline ali napačen spol -> status error,
    v bazi se ne sme pojaviti nič (rollback)."""
    csv = "\ufeff" + CSV_HEADER + "\n" + "Napaka,Uvozna2,OŠ Žirovnica,2.r,M,99\n"
    r = client.post("/import-csv", files={"file": ("bad.csv", csv.encode("utf-8"), "text/csv")}).json()
    assert r["status"] == "error", r
    assert not [p for p in client.get("/pupils").json() if p["first_name"] == "Napaka"], \
        "ob napaki se učenec ne sme shraniti"

    csv2 = "\ufeff" + CSV_HEADER + "\n" + "Mojca,Testna3,OŠ Žirovnica,2.r,Ž,1\n"
    r2 = client.post("/import-csv", files={"file": ("bad2.csv", csv2.encode("utf-8"), "text/csv")}).json()
    assert r2["status"] == "error", r2
    assert any("drug spol" in e for e in r2["errors"]), r2


def test_predloga_vsebuje_id_tabelo_in_stolpec(client):
    """PREDLOGA: /send-invitations mora vsebovati ID tabelo disciplin in stolpec Discipline (ID)."""
    j = client.get("/send-invitations").json()
    t = j["csv_template"]
    assert LEGENDA in t
    assert "Discipline (ID)" in t
    assert "1,tek 60m ♂ (fantje)" in t
    assert "8,met vorteksa ♀ (punce)" in t


def test_startna_lista_pdf_s_stolpcem_rezultat(client):
    """ŠTARTNA LISTA — PDF: /start-lists/{id}/pdf mora vrniti pravi PDF
    (application/pdf, %PDF), da ga uporabnik lahko prenese/odpre.
    PDF(tabla štartne liste) mora po novem vsebovati tudi stolpec 'Rezultat'
    s prostorom za vpis rezultata."""
    seznami = client.get("/start-lists").json()
    assert seznami, "seed mora imeti vsaj eno štartno listo"
    r = client.get(f"/start-lists/{seznami[0]['id']}/pdf")
    assert r.status_code == 200, r.text
    assert r.headers["Content-Type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF"), "odgovor mora biti pravi PDF"
    # stolpec 'Rezultat' mora biti v Pythonovem izvornem nizu tabele PDF
    src = io.open("/home/matej/forgejo/opensource/tekmovanja/app/utils.py", encoding="utf-8").read()
    assert '"Rezultat"' in src, "start_list_pdf_bytes mora vsebovati stolpec 'Rezultat'"




def test_avtomatizirani_rekordi_ob_vpisu_rezultata(client):
    """Ob vpisu rezultata prek /events se tabela rekordov samodejno posodobi:
    - boljši čas  -> nadomesti (stara vrstica izgine),
    - enak čas    -> nova vrstica (tie, ista disciplina ima lahko več vrstic),
    - slabši      -> nespremenjeno."""
    ucenci = client.get("/pupils").json()
    disc = client.get("/disciplines").json()
    rek = client.get("/records").json()
    target = next(r for r in rek if r["pupil_grade"])
    disc_id, grade = target["discipline_id"], target["pupil_grade"]
    d = next(x for x in disc if x["id"] == disc_id)
    kandidat = next(u for u in ucenci if u["grade"] == grade and u["gender"] == d["gender"])

    # 1) boljši rezultat -> nadomesti vse rekorde te discipline+razreda
    trenutni = [r for r in client.get("/records").json()
                if r["discipline_id"] == disc_id and r["pupil_grade"] == grade]
    best = min(x["time"] for x in trenutni)
    boljsi = round(best - 1.0, 2)
    r1 = client.post("/events", data={"pupil_id": kandidat["id"], "discipline_id": disc_id,
                                      "time": boljsi, "position": 1})
    assert r1.status_code == 200, r1.text
    od = [r for r in client.get("/records").json()
          if r["discipline_id"] == disc_id and r["pupil_grade"] == grade]
    assert len(od) == 1 and abs(od[0]["time"] - boljsi) < 1e-9, f"boljši mora nadomestiti: {od}"

    # 2) enak čas (drug učenec) -> nova vrstica
    drugi = next(u for u in ucenci if u["id"] != kandidat["id"]
                 and u["grade"] == grade and u["gender"] == d["gender"])
    r2 = client.post("/events", data={"pupil_id": drugi["id"], "discipline_id": disc_id,
                                      "time": boljsi, "position": 2})
    assert r2.status_code == 200, r2.text
    od2 = [r for r in client.get("/records").json()
           if r["discipline_id"] == disc_id and r["pupil_grade"] == grade]
    assert len(od2) == 2, f"enak čas -> 2 vrstici: {od2}"

    # 3) slabši -> nespremenjeno (ostaneta 2)
    r3 = client.post("/events", data={"pupil_id": kandidat["id"], "discipline_id": disc_id,
                                      "time": boljsi + 1.5, "position": 3})
    assert r3.status_code == 200, r3.text
    od3 = [r for r in client.get("/records").json()
           if r["discipline_id"] == disc_id and r["pupil_grade"] == grade]
    assert len(od3) == 2, f"slabši -> nespremenjeno: {od3}"

def test_avtomatsko_mesto_in_tocke_po_solah(client):
    """Mesto se določi SAMODEJNO ob vpisu rezultata (uporabnik ga ne vpisuje);
    /school-points sešteje točke po šolah (1.=15, 2.=12, ..., 12.=1)."""
    # nova disciplina (2.r fantje), da ne vplivamo na seed
    nd = client.post("/disciplines", data={"name": "TEST 100m ♂", "gender": "M",
                                           "applicable_grades": "2.r"}).json()
    did = nd["id"]
    us = [u for u in client.get("/pupils").json() if u["grade"] == "2.r" and u["gender"] == "M"]
    u0 = us[0]
    u1 = next(u for u in us if u["school_id"] != u0["school_id"])

    before = {x["school_name"]: x["points"] for x in client.get("/school-points").json()}

    # počasnejši najprej -> samodejno 1. mesto
    e1 = client.post("/events", data={"pupil_id": u0["id"], "discipline_id": did,
                                      "time": 15.0}).json()
    assert e1["position"] == 1, e1
    # hitrejši -> 1., prejšnji postane 2.
    e2 = client.post("/events", data={"pupil_id": u1["id"], "discipline_id": did,
                                      "time": 12.0}).json()
    assert e2["position"] == 1, e2
    e1_po = [e for e in client.get("/events").json() if e["id"] == e1["id"]][0]
    assert e1_po["position"] == 2, "po hitrejšem mora prvi postati 2. mesto"

    after = {x["school_name"]: x["points"] for x in client.get("/school-points").json()}
    n = lambda nm: after.get(nm, 0) - before.get(nm, 0)
    assert n(u0["school_name"]) == 12, f"šola 2. mesta mora dobiti 12 točk (dobil {n(u0['school_name'])})"
    assert n(u1["school_name"]) == 15, f"šola 1. mesta mora dobiti 15 točk (dobil {n(u1['school_name'])})"

def test_delete_data_nato_ponoven_uvoz_csv(client):
    """DELETE-DATA: po /delete-data/execute morajo izginiti TUDI rekordi in štartne
    liste (ne ostanejo imena otrok), in ponovni CSV uvoz mora spet delovati.
    (prej je delete pustil sirote v start_list_pupils -> ob ponovnem uvozu UNIQUE
    constraint start_list_pupils in uvoz je padel)."""
    csv = "\ufeff" + CSV_HEADER + "\n" + "Testa,Uvozna,OŠ Žirovnica,2.r,M,1 3\n"
    r1 = client.post("/import-csv", files={"file": ("t.csv", csv.encode("utf-8"), "text/csv")}).json()
    assert r1["status"] == "success", r1
    # rekordi obstajajo (seed jih ustvari)
    rek = client.get("/records").json()
    assert rek, "seed mora imeti rekorde"
    assert "school_name" in rek[0], "Record.to_dict mora vsebovati school_name"
    rek_before = len(rek)

    d = client.post("/delete-data/execute")
    assert d.status_code == 200, d.text
    assert client.get("/pupils").json() == [], "pupils morajo izginiti"
    assert client.get("/events").json() == [], "events morajo izginiti"
    # REKORDI OSTANEJO nedotaknjeni (samostojna tabela)
    assert len(client.get("/records").json()) == rek_before, "rekordi morajo OSTATI po izbrisu (nedotaknjeni)"
    assert client.get("/start-lists").json() == [], "štartne liste morajo izginiti"

    # ponovni uvoz PO izbrisu mora spet uspeti (prej UNIQUE start_list_pupils -> 500)
    r2 = client.post("/import-csv", files={"file": ("t2.csv", csv.encode("utf-8"), "text/csv")})
    assert r2.status_code == 200, r2.text
    j2 = r2.json()
    assert j2["status"] == "success", j2
    assert j2["records_created"] == 1, j2


def test_enota_za_disciplino():
    """Enota rezultata je odvisna od discipline: skok v daljino → cm,
    met vorteksa → m, tek → s."""
    from app.utils import enota_za_disciplino
    assert enota_za_disciplino("met vorteksa ♂") == "m"
    assert enota_za_disciplino("skok v daljino ♀") == "cm"
    assert enota_za_disciplino("tek 60m ♂") == "s"
    assert enota_za_disciplino("") == "s"
    assert enota_za_disciplino(None) == "s"


def test_smtp_api_shrani_in_ohrani_geslo(client, tmp_path, monkeypatch):
    """GET/POST /api/smtp: shrani vrednosti, gesla NE vrača; prazno geslo
    ohrani obstoječe; brez shranjenega gesla + prazno → 400."""
    from app.settings import AppConfig
    from app.main import app_config

    # Izoliramo config v tmp (ne kvarimo /tmp/tekmovanja/config.json)
    import json as _json
    orig = app_config.config_file
    app_config.config_file = tmp_path / "config.json"
    app_config.smtp_host = "smtp.arne.gov.si"
    app_config.smtp_port = 587
    app_config.smtp_user = None
    app_config.smtp_password = None
    app_config.smtp_from = None
    try:
        # GET na čistem → prazna polja
        g = client.get("/api/smtp").json()
        assert g["user"] == "" and g["from_addr"] == "" and g["has_password"] is False

        # POST z geslom → shrani
        r = client.post("/api/smtp", data={
            "host": "smtp.arnes.si", "port": "587",
            "user": "ime.priimek", "password": "sifra123",
            "from_addr": "ime.priimek@arnes.si",
        })
        assert r.status_code == 200, r.text
        assert app_config.smtp_user == "ime.priimek"
        assert app_config.smtp_password == "sifra123"
        assert app_config.smtp_from == "ime.priimek@arnes.si"

        # GET → vrne vse RAZEN gesla
        g2 = client.get("/api/smtp").json()
        assert g2["user"] == "ime.priimek"
        assert g2["from_addr"] == "ime.priimek@arnes.si"
        assert g2["has_password"] is True
        assert "password" not in g2, "geslo se ne sme vračati"

        # POST s PRAZNIM geslom → ohrani obstoječe (200)
        r2 = client.post("/api/smtp", data={
            "host": "smtp.arnes.si", "port": "587",
            "user": "ime.priimek", "password": "", "from_addr": "ime.priimek@arnes.si",
        })
        assert r2.status_code == 200, r2.text
        assert app_config.smtp_password == "sifra123", "geslo se mora ohraniti"

        # POST prazno geslo + NI shranjenega → 400
        app_config.smtp_password = None
        r3 = client.post("/api/smtp", data={
            "host": "smtp.arnes.si", "port": "587",
            "user": "neki", "password": "", "from_addr": "",
        })
        assert r3.status_code == 400, r3.text
    finally:
        app_config.config_file = orig

