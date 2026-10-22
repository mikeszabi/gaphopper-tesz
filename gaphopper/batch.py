"""Tömeges futtatás.

1. Kötegelt (batch) mód – Anthropic Message Batches API: a P1 és P2 kérések egyben mennek be,
   aszinkron, kedvezményes áron futnak; a választ később gyűjtjük be.
       submit  -> a rekordok P1_KOTEGBEN / P2_KOTEGBEN állapotba kerülnek
       status  -> a kötegek állapota
       collect -> a kész kötegek eredményei validálva bekerülnek; a hibás rekordok visszaállnak,
                  és a hibaüzenet a következő kísérlet promptjába kerül
2. Párhuzamos mód – bármelyik szolgáltatóval (Gemini, helyi Qwen ...): N szál hívja az API-t, az írás egy
   szálon fut.
"""
from __future__ import annotations

import hashlib
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from .llm import LLMError, anthropic_extra, call_json, extract_json
from .pipeline import (Context, apply_p2, build_p1, build_p2, finish_p1, finish_p2, load_p1, record_error)
from .schemas import P1Output, P2Output

SCHEMA = {"P1": P1Output, "P2": P2Output}
ELIGIBLE = {"P1": "NYERS", "P2": "P1_KESZ"}
PENDING = {"P1": "P1_KOTEGBEN", "P2": "P2_KOTEGBEN"}
BACK_TO = {"P1": "NYERS", "P2": "P1_KESZ"}


def custom_id(step: str, job_id: str) -> str:
    """Az API custom_id-je csak [a-zA-Z0-9_-] lehet, legfeljebb 64 karakter."""
    return f"{step}-{hashlib.sha1(job_id.encode('utf-8')).hexdigest()[:24]}"


def _eligible(ctx: Context, step: str, job_ids: list[str] | None, limit: int | None) -> list[str]:
    if job_ids:
        ids = [j for j in job_ids if ctx.store.status(j) == ELIGIBLE[step]]
    else:
        ids = [r["job_id"] for r in ctx.store.jobs(ELIGIBLE[step])]
    return ids[:limit] if limit else ids


def _build(ctx: Context, step: str, job_id: str) -> tuple[str, dict]:
    job = ctx.store.job(job_id)
    if step == "P1":
        return build_p1(ctx, job), {}
    p1 = load_p1(ctx, job_id)
    user, flags = build_p2(ctx, job, p1)
    return user, {"p1": p1, "flags": flags}


def _finish(ctx: Context, step: str, job_id: str, obj, extra: dict) -> str:
    job = ctx.store.job(job_id)
    if step == "P1":
        finish_p1(ctx, job, obj)
    else:
        if "p1" not in extra:
            extra = {"p1": load_p1(ctx, job_id), "flags": _build(ctx, "P2", job_id)[1]["flags"]}
        finish_p2(ctx, job, extra["p1"], obj, extra["flags"])
    return ctx.store.status(job_id)


# ------------------------------------------------------------------ kötegelt (Anthropic)
def _client():
    from .llm import anthropic_client

    return anthropic_client()


def submit(ctx: Context, step: str, job_ids: list[str] | None = None, limit: int | None = None,
           max_per_batch: int = 2000, client=None) -> list[str]:
    cfg = getattr(ctx.settings, step.lower())
    if cfg.provider != "anthropic":
        raise LLMError(f"A kötegelt mód most Anthropic-kal működik; a {step} szolgáltatója: {cfg.provider}. "
                       "Használd a párhuzamos módot: run --workers N")
    ids = _eligible(ctx, step, job_ids, limit)
    if not ids:
        return []
    client = client or _client()
    system = [{"type": "text", "text": ctx.system, "cache_control": {"type": "ephemeral"}}]
    batch_ids = []
    for start in range(0, len(ids), max_per_batch):
        part = ids[start:start + max_per_batch]
        requests, mapping = [], {}
        for jid in part:
            user, _ = _build(ctx, step, jid)
            params = {"model": cfg.model, "max_tokens": cfg.max_tokens, "system": system,
                      "messages": [{"role": "user", "content": user}], **anthropic_extra(cfg)}
            if ctx.settings.batch_send_temperature and cfg.temperature is not None:
                params["temperature"] = cfg.temperature
            cid = custom_id(step, jid)
            requests.append({"custom_id": cid, "params": params})
            mapping[cid] = jid
        b = client.messages.batches.create(requests=requests)
        ctx.store.save_batch(b.id, step, "anthropic", cfg.model, ctx.pv, mapping)
        for jid in part:
            ctx.store.set_status(jid, PENDING[step], f"köteg: {b.id}")
        batch_ids.append(b.id)
    return batch_ids


