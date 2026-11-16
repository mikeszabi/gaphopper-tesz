"""T0: besorolási és ágazati taxonómiák a neo4j-ben (leírás: docs/taxonomia.md).

Csomópontok (kulcs: `id = "<rendszer>:<kód>"`):
  (:Besorolas:Isco|Esco|Feor|Soc|Onet {id, rendszer, kod, szint, szint_nev, label_hu, label_en, uri})
  (:Agazat:Isic|Wef {id, rendszer, kod, label_hu, label_en, leiras_hu, leiras_en})
Élek:
  (gyerek)-[:RESZE]->(szülő)                                   hierarchia rendszeren belül
  (a)-[:MEGFELEL {forras, match, method, primary, confidence}]->(b)   rendszerek közötti megfeleltetés
  (x)-[:AGAZATBAN {forras, primary, arany, db}]->(:Agazat)     foglalkozás/csoport -> ISIC/WEF
  (:MunkaKor)-[:BESOROLVA {match, method, primary, confidence}]->(:Besorolas)   a rekord `refs`-éből

Minden írás UNWIND-dal, egy tranzakcióban (graph.write_taxonomy); újrafuttatható (MERGE, a forrás élei frissülnek).
"""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path

from .job_all import FEOR_LEVEL, ISCO_LEVEL, SOC_LEVEL, Sources, _xlsx

SYSTEM_LABEL = {"isco": "Isco", "esco": "Esco", "feor": "Feor", "soc": "Soc", "onet": "Onet"}
SECTOR_LABEL = {"isic": "Isic", "wef": "Wef"}
# a `refs` rendszerei, amelyekhez Besorolas-csomópont tartozik (a crawl forrás, nem taxonómia)
REF_SYSTEMS = set(SYSTEM_LABEL)


def nid(system: str, code: str) -> str:
    return f"{system}:{code}"


def _node(system, code, szint, szint_nev, hu="", en="", uri="", parent=None) -> dict:
    return {"id": nid(system, code), "rendszer": system, "kod": code, "szint": szint, "szint_nev": szint_nev,
            "label_hu": hu or "", "label_en": en or "", "uri": uri or "",
            "_parent": nid(system, parent) if parent else None}


# ------------------------------------------------------------------ foglalkozási taxonómiák
def occupation_nodes(src: Sources) -> list[dict]:
    nodes = []
    for c, g in src.isco.items():
        nodes.append(_node("isco", c, len(c), ISCO_LEVEL.get(len(c), ""), g["hu"], g["en"], g["uri"],
                           c[:-1] if len(c) > 1 and c[:-1] in src.isco else None))
    for uri, e in src.esco.items():
        parts = e["code"].split(".")
        parent = next((".".join(parts[:n]) for n in range(len(parts) - 1, 1, -1)
                       if ".".join(parts[:n]) in src.esco_by_code), None)
        n = _node("esco", e["code"], 3 + len(parts), "ESCO occupation", e["hu"], e["en"], uri,
                  parent)
        if parent is None and e["isco"] in src.isco:
            n["_parent"] = nid("isco", e["isco"])
        nodes.append(n)
    for c, f in src.feor.items():
        nodes.append(_node("feor", c, f["level"], FEOR_LEVEL.get(f["level"], ""), f["name"], "", "",
                           f["parent"] if f["parent"] in src.feor else None))
    for c, (grp, title) in src.soc.items():
        lvl = SOC_LEVEL[grp]
        cands = [f"{c[:2]}-0000", f"{c[:4]}000", f"{c[:6]}0"][:lvl - 1]
        parent = next((x for x in reversed(cands) if x in src.soc and x != c), None)
        nodes.append(_node("soc", c, lvl, grp.lower(), "", title, "", parent))
    for c, o in src.onet.items():
        n = _node("onet", c, 5, "O*NET occupation", "", o["title"], f"https://www.onetonline.org/link/summary/{c}")
        n["_parent"] = nid("soc", c[:7]) if c[:7] in src.soc else None
        nodes.append(n)
    return nodes


