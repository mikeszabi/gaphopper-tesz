"""Offline tesztek: nem kell hozzájuk neo4j és API-kulcs.  python -m pytest -q"""
import json
import os
import re
from pathlib import Path

import pytest

from gaphopper import rules
from gaphopper.config import Settings, prompt, vocab
from gaphopper.llm import FakeLLM, LLMError, call_json, extract_json
from gaphopper.normalize import job_title_key, norm_key, stable_id
from gaphopper.schemas import P1Output, P2Output, Tesz

FIX = Path(__file__).parent / "fixtures"


def p1_fixture() -> dict:
    return json.loads((FIX / "p1_nurse.json").read_text(encoding="utf-8"))


def test_vocab_codes_in_p0():
    p0 = prompt("p0_system")
    v = vocab()
    for group in ("tesz_tipus", "kozeg", "helyzet", "szer_elem_fajta"):
        for code in v[group]:
            assert code in p0, f"{code} hiányzik a P0-ból"
    for role in v["szer_elem_szerep"]:
        assert role in p0


def test_prompt_placeholders():
    assert "{{job_record_json}}" in prompt("p1_felbontas")
    for ph in ("{{job_record_json}}", "{{p1_json}}", "{{rule_flags_json}}"):
        assert ph in prompt("p2_biralat")
    assert "{{cases_json}}" in prompt("p3_egyesites")
    assert "{{tesz_list_json}}" in prompt("p5_tanusitas")


def test_p1_fixture_valid_and_clean():
    p1 = P1Output.model_validate(p1_fixture())
    assert len(p1.tesz) == 14
    flags = rules.check(p1.tesz, [k.kor_id for k in p1.tesz_korok], "Health and care")
    assert [f.rule for f in flags] == [], flags


def test_schema_rejects_unknown_codes():
    d = p1_fixture()
    d["tesz"][0]["helyzet"] = "HELY_NEMLETEZO"
    with pytest.raises(Exception):
        P1Output.model_validate(d)


def test_rules_catch_bad_sets():
    d = p1_fixture()
    for t in d["tesz"]:
        t["tesz_type"] = "EXECUTE"
    d["tesz"][0]["label_hu"] = "Install, test"
    d["tesz"][1]["label_hu"] = "Apply Welding processes in professional work"
    d["tesz"][2]["confidence"] = 0.3
    p1 = P1Output.model_validate(d)
    got = {f.rule for f in rules.check(p1.tesz, [k.kor_id for k in p1.tesz_korok], "")}
    assert {"R4", "R5", "R10"} <= got
    assert rules.needs_expert(rules.check(p1.tesz))


def test_extract_json_with_fences():
    assert extract_json('Itt van:\n```json\n{"a": 1}\n```') == {"a": 1}


def test_call_json_retries_on_schema_error():
    answers = iter(['{"verdict": "OK"}', (FIX / "p2_nurse.json").read_text(encoding="utf-8")])
    llm = FakeLLM(lambda s, u: next(answers))
    obj, _ = call_json(llm, "sys", "user", P2Output, retries=1)
    assert obj.verdict == "ACCEPT"
    llm2 = FakeLLM(lambda s, u: "nem json")
    with pytest.raises(LLMError):
        call_json(llm2, "sys", "user", P2Output, retries=1)


def test_normalize_and_ids():
    assert norm_key("  Ápolói   dokumentáció, vezetése! ") == "ápolói dokumentáció vezetése"
    key, removed = job_title_key("Senior Backend Developer")
    assert key == "backend developer" and removed == ["senior"]
    assert job_title_key("Gyógytornász asszisztens")[0] == "gyógytornász asszisztens"
    assert stable_id("gh_tesz", "A", "B") == stable_id("gh_tesz", "a ", "b")


