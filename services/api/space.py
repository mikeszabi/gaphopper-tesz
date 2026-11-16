"""Kompetenciatér: a kompetenciák 2D-elrendezése együttes előfordulásuk alapján, klaszterekkel és illeszkedéssel.

- Hasonlóság: két kompetencia koszinusz-hasonlósága a TESZ-SZÍN-ekben való együttes előfordulás alapján, az összes
  felbontott munkakörön számolva: sim(a, b) = n(a, b) / sqrt(n(a) · n(b)).
- Előzetes (prior): a KÉSZ-hierarchia (azonos csoport 1, azonos kategória 0,4) 25% súllyal keveredik az együttes
  előforduláshoz; így a ritka, más kompetenciával együtt alig szereplő elemek is a rokonaik közelébe kerülnek, és nem
  préselik szét az elrendezést. sim = 0,75 · cooc + 0,25 · prior.
- Távolság: a k legközelebbi szomszéd gráfján (élsúly 1 − sim) vett legrövidebb út (Isomap-szerű); így a lánc
  mentén összefüggő kompetenciák is értelmes távolságot kapnak. Az össze nem függő részek a legnagyobb távolság
  1,2-szeresére kerülnek.
- Elrendezés: klasszikus többdimenziós skálázás (MDS) három dimenzióba (determinisztikus).
- Klaszterszétválasztás: a klaszterközéppontok SEPARATION-szörösre tolódnak a tér középpontjától, a tagok
  COHESION-szörösre húzódnak a saját középpontjukhoz; a klaszteren belüli viszonyok megmaradnak. Végül [-1, 1]-be
  skálázva.
- Klaszterek: átlagos kapcsolású hierarchikus csoportosítás ugyanezen a távolságon; a név a tagok leggyakoribb
  KÉSZ-csoportja.
"""
from __future__ import annotations

import math
from collections import Counter
from itertools import combinations

import numpy as np

K_NEIGHBOURS = 6
SEPARATION = 2.2
COHESION = 0.7
PRIOR_WEIGHT = 0.25
PRIOR = {"group": 1.0, "category": 0.4}


def cooccurrence(profiles: dict[str, dict]) -> tuple[Counter, Counter]:
    single, pair = Counter(), Counter()
    for p in profiles.values():
        for t in p["tesz"]:
            ids = sorted(set(t["competency_ids"]))
            single.update(ids)
            pair.update(combinations(ids, 2))
    return single, pair


def similarity(ids: list[str], single: Counter, pair: Counter, catalog: dict | None = None) -> np.ndarray:
    n = len(ids)
    s = np.zeros((n, n))
    meta = [(catalog or {}).get(i, {}) for i in ids]
    for i in range(n):
        for j in range(i + 1, n):
            a, b = ids[i], ids[j]
            c = pair.get((a, b) if a < b else (b, a), 0)
            cooc = c / math.sqrt(single[a] * single[b]) if c else 0.0
            prior = 0.0
            if catalog:
                if meta[i].get("group") and meta[i].get("group") == meta[j].get("group"):
                    prior = PRIOR["group"]
                elif meta[i].get("category") and meta[i].get("category") == meta[j].get("category"):
                    prior = PRIOR["category"]
                s[i, j] = s[j, i] = (1 - PRIOR_WEIGHT) * cooc + PRIOR_WEIGHT * prior
            else:
                s[i, j] = s[j, i] = cooc
    np.fill_diagonal(s, 1.0)
    return s


def geodesic(sim: np.ndarray, k: int = K_NEIGHBOURS) -> np.ndarray:
    n = len(sim)
    d = np.full((n, n), np.inf)
    np.fill_diagonal(d, 0)
    for i in range(n):
        order = [j for j in np.argsort(-sim[i]) if j != i and sim[i, j] > 0][:k]
        for j in order:
            w = 1 - sim[i, j] + 0.05          # kis alapköltség: az azonos profilú elemek se essenek egy pontba
            d[i, j] = d[j, i] = min(d[i, j], w)
    for m in range(n):                          # Floyd–Warshall (n legfeljebb néhány száz)
        d = np.minimum(d, d[:, m:m + 1] + d[m:m + 1, :])
    finite = d[np.isfinite(d)]
    cap = (finite.max() if finite.size else 1.0) * 1.2 or 1.0
    d[~np.isfinite(d)] = cap
    return d


def mds(d: np.ndarray, dims: int = 2) -> np.ndarray:
    n = len(d)
    if n == 1:
        return np.full((1, dims), 0.5)
    j = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * j @ (d ** 2) @ j
    w, v = np.linalg.eigh(b)
    idx = np.argsort(w)[::-1][:dims]
    xy = v[:, idx] * np.sqrt(np.maximum(w[idx], 1e-9))
    if xy.shape[1] < dims:                      # kevés pont: hiányzó dimenzió nullával
        xy = np.hstack([xy, np.zeros((n, dims - xy.shape[1]))])
    for c in range(dims):                       # előjel rögzítése: determinisztikus tájolás
        if xy[np.argmax(np.abs(xy[:, c])), c] < 0:
            xy[:, c] *= -1
    lo, hi = xy.min(axis=0), xy.max(axis=0)
    span = np.where(hi - lo > 1e-9, hi - lo, 1)
    return (xy - lo) / span


def clusters(d: np.ndarray, k: int) -> list[int]:
    """Átlagos kapcsolású agglomeratív csoportosítás k klaszterre."""
    n = len(d)
    groups = [[i] for i in range(n)]
    while len(groups) > k:
        best, pair = math.inf, (0, 1)
        for a in range(len(groups)):
            for b in range(a + 1, len(groups)):
                v = d[np.ix_(groups[a], groups[b])].mean()
                if v < best:
                    best, pair = v, (a, b)
        a, b = pair
        groups[a] += groups.pop(b)
    label = [0] * n
    for g, members in enumerate(sorted(groups, key=lambda m: (-len(m), min(m)))):
        for i in members:
            label[i] = g
    return label


