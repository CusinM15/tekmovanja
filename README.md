# Tekmovanja — upravljalec šolskega atletskega tekmovanja

Lokalna spletna aplikacija (FastAPI + SQLite) za vodenje šolskega atletskega
tekmovanja: šole, discipline, učenci, prireditve, rezultati, rekordi,
pošiljanje e-pošte in uvoz CSV.

Aplikacija teče **lokalno v vašem računalniku** in jo odprete v brskalniku —
nič se ne pošilja na internet. Internet potrebujete samo, če želite pošiljati
e-pošto šolam (SMTP).

---

## 1. Predpogoji

- **Python 3.13** (novejše 3.x večinoma delujejo).
- V mapi projekta je že pripravljeno okolje `venv/` (vsi paketi so v njem).
  Če `venv/` ne obstaja, si ga ustvarite po navodilu v razdelku 3.

Preverite, da imate Python:

```
python --version
```

> Priporočljivo: na Windows v ukazni vrstici uporabite `py` namesto `python`,
> če `python` ne deluje: `py --version`.

---

## 2. Zagon aplikacije

Odprite ukazno vrstico/terminal in pojdite v mapo projekta:

```
cd pot/do/mape/tekmovanja
```

Zaženite aplikacijo:

```
venv\Scripts\python run.py        (Windows)
```

ali

```
./venv/bin/python run.py          (Linux / macOS)
```

Po nekaj sekundah vidite izpis, da strežnik teče na
`http://127.0.0.1:8000`. Pritisnite Ctrl+C za ustavitev.

Odprite v brskalniku:

| Naslov | Kaj je tam |
|---|---|
| `http://127.0.0.1:8000/` | Osnovna stran (JSON sporočilo) |
| `http://127.0.0.1:8000/docs` | **Interaktivna dokumentacija (Swagger UI)** — tukaj preizkusite vse funkcije s klikom |
| `http://127.0.0.1:8000/health` | Preverba, da je aplikacija živa (`{"status":"healthy"}`) |

V `…/docs` lahko vsak konec (API) odprete, kliknete **Try it out**, vnesete
podatke in sprožite klic — brez programiranja.

---

## 3. Ustvarjanje okolja od začetka (če `venv/` manjka)

```
python -m venv venv
venv\Scripts\pip install -r requirements.txt      (Windows)
./venv/bin/pip install -r requirements.txt         (Linux / macOS)
```

---

## 4. Glavne funkcije (vsa navodila po korakih veljajo za `…/docs`)

- **Šole** — `POST /schools` dodaja šolo, `GET /schools` jih izpiše,
  `DELETE /schools/{id}` jo deaktivira (zgodovina ostane), `DELETE /schools`
  jih vse ponovno aktivira.
- **Discipline** — `POST /disciplines` (npr. `tek 60m M`, `met vorteksa Ž`),
  `GET /disciplines`, `PUT /disciplines/{id}`.
- **Učenci** — `POST /pupils` (ime, šola, razred 2.r ali 3.r, spol),
  `GET /pupils`.
- **Rezultati in rekordi** — `POST /events` vnese rezultat,
  `POST /records` izračuna/oživlja rekorde (pri izenačenju se rekord obdrži
  kot dodaten), `GET /records` prikaže najboljše rezultate.
- **Štartne liste** — `POST /start-lists` ustvari štartno listo za
  disciplino in razred; `GET /start-lists` jih izpiše.
- **E-pošta** — `POST /send-emails` pošlje vabila s prazno CSV predlogo vsem
  aktivnim šolam; `POST /send-results` pošlje rezultate. (Zahteva nastavitev
  SMTP — glej razdelek 5.)
- **Uvoz CSV** — `POST /import-csv` uvozi izpolnjene datoteke, preverja
  podatke in preprečuje podvojene vnose.
- **Izpis rezultatov** — `GET /results-pdf` vrne pregled rezultatov.
- **Brisanje podatkov** — `GET /delete-data` pokaže, kaj bi se izbrisalo,
  `POST /delete-data/execute` podatke tekmovanja dejansko izbriše
  (šole, discipline in rekordi ostanejo).

---

## 5. Nastavitev e-pošte (SMTP)

Vso konfiguracijo najdete v `app/settings.py` (zavihek `smtp`):

- `host` — npr. `smtp.arne.gov.si` (Arnes)
- `port` — 587
- `user` / `password` — vaš poštni račun (privzeto je `None`; gesla **nikoli**
  ne shranjujte v spremembe kode/git — vpišite jih samo v nastavitve na
  lokalnem računalniku).

`app/settings.py` vsebuje tudi privzeti seznam šol in disciplin, ki se
ustvarijo ob zagonu.

---

## 6. Pomembno: kje so podatki

**Podatki so trenutno v pomnilniku (in-memory SQLite).** Ob vsakem
ponovnem zagonu aplikacije se podatki izpraznijo in začnete znova
(privzete šole/discipline pa se spet ustvarijo iz `settings.py`).

Če želite trajno bazo, nastavite `sqlite_path` v `app/settings.py`
(npr. `"tekmovanja.db"`) — datoteka nastane v mapi projekta.
Datoteke `*.db` so zavestno izključene iz gita (`.gitignore`).

---

## 7. Odpravljanje težav

| Težava | Rešitev |
|---|---|
| `Errno 98: Address already in use` | Vrata 8000 že zaseda drug program (npr. star strežnik). Ustavite ga: v ukazni vrstici `netstat -ano | findstr :8000` (Windows) oziroma `ss -tlnp | grep 8000` (Linux), dobite PID, in ga končajte; nato zaženite znova. |
| `ModuleNotFoundError` | Okolje ni popolno: ponovite razdelek 3 (`pip install -r requirements.txt`). |
| Aplikacija se ne odpre v brskalniku | Preverite, da je v terminalu izpis strežnika brez napak in da vnašate točno `http://127.0.0.1:8000/`. |
| Naj se strežnik ne ujame | Pritisnite `Ctrl+C` v oknu, kjer teče; če se ne ustavi, končajte proces na vratih 8000 kot zgoraj. |

---

## 8. Za razvijalce

- Vstopna točka: `run.py` (uvicorn, `reload=True`).
- Koda je v mapi `app/` (glavna logika v `app/main.py`, podatkovni modeli v
  `app/models.py`, baza v `app/database.py`, nastavitve v `app/settings.py`,
  pomožne funkcije v `app/utils.py`).
- Vzorec za dostop do baze v katerem koli koncu: `db=Depends(get_db)`
  (ne `db=get_db()` — to FastAPI razume kot query parameter in sproži
  `cannot pickle 'module' object` na vsakem klicu).
- Baza: en skupen SQLAlchemy engine (`app/database.py` → `get_engine()`);
  za `:memory:` se uporablja `StaticPool`, da vsa vlakna vidijo isto bazo.

---

Licenca: MIT (glej `LICENSE`).