def test_pipeline_until_p2_with_fake_llm(tmp_path):
    from gaphopper.pipeline import Context, process_job

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.p2_mode = "always"
    p1_text = (FIX / "p1_nurse.json").read_text(encoding="utf-8")
    p2_text = (FIX / "p2_nurse.json").read_text(encoding="utf-8")
    ctx = Context(s, with_graph=False, llm_override={
        "P1": FakeLLM(lambda sy, u: p1_text), "P2": FakeLLM(lambda sy, u: p2_text)})
    rec = json.loads((Path(__file__).parents[1] / "data" / "sample_jobs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    ctx.store.upsert_job(rec)
    assert process_job(ctx, rec["job_id"], until="p2") == "P2_ELFOGADVA"
    assert ctx.store.artifact(rec["job_id"], "P2")["rule_flags_after"] == []
    # újrafuttatás nem hív újra LLM-et (az eredmény a tárban van)
    ctx.llm_override = {}
    assert process_job(ctx, rec["job_id"], until="p2") == "P2_ELFOGADVA"


def test_manual_mode_writes_prompts(tmp_path, monkeypatch):
    from gaphopper import pipeline

    monkeypatch.setattr(pipeline, "MANUAL_DIR", tmp_path / "manual")
    monkeypatch.setenv("GH_PROVIDER", "manual")
    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    ctx = pipeline.Context(s, with_graph=False)
    rec = {"job_id": "x:1", "title": "Pék", "field": "Élelmiszeripar", "description": "Kenyér és péksütemény készítése."}
    ctx.store.upsert_job(rec)
    out = pipeline.process_job(ctx, "x:1", until="p2")
    assert out.startswith("MANUAL:")
    d = Path(out.split(":", 1)[1])
    assert (d / "P1_user.md").read_text(encoding="utf-8").count("Pék") >= 1


@pytest.mark.skipif(not os.getenv("GH_TEST_XLSX"), reason="GH_TEST_XLSX nincs megadva")
def test_import_xlsx():
    from gaphopper.importers import load

    recs = load(os.environ["GH_TEST_XLSX"])
    assert len(recs) >= 1 and recs[0]["job_id"].startswith("onet:")


# ---------------------------------------------------------------- E0 kinyerés
DOCS = FIX / "docs"


def _e0_answer(system, user):
    if "Villanyszerelő" in user:
        return json.dumps({"doc_type": "JOB_AD", "jobs": [{"title": "Villanyszerelő", "lang": "hu",
                           "tasks": ["Épületvillamossági hálózatok kiépítése terv alapján", "Hibakeresés és javítás"],
                           "requirements": "villanyszerelő szakképesítés", "confidence": 0.9}]}, ensure_ascii=False)
    return json.dumps({"doc_type": "JOB_DESCRIPTION", "jobs": [
        {"title": "Raktári munkatárs", "tasks": ["áru beérkeztetése"], "confidence": 0.9},
        {"title": "Bérszámfejtő", "tasks": ["havi bérszámfejtés bérprogramban"], "confidence": 0.9}]},
        ensure_ascii=False)


def test_read_text_html_skips_script():
    from gaphopper.extract import read_text

    t = read_text(DOCS / "allas.html")
    assert "Villanyszerelő" in t and "NE OLVASD" not in t and "- Hibakeresés" in t


def test_chunk_and_merge():
    from gaphopper.extract import chunk_text, merge_jobs
    from gaphopper.schemas import ExtractedJob

    text = "\n\n".join(f"Bekezdés {i} " + "x" * 80 for i in range(30))
    ch = chunk_text(text, 500, 100)
    assert len(ch) > 3 and all(len(c) <= 600 for c in ch)
    a = ExtractedJob(title="Senior Bérszámfejtő", tasks=["havi bérszámfejtés"], truncated=True)
    b = ExtractedJob(title="bérszámfejtő", tasks=["havi bérszámfejtés", "bevallások"], codes={"feor": "3612"})
    m = merge_jobs([a, b])
    assert len(m) == 1 and m[0].tasks == ["havi bérszámfejtés", "bevallások"] and m[0].codes.feor == "3612"


def test_extract_files_to_jsonl(tmp_path):
    from gaphopper.extract import extract_file, iter_files, write_jsonl
    from gaphopper.pipeline import Context

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    ctx = Context(s, with_graph=False, llm_override={"E0": FakeLLM(_e0_answer)})
    recs = []
    for f in iter_files([str(DOCS)]):
        r, _ = extract_file(ctx, f, workers=2)
        recs += r
    assert sorted(r["title"] for r in recs) == ["Bérszámfejtő", "Raktári munkatárs", "Villanyszerelő"]
    ad = next(r for r in recs if r["title"] == "Villanyszerelő")
    assert ad["job_id"].startswith("ad:") and ad["source"] == "allas.html"
    out = tmp_path / "jobs.jsonl"
    assert write_jsonl(recs, out) == 3 and write_jsonl(recs, out) == 3  # idempotens


# ---------------------------------------------------------------- kötegelt és párhuzamos mód
class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class FakeBatchClient:
    """Az Anthropic messages.batches felület utánzata."""

    def __init__(self, answer, prefix="a"):
        self.answer, self.created, self.prefix = answer, [], prefix
        self.messages = _Obj(batches=self)

    def create(self, requests):
        self.created.append(requests)
        return _Obj(id=f"msgbatch_{self.prefix}_{len(self.created)}")

    def retrieve(self, batch_id):
        return _Obj(processing_status="ended", request_counts=None)

    def results(self, batch_id):
        reqs = self.created[int(batch_id.split("_")[-1]) - 1]
        for r in reqs:
            text = self.answer(r["params"]["messages"][0]["content"])
            yield _Obj(custom_id=r["custom_id"], result=_Obj(type="succeeded", message=_Obj(
                content=[_Obj(text=text)], usage=_Obj(model_dump=lambda: {"input_tokens": 1}))))


def test_batch_submit_collect_and_retry(tmp_path):
    from gaphopper import batch
    from gaphopper.pipeline import Context

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.p1.provider = s.p2.provider = "anthropic"
    s.p2_mode = "always"
    ctx = Context(s, with_graph=False)
    good = (FIX / "p1_nurse.json").read_text(encoding="utf-8")
    jobs = [json.loads(l) for l in (Path(__file__).parents[1] / "data" / "sample_jobs.jsonl").read_text(
        encoding="utf-8").splitlines()]
    for j in jobs:
        ctx.store.upsert_job(j)
    # az első munkakör jó választ kap, a többi hibásat
    client = FakeBatchClient(lambda user: good if jobs[0]["job_id"] in user else '{"rossz": 1}')
    ids = batch.submit(ctx, "P1", client=client)
    assert len(ids) == 1 and ctx.store.status(jobs[0]["job_id"]) == "P1_KOTEGBEN"
    assert all(len(r["custom_id"]) <= 64 for r in client.created[0])
    st = batch.collect(ctx, client=client)
    assert st == {"ok": 1, "hiba": 2, "fut": 0}
    assert ctx.store.status(jobs[0]["job_id"]) == "P1_KESZ"
    assert ctx.store.status(jobs[1]["job_id"]) == "NYERS"
    # újraküldéskor a hibaüzenet bekerül a promptba
    batch.submit(ctx, "P1", client=client)
    batch.collect(ctx, client=client)  # a második P1-köteg begyűjtése (ismét hibás válaszok)
    assert "NEM FELELT MEG A SÉMÁNAK" in client.created[1][0]["params"]["messages"][0]["content"]
    # P2 köteg a kész P1-re
    p2 = (FIX / "p2_nurse.json").read_text(encoding="utf-8")
    client2 = FakeBatchClient(lambda user: p2, prefix="b")
    batch.submit(ctx, "P2", client=client2)
    assert batch.collect(ctx, client=client2)["ok"] == 1
    assert ctx.store.status(jobs[0]["job_id"]) == "P2_ELFOGADVA"


def test_run_parallel(tmp_path):
    from gaphopper import batch
    from gaphopper.pipeline import Context

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.p2_mode = "always"
    p1 = (FIX / "p1_nurse.json").read_text(encoding="utf-8")
    p2 = (FIX / "p2_nurse.json").read_text(encoding="utf-8")
    ctx = Context(s, with_graph=False, llm_override={"P1": FakeLLM(lambda a, b: p1), "P2": FakeLLM(lambda a, b: p2)})
    for line in (Path(__file__).parents[1] / "data" / "sample_jobs.jsonl").read_text(encoding="utf-8").splitlines():
        ctx.store.upsert_job(json.loads(line))
    assert batch.run_parallel(ctx, "P1", workers=3) == {"ok": 3, "hiba": 0}
    assert batch.run_parallel(ctx, "P2", workers=3) == {"ok": 3, "hiba": 0}
    assert len(ctx.store.jobs("P2_ELFOGADVA")) == 3


def _fake_ollama(new_api: bool):
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            text = body.get("input") or body.get("prompt") or ""
            vec = [float(len(text)), 1.0, 0.0]
            if body.get("model") == "nincs":
                data = b'{"error":"model \\"nincs\\" not found, try pulling it first"}'
                self.send_response(404); self.send_header("Content-Length", str(len(data))); self.end_headers()
                self.wfile.write(data); return
            if self.path == "/api/embed" and new_api:
                out = {"embeddings": [vec]}
            elif self.path == "/api/embeddings" and not new_api:
                out = {"embedding": vec}
            else:
                self.send_response(404); self.send_header("Content-Length", "0"); self.end_headers(); return
            data = json.dumps(out).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def test_ollama_embedder_new_and_old_api():
    from gaphopper.embed import EmbedError, Embedder

    for new_api in (True, False):
        srv = _fake_ollama(new_api)
        try:
            e = Embedder("bge-m3", "ollama", f"http://127.0.0.1:{srv.server_port}", prefix="doc: ")
            v = e.encode("ab")
            assert e.dim == 3
            assert abs(sum(x * x for x in v) - 1.0) < 1e-9  # normalizált
            assert v[0] > v[1]  # "doc: ab" = 7 karakter
            with pytest.raises(EmbedError):
                Embedder("nincs", "ollama", f"http://127.0.0.1:{srv.server_port}").encode("x")
        finally:
            srv.shutdown()


def test_ollama_unreachable_gives_clear_error():
    from gaphopper.embed import EmbedError, Embedder

    e = Embedder("bge-m3", "ollama", "http://127.0.0.1:9", timeout=2)
    with pytest.raises(EmbedError):
        e.encode("x")
    assert not Embedder("").enabled


def test_onet_tasks_enrich(tmp_path):
    from gaphopper.onet import enrich

    st = tmp_path / "Task Statements.txt"
    st.write_text("O*NET-SOC Code\tTitle\tTask ID\tTask\tTask Type\n"
                  "29-1141.00\tRN\t1\tRecord vital signs.\tCore\n"
                  "29-1141.00\tRN\t2\tAdminister medications.\tCore\n"
                  "29-1141.00\tRN\t3\tEngage in nursing research.\tSupplemental\n", encoding="utf-8")
    rt = tmp_path / "Task Ratings.txt"
    rt.write_text("O*NET-SOC Code\tTask ID\tScale ID\tData Value\n"
                  "29-1141.00\t1\tIM\t3.9\n29-1141.00\t2\tIM\t4.6\n", encoding="utf-8")
    jobs = tmp_path / "jobs.jsonl"
    jobs.write_text(json.dumps({"job_id": "onet:29-1141.00", "title": "Registered nurse",
                                "codes": {"onet": "29-1141.00"}}) + "\n"
                    + json.dumps({"job_id": "onet:99-9999.00", "title": "X"}) + "\n", encoding="utf-8")
    res = enrich(jobs, st, rt, release="31.0")
    assert res["with_tasks"] == 1 and res["missing"] == ["99-9999.00"]
    r = json.loads(jobs.read_text(encoding="utf-8").splitlines()[0])
    assert r["tasks"] == ["Administer medications.", "Record vital signs."]  # fontosság szerint
    assert r["tasks_supplemental"] == ["Engage in nursing research."]
    assert "31.0" in r["tasks_source"]


# ---------------------------------------------------------------- olcsóbb futás: karcsú P2, kihagyás, egy P3-hívás
def _p1_obj():
    return P1Output.model_validate(p1_fixture())


def test_apply_p2_changes():
    from gaphopper.pipeline import apply_p2
    from gaphopper.schemas import P2Output

    p1 = _p1_obj()
    new_t = p1_fixture()["tesz"][0] | {"cand_id": "C02", "label_hu": "Új, hozzáadott tevékenység leírása itt"}
    out = P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "cand_id": "C01", "set": {"helyzet": "HELY_RUTIN"}},
        {"op": "DROP", "cand_id": "C03"},
        {"op": "MERGE", "cand_id": "C04", "into": "C05"},
        {"op": "SPLIT", "cand_id": "C06", "new": [p1_fixture()["tesz"][5] | {"cand_id": "C06a"},
                                                 p1_fixture()["tesz"][5] | {"cand_id": "C06b"}]},
        {"op": "ADD", "new": [new_t]}]})
    final, warn = apply_p2(p1.tesz, out.changes)
    ids = [t.cand_id for t in final]
    assert ids[0] == "C01" and final[0].helyzet == "HELY_RUTIN"
    assert "C03" not in ids and "C04" not in ids and "C06" not in ids
    assert ids[ids.index("C05") + 1: ids.index("C05") + 3] == ["C06a", "C06b"]
    assert len(ids) == 14 - 3 + 2 + 1 and len(set(ids)) == len(ids)  # az ütköző C02 új azonosítót kap
    with pytest.raises(ValueError):
        apply_p2(p1.tesz, P2Output.model_validate({"verdict": "FIXED", "changes": [
            {"op": "FIX", "cand_id": "C99", "set": {}}]}).changes)
    with pytest.raises(ValueError):  # érvénytelen kód a javításban
        apply_p2(p1.tesz, P2Output.model_validate({"verdict": "FIXED", "changes": [
            {"op": "FIX", "cand_id": "C01", "set": {"kozeg": "KOZ_NINCS"}}]}).changes)