def crosswalk_edges(src: Sources) -> list[dict]:
    """A hivatalos megfeleltetések: ESCO/ISCO -> O*NET (ESCO–O*NET crosswalk), ISCO-08 -> FEOR-08."""
    out = []
    for onet, rows in src.cw.items():
        if onet not in src.onet:
            continue
        for r in rows:
            if r["uri"] in src.esco:
                a = nid("esco", src.esco[r["uri"]]["code"])
            elif "/isco/" in r["uri"]:
                a = nid("isco", r["uri"].rsplit("/C", 1)[-1])
            else:
                continue
            out.append({"a": a, "b": nid("onet", onet), "forras": "esco-onet-crosswalk", "match": r["match"],
                        "method": "crosswalk", "primary": False, "confidence": None})
    for isco, feors in src.isco_feor.items():
        for f in feors:
            if isco in src.isco and f in src.feor:
                out.append({"a": nid("isco", isco), "b": nid("feor", f), "forras": "isco08-feor08",
                            "match": "", "method": "crosswalk", "primary": False, "confidence": None})
    return out


def jobs_from_file(path: Path | None) -> dict[str, dict]:
    if not path or not Path(path).exists():
        return {}
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            r = json.loads(line)
            out[r["job_id"]] = r
    return out


def ref_rows(rec: dict) -> list[dict]:
    """A rekord `refs`-éből a Besorolas-hivatkozások (rendszer, kód, viszony, módszer, elsődleges, bizonyosság)."""
    rows = []
    for system, refs in (rec.get("refs") or {}).items():
        if system not in REF_SYSTEMS:
            continue
        for r in refs:
            rows.append({"b": nid(system, r["code"]), "match": r.get("match", ""), "method": r.get("method", ""),
                         "primary": bool(r.get("primary")), "confidence": r.get("confidence")})
    return rows


def job_all_edges(jobs: dict[str, dict]) -> list[dict]:
    """A J0-döntések a taxonómia szintjén: O*NET-foglalkozás -> ESCO/ISCO/FEOR (forras: job_all)."""
    out = []
    for jid, rec in jobs.items():
        onet = (rec.get("codes") or {}).get("onet")
        if not onet:
            continue
        for r in ref_rows(rec):
            if not r["b"].startswith("onet:"):
                out.append({"a": nid("onet", onet), **r, "forras": "job_all"})
    return out


