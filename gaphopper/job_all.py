"""J0: egységes munkakör-rekordok (job_all.jsonl) O*NET-alapon, ESCO-, ISCO-, FEOR- és crawl-hivatkozással.

Egy rekord = egy feladatlistával rendelkező O*NET-foglalkozás (job_id `onet:<kód>`, mint a job_100-ban).
A többi besorolás a `refs` blokkba kerül, rendszerenként listaként, egységes elemekkel:

    {"system", "code", "uri", "name_en", "name_hu", "match", "method", "primary", "confidence", "path": [...]}

ahol `path` a gyökértől a levélig tartó hierarchia ({"level", "level_name", "code", "name_en", "name_hu"}).
A `codes` az elsődleges kódokat adja (onet, soc, isco, esco, feor). Új forrás = új kulcs a `refs`-ben.

Megfeleltetések: O*NET–ESCO a hivatalos ESCO–O*NET crosswalkból, ISCO az ESCO-foglalkozás csoportjából,
FEOR-08 az ISCO-08–FEOR-08 megfeleltetésből. Ahol ez nem egyértelmű (több FEOR-jelölt, nincs ESCO-pár),
az LLM a jelöltlistából választ (method: "llm"); a döntések gyorsítótárba kerülnek, újrafuttatás nem fizet újra.
A prompt szándékosan nem a `prompts/` mappában van (a `prompt_version` része lenne).
"""
from __future__ import annotations

import csv
import difflib
import json
import math
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

STEP = "J0"
MATCH_RANK = {"exactMatch": 0, "exactISCO": 0, "closeMatch": 1, "narrowMatch": 2, "broadMatch": 3}
ISCO_LEVEL = {1: "major group", 2: "sub-major group", 3: "minor group", 4: "unit group"}
FEOR_LEVEL = {1: "főcsoport", 2: "csoport", 3: "alcsoport", 4: "foglalkozás"}
SOC_LEVEL = {"Major": 1, "Minor": 2, "Broad": 3, "Detailed": 4}

# A crawl-katalógus foglalkozásai -> O*NET (kézi megfeleltetés, a crawl-katalógus 19 eleme)
CRAWL_ONET = {
    "SW_DEV": "15-1252.00", "QA_ENG": "15-1253.00", "SYSADMIN": "15-1244.00", "DATA_ANAL": "15-2051.01",
    "FIN_ANAL": "13-2051.00", "ACCOUNT": "13-2011.00", "PM": "13-1082.00", "HR_SPEC": "13-1071.00",
    "MARKETING": "13-1161.00", "DESIGNER": "27-1024.00", "CUST_SVC": "43-4051.00", "SALES": "41-4012.00",
    "ELECTRIC": "47-2111.00", "CNC": "51-9161.00", "LOGISTICS": "13-1081.00", "PENZTAROS": "41-2011.00",
    "TARGONCA": "53-7051.00", "OCC-4AF3E8": "53-3033.00", "OCC-5A8CFC": "15-1242.00",
}

SYSTEM = """Foglalkozási besorolások megfeleltetését döntöd el. Minden tételnél egy O*NET-foglalkozás
(cím, leírás, ESCO/ISCO-adatok) és jelöltlisták szerepelnek.
- Ha van `feor_jeloltek`: válaszd ki azt az egy FEOR-08 kódot, amely a foglalkozást a legjobban fedi.
- Ha van `esco_jeloltek`: válaszd ki azt az egy ESCO-foglalkozást (az azonosítóját, pl. "E3"), amely az
  O*NET-foglalkozás egészét a legjobban képviseli (ez adja a magyar megnevezést is). A jelölt mellett
  szereplő egyezéstípus (closeMatch stb.) a hivatalos megfeleltetésé, de lehet pontatlan: a tartalom döntsön
  (pl. "Industrial Truck and Tractor Operators" -> forklift operator, nem moving truck driver).
  Add meg a viszonyt is: closeMatch (lényegében ugyanaz), broadMatch (az ESCO tágabb), narrowMatch (az ESCO
  szűkebb). Mindig válassz, a legközelebbit akkor is, ha nem tökéletes.
Csak a megadott jelöltek közül választhatsz. `confidence`: 0–1.
Csak JSON-t adj vissza: {"items": [{"id": "...", "feor": "kód vagy null", "esco": "E.. vagy null",
"esco_match": "closeMatch|broadMatch|narrowMatch vagy null", "confidence": 0.0}]}"""


