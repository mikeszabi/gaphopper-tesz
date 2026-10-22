import json

from gaphopper import config, web
from gaphopper.llm import LocalLLM, get_llm
from gaphopper.report import summary
from gaphopper.store import Store


def test_save_switch_and_endpoint(monkeypatch):
    monkeypatch.setenv("TEST_API_KEY", "secret")
    state = web.models_state()
    state["steps"][1].update(provider="gemini", model="gemini-test", effort="")
    state["openai"].update(base_url="https://example.com/v1", api_key_env="TEST_API_KEY")
    state["embed"].update(model="test-embed", host="http://localhost:11434")
    web.save_models(state)
    s = config.settings()
    assert s.p1.provider == "gemini" and s.p1.model == "gemini-test"
    assert s.embed_model == "test-embed"
    llm = get_llm(config.StepModel("openai", "custom", None, 100), "P1", settings=s)
    assert llm.base_url == "https://example.com/v1"
    assert llm.api_key == "secret" and llm.use_proxy
    assert "secret" not in config.MODELS_PATH.read_text()


def test_catalog_persisted(monkeypatch):
    monkeypatch.setattr(LocalLLM, "models", lambda self: ["model-b", "model-a"])
    r = web.discover_models({"provider": "openai", "base_url": "https://example.com/v1"})
    assert r == {"ok": True, "models": ["model-a", "model-b"]}
    assert web.models_state()["catalog"]["openai"] == r["models"]


def test_embed_probe_and_error(monkeypatch):
    from gaphopper.embed import Embedder
    monkeypatch.setattr(Embedder, "encode", lambda self, text: [1.0, 0.0])
    assert web.test_embed({"model": "test"})["dimension"] == 2
    assert not web.test_embed({"model": ""})["ok"]


def test_cost_unknown_cache_and_retry(tmp_path):
    config.save_ui_models({"prices": {"test": [1, 2, 0.5]}})
    store = Store(tmp_path / "usage.sqlite")
    for ok in (False, True):
        store.log("job", "P1", "openai", "test", "v", "", "{}", ok,
                  {"input_tokens": 1000000, "output_tokens": 1000000, "cache_read_input_tokens": 1000000})
    row = summary(store, by=("model",))[0]
    assert row["koltseg_usd"] == 7.0
    assert row["hivas"] == 2 and row["cache_read"] == 2000000
    store.log("job", "P2", "gemini", "unknown", "v", "", "{}", True, {})
    assert summary(store, by=("step",))[1]["koltseg_usd"] is None


