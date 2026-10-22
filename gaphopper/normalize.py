"""Szövegnormalizálás az alias-illesztéshez (magyar és angol)."""
from __future__ import annotations

import hashlib
import os
import re
from functools import lru_cache

_PUNCT = re.compile(r"[\"'„”“’`´()\[\]{}.,;:!?/\\|*_+=<>~^#@&%$–—-]+")
_SPACE = re.compile(r"\s+")
_HU_CHARS = re.compile(r"[áéíóöőúüű]", re.IGNORECASE)

# Szint- és szerepjelzők: munkakörcímből kivehetők, nem különböztetnek munkakört.
SENIORITY = {
    "senior", "junior", "medior", "lead", "principal", "trainee", "intern", "gyakornok",
    "vezető", "vezeto", "főmunkatárs",
    "i", "ii", "iii", "iv", "sr", "jr",
    # Szándékosan NINCS benne: asszisztens, segéd, technikus – ezek más munkakört jelölnek.
}


def detect_lang(text: str) -> str:
    """Egyszerű heurisztika: magyar ékezet -> hu, különben en."""
    return "hu" if _HU_CHARS.search(text or "") else "en"


@lru_cache(maxsize=4)
def _spacy(lang: str):
    """Opcionális lemmatizálás (GH_LEMMATIZE=1 és telepített modell esetén)."""
    if os.getenv("GH_LEMMATIZE") != "1":
        return None
    try:
        import spacy
    except ImportError:
        return None
    name = {"hu": os.getenv("GH_SPACY_HU", "hu_core_news_md"),
            "en": os.getenv("GH_SPACY_EN", "en_core_web_sm")}[lang]
    try:
        return spacy.load(name, disable=["parser", "ner"])
    except OSError:
        return None


def norm_key(text: str, lang: str | None = None) -> str:
    """Kisbetű, írásjelek nélkül, szóközök összevonva, opcionálisan lemmatizálva.

    Az ékezeteket megtartjuk: magyarul jelentést hordoznak (kor / kór).
    """
    t = (text or "").casefold().strip()
    t = _PUNCT.sub(" ", t)
    t = _SPACE.sub(" ", t).strip()
    nlp = _spacy(lang or detect_lang(t))
    if nlp is not None and t:
        t = " ".join(tok.lemma_.casefold() for tok in nlp(t))
    return t


def job_title_key(title: str) -> tuple[str, list[str]]:
    """Munkakörcím kulcsa a szintjelzők nélkül, és a levett jelzők listája."""
    words = norm_key(title).split()
    kept = [w for w in words if w not in SENIORITY]
    removed = [w for w in words if w in SENIORITY]
    return " ".join(kept) or " ".join(words), removed


def alias_tag(text: str, lang: str) -> str:
    return f"{lang}:{text.strip()}"


def stable_id(prefix: str, *parts: str) -> str:
    """Tartalomból képzett, újrafuttatáskor is azonos azonosító."""
    h = hashlib.sha1("|".join(norm_key(p) for p in parts).encode("utf-8")).hexdigest()[:10]
    return f"{prefix}_{h}"
