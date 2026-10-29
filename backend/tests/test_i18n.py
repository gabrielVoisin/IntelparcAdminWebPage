"""The three catalogs (fr, en, de) must have the same keys and the same {placeholders}."""
import json
import re
from pathlib import Path

LOCALES = Path(__file__).resolve().parents[1] / "app" / "locales"
TEMPLATES = Path(__file__).resolve().parents[1] / "app" / "web" / "templates"


def load(lang):
    return json.loads((LOCALES / f"{lang}.json").read_text(encoding="utf-8"))


def test_same_keys_and_placeholders():
    fr, en, de = load("fr"), load("en"), load("de")
    assert fr.keys() == en.keys() == de.keys()
    for key in fr:
        ph = {lang: set(re.findall(r"{(\w+)}", cat[key])) for lang, cat in (("fr", fr), ("en", en), ("de", de))}
        assert ph["fr"] == ph["en"] == ph["de"], key


def test_static_template_keys_exist():
    fr = load("fr")
    used = set()
    for path in TEMPLATES.glob("*.html"):
        used |= set(re.findall(r"""t\('([a-z_]+\.[a-zA-Z0-9_.\-]*[a-zA-Z0-9])'""", path.read_text(encoding="utf-8")))
    missing = sorted(k for k in used if k not in fr)
    assert not missing, missing