def separate(p: np.ndarray, lab: list[int]) -> np.ndarray:
    """Klaszterek széthúzása: középpont × SEPARATION + (pont − középpont) × COHESION, majd [-1, 1]-be skálázás."""
    out = p.copy()
    lab = np.asarray(lab)
    for c in set(lab.tolist()):
        m = lab == c
        cen = p[m].mean(axis=0)
        out[m] = cen * SEPARATION + (p[m] - cen) * COHESION
    out -= out.mean(axis=0)
    scale = np.abs(out).max() or 1.0
    return out / scale


def build_space(data: dict, gaps: list[dict], person: dict | None) -> dict:
    """Csomópontok: a kiválasztott munkakörök és az egyén kompetenciái. gaps: a lab.gap eredményei munkakörönként
    (személy nélkül a puszta profilok, állapot nélkül)."""
    catalog, profiles = data["competencies"], data["profiles"]
    if "cooc" not in data:
        data["cooc"] = cooccurrence(profiles)
    single, pair = data["cooc"]
    own = {c["id"]: c.get("eqf") for c in (person or {}).get("competencies", [])}
    ids = list(dict.fromkeys([c["id"] for g in gaps for c in g["competencies"]] + list(own)))
    ids = [i for i in ids if i in catalog]
    if not ids:
        return {"nodes": [], "edges": [], "clusters": [], "jobs": [], "person": None}
    sim = similarity(ids, single, pair, catalog)
    dist = geodesic(sim)
    k = max(2, min(8, round(math.sqrt(len(ids) / 2)))) if len(ids) >= 4 else 1
    lab = clusters(dist, k) if k > 1 else [0] * len(ids)
    xy = separate(mds(dist, 3) * 2 - 1, lab)
    pos = {cid: i for i, cid in enumerate(ids)}

    nodes = []
    for i, cid in enumerate(ids):
        c = catalog[cid]
        per_job = {}
        for g in gaps:
            hit = next((x for x in g["competencies"] if x["id"] == cid), None)
            if hit:
                per_job[g["job_id"]] = {k2: hit.get(k2) for k2 in ("status", "required_eqf", "person_eqf", "importance", "weight", "shortfall")}
        nodes.append({"id": cid, "label": c["label"], "group_label": c.get("group_label", ""),
                      "category_label": c.get("category_label", ""), "x": round(float(xy[i, 0]), 4),
                      "y": round(float(xy[i, 1]), 4), "z": round(float(xy[i, 2]), 4), "cluster": lab[i], "own": cid in own,
                      "own_eqf": own.get(cid), "jobs": per_job,
                      "importance": max([v["importance"] or 0 for v in per_job.values()], default=0)})
    # hálózat: minden csomópont legerősebb 2 kapcsolata (ritka, recesszív háttér)
    edges = set()
    for i in range(len(ids)):
        for j in [j for j in np.argsort(-sim[i]) if j != i and sim[i, j] >= 0.2][:2]:
            edges.add((min(i, j), max(i, j)))
    edges = [{"a": ids[i], "b": ids[j], "w": round(float(sim[i, j]), 3)} for i, j in sorted(edges)]

    def centroid(weights: dict[str, float]):
        tot = sum(weights.values())
        if not tot:
            return None
        return {a: round(float(sum(xy[pos[c], i] * w for c, w in weights.items()) / tot), 4)
                for i, a in enumerate("xyz")}

    jobs = []
    for g in gaps:
        w = {c["id"]: c["weight"] for c in g["competencies"] if c["id"] in pos}
        jobs.append({"job_id": g["job_id"], "title": g["title"], "fit": g.get("fit"), "eqf": g["eqf"]["typical"],
                     **(centroid(w) or {"x": 0.0, "y": 0.0, "z": 0.0})})
    me = centroid({c: 1.0 for c in own if c in pos}) if own else None

    cl = []
    for c in range(k):
        members = [n for n in nodes if n["cluster"] == c]
        groups = Counter(n["group_label"] for n in members if n["group_label"]).most_common(2)
        per_job = {}
        for g in gaps:
            req = [m["jobs"][g["job_id"]] for m in members if g["job_id"] in m["jobs"]]
            tw = sum(r["weight"] for r in req)
            if tw:
                per_job[g["job_id"]] = {"required": len(req),
                                        "fit": round(1 - sum(r["weight"] * (r["shortfall"] or 0) for r in req) / tw, 4),
                                        "missing": sum(1 for r in req if r["status"] == "missing"),
                                        "under": sum(1 for r in req if r["status"] == "under")}
        cl.append({"id": c, "label": groups[0][0] if groups else f"{c + 1}. klaszter",
                   "label_2": groups[1][0] if len(groups) > 1 else "", "size": len(members),
                   "own": sum(1 for m in members if m["own"]), "jobs": per_job,
                   "top": [m["label"] for m in sorted(members, key=lambda m: -m["importance"])[:4]]})
    seen = Counter(c["label"] for c in cl)
    for c in cl:
        if seen[c["label"]] > 1:
            c["label"] = f"{c['label']} · {c['label_2']}" if c["label_2"] else f"{c['label']} ({c['id'] + 1})"
    return {"nodes": nodes, "edges": edges, "clusters": cl, "jobs": jobs, "person": me,
            "method": {"neighbours": K_NEIGHBOURS, "separation": SEPARATION, "cohesion": COHESION, "clusters": k, "tesz_count": sum(len(p["tesz"]) for p in profiles.values())}}
