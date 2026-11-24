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
        nevtralen = not (d.get("gender") or "")
        for p in ucenci:
            if p["grade"] not in grades:
                continue
            if (not nevtralen) and p["gender"] != d["gender"]:
                continue
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
    assert len(_disciplines(client)) == 4, "samo 4 discipline (športi: tek 60m, tek 400m, skok, met)"
    assert len(client.get("/pupils").json()) == 120
    assert len(_events(client)) == 72, "rezultate dobi LE del nastopajočih iz OBEH spolov (9 × 4 discipline × fantje+dekleta)"
    assert len(_start_lists(client)) == 16, "4 športi × 2 razreda × 2 spola = 16 štartnih list"


def test_kartice_xN_format_za_vsako_disciplino(client):
    """KARTICE: za vsako disciplino morata obstajati obe vrstici '2.r: x/N' in '3.r: x/N'
    (x = rezultati, N = nastopajoči). 2.r mora imeti nastopajoče (N>0) in x<=N."""
    dogodki = _events(client)
    disc = _disciplines(client)
    ucenci = client.get("/pupils").json()
    nastopi = _ucenci_po_disciplini_in_razredu(ucenci, disc)

    assert len(disc) == 4, "mora biti 4 discipline (spol×razred)"
    for d in disc:
        x2, N2, x3, N3 = _kartica_stevila(dogodki, d, nastopi)
        veljavni = [g.strip() for g in (d["applicable_grades"] or "").split(",")]
        for razred, x, N in (("2.r", x2, N2), ("3.r", x3, N3)):
            if razred not in veljavni:
                continue
            assert N > 0, f"disciplina {d['name']}: N({razred}) mora biti > 0 (N={N})"
            assert x <= N, f"disciplina {d['name']}: x({razred})={x} ne sme preseči N={N}"


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
    """CSV UVOZ (nov format): šola zgoraj, razred 2/3; disciplina se določi SAMODEJNO
    iz spola+razreda. Ponovni uvoz iste datoteke ne sme ničesar podvojiti."""
    csv = ("\ufeff"
           "*** NAVODILA ***\n"
           "ŠOLA:\nOŠ Žirovnica\n"
           "\n"
           "First Name, Last Name, Grade, Gender, Discipline (IDs)\n"
           "Testa,Uvozna,2,Ž,2\n")
    r1 = client.post("/import-csv", files={"file": ("t.csv", csv.encode("utf-8"), "text/csv")}).json()
    assert r1["status"] == "success", r1
    assert r1["records_created"] == 1
    assert r1["start_entries"] == 1, "ena disciplina (ID 2 → tek 400m, 2.r, Ž)"

    r2 = client.post("/import-csv", files={"file": ("t.csv", csv.encode("utf-8"), "text/csv")}).json()
    assert r2["status"] == "success", r2
    assert r2["records_created"] == 0 and r2["start_entries"] == 0, "ponovni uvoz mora biti idempotenten"


def test_uvoz_csv_napaka_ne_shrani_nicesar(client):
    """CSV UVOZ: neveljaven spol ali razred -> status error, nič se ne shrani (rollback)."""
    def uvozi(vrstica):
        csv = ("\ufeffŠOLA:\nOŠ Žirovnica\n\n"
               "First Name, Last Name, Grade, Gender, Discipline (IDs)\n" + vrstica + "\n")
        return client.post("/import-csv", files={"file": ("b.csv", csv.encode("utf-8"), "text/csv")}).json()
    r = uvozi("Napaka,Uvozna2,2,X,1")
    assert r["status"] == "error", r
    assert not [p for p in client.get("/pupils").json() if p["first_name"] == "Napaka"]
    r2 = uvozi("Mojca,Testna3,4,Ž,1")
    assert r2["status"] == "error", r2
    assert not [p for p in client.get("/pupils").json() if p["first_name"] == "Mojca"]


