"""Offline tesztek: nem kell hozzájuk neo4j és API-kulcs.  python -m pytest -q"""
import json
import os
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from gaphopper import rules
from gaphopper.config import Settings, prompt, vocab
from gaphopper.llm import FakeLLM, LLMError, call_json, extract_json
from gaphopper.normalize import job_title_key, norm_key, stable_id
from gaphopper.schemas import P1Output, P2Output, TeszSzin

FIX = Path(__file__).parent / "fixtures"


def p1_fixture() -> dict:
    return json.loads((FIX / "p1_nurse.json").read_text(encoding="utf-8"))


def test_vocab_codes_in_p0():
    p0 = prompt("p0_system")
    v = vocab()
    for group in ("tesz_tipus", "kozeg", "helyzet", "szer_elem_fajta"):
        for code, x in v[group].items():
            if isinstance(x, dict) and x.get("csak_kesz"):
                continue  # KOZ_EGYEN: csak KÉSZ-ség-irány (a P1 törzsblokkjának fejlécében), TESZ-SZÍN-ben nem
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
    assert len(p1.nivalok) == 11 and len(p1.szinek) == 14
    flags = rules.check(p1.szinek, [k.kor_id for k in p1.tesz_korok], "Health and care", magok=p1.nivalok)
    assert [f.rule for f in flags] == [], flags
    # a változat örökli a MAG mezőit, az ALAP-SZÍN a MAG címkéjét is
    c05, c05b = (next(t for t in p1.szinek if t.cand_id == c) for c in ("C05", "C05b"))
    assert c05.tesz_type == c05b.tesz_type == "EXECUTE" and c05.primary_szer == c05b.primary_szer
    # G3: a változat címkéje mindig a MAG-é, a kontextus és a KÉSZ-ség a MAG-on
    assert c05.label_hu == c05b.label_hu == p1.nivalok[4].label_hu
    assert c05b.kontextus_hu == "osztályos gyógyszerelés" and p1.nivalok[4].kesz_ids == ["gh_c_28", "gh_c_29", "gh_c_139"]


def test_schema_mag_rules():
    d = p1_fixture()
    d["szinek"][0]["nivalo_id"] = "M99"
    with pytest.raises(Exception, match="ismeretlen nivalo_id"):
        P1Output.model_validate(d)
    d = p1_fixture()
    next(t for t in d["szinek"] if t["cand_id"] == "C05b")["alap_szin"] = True
    with pytest.raises(Exception, match="R15"):
        P1Output.model_validate(d)
    d = p1_fixture()
    d["nivalok"][8]["melyites_jeloltek"] = [{"kozeg": "KOZ_FIZIKAI", "helyzet": "HELY_KRITIKUS"}]  # SUPPORTING
    with pytest.raises(Exception, match="CORE"):
        P1Output.model_validate(d)
    d = p1_fixture()
    d["szinek"] = [t for t in d["szinek"] if t["nivalo_id"] != "M11"]
    with pytest.raises(Exception, match="nincs változata"):
        P1Output.model_validate(d)


def test_same_szin_on_one_mag_is_one_tesz():
    """Egy MAG-on a SZÍN a változat kiléte: az azonos SZÍN-ű változat determinisztikusan összeolvad."""
    d = p1_fixture()
    d["szinek"].append({"cand_id": "C05x", "nivalo_id": "M05", "alap_szin": False, "kozeg": "KOZ_FIZIKAI",
                      "helyzet": "HELY_RUTIN", "label_hu": "Gyógyszer beadása angol nyelvű betegnek",
                      "szin_kulonbseg_hu": "Idegen nyelvű kommunikáció a beadás közben.", "expected_eqf": 4})
    p1 = P1Output.model_validate(d)
    assert "C05x" not in [t.cand_id for t in p1.szinek] and len(p1.szinek) == 14
    assert p1.link_warnings and "C05x" in p1.link_warnings[0]
    flags = rules.check(p1.szinek, [k.kor_id for k in p1.tesz_korok], "Health", magok=p1.nivalok)
    assert "R8" not in {f.rule for f in flags}


def test_legacy_p1_without_magok_still_loads():
    """Régi futás (MAG nélküli TESZ-ek): egyelemű MAG-ok készülnek, a szabályok futnak."""
    d = p1_fixture()
    mags = {m["nivalo_id"]: m for m in d.pop("nivalok")}
    old = []
    for t in d["szinek"]:
        if not t["alap_szin"]:
            continue
        m = mags[t.pop("nivalo_id")]
        t.pop("alap_szin")
        old.append(t | {k: v for k, v in m.items() if k not in ("nivalo_id", "melyites_jeloltek")})
    d["szinek"] = old
    p1 = P1Output.model_validate(d)
    assert len(p1.nivalok) == 11 and all(t.nivalo_id.startswith("N_") and t.alap_szin for t in p1.szinek)
    assert rules.check(p1.szinek, None, "") is not None


def test_schema_rejects_unknown_codes():
    d = p1_fixture()
    d["szinek"][0]["helyzet"] = "HELY_NEMLETEZO"
    with pytest.raises(Exception):
        P1Output.model_validate(d)


def test_rules_catch_bad_sets():
    d = p1_fixture()
    for m in d["nivalok"]:
        m["tesz_type"] = "EXECUTE"
    d["nivalok"][0]["label_hu"] = "Install, test"
    d["nivalok"][1]["label_hu"] = "Apply Welding processes in professional work"
    d["szinek"][2]["confidence"] = 0.3
    p1 = P1Output.model_validate(d)
    got = {f.rule for f in rules.check(p1.szinek, [k.kor_id for k in p1.tesz_korok], "", magok=p1.nivalok)}
    assert {"R4", "R5", "R10"} <= got
    assert rules.needs_expert(rules.check(p1.szinek, magok=p1.nivalok))


def _flags(d, field=""):
    p1 = P1Output.model_validate(d)
    return {f.rule: f for f in rules.check(p1.szinek, [k.kor_id for k in p1.tesz_korok], field, magok=p1.nivalok)}