class Decision(BaseModel):
    model_config = ConfigDict(extra="ignore")
    id: str
    feor: str | None = None
    esco: str | None = None
    esco_match: str | None = None
    confidence: float = Field(default=0.5, ge=0, le=1)


class Decisions(BaseModel):
    items: list[Decision]


# ------------------------------------------------------------------ betöltés
def _csv(path: Path, **kw) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return list(csv.DictReader(fh, **kw))


def _xlsx(path: Path, header_row: int = 0) -> list[dict]:
    import openpyxl

    ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
    rows = list(ws.iter_rows(values_only=True))
    head = [str(h).strip() if h is not None else "" for h in rows[header_row]]
    return [{head[i]: r[i] for i in range(len(head)) if head[i]} for r in rows[header_row + 1:] if r and r[0]]


def _lines(s) -> list[str]:
    return [x.strip() for x in str(s or "").splitlines() if x.strip()]


class Sources:
    def __init__(self, esco_dir: Path, ref_dir: Path, statements: Path | None = None, ratings: Path | None = None):
        from .onet import load_tasks

        e = Path(esco_dir)
        self.onet = {str(r["O*NET-SOC Code"]): {"title": r["Title"], "desc": r["Description"]}
                     for r in _xlsx(e / "ONET/Occupation Data.xlsx")}
        alt = defaultdict(list)
        for r in _xlsx(e / "ONET/Alternate Titles.xlsx"):
            alt[str(r["O*NET-SOC Code"])].append(str(r["Alternate Title"]))
        self.onet_alt = alt
        self.soc = {str(r["SOC Code"]).strip(): (r["SOC Group"], str(r["SOC Title"]).strip())
                    for r in _xlsx(e / "ONET/soc_2018_definitions.xlsx", header_row=7) if r.get("SOC Code")}
        self.tasks = load_tasks(statements, ratings) if statements else {}

        self.esco = {}
        for r in _csv(e / "ESCO/occupations_enhu.csv"):
            self.esco[r["conceptUri"]] = {"code": r["code"], "isco": r["iscoGroup"].zfill(4), "en": r["preferredLabel"],
                                          "hu": r["preferredLabel_hu"], "alt_en": _lines(r["altLabels"]),
                                          "alt_hu": _lines(r["altLabels_hu"]), "desc": r.get("description") or ""}
        self.esco_by_code = {v["code"]: u for u, v in self.esco.items()}
        self.isco = {}
        for r in _xlsx(e / "ESCO/ISCOGroups_enhu.xlsx"):
            self.isco[str(r["code"]).strip()] = {"en": r["preferredLabel"], "hu": r["preferredLabel_hu"],
                                                 "uri": r["conceptUri"]}
        with open(e / "ESCO/ONET_ESCO_crosswalk.csv", encoding="utf-8-sig", newline="") as fh:
            rows = list(csv.reader(fh))
        hi = next(i for i, r in enumerate(rows) if r and r[0] == "O*NET Id")
        self.cw = defaultdict(list)
        for r in rows[hi + 1:]:
            if r and r[0]:
                self.cw[r[0]].append({"uri": r[3], "match": r[6]})

        self.feor = {r["code"]: {"name": r["name"].strip(), "level": int(r["level"]), "parent": r["parent_code"]}
                     for r in _csv(Path(ref_dir) / "feor08_hierarchy.csv")}
        self.isco_feor = defaultdict(list)
        for r in _csv(Path(ref_dir) / "isco08_feor08_crosswalk.csv"):
            if r["feor08"] not in self.isco_feor[r["isco08"]]:
                self.isco_feor[r["isco08"]].append(r["feor08"])

    # ---------------------------------------------------------------- hierarchiautak
    def soc_path(self, onet_code: str) -> list[dict]:
        d = onet_code[:7]
        cands = [f"{d[:2]}-0000", f"{d[:4]}000", f"{d[:6]}0", d]
        out = []
        for c in cands:
            if c in self.soc and (not out or out[-1]["code"] != c):
                grp, title = self.soc[c]
                out.append({"level": SOC_LEVEL[grp], "level_name": grp.lower(), "code": c,
                            "name_en": title, "name_hu": ""})
        o = self.onet[onet_code]
        out.append({"level": 5, "level_name": "O*NET occupation", "code": onet_code, "name_en": o["title"],
                    "name_hu": ""})
        return out

    def isco_path(self, code: str) -> list[dict]:
        return [{"level": n, "level_name": ISCO_LEVEL[n], "code": code[:n], "name_en": self.isco[code[:n]]["en"],
                 "name_hu": self.isco[code[:n]]["hu"]} for n in range(1, len(code) + 1) if code[:n] in self.isco]

    def esco_path(self, uri: str) -> list[dict]:
        o = self.esco[uri]
        path = self.isco_path(o["isco"])
        parts = o["code"].split(".")
        for n in range(2, len(parts) + 1):
            u = self.esco_by_code.get(".".join(parts[:n]))
            if u:
                path.append({"level": 3 + n, "level_name": "ESCO occupation", "code": ".".join(parts[:n]),
                             "name_en": self.esco[u]["en"], "name_hu": self.esco[u]["hu"]})
        return path

    def feor_path(self, code: str) -> list[dict]:
        out, c = [], code
        while c and c in self.feor:
            f = self.feor[c]
            out.append({"level": f["level"], "level_name": FEOR_LEVEL.get(f["level"], ""), "code": c,
                        "name_en": "", "name_hu": f["name"]})
            c = f["parent"]
        return out[::-1]

    def feor_candidates(self, isco_codes: list[str]) -> list[str]:
        for i in isco_codes:  # 4 jegy
            hit = [f for f in self.isco_feor.get(i, []) if len(f) == 4 and f in self.feor]
            if hit:
                return hit
        for i in isco_codes:  # 3 jegyű csoport -> a FEOR-alcsoport foglalkozásai
            subs = self.isco_feor.get(i[:3], [])
            kids = [c for c, f in self.feor.items() if f["level"] == 4 and f["parent"] in subs]
            if kids:
                return kids
        return []


