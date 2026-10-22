"""P2 determinisztikus szabályai (R1, R4–R16; az R2–R3-at és az R15-öt a séma ellenőrzi).

A MAG-szintű szabályok (R1 MAG/CORE, R4, R5, R9, R11, R14) a TESZ-MAG-okra, a SZÍN-szintűek
(R1 TESZ, R6 ALAP-SZÍN, R7, R8, R10, R12, R13, R16) a változatokra (TESZ-nivalókra) számolnak.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from .config import vocab
from .normalize import norm_key
from .schemas import Tesz, TeszMag, link_magok, magok_from_tesz


@dataclass
class Flag:
    rule: str
    message: str
    cand_ids: list[str] = field(default_factory=list)
    severity: str = "soft"  # soft: LLM-bírálóhoz; expert: szakértőhöz

    def as_dict(self) -> dict:
        return asdict(self)


def _high_risk(field_text: str) -> bool:
    words = vocab()["rules"]["high_risk_fields"]
    f = (field_text or "").lower()
    return any(w in f for w in words)


def _szer_key(m) -> str:
    return norm_key(m.primary_szer.label_hu, "hu") if m.primary_szer else ""


def _jelzo(label: str, words: list[str]) -> list[str]:
    """SZÍN-jelzők a címkében: szókezdetre illesztve (sürgős -> sürgősségi is)."""
    low = label.lower()
    return [w for w in words if re.search(r"(?<!\w)" + re.escape(w.lower()), low)]


def check(tesz: list[Tesz], kor_ids: list[str] | None = None, job_field: str = "",
          magok: list[TeszMag] | None = None) -> list[Flag]:
    r = vocab()["rules"]
    pairs = vocab()["termeszetes_par"]
    if magok is None:
        magok = magok_from_tesz(tesz)
    if any(t.mag_id is None for t in tesz):
        magok, tesz, _ = link_magok(list(magok), list(tesz), strict=False)
    flags: list[Flag] = []
    n, nm = len(tesz), len(magok)
    core = sum(1 for m in magok if m.relevance == "CORE")
    by_mag: dict[str, list[Tesz]] = defaultdict(list)
    for t in tesz:
        by_mag[t.mag_id].append(t)

    # R1 – darabszám: MAG, TESZ-nivaló, CORE MAG
    if not r["mag_min"] <= nm <= r["mag_max"]:
        flags.append(Flag("R1", f"{nm} TESZ-MAG (elvárt {r['mag_min']}–{r['mag_max']})."))
    if not r["tesz_min"] <= n <= r["tesz_max"]:
        flags.append(Flag("R1", f"{n} TESZ-nivaló (elvárt {r['tesz_min']}–{r['tesz_max']})."))
    if not r["core_min"] <= core <= r["core_max"]:
        flags.append(Flag("R1", f"{core} CORE MAG (elvárt {r['core_min']}–{r['core_max']})."))

    # R4 – címke hossza (MAG) és tiltott minták (MAG és változat)
    bad_len, bad_pat = [], []
    wmin = r.get("mag_label_words_min", r["label_words_min"])
    for m in magok:
        words = len(m.label_hu.split())
        if not wmin <= words <= r["label_words_max"]:
            bad_len.append(m.mag_id)
    for x in list(magok) + [t for t in tesz if not t.alap_szin]:
        text = f"{x.label_hu} {x.label_en}".lower()
        if any(p in text for p in r["forbidden_label_patterns"]):
            bad_pat.append(getattr(x, "cand_id", None) or x.mag_id)
    if bad_len:
        flags.append(Flag("R4", f"A MAG-címke hossza nem {wmin}–{r['label_words_max']} szó.", bad_len))
    if bad_pat:
        flags.append(Flag("R4", "Tiltott címkeminta (általános minta vagy álcázott készség).", bad_pat))

    if nm:
        # R5 – EXECUTE aránya a MAG-ok között
        ex = [m.mag_id for m in magok if m.tesz_type == "EXECUTE"]
        if len(ex) / nm > r["execute_max_share"]:
            flags.append(Flag("R5", f"EXECUTE aránya {len(ex)/nm:.0%} (max {r['execute_max_share']:.0%}).", ex))
        # R6 – az ALAP-SZÍN HELY-zete a TESZ-típus természetes párja
        alap = [t for t in tesz if t.alap_szin]
        nat = [t.cand_id for t in alap if pairs.get(t.tesz_type) == t.helyzet]
        if alap and len(nat) / len(alap) > r["natural_pair_max_share"]:
            flags.append(Flag("R6", f"Az ALAP-SZÍN-ek {len(nat)/len(alap):.0%}-ának HELY-zete a TESZ-típus "
                                    "természetes párja.", nat))
    if n:
        # R7 – Kritikus HELY-zet aránya a változatok között
        kr = [t.cand_id for t in tesz if t.helyzet == "HELY_KRITIKUS"]
        limit = r["kritikus_max_share_high_risk"] if _high_risk(job_field) else r["kritikus_max_share"]
        if len(kr) / n > limit:
            flags.append(Flag("R7", f"Kritikus HELY-zet aránya {len(kr)/n:.0%} (max {limit:.0%}).", kr))

    # R8 – azonos ⟨típus, meghatározó SZER-ELEM, KÖZ-eg, HELY-zet⟩ négyes
    def quad(t: Tesz):
        return (t.tesz_type, _szer_key(t), t.kozeg, t.helyzet)
    q = Counter(quad(t) for t in tesz)
    dup = [t.cand_id for t in tesz if q[quad(t)] > 1]
    if dup:
        flags.append(Flag("R8", "Azonos ⟨típus, meghatározó SZER-ELEM, KÖZ-eg, HELY-zet⟩ négyes – összevonás?", dup))

    # R9 – feladatkörönként legalább N MAG
    if kor_ids:
        per = Counter(m.kor_id for m in magok)
        thin = [k for k in kor_ids if per.get(k, 0) < r["mag_per_kor_min"]]
        if thin:
            flags.append(Flag("R9", f"Kevesebb mint {r['mag_per_kor_min']} MAG ezekben a feladatkörökben: "
                                    f"{', '.join(thin)}."))

    # R10 – alacsony bizonyosság
    low = [t.cand_id for t in tesz if t.confidence < r["confidence_min"]]
    if low:
        flags.append(Flag("R10", "Alacsony bizonyosság.", low, severity="expert"))

    # R11 – két külön MAG azonos ⟨típus, meghatározó SZER-ELEM⟩ párral
    pair = Counter((m.tesz_type, _szer_key(m)) for m in magok)
    twins = [m.mag_id for m in magok if pair[(m.tesz_type, _szer_key(m))] > 1]
    if twins:
        flags.append(Flag("R11", "Két MAG azonos ⟨TESZ-típus, meghatározó SZER-ELEM⟩ párral – egy MAG két SZÍN-e "
                                 "(REMAG)?", twins))

    # R12 – túl sok változat egy MAG-on
    many = [mid for mid, ts in by_mag.items() if len(ts) > r["variants_per_mag_max"]]
    if many:
        flags.append(Flag("R12", f"Egy MAG-on legfeljebb {r['variants_per_mag_max']} változat lehet.", many))

    # R13 – nem ALAP-SZÍN változat SZÍN-különbség nélkül (M2)
    thin_diff = [t.cand_id for t in tesz if not t.alap_szin
                 and len((t.szin_kulonbseg_hu or "").split()) < r["szin_kulonbseg_words_min"]]
    if thin_diff:
        flags.append(Flag("R13", "A változat nem írja le, mitől más vagy nehezebb az ALAP-SZÍN-nél (M2); "
                                 "FOLD vagy szin_kulonbseg_hu.", thin_diff))

    # R14 – SZÍN-jelző a MAG címkéjében
    jel = [m.mag_id for m in magok if _jelzo(f"{m.label_hu} {m.label_en}", r["szin_jelzok"])]
    if jel:
        flags.append(Flag("R14", "A MAG-címke SZÍN-jelzőt tartalmaz (a MAG KÖZ-eg- és HELY-zet-semleges).", jel))

    # R16 – csak KÖZ-egben eltérő változat, azonos HELY-zettel és kísérő SZER-ELEM-ekkel
    kis = []
    for ts in by_mag.values():
        base = next((t for t in ts if t.alap_szin), None)
        if base is None:
            continue
        bsec = {norm_key(s.label_hu, "hu") for s in base.secondary_szer}
        for t in ts:
            if t is not base and t.helyzet == base.helyzet and t.kozeg != base.kozeg \
                    and {norm_key(s.label_hu, "hu") for s in t.secondary_szer} == bsec:
                kis.append(t.cand_id)
    if kis:
        flags.append(Flag("R16", "Csak KÖZ-egben tér el az ALAP-SZÍN-től, a kísérő SZER-ELEM-ek azonosak: "
                                 "valószínű kísérő KÖZ-eg (FOLD).", kis))
    return flags


def needs_expert(flags: list[Flag]) -> bool:
    return any(f.severity == "expert" for f in flags)


def soft(flags: list[Flag]) -> list[Flag]:
    return [f for f in flags if f.severity == "soft"]
