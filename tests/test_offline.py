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
    assert len(p1.tesz_magok) == 11 and len(p1.tesz) == 14
    flags = rules.check(p1.tesz, [k.kor_id for k in p1.tesz_korok], "Health and care", magok=p1.tesz_magok)
    assert [f.rule for f in flags] == [], flags
    # a változat örökli a MAG mezőit, az ALAP-SZÍN a MAG címkéjét is
    c05, c05b = (next(t for t in p1.tesz if t.cand_id == c) for c in ("C05", "C05b"))
    assert c05.tesz_type == c05b.tesz_type == "EXECUTE" and c05.primary_szer == c05b.primary_szer
    # G3: a változat címkéje mindig a MAG-é, a kontextus és a KÉSZ-ség a MAG-on
    assert c05.label_hu == c05b.label_hu == p1.tesz_magok[4].label_hu
    assert c05b.kontextus_hu == "osztályos gyógyszerelés" and p1.tesz_magok[4].kesz_ids == ["gh_c_28", "gh_c_29", "gh_c_139"]


def test_schema_mag_rules():
    d = p1_fixture()
    d["tesz"][0]["mag_id"] = "M99"
    with pytest.raises(Exception, match="ismeretlen mag_id"):
        P1Output.model_validate(d)
    d = p1_fixture()
    next(t for t in d["tesz"] if t["cand_id"] == "C05b")["alap_szin"] = True
    with pytest.raises(Exception, match="R15"):
        P1Output.model_validate(d)
    d = p1_fixture()
    d["tesz_magok"][8]["melyites_jeloltek"] = [{"kozeg": "KOZ_FIZIKAI", "helyzet": "HELY_KRITIKUS"}]  # SUPPORTING
    with pytest.raises(Exception, match="CORE"):
        P1Output.model_validate(d)
    d = p1_fixture()
    d["tesz"] = [t for t in d["tesz"] if t["mag_id"] != "M11"]
    with pytest.raises(Exception, match="nincs változata"):
        P1Output.model_validate(d)


def test_same_szin_on_one_mag_is_one_tesz():
    """Egy MAG-on a SZÍN a változat kiléte: az azonos SZÍN-ű változat determinisztikusan összeolvad."""
    d = p1_fixture()
    d["tesz"].append({"cand_id": "C05x", "mag_id": "M05", "alap_szin": False, "kozeg": "KOZ_FIZIKAI",
                      "helyzet": "HELY_RUTIN", "label_hu": "Gyógyszer beadása angol nyelvű betegnek",
                      "szin_kulonbseg_hu": "Idegen nyelvű kommunikáció a beadás közben.", "expected_eqf": 4})
    p1 = P1Output.model_validate(d)
    assert "C05x" not in [t.cand_id for t in p1.tesz] and len(p1.tesz) == 14
    assert p1.link_warnings and "C05x" in p1.link_warnings[0]
    flags = rules.check(p1.tesz, [k.kor_id for k in p1.tesz_korok], "Health", magok=p1.tesz_magok)
    assert "R8" not in {f.rule for f in flags}


def test_legacy_p1_without_magok_still_loads():
    """Régi futás (MAG nélküli TESZ-ek): egyelemű MAG-ok készülnek, a szabályok futnak."""
    d = p1_fixture()
    mags = {m["mag_id"]: m for m in d.pop("tesz_magok")}
    old = []
    for t in d["tesz"]:
        if not t["alap_szin"]:
            continue
        m = mags[t.pop("mag_id")]
        t.pop("alap_szin")
        old.append(t | {k: v for k, v in m.items() if k not in ("mag_id", "melyites_jeloltek")})
    d["tesz"] = old
    p1 = P1Output.model_validate(d)
    assert len(p1.tesz_magok) == 11 and all(t.mag_id.startswith("M_") and t.alap_szin for t in p1.tesz)
    assert rules.check(p1.tesz, None, "") is not None


def test_schema_rejects_unknown_codes():
    d = p1_fixture()
    d["tesz"][0]["helyzet"] = "HELY_NEMLETEZO"
    with pytest.raises(Exception):
        P1Output.model_validate(d)