# ------------------------------------------------------------------ jelöltkeresés (ESCO-pár nélküli O*NET-hez)
_TOK = re.compile(r"[a-z]+")


def _toks(s: str) -> list[str]:
    return [t.rstrip("s") for t in _TOK.findall(s.lower()) if len(t) > 2]


class EscoIndex:
    def __init__(self, src: Sources):
        self.docs = {u: Counter(_toks(" ".join([o["en"], *o["alt_en"]]))) for u, o in src.esco.items()}
        df = Counter(t for c in self.docs.values() for t in c)
        n = len(self.docs)
        self.idf = {t: math.log(n / (1 + d)) for t, d in df.items()}

    def top(self, text: str, k: int = 25) -> list[str]:
        q = set(_toks(text))
        sc = {u: sum(self.idf.get(t, 0) for t in q & c.keys()) / math.sqrt(1 + len(c)) for u, c in self.docs.items()}
        return [u for u, s in sorted(sc.items(), key=lambda x: -x[1])[:k] if s > 0]


# ------------------------------------------------------------------ rekordépítés
def _sim(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a.lower(), b.lower()).ratio()


def _ref(system, code, name_en="", name_hu="", match="", method="crosswalk", primary=False, confidence=None,
         uri="", path=None, **extra) -> dict:
    r = {"system": system, "code": code, "uri": uri, "name_en": name_en, "name_hu": name_hu, "match": match,
         "method": method, "primary": primary}
    if confidence is not None:
        r["confidence"] = round(float(confidence), 2)
    r["path"] = path or []
    r.update(extra)
    return r


