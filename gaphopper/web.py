"""Webes felület: kis JSON API a gaphopper csomag fölött + a web/ mappa statikus JavaScript-oldala.

Indítás: python -m gaphopper ui [--port 8765]   ->   http://localhost:8765
Csak a standard könyvtárat használja; alapból csak a saját gépről érhető el (127.0.0.1).
"""
from __future__ import annotations

import difflib
import json
import mimetypes
import re
import sqlite3
import threading
import time
import traceback
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

from . import tasks
from .config import (MODELS_PATH, PRICES, MODEL_CATALOG, GEMINI_PRICE_NOTE, PROMPT_DIR, PROVIDERS, ROOT, STEP_NAMES, STEPS, VOCAB_PATH, price,
                     prompt_version, save_ui_models, settings, ui_models)
from .store import Store

WEB_DIR = ROOT / "web"
UPLOAD_DIR = ROOT / "data" / "uploads"
DATA_DIR = ROOT / "data"
HISTORY = PROMPT_DIR / ".history"
DOC_EXT = {".pdf", ".docx", ".txt", ".md", ".html", ".htm", ".odt", ".rtf"}
REC_EXT = {".jsonl", ".json", ".csv", ".xlsx"}

PHASES = [  # a felületen látható fázisok sorrendben
    {"key": "E0", "name": "Kinyerés", "about": "munkakör kiolvasása dokumentumból (csak dokumentumból jött rekordnál)"},
    {"key": "P1", "name": "Felbontás", "about": "TESZ-nivalók és SZÍN-ezett változataik (TESZ-SZÍN-ek)"},
    {"key": "P2", "name": "Bírálat", "about": "független ellenőrzés; auto módban csak jelzésnél vagy mintában"},
    {"key": "P3", "name": "Gráfba írás", "about": "TESZ-nivaló-egyesítés (P3), SZÍN-egyezés a kódban, írás a neo4j-be"},
    {"key": "P5", "name": "Tanúsítás", "about": "TESZ-nivaló-rubrika, változatonként igazolás és SZÍN-rubrika"},
]
PROMPT_STEP = {  # a felület ebben a sorrendben mutatja
    "p0_system": ("P1, P2, P4, P5", "közös rendszerprompt: fogalmak, zárt listák, szabályok"),
    "e0_system": ("E0", "kinyerés: rendszerprompt"),
    "e0_kinyeres": ("E0", "kinyerés: munkakörök egy dokumentumrészletből"),
    "p1_felbontas": ("P1", "munkakör felbontása TESZ-nivalókra és SZÍN-ezett változatokra"),
    "p2_biralat": ("P2", "bírálat, csak az eltéréseket adja vissza"),
    "p3_system": ("P3", "az egyesítő rövid rendszerpromptja"),
    "p3_egyesites": ("P3", "a kétes TESZ-nivaló- és SZER-ELEM-egyesítések, egy hívásban"),
    "p4a_keszseg": ("P4", "KÉSZ-ség-horgonyzás"),
    "p4b_eqf": ("P4", "EQF-szintleírók"),
    "p5_tanusitas": ("P5", "TESZ-nivaló-rubrika és változatonként igazolás + SZÍN-rubrika"),
    "vocab": ("mind", "zárt szótárak és szabálykuszöbök (config/vocab.yaml)"),
}
DECISION_HU = {"NEW": "új elem", "SAME": "már megvolt, összekapcsolva", "ALIAS": "már megvolt (név alapján)",
               "VARIANT": "új változat egy meglévőhöz", "NARROWER": "szűkebb elem", "BROADER": "tágabb, meglévő elem"}
CYPHER = {
    "Csomópontok típus szerint": "MATCH (n) RETURN labels(n)[0] AS tipus, count(*) AS db ORDER BY db DESC",
    "Legtöbb munkakörben szereplő TESZ-nivaló": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(:TeszSzin)-[:SZINEZI]->(g:TeszNivalo) "
                                             "RETURN g.id AS id, g.label_hu AS mag, count(DISTINCT m) AS munkakor_db "
                                             "ORDER BY munkakor_db DESC LIMIT 50",
    "TESZ-nivalók mélyítési létrája": "MATCH (g:TeszNivalo)<-[:SZINEZI]-(t:TeszSzin)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h:Helyzet) "
                                "WITH g, collect(h.id + ' (' + coalesce(toString(h.fok), h.tengely) + ')') AS szinek, "
                                "count(t) AS v WHERE v > 1 RETURN g.label_hu AS mag, v AS valtozat, szinek "
                                "ORDER BY v DESC LIMIT 100",
    "Mélyítési jelöltek": "MATCH (g:TeszNivalo)-[e:MELYITESI_JELOLT]->(h:Helyzet) "
                          "RETURN g.label_hu AS mag, e.kozeg + '|' + h.id AS szin, e.indok_hu AS indok, "
                          "e.forras_munkakor AS munkakor",
    "SZER-ELEM-ek használat szerint": "MATCH (x)-[:IRANYUL|HASZNAL]->(s:SzerElem) "
                                      "RETURN s.id AS id, s.label_hu AS szer_elem, count(x) AS hasznalat "
                                      "ORDER BY hasznalat DESC LIMIT 100",
    "TESZ-típus × SZÍN": "MATCH (:MunkaKor)-[:TARTALMAZ_TESZT]->(t)-[:SZINEZI]->(:TeszNivalo)-[:TIPUSA]->(ty), "
                         "(t)-[:KOZEGBEN_ZAJLIK {primary: true}]->(k), (t)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h) "
                         "RETURN ty.id AS tipus, k.id + '|' + h.id AS szin, count(*) AS db ORDER BY db DESC",
    "TESZ-nivalók SZÍN-jei (TESZ-SZÍN-ek)": "MATCH (g:TeszNivalo)<-[:SZINEZI]-(t:TeszSzin), "
                                      "(t)-[:KOZEGBEN_ZAJLIK {primary: true}]->(k), (t)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h) "
                                      "OPTIONAL MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t) "
                                      "RETURN g.label_hu AS mag, k.label_hu + ' / ' + h.label_hu AS szin, "
                                      "count(DISTINCT m) AS munkakor_db ORDER BY mag, munkakor_db DESC LIMIT 300",
}


