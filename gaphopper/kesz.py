"""KÉSZ-ség-törzs: a 2. tengely (TESZ-típus-család, hordozhatóság) számítása és a törzsbővítés segédei."""
from __future__ import annotations

import json
from collections import Counter, defaultdict

from .config import vocab


def terulet(field: str | None) -> str:
    """A munkakör fő szakterülete: a field első tagja ("Health and care / Health and welfare" -> "health and care")."""
    return (field or "").split("/")[0].split(";")[0].strip().lower()


def profil(edges: list[dict], fields: dict[str, str]) -> list[dict]:
    """KÉSZ-ségenként családarányok és hordozhatóság.

    edges: {k: Keszseg-id, tesz_type, munkakorok: [...]}; fields: munkakör-id -> field.
    A családarány a súlyozott (munkakörönként számolt) igénylések megoszlása a TESZ-típus-családok között."""
    v = vocab()
    fam = {t: c for c, x in (v.get("tesz_csalad") or {}).items() for t in x["tipusok"]}
    r = v["rules"]
    atv, alt = int(r.get("hordozhatosag_atviheto_terulet", 2)), int(r.get("hordozhatosag_altalanos_terulet", 3))
    cnt: dict[str, Counter] = defaultdict(Counter)
    jobs: dict[str, set] = defaultdict(set)
    for e in edges:
        c = fam.get(e.get("tesz_type") or "")
        mk = e.get("munkakorok") or []
        if c:
            cnt[e["k"]][c] += len(mk) or 1
        jobs[e["k"]].update(mk)
    out = []
    for k in sorted(set(cnt) | set(jobs)):
        tot = sum(cnt[k].values()) or 1
        fams = cnt[k].most_common()
        ter = {terulet(fields.get(j)) for j in jobs[k]} - {""}
        n = len(ter) if ter else (1 if jobs[k] else 0)
        hord = "altalanos" if n >= alt else "atviheto" if n >= atv else "szakmai"
        out.append({"id": k, "csalad": [c for c, _ in fams], "csalad_arany": [round(x / tot, 2) for _, x in fams],
                    "fo_csalad": fams[0][0] if fams else None, "munkakor_db": len(jobs[k]), "terulet_db": n,
                    "hordozhatosag": hord})
    return out


def torzs_keys() -> dict[str, str]:
    """Normalizált név -> gh_c-azonosító a törzs minden nevére (magyar, angol, alias): szinonima-szűrés."""
    from .config import kesz_torzs
    from .normalize import norm_key

    out: dict[str, str] = {}
    for k, x in kesz_torzs().items():
        for text, lang in [(x["label_hu"], "hu"), (x["label_en"], "en")] + \
                [(a[3:], a[:2]) for a in x.get("aliases") or [] if a[2:3] == ":"]:
            if text:
                out.setdefault(norm_key(text, lang), k)
    return out


def hiany_rows(job: dict, nivalo_graph_id: str, hianyok, keys: dict[str, str] | None = None) -> tuple[list, list]:
    """Egy TESZ-nivaló hiányjelöltjei: (gráfba írandó KeszHiany-sorok, a törzsben már meglévő gh_c-k).

    R23-őr: a nem törzsszintű név TUD_SZER állapotú (TUD-ás-/SZER-ELEM-jelölt), nem kerül a kesz-bovit elé."""
    from .normalize import norm_key, stable_id
    from .rules import kesz_hiany_guard

    keys = torzs_keys() if keys is None else keys
    rows, existing = [], []
    for h in hianyok:
        key = norm_key(h.label_hu, "hu")
        hit = keys.get(key) or (keys.get(norm_key(h.label_en, "en")) if h.label_en else None)
        if hit:
            existing.append(hit)
            continue
        ok = kesz_hiany_guard(h.label_hu, h.label_en, job.get("title", ""), job.get("field", ""),
                              job.get("title_hu", ""))
        status = "TUD_SZER" if ok else "JELOLT"
        rows.append({"id": stable_id("gh_kh", status, h.label_hu), "mag": nivalo_graph_id, "status": status,
                     "props": {"label_hu": h.label_hu, "label_en": h.label_en, "csoport": h.csoport,
                               "irany": h.irany, "norm": key, "ok": ok}})
    return rows, existing


