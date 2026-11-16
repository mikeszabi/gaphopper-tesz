"""Egyéni profil kivonatolása öt forrásból (a kompetenciának nem kell szó szerint szerepelnie a szövegben).

- EXPLICIT: a szöveg kimondja a kompetenciát („magabiztos Excel-használat”).
- IMPLICIT: a leírt tevékenységből következik („10 fős csapatot irányítottam” → vezetés).
- MUNKAKOR: a betöltött munkakörökből. Ha az AI a tapasztalatot egy felbontott munkakörhöz rendeli, a kompetenciákat
  a program örökíti a gráfprofilból (ugyanabból, amelyet a labor 1. funkciója mutat); ha nincs megfelelő munkakör,
  az AI következtet a munkakör tipikus kompetenciáira.
- KEPZES: a megemlített képzésekből és végzettségekből, a képzés EQF-szintjével.
- STILUS: a dokumentum általános stílusából (felépítés, fogalmazás, pontosság); idézet nélkül is, legfeljebb közepes
  bizonyossággal.

Egy kompetencia több forrásból is jöhet: egy sorba kerül a források listájával. Az összesített bizonyosság
1 - Π(1 - c_i), az EQF-szint a nem stílus alapú források legmagasabb értéke (stílusból nem becsülünk szintet).
"""
from __future__ import annotations

import logging
import re
from collections import Counter

from fastapi import HTTPException
from gaphopper.llm import LLMError

from .models import PersonIdentification
from .service import norm

SOURCE_HU = {"EXPLICIT": "explicit", "IMPLICIT": "implicit", "MUNKAKOR": "munkakör", "KEPZES": "képzés",
             "STILUS": "stílus", "KEZI": "kézi"}
STYLE_MAX_CONFIDENCE = 0.6
MAX_TOKENS = 24000
log = logging.getLogger(__name__)
INHERIT = {"base": 0.35, "importance": 0.35, "long_bonus": 0.1, "long_years": 2, "short_years": 1}

INSTRUCTION = (
    "Egy személy önéletrajza vagy más szakmai szövege alapján állítsd össze a kompetenciaprofilját a "
    "kompetencia-adatbázisból (competencies). A kompetenciának NEM kell szó szerint szerepelnie: öt forrásból "
    "dolgozz, és minden találatnál add meg a forrás típusát (forras).\n"
    "1. EXPLICIT: a szöveg kimondja a kompetenciát vagy készséget.\n"
    "2. IMPLICIT: a leírt tevékenységből, eredményből, felelősségből egyértelműen következik.\n"
    "3. MUNKAKOR: a betöltött munkakörökből. Előbb gyűjtsd ki a munkatapasztalatokat (work_experience: a megnevezés "
    "a szövegből, évek száma, ha kiszámítható, szó szerinti idézet). Ha egy tapasztalat egyértelműen megfelel a "
    "jobs lista egy munkakörének, add meg a job_id-t: ilyenkor a program a munkakör kompetenciáit maga örökíti, te "
    "NE adj hozzá MUNKAKOR-találatot. Csak a job_id nélküli tapasztalatokhoz adj MUNKAKOR-találatot (ref: a "
    "tapasztalat sorszáma nullától), a munkakör tipikus, a leírással összhangban lévő kompetenciáira.\n"
    "4. KEPZES: a képzésekből, végzettségekből, tanfolyamokból. Gyűjtsd ki őket (trainings: név, a képesítés "
    "EQF-szintje, ha megállapítható, pl. szakmunkás 3-4, technikus 5, BSc 6, MSc 7, PhD 8; szó szerinti idézet), "
    "és add meg a képzés által tipikusan fejlesztett kompetenciákat (ref: a képzés sorszáma nullától).\n"
    "5. STILUS: a dokumentum egészének stílusából (felépítés, tagoltság, fogalmazás, pontosság, hangnem), pl. "
    "írásbeli kommunikáció, strukturált gondolkodás, részletekre figyelés. Itt nem kötelező az idézet, de a "
    "reason mezőben konkrétan írd le, mi a stílusban az alap; confidence legfeljebb 0,6.\n"
    "Az EXPLICIT, IMPLICIT, MUNKAKOR és KEPZES találatokhoz legalább egy rövid, szó szerinti idézet kell "
    "(evidence). Az idézetek pontosan, betűre egyezzenek a szöveggel (ne vonj össze sorokat, ne javíts); találatonként legfeljebb 2 rövid idézet, a reason egyetlen rövid mondat. Legfeljebb 80 találatot adj, a legjobban alátámasztottakat. Ugyanaz a kompetencia több forrásból is szerepelhet, de egy forrástípuson és hivatkozáson belül "
    "csak egyszer. Minden találathoz becsüld meg az eqf mezőben (1-8), milyen szinten gyakorolja a személy "
    "(tudás mélysége, műveletek összetettsége, önállóság és felelősség: 3 rutinfeladat felügyelet mellett; 4 önálló "
    "munka ismert keretek között; 5 önálló, változó helyzetekben is, kisebb csapat irányítása; 6 összetett, "
    "kiszámíthatatlan helyzetek, szakmai döntések és felelősség; 7 új megközelítések, stratégiai felelősség; 8 "
    "kutatási élvonal); STILUS-nál hagyd üresen. Az eqf mezőbe a személy egészére vonatkozó szintbecslés kerüljön "
    "rövid magyar indoklással. Ne következtess névből, életkorból, nemből, nemzetiségből, egészségből vagy más "
    "érzékeny személyes jellemzőből. A confidence a saját bizonyossági becslésed (0..1). Bizonytalanságot a "
    "warnings mezőben jelezz.")