# ------------------------------------------------------------------ kapcsolatok
class _GraphHolder:
    """neo4j-kapcsolat; ha nem érhető el, 20 mp-ig nem próbálkozunk újra."""

    def __init__(self):
        self.g, self.err, self.at, self.lock = None, "", 0.0, threading.Lock()

    def get(self):
        with self.lock:
            if self.g is not None:
                return self.g
            if self.err and time.time() - self.at < 20:
                return None
            try:
                from .graph import Graph

                g = Graph(settings())
                g.driver.verify_connectivity()
                self.g, self.err = g, ""
            except Exception as exc:  # nem fut, rossz jelszó, hiányzó csomag
                self.err, self.at = str(exc).splitlines()[0][:300], time.time()
            return self.g

    def read(self, q: str, limit: int = 5000, **params) -> list[dict] | None:
        g = self.get()
        if g is None:
            return None
        try:
            return g.read(q, limit=limit, **params)
        except Exception as exc:
            if "ServiceUnavailable" in type(exc).__name__ or "connect" in str(exc).lower():
                with self.lock:
                    self.g, self.err, self.at = None, str(exc)[:300], time.time()
                return None
            raise


GRAPH = _GraphHolder()


def store() -> Store:
    return Store(settings().state_db)


# ------------------------------------------------------------------ fázisállapotok
def _art_index(S: Store) -> dict[str, dict[str, dict]]:
    rows = S.db.execute(
        "SELECT job_id, step, created_at, "
        "CASE WHEN step='P2' THEN json_extract(json, '$.verdict') END AS verdict, "
        "CASE step WHEN 'P2' THEN json_extract(json, '$.p2_reason') "
        "          WHEN 'P1_ERR' THEN json_extract(json, '$.error') "
        "          WHEN 'P2_ERR' THEN json_extract(json, '$.error') "
        "          WHEN 'P3_ERR' THEN json_extract(json, '$.error') END AS info, "
        "CASE step WHEN 'P1' THEN json_array_length(json, '$.szinek') "
        "          WHEN 'P2' THEN json_array_length(json, '$.final_szinek') "
        "          WHEN 'P3' THEN json_array_length(json, '$.szinek') END AS n, "
        "CASE step WHEN 'P1' THEN json_array_length(json, '$.nivalok') "
        "          WHEN 'P2' THEN json_array_length(json, '$.final_nivalok') "
        "          WHEN 'P3' THEN json_array_length(json, '$.nivalok') END AS nm, "
        "CASE WHEN step='P3' THEN json_extract(json, '$.job_id') END AS graph_id, "
        "CASE WHEN step='P3' THEN json_extract(json, '$.p3_cases') END AS cases "
        "FROM artifacts").fetchall()
    out: dict[str, dict[str, dict]] = {}
    for r in rows:
        out.setdefault(r["job_id"], {})[r["step"]] = dict(r)
    return out


def _graph_counts() -> dict[str, dict] | None:
    rows = GRAPH.read("MATCH (m:MunkaKor) OPTIONAL MATCH (m)-[:TARTALMAZ_TESZT]->(t:TeszSzin) "
                      "RETURN m.id AS id, count(t) AS n, "
                      "sum(CASE WHEN t.attestation_hu IS NOT NULL THEN 1 ELSE 0 END) AS r",
                      limit=10 ** 7)
    return None if rows is None else {r["id"]: r for r in rows}


def _mag_text(a: dict, what: str) -> str:
    """'12 TESZ-nivaló / 17 TESZ-SZÍN ...' (régi, TESZ-nivaló nélküli eredménynél csak a TESZ-szám)."""
    return (f"{a['nm']} TESZ-nivaló / {a['n']} TESZ-SZÍN {what}" if a.get("nm") else f"{a['n']} TESZ {what}")


def display_status(status: str) -> str:
    """A korábbi kötegállapotok a rendes feldolgozási sorba tartoznak."""
    return {"P1_KOTEGBEN": "NYERS", "P2_KOTEGBEN": "P1_KESZ"}.get(status, status)


def phases(status: str, note: str, rec: dict, arts: dict, gc: dict | None) -> tuple[list[dict], dict]:
    """Munkakörönként a fázisok állapota és az adatbázis-állapot.

    állapotkódok: done, skip, error, wait, review, na, part, unknown (a 'run'-t a hívó teszi rá).
    """
    status = display_status(status)
    fresh = status == "NYERS"  # visszaállítva vagy új: a régi köztes eredmények újra készülnek
    a = {k: v for k, v in arts.items() if k.endswith("_ERR")} if fresh else arts
    ph = {k: {"state": "wait", "text": ""} for k in ("E0", "P1", "P2", "P3", "P5")}
    if rec.get("doc_type") or (rec.get("source") and rec.get("source_quote") is not None):
        ph["E0"] = {"state": "done", "text": f"dokumentumból: {rec.get('source', '')}"}
    else:
        ph["E0"] = {"state": "na", "text": "közvetlenül importált rekord"}
    # P1
    if "P1" in a:
        ph["P1"] = {"state": "done", "text": _mag_text(a["P1"], "jelölt")}
    elif "P1_ERR" in a:
        ph["P1"] = {"state": "error", "text": a["P1_ERR"]["info"] or note}
    # P2
    if "P2" in a:
        if a["P2"]["verdict"] == "SKIPPED":
            ph["P2"] = {"state": "skip", "text": f"kimaradt: {a['P2']['info'] or ''}"}
        else:
            ph["P2"] = {"state": "done", "text": f"{a['P2']['verdict']}; {_mag_text(a['P2'], 'marad')}"
                        + (f" ({a['P2']['info']})" if a["P2"]["info"] else "")}
    elif "P2_ERR" in a:
        ph["P2"] = {"state": "error", "text": a["P2_ERR"]["info"] or note}
    elif status == "P1_KESZ":
        ph["P2"] = {"state": "wait", "text": note or "bírálatra vár"}
    # P3 / gráf
    if status == "SZAKERTORE":
        ph["P3"] = {"state": "review", "text": "szakértői jóváhagyásra vár, addig nem kerül a gráfba"}
    elif status == "ELVETVE":
        ph["P3"] = {"state": "skip", "text": "elvetve, nem kerül a gráfba"}
    elif "P3" in a and status == "GRAFBAN":
        cases = a["P3"]["cases"]
        ph["P3"] = {"state": "done", "text": f"{_mag_text(a['P3'], 'beírva')}" + (f", {cases} kétes eset" if cases else "")}
    elif "P3_ERR" in a:
        ph["P3"] = {"state": "error", "text": a["P3_ERR"].get("info") or note}
    elif status == "HIBA" and "P1" in a and "P2" in a:
        ph["P3"] = {"state": "error", "text": note}
    elif status == "HIBA" and "P1" in a and "P2_ERR" not in a:
        ph["P2"] = {"state": "error", "text": note}
    elif status == "HIBA" and "P1" not in a and "P1_ERR" not in a:
        ph["P1"] = {"state": "error", "text": note}
    # adatbázis (neo4j) és P5
    gid = (arts.get("P3") or {}).get("graph_id") or rec.get("job_id")
    if gc is None:
        db = {"state": "unknown", "text": "a neo4j nem érhető el"}
        ph["P5"] = {"state": "unknown", "text": "a neo4j nem érhető el"}
    elif gid in gc:
        g = gc[gid]
        db = {"state": "in", "text": f"{g['n']} TESZ a gráfban", "n": g["n"], "graph_id": gid}
        if g["n"] and g["r"] == g["n"]:
            ph["P5"] = {"state": "done", "text": f"mind a {g['n']} TESZ-SZÍN-nek van igazolása"}
        elif g["r"]:
            ph["P5"] = {"state": "part", "text": f"{g['r']} / {g['n']} TESZ-SZÍN-nek van igazolása"}
        else:
            ph["P5"] = {"state": "wait", "text": "még egy TESZ-SZÍN-nek sincs igazolása"}
        if fresh:
            db["text"] += " (régi futásból; az újrafuttatás felülírja)"
    else:
        db = {"state": "out", "text": "nincs a gráfban"}
        if status == "GRAFBAN":
            db = {"state": "missing", "text": "az állapot szerint beírva, de a gráfban nincs meg"}
        ph["P5"] = {"state": "wait", "text": ""}
    return [ph[k] | {"key": k} for k in ("E0", "P1", "P2", "P3", "P5")], db