def test_p2_diff_error_triggers_retry(tmp_path):
    from gaphopper.pipeline import Context, process_job

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.p2_mode = "always"
    answers = iter(['{"verdict": "FIXED", "changes": [{"op": "DROP", "cand_id": "C77"}]}',
                    '{"verdict": "FIXED", "changes": [{"op": "DROP", "cand_id": "C14"}]}'])
    prompts = []
    p1_text = (FIX / "p1_nurse.json").read_text(encoding="utf-8")
    ctx = Context(s, with_graph=False, llm_override={
        "P1": FakeLLM(lambda sy, u: p1_text),
        "P2": FakeLLM(lambda sy, u: prompts.append(u) or next(answers))})
    rec = json.loads((Path(__file__).parents[1] / "data" / "sample_jobs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    ctx.store.upsert_job(rec)
    process_job(ctx, rec["job_id"], until="p2")
    assert "C77" in prompts[1]  # a hibaüzenet a második kérésben
    assert len(ctx.store.artifact(rec["job_id"], "P2")["final_tesz"]) == 13


def test_p2_skipped_when_rules_clean(tmp_path):
    from gaphopper.pipeline import Context, process_job

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.p2_mode, s.p2_sample = "auto", 0.0
    p1_text = (FIX / "p1_nurse.json").read_text(encoding="utf-8")

    def no_p2(sy, u):
        raise AssertionError("A P2-nek nem kellett volna futnia.")

    ctx = Context(s, with_graph=False, llm_override={"P1": FakeLLM(lambda sy, u: p1_text), "P2": FakeLLM(no_p2)})
    rec = json.loads((Path(__file__).parents[1] / "data" / "sample_jobs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    ctx.store.upsert_job(rec)
    assert process_job(ctx, rec["job_id"], until="p2") == "P2_ELFOGADVA"
    art = ctx.store.artifact(rec["job_id"], "P2")
    assert art["verdict"] == "SKIPPED" and len(art["final_tesz"]) == 14
    # szabályjelzésnél a P2 fut: rontsuk el a P1-et (minden EXECUTE -> R5)
    bad = p1_fixture()
    for t in bad["tesz"]:
        t["tesz_type"] = "EXECUTE"
    ctx2 = Context(s, with_graph=False, llm_override={"P1": FakeLLM(lambda sy, u: json.dumps(bad))})
    rec2 = rec | {"job_id": "x:2"}
    ctx2.store.upsert_job(rec2)
    assert process_job(ctx2, "x:2", until="p1") == "P1_KESZ"


def test_p2_sample_is_deterministic():
    from gaphopper.pipeline import _sampled

    ids = [f"onet:{i}" for i in range(1000)]
    share = sum(_sampled(i, 0.15) for i in ids) / len(ids)
    assert 0.10 < share < 0.20 and [_sampled(i, 0.15) for i in ids[:50]] == [_sampled(i, 0.15) for i in ids[:50]]


class FakeGraph:
    """A Graph felület annyira, amennyit az egyesítés használ: minden keresés talál jelöltet."""

    def __init__(self):
        self.nodes, self.links = {}, []

    def find_by_alias(self, et, key):
        return None

    def fulltext_candidates(self, et, text, k=8):
        return [{"props": {"id": f"old_{et}", "label_hu": "régi elem", "kind": "METHOD"}, "score": 3.0}]

    def vector_candidates(self, et, vec, k=8):
        return self.fulltext_candidates(et, "")

    def tesz_context(self, tid):
        return {"tesz_type": "VERIFY", "primary_szer_id": "x", "primary_szer_label": "x", "helyzet": "HELY_RUTIN"}

    def add_aliases(self, *a):
        pass

    def set_embedding(self, *a):
        pass

    def upsert_job(self, jid, props):
        self.nodes[jid] = props

    def upsert_szer(self, sid, props):
        self.nodes[sid] = props

    def link_szer_broader(self, a, b):
        self.links.append(("FAJTAJA", a, b))

    def upsert_tesz(self, tid, props, *a, **kw):
        self.nodes[tid] = props

    def link_job_tesz(self, j, t, props):
        self.links.append(("TARTALMAZ", j, t))

    def clear_job_links(self, j):
        pass


def test_write_graph_single_p3_call(tmp_path):
    from gaphopper.pipeline import Context, write_graph

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.embed_model = ""
    calls = []

    def p3(system, user):
        assert system.startswith("Te a Gaphopper kompetencia-ontológia egyesítője")  # rövid saját rendszerprompt
        calls.append(user)
        cases = json.loads(user.split("ESETEK:\n", 1)[1].split("\n\nKIMENET", 1)[0])
        # az első SZER-eset legyen azonos a régivel, minden más új
        return json.dumps({"decisions": [
            {"case_id": c["case_id"], "decision": "SAME" if c["case_id"] == "S1" else "NEW",
             "target_id": "old_SZER_ELEM" if c["case_id"] == "S1" else None, "confidence": 0.9}
            for c in cases]})

    ctx = Context(s, with_graph=False, llm_override={"P3": FakeLLM(p3)})
    ctx.graph = FakeGraph()
    p1 = _p1_obj()
    job = {"job_id": "onet:29-1141.00", "title": "Registered nurse"}
    ctx.store.upsert_job(job)
    summary = write_graph(ctx, job, p1.model_dump(), p1.tesz)
    assert len(calls) == 1 and summary["p3_cases"] > 14
    assert len(summary["tesz"]) == 14 and all(t["via"] == "llm" for t in summary["tesz"])
    assert ctx.store.status(job["job_id"]) == "GRAFBAN"


def test_anthropic_extra_effort_and_thinking():
    from gaphopper.config import StepModel
    from gaphopper.llm import anthropic_extra

    assert anthropic_extra(StepModel("anthropic", "claude-sonnet-5-5", None, 1, "low", "off")) == {
        "output_config": {"effort": "low"}, "thinking": {"type": "between_tools"}}
    assert anthropic_extra(StepModel("anthropic", "claude-haiku-5-5", None, 1, "", "off")) == {
        "thinking": {"type": "disabled"}}
    assert anthropic_extra(StepModel("anthropic", "claude-opus-5-5", None, 1, "low", "off")) == {
        "output_config": {"effort": "low"}}


def _fake_openai():
    import http.server

    seen = []

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_GET(self):
            body = json.dumps({"data": [{"id": "qwen3.8-27b"}]}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)

        def do_POST(self):
            req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.append(req)
            content = '<think>gondolkodom</think>{"verdict": "ACCEPT", "changes": []}'
            body = json.dumps({"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                               "usage": {"prompt_tokens": 10, "completion_tokens": 5}}).encode()
            self.send_response(200); self.end_headers(); self.wfile.write(body)

    return H, seen


def test_local_openai_compatible_llm():
    import http.server
    import threading

    from gaphopper.config import StepModel
    from gaphopper.llm import LocalLLM
    from gaphopper.schemas import P2Output

    H, seen = _fake_openai()
    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{srv.server_port}/v1"
        llm = LocalLLM(StepModel("local", "qwen3.8-27b", None, 2000, "", "off"), base, json_mode="schema")
        obj, _ = call_json(llm, "sys", "user", P2Output)
        assert obj.verdict == "ACCEPT"
        req = seen[0]
        assert req["model"] == "qwen3.8-27b" and req["chat_template_kwargs"] == {"enable_thinking": False}
        assert req["response_format"]["type"] == "json_schema" and req["messages"][0]["role"] == "system"
        assert llm.models() == ["qwen3.8-27b"]
    finally:
        srv.shutdown()


def test_models_yaml_overrides_env(tmp_path, monkeypatch):
    from gaphopper import config

    monkeypatch.setattr(config, "MODELS_PATH", tmp_path / "models.yaml")
    monkeypatch.setenv("GH_PROVIDER_P3", "anthropic")
    monkeypatch.setenv("GH_MODEL_P3", "claude-haiku-5-5")
    assert config.Settings().p3.model == "claude-haiku-5-5"
    config.save_ui_models({"steps": {"P3": {"provider": "local", "model": "qwen3.8-27b", "thinking": "off"}},
                           "local": {"base_url": "http://spark:8000/v1"}, "p2": {"mode": "never"}})
    s = config.Settings()
    assert (s.p3.provider, s.p3.model, s.p3.thinking) == ("local", "qwen3.8-27b", "off")
    assert s.local_base_url == "http://spark:8000/v1" and s.p2_mode == "never"


def test_web_api_phases_and_files(tmp_path, monkeypatch):
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from gaphopper import web
    from gaphopper.store import Store

    monkeypatch.setenv("GH_STATE_DB", str(tmp_path / "state.sqlite"))
    monkeypatch.setattr(web, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(web, "UPLOAD_DIR", tmp_path / "uploads")
    monkeypatch.setattr(web.tasks, "TASK_DIR", tmp_path / "tasks")

    class NoGraph:
        err = "nem fut"

        def read(self, *a, **kw):
            return None
    monkeypatch.setattr(web, "GRAPH", NoGraph())
    S = Store(tmp_path / "state.sqlite")
    S.upsert_job({"job_id": "a", "title": "Ápoló"})
    S.upsert_job({"job_id": "b", "title": "Villanyszerelő", "doc_type": "JOB_AD", "source": "x.pdf", "source_quote": ""})
    S.save_artifact("b", "P1", json.loads((FIX / "p1_nurse.json").read_text(encoding="utf-8")), "v")
    S.save_artifact("b", "P2", {"verdict": "SKIPPED", "p2_reason": "szabályok rendben", "final_tesz": []}, "v")
    S.set_status("b", "P2_ELFOGADVA")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, method="GET", data=None, raw=None):
        body = raw if raw is not None else (json.dumps(data).encode() if data is not None else None)
        req = urllib.request.Request(base + path, data=body, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        _, d = call("/api/jobs")
        by = {j["job_id"]: j for j in d["jobs"]}
        assert [p["state"] for p in by["a"]["phases"]] == ["na", "wait", "wait", "wait", "unknown"]
        assert [p["state"] for p in by["b"]["phases"]][:4] == ["done", "done", "skip", "wait"]
        assert by["b"]["db"]["state"] == "unknown" and d["graph_ok"] is False
        _, det = call("/api/jobs/b")
        assert det["p2"]["verdict"] == "SKIPPED" and det["graph_ok"] is False
        assert call("/api/jobs/nincs")[0] == 404
        _, m = call("/api/models")
        assert [s["step"] for s in m["steps"]] == ["E0", "P1", "P2", "P3", "P4", "P5"]
        _, pr = call("/api/prompts")
        assert pr["prompts"][0]["name"] == "p0_system" and any(p["name"] == "vocab" for p in pr["prompts"])
        assert call("/api/prompts/..%2Fconfig")[0] == 404
        # feltöltés: csak a fájlnév számít, a mappát nem lehet kijátszani
        rec = json.dumps({"job_id": "c", "title": "Hegesztő"}, ensure_ascii=False).encode()
        assert call("/api/files/rec/..%2F..%2Fuj.jsonl", "PUT", raw=rec)[1]["saved"] == "uj.jsonl"
        assert (tmp_path / "data" / "uj.jsonl").exists()
        assert call("/api/files/rec/rossz.exe", "PUT", raw=b"x")[0] == 400
        _, pv = call("/api/files/rec/uj.jsonl")
        assert (pv["count"], pv["new"]) == (1, 1)
        assert call("/api/import", "POST", {"file": "uj.jsonl"})[1]["new"] == 1
        assert call("/api/run", "POST", {"jobs": [], "until": "xx"})[0] == 400
        with urllib.request.urlopen(base + "/") as r:
            assert b"app.js" in r.read()
    finally:
        srv.shutdown()