def test_openai_cache_normalization(monkeypatch):
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self): return json.dumps({"choices": [{"message": {"content": "{}"}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20,
                      "prompt_tokens_details": {"cached_tokens": 40}}}).encode()
    monkeypatch.setattr(LocalLLM, "_open", lambda *args: Response())
    llm = LocalLLM(config.StepModel("openai", "test", None, 100), "https://example.com/v1")
    _, usage = llm.complete("system", "user")
    assert usage == {"input_tokens": 60, "cache_read_input_tokens": 40, "output_tokens": 20}


def test_saved_empty_embed_disables_environment(monkeypatch):
    monkeypatch.setenv("GH_EMBED_MODEL", "old-model")
    config.save_ui_models({"embed": {"model": "", "prefix": ""}})
    assert config.settings().embed_model == ""


def test_gemini_choices_and_prices_available_offline():
    state = web.models_state()
    ids = state["catalog"]["gemini"]
    assert len(ids) >= 10
    prices = {r["model"]: r for r in state["prices"]}
    assert all(m in prices for m in ids)
    assert prices["gemini-2.5-flash"]["input"] == 0.30
    web.save_models(state)
    # Merely saving the screen must preserve the built-in long-context tariff.
    assert config.price("gemini", "gemini-3.1-pro-preview", 200001) == (4, 18, 0.4)


def test_gemini_cost_bands_per_call(tmp_path):
    store = Store(tmp_path / "bands.sqlite")
    for tokens in (150000, 150000, 250000):
        store.log("job", "P1", "gemini", "gemini-3.1-pro-preview", "v", "", "{}", True,
                  {"input_tokens": tokens, "output_tokens": 1000})
    result = summary(store, by=("step",))[0]
    # Two short prompts: .624; one long prompt: 1.018.
    assert result["koltseg_usd"] == 1.642
    assert result["hivas"] == 3
    config.save_ui_models({"prices": {"gemini-3.1-pro-preview": [1, 2, 0.1]}})
    assert config.price("gemini", "gemini-3.1-pro-preview", 250000) == (1, 2, 0.1)


def test_openai_connection_unsaved_values_and_no_mutation(monkeypatch):
    monkeypatch.setenv("PROBE_KEY", "probe-secret")
    config.save_ui_models({"openai": {"base_url": "https://saved.example/v1"}})
    before = config.MODELS_PATH.read_text()
    def models(client):
        assert client.base_url == "https://unsaved.example/v1"
        assert client.api_key == "probe-secret"
        assert client.timeout == 15 and client.use_proxy
        return ["b", "a"]
    monkeypatch.setattr(LocalLLM, "models", models)
    result = web.test_openai({"base_url": "https://unsaved.example/v1/", "api_key_env": "PROBE_KEY"})
    assert result == {"ok": True, "models": ["a", "b"]}
    assert config.MODELS_PATH.read_text() == before


def test_openai_connection_errors_and_secret_redaction(monkeypatch):
    assert not web.test_openai({"base_url": "bad", "api_key_env": ""})["ok"]
    monkeypatch.delenv("MISSING_PROBE_KEY", raising=False)
    result = web.test_openai({"base_url": "https://example.com/v1", "api_key_env": "MISSING_PROBE_KEY"})
    assert not result["ok"] and "nincs kitöltve" in result["error"]
    monkeypatch.setenv("PROBE_KEY", "probe-secret")
    def failed(client):
        raise RuntimeError("401: probe-secret")
    monkeypatch.setattr(LocalLLM, "models", failed)
    result = web.test_openai({"base_url": "https://example.com/v1", "api_key_env": "PROBE_KEY"})
    assert not result["ok"] and "401" in result["error"]
    assert "probe-secret" not in result["error"]


def test_primary_ui_no_local_provider_and_preserves_legacy(monkeypatch):
    config.save_ui_models({"local": {"base_url": "http://legacy/v1", "model": "legacy"}})
    state = web.models_state()
    assert "local" not in state["providers"]
    state.pop("local")
    web.save_models(state)
    assert config.ui_models()["local"]["model"] == "legacy"


def test_openai_refused_connection_explains_port(monkeypatch):
    import urllib.error
    def refused(client):
        raise urllib.error.URLError(ConnectionRefusedError(111, "Connection refused"))
    monkeypatch.setattr(LocalLLM, "models", refused)
    result = web.test_openai({"base_url": "http://192.168.1.37/v1", "api_key_env": ""})
    assert not result["ok"]
    assert "192.168.1.37:80" in result["error"]
    assert "port" in result["error"]


def test_legacy_batch_states_presented_as_waiting():
    for old, expected in (("P1_KOTEGBEN", "NYERS"), ("P2_KOTEGBEN", "P1_KESZ")):
        assert web.display_status(old) == expected
        phases, _ = web.phases(old, "", {}, {}, None)
        assert all(p["state"] != "batch" for p in phases)


def test_start_run_restores_legacy_waiting_states(tmp_path, monkeypatch):
    store = Store(tmp_path / "legacy.sqlite")
    for job, status in (("a", "P1_KOTEGBEN"), ("b", "P2_KOTEGBEN")):
        store.upsert_job({"job_id": job, "title": job})
        store.set_status(job, status)
    calls = []
    monkeypatch.setattr(web.tasks, "start", lambda *a, **kw: calls.append((a, kw)) or "task")
    assert web.start_run({"until": "p2"}, store) == "task"
    assert store.status("a") == "NYERS"
    assert store.status("b") == "P1_KESZ"
    assert set(calls[0][1]["targets"]) == {"a", "b"}


def test_gemini_default_prefers_available_generation(monkeypatch):
    monkeypatch.setenv("GH_PROVIDER_P2", "gemini")
    monkeypatch.delenv("GH_MODEL_P2", raising=False)
    assert config.settings().p2.model == "gemini-3.8-flash"
    assert web.models_state()["catalog"]["gemini"][0] == "gemini-3.8-flash"


def test_parallel_graph_failure_does_not_stop_remaining_jobs(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from gaphopper import batch, cli, pipeline
    store = Store(tmp_path / "p3-errors.sqlite")
    for job in ("a", "b"):
        store.upsert_job({"job_id": job, "title": job})
        store.set_status(job, "P2_ELFOGADVA")
    ctx = SimpleNamespace(store=store, pv="v")
    monkeypatch.setattr(batch, "run_parallel", lambda *a, **kw: {"ok": 0, "hiba": 0})
    visited = []
    def process(ctx, jid):
        visited.append(jid)
        if jid == "a": raise TimeoutError("timeout")
        return "GRAFBAN"
    monkeypatch.setattr(pipeline, "process_job", process)
    cli._run_parallel(ctx, SimpleNamespace(until="graph", limit=None, workers=4), ["a", "b"])
    assert visited == ["a", "b"]
    assert store.status("a") == "P2_ELFOGADVA"
    assert store.artifact("a", "P3_ERR")["error"] == "timeout"
    assert "P3 hiba" in store.jobs()[0]["note"]


def test_openai_completion_timeout_is_actionable(monkeypatch):
    from gaphopper.llm import LLMError
    import pytest
    def timed_out(*args): raise TimeoutError("timed out")
    monkeypatch.setattr(LocalLLM, "_open", timed_out)
    llm = LocalLLM(config.StepModel("openai", "test", None, 8000), "http://example.com/v1", timeout=120)
    with pytest.raises(LLMError, match="120 mp"):
        llm.complete("s", "u")


def test_approval_starts_tracked_background_task(tmp_path, monkeypatch):
    store = Store(tmp_path / "approval.sqlite")
    store.upsert_job({"job_id": "a", "title": "Test"})
    qid = store.enqueue("a", "job_review", {})
    monkeypatch.setattr(web.tasks, "running", lambda: [])
    calls = []
    monkeypatch.setattr(web.tasks, "start", lambda *a, **kw: calls.append((a, kw)) or "approval-task")
    assert web.start_approval(store, qid) == {"task": "approval-task"}
    assert calls[0][0][1] == ["review", "--approve", str(qid)]
    assert calls[0][1]["targets"] == ["a"]
    assert len(store.queue()) == 1  # The worker closes the request only after writing.


def test_approval_rejects_duplicate_or_closed_requests(tmp_path, monkeypatch):
    import pytest
    store = Store(tmp_path / "approval.sqlite")
    store.upsert_job({"job_id": "a", "title": "Test"})
    qid = store.enqueue("a", "job_review", {})
    monkeypatch.setattr(web.tasks, "running", lambda: [{"targets": ["a"]}])
    with pytest.raises(ValueError, match="már fut"):
        web.start_approval(store, qid)
    store.decide(qid, "JOVAHAGYVA")
    with pytest.raises(ValueError, match="le van zárva"):
        web.start_approval(store, qid)


def test_approval_is_visible_as_running_p3(monkeypatch):
    monkeypatch.setattr(web.tasks, "running", lambda: [{"args": ["review", "--approve", "1"], "targets": ["a"]}])
    rows = [{"job_id": "a", "phases": [{"state": "wait"} for _ in range(5)]}]
    web._mark_running(rows)
    assert rows[0]["phases"][3]["state"] == "run"