# ------------------------------------------------------------------ ágazatok (ISIC, WEF)
def sector_nodes(esco_dir: Path) -> tuple[list[dict], list[dict], dict[str, str]]:
    e = Path(esco_dir)
    nodes = []
    with open(e / "ISIC/isic_sectors_enhu.csv", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            nodes.append({"id": nid("isic", r["Code"]), "rendszer": "isic", "kod": r["Code"], "szint": 1,
                          "szint_nev": "section", "label_en": r["ISIC Sector"], "label_hu": r["ISIC Sector_hu"],
                          "leiras_en": r["Description"], "leiras_hu": r["Description_hu"]})
    for r in _xlsx(e / "WEF/WEF Sectors.xlsx"):
        nodes.append({"id": nid("wef", r["GH_CODE"]), "rendszer": "wef", "kod": r["GH_CODE"], "szint": 1,
                      "szint_nev": "sector", "label_en": r["EN"], "label_hu": r["HU"], "leiras_en": "",
                      "leiras_hu": ""})
    isic_wef, edges = {}, []
    for r in _xlsx(e / "WEF/ISIC_WEF_sectors.xlsx"):
        isic_wef[r["ISIC_sector_code"]] = r["related_wef_sector_code"]
        edges.append({"a": nid("isic", r["ISIC_sector_code"]), "b": nid("wef", r["related_wef_sector_code"]),
                      "forras": "gh-isic-wef", "match": "", "method": "manual", "primary": True,
                      "confidence": None})
    return nodes, edges, isic_wef


def _isic_codes(s: str) -> list[str]:
    return [x.strip() for x in str(s or "").replace(";", ",").split(",") if x.strip()]


def sector_edges(src: Sources, esco_dir: Path, jobs: dict[str, dict], isic_wef: dict[str, str]) -> list[dict]:
    """AGAZATBAN élek: ISCO/ESCO (occupation_isic.csv), O*NET-foglalkozás és SOC-főcsoport (származtatott)."""
    by_uri = {}
    with open(Path(esco_dir) / "ESCO/occupation_isic.csv", encoding="utf-8-sig", newline="") as fh:
        for r in csv.DictReader(fh):
            by_uri.setdefault(r["uri"], _isic_codes(r["ISIC_Code"]))
    out = []
    isco_by_uri = {g["uri"]: c for c, g in src.isco.items()}

    def add(a, codes, forras, db=None, total=None):
        for i, c in enumerate(codes):
            out.append({"a": a, "b": nid("isic", c), "forras": forras, "primary": i == 0,
                        "arany": round(db[c] / total, 3) if db else None, "db": db[c] if db else None,
                        "_total": total})
            if c in isic_wef:  # azonos WEF-re mutató szekciók élei a végén összevonódnak
                out.append({"a": a, "b": nid("wef", isic_wef[c]), "forras": forras + "+isic-wef",
                            "primary": i == 0, "arany": round(db[c] / total, 3) if db else None,
                            "db": db[c] if db else None, "_total": total})

    esco_isic = {}
    for uri, codes in by_uri.items():
        if uri in src.esco:
            esco_isic[uri] = codes
            add(nid("esco", src.esco[uri]["code"]), codes, "esco-isic")
        elif uri in isco_by_uri:
            add(nid("isco", isco_by_uri[uri]), codes, "esco-isic")

    # O*NET-foglalkozás: az ESCO-hivatkozásai ISIC-kódjaiból; az elsődleges ESCO első kódja az elsődleges
    major = defaultdict(Counter)
    for rec in jobs.values():
        onet = (rec.get("codes") or {}).get("onet")
        refs = (rec.get("refs") or {}).get("esco", [])
        if not onet or not refs:
            continue
        cnt, prim = Counter(), None
        for r in refs:
            codes = esco_isic.get(r.get("uri"), [])
            cnt.update(codes)
            if r.get("primary") and codes:
                prim = codes[0]
        if not cnt:
            continue
        order = sorted(cnt, key=lambda c: (c != prim, -cnt[c], c))
        add(nid("onet", onet), order, "job_all-esco-isic", cnt, sum(cnt.values()))
        major[onet[:2]][order[0]] += 1
    # SOC-főcsoport ("legfelső szint"): a foglalkozásai elsődleges ágazatának megoszlása
    for mg, cnt in major.items():
        order = sorted(cnt, key=lambda c: (-cnt[c], c))
        add(nid("soc", f"{mg}-0000"), order, "soc-major-aggregate", cnt, sum(cnt.values()))
    # több ISIC-szekció ugyanarra a WEF-szektorra mutathat: egy él, összeadott aránnyal és darabszámmal
    merged: dict[tuple, dict] = {}
    for e in out:
        k = (e["a"], e["b"], e["forras"])
        if k not in merged:
            merged[k] = dict(e)
            continue
        m = merged[k]
        m["primary"] = m["primary"] or e["primary"]
        if e["db"] is not None:
            m["db"] = (m["db"] or 0) + e["db"]
            m["arany"] = round(m["db"] / e["_total"], 3)
    return [{k: v for k, v in m.items() if k != "_total"} for m in merged.values()]


# ------------------------------------------------------------------ fő lépés
def build(esco_dir: Path, ref_dir: Path, jobs_path: Path | None) -> dict:
    src = Sources(esco_dir, ref_dir)
    jobs = jobs_from_file(jobs_path)
    nodes = occupation_nodes(src)
    sect, isic_wef_edges, isic_wef = sector_nodes(esco_dir)
    megfelel = crosswalk_edges(src) + job_all_edges(jobs) + isic_wef_edges
    agazat = sector_edges(src, esco_dir, jobs, isic_wef)
    ids = {n["id"] for n in nodes} | {s["id"] for s in sect}
    dropped = [e for e in megfelel + agazat if e["a"] not in ids or e["b"] not in ids]
    megfelel = [e for e in megfelel if e["a"] in ids and e["b"] in ids]
    agazat = [e for e in agazat if e["a"] in ids and e["b"] in ids]
    resze = [{"a": n["id"], "b": n["_parent"]} for n in nodes if n["_parent"] in ids]
    return {"nodes": [{k: v for k, v in n.items() if k != "_parent"} for n in nodes], "sectors": sect,
            "resze": resze, "megfelel": megfelel, "agazat": agazat, "dropped": len(dropped), "jobs": jobs}


def summary(t: dict) -> str:
    by = Counter(n["rendszer"] for n in t["nodes"]) + Counter(s["rendszer"] for s in t["sectors"])
    mf = Counter(e["forras"] for e in t["megfelel"])
    ag = Counter(e["forras"] for e in t["agazat"])
    return (f"csomópont: {dict(by)}; RESZE: {len(t['resze'])}; MEGFELEL: {dict(mf)}; AGAZATBAN: {dict(ag)}; "
            f"ismeretlen végpont miatt kihagyott él: {t['dropped']}")


def link_rows(graph, store, jobs: dict[str, dict]) -> list[dict]:
    """A gráfban már meglévő MunkaKor-csomópontok BESOROLVA-sorai (a tárolt rekord refs-e, ha van, különben a fájlé)."""
    rows = []
    for r in graph.run("MATCH (m:MunkaKor) RETURN m.id AS id"):
        rec = None
        try:
            rec = store.job(r["id"])
        except KeyError:
            pass
        if not (rec or {}).get("refs"):
            rec = jobs.get(r["id"])
        for x in ref_rows(rec or {}):
            rows.append({"job": r["id"], **x})
    return rows


def job_node_rows(jobs: dict[str, dict]) -> list[dict]:
    """Előzetes MunkaKor-sorok a job_all-rekordokból (TESZ-felbontás nélkül): {id, props, kodok}."""
    from .merge import job_codes, job_props

    return [{"id": jid, "props": job_props(rec), "kodok": job_codes(rec)} for jid, rec in sorted(jobs.items())]


def run_taxonomy(ctx, esco_dir: Path, ref_dir: Path, jobs_path: Path | None, link: bool = True,
                 dry_run: bool = False, create_jobs: bool = False, log=print) -> dict:
    t = build(esco_dir, ref_dir, jobs_path)
    log(summary(t))
    if create_jobs:
        log(f"Előzetes MunkaKor-csomópont: {len(t['jobs'])} (a {jobs_path} rekordjaiból, TESZ-felbontás nélkül)")
    if dry_run:
        log("Próbafutás: a gráf nem változott.")
        return t
    res = ctx.graph.write_taxonomy(t)
    log(f"Írva: {res}")
    if create_jobs:
        n = ctx.graph.write_job_nodes(job_node_rows(t["jobs"]))
        log(f"MunkaKor: {n['munkakor']} csomópont (új: előzetes, a meglévők név- és TESZ-adatai változatlanok)")
    if link:
        rows = link_rows(ctx.graph, ctx.store, t["jobs"])
        n = ctx.graph.write_job_besorolas(rows)
        log(f"BESOROLVA: {n['el']} él {n['munkakor']} meglévő munkakörhöz")
    return t