def test_rules_mag_szin():
    d = p1_fixture()
    # R11: két MAG azonos ⟨típus, meghatározó SZER-ELEM⟩ párral
    d["nivalok"][10]["tesz_type"] = d["nivalok"][9]["tesz_type"]
    d["nivalok"][10]["primary_szer"] = d["nivalok"][9]["primary_szer"]
    # R14: SZÍN-jelző a MAG címkéjében
    d["nivalok"][0]["label_hu"] = "Sürgősségi állapotfelmérés felmérőlappal"
    # R13: nem ALAP-SZÍN változat SZÍN-különbség nélkül
    next(t for t in d["szinek"] if t["cand_id"] == "C05b")["szin_kulonbseg_hu"] = "nehezebb"
    # R16: csak KÖZ-egben eltérő változat, azonos HELY-zettel és kísérő SZER-ELEM-ekkel
    c12 = next(t for t in d["szinek"] if t["cand_id"] == "C10")
    d["szinek"].append(c12 | {"cand_id": "C12b", "alap_szin": False, "kozeg": "KOZ_SZERVEZETI",
                            "label_hu": "Ápolási dokumentáció vezetése papíron", "szin_kulonbseg_hu":
                            "Papíralapú vezetés, rendszerleállás idején, utólagos rögzítéssel."})
    # R12: négy változat egy MAG-on
    for i, h in enumerate(["HELY_PROBLEMA", "HELY_KRITIKUS", "HELY_TANULASI"]):
        d["szinek"].append({"cand_id": f"C06{i}", "nivalo_id": "M06", "alap_szin": False, "kozeg": "KOZ_FIZIKAI",
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
    d["nivalok"] = [m for m in d["nivalok"] if m["nivalo_id"] in keep]
    d["szinek"] = [t for t in d["szinek"] if t["nivalo_id"] in keep]
    p1 = P1Output.model_validate(d)
    r1 = [f.message for f in rules.check(p1.szinek, None, "", magok=p1.nivalok) if f.rule == "R1"]
    assert any("TESZ-nivaló" in m for m in r1) and len(p1.szinek) == 7
    # R6 csak az ALAP-SZÍN-ekre: a nem ALAP-SZÍN kritikus változat nem számít bele
    d = p1_fixture()
    for m in d["nivalok"]:
        m["tesz_type"] = "COMPLY"
        m["primary_szer"] = m["primary_szer"] | {"label_hu": m["label_hu"]}  # ne legyen R11
    for t in d["szinek"]:
        if t["alap_szin"]:
            t["helyzet"] = "HELY_RUTIN"
    f = _flags(d, "Health")
    assert "R6" not in f
    # R7 a változatokra: sok kritikus változat
    d = p1_fixture()
    for t in d["szinek"]:
        if not t["alap_szin"]:
            t["helyzet"] = "HELY_KRITIKUS"
    for t in d["szinek"][:5]:
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
    c06 = next(t for t in fx["szinek"] if t["cand_id"] == "C06")
    new_t = {"cand_id": "C02", "nivalo_id": "M01", "alap_szin": False, "kozeg": "KOZ_DIGITALIS",
             "helyzet": "HELY_PROBLEMA", "label_hu": "Új, hozzáadott változat leírása itt",
             "szin_kulonbseg_hu": "Hiányos adatokból kell következtetni.", "expected_eqf": 5}
    out = P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "cand_id": "C01", "set": {"helyzet": "HELY_RUTIN"}},
        {"op": "FIX", "nivalo_id": "M04", "set": {"label_hu": "Ápolási terv készítése a felmérés alapján",
                                               "runner_up": "DECIDE"}},
        {"op": "DROP", "cand_id": "C03"},
        {"op": "MERGE", "cand_id": "C04", "into": "C05"},
        {"op": "SPLIT", "cand_id": "C06", "new": [c06 | {"cand_id": "C06a"},
                                                 c06 | {"cand_id": "C06b", "alap_szin": False,
                                                        "helyzet": "HELY_PROBLEMA"}]},
        {"op": "ADD", "new": [new_t]},
        {"op": "FOLD", "cand_id": "C02b", "into": "C02", "as": "helyzet_secondary"},
        {"op": "MOVE", "cand_ids": ["C10"], "nivalo_id": "M11"},
        {"op": "ADD", "new_nivalo": [fx["nivalok"][7] | {"nivalo_id": "M21", "label_hu": "Beteg ágyba fektetése"}],
         "new": [{"cand_id": "C40", "nivalo_id": "M21", "kozeg": "KOZ_FIZIKAI", "helyzet": "HELY_RUTIN",
                  "expected_eqf": 3}]},
        {"op": "DROP", "nivalo_id": "M09"}]})
    magok, final, warn = apply_p2(p1.nivalok, p1.szinek, out.changes)
    ids = [t.cand_id for t in final]
    by = {t.cand_id: t for t in final}
    mids = [m.nivalo_id for m in magok]
    assert ids[0] == "C01" and final[0].helyzet == "HELY_RUTIN"
    assert "C03" not in ids and "C04" not in ids and "C06" not in ids and "C02b" not in ids and "C09" not in ids
    assert "M03" not in mids and "M04" not in mids and "M09" not in mids and "M10" not in mids  # kiürült MAG-ok
    assert ids[ids.index("C05b") + 1: ids.index("C05b") + 3] == ["C06a", "C06b"]
    assert by["C02"].helyzet_secondary == "HELY_VALTOZO"                   # FOLD
    assert by["C10"].nivalo_id == "M11" and not by["C10"].alap_szin            # MOVE
    assert by["C10"].tesz_type == "COORDINATE" and by["C10"].primary_szer.label_hu == "Feladatkiosztási terv"
    assert by["C10"].label_hu == "Napi feladatok kiosztása és felügyelete"   # G3: a MAG címkéje
    assert by["C40"].nivalo_id == "M21" and by["C40"].alap_szin and by["C40"].label_hu == "Beteg ágyba fektetése"
    new_c = next(t for t in final if (t.szin_kulonbseg_hu or "").startswith("Hiányos adatokból"))
    assert new_c.cand_id != "C02" and new_c.nivalo_id == "M01"                 # az ütköző C02 új azonosítót kap
    assert len(set(ids)) == len(ids)
    with pytest.raises(ValueError):
        apply_p2(p1.nivalok, p1.szinek, P2Output.model_validate({"verdict": "FIXED", "changes": [
            {"op": "FIX", "cand_id": "C99", "set": {}}]}).changes)
    with pytest.raises(ValueError):  # érvénytelen kód a javításban
        apply_p2(p1.nivalok, p1.szinek, P2Output.model_validate({"verdict": "FIXED", "changes": [
            {"op": "FIX", "cand_id": "C01", "set": {"kozeg": "KOZ_NINCS"}}]}).changes)
    with pytest.raises(ValueError):  # változatmező MAG-szintű FIX-ben
        apply_p2(p1.nivalok, p1.szinek, P2Output.model_validate({"verdict": "FIXED", "changes": [
            {"op": "FIX", "nivalo_id": "M01", "set": {"kozeg": "KOZ_FIZIKAI"}}]}).changes)
    # változaton javított MAG-mező: a MAG-ra kerül, minden változatára érvényes
    magok, final, warn = apply_p2(p1.nivalok, p1.szinek, P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "cand_id": "C05b", "set": {"tesz_type": "COMPLY"}}]}).changes)
    assert {t.tesz_type for t in final if t.nivalo_id == "M05"} == {"COMPLY"} and warn
    # R15 javítás: ha a MAG ALAP-SZÍN-e kiesik, a megmaradó változat lesz az
    magok, final, warn = apply_p2(p1.nivalok, p1.szinek, P2Output.model_validate({"verdict": "FIXED", "changes": [
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
    assert len(ctx.store.artifact(rec["job_id"], "P2")["final_szinek"]) == 13


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
    assert art["verdict"] == "SKIPPED" and len(art["final_szinek"]) == 14 and len(art["final_nivalok"]) == 11
    # szabályjelzésnél a P2 fut: rontsuk el a P1-et (minden EXECUTE -> R5)
    bad = p1_fixture()
    for m in bad["nivalok"]:
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

    def nivalo_context(self, mid):
        return {"tesz_type": "VERIFY", "primary_szer_id": "x", "primary_szer_label": "x"}

    def existing_ids(self, et, ids):
        return set()

    def write_plan(self, plan):
        self.plans.append(plan)
        return {"szinek": len(plan.szinek)}


def _p3_same_first(calls):
    def p3(system, user):
        assert system.startswith("Te a Gaphopper kompetencia-ontológia egyesítője")  # rövid saját rendszerprompt
        calls.append(user)
        cases = json.loads(user.split("ESETEK:\n", 1)[1].split("\n\nKIMENET", 1)[0])
        # az első SZER-eset és az első MAG-eset legyen azonos a régivel, minden más új
        same = {"S1": "old_SZER_ELEM", "N1": "old_TESZ_NIVALO"}
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
    summary = write_graph(ctx, job, p1.model_dump(), p1.nivalok, p1.szinek)
    assert len(calls) == 1 and summary["p3_cases"] >= 14
    cases = json.loads(calls[0].split("ESETEK:\n", 1)[1].split("\n\nKIMENET", 1)[0])
    assert {c["type"] for c in cases} == {"SZER_ELEM", "TESZ_NIVALO"}  # a változatok nem mennek a P3-ba
    assert all("helyzet" not in c["candidate"] and "kozeg" not in c["candidate"] for c in cases)
    plan = ctx.graph.plans[0]
    m05 = next(m for m in summary["nivalok"] if m["nivalo_id"] == "M05")
    # a C05 és a C05b ugyanazon MAG két SZÍN-e: két TESZ-nivaló, egy MAG alatt
    t5 = [t for t in summary["szinek"] if t["nivalo_id"] == "M05"]
    assert len(t5) == 2 and t5[0]["tesz_id"] != t5[1]["tesz_id"]
    assert {plan.szinek[t["tesz_id"]]["mag"] for t in t5} == {m05["graph_id"]}
    v = plan.szinek[t5[1]["tesz_id"]]
    assert (v["kozeg"][0], v["helyzet"][0]) == ("KOZ_FIZIKAI", "HELY_VALTOZO")
    # a TESZ-nivaló csomópont karcsú: címke és ALAP-jelölés nélkül (az ALAP-SZÍN a munkakör élén van)
    assert "label_hu" not in v["props"] and "alap_szin" not in v["props"]
    assert {l["props"]["alap_szin"] for l in plan.links if l["t"] in {t["tesz_id"] for t in t5}} == {True, False}
    # az LLM SAME-je a MAG-ra csak azonos TESZ-típusnál és meghatározó SZER-ELEM-nél áll meg (M1-őr):
    # a FakeGraph törzseleme VERIFY / 'x', a jelölt nem az -> új MAG, szakértői átnézésre
    assert summary["nivalok"][0]["decision"] == "NEW" and summary["nivalok"][0]["via"] == "llm-elutasitva"
    assert any(r["kind"] == "mag" for r in summary["review"])
    assert len(plan.links) == 14 and len(summary["nivalok"]) == 11
    assert plan.nivalok[m05["graph_id"]]["melyites"][0]["helyzet"] == "HELY_KRITIKUS"
    assert ctx.store.status(job["job_id"]) == "GRAFBAN"
    # a tesz_id a SZÍN-ből képzett: ugyanaz a MAG és SZÍN ugyanazt az id-t adja
    from gaphopper.merge import Resolver
    c05 = next(t for t in p1.szinek if t.cand_id == "C05")
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
    m = _p1_obj().nivalok[0].model_copy(update={"tesz_type": "VERIFY"})
    res.decisions["M1"] = __import__("gaphopper.schemas", fromlist=["P3Decision"]).P3Decision(
        case_id="M1", decision="SAME", target_id="old_TESZ_NIVALO", confidence=0.9)
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
    assert out["szamok"] == {"nivalo": 11, "szin": 14, "core_nivalo": 8, "nem_alap": 3, "melyitesi_jelolt": 1,
                             "torzsbol": 0, "torzs_meret": 0, "kesz_hiany": 0, "kesz_hiany_jelolt": 0}
    assert len(out["new_torzs"]) == 11 and out["new_torzs"][0]["kesz_ids"] == ["gh_c_3", "gh_c_270", "gh_c_42"]
    assert out["nivalok"][0]["egyesites"] == "NEW" and out["p3_cases"] >= 11
    assert [u["step"] for u in out["usage"]] == ["P1", "P3"]
    saved = json.loads((tmp_path / "dry" / "onet_29-1141.00.json").read_text(encoding="utf-8"))
    assert saved["szamok"]["szin"] == 14
    text = format_dry_run(out)
    assert "PRÓBAFUTÁS" in text and "C05b" in text and "mélyítési jelölt" in text
    # gráf nélkül az egyesítés kimarad, a többi lefut
    out2 = dry_run(rec, s, None, llm_override={"P1": FakeLLM(lambda sy, u: p1_text)}, out_dir=tmp_path / "dry")
    assert out2["ok"] and "p3_cases" not in out2 and not s.state_db.exists()


def test_p2_view_is_grouped_and_slim():
    from gaphopper.pipeline import p2_view

    v = p2_view(_p1_obj())
    m05 = next(m for m in v["nivalok"] if m["nivalo_id"] == "M05")
    assert [x["cand_id"] for x in m05["szinek"]] == ["C05", "C05b"]
    assert "tesz_type" not in m05["szinek"][0] and "label_hu" not in m05["szinek"][0]   # nem ismétli a MAG-ot
    assert m05["szinek"][1]["szin_kulonbseg_hu"] and m05["szinek"][1]["alap_szin"] is False
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
    S.save_artifact("b", "P2", {"verdict": "SKIPPED", "p2_reason": "szabályok rendben", "final_szinek": []}, "v")
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
        {"job_id": "onet:1", "title": "Ápoló", "ok": True, "szamok": {"mag": 12, "szinek": 16},
         "rule_flags": [{"rule": "R13", "message": "x"}], "total": {"input": 1, "output": 2}, "p2": {}}), encoding="utf-8")
    lst = web.dryrun_list()["runs"]
    assert lst[0]["job_id"] == "onet:1" and lst[0]["flags"] == ["R13"]
    assert web.dryrun_get("onet:1", brief=True)["szamok"]["mag"] == 12 and web.dryrun_get("x") is None


# ---------------------------------------------------------------- v2: általános MAG, KÉSZ-ség- és MAG-törzs
def _torzs_rec(mid="gh_niv_tanora", label="Tanóra vagy foglalkozás megtartása"):
    return {"id": mid, "label_hu": label, "label_en": "Deliver a lesson or session", "outcome_hu": "Megtartott óra.",
            "tesz_type": "SUPPORT_SERVE", "kesz_ids": ["gh_c_246", "gh_c_239"],
            "primary_szer": {"label_hu": "Foglalkozásterv", "label_en": "Session plan", "kind": "PLAN",
                             "role": "GUIDED_BY", "definition_hu": ""}}


def test_kesz_ids_normalized_and_checked():
    from gaphopper.config import kesz_torzs
    from gaphopper.schemas import TeszNivalo

    assert len(kesz_torzs()) >= 291 and kesz_torzs()["gh_c_246"]["label_en"] == "Public speaking (technical)"
    assert kesz_torzs()["gh_c_246"]["label_hu"] == "Szakmai nyilvános beszéd"
    assert all(x["label_hu"] and x["label_hu"] != x["label_en"] or x["label_hu"] in ("Empátia",)
               for x in kesz_torzs().values()) and kesz_torzs()["gh_c_19"]["label_hu"] == "Gyökérok feltárása"
    d = p1_fixture()["nivalok"][0] | {"kesz_ids": [3, "270", "gh_c_42", 3]}
    assert TeszNivalo.model_validate(d).kesz_ids == ["gh_c_3", "gh_c_270", "gh_c_42"]
    with pytest.raises(Exception, match="nincs a KÉSZ-ség-törzsben"):
        TeszNivalo.model_validate(d | {"kesz_ids": [999]})
    with pytest.raises(Exception):
        TeszNivalo.model_validate(d | {"kesz_ids": [1, 2, 3, 4, 5, 6]})  # legfeljebb 5 (2026-11-04)
    assert len(TeszNivalo.model_validate(d | {"kesz_ids": [1, 2, 3, 4, 5]}).kesz_ids) == 5


def test_kesz_hierarchy_v3():
    from gaphopper.config import kesz_hierarchia, kesz_torzs
    from gaphopper.schemas import KESZ_IRANY, KOZEG

    h, t = kesz_hierarchia(), kesz_torzs()
    kat = {k["kod"] for k in h["kategoriak"]}
    cs = {c["kod"]: c["kategoria"] for c in h["csoportok"]}
    assert kat == {"SZEM", "TARS", "VEZ", "KOG", "TECH"} and len(cs) == 19 and set(cs.values()) <= kat
    assert all(x["csoport"] in cs and 1 <= len(x["irany"]) <= 2 and set(x["irany"]) <= KESZ_IRANY for x in t.values())
    assert not any("tipus" in x or "type" in x for x in h["items"])      # a TUD/KÉP/HOZZÁ nem fajta (2026-11-05)
    assert "KOZ_EGYEN" in KESZ_IRANY and "KOZ_EGYEN" not in KOZEG       # egy-ÉN csak KÉSZ-ség-irány
    assert t["gh_c_181"]["irany"] == ["KOZ_EGYEN"] and t["gh_c_246"]["irany"] == ["KOZ_TARSAS"]
    assert t["gh_c_286"]["label_hu"] == "Hibaelhárítás" and t["gh_c_286"]["csoport"] == "H10"
    assert "en:Troubleshooting (advanced)" in t["gh_c_286"]["aliases"] and "en:Problem solving" in t["gh_c_5"]["aliases"]
    assert t["gh_c_290"]["csoport"] == "P2"
    assert all(f"gh_c_{n}" in t for n in [*range(21, 42), 44, 45, 51, 55])   # a v3-ból hiányzók maradnak
    assert t["gh_c_1"]["leiras_hu"]


def test_kesz_yaml_roundtrip(tmp_path):
    import yaml

    from gaphopper.config import kesz_hierarchia, save_kesz_torzs

    h = kesz_hierarchia()
    save_kesz_torzs(h, tmp_path / "k.yaml")
    d = yaml.safe_load((tmp_path / "k.yaml").read_text(encoding="utf-8"))
    assert d["items"] == h["items"] and d["csoportok"] == h["csoportok"] and d["version"] == h["version"]


def test_kesz_profil_axis2():
    from gaphopper.kesz import profil

    edges = [{"k": "gh_c_1", "tesz_type": "ASSESS_ANALYSE", "munkakorok": ["a", "b"]},
             {"k": "gh_c_1", "tesz_type": "PROBLEM_SOLVE", "munkakorok": ["c"]},
             {"k": "gh_c_2", "tesz_type": "DOCUMENT", "munkakorok": ["a"]},
             {"k": "gh_c_3", "tesz_type": "EXECUTE", "munkakorok": ["a", "b"]}]
    fields = {"a": "Health and care / Health", "b": "Digital and data / Computing", "c": "Engineering / Science"}
    p = {r["id"]: r for r in profil(edges, fields)}
    assert p["gh_c_1"]["csalad"] == ["megismero", "javito"] and p["gh_c_1"]["csalad_arany"] == [0.67, 0.33]
    assert p["gh_c_1"]["hordozhatosag"] == "altalanos" and p["gh_c_1"]["munkakor_db"] == 3
    assert p["gh_c_3"]["hordozhatosag"] == "atviheto" and p["gh_c_2"]["hordozhatosag"] == "szakmai"
    assert p["gh_c_2"]["fo_csalad"] == "kozlo"


def test_kesz_hiany_guard_r23():
    from gaphopper.rules import kesz_hiany_guard as g

    assert g("Hibaelhárítás", "Troubleshooting") == ""
    assert g("Kockázatérzékelés", "Risk awareness") == ""
    assert "termék" in g("SAP használata", "Using SAP")
    assert "szám" in g("IEC 60364 ismerete", "")
    assert "szint" in g("Haladó táblázatkezelés", "Advanced spreadsheets")
    assert "hosszú" in g("Nagyon hosszú és összetett készség neve itt", "")
    assert "munkakör" in g("Villanyszerelési rutin", "", "Villanyszerelő", "Built environment and trades")
    # a magyar munkakörnév töve (2026-11-05: az angol cím nem fogta meg az "Állatkezelés"-t)
    assert "magyar nevének" in g("Állatkezelés", "Animal handling", "Veterinary technician", "Health", "Állatorvos-asszisztens")
    assert g("Kockázatérzékelés", "Risk awareness", "Chef", "Hospitality", "Séf") == ""


def test_kesz_hiany_schema_and_rows():
    from gaphopper.kesz import hiany_rows
    from gaphopper.schemas import TeszNivalo

    d = p1_fixture()["nivalok"][0]
    m = TeszNivalo.model_validate(d | {"kesz_hiany": [
        {"label_hu": "Kockázatérzékelés", "label_en": "Risk awareness", "csoport": "P2", "irany": "KOZ_EGYEN"},
        {"label_hu": "Hibaelhárítás", "label_en": "x"}]})
    assert m.kesz_hiany[0].irany == ["KOZ_EGYEN"]
    with pytest.raises(Exception):
        TeszNivalo.model_validate(d | {"kesz_hiany": [{"label_hu": "a"}] * 3})        # legfeljebb 2
    with pytest.raises(Exception, match="csoport"):
        TeszNivalo.model_validate(d | {"kesz_hiany": [{"label_hu": "a", "csoport": "X9"}]})
    rows, had = hiany_rows({"title": "Ápoló", "field": "Health"}, "gh_niv_x", m.kesz_hiany)
    assert had == ["gh_c_286"]                                 # a törzsben meglévő név IGENYEL lesz, nem jelölt
    assert [r["status"] for r in rows] == ["JELOLT"] and rows[0]["id"].startswith("gh_kh_")
    rows2, _ = hiany_rows({}, "gh_niv_x", TeszNivalo.model_validate(
        d | {"kesz_hiany": [{"label_hu": "SAP kezelése"}]}).kesz_hiany)
    assert rows2[0]["status"] == "TUD_SZER" and rows2[0]["props"]["ok"]


def test_p1_system_has_torzs_and_two_cache_blocks(tmp_path):
    from gaphopper.llm import CACHE_SPLIT, plain_system, system_blocks
    from gaphopper.pipeline import Context

    ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False)
    ctx.extra_torzs = [_torzs_rec()]
    sys_ = ctx.p1_system()
    static, dynamic = sys_.split(CACHE_SPLIT)
    assert "246|Szakmai nyilvános beszéd|S1" in static     # a P1 a magyar nevet látja, irány szerinti fejléc alatt
    assert static.index("# KOZ_TARSAS") < static.index("246|Szakmai") and "Technikák és megközelítések" not in static
    assert "hozzáÁLL" not in static and "KOZ_EGYEN = egy-ÉN" in static
    assert dynamic.endswith("T01|Tanóra vagy foglalkozás megtartása|SUPPORT_SERVE")
    blocks = system_blocks(sys_)
    assert len(blocks) == 2 and all(b["cache_control"] == {"type": "ephemeral"} for b in blocks)
    assert CACHE_SPLIT not in plain_system(sys_)


def test_mag_ref_from_torzs_without_p3(tmp_path):
    """A törzsből választott MAG (nivalo_ref) mezői a törzsből jönnek; az egyesítés hívás nélkül feloldja."""
    from gaphopper.pipeline import Context, merge_plan

    s = Settings(state_db=tmp_path / "s.sqlite")
    s.embed_model = ""
    d = p1_fixture()
    d["nivalok"][8] = {"nivalo_id": "M09", "nivalo_ref": "T01", "kor_id": "K5", "relevance": "SUPPORTING",
                          "kontextus_hu": "betegek és hozzátartozók"}
    calls = []
    ctx = Context(s, with_graph=False, llm_override={"P1": FakeLLM(lambda sy, u: json.dumps(d, ensure_ascii=False)),
                                                     "P3": FakeLLM(_p3_same_first(calls))})
    ctx.extra_torzs = [_torzs_rec()]
    obj, _ = call_json(ctx.llm("P1"), ctx.p1_system(), "job", P1Output, pre=ctx.resolve_mag_refs)
    m9 = obj.nivalok[8]
    assert m9.nivalo_ref == "gh_niv_tanora" and m9.label_hu == "Tanóra vagy foglalkozás megtartása"
    assert m9.kesz_ids == ["gh_c_246", "gh_c_239"] and m9.tesz_type == "SUPPORT_SERVE"
    c09 = next(t for t in obj.szinek if t.nivalo_id == "M09")
    assert c09.label_hu == m9.label_hu and c09.kontextus_hu == "betegek és hozzátartozók"
    ctx.graph = FakeGraph()
    plan, summary, _ = merge_plan(ctx, {"job_id": "x", "title": "Ápoló"}, obj.model_dump(), obj.nivalok, obj.szinek)
    s9 = next(m for m in summary["nivalok"] if m["nivalo_id"] == "M09")
    assert (s9["decision"], s9["via"], s9["graph_id"]) == ("SAME", "torzs", "gh_niv_tanora")
    cases = json.loads(calls[0].split("ESETEK:\n", 1)[1].split("\n\nKIMENET", 1)[0])
    assert all(c["candidate"].get("label_hu") != m9.label_hu for c in cases if c["type"] == "TESZ_NIVALO")
    link = next(l for l in plan.links if l["props"]["nivalo_cand_id"] == "M09")
    assert link["props"]["kontextus_hu"] == "betegek és hozzátartozók"
    assert plan.nivalok["gh_niv_tanora"]["kesz_ids"] == ["gh_c_246", "gh_c_239"]
    # a FakeGraph-ban a törzs-elem nincs meg (pl. gráf-újraépítés után): teljes mezőkkel, új elemként jön létre
    assert plan.nivalok["gh_niv_tanora"]["new"] and plan.nivalok["gh_niv_tanora"]["props"]["label_hu"]
    # a régi azonosító-előtag (gh_mag_) a hash megtartásával gh_niv_-re fordul
    from gaphopper.schemas import TeszNivalo
    assert TeszNivalo.model_validate(d["nivalok"][0] | {"nivalo_ref": "gh_mag_abc"}).nivalo_ref == "gh_niv_abc"
    # ismeretlen kód: sémahiba (újrapróbálás a hibaüzenettel)
    d["nivalok"][8]["nivalo_ref"] = "T99"
    with pytest.raises(LLMError, match="ismeretlen nivalo_ref"):
        call_json(FakeLLM(lambda *a: json.dumps(d)), "s", "u", P1Output, retries=0, pre=ctx.resolve_mag_refs)


def test_rules_v2_generality():
    d = p1_fixture()
    d["nivalok"][0]["label_hu"] = "Ápolói állapotfelmérés a hallgatók körében"   # R17: célcsoport + munkakör
    d["nivalok"][6]["kesz_ids"] = [28, 29]                                        # R18: M05 ~ M07? más típus
    d["nivalok"][5]["kesz_ids"] = [28, 29, 24]                                    # R18: M05 + M06 EXECUTE
    d["nivalok"][3]["kesz_ids"] = [113]                                           # R20
    p1 = P1Output.model_validate(d)
    f = {x.rule: x for x in rules.check(p1.szinek, None, "Health and care", magok=p1.nivalok,
                                         job_title="Ápoló")}
    assert f["R17"].cand_ids == ["M01"] and f["R20"].cand_ids == ["M04"]
    # az általános célcsoport maradhat, a szak szerinti bontás nem (felhasználói döntés, 2026-11-04)
    for label, flagged in (("Kutatómunka témavezetése hallgatóknak", False),
                           ("Kutatómunka témavezetése gépész hallgatóknak", True)):
        d2 = p1_fixture()
        d2["nivalok"][3]["label_hu"] = label
        p2 = P1Output.model_validate(d2)
        # R17 csak a saját munkakör címét és szakterületét nézi (2026-11-05): itt a szakterület "Gépészet"
        r17 = [x for x in rules.check(p2.szinek, None, "Gépészet", magok=p2.nivalok, job_title="Egyetemi oktató")
               if x.rule == "R17"]
        assert ("M04" in (r17[0].cand_ids if r17 else [])) is flagged, label
    assert set(f["R18"].cand_ids) == {"M05", "M06"} and "M07" not in f["R18"].cand_ids
    assert "R19" not in f
    # R19: nagy törzs mellett túl sok új MAG
    f2 = {x.rule for x in rules.check(p1.szinek, None, "", magok=p1.nivalok, torzs_size=60)}
    assert "R19" in f2
    # R4: hosszú MAG-címke (G1: legfeljebb 6 szó)
    d = p1_fixture()
    d["nivalok"][1]["label_hu"] = "Életjelek mérése és rögzítése minden műszak elején és végén"
    assert "R4" in {x.rule for x in rules.check(P1Output.model_validate(d).szinek, None, "",
                                                 magok=P1Output.model_validate(d).nivalok)}


def test_r17_no_false_positives_from_english_titles():
    """TOP10-ben talált téves R17-jelzések: angol munkakörcím-szavak az angol címkében, 'engineering'."""
    d = p1_fixture()
    d["nivalok"][9]["label_en"] = "Record in a register"
    d["nivalok"][3]["label_en"] = "Develop and run tests"
    d["nivalok"][4]["label_en"] = "Perform engineering calculations"
    p1 = P1Output.model_validate(d)
    for title in ("Registered nurse", "Software developer", "Electrical engineer"):
        assert "R17" not in {x.rule for x in rules.check(p1.szinek, None, "", magok=p1.nivalok, job_title=title)}
    d["nivalok"][10]["label_en"] = "Direct nursing staff work"     # rögzített szólista nincs: nem jelez
    p1 = P1Output.model_validate(d)
    assert [x.cand_ids for x in rules.check(p1.szinek, None, "", magok=p1.nivalok) if x.rule == "R17"] == []


def test_r21_szer_in_label():
    """R21 (G5): a TESZ-nivaló neve tartalmazza a meghatározó SZER-ELEM nevét vagy tövét."""
    d = p1_fixture()
    d["nivalok"][4]["label_hu"] = "Működés ellenőrzése előírt kritérium szerint"   # SZER: Gyógyszerelési rendelés
    d["nivalok"][1]["label_hu"] = "Életjel mérése"                                  # SZER: Életjel-mérő eszköz
    p1 = P1Output.model_validate(d)
    f = [x for x in rules.check(p1.szinek, None, "", magok=p1.nivalok) if x.rule == "R21"]
    assert f and f[0].cand_ids == ["M05"]
    assert rules.stem_overlap("Programkód", "Program írása és módosítása")
    assert rules.stem_overlap("Ábra", "Ábrák készítése adatokból")          # magánhangzó-nyúlás
    assert not rules.stem_overlap("Teszteset", "Működés ellenőrzése előírt kritérium szerint")


def test_p2_fix_melyites_as_text_is_dropped():
    from gaphopper.pipeline import apply_p2
    from gaphopper.schemas import P2Output

    p1 = _p1_obj()
    out = P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "nivalo_id": "M02", "set": {"melyites_jeloltek": "Kritikus leállás (HELY_KRITIKUS)",
                                               "kontextus_hu": "gyártósor"}}]})
    magok, _, warn = apply_p2(p1.nivalok, p1.szinek, out.changes)
    assert magok[1].kontextus_hu == "gyártósor" and any("melyites_jeloltek" in w for w in warn)