def _mark_running(rows: list[dict]) -> None:
    for t in tasks.running():
        args, targets = t.get("args") or [], t.get("targets")
        if not args:
            continue
        if args[0] == "run" and "--dry-run" in args:
            continue  # a próbafutás nem változtat állapotot
        if args[0] == "run":
            until = args[args.index("--until") + 1] if "--until" in args else "graph"
            last = {"p1": "P1", "p2": "P2", "graph": "P3"}[until]
            for r in rows:
                if targets == "all" or (isinstance(targets, list) and r["job_id"] in targets):
                    for p in r["phases"]:
                        if p["key"] in ("P1", "P2", "P3") and p["state"] in ("wait", "error"):
                            if ["P1", "P2", "P3"].index(p["key"]) <= ["P1", "P2", "P3"].index(last):
                                p["state"] = "run"
                            break
        elif args[0] == "review" and "--approve" in args:
            for r in rows:
                if r["job_id"] in (targets or []):
                    r["phases"][3].update(state="run", text="Jóváhagyás: egyesítés és gráfba írás folyamatban")
        elif args[0] == "certify":
            for r in rows:
                p5 = r["phases"][4]
                if r["db"]["state"] == "in" and p5["state"] in ("wait", "part"):
                    p5["state"] = "run"


def job_rows(S: Store) -> tuple[list[dict], bool]:
    arts = _art_index(S)
    gc = _graph_counts()
    out = []
    for r in S.jobs():
        rec = json.loads(r["record_json"])
        ph, db = phases(r["status"], r["note"] or "", rec, arts.get(r["job_id"], {}), gc)
        out.append({"job_id": r["job_id"], "title": rec.get("title", ""), "status": display_status(r["status"]),
                    "note": r["note"] or "", "updated_at": r["updated_at"], "phases": ph, "db": db})
    _mark_running(out)
    return out, gc is not None


