"""Kompetencialabor: profil, GAP és ajánlás determinisztikus számítása, valamint a /api/v1/lab végpontok."""
import pytest
from fastapi.testclient import TestClient

from services.api.accounts import MemoryState
from services.api.lab import PROFILE_QUERY, build_profiles, gap, recommend, weighted_median
from services.api.main import create_app
from services.tests.test_services import FakeAI, Repo

COMPS = {f"k{i}": {"id": f"k{i}", "label": f"Kompetencia {i}", "label_en": "", "description": "", "group": "H1",
                   "group_label": "Kognitív", "category": "KOG", "category_label": "Kognitív kompetenciák"}
         for i in range(1, 6)}


def row(job, szin, eqf, rel="CORE", freq="DAILY", ks=()):
    return {"job_id": job, "title": f"Munkakör {job}", "title_en": "", "purpose": "", "aliases": [], "szin_id": szin,
            "tesz_id": "t" + szin, "tesz": "TESZ " + szin, "relevance": rel, "frequency": freq, "eqf": eqf,
            "kontextus": "", "kozeg": "KOZ_DIGITALIS", "helyzet": "HELY_RUTIN",
            "ks": [{"id": k, "rang": i + 1} for i, k in enumerate(ks)]}


ROWS = [row("j1", "s1", 6, ks=["k1", "k2"]), row("j1", "s2", 4, rel="SUPPORTING", freq="MONTHLY", ks=["k2", "k3"]),
        row("j2", "s3", 4, ks=["k1"]), row("j2", "s4", 3, ks=["k4"])]


def test_weighted_median():
    assert weighted_median([(4, 1), (6, 3)]) == 6 and weighted_median([(4, 3), (6, 1)]) == 4
    assert weighted_median([]) is None


def test_profile_eqf_and_required_levels():
    p = build_profiles(ROWS, COMPS)["j1"]
    by = {c["id"]: c for c in p["competencies"]}
    # k2-t a CORE (EQF 6) és a SUPPORTING (EQF 4) TESZ is igényli: az elvárt szint a CORE-é, a súly összeadódik
    assert by["k2"]["required_eqf"] == 6 and by["k2"]["importance"] == 1.0 and by["k2"]["core"]
    # k3-at csak SUPPORTING igényli: annak szintje számít
    assert by["k3"]["required_eqf"] == 4 and not by["k3"]["core"]
    assert p["eqf"]["typical"] == 6 and (p["eqf"]["min"], p["eqf"]["max"]) == (4, 6)
    assert by["k1"]["category_label"] == "Kognitív kompetenciák"


def test_gap_statuses_and_distance():
    p = build_profiles(ROWS, COMPS)["j1"]
    g = gap(p, {"competencies": [{"id": "k1", "eqf": 6}, {"id": "k2", "eqf": 3}], "eqf": 5})
    st = {c["id"]: c["status"] for c in g["competencies"]}
    assert st == {"k1": "ok", "k2": "under", "k3": "missing"}
    k2 = next(c for c in g["competencies"] if c["id"] == "k2")
    assert k2["shortfall"] == 0.5                                   # (6 - 3) / 6
    w = {c["id"]: c["weight"] for c in g["competencies"]}
    assert g["distance"] == round((w["k2"] * 0.5 + w["k3"]) / sum(w.values()), 4)
    assert g["fit"] == round(1 - g["distance"], 4) and g["eqf_delta"] == 1
    # szint nélkül megadott kompetencia: az egyén általános szintje számít
    g2 = gap(p, {"competencies": [{"id": "k1"}], "eqf": 6})
    assert next(c for c in g2["competencies"] if c["id"] == "k1")["status"] == "ok"


def test_recommend_ranks_and_filters_by_eqf():
    profiles = build_profiles(ROWS, COMPS)
    person = {"competencies": [{"id": "k1", "eqf": 4}, {"id": "k4", "eqf": 4}], "eqf": 4}
    r = recommend(profiles, person, limit=5)
    assert [x["job_id"] for x in r["items"]] == ["j2", "j1"] and r["items"][0]["fit"] == 1.0
    assert r["items"][0]["band"] == "kesz" and r["items"][1]["missing"]
    # j2 tipikus szintje 3 (két egyenlő súlyú TESZ: 3 és 4, az alsó medián), j1-é 6: ±1 ablakban csak j2 marad
    assert recommend(profiles, person, eqf_window=1)["filtered"] == 1