def test_p2_fix_identity_drops_mag_ref():
    from gaphopper.pipeline import apply_p2
    from gaphopper.schemas import P2Output

    p1 = _p1_obj()
    mags = [m.model_copy(update={"nivalo_ref": "gh_mag_x"}) if m.nivalo_id == "M04" else m for m in p1.nivalok]
    out = P2Output.model_validate({"verdict": "FIXED", "changes": [
        {"op": "FIX", "nivalo_id": "M04", "set": {"label_hu": "Ellátási terv készítése és frissítése"}},
        {"op": "FIX", "nivalo_id": "M05", "set": {"kontextus_hu": "intenzív osztály", "kesz_ids": [28, 29]}}]})
    magok, final, _ = apply_p2(mags, p1.szinek, out.changes)
    by = {m.nivalo_id: m for m in magok}
    assert by["M04"].nivalo_ref is None and by["M05"].kesz_ids == ["gh_c_28", "gh_c_29"]
    assert next(t for t in final if t.cand_id == "C05b").kontextus_hu == "intenzív osztály"


def test_p5_splits_on_max_tokens(tmp_path):
    """P5: túl hosszú válasznál a csomag kettéválik (a kimenetet nem vágjuk)."""
    from gaphopper.pipeline import Context, run_p5

    rows = [{"id": f"g{i}", "label_hu": f"MAG {i}", "outcome_hu": "x", "tesz_type": "VERIFY", "primary_szer": "y",
             "rubric_alap_hu": None, "kontextusok": [],
             "szinek": [{"id": f"t{i}", "kozeg": "KOZ_FIZIKAI", "helyzet": "HELY_RUTIN", "alap": True,
                             "kesz": False}]} for i in range(4)]
    written = []
    graph = SimpleNamespace(p5_todo=lambda limit: rows, set_certificates=lambda m, v, pv: written.append((m, v)))

    def p5(system, user):
        part = json.loads(user.split("TESZ-SZÍN-EIK:\n", 1)[1].split("\n\nKIMENET", 1)[0])
        if len(part) > 1:
            raise LLMError("A válasz elérte a max_tokens korlátot (6000)")
        return json.dumps({"nivalok": [{"id": x["id"], "rubric_alap_hu": ["a", "b", "c"]} for x in part],
                           "szinek": [{"id": v["id"], "attestation_hu": "A személy önállóan ...", "rubric_szin_hu": []}
                                          for x in part for v in x["szinek"]]})

    ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False, llm_override={"P5": FakeLLM(p5)})
    ctx.graph = graph
    st = run_p5(ctx, log=lambda *a: None)
    assert st["kesz_mag"] == 4 and st["kesz_valtozat"] == 4 and st["hiba"] == 0 and len(written) == 4