def test_predloga_vsebuje_glavo_in_sola(client):
    """PREDLOGA: /send-invitations vsebuje navodila in polje ŠOLA ter glavo
    (Razred 2/3, brez stolpca Discipline)."""
    j = client.get("/send-invitations").json()
    gl = j["csv_template"]
    assert "Ime, Priimek, Razred, Spol, Discipline (ID)" in gl
    assert "ŠOLA:" in gl
    assert "*** NAVODILA" in gl
    assert "First Name" not in gl


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
    """Ob vpisu rezultata prek /events se tabela rekordov samodejno posodobi
    (skupina = šport × spol × razred):
    - boljši čas  -> nadomesti (stara vrstica izgine),
    - enak čas    -> nova vrstica (tie, ista skupina ima lahko več vrstic),
    - slabši      -> nespremenjeno."""
    ucenci = client.get("/pupils").json()
    disc = client.get("/disciplines").json()
    rek = client.get("/records").json()
    target = next(r for r in rek if r["pupil_grade"] and r.get("pupil_gender"))
    disc_id, grade, spol = target["discipline_id"], target["pupil_grade"], target["pupil_gender"]

    def filtriraj():
        return [r for r in client.get("/records").json()
                if r["discipline_id"] == disc_id and r["pupil_grade"] == grade
                and r["pupil_gender"] == spol]

    kandidat = next(u for u in ucenci if u["grade"] == grade and u["gender"] == spol)

    # 1) boljši rezultat -> nadomesti vse rekorde te skupine
    trenutni = filtriraj()
    best = min(x["time"] for x in trenutni)
    boljsi = round(best - 1.0, 2)
    r1 = client.post("/events", data={"pupil_id": kandidat["id"], "discipline_id": disc_id,
                                      "time": boljsi, "position": 1})
    assert r1.status_code == 200, r1.text
    od = filtriraj()
    assert len(od) == 1 and abs(od[0]["time"] - boljsi) < 1e-9, f"boljši mora nadomestiti: {od}"

    # 2) enak čas (drug učenec) -> nova vrstica
    drugi = next(u for u in ucenci if u["id"] != kandidat["id"]
                 and u["grade"] == grade and u["gender"] == spol)
    r2 = client.post("/events", data={"pupil_id": drugi["id"], "discipline_id": disc_id,
                                      "time": boljsi, "position": 2})
    assert r2.status_code == 200, r2.text
    od2 = filtriraj()
    assert len(od2) == 2, f"enak čas -> 2 vrstici: {od2}"

    # 3) slabši -> nespremenjeno (ostaneta 2)
    r3 = client.post("/events", data={"pupil_id": kandidat["id"], "discipline_id": disc_id,
                                      "time": boljsi + 1.5, "position": 3})
    assert r3.status_code == 200, r3.text
    od3 = filtriraj()
    assert len(od3) == 2, f"slabši -> nespremenjeno: {od3}"


def test_smer_skok_met_tek(client):
    """Pri skoku/metu je boljši VEČJI rezultat, pri teku pa MANJŠI — mesto 1.
    novi disciplini (brez seed rezultatov), da je preverba nedvoumna."""
    us = [u for u in client.get("/pupils").json() if u["grade"] == "2.r" and u["gender"] == "M" and u.get("school_id")]
    u0, u1 = us[0], us[1]
    # skok (večji = 1. mesto)
    sk = client.post("/disciplines", data={"name": "SKOK met test", "gender": "M",
                                          "applicable_grades": "2.r"}).json()
    r1 = client.post("/events", data={"pupil_id": u0["id"], "discipline_id": sk["id"], "time": 2.0}).json()
    r2 = client.post("/events", data={"pupil_id": u1["id"], "discipline_id": sk["id"], "time": 5.0}).json()
    assert r2["position"] == 1, f"skok: večji 5.0 mora biti 1. (dobil {r2['position']})"
    def poz(ev):
        return next(e for e in client.get("/events").json() if e["id"] == ev["id"])["position"]
    assert poz(r1) == 2, f"skok: manjši 2.0 mora biti 2. (dobil {poz(r1)})"
    # tek (manjši = 1. mesto)
    te = client.post("/disciplines", data={"name": "TEK test", "gender": "M",
                                          "applicable_grades": "2.r"}).json()
    f1 = client.post("/events", data={"pupil_id": u0["id"], "discipline_id": te["id"], "time": 10.0}).json()
    f2 = client.post("/events", data={"pupil_id": u1["id"], "discipline_id": te["id"], "time": 8.0}).json()
    assert f2["position"] == 1, f"tek: manjši 8.0 mora biti 1. (dobil {f2['position']})"
    assert poz(f1) == 2, f"tek: večji 10.0 mora biti 2. (dobil {poz(f1)})"