def status(ctx: Context, client=None) -> list[dict]:
    client = client or _client()
    out = []
    for row in ctx.store.batches(only_open=True):
        b = client.messages.batches.retrieve(row["batch_id"])
        counts = getattr(b, "request_counts", None)
        out.append({"batch_id": row["batch_id"], "step": row["step"], "status": b.processing_status,
                    "counts": counts.model_dump() if hasattr(counts, "model_dump") else counts})
    return out


def collect(ctx: Context, client=None) -> dict:
    """A befejezett kötegek eredményeinek begyűjtése. Visszaadja: {'ok': n, 'hiba': n, 'fut': n}."""
    import json as _json

    client = client or _client()
    stat = {"ok": 0, "hiba": 0, "fut": 0}
    for row in ctx.store.batches(only_open=True):
        b = client.messages.batches.retrieve(row["batch_id"])
        if b.processing_status != "ended":
            stat["fut"] += 1
            continue
        step, mapping = row["step"], _json.loads(row["map_json"])
        cfg = getattr(ctx.settings, step.lower())
        seen = set()
        for r in client.messages.batches.results(row["batch_id"]):
            jid = mapping.get(r.custom_id)
            if not jid:
                continue
            seen.add(jid)
            if r.result.type != "succeeded":
                err = getattr(r.result, "error", None)
                record_error(ctx, jid, step, f"köteg: {r.result.type} {err}", BACK_TO[step])
                stat["hiba"] += 1
                continue
            text = "".join(getattr(c, "text", "") for c in r.result.message.content
                           if getattr(c, "type", "text") == "text")
            usage = r.result.message.usage
            usage = usage.model_dump() if hasattr(usage, "model_dump") else {}
            try:
                obj = SCHEMA[step].model_validate(extract_json(text))
                if step == "P2":  # az eltérések alkalmazhatók-e
                    p1x = load_p1(ctx, jid)
                    apply_p2(p1x.tesz_magok, p1x.tesz, obj.changes)
            except Exception as exc:  # séma-, JSON- vagy eltéréshiba
                ctx.store.log(jid, step, "anthropic-batch", cfg.model, ctx.pv, "", text, False, usage)
                record_error(ctx, jid, step, str(exc)[:3000], BACK_TO[step])
                stat["hiba"] += 1
                continue
            ctx.store.log(jid, step, "anthropic-batch", cfg.model, ctx.pv, "", text, True, usage)
            _finish(ctx, step, jid, obj, {})
            stat["ok"] += 1
        for jid in set(mapping.values()) - seen:  # eredmény nélkül maradt kérések
            record_error(ctx, jid, step, "köteg: nincs eredmény", BACK_TO[step])
            stat["hiba"] += 1
        ctx.store.close_batch(row["batch_id"])
    return stat


def wait_and_collect(ctx: Context, poll_seconds: int = 60, client=None) -> dict:
    client = client or _client()
    total = {"ok": 0, "hiba": 0}
    while True:
        s = collect(ctx, client)
        total["ok"] += s["ok"]
        total["hiba"] += s["hiba"]
        if s["fut"] == 0:
            return total
        time.sleep(poll_seconds)


# ------------------------------------------------------------------ párhuzamos (bármely szolgáltató)
def run_parallel(ctx: Context, step: str, job_ids: list[str] | None = None, limit: int | None = None,
                 workers: int = 4) -> dict:
    ids = _eligible(ctx, step, job_ids, limit)
    if not ids:
        return {"ok": 0, "hiba": 0}
    llm = ctx.llm(step)
    cfg = getattr(ctx.settings, step.lower())
    prepared = {jid: _build(ctx, step, jid) for jid in ids}  # SQLite csak a fő szálon
    stat = {"ok": 0, "hiba": 0}

    def work(jid: str):
        logs: list = []
        user, extra = prepared[jid]
        check = (lambda o: apply_p2(extra["p1"].tesz_magok, extra["p1"].tesz, o.changes)) if step == "P2" else None
        try:
            obj, _ = call_json(llm, ctx.system, user, SCHEMA[step],
                               log=lambda raw, ok, usage: logs.append((raw, ok, usage)), check=check)
            return jid, obj, None, logs
        except Exception as exc:
            return jid, None, str(exc), logs

    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut in as_completed([ex.submit(work, j) for j in ids]):
            jid, obj, err, logs = fut.result()
            for raw, ok, usage in logs:
                ctx.store.log(jid, step, cfg.provider, cfg.model, ctx.pv, "", raw, ok, usage)
            if err:
                record_error(ctx, jid, step, err, BACK_TO[step])
                stat["hiba"] += 1
            else:
                _finish(ctx, step, jid, obj, prepared[jid][1])
                stat["ok"] += 1
    return stat