def test_generalize_renames_and_splits(tmp_path, monkeypatch):
    """P6: ATNEVEZ -> új név, a régi alias (azonosító marad); szűk SZER-ELEM -> általános fajta; BONT -> szakértőhöz."""
    from gaphopper import pipeline
    from gaphopper.pipeline import Context, run_generalize

    monkeypatch.setattr(pipeline, "ROOT", tmp_path)
    cands = [{"id": "g1", "label_hu": "Higiéniai előírás betartása", "label_en": "Comply with hygiene rules",
              "tesz_type": "COMPLY", "szer_id": "s1", "primary_szer": "Higiéniai előírás", "munkakor_db": 3,
              "munkakorok": [{"munkakor": "Ápoló", "kontextus": "kézhigiéné"},
                             {"munkakor": "Villanyszerelő", "kontextus": "villamos biztonság"}], "kesz": []},
             {"id": "g2", "label_hu": "Működés ellenőrzése kritérium szerint", "label_en": "", "tesz_type": "VERIFY",
              "szer_id": "s2", "primary_szer": "Teszteset", "munkakor_db": 7,
              "munkakorok": [{"munkakor": "Szoftverfejlesztő", "kontextus": "szoftvertesztelés"},
                             {"munkakor": "Villanyszerelő", "kontextus": "szigetelésmérés"}], "kesz": []},
             {"id": "g3", "label_hu": "Nyilvántartás vezetése", "label_en": "", "tesz_type": "DOCUMENT",
              "szer_id": "s3", "primary_szer": "Nyilvántartás", "munkakor_db": 6,
              "munkakorok": [{"munkakor": "Ápoló", "kontextus": "a"}, {"munkakor": "Adattudós", "kontextus": "b"}],
              "kesz": []}]
    calls = {"ren": [], "szer": []}
    graph = SimpleNamespace(generalize_candidates=lambda mj: cands,
                            rename_nivalo=lambda rows: calls["ren"].extend(rows),
                            generalize_szer=lambda rows: calls["szer"].extend(rows))
    answer = {"nivalok": [
        {"id": "g1", "dontes": "ATNEVEZ", "label_hu": "Előírás betartása és igazolása",
         "label_en": "Comply with regulations", "szer_hu": "Előírás", "indok_hu": "Nem csak higiéniai."},
        {"id": "g2", "dontes": "BONT", "indok_hu": "Két külön tevékenység.",
         "bontas": [{"label_hu": "Szoftver tesztelése", "munkakorok": ["Szoftverfejlesztő"]},
                    {"label_hu": "Villamos jellemzők mérése", "munkakorok": ["Villanyszerelő"]}]},
        {"id": "g3", "dontes": "MARAD"}]}
    prompts = []
    ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False,
                  llm_override={"P3": FakeLLM(lambda s, u: prompts.append(u) or json.dumps(answer, ensure_ascii=False))})
    ctx.graph = graph
    out = run_generalize(ctx, log=lambda *a: None)
    assert out["marad"] == 1 and len(out["valtozasok"]) == 1 and len(out["bontas"]) == 1
    assert calls["ren"][0]["label_hu"] == "Előírás betartása és igazolása"
    assert "higiéniai előírás betartása" in calls["ren"][0]["old_keys"]           # a régi név alias marad
    assert calls["szer"][0]["szer_hu"] == "Előírás"
    assert [r["kind"] for r in ctx.store.queue()] == ["split_review"]
    assert "Villanyszerelő: villamos biztonság" in prompts[0]
    assert json.loads(Path(out["fajl"]).read_text(encoding="utf-8"))["valtozasok"][0]["regi"] == "Higiéniai előírás betartása"
    # próbafutás: semmi nem íródik
    calls["ren"].clear()
    run_generalize(ctx, apply=False, log=lambda *a: None)
    assert calls["ren"] == []