def plan(src: Sources) -> dict[str, dict]:
    """O*NET-kódonként a determinisztikus megfeleltetés és a nyitott (LLM-es) kérdések."""
    out = {}
    for code in sorted(src.onet):
        if not src.tasks.get(code):
            continue
        rows = src.cw.get(code, [])
        esco_rows = [r for r in rows if r["uri"] in src.esco]
        isco_rows = [r for r in rows if "/isco/" in r["uri"]]
        title = src.onet[code]["title"]
        esco_rows.sort(key=lambda r: (MATCH_RANK.get(r["match"], 9), -_sim(title, src.esco[r["uri"]]["en"])))
        p = {"code": code, "esco_rows": esco_rows, "isco_rows": isco_rows, "esco_open": not esco_rows}
        isco_list = [src.esco[r["uri"]]["isco"] for r in esco_rows] + [r["uri"].rsplit("/C", 1)[-1] for r in isco_rows]
        p["isco_order"] = list(dict.fromkeys(isco_list))
        p["feor_cands"] = src.feor_candidates(p["isco_order"]) if p["isco_order"] else []
        out[code] = p
    return out


def _needs_pick(p: dict) -> bool:
    """Több crosswalk-ESCO-jelölt, és nincs közöttük egyetlen pontos egyezés: az elsődlegest az LLM választja."""
    rows = p["esco_rows"]
    return len(rows) > 1 and sum(r["match"] == "exactMatch" for r in rows) != 1


def _label(src: Sources, u: str, match: str = "") -> str:
    e = src.esco[u]
    return f"{e['en']} / {e['hu']} (ISCO {e['isco']}{', ' + match if match else ''})"


def esco_items(src: Sources, plans: dict, idx: "EscoIndex | None") -> list[dict]:
    """Elsődleges ESCO-döntést igénylő O*NET-foglalkozások.

    - nincs ESCO-pár: jelöltek az ISCO-egyezés csoportjából és szöveges hasonlóságból (`esco_match` is kell);
    - több crosswalk-jelölt egyértelmű pontos egyezés nélkül: a crosswalk-jelöltek közül választ.
    """
    items = []
    for code, p in plans.items():
        o = src.onet[code]
        base = {"id": code, "title": o["title"], "alt": src.onet_alt.get(code, [])[:10], "leiras": o["desc"][:350]}
        if p["esco_open"]:
            uris = []
            for r in p["isco_rows"]:
                g = r["uri"].rsplit("/C", 1)[-1]
                uris += [u for u, e in src.esco.items() if e["isco"] == g]
            uris = sorted(set(uris), key=lambda u: -_sim(o["title"], src.esco[u]["en"]))[:15]
            text = " ".join([o["title"], *src.onet_alt.get(code, [])[:15], o["desc"]])
            uris = list(dict.fromkeys(uris + (idx.top(text, 40) if idx else [])))[:30]
            items.append(base | {"esco_jeloltek": {f"E{i + 1}": _label(src, u) for i, u in enumerate(uris)},
                                 "_esco_uris": uris, "_open": True})
        elif _needs_pick(p):
            rows = p["esco_rows"][:30]
            items.append(base | {"esco_jeloltek": {f"E{i + 1}": _label(src, r["uri"], r["match"])
                                                   for i, r in enumerate(rows)},
                                 "_esco_uris": [r["uri"] for r in rows], "_open": False})
    return items


def apply_esco(src: Sources, plans: dict, dec: dict) -> None:
    for code, d in dec.items():
        p = plans.get(code)
        if not p or d.get("esco") not in src.esco:
            continue
        if p["esco_open"]:
            p["esco_rows"] = [{"uri": d["esco"], "match": d["esco_match"], "method": "llm",
                               "confidence": d.get("confidence")}]
            p["esco_open"] = False
        else:
            chosen = next((r for r in p["esco_rows"] if r["uri"] == d["esco"]), None)
            if not chosen:
                continue
            chosen = chosen | {"method": "crosswalk+llm", "confidence": d.get("confidence")}
            p["esco_rows"] = [chosen] + [r for r in p["esco_rows"] if r["uri"] != d["esco"]]
        isco = [src.esco[r["uri"]]["isco"] for r in p["esco_rows"]]
        isco += [r["uri"].rsplit("/C", 1)[-1] for r in p["isco_rows"]]
        p["isco_order"] = list(dict.fromkeys(isco))
        p["feor_cands"] = src.feor_candidates(p["isco_order"])


