"""Kompetencialabor: munkaköri profil, egyéni profil, illeszkedés (GAP) és ajánlás.

Determinisztikus, magyarázható számítás a gráf adataiból (AI-hívás nélkül):

- TESZ-SZÍN súlya egy munkakörben: w = relevancia (CORE 1, SUPPORTING 0,5) × gyakoriság (DAILY 1, WEEKLY 0,85,
  MONTHLY 0,7, RARE 0,5).
- Munkakör EQF-szintje: a TESZ-SZÍN-ek expected_eqf értékének w-vel súlyozott mediánja; mellette a terjedelem.
- Kompetencia súlya a munkakörben: az őt igénylő TESZ-SZÍN-ek w-összege (a legnagyobbhoz viszonyítva 0..1).
- Kompetencia elvárt EQF-szintje: az őt igénylő CORE TESZ-SZÍN-ek legmagasabb EQF-je (ha nincs CORE: az összesé).
- Hiány egy kompetenciára: s = max(0, r - p) / r, ahol r az elvárt, p az egyéni szint (hiányzó kompetencia: p = 0;
  szint nélkül megadott kompetencia: az egyén általános EQF-szintje, ha az sincs, akkor elvárt szintűnek vesszük).
- Távolság: d = Σ w·s / Σ w (0 = teljes illeszkedés, 1 = semmi sem fedi); illeszkedés = 1 - d.
"""
from __future__ import annotations

import threading
import time

from gaphopper.config import vocab

REL_W = {"CORE": 1.0, "SUPPORTING": 0.5}
FREQ_W = {"DAILY": 1.0, "WEEKLY": 0.85, "MONTHLY": 0.7, "RARE": 0.5}
STATUS_HU = {"ok": "megvan", "under": "szint alatt", "missing": "hiányzik"}
CACHE_SECONDS = 300

PROFILE_QUERY = (
    "MATCH (m:MunkaKor)-[r:TARTALMAZ_TESZT]->(s:TeszSzin)-[:SZINEZI]->(t:TeszNivalo) WHERE m.elozetes IS NULL "
    "OPTIONAL MATCH (s)-[:KOZEGBEN_ZAJLIK {primary:true}]->(ko:Kozeg) "
    "OPTIONAL MATCH (s)-[:HELYZETBEN_VEGZENDO {primary:true}]->(he:Helyzet) "
    "OPTIONAL MATCH (t)-[i:IGENYEL]->(k:Keszseg) WHERE i.munkakorok IS NULL OR m.id IN i.munkakorok "
    "WITH m, r, s, t, ko, he, [x IN collect(DISTINCT {id: k.id, rang: i.rang}) WHERE x.id IS NOT NULL] AS ks "
    "RETURN m.id AS job_id, coalesce(m.label_hu, m.label_en, m.id) AS title, m.label_en AS title_en, "
    "coalesce(m.purpose_hu, '') AS purpose, coalesce(m.aliases, []) AS aliases, "
    "s.id AS szin_id, t.id AS tesz_id, t.label_hu AS tesz, r.relevance AS relevance, r.frequency AS frequency, "
    "r.expected_eqf AS eqf, coalesce(r.kontextus_hu, '') AS kontextus, ko.id AS kozeg, he.id AS helyzet, ks")

COMPETENCY_QUERY = (
    "MATCH (k:Keszseg) OPTIONAL MATCH (k)-[:CSOPORTJA]->(c:KeszsegCsoport) "
    "OPTIONAL MATCH (c)-[:KATEGORIAJA]->(kat:KeszsegKategoria) "
    "RETURN k.id AS id, coalesce(k.label_hu, k.label_en, k.id) AS label, coalesce(k.label_en, '') AS label_en, "
    "coalesce(k.leiras_hu, '') AS description, c.kod AS group, coalesce(c.label_hu, '') AS group_label, "
    "kat.kod AS category, coalesce(kat.label_hu, '') AS category_label")


