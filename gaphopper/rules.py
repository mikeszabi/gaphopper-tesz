"""P2 determinisztikus szabályai (R1, R4–R16; az R2–R3-at és az R15-öt a séma ellenőrzi).

A TESZ-nivaló-szintű szabályok (R1 TESZ-nivaló/CORE, R4, R5, R9, R11, R14) a TESZ-nivalókra, a SZÍN-szintűek
(R1 TESZ, R6 ALAP-SZÍN, R7, R8, R10, R12, R13, R16) a változatokra (TESZ-SZÍN-ekre) számolnak.
"""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field

from .config import vocab
from .normalize import norm_key
from .schemas import TeszSzin, TeszNivalo, link_nivalok, nivalok_from_szinek


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


_STOP = {"and", "the", "for", "with", "és", "vagy", "egyéb", "general", "other", "szakember", "munkatárs",
         "specialist", "worker", "assistant", "including"}


def _job_words(*texts: str) -> list[str]:
    """R17: a munkakör címéből és szakterületéből képzett szótövek (legalább 5 betűs szavak)."""
    out = []
    for t in texts:
        for w in re.findall(r"\w+", (t or "").lower()):
            if len(w) >= 5 and w not in _STOP and not w.isdigit():
                out.append(w[:max(5, len(w) - 2)])
    return sorted(set(out))


def stems(text: str, n: int = 5) -> set[str]:
    """Szótövek durván: a legalább 4 betűs szavak első n betűje (magyar ragozás és összetétel ellen)."""
    return {w[:n] for w in re.findall(r"\w+", (text or "").lower()) if len(w) >= 4 and w not in _STOP}


# a szótő-egyezésnél csak a kötőszavak esnek ki; a _STOP a munkakörcímekre szól ("munkatárs", "szakember" ott
# általános szó, de SZER-ELEM-névként – "Munkatárs" – érdemi; 2026-11-05: emiatt jelzett tévesen az R21)
_KOTO = {"and", "the", "for", "with", "és", "vagy", "egyéb", "including", "other"}
_FOLD = str.maketrans({"á": "a", "é": "e"})  # tővégi magánhangzó-nyúlás: ábra ~ ábrák, kese ~ kesét


def stem_overlap(a: str, b: str) -> bool:
    """Van-e közös szótő, vagy az egyik szó a másik elején (összetétel: programkód ~ program)."""
    wa = [w.translate(_FOLD) for w in re.findall(r"\w+", (a or "").lower()) if len(w) >= 3 and w not in _KOTO]
    wb = [w.translate(_FOLD) for w in re.findall(r"\w+", (b or "").lower()) if len(w) >= 3 and w not in _KOTO]
    return any(x[:5] == y[:5] or x.startswith(y[:6]) or y.startswith(x[:6]) for x in wa for y in wb)


def szer_in_label(m: TeszNivalo) -> bool:
    return stem_overlap(m.primary_szer.label_hu, m.label_hu)


def g4_pairs(magok: list[TeszNivalo], overlap: float) -> list[tuple[str, str]]:
    """G4: azonos TESZ-típus és legalább `overlap` arányú KÉSZ-ség-átfedés (a kisebbik halmazhoz mérve)."""
    out = []
    for i, a in enumerate(magok):
        for b in magok[i + 1:]:
            if a.tesz_type != b.tesz_type or not a.kesz_ids or not b.kesz_ids:
                continue
            common = len(set(a.kesz_ids) & set(b.kesz_ids))
            if common / min(len(a.kesz_ids), len(b.kesz_ids)) >= overlap:
                out.append((a.nivalo_id, b.nivalo_id))
    return out


