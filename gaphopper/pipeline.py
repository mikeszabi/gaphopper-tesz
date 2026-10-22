"""A feldolgozási sor: P1 felbontás -> P2 bírálat -> P3 egyesítés és gráfba írás; P4a horgonyzás."""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import rules
from .config import ROOT, Settings, StepModel, prompt, prompt_version, render, settings
from .embed import get_embedder
from .llm import LLMError, ManualStep, call_json, get_llm
from .merge import Resolver
from .normalize import norm_key, stable_id
from .schemas import P1Output, P2Change, P2Output, P4aOutput, P5Output, Tesz
from .store import Store

MANUAL_DIR = ROOT / "runs" / "manual"


class Context:
    def __init__(self, s: Settings | None = None, with_graph: bool = True,
                 answer_files: dict[str, Path] | None = None, llm_override: dict | None = None):
        self.settings = s or settings()
        self.store = Store(self.settings.state_db)
        self.graph = None
        if with_graph:
            from .graph import Graph

            self.graph = Graph(self.settings)
        self.embedder = get_embedder(self.settings.embed_model, self.settings.embed_provider,
                                     self.settings.ollama_host, self.settings.embed_prefix)
        self.system = prompt("p0_system")
        self.pv = prompt_version()
        self.answer_files = answer_files or {}
        self.llm_override = llm_override or {}
        self.current_job: str | None = None

    def llm(self, step: str):
        if step in self.llm_override:
            return self.llm_override[step]
        cfg: StepModel = getattr(self.settings, step.lower())
        return get_llm(cfg, step, self.answer_files.get(step), self.settings)

    def logger(self, job_id: str | None, step: str, cfg: StepModel):
        def _log(raw: str, ok: bool, usage: dict) -> None:
            self.store.log(job_id, step, cfg.provider, cfg.model, self.pv, "", raw, ok, usage)
        return _log

    def close(self) -> None:
        if self.graph:
            self.graph.close()


# ------------------------------------------------------------------ P1, P2
def _retry_note(ctx: Context, job_id: str, step: str) -> str:
    err = ctx.store.artifact(job_id, f"{step}_ERR")
    if not err:
        return ""
    return ("\n\nAZ ELŐZŐ KÍSÉRLET NEM FELELT MEG A SÉMÁNAK:\n" + err.get("error", "")[:2000]
            + "\nKerüld el ezt a hibát, és a teljes JSON-t add vissza.")


def build_p1(ctx: Context, job: dict) -> str:
    return (render(prompt("p1_felbontas"), job_record_json=json.dumps(job, ensure_ascii=False, separators=(",", ":")))
            + _retry_note(ctx, job["job_id"], "P1"))