# ------------------------------------------------------------------ kesz-irany: 1. tengely elemenként
def run_kesz_irany(ctx, chunk: int = 60, apply: bool = True, only_default: bool = True,
                   iranyok: set[str] | None = None, log=print) -> dict:
    """A csoportból örökölt irány elemenkénti felülvizsgálata (P3-modell). Biztos eltérés: a yaml-ba (irany_forras
    llm); bizonytalan: szakértői sor (kesz_irany_review); a kiírás nélküli elem az alapirányt megerősíti."""
    from .config import kesz_hierarchia, prompt, render, save_kesz_torzs, kesz_torzs
    from .llm import LLMError, call_json
    from .schemas import P7Output

    h = kesz_hierarchia()
    torzs = kesz_torzs()
    items = [x for x in h["items"] if not only_default or x.get("irany_forras", "csoport") == "csoport"]
    if iranyok:  # csak az ilyen irányú elemek (pl. új KÖZ-eg után az érintettek)
        items = [x for x in items if set(torzs[x["id"]]["irany"]) & iranyok]
    out = {"vizsgalt": 0, "valtozott": [], "szakertohoz": [], "hiba": 0, "elavult": 0}
    if apply:
        # a most újravizsgált elemek korábbi, nyitott irány-kérdései elavulnak (az új vizsgálat újakat tehet a sorba)
        ids = {x["id"] for x in items}
        for r in ctx.store.queue():
            if r["kind"] == "kesz_irany_review" and json.loads(r["payload_json"]).get("id") in ids:
                ctx.store.decide(r["id"], "ELAVULT", {"ok": "újravizsgálva (kesz-irany)"})
                out["elavult"] += 1
    by_id = {x["id"]: x for x in h["items"]}
    for i in range(0, len(items), chunk):
        part = items[i:i + chunk]
        nums = {int(x["id"][5:]) for x in part}
        lines = "\n".join(f"{x['id'][5:]}|{x['label_hu']}|{x['csoport']}|{','.join(torzs[x['id']]['irany'])}|"
                          f"{(x.get('leiras_hu') or '')[:90]}" for x in part)

        def check(o):
            bad = [e.n for e in o.elemek if e.n not in nums]
            if bad:
                raise ValueError(f"Ismeretlen sorszámok: {bad}")

        try:
            obj, _ = call_json(ctx.llm("P3"), "Csak a kért tömör JSON-t add vissza.", render(prompt("p7_kesz_irany"), elemek=lines), P7Output,
                               log=ctx.logger(None, "P7", ctx.settings.p3), check=check)
        except LLMError as exc:
            out["hiba"] += len(part)
            log(f"P7 hiba ({len(part)} elem): {exc}")
            continue
        out["vizsgalt"] += len(part)
        seen = set()
        for e in obj.elemek:
            k = f"gh_c_{e.n}"
            seen.add(k)
            row = {"id": k, "label_hu": by_id[k]["label_hu"], "jelenlegi": torzs[k]["irany"], "javaslat": e.irany,
                   "indok": e.indok_hu}
            if not e.biztos:
                out["szakertohoz"].append(row)
                if apply:
                    ctx.store.enqueue(None, "kesz_irany_review", row)
                    by_id[k]["irany_forras"] = "ellenorzendo"
            elif e.irany != torzs[k]["irany"]:
                out["valtozott"].append(row)
                if apply:
                    by_id[k] |= {"irany": e.irany, "irany_forras": "llm"}
        if apply:
            for x in part:
                if x["id"] not in seen:
                    x["irany_forras"] = "llm"
    if apply:
        save_kesz_torzs(h)
        if ctx.graph is not None:
            ctx.graph.upsert_keszseg([r["id"] for r in out["valtozott"]])
    return out


