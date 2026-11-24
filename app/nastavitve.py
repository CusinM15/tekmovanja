"""Nastavitve tekmovanja: ime tekmovanja, ime za rekorde ter logotip in slika,
ki sta prikazana na prvi strani PDF rezultatov (Pošlji rezultate šolam).

Podatki se hranijo lokalno v static/nastavitve.json; slike v static/ (privzete)
oziroma static/uploads/ (naložene). Nič ni v config.json, nič ni v bazi.
"""
import os
import json
from pathlib import Path

_DIR = Path(__file__).resolve().parent.parent / "static"
_UPLOAD = _DIR / "uploads"
_FILE = _DIR / "nastavitve.json"

_DEFAULTS = {
    "ime_sole": "OŠ Toneta Čufarja Jesenice",   # šola, ki organizira (v glavi PDF-ov)
    "tekmovanje_title": "STANKOV MEMORIAL",
    "rekordi_title": "SUPER STANKO",
    "kraj_datum": "Jesenice, 2. 10. 2025",
    "logo": "ostcj_logo.png",      # relativno na static/
    "slika": "ostcj_slika.jpeg",
}


def _load() -> dict:
    d = dict(_DEFAULTS)
    if _FILE.exists():
        try:
            with open(_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                for k in _DEFAULTS:
                    if k in data:
                        d[k] = data[k]
        except (json.JSONDecodeError, IOError):
            pass
    return d


def get() -> dict:
    return _load()


def save_update(podatki: dict) -> dict:
    """Združi podano z obstoječim in shrani. Vrne posodobljeno stanje."""
    d = _load()
    for k in _DEFAULTS:
        if k in podatki and podatki[k] is not None:
            d[k] = podatki[k]
    _UPLOAD.mkdir(parents=True, exist_ok=True)
    with open(_FILE, "w", encoding="utf-8") as f:
        json.dump(d, f, ensure_ascii=False, indent=2)
    return d


def abs_path(rel: str) -> Path:
    """Absolutna pot slike pod static/; prepreči uhajanje izven static/."""
    p = (_DIR / str(rel)).resolve()
    pre = _DIR.resolve()
    return p if str(p).startswith(str(pre)) else _DIR / _DEFAULTS.get(rel if rel in _DEFAULTS else "logo", "ostcj_logo.png")


def obstajajo_privzete() -> bool:
    """Ali obstajata privzeta logotip in slika (za prvi zagon)."""
    return (_DIR / str(_DEFAULTS["logo"])).exists() and (_DIR / str(_DEFAULTS["slika"])).exists()