def test_rules_catch_bad_sets():
    d = p1_fixture()
    for m in d["tesz_magok"]:
        m["tesz_type"] = "EXECUTE"
    d["tesz_magok"][0]["label_hu"] = "Install, test"
    d["tesz_magok"][1]["label_hu"] = "Apply Welding processes in professional work"
    d["tesz"][2]["confidence"] = 0.3
    p1 = P1Output.model_validate(d)
    got = {f.rule for f in rules.check(p1.tesz, [k.kor_id for k in p1.tesz_korok], "", magok=p1.tesz_magok)}
    assert {"R4", "R5", "R10"} <= got
    assert rules.needs_expert(rules.check(p1.tesz, magok=p1.tesz_magok))


def _flags(d, field=""):
    p1 = P1Output.model_validate(d)
    return {f.rule: f for f in rules.check(p1.tesz, [k.kor_id for k in p1.tesz_korok], field, magok=p1.tesz_magok)}


def test_rules_mag_szin():
    d = p1_fixture()
    # R11: két MAG azonos ⟨típus, meghatározó SZER-ELEM⟩ párral
    d["tesz_magok"][10]["tesz_type"] = d["tesz_magok"][9]["tesz_type"]
    d["tesz_magok"][10]["primary_szer"] = d["tesz_magok"][9]["primary_szer"]
    # R14: SZÍN-jelző a MAG címkéjében
    d["tesz_magok"][0]["label_hu"] = "Sürgősségi állapotfelmérés felmérőlappal"
    # R13: nem ALAP-SZÍN változat SZÍN-különbség nélkül
    next(t for t in d["tesz"] if t["cand_id"] == "C05b")["szin_kulonbseg_hu"] = "nehezebb"
    # R16: csak KÖZ-egben eltérő változat, azonos HELY-zettel és kísérő SZER-ELEM-ekkel
    c12 = next(t for t in d["tesz"] if t["cand_id"] == "C10")
    d["tesz"].append(c12 | {"cand_id": "C12b", "alap_szin": False, "kozeg": "KOZ_SZERVEZETI",
                            "label_hu": "Ápolási dokumentáció vezetése papíron", "szin_kulonbseg_hu":
                            "Papíralapú vezetés, rendszerleállás idején, utólagos rögzítéssel."})
    # R12: négy változat egy MAG-on
    for i, h in enumerate(["HELY_PROBLEMA", "HELY_KRITIKUS", "HELY_TANULASI"]):
        d["tesz"].append({"cand_id": f"C06{i}", "mag_id": "M06", "alap_szin": False, "kozeg": "KOZ_FIZIKAI",
                          "helyzet": h, "label_hu": f"Seb ellátása {h}", "szin_kulonbseg_hu":
                          "Szigorúbb értékelési feltétel ebben a helyzetben.", "expected_eqf": 5})
    f = _flags(d, "Health")
    assert set(f["R11"].cand_ids) == {"M10", "M11"}
    assert f["R14"].cand_ids == ["M01"] and f["R13"].cand_ids == ["C05b"]
    assert f["R16"].cand_ids == ["C12b"] and f["R12"].cand_ids == ["M06"]