def check(tesz: list[TeszSzin], kor_ids: list[str] | None = None, job_field: str = "",
          magok: list[TeszNivalo] | None = None, job_title: str = "", torzs_size: int = 0) -> list[Flag]:
    r = vocab()["rules"]
    pairs = vocab()["termeszetes_par"]
    if magok is None:
        magok = nivalok_from_szinek(tesz)
    if any(t.nivalo_id is None for t in tesz):
        magok, tesz, _ = link_nivalok(list(magok), list(tesz), strict=False)
    flags: list[Flag] = []
    n, nm = len(tesz), len(magok)
    core = sum(1 for m in magok if m.relevance == "CORE")
    by_mag: dict[str, list[TeszSzin]] = defaultdict(list)
    for t in tesz:
        by_mag[t.nivalo_id].append(t)

    # R1 – darabszám: TESZ-nivaló, TESZ-SZÍN, CORE TESZ-nivaló
    if not r["mag_min"] <= nm <= r["mag_max"]:
        flags.append(Flag("R1", f"{nm} TESZ-nivaló (elvárt {r['mag_min']}–{r['mag_max']})."))
    if not r["tesz_min"] <= n <= r["tesz_max"]:
        flags.append(Flag("R1", f"{n} TESZ-SZÍN (elvárt {r['tesz_min']}–{r['tesz_max']})."))
    if not r["core_min"] <= core <= r["core_max"]:
        flags.append(Flag("R1", f"{core} CORE TESZ-nivaló (elvárt {r['core_min']}–{r['core_max']})."))

    # R4 – címke hossza (TESZ-nivaló) és tiltott minták (TESZ-nivaló és változat)
    bad_len, bad_pat = [], []
    wmin = r.get("mag_label_words_min", r["label_words_min"])
    wmax = r.get("mag_label_words_max", r["label_words_max"])
    for m in magok:
        words = len(m.label_hu.split())
        if not wmin <= words <= wmax:
            bad_len.append(m.nivalo_id)
    for x in list(magok) + [t for t in tesz if not t.alap_szin]:
        text = f"{x.label_hu} {x.label_en}".lower()
        if any(p in text for p in r["forbidden_label_patterns"]):
            bad_pat.append(getattr(x, "cand_id", None) or x.nivalo_id)
    if bad_len:
        flags.append(Flag("R4", f"A TESZ-nivaló-címke hossza nem {wmin}–{wmax} szó (G1).", bad_len))
    if bad_pat:
        flags.append(Flag("R4", "Tiltott címkeminta (általános minta vagy álcázott készség).", bad_pat))

    if nm:
        # R5 – EXECUTE aránya a TESZ-nivalók között
        ex = [m.nivalo_id for m in magok if m.tesz_type == "EXECUTE"]
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
    def quad(t: TeszSzin):
        return (t.tesz_type, _szer_key(t), t.kozeg, t.helyzet)
    q = Counter(quad(t) for t in tesz)
    dup = [t.cand_id for t in tesz if q[quad(t)] > 1]
    if dup:
        flags.append(Flag("R8", "Azonos ⟨típus, meghatározó SZER-ELEM, KÖZ-eg, HELY-zet⟩ négyes – összevonás?", dup))

    # R9 – feladatkörönként legalább N TESZ-nivaló
    if kor_ids:
        per = Counter(m.kor_id for m in magok)
        thin = [k for k in kor_ids if per.get(k, 0) < r["mag_per_kor_min"]]
        if thin:
            flags.append(Flag("R9", f"Kevesebb mint {r['mag_per_kor_min']} TESZ-nivaló ezekben a feladatkörökben: "
                                    f"{', '.join(thin)}."))

    # R10 – alacsony bizonyosság
    low = [t.cand_id for t in tesz if t.confidence < r["confidence_min"]]
    if low:
        flags.append(Flag("R10", "Alacsony bizonyosság.", low, severity="expert"))

    # R11 – két külön TESZ-nivaló azonos ⟨típus, meghatározó SZER-ELEM⟩ párral
    pair = Counter((m.tesz_type, _szer_key(m)) for m in magok)
    twins = [m.nivalo_id for m in magok if pair[(m.tesz_type, _szer_key(m))] > 1]
    if twins:
        flags.append(Flag("R11", "Két TESZ-nivaló azonos ⟨TESZ-típus, meghatározó SZER-ELEM⟩ párral – egy TESZ-nivaló két SZÍN-e "
                                 "(MOVE)?", twins))

    # R12 – túl sok változat egy TESZ-nivalón
    many = [mid for mid, ts in by_mag.items() if len(ts) > r["variants_per_mag_max"]]
    if many:
        flags.append(Flag("R12", f"Egy TESZ-nivalón legfeljebb {r['variants_per_mag_max']} változat lehet.", many))

    # R13 – nem ALAP-SZÍN változat SZÍN-különbség nélkül (M2)
    thin_diff = [t.cand_id for t in tesz if not t.alap_szin
                 and len((t.szin_kulonbseg_hu or "").split()) < r["szin_kulonbseg_words_min"]]
    if thin_diff:
        flags.append(Flag("R13", "A változat nem írja le, mitől más vagy nehezebb az ALAP-SZÍN-nél (M2); "
                                 "FOLD vagy szin_kulonbseg_hu.", thin_diff))

    # R14 – SZÍN-jelző a TESZ-nivaló címkéjében
    jel = [m.nivalo_id for m in magok if _jelzo(f"{m.label_hu} {m.label_en}", r["szin_jelzok"])]
    if jel:
        flags.append(Flag("R14", "A TESZ-nivaló-címke SZÍN-jelzőt tartalmaz (a TESZ-nivaló KÖZ-eg- és HELY-zet-semleges).", jel))

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

    # R17 – a munkakör SAJÁT címének vagy szakterületének szava a TESZ-nivaló magyar címkéjében vagy meghatározó
    # SZER-ELEM-ében (G1). Rögzített szólista nincs (felhasználói döntés, 2026-11-05): a túl szűk nevet a
    # munkakörök közti bizonyíték alapján a `generalize` törzskarbantartó javítja. Az angol címkét nem nézi: az
    # O*NET-címek szavai gyakori igék és főnevek ("developer" ~ "develop").
    cim = _job_words(job_title, job_field)
    spec = [m.nivalo_id for m in magok if not m.nivalo_ref
            and _jelzo(f"{m.label_hu} {m.primary_szer.label_hu}", cim)]
    if spec:
        flags.append(Flag("R17", "A munkakör saját címének vagy szakterületének szava a TESZ-nivaló nevében vagy "
                                 "meghatározó SZER-ELEM-ében: a specifikum a kontextus_hu-ba és a kísérő SZER-ELEM-be "
                                 "kerül (G1).", spec))

    # R21 – a név tartalmazza a meghatározó SZER-ELEM nevét vagy tövét (G5: felismerhetőség)
    nofit = [m.nivalo_id for m in magok if not m.nivalo_ref and not szer_in_label(m)]
    if nofit:
        flags.append(Flag("R21", "A TESZ-nivaló neve nem tartalmazza a meghatározó SZER-ELEM nevét vagy tövét: túl "
                                 "elvont vagy más dologról szól (G5).", nofit))

    # R18 – G4-összevonási jelölt: azonos típus és nagy KÉSZ-ség-átfedés
    pairs = g4_pairs(magok, float(r.get("g4_kesz_overlap", 0.66)))
    if pairs:
        flags.append(Flag("R18", "Azonos TESZ-típus és legalább 2/3 KÉSZ-ség-átfedés: egy TESZ-nivaló (G4)? "
                                 + ", ".join(f"{a}+{b}" for a, b in pairs), sorted({x for p in pairs for x in p})))

    # R19 – nagy TESZ-törzs mellett túl sok új TESZ-nivaló
    if torzs_size >= r.get("r19_torzs_min", 50) and nm:
        new = [m.nivalo_id for m in magok if not m.nivalo_ref]
        if len(new) / nm > r.get("r19_new_share_max", 0.5):
            flags.append(Flag("R19", f"{len(new)}/{nm} új TESZ-nivaló {torzs_size} elemű TESZ-törzs mellett: válassz a "
                                     "törzsből (nivalo_ref), ha illik.", new))

    # R20 – kevés KÉSZ-ség-törzselem (G2)
    few = [m.nivalo_id for m in magok if len(m.kesz_ids) < r.get("kesz_min", 2)]
    if few:
        flags.append(Flag("R20", f"{r.get('kesz_min', 2)}-nél kevesebb kesz_ids (G2: 2–5 illeszkedő KÉSZ-ség).", few))
    return flags


