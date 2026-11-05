"""A webes felület KÉSZ-ség-oldalai: törzs (hierarchia, irány, 2. tengely), karbantartó parancsok, KÉSZ-ség-sor.

Írás csak a CLI-parancsokon át történik (külön folyamat, napló a Futás oldalon): review --approve/--reject,
kesz-bovit, kesz-irany, generalize, split. Az UJ-javaslat neve jóváhagyás előtt szerkeszthető; a régi név alias lesz."""
from __future__ import annotations

import json

from . import tasks
from .config import kesz_cache_clear, kesz_hierarchia, kesz_torzs, vocab
from .store import Store

KESZ_KINDS = ("kesz_review", "kesz_irany_review", "split_review")
HORD_HU = {"altalanos": "általános", "atviheto": "átvihető", "szakmai": "szakmai"}


def kesz_list(graph) -> dict:
    """A törzs a yaml-ból (frissen: a szerver hosszan fut), a 2. tengely a gráfból (ha elérhető)."""
    kesz_cache_clear()
    h, t, v = kesz_hierarchia(), kesz_torzs(), vocab()
    cs = {c["kod"]: c for c in h["csoportok"]}
    kat = {k["kod"]: k for k in h["kategoriak"]}
    prof = {r["id"]: r for r in (graph.read(
        "MATCH (k:Keszseg) RETURN k.id AS id, k.csalad AS csalad, k.csalad_arany AS arany, "
        "k.hordozhatosag AS hord, k.munkakor_db AS munkakor_db") or [])}
    items = []
    for k, x in t.items():
        c = cs.get(x["csoport"], {})
        p = prof.get(k, {})
        items.append({"id": k, "n": int(k[5:]), "label_hu": x["label_hu"], "label_en": x["label_en"],
                      "csoport": x["csoport"], "csoport_hu": c.get("label_hu", ""), "kategoria": c.get("kategoria", ""),
                      "kategoria_hu": kat.get(c.get("kategoria"), {}).get("label_hu", ""), "irany": x["irany"],
                      "irany_forras": x.get("irany_forras", ""), "forras": x.get("forras", ""),
                      "aliases": x.get("aliases") or [], "leiras_hu": x.get("leiras_hu", ""),
                      "csalad": p.get("csalad") or [], "csalad_arany": p.get("arany") or [],
                      "hordozhatosag": p.get("hord"), "munkakor_db": p.get("munkakor_db") or 0})
    graph_ok = bool(prof) or (hasattr(graph, "get") and graph.get() is not None)
    return {"items": items, "graph_ok": graph_ok, "graph_err": getattr(graph, "err", ""), "version": h.get("version", ""),
            "csoportok": [{"kod": c["kod"], "label_hu": c["label_hu"], "kategoria": c["kategoria"]} for c in h["csoportok"]],
            "kategoriak": [{"kod": k["kod"], "label_hu": k["label_hu"]} for k in h["kategoriak"]],
            "iranyok": {k: x["hu"] for k, x in v["kozeg"].items()},
            "csaladok": {k: x["hu"] for k, x in (v.get("tesz_csalad") or {}).items()},
            "hordozhatosag": HORD_HU}


def kesz_detail(graph, kid: str) -> dict:
    kesz_cache_clear()
    x = kesz_torzs().get(kid)
    if x is None:
        raise KeyError(kid)
    edges = graph.read(
        "MATCH (g:TeszNivalo)-[e:IGENYEL]->(:Keszseg {id: $id}) OPTIONAL MATCH (g)-[:TIPUSA]->(t:TeszTipus) "
        "RETURN g.id AS nivalo_id, g.label_hu AS nivalo, t.id AS tesz_type, coalesce(e.suly, 1) AS suly, "
        "coalesce(e.munkakorok, []) AS munkakorok ORDER BY suly DESC, nivalo", id=kid) or []
    jobs = {r["id"]: r["label"] for r in (graph.read(
        "MATCH (m:MunkaKor) WHERE m.id IN $ids RETURN m.id AS id, m.label_hu AS label",
        ids=sorted({j for e in edges for j in e["munkakorok"]})) or [])}
    for e in edges:
        e["munkakorok"] = [{"id": j, "label": jobs.get(j, j)} for j in e["munkakorok"]]
    hiany = graph.read("MATCH (h:KeszHiany)-[:BEOLVADT]->(:Keszseg {id: $id}) RETURN h.label_hu AS label_hu, "
                       "h.status AS status", id=kid) or []
    base = next(i for i in kesz_list(graph)["items"] if i["id"] == kid)
    return base | {"igenyel": edges, "beolvadt": hiany}