def test_rules_r1_r6_r7_on_right_level():
    d = p1_fixture()
    # 9 MAG, 12 változat: MAG-szám alatta, változatszám rendben
    keep = {f"M{i:02d}" for i in range(1, 6)}
    d["tesz_magok"] = [m for m in d["tesz_magok"] if m["mag_id"] in keep]
    d["tesz"] = [t for t in d["tesz"] if t["mag_id"] in keep]
    p1 = P1Output.model_validate(d)
    r1 = [f.message for f in rules.check(p1.tesz, None, "", magok=p1.tesz_magok) if f.rule == "R1"]
    assert any("TESZ-MAG" in m for m in r1) and len(p1.tesz) == 7
    # R6 csak az ALAP-SZÍN-ekre: a nem ALAP-SZÍN kritikus változat nem számít bele
    d = p1_fixture()
    for m in d["tesz_magok"]:
        m["tesz_type"] = "COMPLY"
        m["primary_szer"] = m["primary_szer"] | {"label_hu": m["label_hu"]}  # ne legyen R11
    for t in d["tesz"]:
        if t["alap_szin"]:
            t["helyzet"] = "HELY_RUTIN"
    f = _flags(d, "Health")
    assert "R6" not in f
    # R7 a változatokra: sok kritikus változat
    d = p1_fixture()
    for t in d["tesz"]:
        if not t["alap_szin"]:
            t["helyzet"] = "HELY_KRITIKUS"
    for t in d["tesz"][:5]:
        t["helyzet"] = "HELY_KRITIKUS"
    assert "R7" in _flags(d, "Construction")


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
    fx = p1_fixture()
    c06 = next(t for t in fx["tesz"] if t["cand_id"] == "C06")
    new_t = {"cand_id": "C02", "mag_id": "M01", "alap_szin": False, "kozeg": "KOZ_DIGITALIS",
             "helyzet": "HELY_PROBLEMA", "label_hu": "Új, hozzáadott változat leírása itt",
             "szin_kulonbseg_hu": "Hiányos adatokból kell következtetni.", "expected_eqf": 5}
    out = P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "cand_id": "C01", "set": {"helyzet": "HELY_RUTIN"}},
        {"op": "FIX", "mag_id": "M04", "set": {"label_hu": "Ápolási terv készítése a felmérés alapján",
                                               "runner_up": "DECIDE"}},
        {"op": "DROP", "cand_id": "C03"},
        {"op": "MERGE", "cand_id": "C04", "into": "C05"},
        {"op": "SPLIT", "cand_id": "C06", "new": [c06 | {"cand_id": "C06a"},
                                                 c06 | {"cand_id": "C06b", "alap_szin": False,
                                                        "helyzet": "HELY_PROBLEMA"}]},
        {"op": "ADD", "new": [new_t]},
        {"op": "FOLD", "cand_id": "C02b", "into": "C02", "as": "helyzet_secondary"},
        {"op": "REMAG", "cand_ids": ["C10"], "mag_id": "M11"},
        {"op": "ADD", "new_mag": [fx["tesz_magok"][7] | {"mag_id": "M21", "label_hu": "Beteg ágyba fektetése"}],
         "new": [{"cand_id": "C40", "mag_id": "M21", "kozeg": "KOZ_FIZIKAI", "helyzet": "HELY_RUTIN",
                  "expected_eqf": 3}]},
        {"op": "DROP", "mag_id": "M09"}]})
    magok, final, warn = apply_p2(p1.tesz_magok, p1.tesz, out.changes)
    ids = [t.cand_id for t in final]
    by = {t.cand_id: t for t in final}
    mids = [m.mag_id for m in magok]
    assert ids[0] == "C01" and final[0].helyzet == "HELY_RUTIN"
    assert "C03" not in ids and "C04" not in ids and "C06" not in ids and "C02b" not in ids and "C09" not in ids
    assert "M03" not in mids and "M04" not in mids and "M09" not in mids and "M10" not in mids  # kiürült MAG-ok
    assert ids[ids.index("C05b") + 1: ids.index("C05b") + 3] == ["C06a", "C06b"]
    assert by["C02"].helyzet_secondary == "HELY_VALTOZO"                   # FOLD
    assert by["C10"].mag_id == "M11" and not by["C10"].alap_szin            # REMAG
    assert by["C10"].tesz_type == "COORDINATE" and by["C10"].primary_szer.label_hu == "Feladatkiosztási terv"
    assert by["C10"].label_hu == "Napi feladatok kiosztása és felügyelete"   # G3: a MAG címkéje
    assert by["C40"].mag_id == "M21" and by["C40"].alap_szin and by["C40"].label_hu == "Beteg ágyba fektetése"
    new_c = next(t for t in final if (t.szin_kulonbseg_hu or "").startswith("Hiányos adatokból"))
    assert new_c.cand_id != "C02" and new_c.mag_id == "M01"                 # az ütköző C02 új azonosítót kap
    assert len(set(ids)) == len(ids)
    with pytest.raises(ValueError):
        apply_p2(p1.tesz_magok, p1.tesz, P2Output.model_validate({"verdict": "FIXED", "changes": [
            {"op": "FIX", "cand_id": "C99", "set": {}}]}).changes)
    with pytest.raises(ValueError):  # érvénytelen kód a javításban
        apply_p2(p1.tesz_magok, p1.tesz, P2Output.model_validate({"verdict": "FIXED", "changes": [
            {"op": "FIX", "cand_id": "C01", "set": {"kozeg": "KOZ_NINCS"}}]}).changes)
    with pytest.raises(ValueError):  # változatmező MAG-szintű FIX-ben
        apply_p2(p1.tesz_magok, p1.tesz, P2Output.model_validate({"verdict": "FIXED", "changes": [
            {"op": "FIX", "mag_id": "M01", "set": {"kozeg": "KOZ_FIZIKAI"}}]}).changes)
    # változaton javított MAG-mező: a MAG-ra kerül, minden változatára érvényes
    magok, final, warn = apply_p2(p1.tesz_magok, p1.tesz, P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "cand_id": "C05b", "set": {"tesz_type": "COMPLY"}}]}).changes)
    assert {t.tesz_type for t in final if t.mag_id == "M05"} == {"COMPLY"} and warn
    # R15 javítás: ha a MAG ALAP-SZÍN-e kiesik, a megmaradó változat lesz az
    magok, final, warn = apply_p2(p1.tesz_magok, p1.tesz, P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "DROP", "cand_id": "C05"}]}).changes)
    assert next(t for t in final if t.cand_id == "C05b").alap_szin and any("R15" in w for w in warn)


