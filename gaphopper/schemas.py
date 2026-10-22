"""A promptok kimeneteinek sémái (pydantic v2). A zárt listák a config/vocab.yaml-ból jönnek."""
from __future__ import annotations

from typing import Literal, Optional, Union

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .config import vocab

_V = vocab()
TESZ_TIPUS = set(_V["tesz_tipus"])
KOZEG = set(_V["kozeg"])
HELYZET = set(_V["helyzet"])
SZER_FAJTA = set(_V["szer_elem_fajta"])
SZER_SZEREP = set(_V["szer_elem_szerep"])
FREQUENCY = set(_V["frequency"])


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


class Tesz(_Base):
    cand_id: str
    kor_id: Optional[str] = None
    label_hu: str
    label_en: str = ""
    outcome_hu: str
    # Az igazolási mondatot és a rubrikát egyesítés után, kanonikus TESZ-enként egyszer írjuk (P5);
    # a P1 már nem adja ki őket. Régi eredményeknél még szerepelhetnek.
    attestation_hu: str = ""
    rubric_hu: list[str] = Field(default_factory=list)
    tesz_type: str
    tesz_type_runner_up: Optional[str] = None
    primary_szer: SzerElem
    secondary_szer: list[SzerElem] = Field(default_factory=list, max_length=4)
    kozeg: str
    kozeg_secondary: list[str] = Field(default_factory=list, max_length=2)
    helyzet: str
    helyzet_secondary: Optional[str] = None
    relevance: Literal["CORE", "SUPPORTING"] = "SUPPORTING"
    frequency: Optional[str] = None
    expected_eqf: int = Field(ge=1, le=8)
    # egyetlen rövid mondat; régi eredményeknél szótár (type/szer/kozeg/helyzet)
    rationale_hu: Optional[Union[str, dict[str, str]]] = None
    confidence: float = Field(default=0.5, ge=0, le=1)
    open_question: Optional[str] = None

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
    tesz: list[Tesz] = Field(min_length=1)
    skill_mentions: list[SkillMention] = Field(default_factory=list)


class P2Change(_Base):
    """A bírálat egy eltérése a P1-hez képest. A változatlan TESZ-eket a bíráló nem írja ki."""
    op: Literal["FIX", "DROP", "MERGE", "SPLIT", "ADD"]
    cand_id: Optional[str] = None
    set: dict = Field(default_factory=dict)          # FIX: a javított mezők
    into: Optional[str] = None                        # MERGE: ebbe olvad
    new: list[Tesz] = Field(default_factory=list)     # SPLIT / ADD: teljes új TESZ-ek
    failed_tests: list[str] = Field(default_factory=list)
    reason_hu: str = ""


class P2Output(_Base):
    verdict: Literal["ACCEPT", "FIXED", "EXPERT"]
    changes: list[P2Change] = Field(default_factory=list)
    coverage_gaps_hu: list[str] = Field(default_factory=list)
    expert_questions_hu: list[str] = Field(default_factory=list)


class P3Decision(_Base):
    case_id: str
    decision: Literal["SAME", "VARIANT", "NARROWER", "BROADER", "NEW"]
    target_id: Optional[str] = None
    new_aliases: list[Alias] = Field(default_factory=list)
    upgrade_proposal: Optional[object] = None
    note_hu: str = ""
    confidence: float = Field(default=0.5, ge=0, le=1)


class P3Output(_Base):
    """Egy munkakör összes kétes egyesítési esete egy hívásban."""
    decisions: list[P3Decision] = Field(default_factory=list)


class P5Item(_Base):
    id: str
    attestation_hu: str
    rubric_hu: list[str] = Field(min_length=3, max_length=6)


class P5Output(_Base):
    items: list[P5Item] = Field(default_factory=list)


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


class P1RelevanceRepair(_Base):
    fixes: list[P1RelevanceFix] = Field(min_length=1)
