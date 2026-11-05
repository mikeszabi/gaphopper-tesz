"""Parancssor: python -m gaphopper <parancs> ...

  init                      SQLite + neo4j séma, zárt szótárak csomópontként
  extract FÁJL|MAPPA.. [--out data/jobs.jsonl] [--import] [--workers N]
                            0. lépés: dokumentumokból jobs.jsonl (E0 prompt)
  import FÁJL               munkakörök beolvasása (.xlsx / .jsonl / .csv / .json)
  run [--job ID..] [--limit N] [--until p1|p2|graph] [--force] [--workers N]
  run --dry-run --job ID.. [--input jobs.jsonl] [--workers N | --chain] [--no-graph]
                            próbafutás: P1–P3 lefut, a gráf és a state.sqlite nem változik;
                            eredmény: runs/dryrun/<job>.json
  db-reset --yes [--graph-only]   neo4j (és state.sqlite) ürítése, előtte mentés: runs/backup-<dátum>/
  batch submit --step P1|P2 [--limit N]   kötegelt beküldés (Anthropic)
  batch status | batch collect [--wait]
  manual-submit --job ID --step P1|P2 --file VÁLASZ.json
  review [--list | --approve QID | --reject QID]
  stats                     eloszlások a gráfból
  anchors [--min-tesz 5 --min-jobs 3]
  p4a --type HELYZET --value HELY_RUTIN [--runs 3]
  show --job ID             egy munkakör köztes eredményei
  split [--review QID]      jóváhagyott bontási javaslat végrehajtása (új TESZ-nivaló, a munkakörök átkötése)
  generalize [--min-jobs 2] [--dry-run]   P6: túl szűk TESZ-nivaló-nevek általánosítása bizonyíték alapján
  certify [--limit N] [--chunk 10]   P5: igazolási mondat és rubrika a rubrika nélküli TESZ-ekhez
  report [--job ID..] [--by prompt_version,step]   tokenek, idő és költség a futásnaplóból
  models [--check-local]    a lépésenként érvényes modellbeállítás (és a helyi szerver próbája)
  ui [--port 8765]          a webes felület indítása (http://localhost:8765)
  onet-tasks <Task Statements.txt|.xlsx> [--ratings "Task Ratings.txt"]   O*NET-feladatlisták a rekordokba
  embed check | embed backfill   beágyazási modell (pl. helyi Ollama) próbája, hiányzó vektorok pótlása
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .config import settings


def _ctx(with_graph: bool = True, **kw):
    from .pipeline import Context

    return Context(settings(), with_graph=with_graph, **kw)


def cmd_init(a):
    ctx = _ctx()
    dim = ctx.embedder.dim if ctx.embedder.enabled else None
    ctx.graph.setup(dim)
    print(f"Kész: SQLite {ctx.settings.state_db}, neo4j séma"
          + (f", vektorindexek ({dim} dim)" if dim else ", vektorindex nélkül (GH_EMBED_MODEL üres)"))
    ctx.close()


def cmd_import(a):
    from .importers import load

    ctx = _ctx(with_graph=False)
    recs = load(a.file)
    new = sum(ctx.store.upsert_job(r) for r in recs)
    print(f"{len(recs)} rekord beolvasva, ebből {new} új vagy változott.")


def cmd_extract(a):
    from .extract import ExtractError, extract_file, iter_files, write_jsonl

    ctx = _ctx(with_graph=False)
    files = iter_files(a.paths)
    if not files:
        print("Nincs feldolgozható fájl (pdf, docx, txt, md, html, odt, rtf).")
        return
    out = Path(a.out)
    total, all_recs = 0, []
    for f in files:
        try:
            recs, warns = extract_file(ctx, f, workers=a.workers)
        except ExtractError as exc:
            print(f"HIBA {f.name}: {exc}")
            continue
        all_recs += recs
        print(f"{f.name}: {len(recs)} munkakör" + (f"  ({len(warns)} figyelmeztetés)" if warns else ""))
        for w in warns:
            print(f"   ! {w}")
        total += len(recs)
    n = write_jsonl(all_recs, out, append=not a.overwrite)
    print(f"{total} munkakör kinyerve; {out}: {n} sor.")
    if a.do_import:
        new = sum(ctx.store.upsert_job(r) for r in all_recs)
        print(f"Importálva: {new} új vagy változott rekord.")


def _run_parallel(ctx, a, ids):
    from .batch import run_parallel
    from .pipeline import process_job

    for step in ("P1", "P2"):
        if a.until == "p1" and step == "P2":
            break
        st = run_parallel(ctx, step, ids, a.limit, workers=a.workers)
        print(f"{step}: {st['ok']} kész, {st['hiba']} hiba")
    if a.until == "graph":
        ready = [r["job_id"] for r in ctx.store.jobs("P2_ELFOGADVA") if not ids or r["job_id"] in ids]
        for jid in ready:
            try:
                result = process_job(ctx, jid)
            except Exception as exc:
                # A P2 eredménye kész: a következő indítás csak a P3-at próbálja újra.
                message = str(exc)[:3000]
                ctx.store.save_artifact(jid, "P3_ERR", {"error": message}, ctx.pv)
                ctx.store.set_status(jid, "P2_ELFOGADVA", f"P3 hiba: {message[:500]}")
                result = f"P3 HIBA: {message}"
            print(f"{jid}: {result}", flush=True)


def _dry_run(a):
    from concurrent.futures import ThreadPoolExecutor

    from .pipeline import dry_run, format_dry_run

    s = settings()
    recs: dict[str, dict] = {}
    if a.input:
        from .importers import load

        recs = {r["job_id"]: r for r in load(a.input)}
    else:
        import sqlite3

        if not Path(s.state_db).exists():
            raise SystemExit(f"Nincs állapotfájl ({s.state_db}); add meg a rekordokat: --input jobs.jsonl")
        con = sqlite3.connect(f"file:{s.state_db}?mode=ro", uri=True)  # csak olvas
        for jid, rec in con.execute("SELECT job_id, record_json FROM jobs"):
            recs[jid] = json.loads(rec)
        con.close()
    ids = a.job or list(recs)[: a.limit or 1]
    missing = [j for j in ids if j not in recs]
    if missing:
        raise SystemExit(f"Ismeretlen munkakör: {', '.join(missing)}")
    graph = None
    if not a.no_graph:
        try:
            from .graph import Graph

            graph = Graph(s)
            graph.driver.verify_connectivity()
        except Exception as exc:  # gráf nélkül az egyesítés kimarad
            print(f"A neo4j nem érhető el ({str(exc).splitlines()[0][:200]}); az egyesítés kimarad.", flush=True)
            graph = None
    try:
        if a.chain:
            # lánc: egymás után; a korábbi munkakörök új TESZ-nivalói a következő TESZ-törzsébe kerülnek
            extra: list[dict] = []
            for j in ids:
                out = dry_run(recs[j], s, graph, extra_torzs=extra)
                extra += out.get("new_torzs", [])
                print(format_dry_run(out) + "\n", flush=True)
            return
        with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
            for out in ex.map(lambda j: dry_run(recs[j], s, graph), ids):
                print(format_dry_run(out) + "\n", flush=True)
    finally:
        if graph:
            graph.close()


def cmd_db_reset(a):
    if not a.yes:
        raise SystemExit("Ez minden neo4j-csomópontot, indexet és a state.sqlite-ot törli. Futtasd --yes kapcsolóval.")
    import shutil
    import sqlite3
    from datetime import date

    s = settings()
    from datetime import datetime

    d = Path(s.state_db).parent / f"backup-{date.today().isoformat()}"
    if d.exists():  # ugyanaznapi korábbi mentést nem írunk felül
        d = d.with_name(d.name + datetime.now().strftime("-%H%M%S"))
    d.mkdir(parents=True)
    if Path(s.state_db).exists():
        src = sqlite3.connect(str(s.state_db))
        dst = sqlite3.connect(str(d / "state.sqlite"))
        src.backup(dst)
        dst.close()
        src.close()
    from .graph import Graph

    g = Graph(s)
    nodes = g.run("MATCH (n) RETURN elementId(n) AS eid, labels(n) AS labels, properties(n) AS props")
    rels = g.run("MATCH (a)-[r]->(b) RETURN type(r) AS type, elementId(a) AS src, elementId(b) AS dst, "
                 "properties(r) AS props")
    (d / "neo4j-export.json").write_text(json.dumps({"nodes": nodes, "relationships": rels}, ensure_ascii=False,
                                                    default=str), encoding="utf-8")
    print(f"Mentés: {d}  ({len(nodes)} csomópont, {len(rels)} él)")
    print("neo4j törölve:", g.reset())
    g.close()
    if a.graph_only:
        # Csak a gráf épül újra: a P1/P2-eredmények maradnak, a gráfban lévő munkakörök a P3-ra várnak.
        from .store import Store

        S = Store(s.state_db)
        S.db.execute("DELETE FROM alias_index")
        n = S.db.execute("UPDATE jobs SET status='P2_ELFOGADVA', note='gráf-újraépítés: P3-ra vár' "
                         "WHERE status='GRAFBAN'").rowcount
        S.db.commit()
        print(f"state.sqlite megmaradt; {n} munkakör P2_ELFOGADVA állapotba került. Újraépítés: "
              "python -m gaphopper init, majd python -m gaphopper run")
        return
    for suffix in ("", "-wal", "-shm"):
        Path(str(s.state_db) + suffix).unlink(missing_ok=True)
    print(f"state.sqlite törölve: {s.state_db}. Újraépítés: python -m gaphopper init")
    shutil.rmtree(Path(s.state_db).parent / "manual", ignore_errors=True)


def cmd_run(a):
    from .pipeline import process_job

    if a.dry_run:
        return _dry_run(a)
    ctx = _ctx(with_graph=a.until == "graph")
    manual = "manual" in (ctx.settings.p1.provider, ctx.settings.p2.provider)
    if a.workers and a.workers > 1 and not manual:  # kézi módban nincs értelme a párhuzamosságnak
        try:
            _run_parallel(ctx, a, a.job)
        finally:
            ctx.close()
        return
    ids = a.job or [r["job_id"] for r in ctx.store.jobs() if r["status"] not in ("GRAFBAN", "ELVETVE")]
    if a.limit:
        ids = ids[: a.limit]
    for jid in ids:
        try:
            st = process_job(ctx, jid, until=a.until, force=a.force)
        except Exception as exc:  # egy hibás rekord ne állítsa meg a sort
            if ctx.store.status(jid) != "P2_ELFOGADVA":  # P3-hibánál a P2 eredménye megmarad
                ctx.store.set_status(jid, "HIBA", str(exc)[:500])
            st = f"HIBA: {exc}"
        print(f"{jid}: {st}", flush=True)
        if st.startswith("MANUAL:"):
            d = st.split(":", 1)[1]
            print(f"  Kézi mód: add a(z) {d} mappa _system.md és _user.md fájlját a modellnek,\n"
                  f"  a JSON-választ mentsd fájlba, majd: python -m gaphopper manual-submit "
                  f"--job {jid} --step <P1|P2> --file <válasz.json>")
            break
    ctx.close()


def cmd_batch(a):
    from . import batch

    ctx = _ctx(with_graph=False)
    if a.action == "submit":
        ids = batch.submit(ctx, a.step, a.job, a.limit, max_per_batch=a.max_per_batch)
        print(f"Beküldött kötegek: {ids or 'nincs beküldhető rekord'}")
    elif a.action == "status":
        for b in batch.status(ctx):
            print(b)
        if not ctx.store.batches():
            print("Nincs nyitott köteg.")
    elif a.action == "collect":
        st = batch.wait_and_collect(ctx, a.poll) if a.wait else batch.collect(ctx)
        print(st)


def cmd_embed(a):
    from .embed import EmbedError

    ctx = _ctx(with_graph=a.action == "backfill")
    emb = ctx.embedder
    if not emb.enabled:
        print("A beágyazás ki van kapcsolva: állítsd be a GH_EMBED_MODEL-t a .env-ben.")
        return
    try:
        if a.action == "check":
            pairs = [("szoftverfejlesztő", "software developer"), ("ápoló", "registered nurse"),
                     ("ápoló", "software developer")]
            print(f"{emb.provider} / {emb.model_name}: {emb.dim} dimenzió")
            for x, y in pairs:
                vx, vy = emb.encode(x), emb.encode(y)
                print(f"  {x!r} ~ {y!r}: {sum(p * q for p, q in zip(vx, vy)):.3f}")
            print("Többnyelvű modellnél az első két pár jóval hasonlóbb, mint a harmadik.")
        else:
            total = 0
            for et in ("MUNKA_KOR", "TESZ_NIVALO", "SZER_ELEM", "KESZSEG"):
                rows = ctx.graph.missing_embeddings(et, a.limit)
                for r in rows:
                    text = " | ".join(x for x in (r["label_hu"], r["label_en"], r["extra"]) if x)
                    ctx.graph.set_embedding(et, r["id"], emb.encode(text))
                total += len(rows)
                print(f"{et}: {len(rows)} vektor")
            print(f"Összesen {total}. (Ha a vektorindex még nincs meg: python -m gaphopper init)")
    except EmbedError as exc:
        print(f"HIBA: {exc}")
    ctx.close()


def cmd_onet_tasks(a):
    from pathlib import Path

    from .onet import enrich

    res = enrich(Path(a.jobs), Path(a.statements), Path(a.ratings) if a.ratings else None,
                 Path(a.out) if a.out else None, a.supplemental, a.release)
    print(f"{res['with_tasks']}/{res['records']} rekord kapott feladatlistát -> {res['out']}")
    if res["missing"]:
        print("Nincs feladat ezekhez a kódokhoz:", ", ".join(res["missing"]))
    print("Ha a rekordok már a sorban vannak: python -m gaphopper import", res["out"])


def cmd_manual_submit(a):
    from .pipeline import process_job

    ctx = _ctx(with_graph=a.until == "graph", answer_files={a.step: Path(a.file)})
    print(process_job(ctx, a.job, until=a.until))
    ctx.close()


def cmd_review(a):
    from .pipeline import approve_job

    ctx = _ctx(with_graph=bool(a.approve or a.reject))
    try:
        if a.approve:
            print(json.dumps(approve_job(ctx, a.approve), ensure_ascii=False, indent=1))
        elif a.reject:
            row = next(r for r in ctx.store.queue() if r["id"] == a.reject)
            ctx.store.decide(a.reject, "ELUTASITVA")
            if row["kind"] == "kesz_review":
                ctx.graph.kesz_hiany_status(json.loads(row["payload_json"])["ids"], "ELVETVE", "szakértő elutasította")
            elif row["kind"] == "kesz_irany_review":
                from .config import kesz_hierarchia, save_kesz_torzs

                h = kesz_hierarchia()
                next(x for x in h["items"] if x["id"] == json.loads(row["payload_json"])["id"])["irany_forras"] = "szakerto"
                save_kesz_torzs(h)
            elif row["job_id"]:
                ctx.store.set_status(row["job_id"], "ELVETVE")
            print("Elutasítva.")
        else:
            for r in ctx.store.queue():
                print(f"[{r['id']}] {r['job_id']} {r['kind']}: {r['payload_json'][:300]}")
    finally:
        ctx.close()


def cmd_stats(a):
    ctx = _ctx()
    print(json.dumps(ctx.graph.stats(), ensure_ascii=False, indent=1))
    rows = ctx.store.db.execute("SELECT status, count(*) AS n FROM jobs GROUP BY status").fetchall()
    print("Munkakörök állapota:", {r["status"]: r["n"] for r in rows})
    ctx.close()


def cmd_anchors(a):
    ctx = _ctx()
    for r in ctx.graph.anchors(a.min_tesz, a.min_jobs):
        print(r)
    ctx.close()


def cmd_p4a(a):
    from .pipeline import run_p4a

    ctx = _ctx()
    print(json.dumps(run_p4a(ctx, a.type, a.value, runs=a.runs), ensure_ascii=False, indent=1))
    ctx.close()


def cmd_show(a):
    ctx = _ctx(with_graph=False)
    print("Állapot:", ctx.store.status(a.job))
    for step in ("P1", "P2", "P3"):
        art = ctx.store.artifact(a.job, step)
        if art:
            print(f"\n== {step} ==")
            print(json.dumps(art, ensure_ascii=False, indent=1)[: a.max_chars])


def cmd_certify(a):
    from .pipeline import run_p5

    ctx = _ctx()
    print(run_p5(ctx, limit=a.limit, chunk=a.chunk))
    ctx.close()


def cmd_split(a):
    from .pipeline import run_split

    ctx = _ctx()
    try:
        ids = a.review or [r["id"] for r in ctx.store.queue() if r["kind"] == "split_review"]
        for q in ids:
            run_split(ctx, q)
    finally:
        ctx.close()


def cmd_kesz_irany(a):
    from .kesz import run_kesz_irany

    ctx = _ctx()
    try:
        out = run_kesz_irany(ctx, chunk=a.chunk, apply=not a.dry_run, only_default=not a.all)
    finally:
        ctx.close()
    print(f"{out['vizsgalt']} elem vizsgálva; irány módosítva: {len(out['valtozott'])}, szakértőhöz: "
          f"{len(out['szakertohoz'])}, hiba: {out['hiba']}" + ("  [PRÓBA: semmi nem íródott]" if a.dry_run else ""))
    for r in out["valtozott"] + out["szakertohoz"]:
        print(f"  {r['id']} {r['label_hu']}: {','.join(r['jelenlegi'])} -> {','.join(r['javaslat'])}  ({r['indok']})")


def cmd_kesz_bovit(a):
    from .kesz import candidates_from_dryrun, run_kesz_bovit

    ctx = _ctx(with_graph=not a.from_dryrun)
    try:
        rows = candidates_from_dryrun(a.from_dryrun) if a.from_dryrun else None
        out = run_kesz_bovit(ctx, rows, chunk=a.chunk, apply=not (a.dry_run or a.from_dryrun), queue=a.queue)
    finally:
        ctx.close()
    print(f"{out['jeloltek']} hiányjelölt; ALIAS: {len(out['alias'])}, UJ (szakértőhöz): {len(out['uj'])}, "
          f"vár (kevés munkakör): {len(out['var'])}, ELVET: {len(out['elvet'])}, hiba: {out['hiba']}"
          + ("  [PRÓBA: a gráf és a yaml nem változott" + ("; UJ a szakértői sorban]" if a.queue else "]")
             if a.dry_run or a.from_dryrun else ""))
    for c in out["alias"]:
        print(f"  ALIAS {', '.join(c['nevek'])} -> {c['kesz'][5:]} {c['kesz_nev']}")
    for c in out["uj"] + out["var"]:
        print(f"  UJ {c['label_hu']} / {c['label_en']} ({c['csoport']}, {','.join(c['irany'])}; "
              f"{len(c['munkakorok'])} munkakör) <- {', '.join(c['nevek'])}")
    for c in out["elvet"]:
        print(f"  ELVET {', '.join(c['nevek'])}: {c['indok']}")


def cmd_generalize(a):
    from .pipeline import run_generalize

    ctx = _ctx()
    try:
        out = run_generalize(ctx, min_jobs=a.min_jobs, chunk=a.chunk, apply=not a.dry_run)
    finally:
        ctx.close()
    print(f"{out['jeloltek']} jelölt; átnevezve: {len(out['valtozasok'])}, marad: {out['marad']}, "
          f"bontási javaslat: {len(out['bontas'])}, hiba: {out['hiba']} ({out['ido_mp']} mp)"
          + ("  [PRÓBA: semmi nem íródott]" if a.dry_run else ""))
    for c in out["valtozasok"]:
        print(f"  ÁTNEVEZ ({c['munkakor_db']} mk): {c['regi']}  ->  {c['uj']}"
              + (f"   [SZER-ELEM: {c['szer_regi']} -> {c['szer_uj']}]" if c.get("szer_uj") else "")
              + (f"   – {c['indok']}" if c.get("indok") else ""))
    for b in out["bontas"]:
        print(f"  BONTÁS (szakértőhöz): {b['nev']} -> " + " | ".join(x["label_hu"] for x in b["javaslat"]))
    print("Lista:", out["fajl"])


def cmd_report(a):
    from .report import summary
    from .store import Store

    s = settings()
    rows = summary(Store(s.state_db), a.job, tuple(a.by.split(",")))
    if not rows:
        print("A futásnapló üres.")
        return
    cols = list(rows[0].keys())
    print("\t".join(cols))
    for r in sorted(rows, key=lambda r: tuple(str(r[c]) for c in a.by.split(","))):
        print("\t".join(str(r.get(c, "")) for c in cols))


def cmd_models(a):
    from .config import STEP_NAMES, STEPS

    s = settings()
    for st in STEPS:
        m = s.step(st)
        print(f"{st:3} {STEP_NAMES[st]:28} {m.provider:9} {m.model:28} effort={m.effort or '-'} "
              f"thinking={m.thinking or '-'} max_tokens={m.max_tokens}")
    print(f"P2 mód: {s.p2_mode}, minta: {s.p2_sample:.0%}")
    print(f"Helyi szerver: {s.local_base_url} (json: {s.local_json_mode})")
    if a.check_local:
        from .llm import LocalLLM

        try:
            ids = LocalLLM(s.p3, s.local_base_url, s.local_api_key).models()
            print("Elérhető helyi modellek:", ", ".join(ids) or "-")
        except Exception as exc:  # hálózati vagy formátumhiba
            print(f"HIBA: {exc}")


def cmd_ui(a):
    from .tasks import env_problem
    from .web import serve

    if env_problem():
        print("Figyelem: " + env_problem(), file=sys.stderr, flush=True)
    serve(a.host, a.port, open_browser=not a.no_browser)


def main(argv=None):
    p = argparse.ArgumentParser(prog="gaphopper", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init").set_defaults(fn=cmd_init)
    s = sub.add_parser("extract"); s.add_argument("paths", nargs="+")
    s.add_argument("--out", default="data/jobs.jsonl"); s.add_argument("--workers", type=int, default=4)
    s.add_argument("--import", dest="do_import", action="store_true")
    s.add_argument("--overwrite", action="store_true", help="a kimeneti fájl felülírása hozzáfűzés helyett")
    s.set_defaults(fn=cmd_extract)
    s = sub.add_parser("batch"); s.add_argument("action", choices=["submit", "status", "collect"])
    s.add_argument("--step", choices=["P1", "P2"], default="P1"); s.add_argument("--job", action="append")
    s.add_argument("--limit", type=int); s.add_argument("--max-per-batch", type=int, default=2000)
    s.add_argument("--wait", action="store_true"); s.add_argument("--poll", type=int, default=60)
    s.set_defaults(fn=cmd_batch)
    s = sub.add_parser("import"); s.add_argument("file"); s.set_defaults(fn=cmd_import)
    s = sub.add_parser("run")
    s.add_argument("--job", action="append"); s.add_argument("--limit", type=int)
    s.add_argument("--until", choices=["p1", "p2", "graph"], default="graph")
    s.add_argument("--force", action="store_true")
    s.add_argument("--workers", type=int, default=1, help="párhuzamos API-hívások száma (P1, P2)")
    s.add_argument("--dry-run", action="store_true",
                   help="próbafutás: LLM-lépések lefutnak, a gráf és a state.sqlite nem változik")
    s.add_argument("--input", help="próbafutásnál: a rekordok fájlból (.jsonl/.json/.csv/.xlsx)")
    s.add_argument("--chain", action="store_true",
                   help="próbafutás-lánc: a munkakörök egymás után, a korábbiak új TESZ-nivalói a törzsbe kerülnek")
    s.add_argument("--no-graph", action="store_true", help="próbafutás a neo4j olvasása nélkül (egyesítés nélkül)")
    s.set_defaults(fn=cmd_run)
    s = sub.add_parser("db-reset", help="neo4j és state.sqlite ürítése mentéssel")
    s.add_argument("--yes", action="store_true")
    s.add_argument("--graph-only", action="store_true",
                   help="csak a neo4j ürül; a state.sqlite (P1/P2-eredmények) marad, a gráf a P3-mal újraépíthető")
    s.set_defaults(fn=cmd_db_reset)
    s = sub.add_parser("embed"); s.add_argument("action", choices=["check", "backfill"])
    s.add_argument("--limit", type=int, default=100000); s.set_defaults(fn=cmd_embed)
    s = sub.add_parser("onet-tasks", help="O*NET Task Statements betöltése a tasks mezőbe")
    s.add_argument("statements"); s.add_argument("--jobs", default="data/job_100.jsonl")
    s.add_argument("--ratings"); s.add_argument("--out"); s.add_argument("--release", default="")
    s.add_argument("--supplemental", action="store_true", help="a kiegészítő feladatok is a tasks-ba kerülnek")
    s.set_defaults(fn=cmd_onet_tasks)
    s = sub.add_parser("manual-submit")
    s.add_argument("--job", required=True); s.add_argument("--step", choices=["P1", "P2"], required=True)
    s.add_argument("--file", required=True)
    s.add_argument("--until", choices=["p1", "p2", "graph"], default="graph")
    s.set_defaults(fn=cmd_manual_submit)
    s = sub.add_parser("review")
    s.add_argument("--list", action="store_true"); s.add_argument("--approve", type=int)
    s.add_argument("--reject", type=int); s.set_defaults(fn=cmd_review)
    sub.add_parser("stats").set_defaults(fn=cmd_stats)
    s = sub.add_parser("anchors"); s.add_argument("--min-tesz", type=int, default=5)
    s.add_argument("--min-jobs", type=int, default=3); s.set_defaults(fn=cmd_anchors)
    s = sub.add_parser("p4a"); s.add_argument("--type", required=True,
                                              choices=["KOZEG", "HELYZET", "SZIN", "TESZ_TIPUS", "SZER_ELEM"])
    s.add_argument("--value", required=True); s.add_argument("--runs", type=int, default=3)
    s.set_defaults(fn=cmd_p4a)
    s = sub.add_parser("show"); s.add_argument("--job", required=True)
    s.add_argument("--max-chars", type=int, default=20000); s.set_defaults(fn=cmd_show)
    s = sub.add_parser("certify"); s.add_argument("--limit", type=int, default=1000)
    s.add_argument("--chunk", type=int, default=6, help="TESZ-nivalók hívásonként"); s.set_defaults(fn=cmd_certify)
    s = sub.add_parser("generalize", help="P6 törzskarbantartás: túl szűk TESZ-nivaló-nevek általánosítása")
    s.add_argument("--min-jobs", type=int, default=2); s.add_argument("--chunk", type=int, default=20)
    s.add_argument("--dry-run", action="store_true", help="csak a javaslatok listája, a gráf nem változik")
    s.set_defaults(fn=cmd_generalize)
    s = sub.add_parser("kesz-irany", help="a KÉSZ-ségek irányultsága elemenként (csoport-alapértékből, P3-modell)")
    s.add_argument("--chunk", type=int, default=60); s.add_argument("--all", action="store_true",
                                                                   help="a már vizsgált elemeket is")
    s.add_argument("--dry-run", action="store_true"); s.set_defaults(fn=cmd_kesz_irany)
    s = sub.add_parser("kesz-bovit", help="KÉSZ-ség-törzs bővítése a hiányjelöltekből (ALIAS / UJ / ELVET)")
    s.add_argument("--chunk", type=int, default=40); s.add_argument("--dry-run", action="store_true")
    s.add_argument("--from-dryrun", nargs="+", help="próbafutás-kimenetek (runs/dryrun/*.json); a gráf nem változik")
    s.add_argument("--queue", action="store_true", help="--from-dryrun mellett: az UJ-javaslatok a szakértői sorba")
    s.set_defaults(fn=cmd_kesz_bovit)
    s = sub.add_parser("split", help="jóváhagyott bontási javaslat (split_review) végrehajtása")
    s.add_argument("--review", type=int, action="append", help="a szakértői sor azonosítója (alapból mind)")
    s.set_defaults(fn=cmd_split)
    s = sub.add_parser("report"); s.add_argument("--job", action="append")
    s.add_argument("--by", default="prompt_version,step"); s.set_defaults(fn=cmd_report)
    s = sub.add_parser("models"); s.add_argument("--check-local", action="store_true"); s.set_defaults(fn=cmd_models)
    s = sub.add_parser("ui"); s.add_argument("--port", type=int, default=8765)
    s.add_argument("--host", default="127.0.0.1", help="0.0.0.0: a helyi hálózatról is elérhető")
    s.add_argument("--no-browser", action="store_true"); s.set_defaults(fn=cmd_ui)
    a = p.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