def test_p2_diff_error_triggers_retry(tmp_path):
    from gaphopper.pipeline import Context, process_job

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.p2_mode = "always"
    answers = iter(['{"verdict": "FIXED", "changes": [{"op": "DROP", "cand_id": "C77"}]}',
                    '{"verdict": "FIXED", "changes": [{"op": "DROP", "cand_id": "C11"}]}'])
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
    assert art["verdict"] == "SKIPPED" and len(art["final_tesz"]) == 14 and len(art["final_magok"]) == 11
    # szabályjelzésnél a P2 fut: rontsuk el a P1-et (minden EXECUTE -> R5)
    bad = p1_fixture()
    for m in bad["tesz_magok"]:
        m["tesz_type"] = "EXECUTE"
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
        self.plans = []

    def find_by_alias(self, et, key):
        return None

    def find_by_aliases(self, et, keys):
        return {}

    def fulltext_candidates(self, et, text, k=8):
        return [{"props": {"id": f"old_{et}", "label_hu": "régi elem", "kind": "METHOD"}, "score": 3.0}]

    def vector_candidates(self, et, vec, k=8):
        return self.fulltext_candidates(et, "")

    def mag_context(self, mid):
        return {"tesz_type": "VERIFY", "primary_szer_id": "x", "primary_szer_label": "x"}

    def existing_ids(self, et, ids):
        return set()

    def write_plan(self, plan):
        self.plans.append(plan)
        return {"tesz": len(plan.tesz)}


def _p3_same_first(calls):
    def p3(system, user):
        assert system.startswith("Te a Gaphopper kompetencia-ontológia egyesítője")  # rövid saját rendszerprompt
        calls.append(user)
        cases = json.loads(user.split("ESETEK:\n", 1)[1].split("\n\nKIMENET", 1)[0])
        # az első SZER-eset és az első MAG-eset legyen azonos a régivel, minden más új
        same = {"S1": "old_SZER_ELEM", "M1": "old_TESZ_MAG"}
        return json.dumps({"decisions": [
            {"case_id": c["case_id"], "decision": "SAME" if c["case_id"] in same else "NEW",
             "target_id": same.get(c["case_id"]), "confidence": 0.9} for c in cases]})
    return p3