def test_rekord_smer_met_vs_tek(client):
    """REKORD: pri metu/skoku je rekord NAJVEČJI rezultat, pri teku NAJMANJŠI.
    (Smer MESTA že pokriva test_smer_skok_met_tek; tale preverja še avtomatski
    rekord prek realnih /events → _sync_records_after_result.)"""
    us = [u for u in client.get("/pupils").json() if u["grade"] == "2.r" and u["gender"] == "M"]
    u0, u1 = us[0], us[1]

    def rekordi(disc_id):
        return [r for r in client.get("/records").json()
                if r["discipline_id"] == disc_id and r["pupil_grade"] == "2.r"
                and r["pupil_gender"] == "M"]

    # met (higher): daljši je rekord → 5.0, ne 2.0
    met = client.post("/disciplines", data={"name": "MET rekord test", "gender": "M",
                                            "applicable_grades": "2.r"}).json()
    client.post("/events", data={"pupil_id": u0["id"], "discipline_id": met["id"], "time": 2.0})
    client.post("/events", data={"pupil_id": u1["id"], "discipline_id": met["id"], "time": 5.0})
    mr = rekordi(met["id"])
    assert mr and abs(max(r["time"] for r in mr) - 5.0) < 1e-9,         f"met rekord mora biti daljši (5.0): {mr}"

    # tek (lower): hitrejši je rekord → 8.0, ne 10.0
    te = client.post("/disciplines", data={"name": "TEK rekord test", "gender": "M",
                                           "applicable_grades": "2.r"}).json()
    client.post("/events", data={"pupil_id": u0["id"], "discipline_id": te["id"], "time": 10.0})
    client.post("/events", data={"pupil_id": u1["id"], "discipline_id": te["id"], "time": 8.0})
    tr = rekordi(te["id"])
    assert tr and abs(min(r["time"] for r in tr) - 8.0) < 1e-9,         f"tek rekord mora biti hitrejši (8.0): {tr}"


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



def test_uvoz_slovenska_glava(client):
    """UVOZ: sprejme tudi SLOVENSKO glavo (Ime, Priimek, Razred, Spol, Discipline (ID))."""
    csv = ("\ufeffŠOLA:\nOŠ Žirovnica\n\n"
           "Ime, Priimek, Razred, Spol, Discipline (ID)\n"
           "Slovenska,Glava,3,Ž,1 2 4\n")
    j = client.post("/import-csv", files={"file": ("s.csv", csv.encode("utf-8"), "text/csv")}).json()
    assert j["status"] == "success", j
    # novo pravilo: vsak učenec nastopa v največ 2 disciplina → od "1 2 4" le 2
    assert j["records_created"] == 1 and j["start_entries"] == 2, j


def test_uvoz_vecih_disciplin_brez_narekovajev(client):
    """'1,2,4' BREZ narekovajev csv razbije na več celic — uvoz mora vseeno
    prebrati VSE discipline (združi celice od Discipline naprej), ne samo prve."""
    csv = ("\ufeffŠOLA:\nOŠ Koroška Bela\n\n"
           "First Name, Last Name, Grade, Gender, Discipline (IDs)\n"
           "Več,Disciplin,2,M,1,2,4\n")
    j = client.post("/import-csv", files={"file": ("m.csv", csv.encode("utf-8"), "text/csv")}).json()
    assert j["status"] == "success", j
    # novo pravilo: največ 2 disciplina na učenca → od "1,2,4" le 2 (1 in 2)
    assert j["start_entries"] == 2, j


def test_delete_data_nato_ponoven_uvoz_csv(client):
    """DELETE-DATA: po /delete-data/execute morajo izginiti TUDI rekordi in štartne
    liste (ne ostanejo imena otrok), in ponovni CSV uvoz mora spet delovati.
    (prej je delete pustil sirote v start_list_pupils -> ob ponovnem uvozu UNIQUE
    constraint start_list_pupils in uvoz je padel)."""
    csv = ("\ufeff*** NAVODILA ***\nŠOLA:\nOŠ Prežihov Voranc\n\n"
           "First Name, Last Name, Grade, Gender, Discipline (IDs)\n"
           "Zala,Mivšek,3,Ž,2\n")
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
    """Enota rezultata je odvisna od discipline: skok v daljino in met vorteksa → cm,
    tek → s."""
    from app.utils import enota_za_disciplino
    assert enota_za_disciplino("met vorteksa ♂") == "cm"
    assert enota_za_disciplino("skok v daljino ♀") == "cm"
    assert enota_za_disciplino("tek 60m ♂") == "s"
    assert enota_za_disciplino("") == "s"