QUOTE_MIN_COVERAGE = 0.85


def _plain(text: str) -> str:
    """Idézetegyezéshez: kisbetű, egységes szóköz, írásjelek (kötőjelek, idézőjelek, felsorolásjelek) nélkül."""
    t = norm(text)
    t = re.sub(r"[‐-―−–—\-•·●▪◦*„”“\"'`´’‘«»()\[\]{}:;,.!?/|]", " ", t)
    return " ".join(t.split())


def quote_ok(quote: str, plain_text: str, words: set[str]) -> bool:
    """Szó szerinti egyezés írásjelek nélkül; ha nincs, az idézet szavainak legalább 85%-a szerepel a szövegben
    (a PDF-ből kinyert szöveg elválasztásai, összevont sorai miatt)."""
    q = _plain(quote)
    if not q:
        return False
    if q in plain_text:
        return True
    toks = [w for w in q.split() if len(w) > 2]
    return bool(toks) and sum(w in words for w in toks) / len(toks) >= QUOTE_MIN_COVERAGE


def make_check(text: str, catalog: dict, job_ids: set[str]):
    """Szerkezeti ellenőrzés (ezt a modell egy újrapróbálással javíthatja): létező azonosítók és érvényes
    hivatkozások. Az idézetek ellenőrzése utólag, találatonként történik (sanitize), hogy egyetlen pontatlan
    idézet ne buktassa el az egész profilt."""

    def check(o: PersonIdentification):
        for e in o.work_experience:
            if e.job_id is not None and e.job_id not in job_ids:
                raise ValueError(f"Ismeretlen job_id: {e.job_id}; csak a jobs listából választhatsz, vagy hagyd üresen.")
        unknown = sorted({m.id for m in o.matches if m.id not in catalog})
        if unknown:
            raise ValueError(f"Ismeretlen kompetenciaazonosítók: {unknown[:10]}; csak a competencies lista id-it használd.")
        for m in o.matches:
            if m.forras == "MUNKAKOR" and (m.ref is None or m.ref >= len(o.work_experience)):
                raise ValueError("MUNKAKOR-találatnál a ref a work_experience érvényes sorszáma legyen.")
            if m.forras == "KEPZES" and (m.ref is None or m.ref >= len(o.trainings)):
                raise ValueError("KEPZES-találatnál a ref a trainings érvényes sorszáma legyen.")
    return check


def sanitize(o: PersonIdentification, text: str) -> tuple[PersonIdentification, list[str]]:
    """Utólagos tisztítás: a nem ellenőrizhető idézetek kimaradnak; az idézet nélkül maradt (nem stílus alapú)
    találat kiesik; az ismétlődő (kompetencia, forrás, hivatkozás) hármas összevonódik; a már örökölt munkakörhöz
    adott MUNKAKOR-találat elmarad. A változásokról figyelmeztetés készül."""
    plain = _plain(text)
    words = set(plain.split())
    keep = lambda qs: [q for q in qs if quote_ok(q, plain, words)]   # noqa: E731
    notes = Counter()
    for e in o.work_experience:
        e.evidence = keep(e.evidence) or e.evidence[:0]
    for t in o.trainings:
        t.evidence = keep(t.evidence) or t.evidence[:0]
    out, seen = [], set()
    for m in o.matches:
        key = (m.id, m.forras, m.ref)
        if key in seen:
            notes["dup"] += 1
            continue
        if m.forras == "MUNKAKOR" and o.work_experience[m.ref].job_id:
            notes["inherited"] += 1
            continue
        good = keep(m.evidence)
        if len(good) < len(m.evidence):
            notes["quote"] += len(m.evidence) - len(good)
        if m.forras != "STILUS" and not good:
            notes["dropped"] += 1
            continue
        m.evidence = good
        seen.add(key)
        out.append(m)
    o.matches = out
    warnings = []
    if notes["dropped"]:
        warnings.append(f"{notes['dropped']} találat kimaradt, mert az idézete nem volt megtalálható a szövegben.")
    if notes["quote"] - notes["dropped"] > 0:
        warnings.append(f"{notes['quote']} nem ellenőrizhető idézet elhagyva (a találat más idézettel megmaradt).")
    return o, warnings


