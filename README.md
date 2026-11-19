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

## 1. Zagon (lokalno)

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

## 2. Kako uporabljati (vmesnik)

Vse je v enem oknu z zavihki:

| Zavihek | Kaj počne |
|---|---|
| 📊 Pregled | Števci: šole, učenci, discipline, rezultati, rekordi |
| 🏫 Šole | Dodajanje, urejanje, odstranjevanje šol |
| 👦 Učenci | Dodajanje, urejanje, brisanje, iskanje, uvoz CSV |
| 🏃 Discipline | Dodajanje, urejanje, odstranjevanje disciplin |
| ⏱️ Rezultati | Vnos, urejanje in brisanje rezultatov tekmovanja |
| 🥇 Rekordi | Dodajanje, urejanje in brisanje najboljših rezultatov + pogled "Najboljši po disciplinah" |
| 📋 Štartne liste | Ustvarjanje štartnih list (disciplina, razred, datum, učenci) |
| 🗄️ Uvoz in pošta | CSV predloga, uvoz, pošiljanje povabil/rezultatov, SMTP, brisanje podatkov |

### Ob prvem zagonu

Baza se samodejno napolni s:

- **5 šolami** (OŠ Žirovnica, OŠ Prežihov Voranc, OŠ Koroška Bela,
  OŠ Josipa Vandota Kranjska Gora, OŠ 16. decembra Mojstrana),
- **8 disciplinami** (tek 60m, tek 400m, skok v daljino, met vorteksa — za fante in punce),
- **10 vzorčnimi učenci**, nekaj rezultati in rekordi — da lahko takoj preizkusite app.

Seed je **idempotenten**: tudi če določena šola ali disciplina manjka, se ob
naslednjem zagonu doda. Učenci in rekordi se dodajo samo, če jih še ni.

### Urejanje učencev

Pri vsakem učencu lahko s klikom:
- **✏️ Uredi** — popraviš tipkarsko napako v imenu, priimku, razredu, spolu ali šoli,
- **🗑️ Izbriši** — trajno odstraniš učenca (skupaj z njegovimi rezultati in rekordi).

### Brisanje podatkov tekmovanja

Gumb **🗑️ Počisti podatke tekmovanja** (zavihek *Uvoz in pošta*) trajno
izbriše učence in rezultate tekmovanja. **Šole, discipline in rekordi
(najboljši rezultati) ostanejo.** Za potrditev je treba vpisati `DA`.

---

## 3. Kje so podatki

- Podatki so v datoteki `tekmovanja.db` v mapi projekta (gitignorano).
- Nastavitve SMTP (če jih shranite v vmesniku) so v `config.json` zunaj
  mape projekta — **gesla se nikoli ne shranjujejo v git**.

---

## 4. Pošiljanje e-pošte (SMTP, neobvezno)

V zavihku **Uvoz in pošta → ⚙️ Nastavitve e-pošte** vnesite svoj Arnes
poštni račun:

- strežnik: `smtp.arne.gov.si`
- vrata: `587`
- uporabniško ime: vaša šolska e-pošta
- geslo

Nato lahko s klikom pošljete **povabila s CSV predlogo** vsem aktivnim šolam
in po koncu tekmovanja še **rezultate**.

---

## 5. Uvoz učencev iz CSV

1. V zavihku *Uvoz in pošta* kliknite **📥 Prenesi prazno predlogo (CSV)**.
2. Predlogo pošljete šolam; one jo izpolnijo in vrnejo.
3. Izpolnjeno datoteko uvozite z **📄 Uvozi izpolnjeno CSV**.

Stolpci: `First Name, Last Name, School, Grade, Gender`
(razred: `2.r` ali `3.r`, spol: `M` ali `Ž`).

---

## 6. Odpravljanje težav

| Težava | Rešitev |
|---|---|
| Vrata 8000 so zasedena | Drug program uporablja 8000. Ustavite ga ali spremenite port v `run.py`. |
| `Errno 98: Address already in use` | Kot zgoraj; na Windows: `netstat -ano | findstr :8000`, končajte PID. |
| `ModuleNotFoundError` | Ponovite `pip install -r requirements.txt`. |
| Star UI v brskalniku | Osvežite z **Ctrl+Shift+R** (trdi reload). |
| Podatki šol se ne pokažejo | Preverite, da je `tekmovanja.db` v mapi projekta; seed se izvede ob zagonu. |

---

## 7. Za razvijalce

- Vstopna točka: `run.py` (uvicorn, `reload=True`).
- Koda: `app/main.py` (vsi API konci), `app/models.py` (podatkovni modeli),
  `app/database.py` (SQLite), `app/settings.py` (nastavitve + privzeti podatki),
  `static/index.html` (celoten vmesnik, brez build koraka).
- Podatkovna baza: SQLite prek SQLAlchemy; ob zagonu se tabele samodejno
  ustvarijo (obstoječi podatki se nikoli ne brišejo).
- Testiranje API-ja: `http://127.0.0.1:8000/docs` (Swagger).

Licenca: MIT (glej `LICENSE`).