def feor_items(src: Sources, plans: dict) -> list[dict]:
    items = []
    for code, p in plans.items():
        if len(p["feor_cands"]) > 1:
            o, e = src.onet[code], src.esco[p["esco_rows"][0]["uri"]] if p["esco_rows"] else None
            items.append({"id": code, "title": o["title"], "leiras": o["desc"][:350],
                          "esco": f"{e['en']} / {e['hu']} (ISCO {e['isco']})" if e else None,
                          "feor_jeloltek": {c: src.feor[c]["name"] for c in p["feor_cands"]}})
    return items


def resolve(ctx, items: list[dict], cache: dict, cache_path: Path, kind: str, chunk: int = 20, log=print) -> dict:
    """LLM-döntés a jelöltlistákból; `cache[kind]` gyorsítótár (újrafuttatás nem fizet újra)."""
    from .llm import call_json

    done = cache.setdefault(kind, {})
    todo = [i for i in items if i["id"] not in done]
    log(f"{kind}: {len(items)} nyitott tétel, gyorsítótárban {len(items) - len(todo)}, LLM-re vár {len(todo)}")
    if not todo or ctx is None:
        return done
    cfg = ctx.settings.p1
    for k in range(0, len(todo), chunk):
        part = todo[k:k + chunk]
        user = json.dumps([{x: v for x, v in i.items() if not x.startswith("_")} for i in part], ensure_ascii=False,
                          indent=1)

        def check(obj, part=part):
            by = {d.id: d for d in obj.items}
            for i in part:
                d = by.get(i["id"])
                if d is None:
                    raise ValueError(f"Hiányzik a döntés: {i['id']}")
                if "feor_jeloltek" in i and d.feor not in i["feor_jeloltek"]:
                    raise ValueError(f"{i['id']}: a FEOR-kód nincs a jelöltek között: {d.feor}")
                if "esco_jeloltek" in i and (d.esco not in i["esco_jeloltek"] or (i.get("_open") and
                                             d.esco_match not in ("closeMatch", "broadMatch", "narrowMatch"))):
                    raise ValueError(f"{i['id']}: érvénytelen ESCO-választás: {d.esco} {d.esco_match}")

        obj, _ = call_json(ctx.llm("P1"), SYSTEM, user, Decisions, retries=1, check=check,
                           log=lambda raw, ok, usage: ctx.store.log("job_all", STEP, cfg.provider, cfg.model, ctx.pv,
                                                                   "", raw, ok, usage))
        for i in part:
            d = next(x for x in obj.items if x.id == i["id"])
            rec = {"confidence": d.confidence}
            if "feor_jeloltek" in i:
                rec["feor"] = d.feor
            if "esco_jeloltek" in i:
                rec["esco"] = i["_esco_uris"][int(d.esco[1:]) - 1]
                rec["esco_match"] = d.esco_match
            done[i["id"]] = rec
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"  {min(k + chunk, len(todo))}/{len(todo)} eldöntve")
    return done