def test_write_graph_single_p3_call(tmp_path):
    from gaphopper.pipeline import Context, write_graph

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.embed_model = ""
    calls = []
    ctx = Context(s, with_graph=False, llm_override={"P3": FakeLLM(_p3_same_first(calls))})
    ctx.graph = FakeGraph()
    p1 = _p1_obj()
    job = {"job_id": "onet:29-1141.00", "title": "Registered nurse"}
    ctx.store.upsert_job(job)
    summary = write_graph(ctx, job, p1.model_dump(), p1.tesz_magok, p1.tesz)
    assert len(calls) == 1 and summary["p3_cases"] >= 14
    cases = json.loads(calls[0].split("ESETEK:\n", 1)[1].split("\n\nKIMENET", 1)[0])
    assert {c["type"] for c in cases} == {"SZER_ELEM", "TESZ_MAG"}  # a változatok nem mennek a P3-ba
    assert all("helyzet" not in c["candidate"] and "kozeg" not in c["candidate"] for c in cases)
    plan = ctx.graph.plans[0]
    m05 = next(m for m in summary["magok"] if m["mag_id"] == "M05")
    # a C05 és a C05b ugyanazon MAG két SZÍN-e: két TESZ-nivaló, egy MAG alatt
    t5 = [t for t in summary["tesz"] if t["mag_id"] == "M05"]
    assert len(t5) == 2 and t5[0]["tesz_id"] != t5[1]["tesz_id"]
    assert {plan.tesz[t["tesz_id"]]["mag"] for t in t5} == {m05["graph_id"]}
    v = plan.tesz[t5[1]["tesz_id"]]
    assert (v["kozeg"][0], v["helyzet"][0]) == ("KOZ_FIZIKAI", "HELY_VALTOZO")
    # a TESZ-nivaló csomópont karcsú: címke és ALAP-jelölés nélkül (az ALAP-SZÍN a munkakör élén van)
    assert "label_hu" not in v["props"] and "alap_szin" not in v["props"]
    assert {l["props"]["alap_szin"] for l in plan.links if l["t"] in {t["tesz_id"] for t in t5}} == {True, False}
    # az LLM SAME-je a MAG-ra csak azonos TESZ-típusnál és meghatározó SZER-ELEM-nél áll meg (M1-őr):
    # a FakeGraph törzseleme VERIFY / 'x', a jelölt nem az -> új MAG, szakértői átnézésre
    assert summary["magok"][0]["decision"] == "NEW" and summary["magok"][0]["via"] == "llm-elutasitva"
    assert any(r["kind"] == "mag" for r in summary["review"])
    assert len(plan.links) == 14 and len(summary["magok"]) == 11
    assert plan.magok[m05["graph_id"]]["melyites"][0]["helyzet"] == "HELY_KRITIKUS"
    assert ctx.store.status(job["job_id"]) == "GRAFBAN"
    # a tesz_id a SZÍN-ből képzett: ugyanaz a MAG és SZÍN ugyanazt az id-t adja
    from gaphopper.merge import Resolver
    c05 = next(t for t in p1.tesz if t.cand_id == "C05")
    assert Resolver.tesz_id("g1", c05) == Resolver.tesz_id("g1", c05.model_copy(update={"label_hu": "más"}))
    assert Resolver.tesz_id("g1", c05) != Resolver.tesz_id("g1", c05.model_copy(update={"kozeg": "KOZ_TARSAS"}))


def test_mag_same_accepted_when_type_and_szer_match(tmp_path):
    from gaphopper.merge import Resolver
    from gaphopper.pipeline import Context

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.embed_model = ""
    ctx = Context(s, with_graph=False)
    ctx.graph = FakeGraph()
    res = Resolver(ctx)
    m = _p1_obj().tesz_magok[0].model_copy(update={"tesz_type": "VERIFY"})
    res.decisions["M1"] = __import__("gaphopper.schemas", fromlist=["P3Decision"]).P3Decision(
        case_id="M1", decision="SAME", target_id="old_TESZ_MAG", confidence=0.9)
    assert res.mag(m, "M1", "x").decision == "SAME"
    assert res.mag(m, "M1", "y").decision == "NEW"


def test_dry_run_writes_nothing(tmp_path):
    """Próbafutás: LLM-lépések lefutnak, a gráf csak olvasott, a state.sqlite nem jön létre."""
    from gaphopper.pipeline import dry_run, format_dry_run

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.embed_model = ""
    s.p2_mode = "never"
    p1_text = (FIX / "p1_nurse.json").read_text(encoding="utf-8")
    calls = []
    graph = FakeGraph()
    rec = json.loads((Path(__file__).parents[1] / "data" / "sample_jobs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    out = dry_run(rec, s, graph, llm_override={"P1": FakeLLM(lambda sy, u: p1_text),
                                               "P3": FakeLLM(_p3_same_first(calls))}, out_dir=tmp_path / "dry")
    assert out["ok"], out.get("error")
    assert not s.state_db.exists() and graph.plans == [] and len(calls) == 1
    assert out["szamok"] == {"mag": 11, "tesz": 14, "core_mag": 8, "nem_alap": 3, "melyitesi_jelolt": 1,
                             "torzsbol": 0, "torzs_meret": 0}
    assert len(out["new_torzs"]) == 11 and out["new_torzs"][0]["kesz_ids"] == ["gh_c_3", "gh_c_270", "gh_c_42"]
    assert out["magok"][0]["egyesites"] == "NEW" and out["p3_cases"] >= 11
    assert [u["step"] for u in out["usage"]] == ["P1", "P3"]
    saved = json.loads((tmp_path / "dry" / "onet_29-1141.00.json").read_text(encoding="utf-8"))
    assert saved["szamok"]["tesz"] == 14
    text = format_dry_run(out)
    assert "PRÓBAFUTÁS" in text and "C05b" in text and "mélyítési jelölt" in text
    # gráf nélkül az egyesítés kimarad, a többi lefut
    out2 = dry_run(rec, s, None, llm_override={"P1": FakeLLM(lambda sy, u: p1_text)}, out_dir=tmp_path / "dry")
    assert out2["ok"] and "p3_cases" not in out2 and not s.state_db.exists()


