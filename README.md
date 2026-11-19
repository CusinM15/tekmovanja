# Tekmovanja — upravljalec šolskega atletskega tekmovanja

Lokalna spletna aplikacija (FastAPI + SQLite) za vodenje šolskega atletskega
tekmovanja: šole, discipline, učenci, rezultati, rekordi, štartne liste,
pošiljanje e-pošte in uvoz CSV.

Aplikacija teče **lokalno v vašem računalniku** in jo odprete v brskalniku —
nič se ne pošilja na internet. Internet potrebujete samo, če želite pošiljati
e-pošto šolam (SMTP).

Vmesnik je preprost in namenjen tudi uporabnikom, ki niso vešči računalnika:
vse poteka s kliki, brez ukazne vrstice.

---

## 1. Zagon z Dockerjem (najlažje)

Potrebujete samo nameščen **Docker** (in Docker Compose).

```bash
docker compose up -d --build
```

Nato v brskalniku odprite: **http://127.0.0.1:8000/**

Ustavitev:

```bash
docker compose down
```

Podatki so shranjeni v Docker volumnu `tekmovanja_data` in ostanejo tudi po
ustavitvi/ponovnem zagonu. Če želite začeti povsem od začetka:

```bash
docker compose down -v
```

Brez Docker Compose lahko uporabite kar `docker run`:

```bash
docker build -t tekmovanja .
docker run -d --name tekmovanja -p 8000:8000 \
  -e TEKMOVANJA_DATA_DIR=/app/data \
  -v tekmovanja_data:/app/data \
  tekmovanja
```

---

## 2. Zagon brez Dockerja (lokalno, za razvoj)

### Predpogoji

- **Python 3.10+** (testirano na 3.12/3.13)

### Ustvarjanje okolja in zagon

```bash
python -m venv venv
venv\Scripts\pip install -r requirements.txt     # Windows
./venv/bin/pip install -r requirements.txt        # Linux / macOS

venv\Scripts\python run.py                        # Windows
./venv/bin/python run.py                          # Linux / macOS
```

Po nekaj sekundah odprite v brskalniku: **http://127.0.0.1:8000/**

---

## 3. Kako uporabljati (vmesnik)

Vse je v enem oknu z zavihki:

| Zavihek | Kaj počne |
|---|---|
| 📊 Pregled | Števci: šole, učenci, discipline, rezultati, rekordi |
| 🏫 Šole | Dodajanje, urejanje, odstranjevanje šol |
| 👦 Učenci | Dodajanje, urejanje, brisanje, iskanje, uvoz CSV |
| 🏃 Discipline | Dodajanje, urejanje, odstranjevanje disciplin |
| ⏱️ Rezultati | Vnos, urejanje in brisanje rezultatov tekmovanja |
| 🥇 Rekordi | Dodajanje, urejanje in brisanje najboljših rezultatov |
| 📋 Štartne liste | Ustvarjanje štartnih list (disciplina, razred, datum, učenci) |
| 🗄️ Uvoz in pošta | CSV predloga, uvoz, pošiljanje povabil/rezultatov, SMTP, brisanje podatkov |

### Ob prvem zagonu

Baza se samodejno napolni z:

- **5 šolami** (OŠ Žirovnica, OŠ Prežihov Voranc, OŠ Koroška Bela,
  OŠ Josipa Vandota Kranjska Gora, OŠ 16. decembra Mojstrana),
- **8 disciplinami** (tek 60m, tek 400m, skok v daljino, met vorteksa — za fante in punce),
- **10 vzorčnimi učenci**, nekaj rezultati in rekordi — da lahko takoj preizkusite app.

Če želite začeti s prazno bazo, izbrišite datoteko `tekmovanja.db`
(oz. `docker compose down -v`) in znova zaženite.

### Brisanje podatkov tekmovanja

Gumb **🗑️ Počisti podatke tekmovanja** (zavihek *Uvoz in pošta*) trajno
izbriše učence in rezultate tekmovanja. **Šole, discipline in rekordi
(najboljši rezultati) ostanejo.** Za potrditev je treba vpisati `DA`.

---

## 4. Kje so podatki

- **Docker:** v volumnu `tekmovanja_data` (SQLite datoteka `tekmovanja.db`).
- **Lokalno:** v mapi projekta kot `tekmovanja.db` (gitignorano).
- Nastavitve SMTP (če jih shranite v vmesniku) so v `config.json` zunaj
  mape projekta — **gesla se nikoli ne shranjujejo v git**.

---

## 5. Pošiljanje e-pošte (SMTP, neobvezno)

V zavihku **Uvoz in pošta → ⚙️ Nastavitve e-pošte** vnesite svoj Arnes
poštni račun:

- strežnik: `smtp.arne.gov.si`
- vrata: `587`
- uporabniško ime: vaša šolska e-pošta
- geslo

Nato lahko s klikom pošljete **povabila s CSV predlogo** vsem aktivnim šolam
in po koncu tekmovanja še **rezultate**.

---

## 6. Uvoz učencev iz CSV

1. V zavihku *Uvoz in pošta* kliknite **📥 Prenesi prazno predlogo (CSV)**.
2. Predlogo pošljete šolam; one jo izpolnijo in vrnejo.
3. Izpolnjeno datoteko uvozite z **📄 Uvozi izpolnjeno CSV**.

Stolpci: `First Name, Last Name, School, Grade, Gender`
(razred: `2.r` ali `3.r`, spol: `M` ali `Ž`).

---

## 7. Odpravljanje težav

| Težava | Rešitev |
|---|---|
| Vrata 8000 so zasedena | Drug program uporablja 8000. Ustavite ga ali spremenite port v `docker-compose.yml` / `run.py`. |
| `Errno 98: Address already in use` | Kot zgoraj; na Windows: `netstat -ano | findstr :8000`, končajte PID. |
| `ModuleNotFoundError` (lokalno) | Ponovite `pip install -r requirements.txt`. |
| Podatki se ne shranijo (docker) | Preverite, da je volumen `tekmovanja_data` priklopljen (docker compose up). |
| Star UI v brskalniku | Osvežite z **Ctrl+Shift+R** (trdi reload). |

---

## 8. Za razvijalce

- Vstopna točka: `run.py` (uvicorn, `reload=True`) oz. `uvicorn app.main:app` v dockerju.
- Koda: `app/main.py` (vsi API konci), `app/models.py` (podatkovni modeli),
  `app/database.py` (SQLite), `app/settings.py` (nastavitve + privzeti podatki),
  `static/index.html` (celoten vmesnik, brez build koraka).
- Podatkovna baza: SQLite prek SQLAlchemy; ob zagonu se tabele samodejno
  ustvarijo (obstoječi podatki se nikoli ne brišejo).
- Testiranje API-ja: `http://127.0.0.1:8000/docs` (Swagger).

Licenca: MIT (glej `LICENSE`).