def kesz_queue(S: Store) -> dict:
    rows = [r for r in S.queue() if r["kind"] in KESZ_KINDS]
    t = kesz_torzs()
    out = []
    for r in rows:
        p = json.loads(r["payload_json"])
        if r["kind"] == "kesz_irany_review":
            p["kesz_nev"] = t.get(p.get("id"), {}).get("label_hu", "")
        out.append({"id": r["id"], "kind": r["kind"], "payload": p, "created_at": r["created_at"]})
    return {"queue": out, "iranyok": {k: x["hu"] for k, x in vocab()["kozeg"].items()}}


def _row(S: Store, qid: int):
    row = next((r for r in S.queue() if r["id"] == qid), None)
    if row is None:
        raise ValueError("Ez a szakértői kérés már le van zárva. Frissítsd az oldalt.")
    if row["kind"] not in KESZ_KINDS:
        raise ValueError("Ez nem KÉSZ-ség- vagy bontási kérés; a munkakör-jóváhagyás a Futás oldalon van.")
    return row


def kesz_decide(S: Store, qid: int, action: str, body: dict) -> dict:
    """Jóváhagyás vagy elutasítás a CLI review-logikájával (külön folyamatban). UJ-javaslatnál a név és a
    csoport/irány jóváhagyás előtt módosítható: a régi név a javaslat aliasai közé kerül."""
    row = _row(S, qid)
    if action == "reject":
        return {"task": tasks.start(f"KÉSZ-ség-sor: elutasítás #{qid}", ["review", "--reject", str(qid)])}
    if action != "approve":
        raise ValueError("approve | reject")
    p = json.loads(row["payload_json"])
    if row["kind"] == "kesz_review":
        from .schemas import P8Item

        new_hu = (body.get("label_hu") or p["label_hu"]).strip()
        new_en = (body.get("label_en") or p["label_en"]).strip()
        P8Item.model_validate({"ids": p["ids"], "dontes": "UJ", "label_hu": new_hu, "label_en": new_en,
                               "csoport": body.get("csoport") or p["csoport"], "irany": body.get("irany") or p["irany"]})
        if new_hu != p["label_hu"] and p["label_hu"] not in p.get("nevek", []):
            p["nevek"] = list(p.get("nevek", [])) + [p["label_hu"]]
        if new_en != p["label_en"] and p["label_en"]:
            p["aliases_extra"] = list(p.get("aliases_extra", [])) + [f"en:{p['label_en']}"]
        p |= {"label_hu": new_hu, "label_en": new_en, "csoport": body.get("csoport") or p["csoport"],
              "irany": body.get("irany") or p["irany"]}
        S.update_payload(qid, p)
    elif row["kind"] == "kesz_irany_review" and body.get("irany"):
        from .schemas import _irany

        p["javaslat"] = _irany(body["irany"])
        S.update_payload(qid, p)
    return {"task": tasks.start(f"KÉSZ-ség-sor: jóváhagyás #{qid}", ["review", "--approve", str(qid)])}


MAINT = {
    "kesz-bovit": "KÉSZ-ség-törzs bővítése",
    "kesz-irany": "KÉSZ-ség-irányok felülvizsgálata",
    "generalize": "törzskarbantartás (generalize)",
    "split": "bontás végrehajtása (split)",
}


def start_maint(cmd: str, body: dict) -> str:
    """Karbantartó parancs indítása ugyanúgy, mint a parancssorból (külön folyamat, napló)."""
    from .pipeline import DRYRUN_DIR

    if cmd not in MAINT:
        raise ValueError(f"Ismeretlen parancs: {cmd}")
    args = [cmd]
    dry = bool(body.get("dry_run"))
    if cmd == "kesz-bovit" and body.get("from_dryrun"):
        files = [str(f) for f in sorted(DRYRUN_DIR.glob("*.json"))]
        if not files:
            raise ValueError("Nincs próbafutás-eredmény (runs/dryrun/).")
        args += ["--from-dryrun", *files] + (["--queue"] if body.get("queue") else [])
    elif cmd in ("kesz-bovit", "kesz-irany", "generalize") and dry:
        args.append("--dry-run")
    if cmd == "kesz-irany" and body.get("all"):
        args.append("--all")
    if cmd == "split" and body.get("review"):
        args += ["--review", str(int(body["review"]))]
    label = MAINT[cmd] + (" – próba" if dry or body.get("from_dryrun") else "")
    return tasks.start(label, args)