def test_fajtaja_edge_and_torzs_guard(tmp_path):
    """A meghatározó SZER-ELEM szűkebb fajtája a TESZ-SZÍN kísérő SZER-ELEM-e, FAJTAJA éllel; törzs-választásnál
    eltérő SZER-ELEM-fajta esetén nincs automatikus SAME (átnézés)."""
    from gaphopper.pipeline import Context, merge_plan

    s = Settings(state_db=tmp_path / "s.sqlite")
    s.embed_model = ""
    d = p1_fixture()
    d["szinek"][0]["secondary_szer"] = [{"label_hu": "Ápolási felmérőlap", "kind": "METHOD", "role": "USES",
                                         "fajtaja": True}]
    d["nivalok"][8] = {"nivalo_id": "M09", "nivalo_ref": "T01", "szer": "Mérőműszer", "kor_id": "K5",
                       "relevance": "SUPPORTING"}
    ctx = Context(s, with_graph=False, llm_override={"P3": FakeLLM(_p3_same_first([]))})
    ctx.extra_torzs = [_torzs_rec()]
    ctx.graph = FakeGraph()
    p1 = P1Output.model_validate(ctx.resolve_mag_refs(d))
    assert p1.nivalok[8].ref_szer == "Mérőműszer"
    plan, summary, _ = merge_plan(ctx, {"job_id": "x", "title": "Ápoló"}, p1.model_dump(), p1.nivalok, p1.szinek)
    m9 = next(m for m in summary["nivalok"] if m["nivalo_id"] == "M09")
    assert m9["via"] == "torzs-ellenorzendo" and any(r["kind"] == "mag" for r in summary["review"])
    assert plan.szer_broader and all(b["a"] != b["b"] for b in plan.szer_broader)


def test_split_plan_moves_listed_jobs():
    """Bontás: a régi névvel azonos rész a régi TESZ-nivalón marad, a többi új TESZ-nivalót kap a munkakörei
    TESZ-SZÍN-jeivel; az új SZER-ELEM a munkakör saját (P1) SZER-ELEM-fajtája."""
    from gaphopper.pipeline import split_plan

    cx = {"g": {"label_hu": "Szoftver tesztelése"}, "tesz_type": "VERIFY", "szer_kind": "DATA", "role": "ACTS_ON",
          "links": [{"job": "j1", "job_hu": "Szoftverfejlesztő", "szin_id": "s1", "kozeg": "KOZ_DIGITALIS",
                     "helyzet": "HELY_RUTIN"},
                    {"job": "j2", "job_hu": "Villanyszerelő", "szin_id": "s2", "kozeg": "KOZ_FIZIKAI",
                     "helyzet": "HELY_RUTIN"},
                    {"job": "j3", "job_hu": "Mechatronikai technikus", "szin_id": "s3", "kozeg": "KOZ_FIZIKAI",
                     "helyzet": "HELY_PROBLEMA"}]}
    prop = {"javaslat": [{"label_hu": "Szoftver tesztelése", "munkakorok": ["Szoftverfejlesztő: egységteszt"]},
                         {"label_hu": "Villamos áramkör vizsgálata", "munkakorok": ["Villanyszerelő: szigetelésmérés"]}]}
    parts = split_plan(cx, prop, {"j2": "Mérőműszer"})
    assert len(parts) == 1
    p = parts[0]
    assert p["new"]["label_hu"] == "Villamos áramkör vizsgálata" and p["new"]["szer_hu"] == "Mérőműszer"
    assert [m["job"] for m in p["moves"]] == ["j2"] and p["moves"][0]["old_szin"] == "s2"
    assert p["new"]["id"].startswith("gh_niv_") and p["moves"][0]["new_szin"].startswith("gh_szin_")


class _KeszGraph:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def kesz_hiany_candidates(self):
        return self.rows

    def upsert_keszseg(self, ids):
        self.calls.append(("upsert", ids))

    def kesz_hiany_resolve(self, ids, k, st):
        self.calls.append(("resolve", ids, k, st))
        return len(ids)

    def kesz_hiany_status(self, ids, st, ok=""):
        self.calls.append(("status", ids, st))

    def update_kesz_profil(self):
        self.calls.append(("profil",))


def _tmp_torzs(tmp_path, monkeypatch):
    import shutil

    from gaphopper import config

    p = tmp_path / "kesz_torzs.yaml"
    shutil.copy(config.KESZ_PATH, p)
    monkeypatch.setattr(config, "KESZ_PATH", p)
    config.kesz_cache_clear()
    return p


def test_kesz_bovit_alias_uj_elvet_and_approve(tmp_path, monkeypatch):
    from gaphopper import config
    from gaphopper.kesz import run_kesz_bovit
    from gaphopper.pipeline import Context, approve_job

    _tmp_torzs(tmp_path, monkeypatch)
    try:
        rows = [{"id": "gh_kh_a", "label_hu": "Hibakeresés", "label_en": "Fault finding", "csoport": "H10",
                 "irany": ["KOZ_FIZIKAI"], "nivalok": ["Hiba elhárítása"], "munkakorok": ["j1"]},
                {"id": "gh_kh_b", "label_hu": "Kockázatérzékelés", "label_en": "Risk awareness", "csoport": "P2",
                 "irany": ["KOZ_EGYEN"], "nivalok": ["X"], "munkakorok": ["j1"]},
                {"id": "gh_kh_c", "label_hu": "Kockázatészlelés", "label_en": "Risk perception", "csoport": "P2",
                 "irany": [], "nivalok": ["Y"], "munkakorok": ["j2"]},
                {"id": "gh_kh_d", "label_hu": "Ritka érzék", "label_en": "", "csoport": "", "irany": [],
                 "nivalok": ["Z"], "munkakorok": ["j3"]},
                {"id": "gh_kh_e", "label_hu": "Egyedi dolog", "label_en": "", "csoport": "", "irany": [],
                 "nivalok": ["Z"], "munkakorok": ["j3"]}]
        seen = {}

        def p8(system, user):
            seen["system"], seen["user"] = system, user
            return json.dumps({"csoportok": [
                {"ids": ["gh_kh_a"], "dontes": "ALIAS", "kesz": 286},
                {"ids": ["gh_kh_b", "gh_kh_c"], "dontes": "UJ", "label_hu": "Kockázatérzékelés",
                 "label_en": "Risk awareness", "csoport": "P2", "irany": ["KOZ_EGYEN"]},
                {"ids": ["gh_kh_d"], "dontes": "UJ", "label_hu": "Ritka érzék", "label_en": "Rare sense",
                 "csoport": "P2", "irany": ["KOZ_EGYEN"]},
                {"ids": ["gh_kh_e"], "dontes": "ELVET", "indok_hu": "szűk"}]})

        s = Settings(state_db=tmp_path / "s.sqlite")
        ctx = Context(s, with_graph=False, llm_override={"P3": FakeLLM(p8)})
        ctx.graph = _KeszGraph(rows)
        out = run_kesz_bovit(ctx)
        assert "286|Hibaelhárítás|H10" in seen["system"] and "gh_kh_a" in seen["user"]
        assert [c["kesz"] for c in out["alias"]] == ["gh_c_286"]
        assert len(out["uj"]) == 1 and out["uj"][0]["munkakorok"] == ["j1", "j2"]
        assert [v["label_hu"] for v in out["var"]] == ["Ritka érzék"]        # 1 munkakör: vár, nem javaslat
        assert ("resolve", ["gh_kh_a"], "gh_c_286", "ALIAS") in ctx.graph.calls
        assert ("status", ["gh_kh_e"], "ELVETVE") in ctx.graph.calls
        assert "hu:Hibakeresés" in config.kesz_torzs()["gh_c_286"]["aliases"]   # a yaml-ba is
        q = [r for r in ctx.store.queue() if r["kind"] == "kesz_review"]
        assert len(q) == 1
        nxt = f"gh_c_{max(int(k[5:]) for k in config.kesz_torzs()) + 1}"     # 292-től, a törzs méretétől függően
        res = approve_job(ctx, q[0]["id"])
        assert res["id"] == nxt and config.kesz_torzs()[nxt]["irany"] == ["KOZ_EGYEN"]
        assert config.kesz_torzs()[nxt]["forras"] == "kesz-bovit"
        assert ("resolve", ["gh_kh_b", "gh_kh_c"], nxt, "BEEMELT") in ctx.graph.calls
        assert not [r for r in ctx.store.queue() if r["kind"] == "kesz_review"]
    finally:
        config.kesz_cache_clear()


