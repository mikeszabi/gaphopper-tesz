"""C0: munkakör-rekordok (jobs.jsonl) a crawler PostgreSQL-adatbázisából.

Foglalkozásonként egy rekord: a hirdetésekből kivont feladat-, elvárás- és juttatásstatisztikából
(`occupation_evidence_stats`) és néhány hirdetésszöveg-mintából az LLM összevont, tisztított
feladatlistát és leírást ír. A gráfot és a munkakör-táblát nem érinti; az import külön lépés.

A prompt szándékosan nem a `prompts/` mappában van: az ottani fájlok a `prompt_version` részei,
egy új fájl a P1–P5 újrafuttatását jelentené.
"""
from __future__ import annotations

import json
import re
import shlex
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

DEFAULT_PSQL = "docker exec -i gh_jobs_pg psql -U postgres -d gaphopper_jobs"
STEP = "C0"

# A fordító-/kivonómodell hibaüzenetei és a definíció jellegű „értékek”.
JUNK = re.compile(r"kérlek|sajnálom|nem tudom|fordít|álláshirdetés|please|sorry|"
                  r"\bjelenti\b|kifejezés|képessége arra|\bdefiníció", re.I)

SYSTEM = """Munkakör-leírást állítasz össze álláshirdetésekből kivont, gyakoriság szerinti listákból.
Csak a kapott adatokra támaszkodj; ne egészítsd ki saját tudásból olyan feladattal, amely nincs a listában.

Szabályok:
- A feladatlista (`tasks`) 8–20 tétel, magyarul, igei szerkezettel ("Árut mozgat targoncával a raktárban."),
  egy tétel egy feladat. A szinonim és átfedő tételeket vond össze (pl. anyagmozgatás, árumozgatás,
  áruk mozgatása -> egy tétel). Gyakoriság szerint rendezz, a leggyakoribb elöl.
- Hagyd ki a zajt: modell-hibaüzenetet, definíciót, juttatást, céginformációt, puszta személyes
  tulajdonságot (pl. "csapatmunka", "precizitás") a feladatok közül; ezek legfeljebb az elvárásokba kerülnek.
- Ha a hirdetések egy része láthatóan nem ehhez a foglalkozáshoz tartozik (téves besorolás), azok
  tételeit hagyd ki, és írd le röviden az `excluded_note` mezőben.
- `description`: 2–3 mondat, a munkakör célja és jellemző környezete, általános (cég- és helyszínsemleges).
- `requirements`: a leggyakoribb elvárások pontosvesszővel elválasztva (végzettség, tapasztalat,
  nyelv, jogosítvány, eszközismeret, készség), legfeljebb 12 tétel.
- `conditions`: munkakörülmények és munkarend (műszak, fizikai terhelés, utazás, home office),
  1–2 mondat; juttatásokat ne sorolj fel.
- `entry_route`: a jellemző belépési út röviden (pl. "Középfokú végzettség + targoncavezetői jogosítvány").
- `field`: szakterület röviden (pl. "Logisztika / raktározás").
- `alt_titles`: legfeljebb 8 gyakori magyar vagy angol megnevezés a hirdetéscímekből.

Csak a JSON-objektumot add vissza ezekkel a kulcsokkal:
title_hu, title_en, alt_titles, field, description, tasks, requirements, conditions, entry_route, excluded_note"""