def test_disciplina_smer_met_vorteksa():
    """met vorteksa je MET → 'higher' (dlje = bolje), ČEPRAV vsebuje podniz 'tek'.
    Regresijski: prej disciplina_smer('met vorteksa') ni preverila podniza 'tek' v
    'vorTEKsa' in je napačno vrnila 'lower' (teki='manjši=boljši')."""
    from app.models import disciplina_smer
    assert disciplina_smer("met vorteksa") == "higher"
    assert disciplina_smer("met vorteksa ♂") == "higher"
    assert disciplina_smer("skok v daljino") == "higher"
    assert disciplina_smer("tek 60m") == "lower"
    assert disciplina_smer("tek 60m ♂") == "lower"
    assert disciplina_smer("tek 400m") == "lower"


def test_smtp_api_shrani_in_ohrani_geslo(client, tmp_path, monkeypatch):
    """GET/POST /api/smtp: shrani vrednosti; geslo se VRNE (obrazec ga prikaže
    maskirano ****** + 👁 prikaz); prazno geslo ohrani obstoječe; brez shranjenega
    gesla + prazno → 400."""
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
            "sender_name": "Ime Priimek",
        })
        assert r.status_code == 200, r.text
        assert app_config.smtp_user == "ime.priimek"
        assert app_config.smtp_password == "sifra123"
        assert app_config.smtp_from == "ime.priimek@arnes.si"

        # GET → vrne vse, TUDI geslo (za maskiran prikaz in 👁 razkritje)
        g2 = client.get("/api/smtp").json()
        assert g2["user"] == "ime.priimek"
        assert g2["from_addr"] == "ime.priimek@arnes.si"
        assert g2["has_password"] is True
        assert g2["password"] == "sifra123", "geslo se vrne za prikaz (maskirano) v obrazcu"
        assert g2["sender_name"] == "Ime Priimek", "ime in priimek pošiljatelja za podpis"

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


def test_send_email_gre_res_prek_smtp(monkeypatch):
    """send_email mora dejansko iti prek SMTP (login + send_message), ne samo printati."""
    from app.settings import app_config
    from app.utils import send_email

    # zapolnimo SMTP nastavitve (testne, izmišljene)
    app_config.smtp_host = "127.0.0.1"
    app_config.smtp_port = 1025
    app_config.smtp_user = "test@test.si"
    app_config.smtp_password = "sifra"
    app_config.smtp_tls = False
    app_config.smtp_from = "posiljatelj@test.si"

    calls = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout=60):
            calls["init"] = (host, port)
        def __enter__(self):
            return self
        def __exit__(self, *a):
            return False
        def starttls(self, context=None):
            calls["starttls"] = True
        def login(self, user, pw):
            calls["login"] = (user, pw)
        def send_message(self, msg):
            calls["send_message"] = msg

    monkeypatch.setattr("smtplib.SMTP", FakeSMTP)

    res = send_email(
        subject="Test",
        sender="posiljatelj@test.si",
        recipients=["a@test.si", "b@test.si"],
        body="Zdravo",
        attachments=[("priloga.csv", "a,b\n1,2\n", "text/csv")],
    )
    assert res["status"] == "sent"
    assert calls["init"] == ("127.0.0.1", 1025)
    assert calls["login"] == ("test@test.si", "sifra")
    msg = calls["send_message"]
    assert msg["From"] == "posiljatelj@test.si"
    assert msg["To"] == "a@test.si, b@test.si"
    assert msg["Subject"] == "Test"
    # priloga mora biti v sporočilu (multipart)
    assert msg.is_multipart(), "sporočilo s prilogo mora biti multipart"

    # brez SMTP nastavitev → napaka, ne tiho printanje
    app_config.smtp_password = None
    import pytest
    with pytest.raises(RuntimeError):
        send_email(subject="T", sender="s", recipients=["a@b.si"], body="b")