def weighted_median(pairs: list[tuple[float, float]]) -> float | None:
    """(érték, súly) párok súlyozott mediánja; egész EQF-értékeknél egész szám jön ki."""
    pairs = sorted((v, w) for v, w in pairs if v is not None and w > 0)
    if not pairs:
        return None
    half, acc = sum(w for _, w in pairs) / 2, 0.0
    for v, w in pairs:
        acc += w
        if acc >= half - 1e-9:
            return v
    return pairs[-1][0]


def build_profiles(rows: list[dict], competencies: dict[str, dict]) -> dict[str, dict]:
    """A gráfsorokból munkakörönként profil: TESZ-SZÍN-ek, kompetenciák súllyal és elvárt EQF-szinttel."""
    v = vocab()
    kozeg = {k: x.get("hu", k) for k, x in v.get("kozeg", {}).items()}
    helyzet = {k: x.get("hu", k) for k, x in v.get("helyzet", {}).items()}
    jobs: dict[str, dict] = {}
    for r in rows:
        j = jobs.setdefault(r["job_id"], {"id": r["job_id"], "title": r["title"], "title_en": r.get("title_en") or "",
                                          "purpose": r.get("purpose") or "", "aliases": r.get("aliases") or [],
                                          "tesz": [], "_k": {}})
        w = REL_W.get(r.get("relevance"), 0.5) * FREQ_W.get(r.get("frequency"), 0.7)
        core = r.get("relevance") == "CORE"
        ids = [x["id"] for x in sorted(r.get("ks") or [], key=lambda x: x.get("rang") or 99)]
        j["tesz"].append({"id": r["szin_id"], "tesz_id": r["tesz_id"], "label": r["tesz"], "eqf": r.get("eqf"),
                          "relevance": r.get("relevance"), "frequency": r.get("frequency"), "weight": round(w, 3),
                          "kontextus": r.get("kontextus") or "", "kozeg": kozeg.get(r.get("kozeg"), r.get("kozeg") or ""),
                          "helyzet": helyzet.get(r.get("helyzet"), r.get("helyzet") or ""), "competency_ids": ids})
        for cid in ids:
            k = j["_k"].setdefault(cid, {"w": 0.0, "core": [], "all": [], "tesz": []})
            k["w"] += w
            if r.get("eqf") is not None:
                (k["core"] if core else k["all"]).append(r["eqf"])
            k["tesz"].append(r["szin_id"])
    out = {}
    for jid, j in jobs.items():
        acc = j.pop("_k")
        top = max((k["w"] for k in acc.values()), default=0) or 1
        comps = []
        for cid, k in acc.items():
            c = competencies.get(cid, {})
            levels = k["core"] or k["all"]
            comps.append({"id": cid, "label": c.get("label", cid), "group": c.get("group"),
                          "group_label": c.get("group_label", ""), "category": c.get("category"),
                          "category_label": c.get("category_label", ""), "weight": round(k["w"], 3),
                          "importance": round(k["w"] / top, 3), "required_eqf": max(levels) if levels else None,
                          "core": bool(k["core"]), "tesz": k["tesz"]})
        comps.sort(key=lambda c: (-c["weight"], c["label"]))
        eqfs = [t["eqf"] for t in j["tesz"] if t["eqf"] is not None]
        j["tesz"].sort(key=lambda t: (-t["weight"], t["label"]))
        out[jid] = j | {"competencies": comps,
                        "eqf": {"typical": weighted_median([(t["eqf"], t["weight"]) for t in j["tesz"]]),
                                "min": min(eqfs) if eqfs else None, "max": max(eqfs) if eqfs else None,
                                "core_max": max([t["eqf"] for t in j["tesz"] if t["relevance"] == "CORE"
                                                 and t["eqf"] is not None], default=None),
                                "distribution": {str(n): round(sum(t["weight"] for t in j["tesz"] if t["eqf"] == n), 3)
                                                 for n in range(1, 9)}}}
    return out


class LabData:
    """A profilok egyetlen gráflekérdezésből, rövid ideig gyorsítótárazva (a labor csak olvas)."""

    def __init__(self, repo):
        self.repo, self.lock, self.at, self.data = repo, threading.Lock(), 0.0, None

    def get(self) -> dict:
        with self.lock:
            if self.data is None or time.time() - self.at > CACHE_SECONDS:
                comps = {c["id"]: c for c in self.repo.query(COMPETENCY_QUERY)}
                self.data = {"competencies": comps, "profiles": build_profiles(self.repo.query(PROFILE_QUERY), comps)}
                self.at = time.time()
            return self.data