def approve_kesz_irany(ctx, row: dict, payload: dict) -> dict:
    from .config import kesz_hierarchia, save_kesz_torzs

    h = kesz_hierarchia()
    it = next(x for x in h["items"] if x["id"] == payload["id"])
    it |= {"irany": payload["javaslat"], "irany_forras": "szakerto"}
    save_kesz_torzs(h)
    ctx.graph.upsert_keszseg([it["id"]])
    ctx.store.decide(row["id"], "JOVAHAGYVA", {"irany": payload["javaslat"]})
    return {"id": it["id"], "irany": payload["javaslat"]}


# ------------------------------------------------------------------ kesz-bovit: törzsbővítés bizonyíték alapján
def candidates_from_dryrun(paths) -> list[dict]:
    """Próbafutás-kimenetekből (runs/dryrun/*.json) ugyanaz a jelöltlista, mint a gráfból."""
    import json
    from pathlib import Path

    by: dict[str, dict] = {}
    for p in paths:
        d = json.loads(Path(p).read_text(encoding="utf-8"))
        for h in d.get("kesz_hiany") or []:
            if h["status"] != "JELOLT":
                continue
            r = by.setdefault(h["id"], {"id": h["id"], "label_hu": h["label_hu"], "label_en": h.get("label_en", ""),
                                        "csoport": h.get("csoport", ""), "irany": h.get("irany", []),
                                        "nivalok": [], "munkakorok": []})
            if h.get("nivalo") and h["nivalo"] not in r["nivalok"]:
                r["nivalok"].append(h["nivalo"])
            if d["job_id"] not in r["munkakorok"]:
                r["munkakorok"].append(d["job_id"])
    return list(by.values())


def run_kesz_bovit(ctx, rows: list[dict] | None = None, chunk: int = 40, apply: bool = True, queue: bool = False,
                   log=print) -> dict:
    """Hiányjelöltek csoportosítása és döntés (P3-modell, a törzs a gyorsítótárazott rendszerpromptban).
    ALIAS: azonnal a törzselem aliasa lesz (yaml + gráf, IGENYEL élek). UJ: szakértői sorba (kesz_review), ha
    legalább rules.kesz_bovit_min_munkakor munkakör igényli, különben vár. ELVET: a jelölt állapota ELVETVE.
    queue (apply nélkül, pl. próbafutás-kimenetekből): csak az UJ-javaslatok kerülnek a szakértői sorba, a gráf és a
    yaml nem változik."""
    from .config import kesz_block, kesz_hierarchia, prompt, render, save_kesz_torzs, kesz_torzs, vocab
    from .llm import CACHE_SPLIT, LLMError, call_json
    from .schemas import P8Output

    if rows is None:
        rows = ctx.graph.kesz_hiany_candidates()
    min_jobs = int(vocab()["rules"].get("kesz_bovit_min_munkakor", 2))
    system = (prompt("p8_kesz_bovit").split("JELÖLTEK:")[0].strip() + "\n\nKÉSZ-SÉG-TÖRZS (fejléc: irány; "
              "sorszám|név|csoport):\n" + kesz_block() + CACHE_SPLIT)
    out = {"jeloltek": len(rows), "alias": [], "uj": [], "var": [], "elvet": [], "hiba": 0}
    by = {r["id"]: r for r in rows}
    for i in range(0, len(rows), chunk):
        part = rows[i:i + chunk]
        ids = {r["id"] for r in part}
        items = [{"id": r["id"], "nev": r["label_hu"], "en": r["label_en"], "csoport": r["csoport"],
                  "munkakor_db": len(r["munkakorok"]), "tesz_nivalok": r["nivalok"][:3]} for r in part]
        user = "JELÖLTEK:\n" + json.dumps(items, ensure_ascii=False) + "\n\nKIMENET" + \
            prompt("p8_kesz_bovit").split("KIMENET", 1)[1]

        def check(o):
            got = [x for c in o.csoportok for x in c.ids]
            miss, extra = ids - set(got), set(got) - ids
            if miss or extra or len(got) != len(set(got)):
                raise ValueError(f"Minden id pontosan egyszer: hiányzik {sorted(miss)}, ismeretlen {sorted(extra)}")
            torzs = kesz_torzs()
            bad = [c.kesz for c in o.csoportok if c.dontes == "ALIAS" and f"gh_c_{c.kesz}" not in torzs]
            if bad:
                raise ValueError(f"Ismeretlen törzselem-sorszám: {bad}")

        try:
            obj, _ = call_json(ctx.llm("P3"), system, user, P8Output, log=ctx.logger(None, "P8", ctx.settings.p3),
                               check=check)
        except LLMError as exc:
            out["hiba"] += len(part)
            log(f"P8 hiba ({len(part)} jelölt): {exc}")
            continue
        for c in obj.csoportok:
            grp = [by[x] for x in c.ids]
            jobs = sorted({j for r in grp for j in r["munkakorok"]})
            names = [r["label_hu"] for r in grp]
            if c.dontes == "ALIAS":
                k = f"gh_c_{c.kesz}"
                out["alias"].append({"ids": c.ids, "nevek": names, "kesz": k, "kesz_nev": kesz_torzs()[k]["label_hu"],
                                     "indok": c.indok_hu})
                if apply:
                    h = kesz_hierarchia()
                    it = next(x for x in h["items"] if x["id"] == k)
                    al = list(it.get("aliases") or [])
                    for r in grp:
                        for t in (f"hu:{r['label_hu']}", f"en:{r['label_en']}" if r["label_en"] else ""):
                            if t and t not in al and t[3:] not in (it["label_hu"], it["label_en"]):
                                al.append(t)
                    it["aliases"] = al
                    save_kesz_torzs(h)
                    ctx.graph.upsert_keszseg([k])
                    ctx.graph.kesz_hiany_resolve(c.ids, k, "ALIAS")
            elif c.dontes == "UJ":
                prop = {"ids": c.ids, "nevek": names, "label_hu": c.label_hu, "label_en": c.label_en,
                        "csoport": c.csoport, "irany": c.irany, "munkakorok": jobs, "indok": c.indok_hu}
                if len(jobs) < min_jobs:
                    out["var"].append(prop)   # kevés bizonyíték: a jelölt marad, a következő futás újra nézi
                    continue
                out["uj"].append(prop)
                if apply or queue:
                    ctx.store.enqueue(None, "kesz_review", prop)
                if apply:
                    ctx.graph.kesz_hiany_status(c.ids, "JAVASOLT", c.indok_hu)
            else:
                out["elvet"].append({"ids": c.ids, "nevek": names, "indok": c.indok_hu})
                if apply:
                    ctx.graph.kesz_hiany_status(c.ids, "ELVETVE", c.indok_hu)
    if apply and out["alias"]:
        ctx.graph.update_kesz_profil()
    return out