def inherit(exp: dict, profile: dict) -> list[dict]:
    """A megfeleltetett munkakör alap (CORE) TESZ-SZÍN-jei által igényelt kompetenciák, az elvárt szinten
    (rövid, egy évnél kevesebb tapasztalatnál egy szinttel lejjebb)."""
    years = exp.get("years")
    out = []
    for c in profile["competencies"]:
        if not c["core"]:
            continue
        eqf = c["required_eqf"]
        if eqf and years is not None and years < INHERIT["short_years"]:
            eqf = max(1, eqf - 1)
        conf = INHERIT["base"] + INHERIT["importance"] * c["importance"]
        if years is not None and years >= INHERIT["long_years"]:
            conf += INHERIT["long_bonus"]
        out.append({"id": c["id"], "type": "MUNKAKOR", "confidence": round(min(conf, 0.9), 3), "eqf": eqf,
                    "evidence": exp["evidence"][:1], "ref_label": profile["title"],
                    "reason": f"A(z) „{profile['title']}” munkakör alapfeladatai igénylik (fontosság "
                              f"{round(c['importance'] * 100)}%, elvárt EQF {c['required_eqf'] or '–'}).",
                    "inherited": True})
    return out


def merge(sources: list[dict], catalog: dict) -> list[dict]:
    by: dict[str, dict] = {}
    for s in sources:
        c = by.setdefault(s["id"], {"id": s["id"], "label": catalog[s["id"]]["label"], "sources": []})
        c["sources"].append({k: v for k, v in s.items() if k != "id"} | {"type_hu": SOURCE_HU[s["type"]]})
    out = []
    for c in by.values():
        rest = 1.0
        for s in c["sources"]:
            rest *= 1 - s["confidence"]
        levels = [s["eqf"] for s in c["sources"] if s["type"] != "STILUS" and s.get("eqf")]
        c |= {"confidence": round(1 - rest, 3), "eqf": max(levels) if levels else None,
              "types": sorted({s["type"] for s in c["sources"]}, key=list(SOURCE_HU).index)}
        out.append(c)
    return sorted(out, key=lambda c: (-c["confidence"], c["label"]))


def identify_person(service, lab: dict, document, filename=None) -> dict:
    catalog = lab["competencies"]
    profiles = lab["profiles"]
    if not catalog:
        raise HTTPException(503, "Üres a kompetenciatörzs.")
    choice = service.ai.choice(document.ai)
    check = make_check(document.text, catalog, set(profiles))
    data = {"text": document.text,
            "competencies": [{"id": c["id"], "label": c["label"], "group": c.get("group_label", "")}
                             for c in catalog.values()],
            "jobs": [{"id": p["id"], "title": p["title"], "title_en": p["title_en"]} for p in profiles.values()]}
    try:
        result = service.ai.ask(choice, INSTRUCTION, data, PersonIdentification, check, max_tokens=MAX_TOKENS, timeout=240)
        check(result)
    except (LLMError, ValueError) as exc:
        # a hibaüzenet a mi ellenőrzésünké vagy a szolgáltatóé (személyes adatot nem tartalmaz)
        log.warning("CV-profil: érvénytelen AI-válasz: %s", str(exc)[:300])
        raise HTTPException(502, "A CV-elemzés AI-válasza két próbálkozás után sem volt érvényes: "
                                 + str(exc).replace("Sémahiba: ", "")[:240] + " Próbáld újra, vagy válassz másik modellt.") from None
    result, cleaned = sanitize(result, document.text)

    experience = [e.model_dump() for e in result.work_experience]
    trainings = [t.model_dump() for t in result.trainings]
    sources = []
    for m in result.matches:
        ref_label = (experience[m.ref]["title"] if m.forras == "MUNKAKOR" else
                     trainings[m.ref]["name"] if m.forras == "KEPZES" else None)
        conf = min(m.confidence, STYLE_MAX_CONFIDENCE) if m.forras == "STILUS" else m.confidence
        eqf = None if m.forras == "STILUS" else m.eqf
        if m.forras == "KEPZES" and eqf is None:
            eqf = trainings[m.ref]["eqf"]
        sources.append({"id": m.id, "type": m.forras, "confidence": round(conf, 3), "eqf": eqf, "evidence": m.evidence,
                        "reason": m.reason, "ref_label": ref_label, "inherited": False})
    for e in experience:
        p = profiles.get(e["job_id"]) if e["job_id"] else None
        e["job_title"] = p["title"] if p else None
        e["job_eqf"] = p["eqf"]["typical"] if p else None
        got = inherit(e, p) if p else []
        e["inherited"] = len(got)
        sources += got
    competencies = merge(sources, catalog)
    for c in competencies:
        k = catalog[c["id"]]
        c |= {"category_label": k.get("category_label", ""), "group_label": k.get("group_label", "")}
    warnings = list(result.warnings) + cleaned
    if not competencies:
        warnings.append("A szöveg alapján nem találtunk kellően alátámasztott kompetenciát.")
    warnings.append("AI-javaslat: az explicit és implicit találatokat a forrásidézetek, az örökölt és képzésből "
                    "származó kompetenciákat a hivatkozott munkakör és képzés alapján ellenőrizd.")
    return {"competencies": competencies, "work_experience": experience, "trainings": trainings,
            "eqf": result.eqf.model_dump() if result.eqf else None,
            "counts": {t: sum(1 for c in competencies if t in c["types"]) for t in SOURCE_HU if t != "KEZI"},
            "warnings": warnings, "ai": choice.model_dump(), "source_filename": filename}