def test_p2_view_is_grouped_and_slim():
    from gaphopper.pipeline import p2_view

    v = p2_view(_p1_obj())
    m05 = next(m for m in v["tesz_magok"] if m["mag_id"] == "M05")
    assert [x["cand_id"] for x in m05["v"]] == ["C05", "C05b"]
    assert "tesz_type" not in m05["v"][0] and "label_hu" not in m05["v"][0]   # nem ismétli a MAG-ot
    assert m05["v"][1]["szin_kulonbseg_hu"] and m05["v"][1]["alap_szin"] is False
    text = json.dumps(v, ensure_ascii=False)
    assert "rationale_hu" not in text and "label_en" not in text


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


def test_web_dry_run_toggle_and_results(tmp_path, monkeypatch):
    """A Futás oldal próbafutás-kapcsolója: run --dry-run indul, állapot nem változik; az eredmény olvasható."""
    from gaphopper import pipeline, web
    from gaphopper.store import Store

    monkeypatch.setenv("GH_STATE_DB", str(tmp_path / "state.sqlite"))
    monkeypatch.setattr(pipeline, "DRYRUN_DIR", tmp_path / "dryrun")
    started = []
    monkeypatch.setattr(web.tasks, "start", lambda label, args, targets=None: started.append((label, args)) or "t1")
    S = Store(tmp_path / "state.sqlite")
    S.upsert_job({"job_id": "onet:1", "title": "Ápoló"})
    assert web.start_run({"jobs": ["onet:1"], "dry_run": True, "until": "graph"}, S) == "t1"
    label, args = started[0]
    assert args[:2] == ["run", "--dry-run"] and "--until" not in args and args[-2:] == ["--job", "onet:1"]
    assert S.status("onet:1") == "NYERS"
    with pytest.raises(KeyError):
        web.start_run({"jobs": ["nincs"], "dry_run": True}, S)
    (tmp_path / "dryrun").mkdir()
    (tmp_path / "dryrun" / "onet_1.json").write_text(json.dumps(
        {"job_id": "onet:1", "title": "Ápoló", "ok": True, "szamok": {"mag": 12, "tesz": 16},
         "rule_flags": [{"rule": "R13", "message": "x"}], "total": {"input": 1, "output": 2}, "p2": {}}), encoding="utf-8")
    lst = web.dryrun_list()["runs"]
    assert lst[0]["job_id"] == "onet:1" and lst[0]["flags"] == ["R13"]
    assert web.dryrun_get("onet:1", brief=True)["szamok"]["mag"] == 12 and web.dryrun_get("x") is None


# ---------------------------------------------------------------- v2: általános MAG, KÉSZ-ség- és MAG-törzs
def _torzs_rec(mid="gh_mag_tanora", label="Tanóra vagy foglalkozás megtartása"):
    return {"id": mid, "label_hu": label, "label_en": "Deliver a lesson or session", "outcome_hu": "Megtartott óra.",
            "tesz_type": "SUPPORT_SERVE", "kesz_ids": ["gh_c_246", "gh_c_239"],
            "primary_szer": {"label_hu": "Foglalkozásterv", "label_en": "Session plan", "kind": "PLAN",
                             "role": "GUIDED_BY", "definition_hu": ""}}


def test_kesz_ids_normalized_and_checked():
    from gaphopper.config import kesz_torzs
    from gaphopper.schemas import TeszMag

    assert len(kesz_torzs()) == 275 and kesz_torzs()["gh_c_246"]["en"] == "Public speaking (technical)"
    d = p1_fixture()["tesz_magok"][0] | {"kesz_ids": [3, "270", "gh_c_42", 3]}
    assert TeszMag.model_validate(d).kesz_ids == ["gh_c_3", "gh_c_270", "gh_c_42"]
    with pytest.raises(Exception, match="nincs a KÉSZ-ség-törzsben"):
        TeszMag.model_validate(d | {"kesz_ids": [999]})
    with pytest.raises(Exception):
        TeszMag.model_validate(d | {"kesz_ids": [1, 2, 3, 4, 5, 6]})  # legfeljebb 5 (2026-11-04)
    assert len(TeszMag.model_validate(d | {"kesz_ids": [1, 2, 3, 4, 5]}).kesz_ids) == 5