def _usage_by_step(S: Store, job_id: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for r in S.db.execute("SELECT step, provider, model, prompt_version, parsed_ok, usage_json, created_at "
                          "FROM llm_log WHERE job_id=? ORDER BY id", (job_id,)).fetchall():
        u = json.loads(r["usage_json"] or "{}")
        a = out.setdefault(r["step"], {"calls": 0, "ok": 0, "input": 0, "output": 0, "cache_read": 0,
                                       "seconds": 0.0, "cost": 0.0, "model": "", "provider": "",
                                       "prompt_version": "", "last": ""})
        a["calls"] += 1
        a["ok"] += int(r["parsed_ok"])
        inp, out_, cr, cw = (int(u.get(k) or 0) for k in ("input_tokens", "output_tokens", "cache_read_input_tokens",
                                                            "cache_creation_input_tokens"))
        inp += cw  # a gyorsítótárba írt bemenet is bemenet (1,25-szörös áron)
        a["input"] += inp
        a["output"] += out_
        a["cache_read"] += cr
        a["seconds"] += (u.get("duration_ms") or 0) / 1000
        p = price(r["provider"].replace("anthropic-batch", "anthropic"), r["model"], inp + cr)
        if p is not None and a["cost"] is not None:
            c = ((inp - cw) * p[0] + cw * p[0] * 1.25 + cr * p[2] + out_ * p[1]) / 1e6
            a["cost"] += c / 2 if r["provider"] == "anthropic-batch" else c
        else:
            a["cost"] = None
        a.update(model=r["model"], provider=r["provider"], prompt_version=r["prompt_version"], last=r["created_at"])
    for a in out.values():
        a["seconds"] = round(a["seconds"], 1)
        if a["cost"] is not None:
            a["cost"] = round(a["cost"], 4)
    return out


def _tesz_brief(t: dict) -> dict:
    return {"cand_id": t.get("cand_id"), "nivalo_id": t.get("nivalo_id"), "alap_szin": t.get("alap_szin", True),
            "szin_kulonbseg_hu": t.get("szin_kulonbseg_hu"), "label_hu": t.get("label_hu"), "tesz_type": t.get("tesz_type"),
            "szer_elem": (t.get("primary_szer") or {}).get("label_hu"), "kozeg": t.get("kozeg"),
            "helyzet": t.get("helyzet"), "relevance": t.get("relevance"), "eqf": t.get("expected_eqf"),
            "confidence": t.get("confidence")}


def job_detail(S: Store, job_id: str) -> dict:
    row = S.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
    if not row:
        raise KeyError(job_id)
    rec = json.loads(row["record_json"])
    arts = {r["step"]: json.loads(r["json"]) | {"_at": r["created_at"], "_pv": r["prompt_version"]}
            for r in S.db.execute("SELECT * FROM artifacts WHERE job_id=?", (job_id,)).fetchall()}
    rows, _ = job_rows_one(S, row, rec)
    usage = _usage_by_step(S, job_id)
    p1, p2, p3 = arts.get("P1"), arts.get("P2"), arts.get("P3")
    # Mi került a gráfba: a végleges lista soronként, a P3 döntésével együtt
    final = (p2 or {}).get("final_szinek") or (p1 or {}).get("szinek") or []
    magok = [{k: m.get(k) for k in ("nivalo_id", "kor_id", "label_hu", "tesz_type", "relevance", "melyites_jeloltek")}
             | {"szer_elem": (m.get("primary_szer") or {}).get("label_hu")}
             for m in ((p2 or {}).get("final_nivalok") or (p1 or {}).get("nivalok") or [])]
    decided = {x["cand_id"]: x for x in (p3 or {}).get("szinek", [])}
    graph_ids = None
    gid = (p3 or {}).get("job_id") or job_id
    g_rows = GRAPH.read(
        "MATCH (m:MunkaKor {id: $id})-[r:TARTALMAZ_TESZT]->(t:TeszSzin) "
        "OPTIONAL MATCH (t)-[:SZINEZI]->(g:TeszNivalo) OPTIONAL MATCH (g)-[:TIPUSA]->(ty) "
        "OPTIONAL MATCH (other:MunkaKor)-[:TARTALMAZ_TESZT]->(t) "
        "OPTIONAL MATCH (t)-[:KOZEGBEN_ZAJLIK {primary: true}]->(k) OPTIONAL MATCH (t)-[:HELYZETBEN_VEGZENDO {primary: true}]->(h) "
        "WITH t, r, g, ty, k, h, count(DISTINCT other) AS munkakor_db "
        "RETURN t.id AS id, g.label_hu + ' – ' + coalesce(k.label_hu, '') + '/' + coalesce(h.label_hu, '') AS label_hu, "
        "ty.id AS tesz_type, r.cand_id AS cand_id, g.id AS mag, r.relevance AS relevance, "
        "size(coalesce(g.rubric_alap_hu, [])) + size(coalesce(t.rubric_szin_hu, [])) AS rubrika, "
        "munkakor_db ORDER BY label_hu", id=gid)
    if g_rows is not None:
        graph_ids = {x["id"] for x in g_rows}
    tesz = []
    for t in final:
        d = decided.get(t.get("cand_id"))
        item = _tesz_brief(t)
        if d and row["status"] == "GRAFBAN":
            item.update(graph_id=d["tesz_id"], decision=DECISION_HU.get(d["decision"], d["decision"]),
                        in_graph=None if graph_ids is None else d["tesz_id"] in graph_ids)
        else:
            item.update(graph_id=None, decision="", in_graph=False if graph_ids is not None else None)
        tesz.append(item)
    dropped = []
    if p2 and p1:
        keep = {t.get("cand_id") for t in final}
        dropped = [_tesz_brief(t) | {"reason": next((c.get("reason_hu") for c in p2.get("changes", [])
                                                      if c.get("cand_id") == t.get("cand_id")), "")}
                   for t in p1.get("szinek", []) if t.get("cand_id") not in keep]
    queue = [dict(q) | {"payload": json.loads(q["payload_json"])} for q in S.queue() if q["job_id"] == job_id]
    for q in queue:
        q.pop("payload_json", None)
    return {
        "job_id": job_id, "title": rec.get("title", ""), "status": display_status(row["status"]), "note": row["note"] or "",
        "updated_at": row["updated_at"], "record": rec, "phases": rows["phases"], "db": rows["db"],
        "approval_tasks": [t | {"log": tasks.tail(t["id"], 15) if t["status"] in ("hiba", "megszakadt") else ""}
                           for t in tasks.tasks() if (t.get("args") or [""])[0] == "review"
                           and job_id in (t.get("targets") or [])],
        "usage": usage, "szinek": tesz, "nivalok": magok, "dropped": dropped, "dryrun": dryrun_get(job_id, brief=True), "graph": g_rows, "graph_ok": g_rows is not None,
        "graph_id": gid, "queue": queue,
        "p1": None if not p1 else {"label": p1.get("job_label_hu"), "purpose": p1.get("purpose_hu", ""),
                                   "at": p1["_at"], "pv": p1["_pv"]},
        "p2": None if not p2 else {"verdict": p2.get("verdict"), "reason": p2.get("p2_reason", ""),
                                   "changes": p2.get("changes", []), "warnings": p2.get("warnings", []),
                                   "at": p2["_at"], "pv": p2["_pv"]},
        "p3": None if not p3 else {"cases": p3.get("p3_cases"), "review": p3.get("review", []), "at": p3["_at"]},
        "errors": {k[:2]: arts[k].get("error", "") for k in ("P1_ERR", "P2_ERR", "P3_ERR") if k in arts},
    }


def job_rows_one(S: Store, row, rec) -> tuple[dict, bool]:
    arts = _art_index_one(S, row["job_id"])
    gc = _graph_counts()
    ph, db = phases(row["status"], row["note"] or "", rec, arts, gc)
    out = [{"job_id": row["job_id"], "phases": ph, "db": db}]
    _mark_running(out)
    return out[0], gc is not None


def _art_index_one(S: Store, job_id: str) -> dict:
    rows = S.db.execute(
        "SELECT step, "
        "CASE WHEN step='P2' THEN json_extract(json, '$.verdict') END AS verdict, "
        "CASE step WHEN 'P2' THEN json_extract(json, '$.p2_reason') WHEN 'P1_ERR' THEN json_extract(json, '$.error') "
        "          WHEN 'P2_ERR' THEN json_extract(json, '$.error') "
        "          WHEN 'P3_ERR' THEN json_extract(json, '$.error') END AS info, "
        "CASE step WHEN 'P1' THEN json_array_length(json, '$.szinek') WHEN 'P2' THEN json_array_length(json, '$.final_szinek') "
        "          WHEN 'P3' THEN json_array_length(json, '$.szinek') END AS n, "
        "CASE step WHEN 'P1' THEN json_array_length(json, '$.nivalok') WHEN 'P2' THEN json_array_length(json, '$.final_nivalok') "
        "          WHEN 'P3' THEN json_array_length(json, '$.nivalok') END AS nm, "
        "CASE WHEN step='P3' THEN json_extract(json, '$.job_id') END AS graph_id, "
        "CASE WHEN step='P3' THEN json_extract(json, '$.p3_cases') END AS cases "
        "FROM artifacts WHERE job_id=?", (job_id,)).fetchall()
    return {r["step"]: dict(r) for r in rows}


# ------------------------------------------------------------------ áttekintés, adatbázis
def overview(S: Store) -> dict:
    rows, graph_ok = job_rows(S)
    counts = {k: {} for k in ("E0", "P1", "P2", "P3", "P5")}
    db = {}
    for r in rows:
        for p in r["phases"]:
            counts[p["key"]][p["state"]] = counts[p["key"]].get(p["state"], 0) + 1
        db[r["db"]["state"]] = db.get(r["db"]["state"], 0) + 1
    cost = S.db.execute("SELECT count(*) FROM llm_log").fetchone()[0]
    from .report import summary

    total = summary(S, None, ("step",))
    from .report import with_cost
    usage = with_cost(S.usage())
    totals = {k: sum(r[k] for r in usage) for k in ("input", "output", "cache_read", "hivas")}
    totals["known_cost"] = sum(r["koltseg_usd"] or 0 for r in usage)
    totals["unpriced_calls"] = sum(r["hivas"] for r in usage if r["koltseg_usd"] is None)
    return {"jobs": len(rows), "phases": PHASES, "counts": counts, "db": db, "graph_ok": graph_ok,
            "graph_err": GRAPH.err, "running": tasks.running(), "calls": cost, "by_step": total, "usage_totals": totals,
            "queue": len(S.queue()), "prompt_version": prompt_version()}


def db_state(S: Store) -> dict:
    sq = {
        "jobs": [dict(r) for r in S.db.execute("SELECT CASE status WHEN 'P1_KOTEGBEN' THEN 'NYERS' WHEN 'P2_KOTEGBEN' THEN 'P1_KESZ' ELSE status END AS status, count(*) AS db FROM jobs GROUP BY 1")],
        "tables": {t: S.db.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                   for t in ("jobs", "artifacts", "llm_log", "review_queue", "alias_index", "batches")},
    }
    out = {"sqlite": sq, "sqlite_path": str(settings().state_db.relative_to(ROOT)) if settings().state_db.is_relative_to(ROOT)
           else str(settings().state_db), "neo4j_uri": settings().neo4j_uri, "cypher": CYPHER}
    labels = GRAPH.read("MATCH (n) RETURN labels(n)[0] AS tipus, count(*) AS db ORDER BY db DESC")
    out["graph_ok"] = labels is not None
    out["graph_err"] = GRAPH.err
    if labels is None:
        return out
    out["labels"] = labels
    out["rels"] = GRAPH.read("MATCH ()-[r]->() RETURN type(r) AS kapcsolat, count(*) AS db ORDER BY db DESC")
    out["no_rubric"] = GRAPH.read("MATCH (t:TeszSzin) WHERE t.attestation_hu IS NULL "
                                  "RETURN count(t) AS db")[0]["db"]
    rows, _ = job_rows(S)
    out["compare"] = {
        "in": sum(r["db"]["state"] == "in" for r in rows),
        "out": sum(r["db"]["state"] == "out" for r in rows),
        "missing": [r["job_id"] for r in rows if r["db"]["state"] == "missing"],
    }
    known = {r["db"].get("graph_id") for r in rows} | {r["job_id"] for r in rows}
    orphan = GRAPH.read("MATCH (m:MunkaKor) RETURN m.id AS id, m.label_hu AS label", limit=10 ** 7) or []
    out["compare"]["orphans"] = [o for o in orphan if o["id"] not in known][:200]
    return out


# ------------------------------------------------------------------ modellek
def models_state() -> dict:
    s = settings()
    data = ui_models()
    steps = []
    for st in STEPS:
        m = s.step(st)
        p = price(m.provider, m.model)
        steps.append({"step": st, "name": STEP_NAMES[st], "provider": m.provider, "model": m.model,
                      "effort": m.effort, "thinking": m.thinking, "max_tokens": m.max_tokens,
                      "price": p, "from_ui": bool(((data.get("steps") or {}).get(st) or {}).get("provider"))})
    return {"steps": steps, "providers": [p for p in PROVIDERS if p != "local"], "local": {
        "base_url": s.local_base_url, "model": (data.get("local") or {}).get("model", ""),
        "json_mode": s.local_json_mode, "timeout": int(s.local_timeout)},
        "p2": {"mode": s.p2_mode, "sample": s.p2_sample}, "has_ui_file": MODELS_PATH.exists(),
        "prices": [{"model": k, "input": v[0], "output": v[1], "cache": v[2]}
                   for k, v in (PRICES | (data.get("prices") or {})).items()],
        "catalog": {k: list(dict.fromkeys(v + (data.get("catalog", {}).get(k) or []))) for k, v in (MODEL_CATALOG | {k: [] for k in data.get("catalog", {}) if k not in MODEL_CATALOG}).items()},
        "gemini_price_note": GEMINI_PRICE_NOTE, "openai": {"base_url": s.openai_base_url, "api_key_env": s.openai_api_key_env, "json_mode": s.openai_json_mode, "timeout": s.openai_timeout},
        "embed": {"provider": s.embed_provider, "model": s.embed_model, "host": s.ollama_host, "prefix": s.embed_prefix},
        "anthropic_models": ["claude-haiku-5-5", "claude-sonnet-5-5", "claude-opus-5-5"]}


def save_models(body: dict) -> None:
    data = ui_models()
    steps = {}
    current = settings()
    for r in body.get("steps", []):
        if r["step"] not in STEPS or r["provider"] not in PROVIDERS:
            raise ValueError(f"Ismeretlen lépés vagy szolgáltató: {r}")
        steps[r["step"]] = {"provider": r["provider"], "model": r.get("model") or "",
                            "effort": r.get("effort") or "", "thinking": "off" if r.get("thinking") == "off" else "",
                            "max_tokens": int(r.get("max_tokens") or 4000),
                            "temperature": current.step(r["step"]).temperature if r["provider"] == current.step(r["step"]).provider else None}
    loc = body.get("local") or data.get("local") or {}
    p2 = body.get("p2") or {}
    if p2.get("mode") not in ("auto", "always", "never"):
        raise ValueError("A P2 módja auto, always vagy never lehet.")
    data.update({"steps": steps,
                 "local": {"base_url": loc.get("base_url", ""), "model": loc.get("model", ""),
                           "json_mode": loc.get("json_mode", "object"), "timeout": int(loc.get("timeout") or 900)},
                 "p2": {"mode": p2["mode"], "sample": float(p2.get("sample", 0.15))}})
    if "openai" in body:
        timeout = float(body["openai"].get("timeout", 120))
        if not 1 <= timeout <= 7200:
            raise ValueError("Az API időkorlátja 1 és 7200 mp között legyen.")
    for key in ("openai", "embed"):
        if key in body:
            allowed = {"openai": {"base_url", "api_key_env", "json_mode", "timeout"},
                       "embed": {"provider", "model", "host", "prefix"}}[key]
            data[key] = {k: v for k, v in body[key].items() if k in allowed}
    if "prices" in body:
        prices = {}
        for row in body["prices"]:
            model = str(row.get("model", "")).strip()
            if not model:
                continue
            values = [float(row[k]) for k in ("input", "output", "cache")]
            import math
            if any(not math.isfinite(v) or v < 0 for v in values):
                raise ValueError("Az ár véges, nem negatív szám legyen.")
            if model in (ui_models().get("prices") or {}) or tuple(values) != PRICES.get(model):
                prices[model] = values
        data["prices"] = prices
    save_ui_models(data)


def discover_models(body: dict) -> dict:
    from .config import StepModel
    from .llm import LocalLLM, anthropic_client
    import os

    s = settings()
    provider = body.get("provider", "local")
    try:
        if provider == "anthropic":
            ids = [m.id for m in anthropic_client().models.list(limit=100)]
        elif provider == "gemini":
            from google import genai
            ids = [m.name.removeprefix("models/") for m in genai.Client().models.list()
                   if "generateContent" in (m.supported_actions or [])]
        else:
            remote = provider == "openai"
            url = body.get("base_url") or (s.openai_base_url if remote else s.local_base_url)
            key = os.getenv(body.get("api_key_env") or s.openai_api_key_env, "") if remote else s.local_api_key
            ids = LocalLLM(StepModel(provider, "probe", None, 1), url, key, timeout=15,
                           use_proxy=remote).models()
        ids = sorted(set(ids))
        data = ui_models()
        data.setdefault("catalog", {})[provider] = ids
        save_ui_models(data)
        return {"ok": True, "models": ids}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def test_embed(body: dict) -> dict:
    from .embed import Embedder

    s = settings()
    try:
        model = body.get("model", s.embed_model)
        if not model:
            raise ValueError("Adj meg egy beágyazó modellt.")
        e = Embedder(model, body.get("provider", s.embed_provider), body.get("host", s.ollama_host),
                     body.get("prefix", s.embed_prefix), timeout=15)
        a, b, c = [e.encode(t) for t in ("szoftverfejlesztő", "software developer", "ápoló")]
        if not a or len(a) != len(b) or len(a) != len(c):
            raise ValueError("Hibás vagy eltérő méretű vektorok.")
        return {"ok": True, "dimension": len(a), "similarity": sum(x*y for x,y in zip(a,b)),
                "unrelated": sum(x*y for x,y in zip(c,b))}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300]}


