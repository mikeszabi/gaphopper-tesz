"""P2 determinisztikus szabályai (R1, R4–R10; az R2–R3-at a séma ellenőrzi)."""
from __future__ import annotations

from collections import Counter
from dataclasses import asdict, dataclass, field

from .config import vocab
from .schemas import Tesz


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


def check(tesz: list[Tesz], kor_ids: list[str] | None = None, job_field: str = "") -> list[Flag]:
    r = vocab()["rules"]
    pairs = vocab()["termeszetes_par"]
    flags: list[Flag] = []
    n = len(tesz)
    core = sum(1 for t in tesz if t.relevance == "CORE")

    # R1 – darabszám
    if not r["tesz_min"] <= n <= r["tesz_max"]:
        flags.append(Flag("R1", f"{n} TESZ-nivaló (elvárt {r['tesz_min']}–{r['tesz_max']})."))
    if not r["core_min"] <= core <= r["core_max"]:
        flags.append(Flag("R1", f"{core} CORE TESZ (elvárt {r['core_min']}–{r['core_max']})."))

    # R4 – címke hossza és tiltott minták
    bad_len, bad_pat = [], []
    for t in tesz:
        words = len(t.label_hu.split())
        if not r["label_words_min"] <= words <= r["label_words_max"]:
            bad_len.append(t.cand_id)
        text = f"{t.label_hu} {t.label_en}".lower()
        if any(p in text for p in r["forbidden_label_patterns"]):
            bad_pat.append(t.cand_id)
    if bad_len:
        flags.append(Flag("R4", f"A címke hossza nem {r['label_words_min']}–{r['label_words_max']} szó.", bad_len))
    if bad_pat:
        flags.append(Flag("R4", "Tiltott címkeminta (általános minta vagy álcázott készség).", bad_pat))

    if n:
        # R5 – EXECUTE aránya
        ex = [t.cand_id for t in tesz if t.tesz_type == "EXECUTE"]
        if len(ex) / n > r["execute_max_share"]:
            flags.append(Flag("R5", f"EXECUTE aránya {len(ex)/n:.0%} (max {r['execute_max_share']:.0%}).", ex))
        # R6 – HELY-zet a TESZ-típus természetes párja
        nat = [t.cand_id for t in tesz if pairs.get(t.tesz_type) == t.helyzet]
        if len(nat) / n > r["natural_pair_max_share"]:
            flags.append(Flag("R6", f"A HELY-zetek {len(nat)/n:.0%}-a a TESZ-típus természetes párja.", nat))
        # R7 – Kritikus HELY-zet aránya
        kr = [t.cand_id for t in tesz if t.helyzet == "HELY_KRITIKUS"]
        limit = r["kritikus_max_share_high_risk"] if _high_risk(job_field) else r["kritikus_max_share"]
        if len(kr) / n > limit:
            flags.append(Flag("R7", f"Kritikus HELY-zet aránya {len(kr)/n:.0%} (max {limit:.0%}).", kr))

    # R8 – azonos négyes
    quad = Counter((t.tesz_type, t.primary_szer.label_hu.strip().lower(), t.kozeg, t.helyzet) for t in tesz)
    dup = [t.cand_id for t in tesz
           if quad[(t.tesz_type, t.primary_szer.label_hu.strip().lower(), t.kozeg, t.helyzet)] > 1]
    if dup:
        flags.append(Flag("R8", "Azonos ⟨típus, meghatározó SZER-ELEM, KÖZ-eg, HELY-zet⟩ négyes – összevonás?", dup))

    # R9 – feladatkörönként legalább 2 TESZ
    if kor_ids:
        per = Counter(t.kor_id for t in tesz)
        thin = [k for k in kor_ids if per.get(k, 0) < 2]
        if thin:
            flags.append(Flag("R9", f"Kevesebb mint 2 TESZ ezekben a feladatkörökben: {', '.join(thin)}."))

    # R10 – alacsony bizonyosság
    low = [t.cand_id for t in tesz if t.confidence < r["confidence_min"]]
    if low:
        flags.append(Flag("R10", "Alacsony bizonyosság.", low, severity="expert"))
    return flags


def needs_expert(flags: list[Flag]) -> bool:
    return any(f.severity == "expert" for f in flags)


def soft(flags: list[Flag]) -> list[Flag]:
    return [f for f in flags if f.severity == "soft"]