class LabRepo(Repo):
    def query(self, cypher, **params):
        return ROWS if cypher == PROFILE_QUERY else list(COMPS.values())

    def jobs(self):
        return [{"id": j, "title": f"Munkakör {j}", "title_en": "", "aliases": []} for j in ("j1", "j2", "j3")]


@pytest.fixture
def client():
    ai = FakeAI()
    with TestClient(create_app(LabRepo(), ai, MemoryState())) as c:
        c.headers["X-Gaphopper-Client"] = "web"
        assert c.post("/api/v1/auth/login", json={"username": "gaphopper", "password": "gaphopper"}).status_code == 200
        yield c, ai


def test_lab_endpoints(client):
    c, ai = client
    jobs = c.get("/api/v1/lab/jobs").json()
    assert [j["id"] for j in jobs["items"]] == ["j1", "j2"] and jobs["items"][0]["eqf"] == 6   # j3: nincs profil
    assert c.post("/api/v1/lab/job-profiles", json={"job_ids": ["j1", "j3"]}).status_code == 404
    prof = c.post("/api/v1/lab/job-profiles", json={"job_ids": ["j1", "j2"]}).json()["profiles"]
    assert [p["id"] for p in prof] == ["j1", "j2"]
    person = {"competencies": [{"id": "k1", "eqf": 6}, {"id": "k5"}], "eqf": 5}
    g = c.post("/api/v1/lab/gap", json={"job_ids": ["j1", "j2"], "person": person}).json()
    assert len(g["jobs"]) == 2 and [x["id"] for x in g["extra"]] == ["k5"]
    bad = c.post("/api/v1/lab/gap", json={"job_ids": ["j1"], "person": {"competencies": [{"id": "zz"}]}})
    assert bad.status_code == 422
    rec = c.post("/api/v1/lab/recommendations", json={"person": person, "limit": 1}).json()
    assert len(rec["items"]) == 1 and len(rec["scatter"]) == 2
    assert c.post("/api/v1/lab/gap", json={"job_ids": [f"j{i}" for i in range(7)], "person": person}).status_code == 422
    assert not ai.calls                                               # a labor számításai AI nélkül futnak


def test_lab_identify_job_only_profiled(client):
    c, ai = client
    r = c.post("/api/v1/lab/identify-job", json={"text": "Álláshirdetés: adatok kezelése és kapcsolattartás."})
    assert r.status_code == 200
    assert {x["id"] for x in ai.calls[-1][1]["candidates"]} == {"j1", "j2"}


# ------------------------------------------------------------------ egyéni profil: öt forrástípus
from services.api.models import PersonExperience, PersonIdentification, PersonMatch, PersonTraining  # noqa: E402
from services.api.person import INHERIT, make_check  # noqa: E402

CV = ("Munkatapasztalat: Munkakör j1, 2019-2024. Tíz fős csapatot irányítottam. Magabiztos táblázatkezelés. "
      "Végzettség: informatikus BSc, 2018.")


def person_answer(**over):
    base = dict(
        work_experience=[PersonExperience(title="Munkakör j1", job_id="j1", years=5, evidence=["Munkakör j1, 2019-2024"])],
        trainings=[PersonTraining(name="informatikus BSc", eqf=6, evidence=["informatikus BSc"])],
        matches=[PersonMatch(id="k4", forras="EXPLICIT", confidence=.9, evidence=["Magabiztos táblázatkezelés"], reason="Kimondja.", eqf=5),
                 PersonMatch(id="k1", forras="IMPLICIT", confidence=.7, evidence=["Tíz fős csapatot irányítottam"], reason="Vezetés.", eqf=5),
                 PersonMatch(id="k5", forras="KEPZES", ref=0, confidence=.6, evidence=["informatikus BSc"], reason="A képzés fejleszti."),
                 PersonMatch(id="k3", forras="STILUS", confidence=.9, reason="Tagolt, tömör szerkezet.")])
    return PersonIdentification(**(base | over))