def test_openai(body: dict) -> dict:
    """Csak olvasás: a megadott, még nem mentett API-kapcsolat próbája."""
    import os
    from .config import StepModel
    from .llm import LocalLLM

    s = settings()
    key_name = str(body.get("api_key_env", s.openai_api_key_env)).strip()
    key = os.getenv(key_name, "")
    url = str(body.get("base_url", s.openai_base_url)).strip().rstrip("/")
    try:
        parsed = urlparse(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Érvényes http(s) API-cím szükséges, beágyazott jelszó nélkül.")
        if key_name and not key:
            raise ValueError(f"A(z) {key_name} környezeti változó nincs kitöltve a .env fájlban.")
        ids = LocalLLM(StepModel("openai", "probe", None, 1), url, key, timeout=15,
                       use_proxy=True).models()
        return {"ok": True, "models": sorted(set(ids))}
    except Exception as exc:
        import urllib.error

        message = str(exc)
        if isinstance(exc, urllib.error.URLError) and isinstance(exc.reason, ConnectionRefusedError):
            parsed = urlparse(url)
            port = parsed.port or (443 if parsed.scheme == "https" else 80)
            message = (f"A szerver nem fogad kapcsolatot: {parsed.hostname}:{port}. "
                       "Ellenőrizd az API portját és hogy fut-e a szerver. "
                       "A címben a portot is add meg, ha nem 80/443 (például :8000/v1).")
        if key:
            message = message.replace(key, "[rejtett kulcs]")
        return {"ok": False, "error": message[:300]}


def test_local(body: dict) -> dict:
    from .config import StepModel
    from .llm import LocalLLM

    s = settings()
    try:
        ids = LocalLLM(StepModel("local", body.get("model") or "x", None, 1), body.get("base_url") or s.local_base_url,
                       s.local_api_key, timeout=15).models()
        return {"ok": True, "models": ids}
    except Exception as exc:  # hálózati hiba
        return {"ok": False, "error": str(exc)[:300]}


# ------------------------------------------------------------------ promptok
def _prompt_path(name: str) -> Path:
    if name == "vocab":
        return VOCAB_PATH
    if not re.fullmatch(r"[a-z0-9_]+", name) or not (PROMPT_DIR / f"{name}.md").exists():
        raise KeyError(name)
    return PROMPT_DIR / f"{name}.md"


def prompts_list() -> dict:
    order = list(PROMPT_STEP)
    names = sorted([p.stem for p in PROMPT_DIR.glob("*.md")] + ["vocab"],
                   key=lambda n: (order.index(n) if n in order else len(order), n))
    out = []
    for n in names:
        p = _prompt_path(n)
        step, about = PROMPT_STEP.get(n, ("", ""))
        hist = len(list(HISTORY.glob(f"{n}.*"))) if HISTORY.exists() else 0
        out.append({"name": n, "step": step, "about": about, "chars": p.stat().st_size,
                    "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M"),
                    "versions": hist})
    return {"prompts": out, "prompt_version": prompt_version()}