def kesz_hiany_guard(label_hu: str, label_en: str, job_title: str = "", job_field: str = "",
                     job_title_hu: str = "") -> str:
    """R23 – a hiányjelölt törzsszintű-e. Üres szöveg: törzsjelölt; különben az ok, amiért TUD-ás-/SZER-ELEM-jelölt.

    Nem törzsszintű: túl hosszú név, szám, nagybetűs termék/rövidítés, a munkakör címéből vagy szakterületéből vett szó
    (az R17 logikájával), szintszó (alap, haladó ...)."""
    r = vocab()["rules"]
    words = re.findall(r"\w+", label_hu or "")
    if len(words) > int(r.get("kesz_hiany_szo_max", 4)):
        return f"túl hosszú név ({len(words)} szó)"
    if re.search(r"\d", f"{label_hu} {label_en}"):
        return "számot tartalmaz (szabvány, verzió vagy termék)"
    for w in re.findall(r"\w+", f"{label_hu} {label_en}"):
        if len(w) >= 2 and (w.isupper() or re.search(r"[a-záéíóöőúüű][A-ZÁÉÍÓÖŐÚÜŰ]", w)):
            return f"termék- vagy rövidítésnév: {w}"
    spec = _jelzo(label_hu, _job_words(job_title, job_field))
    if spec:
        return f"a munkakör címének vagy szakterületének szava: {', '.join(spec)}"
    # a munkakör magyar neve (a P1 job_label_hu-ja): szótő-egyezés, mert a jelölt neve magyar ("Állatorvos-asszisztens"
    # ~ "Állatkezelés"); az angol cím szavai a magyar névben ritkán fordulnak elő
    if job_title_hu and stem_overlap(job_title_hu, label_hu):
        return f"a munkakör magyar nevének töve: {job_title_hu}"
    szint = _jelzo(f"{label_hu} {label_en}", list(r.get("kesz_szintszavak") or []))
    if szint:
        return f"szintváltozat: {', '.join(szint)}"
    return ""


def needs_expert(flags: list[Flag]) -> bool:
    return any(f.severity == "expert" for f in flags)


def soft(flags: list[Flag]) -> list[Flag]:
    return [f for f in flags if f.severity == "soft"]
