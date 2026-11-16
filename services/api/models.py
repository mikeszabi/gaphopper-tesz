from typing import Annotated, Literal
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from gaphopper.config import vocab

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Identifier = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=160)]


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AIChoice(Model):
    provider: Literal["anthropic", "gemini", "openai"]
    model: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=120)]


class JobRequest(Model):
    job_title: Text | None = None
    job_id: Identifier | None = None
    tasks: list[Text] | None = Field(default=None, min_length=1, max_length=100)
    ai: AIChoice | None = None

    @model_validator(mode="after")
    def target(self):
        if not (self.job_title or self.job_id or self.tasks):
            raise ValueError("Munkakör vagy feladatlista szükséges.")
        return self


class Competency(Model):
    id: Identifier
    label: str = ""
    evidence: list[str] = Field(default_factory=list)


class ComparisonRequest(JobRequest):
    user_competency_map: list[Identifier] = Field(min_length=1, max_length=500)


class Experience(Model):
    job_title: Text | None = None
    job_id: Identifier | None = None
    sector: Text | None = None
    years: float = Field(default=0, ge=0, le=80)

    @model_validator(mode="after")
    def target(self):
        if not (self.job_title or self.job_id):
            raise ValueError("A munkatapasztalathoz munkakör szükséges.")
        return self


class CareerRequest(Model):
    user_competency_map: list[Identifier] = Field(min_length=1, max_length=500)
    user_work_experience: list[Experience] = Field(min_length=1, max_length=30)
    per_category: int = Field(default=2, ge=1, le=2)
    ai: AIChoice | None = None


class Triplet(Model):
    id: Identifier
    tesz_nivalo: Text
    tesz_type: Identifier
    szer_elem: Text
    szer_elem_kind: Identifier
    kozeg: Identifier
    helyzet: Identifier
    task_indices: list[int] = Field(min_length=1, max_length=100)
    competency_ids: list[Identifier] = Field(min_length=1, max_length=20)
    reason: Text

    @model_validator(mode="after")
    def ontology(self):
        v = vocab()
        for field, section in (("tesz_type", "tesz_tipus"), ("szer_elem_kind", "szer_elem_fajta"),
                               ("kozeg", "kozeg"), ("helyzet", "helyzet")):
            value = getattr(self, field)
            if value not in v[section] or v[section][value].get("csak_kesz"):
                raise ValueError(f"Érvénytelen ontológiai érték: {field}={value}")
        return self


class GeneratedMap(Model):
    triplets: list[Triplet] = Field(min_length=1, max_length=80)


class Difference(Model):
    baseline_available: bool
    supplied: bool
    changed: bool | None
    added: list[str]
    removed: list[str]
    unchanged: list[str]


class MapResult(Model):
    job_id: str | None
    job_title: str
    source: Literal["default", "generated"]
    tasks: list[str]
    user_job_1_difference: Difference
    user_job_1_triplets: list[dict]
    user_job_1_competencies: list[Competency]
    ai: AIChoice | None
    warnings: list[str] = Field(default_factory=list)


class ComparisonResult(Model):
    job_map: MapResult
    user_job_competency_matches: list[Competency]
    user_job_competency_missing: list[Competency]
    user_competency_extra: list[Competency]
    coverage: float


class Selection(Model):
    job_id: Identifier
    category: Literal["SAFE", "DEVELOP", "ALTERNATE"]
    reason: Text


class CareerSelections(Model):
    suggestions: list[Selection] = Field(max_length=6)


class Suggestion(Model):
    job_id: str
    job_title: str
    sectors: list[str]
    category: Literal["SAFE", "DEVELOP", "ALTERNATE"]
    reason: str
    coverage: float
    matches: list[Competency]
    missing: list[Competency]


class CareerResult(Model):
    suggestions: list[Suggestion]
    ai: AIChoice
    warnings: list[str]
    candidate_count: int


Username = Annotated[str, StringConstraints(strip_whitespace=True, pattern=r"^[a-zA-Z0-9_.-]{3,64}$")]
Password = Annotated[str, StringConstraints(min_length=8, max_length=256)]


class LoginRequest(Model):
    username: Username
    password: Annotated[str, StringConstraints(min_length=1, max_length=256)]


class UserCreate(Model):
    username: Username
    password: Password
    role: Literal['admin', 'user'] = 'user'