def next_kesz_id(items: list[dict]) -> str:
    return f"gh_c_{max(int(x['id'][5:]) for x in items) + 1}"


def approve_kesz(ctx, row: dict, payload: dict) -> dict:
    """Jóváhagyott UJ javaslat: a következő gh_c szám, csoporttal és iránnyal a yaml-ba és a gráfba; a jelöltet igénylő
    TESZ-nivalók IGENYEL élt kapnak rá."""
    from .config import kesz_hierarchia, save_kesz_torzs

    h = kesz_hierarchia()
    kid = next_kesz_id(h["items"])
    aliases = [f"hu:{n}" for n in payload.get("nevek", []) if n != payload["label_hu"]]
    aliases += [a for a in payload.get("aliases_extra", []) if a not in aliases]   # pl. átnevezés előtti angol név
    h["items"].append({"id": kid, "label_hu": payload["label_hu"], "label_en": payload["label_en"],
                       "csoport": payload["csoport"], "irany": payload["irany"], "irany_forras": "szakerto",
                       "forras": "kesz-bovit", "aliases": aliases})
    save_kesz_torzs(h)
    ctx.graph.upsert_keszseg([kid])
    # próbafutásból sorba tett javaslatnál a jelöltek nincsenek a gráfban: IGENYEL él a munkakör valódi futásakor lesz
    n = ctx.graph.kesz_hiany_resolve(payload["ids"], kid, "BEEMELT")
    ctx.graph.update_kesz_profil()
    ctx.store.decide(row["id"], "JOVAHAGYVA", {"kesz_id": kid})
    return {"id": kid, "label_hu": payload["label_hu"], "igenyel": n}
