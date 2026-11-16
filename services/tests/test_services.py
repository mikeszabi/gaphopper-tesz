from copy import deepcopy
from fastapi.testclient import TestClient
import pytest
from services.api.main import create_app
from services.api.accounts import MemoryState
from services.api.models import AIChoice, CareerSelections, GeneratedMap, Selection, Triplet, JobIdentification, CompetencyIdentification, EvidenceMatch
from gaphopper.config import vocab


class Repo:
    def ready(self):
        pass

    def competencies(self):
        return [{"id": f"k{i}", "label": f"Kompetencia {i}", "description": ""} for i in range(1, 5)]

    def jobs(self):
        return [{"id": f"j{i}", "title": f"Munkakör {i}", "title_en": "", "aliases": []} for i in range(1, 7)]

    def profile(self, job):
        return {"tasks": ["Kapcsolattartás", "Adatok kezelése"], "baseline_available": True,
                "triplets": [{"id": "t1", "tesz_nivalo": "Adatkezelés", "competency_ids": ["k1", "k2"]}],
                "competencies": [{"id": k, "label": k, "evidence": ["t1"]} for k in ["k1", "k2"]]}

    def candidates(self):
        skills = [["k1"], ["k1", "k2"], ["k1", "k2", "k3"], ["k1", "k3"], ["k1", "k2", "k4"], ["k1", "k4"]]
        return [{"id": f"j{i}", "title": f"Munkakör {i}", "competency_ids": ks,
                 "sectors": ["Közeli" if i < 4 else "Távoli"], "sector_ids": ["a" if i < 4 else "b"],
                 "taxonomy": ["x" if i < 4 else "y"]} for i, ks in enumerate(skills, 1)]


class FakeAI:
    def __init__(self):
        self.calls = []

    def choice(self, requested):
        return requested or AIChoice(provider="openai", model="test-model")

    def ask(self, choice, instruction, data, schema, check=None):
        self.calls.append((choice, deepcopy(data)))
        if schema is JobIdentification:
            result = JobIdentification(matches=[EvidenceMatch(id='j1',confidence=.92,evidence=['Adatok kezelése'],reason='A feladatok alapján.')],extracted_tasks=['Adatok kezelése'])
        elif schema is CompetencyIdentification:
            result = CompetencyIdentification(matches=[EvidenceMatch(id='k2',confidence=.88,evidence=['Adatok kezelése'],reason='A tapasztalat alapján.')])
        elif schema is GeneratedMap:
            v = vocab()
            result = GeneratedMap(triplets=[Triplet(id="generated-1", tesz_nivalo="Adatkezelés",
                tesz_type=next(iter(v["tesz_tipus"])), szer_elem="Adat", szer_elem_kind="DATA",
                kozeg="KOZ_DIGITALIS", helyzet="HELY_RUTIN", task_indices=list(range(len(data["tasks"]))),
                competency_ids=["k2", "k3"], reason="A megadott feladatok alapján.")])
        else:
            result = CareerSelections(suggestions=[Selection(job_id=c["id"], category=category, reason="Kompetenciák és tapasztalat alapján.")
                for category, pool in data["pools"].items() for c in pool[:data["counts"][category]]])
        if check:
            check(result)
        return result


@pytest.fixture
def setup():
    ai = FakeAI()
    with TestClient(create_app(Repo(), ai, MemoryState())) as client:
        client.headers['X-Gaphopper-Client']='web'
        assert client.post('/api/v1/auth/login',json={'username':'gaphopper','password':'gaphopper'}).status_code==200
        yield client, ai


def test_default_map_no_ai_and_unicode_task_equality(setup):
    client, ai = setup
    response = client.post('/api/v1/job-maps', json={"job_id": "j1", "tasks": ["  ADATOK kezelése ", "kapcsolattartás"]})
    assert response.status_code == 200
    assert response.json()['source'] == 'default'
    assert response.json()['user_job_1_difference']['changed'] is False
    assert not ai.calls


def test_changed_tasks_generate_and_record_difference(setup):
    client, ai = setup
    response = client.post('/api/v1/job-maps', json={"job_id": "j1", "tasks": ["Új feladat"], "ai": {"provider": "gemini", "model": "chosen"}})
    assert response.status_code == 200
    data = response.json()
    assert data['source'] == 'generated'
    assert data['user_job_1_difference']['added'] == ['Új feladat']
    assert len(data['user_job_1_difference']['removed']) == 2
    assert {c['id'] for c in data['user_job_1_competencies']} == {'k2', 'k3'}
    assert ai.calls[0][0].model == 'chosen'