def _sampled(job_id: str, share: float) -> bool:
    """Determinisztikus véletlen minta: ugyanaz a munkakör mindig ugyanúgy esik."""
    h = int(hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < share


def p2_needed(ctx: Context, job: dict, p1: P1Output) -> tuple[bool, str, list]:
    """Kell-e bírálat? auto módban csak szabályjelzésnél és a véletlen mintán."""
    flags = rules.check(p1.tesz, [k.kor_id for k in p1.tesz_korok], job.get("field", ""))
    mode = ctx.settings.p2_mode
    if mode == "always":
        return True, "mindig", flags
    if mode == "never":
        return False, "kikapcsolva", flags
    if flags:
        return True, "szabályjelzés: " + ", ".join(sorted({f.rule for f in flags})), flags
    if _sampled(job["job_id"], ctx.settings.p2_sample):
        return True, f"minőségi minta ({ctx.settings.p2_sample:.0%})", flags
    return False, "szabályok rendben", flags


def finish_p1(ctx: Context, job: dict, obj: P1Output) -> P1Output:
    obj.job_id = job["job_id"]  # a rekord azonosítója a mérvadó, nem a modellé
    ctx.store.save_artifact(job["job_id"], "P1", obj.model_dump(), ctx.pv)
    ctx.store.clear_artifact(job["job_id"], "P1_ERR")
    need, reason, flags = p2_needed(ctx, job, obj)
    if need:
        ctx.store.set_status(job["job_id"], "P1_KESZ", f"P2 kell: {reason}")
        return obj
    # A bírálat kimarad: a P1 lista a végleges.
    art = {"verdict": "SKIPPED", "p2_reason": reason, "changes": [], "warnings": [],
           "final_tesz": [t.model_dump() for t in obj.tesz],
           "rule_flags_before": [f.as_dict() for f in flags], "rule_flags_after": [f.as_dict() for f in flags]}
    ctx.store.save_artifact(job["job_id"], "P2", art, ctx.pv)
    if rules.needs_expert(flags):
        ctx.store.enqueue(job["job_id"], "job_review", {"verdict": "SKIPPED", "flags": art["rule_flags_after"],
                                                         "questions": [], "gaps": []})
        ctx.store.set_status(job["job_id"], "SZAKERTORE", f"P2 kimaradt ({reason}); szakértői jelzés")
    else:
        ctx.store.set_status(job["job_id"], "P2_ELFOGADVA", f"P2 kimaradt: {reason}")
    return obj


def record_error(ctx: Context, job_id: str, step: str, error: str, back_to: str) -> None:
    """Sémahiba vagy API-hiba: a rekord visszaáll, a hiba a következő kísérlet promptjába kerül."""
    ctx.store.save_artifact(job_id, f"{step}_ERR", {"error": error}, ctx.pv)
    ctx.store.set_status(job_id, back_to, f"{step} hiba: {error[:300]}")


def run_p1(ctx: Context, job: dict) -> P1Output:
    try:
        obj, _ = call_json(ctx.llm("P1"), ctx.system, build_p1(ctx, job), P1Output,
                           log=ctx.logger(job["job_id"], "P1", ctx.settings.p1))
    except LLMError as exc:
        record_error(ctx, job["job_id"], "P1", str(exc), "NYERS")
        raise
    return finish_p1(ctx, job, obj)


_SLIM_DROP = ("rationale_hu", "attestation_hu", "rubric_hu", "failure_mode_hu")


def build_p2(ctx: Context, job: dict, p1: P1Output) -> tuple[str, list]:
    kor_ids = [k.kor_id for k in p1.tesz_korok]
    flags = rules.check(p1.tesz, kor_ids, job.get("field", ""))
    p1_slim = p1.model_dump(exclude={"skill_mentions"})
    for t in p1_slim["tesz"]:
        for k in _SLIM_DROP:
            t.pop(k, None)
    user = render(prompt("p2_biralat"),
                  job_record_json=json.dumps(job, ensure_ascii=False),
                  p1_json=json.dumps(p1_slim, ensure_ascii=False),
                  rule_flags_json=json.dumps([f.as_dict() for f in flags], ensure_ascii=False))
    return user + _retry_note(ctx, job["job_id"], "P2"), flags


def apply_p2(p1_tesz: list[Tesz], changes: list[P2Change]) -> tuple[list[Tesz], list[str]]:
    """A bírálat eltéréseinek alkalmazása a P1-listára. Hibás eltérésnél ValueError (újrapróbálás)."""
    order = [t.cand_id for t in p1_tesz]
    items: dict[str, dict] = {t.cand_id: t.model_dump() for t in p1_tesz}
    warnings: list[str] = []
    used = set(order)

    def fresh_id(cid: str) -> str:
        if cid and cid not in used:
            used.add(cid)
            return cid
        n = len(used) + 1
        while f"C{n:02d}" in used:
            n += 1
        used.add(f"C{n:02d}")
        return f"C{n:02d}"

    for ch in changes:
        if ch.op in ("FIX", "DROP", "MERGE", "SPLIT") and ch.cand_id not in items:
            if ch.cand_id in order:  # már elhagyott elemre vonatkozik
                warnings.append(f"{ch.op} {ch.cand_id}: az elem már nincs a listában.")
                continue
            raise ValueError(f"{ch.op}: ismeretlen cand_id '{ch.cand_id}'. Csak a P1 cand_id-jeire hivatkozz.")
        if ch.op == "FIX":
            merged = items[ch.cand_id] | {k: v for k, v in ch.set.items() if k != "cand_id"}
            try:
                items[ch.cand_id] = Tesz.model_validate(merged).model_dump()
            except Exception as exc:
                raise ValueError(f"FIX {ch.cand_id}: a javított TESZ nem érvényes: {exc}")
        elif ch.op == "DROP":
            items.pop(ch.cand_id)
        elif ch.op == "MERGE":
            if ch.into not in items:
                raise ValueError(f"MERGE {ch.cand_id}: az 'into' ('{ch.into}') nem létező cand_id.")
            items.pop(ch.cand_id)
        elif ch.op in ("SPLIT", "ADD"):
            if not ch.new:
                raise ValueError(f"{ch.op}: a 'new' lista üres; add meg a teljes új TESZ-eket.")
            pos = order.index(ch.cand_id) + 1 if ch.op == "SPLIT" else len(order)
            if ch.op == "SPLIT":
                items.pop(ch.cand_id)
            for k, t in enumerate(ch.new):
                d = t.model_dump()
                d["cand_id"] = fresh_id(d.get("cand_id", ""))
                items[d["cand_id"]] = d
                order.insert(pos + k, d["cand_id"])
    final = [Tesz.model_validate(items[c]) for c in order if c in items]
    if not final:
        raise ValueError("A javítások után egyetlen TESZ sem maradt.")
    return final, warnings


def finish_p2(ctx: Context, job: dict, p1: P1Output, obj: P2Output, flags: list) -> dict:
    kor_ids = [k.kor_id for k in p1.tesz_korok]
    final, warnings = apply_p2(p1.tesz, obj.changes)
    flags2 = rules.check(final, kor_ids, job.get("field", ""))
    art = obj.model_dump() | {"final_tesz": [t.model_dump() for t in final], "warnings": warnings,
                              "rule_flags_before": [f.as_dict() for f in flags],
                              "rule_flags_after": [f.as_dict() for f in flags2]}
    ctx.store.save_artifact(job["job_id"], "P2", art, ctx.pv)
    ctx.store.clear_artifact(job["job_id"], "P2_ERR")
    if obj.verdict == "EXPERT" or flags2:
        ctx.store.enqueue(job["job_id"], "job_review", {
            "verdict": obj.verdict, "flags": [f.as_dict() for f in flags2],
            "questions": obj.expert_questions_hu, "gaps": obj.coverage_gaps_hu})
        ctx.store.set_status(job["job_id"], "SZAKERTORE",
                             "; ".join(f"{f.rule}: {f.message}" for f in flags2)[:500])
    else:
        ctx.store.set_status(job["job_id"], "P2_ELFOGADVA")
    return art


def run_p2(ctx: Context, job: dict, p1: P1Output) -> dict:
    user, flags = build_p2(ctx, job, p1)
    try:
        obj, _ = call_json(ctx.llm("P2"), ctx.system, user, P2Output,
                           log=ctx.logger(job["job_id"], "P2", ctx.settings.p2),
                           check=lambda o: apply_p2(p1.tesz, o.changes))
    except LLMError as exc:
        record_error(ctx, job["job_id"], "P2", str(exc), "P1_KESZ")
        raise
    return finish_p2(ctx, job, p1, obj, flags)


def load_p1(ctx: Context, job_id: str) -> P1Output:
    return P1Output.model_validate(ctx.store.artifact(job_id, "P1"))


# ------------------------------------------------------------------ P3 + gráf
def write_graph(ctx: Context, job: dict, p1_meta: dict, final: list[Tesz]) -> dict:
    if ctx.graph is None:
        raise RuntimeError("A gráfba íráshoz neo4j-kapcsolat kell.")
    ctx.current_job = job["job_id"]
    import time

    started = time.monotonic()
    res = Resolver(ctx)
    jid = res.resolve_job(job, p1_meta)
    # 1. gyors út minden elemre, a kétes esetek gyűjtése
    for t in final:
        res.plan_szer(t.primary_szer)
        for s in t.secondary_szer:
            res.plan_szer(s)
    planned = [res.plan_tesz(t) for t in final]
    # 2. egyetlen P3-hívás a kétes esetekre
    planned_at = time.monotonic()
    res.decide()
    decided_at = time.monotonic()
    # 3. írás
    ctx.graph.clear_job_links(jid)
    summary = {"job_id": jid, "tesz": [], "review": [], "p3_cases": len(res.cases)}
    for t, plan in zip(final, planned):
        p = res.szer(t.primary_szer)
        secondary = []
        for s in t.secondary_szer:
            rs = res.szer(s)
            secondary.append({"id": rs.entity_id, "role": s.role,
                              "kozeg": s.kozeg or t.kozeg, "helyzet": s.helyzet or t.helyzet})
            if rs.needs_review:
                summary["review"].append({"kind": "szer", "label": s.label_hu, "id": rs.entity_id})
        rt = res.tesz(t, plan, p.entity_id)
        labels = [(t.label_hu, "hu"), (t.label_en, "en")] + rt.extra_aliases
        if rt.decision in ("ALIAS", "SAME"):
            ctx.graph.add_aliases("TESZ", rt.entity_id, [(x, l) for x, l in labels if x])
        else:
            props = {"label_hu": t.label_hu, "label_en": t.label_en, "outcome_hu": t.outcome_hu,
                     "status": "AI_ELLENORZOTT", "prompt_version": ctx.pv}
            if t.rubric_hu:
                props["rubric_hu"] = t.rubric_hu
            if t.attestation_hu:
                props["attestation_hu"] = t.attestation_hu
            ctx.graph.upsert_tesz(
                rt.entity_id, props, t.tesz_type,
                {"id": p.entity_id, "role": t.primary_szer.role, "kozeg": t.kozeg, "helyzet": t.helyzet},
                [(t.kozeg, True)] + [(k, False) for k in t.kozeg_secondary],
                [(t.helyzet, True)] + ([(t.helyzet_secondary, False)] if t.helyzet_secondary else []),
                secondary, variant_of=rt.target_id if rt.decision == "VARIANT" else None)
            ctx.graph.add_aliases("TESZ", rt.entity_id, [(x, l) for x, l in labels if x])
            if ctx.embedder.enabled:
                ctx.graph.set_embedding("TESZ", rt.entity_id,
                                        res.encode(f"{t.label_hu} | {t.label_en} | {t.outcome_hu}"))
        for text, lang in labels:
            if text:
                ctx.store.alias_add(norm_key(text, lang), lang, text, "TESZ", rt.entity_id, rt.via)
        ctx.graph.link_job_tesz(jid, rt.entity_id, {
            "relevance": t.relevance, "expected_eqf": t.expected_eqf, "frequency": t.frequency,
            "kor_id": t.kor_id, "cand_id": t.cand_id, "confidence": t.confidence,
            "prompt_version": ctx.pv})
        summary["tesz"].append({"cand_id": t.cand_id, "tesz_id": rt.entity_id, "decision": rt.decision,
                                "via": rt.via})
        if rt.needs_review or p.needs_review:
            summary["review"].append({"kind": "tesz", "label": t.label_hu, "id": rt.entity_id})
    if summary["review"]:
        ctx.store.enqueue(job["job_id"], "merge_review", {"items": summary["review"]})
    summary["timings"] = {
        "planning_seconds": round(planned_at - started, 3),
        "decision_seconds": round(decided_at - planned_at, 3),
        "writing_seconds": round(time.monotonic() - decided_at, 3),
        **res.timings,
    }
    print(f"P3 {job['job_id']}: {summary['p3_cases']} kétes eset; "
          f"idők (mp): {summary['timings']}", flush=True)
    ctx.store.save_artifact(job["job_id"], "P3", summary, ctx.pv)
    ctx.store.set_status(job["job_id"], "GRAFBAN")
    ctx.store.clear_artifact(job["job_id"], "P3_ERR")
    return summary


# ------------------------------------------------------------------ P5: tanúsítási szöveg
def run_p5(ctx: Context, limit: int = 1000, chunk: int = 10, log=print) -> dict:
    """Igazolási mondat és rubrika a gráf kanonikus TESZ-eihez, amelyeknek még nincs (egyszer, nem
    munkakörönként)."""
    rows = ctx.graph.tesz_without_rubric(limit)
    stat = {"tesz": len(rows), "kesz": 0, "hiba": 0}
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        user = render(prompt("p5_tanusitas"), tesz_list_json=json.dumps(part, ensure_ascii=False))
        ids = {r["id"] for r in part}

        def check(o: P5Output):
            missing = ids - {x.id for x in o.items}
            if missing:
                raise ValueError(f"Hiányzó id-k: {sorted(missing)}")

        try:
            obj, _ = call_json(ctx.llm("P5"), ctx.system, user, P5Output,
                               log=ctx.logger(None, "P5", ctx.settings.p5), check=check)
        except LLMError as exc:
            stat["hiba"] += len(part)
            log(f"P5 hiba ({len(part)} TESZ): {exc}")
            continue
        for it in obj.items:
            if it.id in ids:
                ctx.graph.set_certificate(it.id, it.attestation_hu, it.rubric_hu, ctx.pv)
                stat["kesz"] += 1
        log(f"P5: {stat['kesz']}/{stat['tesz']}")
    return stat


# ------------------------------------------------------------------ vezérlés
def _write_manual(job_id: str, ms: ManualStep) -> Path:
    d = MANUAL_DIR / job_id.replace(":", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ms.step}_system.md").write_text(ms.system, encoding="utf-8")
    (d / f"{ms.step}_user.md").write_text(ms.user, encoding="utf-8")
    return d


def process_job(ctx: Context, job_id: str, until: str = "graph", force: bool = False) -> str:
    """Egy munkakör végigvitele. Visszaadja az új állapotot (vagy 'MANUAL:<mappa>')."""
    job = ctx.store.job(job_id)
    status = ctx.store.status(job_id)
    if (status in ("GRAFBAN", "ELVETVE") or status.endswith("_KOTEGBEN")) and not force:
        return status
    ctx.current_job = job_id
    try:
        p1_data = ctx.store.artifact(job_id, "P1")
        fresh = force or not p1_data or status == "NYERS"
        p1 = run_p1(ctx, job) if fresh else P1Output.model_validate(p1_data)
        if until == "p1":
            return ctx.store.status(job_id)
        status = ctx.store.status(job_id)
        p2_art = ctx.store.artifact(job_id, "P2")
        if not (status in ("P2_ELFOGADVA", "SZAKERTORE") and p2_art and (fresh or not force)):
            p2_art = run_p2(ctx, job, p1)
        if until == "p2" or ctx.store.status(job_id) == "SZAKERTORE":
            return ctx.store.status(job_id)
        final = [Tesz.model_validate(t) for t in p2_art["final_tesz"]]
        write_graph(ctx, job, p1.model_dump(), final)
        return ctx.store.status(job_id)
    except ManualStep as ms:
        d = _write_manual(job_id, ms)
        return f"MANUAL:{d}"


def approve_job(ctx: Context, queue_id: int, final_override: list[dict] | None = None) -> dict:
    """Szakértői jóváhagyás: a (szerkesztett) végső lista a gráfba kerül."""
    row = next(r for r in ctx.store.queue() if r["id"] == queue_id)
    job = ctx.store.job(row["job_id"])
    p1 = ctx.store.artifact(row["job_id"], "P1") or {}
    p2 = ctx.store.artifact(row["job_id"], "P2") or {}
    final = [Tesz.model_validate(t) for t in (final_override or p2.get("final_tesz", []))]
    summary = write_graph(ctx, job, p1, final)
    ctx.store.decide(queue_id, "JOVAHAGYVA", {"final_override": bool(final_override)})
    return summary


# ------------------------------------------------------------------ P4a
ANCHOR_QUERIES = {
    "KOZEG": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:KOZEGBEN_ZAJLIK {primary: true}]->(:Kozeg {id: $v})",
    "HELYZET": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:HELYZETBEN_VEGZENDO {primary: true}]->(:Helyzet {id: $v})",
    "TESZ_TIPUS": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:TIPUSA]->(:TeszTipus {id: $v})",
    "SZER_ELEM": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:IRANYUL|HASZNAL]->(:SzerElem {id: $v})",
    "SZIN": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[r:IRANYUL]->(:SzerElem) "
            "WHERE r.kozeg_id + '|' + r.helyzet_id = $v",
}