def test_p1_system_has_torzs_and_two_cache_blocks(tmp_path):
    from gaphopper.llm import CACHE_SPLIT, plain_system, system_blocks
    from gaphopper.pipeline import Context

    ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False)
    ctx.extra_torzs = [_torzs_rec()]
    sys_ = ctx.p1_system()
    static, dynamic = sys_.split(CACHE_SPLIT)
    assert "246|Public speaking (technical)|S1S" in static and static.startswith(prompt("p0_system")[:40])
    assert dynamic.endswith("T01|Tanóra vagy foglalkozás megtartása|SUPPORT_SERVE")
    blocks = system_blocks(sys_)
    assert len(blocks) == 2 and all(b["cache_control"] == {"type": "ephemeral"} for b in blocks)
    assert CACHE_SPLIT not in plain_system(sys_)


def test_mag_ref_from_torzs_without_p3(tmp_path):
    """A törzsből választott MAG (mag_ref) mezői a törzsből jönnek; az egyesítés hívás nélkül feloldja."""
    from gaphopper.pipeline import Context, merge_plan

    s = Settings(state_db=tmp_path / "s.sqlite")
    s.embed_model = ""
    d = p1_fixture()
    d["tesz_magok"][8] = {"mag_id": "M09", "mag_ref": "T01", "kor_id": "K5", "relevance": "SUPPORTING",
                          "kontextus_hu": "betegek és hozzátartozók"}
    calls = []
    ctx = Context(s, with_graph=False, llm_override={"P1": FakeLLM(lambda sy, u: json.dumps(d, ensure_ascii=False)),
                                                     "P3": FakeLLM(_p3_same_first(calls))})
    ctx.extra_torzs = [_torzs_rec()]
    obj, _ = call_json(ctx.llm("P1"), ctx.p1_system(), "job", P1Output, pre=ctx.resolve_mag_refs)
    m9 = obj.tesz_magok[8]
    assert m9.mag_ref == "gh_mag_tanora" and m9.label_hu == "Tanóra vagy foglalkozás megtartása"
    assert m9.kesz_ids == ["gh_c_246", "gh_c_239"] and m9.tesz_type == "SUPPORT_SERVE"
    c09 = next(t for t in obj.tesz if t.mag_id == "M09")
    assert c09.label_hu == m9.label_hu and c09.kontextus_hu == "betegek és hozzátartozók"
    ctx.graph = FakeGraph()
    plan, summary, _ = merge_plan(ctx, {"job_id": "x", "title": "Ápoló"}, obj.model_dump(), obj.tesz_magok, obj.tesz)
    s9 = next(m for m in summary["magok"] if m["mag_id"] == "M09")
    assert (s9["decision"], s9["via"], s9["graph_id"]) == ("SAME", "torzs", "gh_mag_tanora")
    cases = json.loads(calls[0].split("ESETEK:\n", 1)[1].split("\n\nKIMENET", 1)[0])
    assert all(c["candidate"].get("label_hu") != m9.label_hu for c in cases if c["type"] == "TESZ_MAG")
    link = next(l for l in plan.links if l["props"]["mag_cand_id"] == "M09")
    assert link["props"]["kontextus_hu"] == "betegek és hozzátartozók"
    assert plan.magok["gh_mag_tanora"]["kesz_ids"] == ["gh_c_246", "gh_c_239"]
    # ismeretlen kód: sémahiba (újrapróbálás a hibaüzenettel)
    d["tesz_magok"][8]["mag_ref"] = "T99"
    with pytest.raises(LLMError, match="ismeretlen mag_ref"):
        call_json(FakeLLM(lambda *a: json.dumps(d)), "s", "u", P1Output, retries=0, pre=ctx.resolve_mag_refs)