def build_record(src: Sources, p: dict, dec: dict, job100: dict | None, crawl: list[dict], fetched_at: str) -> dict:
    code = p["code"]
    o = src.onet[code]
    t = src.tasks[code]
    # ESCO
    esco_refs = []
    rows = list(p["esco_rows"])
    for n, r in enumerate(rows):
        e = src.esco[r["uri"]]
        esco_refs.append(_ref("esco", e["code"], e["en"], e["hu"], r["match"], r.get("method", "crosswalk"), n == 0,
                              r.get("confidence"), r["uri"], src.esco_path(r["uri"])))
    prim_esco = src.esco[rows[0]["uri"]] if rows else None
    # ISCO
    isco_order = ([prim_esco["isco"]] if prim_esco else []) + p["isco_order"]
    isco_order = [i for i in dict.fromkeys(isco_order) if i in src.isco]
    best_match = {}
    for r in p["esco_rows"]:
        g = src.esco[r["uri"]]["isco"]
        best_match[g] = min(best_match.get(g, "broadMatch"), r["match"], key=lambda m: MATCH_RANK.get(m, 9))
    for r in p["isco_rows"]:
        best_match[r["uri"].rsplit("/C", 1)[-1]] = "exactMatch"
    isco_refs = [_ref("isco", g, src.isco[g]["en"], src.isco[g]["hu"],
                      best_match.get(g, rows[0]["match"] if rows else ""),
                      "crosswalk" if g in best_match else "via-esco", n == 0, uri=src.isco[g]["uri"],
                      path=src.isco_path(g)) for n, g in enumerate(isco_order)]
    # FEOR
    cands = p["feor_cands"]
    prim_feor = dec.get("feor") if len(cands) > 1 and dec.get("feor") else (cands[0] if cands else None)
    feor_refs = [_ref("feor", c, "", src.feor[c]["name"], "", ("crosswalk+llm" if len(cands) > 1 else "crosswalk"),
                      c == prim_feor, dec.get("confidence") if (c == prim_feor and len(cands) > 1) else None,
                      path=src.feor_path(c)) for c in sorted(cands, key=lambda c: c != prim_feor)]
    # O*NET
    onet_refs = [_ref("onet", code, o["title"], "", "exactMatch", "source", True,
                      uri=f"https://www.onetonline.org/link/summary/{code}", path=src.soc_path(code))]
    # crawl
    crawl_refs = []
    for c in crawl:
        cc = c["crawl"]
        crawl_refs.append(_ref("crawl", cc["gaphopper_id"], c.get("alt_titles", [""])[0] if c.get("alt_titles") else "",
                               c["title"], "closeMatch", "manual", True,
                               postings=cc["postings"], portals=cc.get("portals", {}), field_hu=c.get("field", ""),
                               description_hu=c.get("description", ""), tasks_hu=c.get("tasks", []),
                               requirements_hu=c.get("requirements", ""), conditions_hu=c.get("conditions", ""),
                               entry_route_hu=c.get("entry_route", ""), source=c.get("source", "")))

    title_hu = prim_esco["hu"] if prim_esco else (crawl[0]["title"] if crawl else o["title"])
    alt_hu = list(dict.fromkeys([*(c["title"] for c in crawl), *(prim_esco["alt_hu"] if prim_esco else [])]))
    alt_hu = [a for a in alt_hu if a.casefold() != title_hu.casefold()][:12]
    isco2 = src.isco.get(isco_order[0][:2]) if isco_order else None
    rec = {
        "job_id": f"onet:{code}",
        "title": title_hu,
        "title_en": o["title"],
        "alt_titles": [a for a in src.onet_alt.get(code, []) if a.casefold() != o["title"].casefold()][:15],
        "alt_titles_hu": alt_hu,
        "field": (job100 or {}).get("field") or (f"{isco2['hu']} / {isco2['en']}" if isco2 else ""),
        "description": o["desc"],
        "description_hu": crawl[0].get("description", "") if crawl else "",
        "tasks": t["core"],
        "tasks_supplemental": t["supplemental"],
        "tasks_source": "O*NET Task Statements (fontossági sorrend: Task Ratings IM), CC BY 4.0",
        "requirements": (job100 or {}).get("requirements") or (crawl[0].get("requirements", "") if crawl else ""),
        "conditions": (job100 or {}).get("conditions") or (crawl[0].get("conditions", "") if crawl else ""),
        "entry_route": (job100 or {}).get("entry_route") or (crawl[0].get("entry_route", "") if crawl else ""),
        "codes": {"onet": code, "soc": code[:7],
                  "isco": isco_order[0] if isco_order else "",
                  "esco": rows[0]["uri"] if rows else "",
                  "esco_code": prim_esco["code"] if prim_esco else "",
                  "feor": prim_feor or ""},
        "refs": {"onet": onet_refs, "esco": esco_refs, "isco": isco_refs, "feor": feor_refs, **(
            {"crawl": crawl_refs} if crawl_refs else {})},
        "sets": ["job_all"] + (["job_100"] if job100 else []) + (["crawl"] if crawl else []),
        "source": "O*NET (Occupation Data, Task Statements) + ESCO v1.1 (ESCO–O*NET crosswalk) + ISCO-08 + "
                  "FEOR-08 (ISCO-08–FEOR-08 megfeleltetés)" + (" + crawler-hirdetések" if crawl else ""),
        "source_url": f"https://www.onetonline.org/link/summary/{code}",
        "fetched_at": fetched_at,
        "locale": "hu-HU",
        "source_lang": "en",
    }
    if job100:  # a job_100 kurált mezői (rank, tier, outlook, ...) megmaradnak
        for k, v in job100.items():
            if k not in rec and k not in ("tasks_supplemental",):
                rec[k] = v
    return rec