def test_tasks_only_input(setup):
    client, _ = setup
    data = client.post('/api/v1/job-maps', json={"tasks": ["Új feladat"]}).json()
    assert data['job_id'] is None
    assert data['user_job_1_difference']['changed'] is None
    assert data['warnings']


def test_comparison_has_matches_missing_extra(setup):
    client, ai = setup
    response = client.post('/api/v1/comparisons', json={"job_title": "Munkakör 1", "user_competency_map": ["k1", "k3", "k1"]})
    assert response.status_code == 200
    data = response.json()
    assert [k['id'] for k in data['user_job_competency_matches']] == ['k1']
    assert [k['id'] for k in data['user_job_competency_missing']] == ['k2']
    assert [k['id'] for k in data['user_competency_extra']] == ['k3']
    assert data['coverage'] == .5
    assert not ai.calls


def test_changed_comparison_generates_first(setup):
    client, ai = setup
    data = client.post('/api/v1/comparisons', json={"job_id":"j1", "tasks":["Új feladat"], "user_competency_map":["k3"]}).json()
    assert data['job_map']['source'] == 'generated'
    assert data['coverage'] == .5
    assert len(ai.calls) == 1


@pytest.mark.parametrize('payload', [{}, {"job_title":" "}, {"job_id":"j1", "tasks":[]}, {"tasks":[""]},
    {"job_id":"j1", "ai":{"provider":"local", "model":"x"}}])
def test_invalid_inputs(setup, payload):
    client, _ = setup
    assert client.post('/api/v1/job-maps', json=payload).status_code == 422


def test_unknown_ids_are_rejected(setup):
    client, _ = setup
    assert client.post('/api/v1/job-maps', json={"job_id":"missing", "tasks":["Feladat"]}).status_code == 404
    assert client.post('/api/v1/comparisons', json={"job_id":"j1", "user_competency_map":["fake"]}).status_code == 422


def test_ambiguous_title_requires_id(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setattr(client.app.state.repo, 'jobs', lambda:[{"id":"1","title":"azonos"},{"id":"2","title":"azonos"}])
    assert client.post('/api/v1/job-maps', json={"job_title":"azonos"}).status_code == 409


def test_career_rank_category_and_no_duplicates(setup):
    client, ai = setup
    response = client.post('/api/v1/career-suggestions', json={"user_competency_map":["k1"],
        "user_work_experience":[{"job_id":"j1", "years":3}], "per_category":1})
    assert response.status_code == 200
    suggestions = response.json()['suggestions']
    assert len({s['job_id'] for s in suggestions}) == 3
    safe = next(s for s in suggestions if s['category']=='SAFE')
    assert safe['job_id'] == 'j1' and safe['coverage'] == 1
    develop = next(s for s in suggestions if s['category']=='DEVELOP')
    assert develop['job_id'] in {'j2', 'j3'} and develop['missing']
    alternate = next(s for s in suggestions if s['category']=='ALTERNATE')
    assert alternate['job_id'] in {'j4','j5','j6'}


def test_career_insufficient_data_reports_warning(setup, monkeypatch):
    client, _ = setup
    repo = client.app.state.repo
    rows = repo.candidates()[:1]
    monkeypatch.setattr(repo,'candidates',lambda:rows)
    data = client.post('/api/v1/career-suggestions', json={"user_competency_map":["k1"],
        "user_work_experience":[{"job_id":"j1"}]}).json()
    assert len(data['suggestions']) == 1
    assert len(data['warnings']) == 3


def test_bad_ai_references_fail_validation(setup, monkeypatch):
    client, ai = setup
    original = ai.ask
    def bad(*args, **kwargs):
        output = original(*args, **kwargs)
        output.triplets[0].competency_ids = ['invented']
        return output
    monkeypatch.setattr(ai, 'ask', bad)
    assert client.post('/api/v1/job-maps',json={"tasks":["Feladat"]}).status_code == 502


def test_auth_and_health(setup, monkeypatch):
    client, _ = setup
    monkeypatch.setenv('SERVICE_API_TOKEN','test-secret')
    client.cookies.clear()
    assert client.get('/healthz').status_code == 200
    assert client.get('/api/v1/jobs').status_code == 401
    assert client.get('/readyz').status_code == 401
    assert client.get('/api/v1/jobs',headers={'Authorization':'Bearer test-secret'}).status_code == 200
    assert client.get('/').status_code == 200


def test_readiness_failure_is_503(setup, monkeypatch):
    client, _ = setup
    def fail():
        raise RuntimeError('secret-host-details')
    monkeypatch.setattr(client.app.state.repo, 'ready', fail)
    response=client.get('/readyz')
    assert response.status_code == 503
    assert 'secret-host-details' not in response.text