def test_rules_v2_generality():
    d = p1_fixture()
    d["tesz_magok"][0]["label_hu"] = "Ápolói állapotfelmérés a hallgatók körében"   # R17: célcsoport + munkakör
    d["tesz_magok"][6]["kesz_ids"] = [28, 29]                                        # R18: M05 ~ M07? más típus
    d["tesz_magok"][5]["kesz_ids"] = [28, 29, 24]                                    # R18: M05 + M06 EXECUTE
    d["tesz_magok"][3]["kesz_ids"] = [113]                                           # R20
    p1 = P1Output.model_validate(d)
    f = {x.rule: x for x in rules.check(p1.tesz, None, "Health and care", magok=p1.tesz_magok,
                                         job_title="Ápoló")}
    assert f["R17"].cand_ids == ["M01"] and f["R20"].cand_ids == ["M04"]
    # az általános célcsoport maradhat, a szak szerinti bontás nem (felhasználói döntés, 2026-11-04)
    for label, flagged in (("Kutatómunka témavezetése hallgatóknak", False),
                           ("Kutatómunka témavezetése gépész hallgatóknak", True)):
        d2 = p1_fixture()
        d2["tesz_magok"][3]["label_hu"] = label
        p2 = P1Output.model_validate(d2)
        r17 = [x for x in rules.check(p2.tesz, None, "", magok=p2.tesz_magok, job_title="Egyetemi oktató")
               if x.rule == "R17"]
        assert ("M04" in (r17[0].cand_ids if r17 else [])) is flagged, label
    assert set(f["R18"].cand_ids) == {"M05", "M06"} and "M07" not in f["R18"].cand_ids
    assert "R19" not in f
    # R19: nagy törzs mellett túl sok új MAG
    f2 = {x.rule for x in rules.check(p1.tesz, None, "", magok=p1.tesz_magok, torzs_size=60)}
    assert "R19" in f2
    # R4: hosszú MAG-címke (G1: legfeljebb 6 szó)
    d = p1_fixture()
    d["tesz_magok"][1]["label_hu"] = "Életjelek mérése és rögzítése minden műszak elején és végén"
    assert "R4" in {x.rule for x in rules.check(P1Output.model_validate(d).tesz, None, "",
                                                 magok=P1Output.model_validate(d).tesz_magok)}


def test_r17_no_false_positives_from_english_titles():
    """TOP10-ben talált téves R17-jelzések: angol munkakörcím-szavak az angol címkében, 'engineering'."""
    d = p1_fixture()
    d["tesz_magok"][9]["label_en"] = "Record in a register"
    d["tesz_magok"][3]["label_en"] = "Develop and run tests"
    d["tesz_magok"][4]["label_en"] = "Perform engineering calculations"
    p1 = P1Output.model_validate(d)
    for title in ("Registered nurse", "Software developer", "Electrical engineer"):
        assert "R17" not in {x.rule for x in rules.check(p1.tesz, None, "", magok=p1.tesz_magok, job_title=title)}
    d["tesz_magok"][10]["label_en"] = "Direct nursing staff work"     # szakszó az angol címkén: jelez
    p1 = P1Output.model_validate(d)
    assert [x.cand_ids for x in rules.check(p1.tesz, None, "", magok=p1.tesz_magok) if x.rule == "R17"] == [["M11"]]


def test_p2_fix_melyites_as_text_is_dropped():
    from gaphopper.pipeline import apply_p2
    from gaphopper.schemas import P2Output

    p1 = _p1_obj()
    out = P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "mag_id": "M02", "set": {"melyites_jeloltek": "Kritikus leállás (HELY_KRITIKUS)",
                                               "kontextus_hu": "gyártósor"}}]})
    magok, _, warn = apply_p2(p1.tesz_magok, p1.tesz, out.changes)
    assert magok[1].kontextus_hu == "gyártósor" and any("melyites_jeloltek" in w for w in warn)


def test_p2_fix_identity_drops_mag_ref():
    from gaphopper.pipeline import apply_p2
    from gaphopper.schemas import P2Output

    p1 = _p1_obj()
    mags = [m.model_copy(update={"mag_ref": "gh_mag_x"}) if m.mag_id == "M04" else m for m in p1.tesz_magok]
    out = P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "mag_id": "M04", "set": {"label_hu": "Ellátási terv készítése és frissítése"}},
        {"op": "FIX", "mag_id": "M05", "set": {"kontextus_hu": "intenzív osztály", "kesz_ids": [28, 29]}}]})
    magok, final, _ = apply_p2(mags, p1.tesz, out.changes)
    by = {m.mag_id: m for m in magok}
    assert by["M04"].mag_ref is None and by["M05"].kesz_ids == ["gh_c_28", "gh_c_29"]
    assert next(t for t in final if t.cand_id == "C05b").kontextus_hu == "intenzív osztály"