def test_kesz_bovit_check_rejects_missing_ids(tmp_path, monkeypatch):
    from gaphopper import config
    from gaphopper.kesz import run_kesz_bovit
    from gaphopper.pipeline import Context

    _tmp_torzs(tmp_path, monkeypatch)
    try:
        rows = [{"id": "gh_kh_a", "label_hu": "A", "label_en": "", "csoport": "", "irany": [], "nivalok": [],
                 "munkakorok": ["j1"]}, {"id": "gh_kh_b", "label_hu": "B", "label_en": "", "csoport": "",
                                         "irany": [], "nivalok": [], "munkakorok": ["j1"]}]
        ans = json.dumps({"csoportok": [{"ids": ["gh_kh_a"], "dontes": "ELVET"}]})
        ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False,
                      llm_override={"P3": FakeLLM(lambda a, b: ans)})
        ctx.graph = _KeszGraph(rows)
        out = run_kesz_bovit(ctx)
        assert out["hiba"] == 2 and ctx.graph.calls == []
    finally:
        config.kesz_cache_clear()


def test_kesz_irany_review(tmp_path, monkeypatch):
    from gaphopper import config
    from gaphopper.kesz import run_kesz_irany
    from gaphopper.pipeline import Context, approve_job

    _tmp_torzs(tmp_path, monkeypatch)
    try:
        ans = json.dumps({"elemek": [{"n": 101, "irany": ["KOZ_DIGITALIS"], "indok_hu": "adat"},
                                     {"n": 102, "irany": ["KOZ_TARSAS", "KOZ_SZERVEZETI"], "biztos": False}]})
        ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False,
                      llm_override={"P3": FakeLLM(lambda a, b: ans if "101|" in b else '{"elemek":[]}')})
        ctx.graph = _KeszGraph([])
        out = run_kesz_irany(ctx, chunk=400, only_default=False)
        t = config.kesz_torzs()
        assert out["vizsgalt"] == len(t) and t["gh_c_101"]["irany"] == ["KOZ_DIGITALIS"]
        assert t["gh_c_101"]["irany_forras"] == "llm" and t["gh_c_1"]["irany_forras"] == "llm"
        assert t["gh_c_102"]["irany_forras"] == "ellenorzendo"
        q = [r for r in ctx.store.queue() if r["kind"] == "kesz_irany_review"]
        approve_job(ctx, q[0]["id"])
        assert config.kesz_torzs()["gh_c_102"]["irany"] == ["KOZ_TARSAS", "KOZ_SZERVEZETI"]
    finally:
        config.kesz_cache_clear()


def test_dry_run_kesz_hiany_to_bovit(tmp_path):
    """P1 kesz_hiany -> próbafutás-kimenet (R23-szűrővel) -> kesz-bovit jelöltlista."""
    from gaphopper.kesz import candidates_from_dryrun
    from gaphopper.pipeline import dry_run

    s = Settings()
    s.state_db = tmp_path / "state.sqlite"
    s.embed_model = ""
    s.p2_mode = "never"
    p1 = json.loads((FIX / "p1_nurse.json").read_text(encoding="utf-8"))
    p1["nivalok"][0]["kesz_hiany"] = [{"label_hu": "Kockázatérzékelés", "label_en": "Risk awareness",
                                       "csoport": "P2", "irany": ["KOZ_EGYEN"]},
                                      {"label_hu": "SAP kezelése", "label_en": "SAP handling"}]
    p1["nivalok"][1]["kesz_hiany"] = [{"label_hu": "Hibaelhárítás", "label_en": "Troubleshooting"}]
    rec = json.loads((Path(__file__).parents[1] / "data" / "sample_jobs.jsonl").read_text(encoding="utf-8").splitlines()[0])
    out = dry_run(rec, s, None, llm_override={"P1": FakeLLM(lambda sy, u: json.dumps(p1))}, out_dir=tmp_path / "dry")
    assert out["ok"], out.get("error")
    st = {h["label_hu"]: h["status"] for h in out["kesz_hiany"]}
    assert st == {"Kockázatérzékelés": "JELOLT", "SAP kezelése": "TUD_SZER", "Hibaelhárítás": "TORZSBEN"}
    assert out["szamok"]["kesz_hiany"] == 3 and out["szamok"]["kesz_hiany_jelolt"] == 1
    c = candidates_from_dryrun(list((tmp_path / "dry").glob("*.json")))
    assert [x["label_hu"] for x in c] == ["Kockázatérzékelés"] and c[0]["munkakorok"] == [rec["job_id"]]


def test_kesz_bovit_partial_cover_is_uj(tmp_path, monkeypatch):
    """Gyógypedagógus, 2026-11-05: az "Egyéni differenciálás" a 161 "Fejlődést segítő támogatás"-t csak részben fedi:
    UJ-javaslat (szakértői sor), nem ALIAS; a prompt a teljes fedést kéri ALIAS-hoz."""
    from gaphopper import config
    from gaphopper.kesz import run_kesz_bovit
    from gaphopper.pipeline import Context

    _tmp_torzs(tmp_path, monkeypatch)
    try:
        assert "Ízérzékelés" in prompt("p8_kesz_bovit")             # érzékszervi képesség törzsszintű (2026-11-05)
        assert config.kesz_torzs()["gh_c_293"]["csoport"] == "H10" and "hu:Ízlés" in config.kesz_torzs()["gh_c_293"]["aliases"]
        assert "csak teljes jelentésfedésnél" in prompt("p8_kesz_bovit") and "Ha kétséges, ALIAS" not in prompt("p8_kesz_bovit")
        assert "kesz_hiany" in prompt("p1_felbontas").split("KIMENET")[1]      # példa a kimeneti mintában ...
        assert "Egyéni differenciálás" not in prompt("p1_felbontas")           # ... helyőrzővel (szivárgás ellen)
        rows = [{"id": "gh_kh_dif", "label_hu": "Egyéni differenciálás", "label_en": "Individual differentiation",
                 "csoport": "L1", "irany": ["KOZ_TARSAS"], "nivalok": ["Egyéni fejlesztési terv készítése"],
                 "munkakorok": ["onet:25-2056.00", "onet:25-2021.00"]}]
        ans = json.dumps({"csoportok": [{"ids": ["gh_kh_dif"], "dontes": "UJ", "label_hu": "Egyéni differenciálás",
                                         "label_en": "Individual differentiation", "csoport": "L1",
                                         "irany": ["KOZ_TARSAS"], "indok_hu": "a 161 csak részben fedi"}]})
        ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False,
                      llm_override={"P3": FakeLLM(lambda a, b: ans)})
        ctx.graph = _KeszGraph(rows)
        n0 = len(config.kesz_torzs())
        out = run_kesz_bovit(ctx)
        assert not out["alias"] and [u["label_hu"] for u in out["uj"]] == ["Egyéni differenciálás"]
        assert [r["kind"] for r in ctx.store.queue()] == ["kesz_review"]
        assert ("status", ["gh_kh_dif"], "JAVASOLT") in ctx.graph.calls
        assert len(config.kesz_torzs()) == n0          # jóváhagyás nélkül nem kerül a törzsbe
    finally:
        config.kesz_cache_clear()


def test_kesz_bovit_queue_from_dryrun_no_graph(tmp_path, monkeypatch):
    from gaphopper import config
    from gaphopper.kesz import run_kesz_bovit
    from gaphopper.pipeline import Context

    _tmp_torzs(tmp_path, monkeypatch)
    try:
        rows = [{"id": "gh_kh_x", "label_hu": "Differenciált oktatás", "label_en": "", "csoport": "L1", "irany": [],
                 "nivalok": [], "munkakorok": ["a", "b"]}]
        ans = json.dumps({"csoportok": [{"ids": ["gh_kh_x"], "dontes": "UJ", "label_hu": "Differenciált támogatás",
                                         "label_en": "Differentiated support", "csoport": "L1", "irany": ["KOZ_TARSAS"]}]})
        ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False,
                      llm_override={"P3": FakeLLM(lambda a, b: ans)})
        out = run_kesz_bovit(ctx, rows, apply=False, queue=True)     # ctx.graph None: nem is írhat
        assert len(out["uj"]) == 1 and [r["kind"] for r in ctx.store.queue()] == ["kesz_review"]
    finally:
        config.kesz_cache_clear()


