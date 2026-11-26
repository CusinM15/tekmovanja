"""Pytest konfiguracija: testi tečejo na LOČENI začasni bazi (TEKMOVANJA_DB),
da se produkcijska tekmovanja.db nikoli ne dotakne. Env spremenljivko moramo
nastaviti PRED importom app.main (init_db + seed tečeta ob importu)."""
import os
import tempfile

os.environ["TEKMOVANJA_DB"] = os.path.join(
    tempfile.mkdtemp(prefix="tekmovanja-test-"), "tekmovanja.db"
)
# Testi pričakujejo demo učence/rekordae: omogoči seed (v produkciji je izklopljen,
# da se podatki vnašajo samo skozi aplikacijo in se baza ohrani).
os.environ["TEKMOVANJA_SEED"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture(scope="session")
def client():
    with TestClient(app) as c:
        yield c
