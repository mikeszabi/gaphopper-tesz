"""A promptok kimeneteinek sémái (pydantic v2). A zárt listák a config/vocab.yaml-ból jönnek."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .config import vocab

_V = vocab()
TESZ_TIPUS = set(_V["tesz_tipus"])
KOZEG = set(_V["kozeg"])
HELYZET = set(_V["helyzet"])
SZER_FAJTA = set(_V["szer_elem_fajta"])
SZER_SZEREP = set(_V["szer_elem_szerep"])
FREQUENCY = set(_V["frequency"])
ALIAS_ERTEK = _V.get("alias_ertek") or {}
# A TESZ-nivalóról öröklődő mezők: a változat (TESZ-SZÍN) nem írja újra őket, a TESZ-nivaló a mérvadó.
NIVALO_FIELDS = ("kor_id", "outcome_hu", "tesz_type", "tesz_type_runner_up", "primary_szer", "relevance", "kontextus_hu")
# A TESZ-nivaló kilétét adó mezők: ha a bíráló ezeket javítja, a TESZ-nivaló már nem a törzs eleme (nivalo_ref elvész).
NIVALO_IDENTITY = ("label_hu", "tesz_type", "primary_szer")


def kesz_id(v) -> str:
    """KÉSZ-ség-törzs azonosító: 12 | "12" | "gh_c_12" -> "gh_c_12"."""
    t = str(v).strip()
    return t if t.startswith("gh_c_") else f"gh_c_{int(t)}"


def _alias(kind: str, value):
    """Zárt lista: a vocab.yaml alias_ertek táblája szerinti determinisztikus megfeleltetés."""
    if isinstance(value, str):
        return (ALIAS_ERTEK.get(kind) or {}).get(value.strip().upper(), value)
    return value


def _check(value, allowed: set, name: str):
    if value is None:
        return value
    if value not in allowed:
        raise ValueError(f"{name}: '{value}' nincs a zárt listában; megengedett: {sorted(allowed)}")
    return value


class _Base(BaseModel):
    model_config = ConfigDict(extra="ignore")


class Alias(_Base):
    text: str
    lang: Literal["hu", "en"]


class SzerElem(_Base):
    label_hu: str
    label_en: str = ""
    kind: str
    role: str
    definition_hu: str = ""
    kozeg: Optional[str] = None
    helyzet: Optional[str] = None
    # kísérő SZER-ELEM-nél: a TESZ-nivaló meghatározó SZER-ELEM-ének szűkebb fajtája (FAJTAJA él)
    fajtaja: bool = False

    @field_validator("kind")
    @classmethod
    def _kind(cls, v):
        return _check(v, SZER_FAJTA, "kind")

    @field_validator("role")
    @classmethod
    def _role(cls, v):
        return _check(v, SZER_SZEREP, "role")

    @field_validator("kozeg")
    @classmethod
    def _kozeg(cls, v):
        return _check(v, KOZEG, "kozeg")

    @field_validator("helyzet")
    @classmethod
    def _helyzet(cls, v):
        return _check(v, HELYZET, "helyzet")


class Melyites(_Base):
    """Mélyítési jelölt: a szakmában ismert nehezebb SZÍN, amely ebben a munkakörben nem jellemző."""
    kozeg: str
    helyzet: str
    indok_hu: str = ""

    @field_validator("kozeg")
    @classmethod
    def _k(cls, v):
        return _check(v, KOZEG, "kozeg")

    @field_validator("helyzet")
    @classmethod
    def _h(cls, v):
        return _check(v, HELYZET, "helyzet")


class TeszNivalo(_Base):
    """TESZ-nivaló: TESZ-típus + meghatározó SZER-ELEM + ERED-mény fajtája; KÖZ-eg- és HELY-zet-semleges."""
    nivalo_id: str
    kor_id: Optional[str] = None
    label_hu: str
    label_en: str = ""
    outcome_hu: str
    tesz_type: str
    tesz_type_runner_up: Optional[str] = None
    primary_szer: SzerElem
    relevance: Literal["CORE", "SUPPORTING"] = "SUPPORTING"
    melyites_jeloltek: list[Melyites] = Field(default_factory=list, max_length=1)
    # G2: 2–5 illeszkedő KÉSZ-ség-törzselem (gh_c_<n>), a legjellemzőbb elöl; a P1 számmal is írhatja
    kesz_ids: list[str] = Field(default_factory=list, max_length=5)
    # a TESZ-törzs eleme, ha a P1 onnan választott (ilyenkor a TESZ-nivaló mezői a törzsből jönnek)
    nivalo_ref: Optional[str] = None
    # munkakör-függő szakmai kontextus (szakterület, célcsoport, tantárgy): a MunkaKor–TESZ élre kerül
    kontextus_hu: Optional[str] = None
    # törzs-választásnál a P1 saját meghatározó SZER-ELEM-fajtája (a P3-őr veti össze a törzselemével)
    ref_szer: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            if "kesz" in data and "kesz_ids" not in data:
                data["kesz_ids"] = data.pop("kesz")
            if "szer" in data and "ref_szer" not in data and isinstance(data["szer"], str):
                data["ref_szer"] = data.pop("szer")
            for old, new in (("mag_id", "nivalo_id"), ("mag_ref", "nivalo_ref")):
                if old in data and new not in data:
                    data[new] = data.pop(old)
            ref = data.get("nivalo_ref")
            if isinstance(ref, str) and ref.startswith("gh_mag_"):
                # a 2026-11-04 előtti azonosító-előtag: a hash azonos, csak az előtag változott
                data["nivalo_ref"] = "gh_niv_" + ref[len("gh_mag_"):]
            mj = data.get("melyites_jeloltek")
            if isinstance(mj, (str, dict)) or (isinstance(mj, list) and any(not isinstance(x, dict) for x in mj)):
                # a bíráló néha szövegként írja: a mélyítési jelölt nem kötelező, a szöveges tétel kimarad
                lst = mj if isinstance(mj, list) else [mj]
                data["melyites_jeloltek"] = [x for x in lst if isinstance(x, dict)]
            if "runner_up" in data and "tesz_type_runner_up" not in data:
                data["tesz_type_runner_up"] = data.pop("runner_up")
            if "relevance" in data:
                data["relevance"] = _alias("relevance", data["relevance"])
        return data

    @field_validator("tesz_type", "tesz_type_runner_up")
    @classmethod
    def _tt(cls, v):
        return _check(v, TESZ_TIPUS, "tesz_type")

    @field_validator("kesz_ids", mode="before")
    @classmethod
    def _kesz(cls, v):
        from .config import kesz_torzs

        torzs = kesz_torzs()
        out = []
        for x in v or []:
            try:
                k = kesz_id(x)
            except ValueError:
                raise ValueError(f"kesz_ids: '{x}' nem KÉSZ-ség-törzs azonosító (szám vagy gh_c_<n>).")
            if torzs and k not in torzs:
                raise ValueError(f"kesz_ids: '{k}' nincs a KÉSZ-ség-törzsben (1–{len(torzs)}).")
            if k not in out:
                out.append(k)
        return out

    @model_validator(mode="after")
    def _melyites_core(self):
        if self.melyites_jeloltek and self.relevance != "CORE":
            raise ValueError(f"{self.nivalo_id}: mélyítési jelölt csak CORE TESZ-nivalón lehet.")
        return self

    @property
    def runner_up(self) -> Optional[str]:
        return self.tesz_type_runner_up


class TeszSzin(_Base):
    """TESZ-SZÍN = egy TESZ-nivaló SZÍN-ezett változata (TESZ-nivaló × ⟨KÖZ-eg, HELY-zet⟩).

    A TESZ-nivaló mezői (NIVALO_FIELDS) a P1Output-ban a TESZ-nivalóról töltődnek ki; régi (TESZ-nivaló nélküli) eredményeknél
    a változat saját mezői maradnak.
    """
    cand_id: str
    nivalo_id: Optional[str] = None
    alap_szin: bool = True
    szin_kulonbseg_hu: Optional[str] = None
    kor_id: Optional[str] = None
    label_hu: str = ""
    label_en: str = ""
    outcome_hu: str = ""
    # Az igazolási mondatot és a rubrikát egyesítés után, kanonikus elemenként egyszer írjuk (P5);
    # a P1 már nem adja ki őket. Régi eredményeknél még szerepelhetnek.
    attestation_hu: str = ""
    rubric_hu: list[str] = Field(default_factory=list)
    tesz_type: Optional[str] = None
    tesz_type_runner_up: Optional[str] = None
    primary_szer: Optional[SzerElem] = None
    secondary_szer: list[SzerElem] = Field(default_factory=list, max_length=4)
    kozeg: str
    kozeg_secondary: list[str] = Field(default_factory=list, max_length=2)
    helyzet: str
    helyzet_secondary: Optional[str] = None
    relevance: Literal["CORE", "SUPPORTING"] = "SUPPORTING"
    kontextus_hu: Optional[str] = None
    frequency: Optional[str] = None
    expected_eqf: int = Field(ge=1, le=8)
    # egyetlen rövid mondat; régi eredményeknél szótár (type/szer/kozeg/helyzet)
    rationale_hu: Optional[Union[str, dict[str, str]]] = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    open_question: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _aliases(cls, data):
        if isinstance(data, dict):
            data = dict(data)
            for k in ("frequency", "relevance"):
                if k in data:
                    data[k] = _alias(k, data[k])
            if "mag_id" in data and "nivalo_id" not in data:
                data["nivalo_id"] = data.pop("mag_id")
            if data.get("alap_szin") is None:
                data.pop("alap_szin", None)
        return data

    @field_validator("tesz_type", "tesz_type_runner_up")
    @classmethod
    def _tt(cls, v):
        return _check(v, TESZ_TIPUS, "tesz_type")

    @field_validator("kozeg")
    @classmethod
    def _k(cls, v):
        return _check(v, KOZEG, "kozeg")

    @field_validator("kozeg_secondary")
    @classmethod
    def _ks(cls, v):
        for x in v:
            _check(x, KOZEG, "kozeg_secondary")
        return v

    @field_validator("helyzet", "helyzet_secondary")
    @classmethod
    def _h(cls, v):
        return _check(v, HELYZET, "helyzet")

    @field_validator("frequency")
    @classmethod
    def _f(cls, v):
        return _check(v, FREQUENCY, "frequency")

    @model_validator(mode="after")
    def _complete(self):
        # TESZ-nivaló nélkül (régi eredmény) a változat maga hordozza a TESZ-nivaló-mezőket.
        if self.nivalo_id is None and (not self.tesz_type or self.primary_szer is None or not self.label_hu):
            raise ValueError(f"{self.cand_id}: nivalo_id nélkül a label_hu, tesz_type és primary_szer kötelező.")
        return self


def inherit(mag: TeszNivalo, t: TeszSzin) -> TeszSzin:
    """A TESZ-nivaló mezőinek rámásolása a változatra (a TESZ-nivaló a mérvadó).

    G3 SZÍN-semlegesség: a változat címkéje mindig a TESZ-nivalóé; a SZÍN-t a ⟨KÖZ-eg, HELY-zet⟩ és a
    szin_kulonbseg_hu hordozza."""
    upd = {k: getattr(mag, k) for k in NIVALO_FIELDS}
    upd["label_hu"], upd["label_en"] = mag.label_hu, mag.label_en
    return t.model_copy(update=upd)


def legacy_nivalo(t: TeszSzin) -> TeszNivalo:
    """Régi (TESZ-nivaló nélküli) TESZ-hez egyelemű TESZ-nivaló: a TESZ a saját TESZ-nivalójának ALAP-SZÍN-e."""
    return TeszNivalo(nivalo_id=f"N_{t.cand_id}", kor_id=t.kor_id, label_hu=t.label_hu, label_en=t.label_en,
                   outcome_hu=t.outcome_hu or t.label_hu, tesz_type=t.tesz_type,
                   tesz_type_runner_up=t.tesz_type_runner_up, primary_szer=t.primary_szer,
                   relevance=t.relevance)


def nivalok_from_szinek(tesz: list[TeszSzin]) -> list[TeszNivalo]:
    """A változatok örökölt mezőiből visszaállított TESZ-nivaló-lista (amikor csak a változatok vannak meg)."""
    out: dict[str, TeszNivalo] = {}
    for t in tesz:
        if t.nivalo_id and t.nivalo_id not in out and t.tesz_type and t.primary_szer:
            out[t.nivalo_id] = TeszNivalo(nivalo_id=t.nivalo_id, kor_id=t.kor_id, label_hu=t.label_hu, label_en=t.label_en,
                                    outcome_hu=t.outcome_hu or t.label_hu, tesz_type=t.tesz_type,
                                    tesz_type_runner_up=t.tesz_type_runner_up, primary_szer=t.primary_szer,
                                    relevance=t.relevance)
    return list(out.values())


def link_nivalok(magok: list[TeszNivalo], tesz: list[TeszSzin], strict: bool = True) -> tuple[list[TeszNivalo], list[TeszSzin], list[str]]:
    """TESZ-nivaló–változat kapcsolás: öröklés, R15 (pontosan egy ALAP-SZÍN TESZ-nivalónként), üres TESZ-nivalók.

    strict=True (P1 séma): a hiba ValueError. strict=False (P2 után): javít és figyelmeztet.
    Visszaad: (TESZ-nivalók, változatok, figyelmeztetések). TESZ-nivaló nélküli TESZ-ekhez egyelemű TESZ-nivaló készül.
    """
    warnings: list[str] = []
    by_id = {m.nivalo_id: m for m in magok}
    if len(by_id) != len(magok):
        dup = sorted({m.nivalo_id for m in magok if sum(x.nivalo_id == m.nivalo_id for x in magok) > 1})
        raise ValueError(f"Ismétlődő nivalo_id: {dup}")
    out: list[TeszSzin] = []
    extra: list[TeszNivalo] = []
    for t in tesz:
        if t.nivalo_id is None:
            m = legacy_nivalo(t)
            extra.append(m)
            out.append(t.model_copy(update={"nivalo_id": m.nivalo_id, "alap_szin": True}))
            continue
        m = by_id.get(t.nivalo_id)
        if m is None:
            raise ValueError(f"{t.cand_id}: ismeretlen nivalo_id '{t.nivalo_id}'; a nivalok listájából válassz.")
        out.append(inherit(m, t))
    # Egy TESZ-nivalón belül a SZÍN a változat kiléte: azonos ⟨KÖZ-eg, HELY-zet⟩ = ugyanaz a TESZ-SZÍN.
    seen: dict[tuple, int] = {}
    dedup: list[TeszSzin] = []
    for t in out:
        k = (t.nivalo_id, t.kozeg, t.helyzet)
        if k in seen:
            keep = dedup[seen[k]]
            if t.alap_szin and not keep.alap_szin:
                dedup[seen[k]] = t.model_copy(update={"kozeg_secondary": keep.kozeg_secondary or t.kozeg_secondary})
            warnings.append(f"{t.cand_id} és {keep.cand_id}: azonos SZÍN ugyanazon a TESZ-nivalón ({t.nivalo_id}, "
                            f"{t.kozeg}/{t.helyzet}) – egy TESZ-SZÍN-ként maradt meg ({dedup[seen[k]].cand_id}).")
            continue
        seen[k] = len(dedup)
        dedup.append(t)
    out = dedup
    used = {t.nivalo_id for t in out}
    final_nivalok = []
    for m in magok:
        if m.nivalo_id not in used:
            if strict:
                raise ValueError(f"{m.nivalo_id}: a TESZ-nivalónak nincs változata (legalább az ALAP-SZÍN kell).")
            warnings.append(f"{m.nivalo_id}: változat nélküli TESZ-nivaló elhagyva.")
            continue
        final_nivalok.append(m)
    final_nivalok += extra
    # R15: pontosan egy ALAP-SZÍN TESZ-nivalónként
    for m in final_nivalok:
        idx = [i for i, t in enumerate(out) if t.nivalo_id == m.nivalo_id]
        alap = [i for i in idx if out[i].alap_szin]
        if len(alap) == 1:
            continue
        if strict:
            raise ValueError(f"R15 {m.nivalo_id}: {len(alap)} ALAP-SZÍN változat; pontosan egy kell "
                             "(alap_szin: true), a többi alap_szin: false.")
        keep = alap[0] if alap else idx[0]
        for i in idx:
            out[i] = out[i].model_copy(update={"alap_szin": i == keep})
        warnings.append(f"R15 {m.nivalo_id}: az ALAP-SZÍN {out[keep].cand_id} lett.")
    return final_nivalok, out, warnings


class TeszKor(_Base):
    kor_id: str
    name_hu: str
    time_share_pct: float = 0


class EqfBand(_Base):
    min: int = Field(ge=1, le=8)
    max: int = Field(ge=1, le=8)
    rationale_hu: str = ""


class SkillMention(_Base):
    text: str
    related_cand_ids: list[str] = Field(default_factory=list)


class P1Output(_Base):
    job_id: str
    job_label_hu: str = ""
    job_label_en: str = ""
    title_aliases: list[Alias] = Field(default_factory=list)
    purpose_hu: str = ""
    eqf_band: Optional[EqfBand] = None
    tesz_korok: list[TeszKor] = Field(default_factory=list)
    nivalok: list[TeszNivalo] = Field(default_factory=list)
    szinek: list[TeszSzin] = Field(min_length=1)
    skill_mentions: list[SkillMention] = Field(default_factory=list)
    # A determinisztikus javítások (pl. azonos SZÍN egy TESZ-nivalón) nyoma; a modell nem írja.
    link_warnings: list[str] = Field(default_factory=list)

    @model_validator(mode="before")
    @classmethod
    def _regi_nevek(cls, data):
        # 2026-11-04 előtti mezőnevek (TESZ-nivaló / TESZ-SZÍN): tesz_magok -> nivalok, tesz -> szinek
        if isinstance(data, dict):
            data = dict(data)
            if "tesz_magok" in data and "nivalok" not in data:
                data["nivalok"] = data.pop("tesz_magok")
            if "tesz" in data and "szinek" not in data:
                data["szinek"] = data.pop("tesz")
        return data

    @model_validator(mode="after")
    def _link(self):
        self.nivalok, self.szinek, w = link_nivalok(self.nivalok, self.szinek, strict=True)
        self.link_warnings = list(dict.fromkeys(self.link_warnings + w))
        return self


class P2Change(_Base):
    """A bírálat egy eltérése a P1-hez képest. A változatlan elemeket a bíráló nem írja ki.

    FIX cand_id: változat-mezők; FIX nivalo_id (cand_id nélkül): TESZ-nivaló-mezők, minden változatra érvényes.
    DROP cand_id | nivalo_id; MERGE cand_id -> into; SPLIT cand_id -> new (változatok) vagy
    SPLIT nivalo_id -> new_nivalo + new (a TESZ-nivaló és változatai helyett); ADD new_nivalo? + new;
    MOVE cand_ids -> nivalo_id (egy TESZ-nivaló alá vonás); FOLD cand_id -> into, as: kozeg_secondary|helyzet_secondary.
    """
    model_config = ConfigDict(extra="ignore", populate_by_name=True)
    op: Literal["FIX", "DROP", "MERGE", "SPLIT", "ADD", "MOVE", "FOLD"]
    cand_id: Optional[str] = None
    nivalo_id: Optional[str] = None
    cand_ids: list[str] = Field(default_factory=list)  # MOVE
    set: dict = Field(default_factory=dict)          # FIX: a javított mezők
    into: Optional[str] = None                        # MERGE, FOLD: ebbe olvad
    as_: Optional[Literal["kozeg_secondary", "helyzet_secondary"]] = Field(default=None, alias="as")  # FOLD
    new_nivalo: list[TeszNivalo] = Field(default_factory=list)  # SPLIT nivalo_id / ADD: új TESZ-nivalók
    new: list[TeszSzin] = Field(default_factory=list)     # SPLIT / ADD: teljes új változatok
    failed_tests: list[str] = Field(default_factory=list)
    reason_hu: str = ""


class P2Output(_Base):
    verdict: Literal["ACCEPT", "FIXED", "EXPERT"]
    changes: list[P2Change] = Field(default_factory=list)
    coverage_gaps_hu: list[str] = Field(default_factory=list)
    expert_questions_hu: list[str] = Field(default_factory=list)


class P3Decision(_Base):
    case_id: str
    # TESZ-nivalónál csak SAME | NARROWER | BROADER | NEW; a SZÍN-egyezést a kód dönti el.
    # VARIANT: régi válaszok kompatibilitása (NEW-ként kezeljük).
    decision: Literal["SAME", "VARIANT", "NARROWER", "BROADER", "NEW"]
    target_id: Optional[str] = None
    new_aliases: list[Alias] = Field(default_factory=list)
    upgrade_proposal: Optional[object] = None
    note_hu: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class P3Output(_Base):
    """Egy munkakör összes kétes egyesítési esete egy hívásban."""
    decisions: list[P3Decision] = Field(default_factory=list)


class P6Bontas(_Base):
    label_hu: str
    munkakorok: list[str] = Field(default_factory=list)


class P6Item(_Base):
    id: str
    dontes: Literal["MARAD", "ATNEVEZ", "BONT"]
    label_hu: str = ""
    label_en: str = ""
    szer_hu: str = ""
    indok_hu: str = ""
    bontas: list[P6Bontas] = Field(default_factory=list)

    @model_validator(mode="after")
    def _teljes(self):
        if self.dontes == "ATNEVEZ" and not self.label_hu:
            raise ValueError(f"{self.id}: ATNEVEZ-hez label_hu kell.")
        if self.dontes == "BONT" and len(self.bontas) < 2:
            raise ValueError(f"{self.id}: BONT-hoz legalább 2 javasolt név kell.")
        return self


class P6Output(_Base):
    nivalok: list[P6Item] = Field(default_factory=list)


class P5Item(_Base):
    id: str
    attestation_hu: str
    rubric_hu: list[str] = Field(min_length=3, max_length=6)


class P5Output(_Base):
    items: list[P5Item] = Field(default_factory=list)


class P5NivaloItem(_Base):
    """TESZ-nivaló-rubrika: 3–6 alapkritérium, minden SZÍN-en érvényes."""
    id: str
    rubric_alap_hu: list[str] = Field(min_length=3, max_length=6)


class P5SzinItem(_Base):
    """SZÍN-rubrika: a változat 1–3 kiegészítő kritériuma az ALAP-SZÍN-hez képest."""
    id: str
    attestation_hu: str
    rubric_szin_hu: list[str] = Field(default_factory=list, max_length=3)


class P5NivaloOutput(_Base):
    nivalok: list[P5NivaloItem] = Field(default_factory=list)
    szinek: list[P5SzinItem] = Field(default_factory=list)


class P4aSkill(_Base):
    skill_id: Optional[str] = None
    label_hu: str
    label_en: str = ""
    definition_hu: str = ""
    kep_elemek: list[str] = Field(default_factory=list)
    tud_elemek: list[str] = Field(default_factory=list)
    aspects: list[list[str]] = Field(default_factory=list)
    erosseg: Literal["KAPU", "SULYOS", "TAMASZ", "HELYZETI", "POTOLHATO"]
    viselkedes_hu: list[str] = Field(default_factory=list)
    covered_tesz_ids: list[str] = Field(default_factory=list)
    external_ref: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class P4aOutput(_Base):
    anchor: dict
    skills: list[P4aSkill] = Field(default_factory=list)
    narrower_anchor: list[dict] = Field(default_factory=list)


# ---------------------------------------------------------------- E0 – kinyerés dokumentumból
class JobCodes(_Base):
    feor: str = ""
    isco: str = ""
    esco: str = ""
    onet: str = ""


class ExtractedJob(_Base):
    title: str
    title_en: str = ""
    lang: Literal["hu", "en"] = "hu"
    codes: JobCodes = Field(default_factory=JobCodes)
    employer: str = ""
    field: str = ""
    description: str = ""
    tasks: list[str] = Field(default_factory=list)
    requirements: str = ""
    conditions: str = ""
    entry_route: str = ""
    truncated: bool = False
    source_quote: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class E0Output(_Base):
    doc_type: Literal["JOB_DESCRIPTION", "JOB_AD", "CATALOG", "OTHER"] = "OTHER"
    jobs: list[ExtractedJob] = Field(default_factory=list)


class P1RelevanceFix(_Base):
    index: int = Field(ge=0)
    relevance: Literal["CORE", "SUPPORTING"]

    @field_validator("relevance", mode="before")
    @classmethod
    def _r(cls, v):
        return _alias("relevance", v)


class P1RelevanceRepair(_Base):
    fixes: list[P1RelevanceFix] = Field(min_length=1)