class PersonAI(FakeAI):
    answer = None

    def ask(self, choice, instruction, data, schema, check=None, **kw):
        if schema is PersonIdentification:
            self.calls.append((choice, data))
            if check:
                check(self.answer)
            return self.answer
        return super().ask(choice, instruction, data, schema, check)


def test_identify_person_sources_inheritance_and_merge():
    ai = PersonAI()
    ai.answer = person_answer()
    with TestClient(create_app(LabRepo(), ai, MemoryState())) as c:
        c.headers["X-Gaphopper-Client"] = "web"
        c.post("/api/v1/auth/login", json={"username": "gaphopper", "password": "gaphopper"})
        r = c.post("/api/v1/lab/identify-person", json={"text": CV})
    assert r.status_code == 200, r.text
    d = r.json()
    by = {x["id"]: x for x in d["competencies"]}
    # j1 alapfeladata (s1, CORE) k1-et és k2-t igényli: ezek örökölve; k3-at csak SUPPORTING igényli, nem öröklődik
    assert by["k2"]["types"] == ["MUNKAKOR"] and by["k2"]["sources"][0]["inherited"]
    assert by["k1"]["types"] == ["IMPLICIT", "MUNKAKOR"]
    assert by["k1"]["confidence"] == round(1 - (1 - .7) * (1 - by["k1"]["sources"][1]["confidence"]), 3)
    assert by["k1"]["eqf"] == 6                                     # a munkakör elvárt szintje (5 év: nincs levonás)
    assert by["k5"]["eqf"] == 6 and by["k5"]["sources"][0]["ref_label"] == "informatikus BSc"   # a képzés szintje
    assert by["k3"]["types"] == ["STILUS"] and by["k3"]["confidence"] == .6 and by["k3"]["eqf"] is None
    assert d["work_experience"][0]["job_title"] == "Munkakör j1" and d["work_experience"][0]["inherited"] == 2
    assert d["counts"]["MUNKAKOR"] == 2 and d["counts"]["STILUS"] == 1
    assert {j["id"] for j in ai.calls[-1][1]["jobs"]} == {"j1", "j2"}      # csak felbontott munkakör a jelölt


def test_identify_person_short_tenure_lowers_inherited_level():
    from services.api.lab import build_profiles
    from services.api.person import inherit
    p = build_profiles(ROWS, COMPS)["j1"]
    got = {x["id"]: x for x in inherit({"years": 0.5, "evidence": ["x"]}, p)}
    assert got["k1"]["eqf"] == 5 and got["k1"]["confidence"] < INHERIT["base"] + INHERIT["importance"] + .01


def test_identify_person_check_rejects_structural_errors():
    # csak a szerkezeti hiba vált ki újrapróbálást; az idézeteket a sanitize kezeli
    check = make_check(CV, COMPS, {"j1", "j2"})
    check(person_answer())
    bad = [
        person_answer(matches=[PersonMatch(id="k2", forras="KEPZES", ref=3, confidence=.5, evidence=["informatikus BSc"], reason="x")]),
        person_answer(matches=[PersonMatch(id="k2", forras="MUNKAKOR", confidence=.5, evidence=["x"], reason="ref nélkül")]),
        person_answer(matches=[PersonMatch(id="zz", forras="EXPLICIT", confidence=.5, evidence=["informatikus BSc"], reason="x")]),
        person_answer(work_experience=[PersonExperience(title="X", job_id="j9", evidence=["Munkakör j1, 2019-2024"])]),
    ]
    for b in bad:
        with pytest.raises(ValueError):
            check(b)