def prompt_get(name: str) -> dict:
    p = _prompt_path(name)
    hist = sorted(HISTORY.glob(f"{name}.*"), reverse=True) if HISTORY.exists() else []
    return {"name": name, "text": p.read_text(encoding="utf-8"), "history": [h.name for h in hist],
            "step": PROMPT_STEP.get(name, ("", ""))[0], "about": PROMPT_STEP.get(name, ("", ""))[1]}


def prompt_save(name: str, text: str) -> dict:
    p = _prompt_path(name)
    if name == "vocab":
        import yaml

        yaml.safe_load(text)  # hibás YAML-nál kivétel
    current = p.read_text(encoding="utf-8")
    if current == text:
        return {"saved": False}
    HISTORY.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (HISTORY / f"{name}.{stamp}{p.suffix}").write_text(current, encoding="utf-8")
    p.write_text(text, encoding="utf-8")
    if name == "vocab":
        from .config import vocab

        vocab.cache_clear()
    return {"saved": True, "backup": f"{name}.{stamp}{p.suffix}", "prompt_version": prompt_version()}


def prompt_old(name: str, file: str) -> dict:
    _prompt_path(name)
    f = HISTORY / Path(file).name
    if not f.name.startswith(name + ".") or not f.exists():
        raise KeyError(file)
    return {"text": f.read_text(encoding="utf-8")}


def diff(a: str, b: str) -> str:
    return "".join(difflib.unified_diff(a.splitlines(True), b.splitlines(True), "mentett", "szerkesztett", n=2))