def test_kesz_bovit_sensory_is_uj(tmp_path, monkeypatch):
    """Séf + pék, 2026-11-05 (felhasználói döntés): az "Ízlésérzék" érzékszervi képesség -> UJ (H10, KOZ_FIZIKAI),
    nem ELVET; a név az érzékre utal, nem a szakmára."""
    from gaphopper import config
    from gaphopper.kesz import run_kesz_bovit
    from gaphopper.pipeline import Context

    _tmp_torzs(tmp_path, monkeypatch)
    try:
        p8 = prompt("p8_kesz_bovit")
        assert "hallási megkülönböztetés" in p8 and "nem \"Konyhai" in p8 and "csak az, ami nem egy konkrét érzékre" in p8
        assert "érzékszervi vagy testi képesség" in prompt("p1_felbontas")
        rows = [{"id": "gh_kh_iz", "label_hu": "Ízlésérzék", "label_en": "Sense of taste", "csoport": "H10",
                 "irany": ["KOZ_FIZIKAI"], "nivalok": ["Étel elkészítése"], "munkakorok": ["onet:35-1011.00",
                                                                                         "onet:51-3011.00"]}]
        ans = json.dumps({"csoportok": [{"ids": ["gh_kh_iz"], "dontes": "UJ", "label_hu": "Ízérzékelés",
                                         "label_en": "Taste perception", "csoport": "H10", "irany": ["KOZ_FIZIKAI"]}]})
        ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False,
                      llm_override={"P3": FakeLLM(lambda a, b: ans)})
        out = run_kesz_bovit(ctx, rows, apply=False, queue=True)
        assert not out["elvet"] and [(u["label_hu"], u["csoport"], u["irany"]) for u in out["uj"]] == \
            [("Ízérzékelés", "H10", ["KOZ_FIZIKAI"])]
        assert [r["kind"] for r in ctx.store.queue()] == ["kesz_review"]
    finally:
        config.kesz_cache_clear()


def test_anthropic_bad_workspace_retries_without_header(monkeypatch):
    """A UI-ból indított futás a .env ANTHROPIC_WORKSPACE_ID-jét is látja: ha a munkaterület nem létezik (404),
    a hívás fejléc nélkül megismétlődik, így a CLI-vel azonos eredményt ad. (Az anthropic SDK helyett álmodul:
    a teszt az SDK nélküli környezetben is fut.)"""
    import sys
    import types

    from gaphopper import llm
    from gaphopper.config import StepModel

    class NotFoundError(Exception):
        status_code = 404

    class BadRequestError(Exception):
        pass

    headers = []

    class Msg:
        stop_reason = "end_turn"
        content = [SimpleNamespace(type="text", text='{"ok": 1}')]
        usage = SimpleNamespace(model_dump=lambda: {"input_tokens": 1, "output_tokens": 1})

    class Stream:
        def __init__(self, ws):
            self.ws = ws

        def __enter__(self):
            if self.ws:
                raise NotFoundError("Workspace `nincs-ilyen` not found.")
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return Msg()

    def fake_client(default_headers=None, **kw):
        ws = (default_headers or {}).get("anthropic-workspace-id")
        headers.append(ws)
        return SimpleNamespace(messages=SimpleNamespace(stream=lambda **k: Stream(ws)))

    class AuthenticationError(Exception):
        status_code = 401

    fake = types.ModuleType("anthropic")
    fake.Anthropic, fake.NotFoundError, fake.BadRequestError = fake_client, NotFoundError, BadRequestError
    fake.AuthenticationError = AuthenticationError
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.setenv("ANTHROPIC_WORKSPACE_ID", "nincs-ilyen")
    monkeypatch.setattr(llm, "_WS_BAD", False)
    m = llm.AnthropicLLM(StepModel("anthropic", "claude-haiku-5-5", None, 100))
    text, _ = m.complete("s", "u")
    assert text == '{"ok": 1}' and headers == ["nincs-ilyen", None] and llm._WS_BAD
    llm.anthropic_client()
    assert headers[-1] is None            # a folyamat további kliensei már fejléc nélkül


def test_web_kesz_pages_api(tmp_path, monkeypatch):
    """KÉSZ-ség-oldal, KÉSZ-ség-sor és Karbantartás végpontjai: olvasás a yaml-ból és a gráfból, írás csak a
    CLI-parancsokon át (tasks.start), UJ-javaslat nevének szerkesztése jóváhagyás előtt."""
    import threading
    import urllib.error
    import urllib.request
    from http.server import ThreadingHTTPServer

    from gaphopper import config, pipeline, web
    from gaphopper.store import Store

    _tmp_torzs(tmp_path, monkeypatch)
    monkeypatch.setenv("GH_STATE_DB", str(tmp_path / "state.sqlite"))
    monkeypatch.setattr(pipeline, "DRYRUN_DIR", tmp_path / "dryrun")
    started = []
    monkeypatch.setattr(web.tasks, "start", lambda label, args, targets=None: started.append(args) or f"t{len(started)}")

    class G:
        err = ""

        def get(self):
            return self

        def read(self, q, limit=5000, **kw):
            if "k.hordozhatosag AS hord" in q:
                return [{"id": "gh_c_19", "csalad": ["javito", "megismero"], "arany": [0.67, 0.33], "hord": "altalanos",
                         "munkakor_db": 5}]
            if "IGENYEL" in q:
                return [{"nivalo_id": "gh_niv_x", "nivalo": "Hiba elhárítása", "tesz_type": "PROBLEM_SOLVE", "suly": 2,
                         "munkakorok": ["j1", "j2"]}]
            if "MunkaKor" in q:
                return [{"id": "j1", "label": "Villanyszerelő"}]
            return []
    monkeypatch.setattr(web, "GRAPH", G())
    S = Store(tmp_path / "state.sqlite")
    q1 = S.enqueue(None, "kesz_review", {"ids": ["gh_kh_a", "gh_kh_b"], "nevek": ["Differenciált oktatás"],
                                          "label_hu": "Egyéni differenciálás", "label_en": "Individual differentiation",
                                          "csoport": "L1", "irany": ["KOZ_TARSAS"], "munkakorok": ["a", "b"]})
    q2 = S.enqueue(None, "kesz_irany_review", {"id": "gh_c_102", "label_hu": "x", "jelenlegi": ["KOZ_SZERVEZETI"],
                                                "javaslat": ["KOZ_TARSAS"]})
    q3 = S.enqueue("job1", "job_review", {})
    (tmp_path / "dryrun").mkdir()
    (tmp_path / "dryrun" / "j.json").write_text(json.dumps({"job_id": "j", "title": "Séf", "ok": True, "kesz_hiany": [
        {"label_hu": "Szaglás", "label_en": "Sense of smell", "csoport": "H10", "irany": ["KOZ_FIZIKAI"],
         "status": "JELOLT", "ok": "", "nivalo": "Étel elkészítése"}], "total": {}}), encoding="utf-8")

    srv = ThreadingHTTPServer(("127.0.0.1", 0), web.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"

    def call(path, method="GET", data=None):
        body = json.dumps(data).encode() if data is not None else None
        req = urllib.request.Request(base + path, data=body, method=method)
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())
    try:
        _, d = call("/api/kesz")
        by = {x["id"]: x for x in d["items"]}
        assert len(by) == len(config.kesz_torzs()) and d["graph_ok"] and "KOZ_EGYEN" in d["iranyok"]
        assert by["gh_c_19"]["hordozhatosag"] == "altalanos" and by["gh_c_19"]["csalad"][0] == "javito"
        assert by["gh_c_181"]["kategoria"] == "SZEM" and by["gh_c_181"]["irany"] == ["KOZ_EGYEN"]
        _, x = call("/api/kesz/gh_c_19")
        assert x["igenyel"][0]["munkakorok"][0] == {"id": "j1", "label": "Villanyszerelő"} and x["igenyel"][0]["suly"] == 2
        assert call("/api/kesz/gh_c_9999")[0] == 404
        _, qd = call("/api/kesz/queue")
        assert [q["id"] for q in qd["queue"]] == [q1, q2]                  # a munkakör-kérés nem ide tartozik
        # UJ: átnevezés jóváhagyás előtt, a régi név alias lesz; a jóváhagyás a CLI review-parancsa
        assert call(f"/api/kesz/queue/{q1}/approve", "POST", {"label_hu": "Egyéni igényekhez igazítás",
                                                             "label_en": "Individual needs adaptation"})[0] == 200
        p = json.loads(next(r for r in S.queue() if r["id"] == q1)["payload_json"])
        assert p["label_hu"] == "Egyéni igényekhez igazítás" and "Egyéni differenciálás" in p["nevek"]
        assert p["aliases_extra"] == ["en:Individual differentiation"] and started[-1] == ["review", "--approve", str(q1)]
        assert call(f"/api/kesz/queue/{q1}/approve", "POST", {"csoport": "X9"})[0] == 400      # sémaellenőrzés
        call(f"/api/kesz/queue/{q2}/approve", "POST", {"irany": ["KOZ_TARSAS", "KOZ_SZERVEZETI"]})
        assert json.loads(next(r for r in S.queue() if r["id"] == q2)["payload_json"])["javaslat"] == \
            ["KOZ_TARSAS", "KOZ_SZERVEZETI"]
        call(f"/api/kesz/queue/{q2}/reject", "POST", {})
        assert started[-1] == ["review", "--reject", str(q2)]
        assert call(f"/api/kesz/queue/{q3}/approve", "POST", {})[0] == 400
        _, rv = call(f"/api/review/{q3}")
        assert rv["tetel"]["nev"] == "Munkakör jóváhagyása" and "gráfba" in rv["tetel"]["jovahagyas"]
        assert qd["queue"][1]["context"]["jelenlegi"] == ["Szervezeti"]
        # karbantartás: ugyanazok a CLI-parancsok
        call("/api/kesz/maint/kesz-bovit", "POST", {"dry_run": True})
        assert started[-1] == ["kesz-bovit", "--dry-run"]
        call("/api/kesz/maint/kesz-bovit", "POST", {"from_dryrun": True, "queue": True})
        assert started[-1][:2] == ["kesz-bovit", "--from-dryrun"] and started[-1][-1] == "--queue"
        call("/api/kesz/maint/kesz-irany", "POST", {"all": True})
        assert started[-1] == ["kesz-irany", "--all"]
        call("/api/kesz/maint/generalize", "POST", {"dry_run": True})
        call("/api/kesz/maint/split", "POST", {"review": 7})
        assert started[-2:] == [["generalize", "--dry-run"], ["split", "--review", "7"]]
        assert call("/api/kesz/maint/db-reset", "POST", {})[0] == 400
        _, dl = call("/api/dryrun")
        assert dl["runs"][0]["kesz_hiany"][0]["label_hu"] == "Szaglás"
        with urllib.request.urlopen(base + "/") as r:
            html = r.read().decode()
        assert "#kesz" in html and "#keszsor" in html and "#karbantartas" in html
    finally:
        srv.shutdown()
        config.kesz_cache_clear()