def test_identify_person_sanitize_keeps_good_matches():
    """2026-11-16: egy valódi CV-nél egyetlen pontatlan képzési idézet miatt az egész profil elveszett (502).
    Most a nem ellenőrizhető idézet kimarad, a többi találat megmarad, és figyelmeztetés jelzi."""
    from services.api.person import sanitize
    o = person_answer(
        trainings=[PersonTraining(name="BSc", eqf=6, evidence=["Végzettség – informatikus BSc (2018)"])],
        matches=[PersonMatch(id="k4", forras="EXPLICIT", confidence=.9, evidence=["Magabiztos táblázatkezelés"], reason="ok"),
                 PersonMatch(id="k4", forras="EXPLICIT", confidence=.9, evidence=["Magabiztos táblázatkezelés"], reason="ismétlés"),
                 PersonMatch(id="k1", forras="IMPLICIT", confidence=.7, evidence=["Húsz fős osztályt vezettem"], reason="kitalált"),
                 PersonMatch(id="k2", forras="MUNKAKOR", ref=0, confidence=.5, evidence=["Munkakör j1, 2019-2024"], reason="örökölt helyett"),
                 PersonMatch(id="k3", forras="STILUS", confidence=.5, reason="tagolt"),
                 PersonMatch(id="k5", forras="IMPLICIT", confidence=.6, evidence=["Tíz fős csapatot irányítottam!", "nem szerepel sehol a szövegben"], reason="x")])
    o, warnings = sanitize(o, CV)
    assert [(m.id, m.forras) for m in o.matches] == [("k4", "EXPLICIT"), ("k3", "STILUS"), ("k5", "IMPLICIT")]
    assert o.matches[2].evidence == ["Tíz fős csapatot irányítottam!"]           # írásjel-eltérés elfogadva
    assert o.trainings[0].evidence == ["Végzettség – informatikus BSc (2018)"]    # írásjelek nélkül egyezik
    assert any("1 találat kimaradt" in w for w in warnings)


def test_identify_person_invalid_answer_gives_clear_error():
    from gaphopper.llm import LLMError

    class Broken(PersonAI):
        def ask(self, choice, instruction, data, schema, check=None, **kw):
            raise LLMError("Sémahiba: Ismeretlen kompetenciaazonosítók: ['zz']")
    with TestClient(create_app(LabRepo(), Broken(), MemoryState())) as c:
        c.headers["X-Gaphopper-Client"] = "web"
        c.post("/api/v1/auth/login", json={"username": "gaphopper", "password": "gaphopper"})
        r = c.post("/api/v1/lab/identify-person", json={"text": CV})
    assert r.status_code == 502 and "Ismeretlen kompetenciaazonosítók" in r.json()["detail"]


# ------------------------------------------------------------------ kompetenciatér
def test_space_layout_clusters_and_endpoint(client):
    import numpy as np
    from services.api.space import clusters, geodesic, mds
    # két jól elkülönülő csoport: {0,1,2} és {3,4,5}
    sim = np.eye(6)
    for a, b in [(0, 1), (1, 2), (0, 2), (3, 4), (4, 5), (3, 5)]:
        sim[a, b] = sim[b, a] = .9
    sim[2, 3] = sim[3, 2] = .1
    d = geodesic(sim)
    lab = clusters(d, 2)
    assert lab[0] == lab[1] == lab[2] != lab[3] == lab[4] == lab[5]
    xy = mds(d)
    assert xy.min() >= 0 and xy.max() <= 1
    assert np.linalg.norm(xy[0] - xy[1]) < np.linalg.norm(xy[0] - xy[4])
    c, _ = client
    person = {"competencies": [{"id": "k1", "eqf": 6}, {"id": "k5"}], "eqf": 5}
    r = c.post("/api/v1/lab/space", json={"job_ids": ["j1", "j2"], "person": person})
    assert r.status_code == 200, r.text
    sp = r.json()
    by = {n["id"]: n for n in sp["nodes"]}
    assert set(by) == {"k1", "k2", "k3", "k4", "k5"} and by["k5"]["own"] and not by["k5"]["jobs"]
    assert by["k1"]["jobs"]["j1"]["status"] == "ok" and by["k3"]["jobs"]["j1"]["status"] == "missing"
    assert len(sp["jobs"]) == 2 and sp["person"] is not None
    assert sum(cl["size"] for cl in sp["clusters"]) == 5
    # személy nélkül is: a munkakörök kompetenciáinak tere, állapot nélkül
    r2 = c.post("/api/v1/lab/space", json={"job_ids": ["j1"]}).json()
    assert r2["person"] is None and all(n["jobs"]["j1"]["status"] is None for n in r2["nodes"])
    assert c.post("/api/v1/lab/space", json={}).status_code == 422