# ------------------------------------------------------------------ fájlok
def files_list() -> dict:
    def listing(folder: Path, exts: set[str]) -> list[dict]:
        if not folder.exists():
            return []
        return [{"name": p.name, "kb": round(p.stat().st_size / 1024, 1),
                 "modified": datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")}
                for p in sorted(folder.iterdir()) if p.is_file() and p.suffix.lower() in exts]
    return {"docs": listing(UPLOAD_DIR, DOC_EXT), "records": listing(DATA_DIR, REC_EXT)}


def _file(kind: str, name: str) -> Path:
    folder, exts = (UPLOAD_DIR, DOC_EXT) if kind == "doc" else (DATA_DIR, REC_EXT)
    p = folder / Path(name).name
    if p.suffix.lower() not in exts:
        raise ValueError(f"Nem támogatott fájltípus: {p.suffix}")
    return p


def preview_records(name: str) -> dict:
    from .importers import load

    recs = load(_file("rec", name))
    S = store()
    known = {r["job_id"]: r["input_hash"] for r in S.db.execute("SELECT job_id, input_hash FROM jobs")}
    from .store import sha

    new = sum(r["job_id"] not in known for r in recs)
    changed = sum(r["job_id"] in known and known[r["job_id"]] != sha(r) for r in recs)
    return {"count": len(recs), "new": new, "changed": changed, "same": len(recs) - new - changed,
            "sample": [{"job_id": r.get("job_id"), "title": r.get("title"), "tasks": len(r.get("tasks") or [])}
                       for r in recs[:15]]}


# ------------------------------------------------------------------ próbafutás (dry run)
def _dry_file(job_id: str) -> Path:
    from .pipeline import DRYRUN_DIR

    return DRYRUN_DIR / f"{job_id.replace(':', '_').replace('/', '_')}.json"


def dryrun_get(job_id: str, brief: bool = False) -> dict | None:
    f = _dry_file(job_id)
    if not f.exists():
        return None
    d = json.loads(f.read_text(encoding="utf-8"))
    d["file_time"] = datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")
    if brief:
        d.pop("p2", None)
    return d


def dryrun_list() -> dict:
    from .pipeline import DRYRUN_DIR

    out = []
    for f in sorted(DRYRUN_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            continue
        out.append({"job_id": d.get("job_id"), "title": d.get("title"), "ok": d.get("ok"), "szamok": d.get("szamok"),
                    "flags": [x.get("rule") for x in d.get("rule_flags") or []], "total": d.get("total"),
                    "prompt_version": d.get("prompt_version"),
                    "time": datetime.fromtimestamp(f.stat().st_mtime).isoformat(timespec="seconds")})
    return {"runs": out}


# ------------------------------------------------------------------ futtatás
def start_run(body: dict, S: Store) -> str:
    until = body.get("until", "graph")
    if until not in ("p1", "p2", "graph"):
        raise ValueError("until: p1 | p2 | graph")
    ids = [str(j) for j in body.get("jobs") or []]
    if body.get("dry_run"):
        # Próbafutás: a rekord a state.sqlite-ból csak olvasva; semmilyen állapot nem változik.
        if not ids:
            ids = [r["job_id"] for r in S.jobs() if r["status"] not in ("GRAFBAN", "ELVETVE")][: int(body.get("limit") or 1)]
        if not ids:
            raise ValueError("Jelölj ki legalább egy munkakört a próbafutáshoz.")
        args = ["run", "--dry-run", "--workers", str(int(body.get("workers") or 4))]
        for j in ids:
            S.job(j)
            args += ["--job", j]
        return tasks.start(f"próbafutás (dry run): {len(ids)} munkakör", args, targets=ids)
    args = ["run", "--until", until, "--workers", str(int(body.get("workers") or 4))]
    force = bool(body.get("force"))
    if not ids:  # a várakozók közül az első N, vagy mind
        ids = [r["job_id"] for r in S.jobs() if r["status"] not in ("GRAFBAN", "ELVETVE")
               ]
        if body.get("limit"):
            ids = ids[: int(body["limit"])]
    if not ids:
        raise ValueError("Nincs feldolgozásra váró munkakör.")
    for j in ids:
        old_status = S.status(j)
        if display_status(old_status) != old_status:
            S.set_status(j, display_status(old_status), "normál feldolgozásra visszaállítva")
        args += ["--job", j]
    if force:
        args.append("--force")
    label = {"p1": "felbontás (P1)", "p2": "felbontás + bírálat (P1–P2)", "graph": "teljes feldolgozás"}[until]
    return tasks.start(f"{label}: {len(ids)} munkakör", args, targets=ids)


APPROVAL_LOCK = threading.Lock()


def start_approval(S: Store, qid: int) -> dict:
    with APPROVAL_LOCK:
        row = next((r for r in S.queue() if r["id"] == qid), None)
        if row is None:
            raise ValueError("Ez a szakértői kérés már le van zárva. Frissítsd az oldalt.")
        if row["kind"] != "job_review":
            raise ValueError("Ehhez a kéréshez a lezárás művelet használható.")
        for task in tasks.running():
            if row["job_id"] in (task.get("targets") or []):
                raise ValueError("Ehhez a munkakörhöz már fut feldolgozás. Az állapota a Futás oldalon látható.")
        tid = tasks.start("jóváhagyás és gráfba írás", ["review", "--approve", str(qid)], targets=[row["job_id"]])
        return {"task": tid}


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "gaphopper"

    def log_message(self, fmt, *args):  # csendes napló: csak a hibák
        if args and str(args[1]).startswith(("4", "5")):
            super().log_message(fmt, *args)

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, obj, code: int = 200) -> None:
        self._send(code, json.dumps(obj, ensure_ascii=False, default=str).encode("utf-8"),
                   "application/json; charset=utf-8")

    def body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def do_GET(self):
        self._route("GET")

    def do_POST(self):
        self._route("POST")

    def do_PUT(self):
        self._route("PUT")

    def do_DELETE(self):
        self._route("DELETE")

    def _route(self, method: str) -> None:
        u = urlparse(self.path)
        path, q = unquote(u.path), {k: v[-1] for k, v in parse_qs(u.query).items()}
        if not path.startswith("/api/"):
            return self._static(path) if method == "GET" else self.json({"error": "nincs ilyen"}, 404)
        parts = path[5:].strip("/").split("/")
        try:
            res = self._api(method, parts, q)
            self.json(res if res is not None else {"ok": True})
        except KeyError as exc:
            self.json({"error": f"Nem található: {exc}"}, 404)
        except (ValueError, sqlite3.Error) as exc:
            self.json({"error": str(exc)}, 400)
        except Exception as exc:  # váratlan hiba: a felület kiírja
            traceback.print_exc()
            self.json({"error": f"{type(exc).__name__}: {exc}"}, 500)

    def _static(self, path: str) -> None:
        f = (WEB_DIR / (path.lstrip("/") or "index.html")).resolve()
        if not f.is_relative_to(WEB_DIR.resolve()) or not f.is_file():
            f = WEB_DIR / "index.html"
        ctype = mimetypes.guess_type(f.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, f.read_bytes(), ctype)

    def _api(self, m: str, p: list[str], q: dict):  # noqa: C901 (egyszerű útválasztó)
        S = store()
        head = p[0]
        if head == "overview" and m == "GET":
            return overview(S)
        if head == "jobs" and m == "GET" and len(p) == 1:
            rows, graph_ok = job_rows(S)
            return {"jobs": rows, "graph_ok": graph_ok, "graph_err": GRAPH.err, "running": tasks.running()}
        if head == "jobs" and len(p) >= 2:
            jid = p[1]
            if m == "GET" and len(p) == 2:
                return job_detail(S, jid)
            if m == "POST" and p[2:] == ["reset"]:
                S.job(jid)
                S.set_status(jid, "NYERS", "a felületről visszaállítva")
                return None
        if head == "review" and m == "POST" and len(p) == 3:
            qid = int(p[1])
            if p[2] == "approve":
                return start_approval(S, qid)
            if p[2] in ("reject", "close"):
                row = next((r for r in S.queue() if r["id"] == qid), None)
                if row is None:
                    raise KeyError(qid)
                S.decide(qid, "ELUTASITVA" if p[2] == "reject" else "LEZARVA")
                if p[2] == "reject":
                    S.set_status(row["job_id"], "ELVETVE", "szakértő elutasította")
                return None
        if head == "run" and m == "POST":
            return {"task": start_run(self.body(), S)}
        if head == "dryrun" and m == "GET":
            if len(p) == 1:
                return dryrun_list()
            d = dryrun_get(p[1])
            if d is None:
                raise KeyError(p[1])
            return d
        if head == "certify" and m == "POST":
            b = self.body()
            return {"task": tasks.start("tanúsítás (P5)", ["certify"] + (["--limit", str(int(b["limit"]))]
                                                                       if b.get("limit") else []))}
        if head == "tasks":
            if m == "GET" and len(p) == 1:
                return {"tasks": tasks.tasks()}
            if m == "GET" and len(p) == 2:
                t = tasks.get(p[1])
                if t is None:
                    raise KeyError(p[1])
                return t | {"log": tasks.tail(p[1], int(q.get("lines", 300)))}
            if m == "POST" and p[2:] == ["stop"]:
                tasks.stop(p[1])
                return None
        if head == "components":
            from .components import component_status, start_neo4j
            if m == "GET" and len(p) == 1:
                return component_status()
            if m == "POST" and p[1:] == ["neo4j-start"]:
                result = start_neo4j()
                return result
        if head == "models":
            if m == "GET":
                return models_state()
            if m == "POST" and len(p) == 1:
                save_models(self.body())
                return models_state()
            if m == "POST" and p[1:] == ["discover"]:
                return discover_models(self.body())
            if m == "POST" and p[1:] == ["openai-test"]:
                return test_openai(self.body())
            if m == "POST" and p[1:] == ["embed-test"]:
                return test_embed(self.body())
            if m == "POST" and p[1:] == ["test"]:
                return test_local(self.body())
            if m == "DELETE":
                MODELS_PATH.unlink(missing_ok=True)
                return models_state()
        if head == "prompts":
            if m == "GET" and len(p) == 1:
                return prompts_list()
            if m == "GET" and len(p) == 2:
                return prompt_get(p[1])
            if m == "GET" and len(p) == 4 and p[2] == "history":
                return prompt_old(p[1], p[3])
            if m == "POST" and len(p) == 2:
                return prompt_save(p[1], self.body()["text"])
            if m == "POST" and p[2:] == ["diff"]:
                b = self.body()
                return {"diff": diff(b.get("a", ""), b.get("b", ""))}
        if head == "files":
            if m == "GET" and len(p) == 1:
                return files_list()
            kind, name = p[1], "/".join(p[2:])
            if m == "PUT":
                f = _file(kind, name)
                f.parent.mkdir(parents=True, exist_ok=True)
                n = int(self.headers.get("Content-Length") or 0)
                f.write_bytes(self.rfile.read(n))
                return {"saved": f.name}
            if m == "DELETE":
                _file(kind, name).unlink(missing_ok=True)
                return None
            if m == "GET" and kind == "rec":
                return preview_records(name)
        if head == "extract" and m == "POST":
            b = self.body()
            names = [_file("doc", n).name for n in b.get("files", [])]
            if not names:
                raise ValueError("Jelölj ki legalább egy dokumentumot.")
            out = b.get("out") or "data/jobs_dokumentumokbol.jsonl"
            args = ["extract", *[str(UPLOAD_DIR / n) for n in names], "--out", out, "--import"]
            return {"task": tasks.start(f"kinyerés (E0): {len(names)} dokumentum", args)}
        if head == "import" and m == "POST":
            from .importers import load

            recs = load(_file("rec", self.body()["file"]))
            new = sum(S.upsert_job(r) for r in recs)
            return {"count": len(recs), "new": new}
        if head == "db":
            if m == "GET" and len(p) == 1:
                return db_state(S)
            if m == "POST" and p[1:] == ["cypher"]:
                rows = GRAPH.read(self.body()["query"], limit=2000)
                if rows is None:
                    raise ValueError(f"A neo4j nem érhető el: {GRAPH.err}")
                return {"rows": rows}
            if m == "POST" and p[1:] == ["sql"]:
                con = sqlite3.connect(f"file:{settings().state_db}?mode=ro", uri=True)
                con.row_factory = sqlite3.Row
                try:
                    return {"rows": [dict(r) for r in con.execute(self.body()["query"]).fetchmany(2000)]}
                finally:
                    con.close()
        if head == "cost" and m == "GET":
            from .report import summary

            by = tuple(x for x in q.get("by", "prompt_version,step").split(",") if x)
            return {"rows": summary(S, None, by)}
        raise KeyError("/".join(p))


def serve(host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True) -> None:
    srv = ThreadingHTTPServer((host, port), Handler)
    url = f"http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}"
    print(f"Gaphopper felület: {url}   (leállítás: Ctrl+C)", flush=True)
    if open_browser:
        import webbrowser

        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
