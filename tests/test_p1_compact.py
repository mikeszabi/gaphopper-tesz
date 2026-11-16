"""Compact P1 wire format must preserve full downstream objects."""
import json
from pathlib import Path
from types import SimpleNamespace

from gaphopper.config import Settings
from gaphopper.pipeline import Context, build_p1, llm_job_view, run_p1
from gaphopper.llm import FakeLLM
from gaphopper.schemas import P1Output

ROOT = Path(__file__).resolve().parents[1]


def test_compact_input_preserves_three_sample_jobs():
    jobs = [json.loads(line) for line in (ROOT / "data/sample_jobs.jsonl").read_text().splitlines()]
    assert len(jobs) >= 3
    for job in jobs[:3]:
        user = build_p1(SimpleNamespace(store=SimpleNamespace(artifact=lambda *a: None)), job)
        encoded = user.split("BEMENET:\n", 1)[1].split("\n\nKIMENET", 1)[0]
        assert json.loads(encoded) == llm_job_view(job)
        assert len(encoded) < len(json.dumps(job, ensure_ascii=False, indent=1))


def test_p1_input_skips_classification_refs():
    line = (ROOT / "data/job_all.jsonl")
    job = json.loads(line.read_text().splitlines()[0]) if line.exists() else {
        "job_id": "onet:x", "title": "t", "tasks": ["a"], "refs": {"esco": [{"code": "1"}]}, "codes": {"onet": "x"}}
    user = build_p1(SimpleNamespace(store=SimpleNamespace(artifact=lambda *a: None)), job)
    encoded = json.loads(user.split("BEMENET:\n", 1)[1].split("\n\nKIMENET", 1)[0])
    assert "refs" not in encoded and "codes" not in encoded
    assert encoded["tasks"] == job["tasks"] and encoded["title"] == job["title"]
    assert "refs" in job  # a tárolt rekord nem változik


def test_compact_p1_preserves_schema_and_saved_artifact(tmp_path):
    baseline = P1Output.model_validate_json((ROOT / "tests/fixtures/p1_nurse.json").read_text())
    compact = baseline.model_dump(exclude_none=True)

    def omit_empty(value):
        if isinstance(value, dict):
            return {k: omit_empty(v) for k, v in value.items() if v != []}
        if isinstance(value, list):
            return [omit_empty(v) for v in value]
        return value

    compact = omit_empty(compact)
    assert P1Output.model_validate(compact) == baseline
    s = Settings(state_db=tmp_path / "state.sqlite")
    ctx = Context(s, with_graph=False, llm_override={"P1": FakeLLM(lambda *a: json.dumps(compact, separators=(",", ":")))})
    job = {"job_id": baseline.job_id, "title": "Registered nurse"}
    ctx.store.upsert_job(job)
    assert run_p1(ctx, job) == baseline
    assert P1Output.model_validate(ctx.store.artifact(job["job_id"], "P1")) == baseline
    ctx.close()