class CrawlJob(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title_hu: str
    title_en: str = ""
    alt_titles: list[str] = Field(default_factory=list)
    field: str = ""
    description: str
    tasks: list[str] = Field(min_length=5, max_length=25)
    requirements: str = ""
    conditions: str = ""
    entry_route: str = ""
    excluded_note: str = ""


# ------------------------------------------------------------------ adatlekérés
def query_json(sql: str, psql: str = DEFAULT_PSQL):
    out = subprocess.run(shlex.split(psql) + ["-At", "-v", "ON_ERROR_STOP=1"], input=sql,
                         capture_output=True, text=True, check=False)
    if out.returncode != 0:
        raise RuntimeError(f"psql hiba: {out.stderr.strip()[:500]}")
    return json.loads(out.stdout.strip() or "null")


def _lit(s: str) -> str:
    return "'" + s.replace("'", "''") + "'"


def occupations(psql: str = DEFAULT_PSQL) -> list[dict]:
    occs = query_json("""
select coalesce(json_agg(x order by x.postings desc), '[]') from (
  select o.id, o.gaphopper_id, o.name_hu, o.name_en,
    (select count(distinct m.job_id) from job_occupation_matches m
      where m.occupation_id = o.id and m.status <> 'REJECTED') postings,
    (select coalesce(json_agg(raw_expression), '[]') from occupation_synonyms s
      where s.occupation_id = o.id and s.approval_status = 'APPROVED') synonyms
  from occupation_catalog o where o.status = 'ACTIVE') x;""", psql)
    for o in occs:  # hibás kódolású szinonimák (pl. "Targonc??s") kimaradnak
        o["synonyms"] = [s for s in o["synonyms"] if "?" not in s and "�" not in s]
    return occs


def occupation_data(occ_id: int, psql: str = DEFAULT_PSQL, samples: int = 3) -> dict:
    def elems(etype: str, limit: int) -> str:
        return f"""(select coalesce(json_agg(json_build_object('n', element_name, 'c', posting_count,
            'r', requirement_type) order by posting_count desc), '[]') from (
            select element_name, posting_count, requirement_type from occupation_evidence_stats
            where occupation_id = {occ_id} and element_type = '{etype}'
            order by posting_count desc, element_name limit {limit}) e)"""
    return query_json(f"""
select json_build_object(
  'tasks', {elems('TASK', 300)},
  'requirements', {elems('REQUIREMENT', 150)},
  'benefits', {elems('BENEFIT', 40)},
  'titles', (select coalesce(json_agg(json_build_object('t', t, 'c', c) order by c desc), '[]') from (
      select regexp_replace(j.title, '\\s*állás ✅.*$', '') t, count(*) c
      from job_occupation_matches m join jobs j on j.id = m.job_id
      where m.occupation_id = {occ_id} and m.status <> 'REJECTED' group by 1 order by 2 desc limit 40) t),
  'portals', (select coalesce(json_object_agg(p, c), '{{}}') from (
      select s.source_portal p, count(*) c from job_occupation_matches m
      join job_sources s on s.job_id = m.job_id
      where m.occupation_id = {occ_id} and m.status <> 'REJECTED' group by 1 order by 2 desc limit 8) p),
  'samples', (select coalesce(json_agg(d), '[]') from (
      select left(coalesce(nullif(j.description_text_hu, ''), j.description_text), 2000) d
      from job_occupation_matches m join jobs j on j.id = m.job_id
      where m.occupation_id = {occ_id} and m.status <> 'REJECTED'
        and length(coalesce(nullif(j.description_text_hu, ''), j.description_text)) > 400
        and coalesce(j.description_text_hu, '') !~* '(kérlek|sajnálom|nem tudom)'
      order by m.score desc, md5(j.id::text) limit {samples}) s)
);""", psql)


def clean(items: list[dict], min_count: int = 2, min_items: int = 20, fallback_cap: int = 60) -> list[dict]:
    """Zajszűrés; alapból legalább `min_count` hirdetésben szereplő tételek, kevés adatnál lazábban."""
    ok = [i for i in items if i["n"] and len(i["n"]) <= 120 and not JUNK.search(i["n"])]
    strong = [i for i in ok if i["c"] >= min_count]
    return strong if len(strong) >= min_items else ok[:max(fallback_cap, len(strong))]


def build_user(occ: dict, data: dict) -> tuple[str, dict]:
    tasks, reqs = clean(data["tasks"]), clean(data["requirements"])
    bens = clean(data["benefits"], min_items=10)
    fmt = lambda xs: "\n".join(f"- {x['n']} ({x['c']})" for x in xs) or "(nincs)"
    fmt_r = lambda xs: "\n".join(f"- {x['n']} [{x.get('r') or '-'}] ({x['c']})" for x in xs) or "(nincs)"
    user = f"""Foglalkozás: {occ['name_hu']} ({occ.get('name_en') or '-'})
Katalógus-szinonimák: {', '.join(occ['synonyms']) or '-'}
Besorolt hirdetések száma: {occ['postings']}

Gyakori hirdetéscímek (darab):
{chr(10).join(f"- {t['t']} ({t['c']})" for t in data['titles'][:25])}

Feladatok (tétel, hány hirdetésben):
{fmt(tasks)}

Elvárások (tétel [típus], hány hirdetésben):
{fmt_r(reqs)}

Juttatások és körülmények (csak a `conditions` mezőhöz, ha munkarendre utal):
{fmt(bens)}

Hirdetésszöveg-minták:
""" + "\n\n---\n".join(data["samples"])
    stat = {"task_items": len(tasks), "requirement_items": len(reqs),
            "task_raw": len(data["tasks"]), "samples": len(data["samples"])}
    return user, stat


# ------------------------------------------------------------------ fő lépés
def record(occ: dict, data: dict, res: CrawlJob, stat: dict, dump_name: str, fetched_at: str) -> dict:
    alt = []
    for t in [res.title_en, *occ["synonyms"], *res.alt_titles]:
        if t and t.casefold() != res.title_hu.casefold() and t.casefold() not in {a.casefold() for a in alt}:
            alt.append(t)
    return {
        "job_id": f"crawl:{occ['gaphopper_id']}",
        "title": res.title_hu or occ["name_hu"],
        "alt_titles": alt[:10],
        "field": res.field,
        "description": res.description,
        "tasks": res.tasks,
        "requirements": res.requirements,
        "conditions": res.conditions,
        "entry_route": res.entry_route,
        "source": f"crawler-adatbázis ({dump_name}), {occ['postings']} álláshirdetés összevonása",
        "source_url": "",
        "fetched_at": fetched_at,
        "locale": "hu-HU",
        "source_lang": "hu",
        "tasks_source": "Álláshirdetésekből kivont feladatok, LLM-mel összevonva (C0)",
        "crawl": {"occupation_id": occ["id"], "gaphopper_id": occ["gaphopper_id"], "postings": occ["postings"],
                  "portals": data["portals"], **stat, "excluded_note": res.excluded_note},
    }


def run_crawl_export(ctx, out: Path, only: list[str] | None = None, workers: int = 6, min_postings: int = 30,
                     psql: str = DEFAULT_PSQL, dump_name: str = "", fetched_at: str = "",
                     dry_run: bool = False, log=print) -> dict:
    from .llm import call_json

    occs = occupations(psql)
    if only:
        want = {o.casefold() for o in only}
        occs = [o for o in occs if {o["gaphopper_id"].casefold(), o["name_hu"].casefold()} & want]
    skipped = [o["name_hu"] for o in occs if o["postings"] < min_postings]
    occs = [o for o in occs if o["postings"] >= min_postings]
    if skipped:
        log(f"Kevés hirdetés (< {min_postings}), kimarad: {', '.join(skipped)}")
    prepared = []
    for o in occs:
        data = occupation_data(o["id"], psql)
        user, stat = build_user(o, data)
        prepared.append((o, data, user, stat))
        log(f"{o['name_hu']}: {o['postings']} hirdetés, {stat['task_items']}/{stat['task_raw']} feladattétel, "
            f"~{(len(SYSTEM) + len(user)) // 3500}k token bemenet")
    if dry_run:
        pdir = out.parent / (out.stem + "_prompts")
        pdir.mkdir(parents=True, exist_ok=True)
        for o, _, user, _ in prepared:
            (pdir / f"{o['gaphopper_id']}.md").write_text(user, encoding="utf-8")
        log(f"Próbafutás: {len(prepared)} prompt -> {pdir} (LLM-hívás nem volt)")
        return {"prompts": len(prepared), "skipped": skipped}

    cfg = ctx.settings.p1
    logs: list[tuple] = []

    def one(item):
        o, data, user, stat = item
        res, _ = call_json(ctx.llm("P1"), SYSTEM, user, CrawlJob, retries=1,
                           log=lambda raw, ok, usage, j=o: logs.append((f"crawl:{j['gaphopper_id']}", raw, ok, usage)))
        return record(o, data, res, stat, dump_name, fetched_at or date.today().isoformat())

    recs, errors = [], []
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futs = {ex.submit(one, it): it[0] for it in prepared}
        for f, o in futs.items():
            try:
                recs.append(f.result())
                log(f"kész: {o['name_hu']} ({len(recs[-1]['tasks'])} feladat)")
            except Exception as exc:  # noqa: BLE001 – a többi munkakör menjen tovább
                errors.append(o["name_hu"])
                log(f"HIBA {o['name_hu']}: {str(exc)[:300]}")
    for jid, raw, ok, usage in logs:  # a naplóírás a fő szálon (SQLite)
        ctx.store.log(jid, STEP, cfg.provider, cfg.model, ctx.pv, "", raw, ok, usage)
    order = {o["gaphopper_id"]: i for i, o in enumerate(occs)}
    recs.sort(key=lambda r: order[r["crawl"]["gaphopper_id"]])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8") as fh:
        for r in recs:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    tin = sum(u.get("input_tokens", 0) for *_, u in logs)
    tout = sum(u.get("output_tokens", 0) for *_, u in logs)
    log(f"{len(recs)} rekord -> {out}; hiba: {len(errors)}; tokenek: {tin} be / {tout} ki; "
        f"{len(logs)} hívás (C0 lépésként naplózva)")
    return {"records": len(recs), "errors": errors, "skipped": skipped, "input_tokens": tin, "output_tokens": tout}