def test_send_emails_endpoint_ne_laze(client, monkeypatch):
    """/send-emails mora ob napaki vrniti sporočilo o napaki, ne 'poslano'."""
    from app.utils import send_email

    def boom(*a, **k):
        raise RuntimeError("SMTP nedosegljiv (test)")

    monkeypatch.setattr("app.utils.send_email", boom)

    r = client.post("/send-emails")
    assert r.status_code == 200, r.text
    j = r.json()
    # ne sme trditi, da je poslal
    assert "Pošiljanje ni uspelo" in j["message"], j
    assert "poslal" not in j["message"].lower().replace("pošiljanje", ""), j


def test_uvoz_novega_formata_sola_zgoraj_in_razred_23(client):
    """NOVI CSV format: šola se vpiše SAMO ENKRAT zgoraj (polje »ŠOLA:«),
    v glavi ni stolpca School, razred je samo 2 ali 3 (brez »\.r«)."""
    csv = ("\ufeff"
           "*** NAVODILA — KAKO IZPOLNIŠ DATOTEKO ***\r\n"
           "1. V polje ŠOLA zgoraj vpiši ime šole.\r\n"
           "ŠOLA:\r\n"
           "OŠ Žirovnica\r\n"
           "\r\n"
           "First Name, Last Name, Grade, Gender, Discipline (ID)\r\n"
           "Nova,Učenka,2,Ž,2 4\r\n"
           "Nov,Deček,3,M,1 3\r\n") 
    r = client.post("/import-csv", files={"file": ("novi.csv", csv.encode("utf-8"), "text/csv")})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "success", j
    assert j["records_created"] == 2, j
    ucenci = client.get("/pupils").json()
    n = [x for x in ucenci if x["first_name"] == "Nova"]
    d = [x for x in ucenci if x["first_name"] == "Nov"]
    assert n and n[0]["grade"] == "2.r" and n[0]["school_name"] == "OŠ Žirovnica", n
    assert d and d[0]["grade"] == "3.r" and d[0]["school_name"] == "OŠ Žirovnica", d


def test_results_pdf_uvodna_stran_in_16_skupin(client):
    """/results-pdf: pravi PDF z uvodno stranjo (naslov STANKOV MEMORIAL,
    podnaslov, sodelujoče šole) + pregled tekmovalnih skupin (šport×spol×razred)."""
    r = client.get("/results-pdf")
    assert r.status_code == 200, r.text
    assert r.headers["Content-Type"].startswith("application/pdf")
    assert r.content.startswith(b"%PDF"), "odgovor mora biti pravi PDF"
    assert len(r.content) > 5000, "PDF mora vsebovati vsebino (ne prazen)"

    src = io.open("/home/matej/forgejo/opensource/tekmovanja/app/utils.py", encoding="utf-8").read()
    # naslov tekmovanja prihaja iz nastavitev (app/nastavitve.py privzeto)
    assert "STANKOV MEMORIAL" in io.open(
        "/home/matej/forgejo/opensource/tekmovanja/app/nastavitve.py", encoding="utf-8").read()
    # uvodna stran (po vzoru Rezultati 2025.docx): šola + naslov + podnaslov + kraj/datum
    assert "sola_ime" in src, "uvodna stran ima ime šole"
    assert "OSNOVNA ŠOLA TONETA ČUFARJA JESENICE" in src
    assert "kraj_datum" in src
    assert "PageBreak" in src, "uvodna stran mora biti ločena od rezultatov"
    # 2. stran: razvrstitev šol po točkah
    assert "Rezultati po šolah" in src
    assert "points_for_position" in src, "točke po šolah se računajo iz mest"
    # 16 skupin: vsaka disciplina × M/Ž × 2.r/3.r, vsaka na svoji strani
    assert 'for spol in ("M", "Ž")' in src
    assert 'for raz in ("2.r", "3.r")' in src
    assert "skupine" in src
    # SUPER STANKO: rekordi, ena stran na razred (2.r nato 3.r)
    assert "SUPER STANKO" in src
    assert '("2.r", "3.r")' in src, "ena stran na razred (2.r in 3.r)"