def run_job_all(ctx, esco_dir: Path, ref_dir: Path, statements: Path, ratings: Path | None, out: Path,
                job100_path: Path | None, crawl_path: Path | None, cache_path: Path, dry_run: bool = False,
                fetched_at: str = "", log=print) -> dict:
    src = Sources(esco_dir, ref_dir, statements, ratings)
    plans = plan(src)
    log(f"O*NET-foglalkozás: {len(src.onet)}, feladatlistával: {len(plans)}")
    cache = json.loads(cache_path.read_text(encoding="utf-8")) if cache_path.exists() else {}
    llm_ctx = None if dry_run else ctx
    e_items = esco_items(src, plans, EscoIndex(src)) if any(p["esco_open"] for p in plans.values()) else []
    apply_esco(src, plans, resolve(llm_ctx, e_items, cache, cache_path, "esco", log=log))
    f_items = feor_items(src, plans)
    fdec = resolve(llm_ctx, f_items, cache, cache_path, "feor", log=log)
    if dry_run:
        pth = out.parent / (out.stem + "_open_items.json")
        pth.write_text(json.dumps({"esco": e_items, "feor": f_items}, ensure_ascii=False, indent=1), encoding="utf-8")
        log(f"Próbafutás: a nyitott tételek -> {pth}; a rekordok LLM-döntés nélkül (első jelölt) készülnek.")
    job100 = {}
    if job100_path and Path(job100_path).exists():
        for line in Path(job100_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                job100[r.get("codes", {}).get("onet") or r["job_id"].split(":", 1)[-1]] = r
    crawl = defaultdict(list)
    if crawl_path and Path(crawl_path).exists():
        for line in Path(crawl_path).read_text(encoding="utf-8").splitlines():
            if line.strip():
                r = json.loads(line)
                oc = CRAWL_ONET.get(r["crawl"]["gaphopper_id"])
                if oc:
                    crawl[oc].append(r)

    recs, missing = [], Counter()
    for code, p in plans.items():
        r = build_record(src, p, fdec.get(code, {}), job100.get(code), crawl.get(code, []),
                         fetched_at or date.today().isoformat())
        for k in ("esco", "feor", "isco"):
            if not r["codes"][k]:
                missing[k] += 1
        recs.append(r)
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    lost100 = sorted(set(job100) - set(plans))
    log(f"{len(recs)} rekord -> {out}; job_100-ból: {sum('job_100' in r['sets'] for r in recs)}"
        f"{' (kimaradt: ' + ', '.join(lost100) + ')' if lost100 else ''}; crawl-lal: "
        f"{sum('crawl' in r['sets'] for r in recs)}; hiányzó elsődleges kód: {dict(missing) or 'nincs'}")
    return {"records": len(recs), "missing": dict(missing), "open": len(e_items) + len(f_items)}