def test_approve_kesz_keeps_renamed_aliases(tmp_path, monkeypatch):
    from gaphopper import config
    from gaphopper.kesz import approve_kesz
    from gaphopper.pipeline import Context

    _tmp_torzs(tmp_path, monkeypatch)
    try:
        ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False)
        ctx.graph = _KeszGraph([])
        qid = ctx.store.enqueue(None, "kesz_review", {})
        res = approve_kesz(ctx, {"id": qid}, {"ids": ["gh_kh_a"], "label_hu": "Új név", "label_en": "New name",
                                              "csoport": "L1", "irany": ["KOZ_TARSAS"], "nevek": ["Régi név"],
                                              "aliases_extra": ["en:Old name"]})
        assert config.kesz_torzs()[res["id"]]["aliases"] == ["hu:Régi név", "en:Old name"]
    finally:
        config.kesz_cache_clear()


def test_ui_task_refuses_python_without_provider_packages(tmp_path, monkeypatch):
    """2026-11-05: a felületet a base Pythonnal indították (anthropic nélkül), a run 2 mp alatt elbukott.
    A tasks.start most indítás előtt, érthető hibaüzenettel szól; a hibás parancs el sem indul."""
    import importlib.util

    from gaphopper import tasks

    monkeypatch.setattr(tasks, "TASK_DIR", tmp_path)
    real = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda m, *a: None if m == "anthropic" else real(m, *a))
    with pytest.raises(ValueError, match="anthropic.*conda activate gaphopper"):
        tasks.start("x", ["run"])
    assert not list(tmp_path.iterdir())
    monkeypatch.setattr(importlib.util, "find_spec", lambda m, *a: object())
    assert tasks.env_problem() == ""
    # az openai-kompatibilis szolgáltató (pl. E0) nem igényli az openai csomagot (2026-11-05)
    monkeypatch.setattr(importlib.util, "find_spec", lambda m, *a: None if m == "openai" else object())
    assert tasks.env_problem() == "" and "openai" not in tasks._PROVIDER_PKG.values()


def test_kesz_irany_filter_and_stale_reviews(tmp_path, monkeypatch):
    """Új KÖZ-eg (KOZ_TUDOMANYOS, KOZ_NYELVI, 2026-11-05): csak az érintett irányú elemek újravizsgálata; a korábbi
    nyitott irány-kérdéseik ELAVULT állapotba kerülnek."""
    from gaphopper import config
    from gaphopper.kesz import run_kesz_irany
    from gaphopper.pipeline import Context
    from gaphopper.schemas import KOZEG

    assert {"KOZ_TUDOMANYOS", "KOZ_NYELVI"} <= KOZEG and "KOZ_TUDOMANYOS" in prompt("p0_system")
    _tmp_torzs(tmp_path, monkeypatch)
    try:
        seen = []
        dig = {k for k, x in config.kesz_torzs().items() if "KOZ_DIGITALIS" in x["irany"]}
        kid = sorted(dig)[0]
        n = int(kid[5:])

        def ans(sy, u):
            seen.append(u)
            return json.dumps({"elemek": [{"n": n, "irany": ["KOZ_NYELVI"]}]} if f"\n{n}|" in "\n" + u else {"elemek": []})
        ctx = Context(Settings(state_db=tmp_path / "s.sqlite"), with_graph=False, llm_override={"P3": FakeLLM(ans)})
        ctx.graph = _KeszGraph([])
        old = ctx.store.enqueue(None, "kesz_irany_review", {"id": kid, "javaslat": ["KOZ_DIGITALIS"]})
        keep = ctx.store.enqueue(None, "kesz_irany_review", {"id": "gh_c_181", "javaslat": ["KOZ_TARSAS"]})
        out = run_kesz_irany(ctx, chunk=500, only_default=False, iranyok={"KOZ_DIGITALIS"})
        assert out["vizsgalt"] == len(dig) and "|Empátia|" not in seen[0]
        assert config.kesz_torzs()[kid]["irany"] == ["KOZ_NYELVI"] and out["elavult"] == 1
        assert [r["id"] for r in ctx.store.queue()] == [keep] and old != keep
    finally:
        config.kesz_cache_clear()


def test_r21_munkatars_not_stopword():
    """Ápoló, 2026-11-05: "Munkatársak és feladatok koordinálása" ‹Munkatárs›: a munkakörcím-stopszó nem rontja el
    a SZER-ELEM-tő egyezését (R21 nem jelez)."""
    assert rules.stem_overlap("Munkatárs", "Munkatársak és feladatok koordinálása")


def test_review_explain_names_rules_and_elements(tmp_path):
    """Szakértői kérés: a szabály neve és jelentése, az érintett TESZ-nivaló teljes megnevezéssel (nem N08),
    R18-nál a két elem és az eltérés, a P2 előtte/utána, és a jóváhagyás/elutasítás jelentése (2026-11-05)."""
    from gaphopper.review_view import explain, format_text, rule_info
    from gaphopper.store import Store

    assert rule_info("R21")["nev"].startswith("A név nem tartalmazza") and rule_info("G5")["nev"]
    S = Store(tmp_path / "s.sqlite")
    S.upsert_job({"job_id": "j", "title": "Registered nurse"})
    p1 = json.loads((FIX / "p1_nurse.json").read_text(encoding="utf-8"))
    n1, n2 = p1["nivalok"][0], p1["nivalok"][1]
    after = dict(n1, label_hu="Új név a javítás után")
    S.save_artifact("j", "P1", p1, "v")
    S.save_artifact("j", "P2", {"verdict": "FIXED", "final_nivalok": [after] + p1["nivalok"][1:],
                                "final_szinek": p1["szinek"], "changes": [
        {"op": "FIX", "nivalo_id": n1["nivalo_id"], "failed_tests": ["R21"], "reason_hu": "G5"}]}, "v")
    c1 = next(x["cand_id"] for x in p1["szinek"] if x["nivalo_id"] == n2["nivalo_id"])
    qid = S.enqueue("j", "job_review", {"verdict": "FIXED", "flags": [
        {"rule": "R18", "message": "x", "cand_ids": [n1["nivalo_id"], c1]}]})
    x = explain(S, next(r for r in S.queue() if r["id"] == qid))
    f = x["flags"][0]
    assert f["nev"] == "Összevonási jelölt" and [v["label_hu"] for v in f["elemek"]] == ["Új név a javítás után", n2["label_hu"]]
    assert f["elemek"][0]["tesz_type_hu"] and f["elemek"][0]["szinek"] and f["elemek"][0]["kesz"][0]["label_hu"] != f["elemek"][0]["kesz"][0]["id"]
    assert x["valtozasok"][0]["elotte"]["label_hu"] == n1["label_hu"] and x["valtozasok"][0]["utana"]["label_hu"] == "Új név a javítás után"
    assert x["job"]["label_en"] == "Registered nurse" and f["kulonbseg"]
    txt = format_text(x)
    assert "R18 – Összevonási jelölt" in txt and "Jóváhagyás:" in txt and n2["label_hu"] in txt and "előtte:" in txt


def test_anthropic_invalid_env_key_falls_back_to_dotenv(monkeypatch):
    """2026-11-10: a UI-folyamat környezetében exportált, érvénytelen ANTHROPIC_API_KEY felülírta a .env kulcsát
    (401). A hívás egyszer megismétlődik a .env kulcsával; ha az azonos vagy nincs, a hiba marad."""
    import os
    import sys
    import types

    from gaphopper import llm
    from gaphopper.config import StepModel

    class AuthenticationError(Exception):
        status_code = 401

    class NotFoundError(Exception):
        status_code = 404

    keys = []

    class Msg:
        stop_reason = "end_turn"
        content = [SimpleNamespace(type="text", text="ok")]
        usage = SimpleNamespace(model_dump=lambda: {})

    class Stream:
        def __enter__(self):
            if os.environ["ANTHROPIC_API_KEY"] != "jo":
                raise AuthenticationError("invalid x-api-key")
            return self

        def __exit__(self, *a):
            return False

        def get_final_message(self):
            return Msg()

    def fake_client(default_headers=None, **kw):
        keys.append(os.environ["ANTHROPIC_API_KEY"])
        return SimpleNamespace(messages=SimpleNamespace(stream=lambda **k: Stream()))

    fake = types.ModuleType("anthropic")
    fake.Anthropic, fake.AuthenticationError, fake.NotFoundError = fake_client, AuthenticationError, NotFoundError
    fake.BadRequestError = type("BadRequestError", (Exception,), {})
    monkeypatch.setitem(sys.modules, "anthropic", fake)
    monkeypatch.delenv("ANTHROPIC_WORKSPACE_ID", raising=False)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "regi")
    monkeypatch.setattr(llm, "dotenv_api_key", lambda: "jo")
    assert llm.AnthropicLLM(StepModel("anthropic", "m", None, 10)).complete("s", "u")[0] == "ok"
    assert keys == ["regi", "jo"]
    monkeypatch.setenv("ANTHROPIC_API_KEY", "regi")
    monkeypatch.setattr(llm, "dotenv_api_key", lambda: "regi")
    with pytest.raises(AuthenticationError):
        llm.AnthropicLLM(StepModel("anthropic", "m", None, 10)).complete("s", "u")


def test_web_send_ignores_closed_client():
    """2026-11-10: a böngésző a válasz előtt bontotta a kapcsolatot; a szerver dupla tracebacket írt (500 a lezárt
    socketre). Most csendben kihagyja, és utána sem próbál írni."""
    from gaphopper import web

    class W:
        def write(self, b):
            raise BrokenPipeError(32, "Broken pipe")

    h = web.Handler.__new__(web.Handler)
    h.wfile, h.request_version, h.requestline, h.command = W(), "HTTP/1.1", "GET /api/x HTTP/1.1", "GET"
    h._headers_buffer = []
    h.send_response = h.send_header = lambda *a, **k: None
    h.end_headers = lambda: h.wfile.write(b"")
    h._send(200, b"{}", "application/json")
    assert h._closed
    h.json({"error": "x"}, 500)          # nem dob kivételt, nem ír