def run_p4a(ctx: Context, anchor_type: str, anchor_value: str, runs: int = 3, limit: int = 40) -> dict:
    q = ANCHOR_QUERIES[anchor_type] + (
        " WITH t, collect(DISTINCT m.label_hu) AS jobs "
        "OPTIONAL MATCH (t)-[r:IRANYUL]->(s:SzerElem) OPTIONAL MATCH (t)-[:TIPUSA]->(ty) "
        "RETURN t.id AS id, t.label_hu AS label_hu, t.outcome_hu AS outcome_hu, ty.id AS tesz_type, "
        "s.label_hu AS primary_szer, r.kozeg_id AS kozeg, r.helyzet_id AS helyzet, jobs LIMIT $limit")
    tesz = ctx.graph.run(q, v=anchor_value, limit=limit)
    if not tesz:
        raise ValueError("Ehhez a horgonyhoz nincs TESZ-nivaló a gráfban.")
    existing = ctx.graph.fulltext_candidates("KESZSEG", anchor_value.replace("_", " "), k=10)
    user = render(prompt("p4a_keszseg"), anchor_type=anchor_type, anchor_value=anchor_value,
                  anchor_tesz_list_json=json.dumps(tesz, ensure_ascii=False),
                  skill_candidates_json=json.dumps(existing, ensure_ascii=False, default=str),
                  skill_mentions_json="[]")
    seen: dict[str, dict] = {}
    for _ in range(runs):
        obj, _ = call_json(ctx.llm("P4"), ctx.system, user, P4aOutput,
                           log=ctx.logger(None, "P4a", ctx.settings.p4))
        for sk in obj.skills:
            key = norm_key(sk.label_hu, "hu")
            entry = seen.setdefault(key, {"skill": sk, "hits": 0})
            entry["hits"] += 1
    n = len(tesz)
    written = []
    for key, e in seen.items():
        sk = e["skill"]
        share = len(set(sk.covered_tesz_ids) & {t["id"] for t in tesz}) / n
        # 2. kapu (részben): ismétlődés a futások között + lefedettség a horgony TESZ-ein
        status = "ELLENORZOTT" if e["hits"] >= math.ceil(2 * runs / 3) and share >= 0.7 else "JELOLT"
        sid = sk.skill_id or stable_id("gh_skill", sk.label_hu)
        ctx.graph.run("MERGE (k:Keszseg {id: $id}) ON CREATE SET k.label_hu = $hu, k.label_en = $en, "
                      "k.definition_hu = $d, k.kep_elemek = $kep, k.tud_elemek = $tud, k.status = $st "
                      "SET k.viselkedes_hu = $vis",
                      id=sid, hu=sk.label_hu, en=sk.label_en, d=sk.definition_hu, kep=sk.kep_elemek,
                      tud=sk.tud_elemek, st=status, vis=sk.viselkedes_hu)
        ctx.graph.add_aliases("KESZSEG", sid, [(sk.label_hu, "hu"), (sk.label_en, "en")])
        props = {"erosseg": sk.erosseg, "aspects": ["|".join(a) for a in sk.aspects],
                 "covered_share": round(share, 2), "runs_hit": e["hits"], "runs": runs, "status": status}
        if anchor_type == "SZIN":
            kz, hz = anchor_value.split("|")
            for lbl, v in (("Kozeg", kz), ("Helyzet", hz)):
                ctx.graph.run(f"MATCH (k:Keszseg {{id: $id}}), (a:{lbl} {{id: $v}}) "
                              "MERGE (k)-[h:HORGONYOZ {szin: $szin}]->(a) SET h += $p",
                              id=sid, v=v, szin=anchor_value, p=props)
        else:
            lbl = {"KOZEG": "Kozeg", "HELYZET": "Helyzet", "TESZ_TIPUS": "TeszTipus", "SZER_ELEM": "SzerElem"}[anchor_type]
            ctx.graph.run(f"MATCH (k:Keszseg {{id: $id}}), (a:{lbl} {{id: $v}}) "
                          "MERGE (k)-[h:HORGONYOZ]->(a) SET h += $p", id=sid, v=anchor_value, p=props)
        written.append({"id": sid, "label_hu": sk.label_hu, "status": status, "share": round(share, 2),
                        "hits": e["hits"]})
    return {"anchor": f"{anchor_type}={anchor_value}", "tesz_db": n, "skills": written}