def person_levels(person: dict) -> tuple[dict[str, float | None], float | None]:
    return {c["id"]: c.get("eqf") for c in person.get("competencies", [])}, person.get("eqf")


def gap(profile: dict, person: dict) -> dict:
    """Egy munkakör és egy egyén összevetése: kompetenciánként állapot, összesítve távolság és lefedettség."""
    levels, overall = person_levels(person)
    rows, sw, sws, cov = [], 0.0, 0.0, 0.0
    for c in profile["competencies"]:
        r = c["required_eqf"]
        if c["id"] in levels:
            p = levels[c["id"]] if levels[c["id"]] is not None else (overall if overall is not None else r)
        else:
            p = None
        if p is None:
            status, s = "missing", 1.0
        elif r is None or p >= r:
            status, s = "ok", 0.0
        else:
            status, s = "under", (r - p) / r
        w = c["weight"]
        sw, sws = sw + w, sws + w * s
        cov += w if p is not None else 0
        rows.append({"id": c["id"], "label": c["label"], "group_label": c["group_label"],
                     "category_label": c["category_label"], "importance": c["importance"], "weight": w,
                     "required_eqf": r, "person_eqf": p, "status": status, "status_hu": STATUS_HU[status],
                     "shortfall": round(s, 3), "tesz": c["tesz"]})
    d = sws / sw if sw else 1.0
    typical = profile["eqf"]["typical"]
    return {"job_id": profile["id"], "title": profile["title"], "eqf": profile["eqf"],
            "distance": round(d, 4), "fit": round(1 - d, 4), "coverage": round(cov / sw, 4) if sw else 0.0,
            "eqf_delta": (typical - overall) if typical is not None and overall is not None else None,
            "counts": {s: sum(1 for x in rows if x["status"] == s) for s in STATUS_HU},
            "competencies": rows}


def extra_competencies(profiles: list[dict], person: dict, catalog: dict[str, dict]) -> list[dict]:
    """Az egyén kompetenciái, amelyeket a kiválasztott munkakörök egyike sem igényel."""
    need = {c["id"] for p in profiles for c in p["competencies"]}
    return [{"id": c["id"], "label": catalog.get(c["id"], {}).get("label", c["id"]), "eqf": c.get("eqf")}
            for c in person.get("competencies", []) if c["id"] not in need]


def recommend(profiles: dict[str, dict], person: dict, limit: int = 12, eqf_window: int | None = None) -> dict:
    """Minden felbontott munkakör illeszkedése az egyénhez; a legjobbak a hiányokkal együtt.
    eqf_window: csak azok a munkakörök, amelyek tipikus EQF-szintje legfeljebb ennyivel tér el az egyénétől."""
    _, overall = person_levels(person)
    scored = [gap(p, person) for p in profiles.values() if p["competencies"]]
    pool = [g for g in scored if eqf_window is None or overall is None or g["eqf"]["typical"] is None
            or abs(g["eqf"]["typical"] - overall) <= eqf_window]
    pool.sort(key=lambda g: (-g["fit"], -g["coverage"], g["title"]))
    best = pool[:limit]
    chosen = {g["job_id"] for g in best}

    def band(g):
        return "kesz" if g["fit"] >= 0.85 else "kozeli" if g["fit"] >= 0.6 else "fejlesztendo"
    for g in best:
        g["band"] = band(g)
        g["missing"] = [c for c in g["competencies"] if c["status"] != "ok"]
        g["missing"].sort(key=lambda c: -c["weight"] * c["shortfall"])
    return {"items": best, "total": len(scored), "filtered": len(pool),
            "scatter": [{"job_id": g["job_id"], "title": g["title"], "fit": g["fit"], "coverage": g["coverage"],
                         "eqf": g["eqf"]["typical"], "selected": g["job_id"] in chosen} for g in scored]}