class UserUpdate(Model):
    password: Password | None = None
    role: Literal['admin', 'user'] | None = None
    active: bool | None = None

    @model_validator(mode='after')
    def has_change(self):
        if all(v is None for v in (self.password, self.role, self.active)):
            raise ValueError('Legalább egy módosítás szükséges.')
        return self


class DocumentRequest(Model):
    text: Annotated[str, StringConstraints(strip_whitespace=True, min_length=20, max_length=60000)]
    ai: AIChoice | None = None


class EvidenceMatch(Model):
    id: Identifier
    confidence: float = Field(ge=0, le=1)
    evidence: list[Text] = Field(min_length=1, max_length=5)
    reason: Text
    # becsült EQF-szint (1-8), amelyen a személy az adott kompetenciát a bizonyíték szerint gyakorolja
    eqf: int | None = Field(default=None, ge=1, le=8)


class EqfEstimate(Model):
    level: int = Field(ge=1, le=8)
    rationale: Text


class JobIdentification(Model):
    matches: list[EvidenceMatch] = Field(max_length=3)
    extracted_tasks: list[Text] = Field(default_factory=list, max_length=100)
    warnings: list[Text] = Field(default_factory=list, max_length=10)


class CompetencyIdentification(Model):
    matches: list[EvidenceMatch] = Field(max_length=100)
    eqf: EqfEstimate | None = None
    warnings: list[Text] = Field(default_factory=list, max_length=10)


class DocumentMatch(EvidenceMatch):
    label: str


class IdentificationResult(Model):
    kind: Literal['job', 'competencies']
    matches: list[DocumentMatch]
    selected_job_id: str | None = None
    user_competency_map: list[str] = Field(default_factory=list)
    extracted_tasks: list[str] = Field(default_factory=list)
    person_eqf: EqfEstimate | None = None
    warnings: list[str]
    ai: AIChoice
    source_filename: str | None = None


# ------------------------------------------------------------------ kompetencialabor
JobIds = Annotated[list[Identifier], Field(min_length=1, max_length=6)]


class PersonCompetency(Model):
    id: Identifier
    eqf: int | None = Field(default=None, ge=1, le=8)


class Person(Model):
    competencies: list[PersonCompetency] = Field(min_length=1, max_length=300)
    eqf: int | None = Field(default=None, ge=1, le=8)


class LabProfilesRequest(Model):
    job_ids: JobIds


class LabGapRequest(Model):
    job_ids: JobIds
    person: Person


# ------------------------------------------------------------------ egyéni profil kivonatolása (forrástípusokkal)
SourceType = Literal['EXPLICIT', 'IMPLICIT', 'MUNKAKOR', 'KEPZES', 'STILUS']


class PersonExperience(Model):
    title: Text                                      # a munkakör megnevezése, ahogy a szövegben szerepel
    job_id: Identifier | None = None                 # a felbontott munkakörök közül, ha egyértelműen megfeleltethető
    years: float | None = Field(default=None, ge=0, le=60)
    evidence: list[Text] = Field(min_length=1, max_length=3)


class PersonTraining(Model):
    name: Text
    eqf: int | None = Field(default=None, ge=1, le=8)   # a képzés/végzettség EQF-szintje
    evidence: list[Text] = Field(min_length=1, max_length=3)


class PersonMatch(Model):
    id: Identifier
    forras: SourceType
    ref: int | None = Field(default=None, ge=0)      # MUNKAKOR: work_experience index, KEPZES: trainings index
    confidence: float = Field(ge=0, le=1)
    evidence: list[Text] = Field(default_factory=list, max_length=5)
    reason: Text
    eqf: int | None = Field(default=None, ge=1, le=8)


class PersonIdentification(Model):
    work_experience: list[PersonExperience] = Field(default_factory=list, max_length=30)
    trainings: list[PersonTraining] = Field(default_factory=list, max_length=30)
    matches: list[PersonMatch] = Field(default_factory=list, max_length=150)
    eqf: EqfEstimate | None = None
    warnings: list[Text] = Field(default_factory=list, max_length=10)


class LabSpaceRequest(Model):
    job_ids: list[Identifier] = Field(default_factory=list, max_length=6)
    person: Person | None = None

    @model_validator(mode="after")
    def target(self):
        if not self.job_ids and not self.person:
            raise ValueError("Munkakör vagy egyéni profil szükséges.")
        return self


class LabRecommendRequest(Model):
    person: Person
    limit: int = Field(default=12, ge=1, le=40)
    eqf_window: int | None = Field(default=None, ge=0, le=7)
