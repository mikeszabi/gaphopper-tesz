"""A szakértői sor tételeinek olvasható magyarázata (UI és `review` CLI): a szabály neve és jelentése, az érintett
elemek teljes megnevezéssel, a P2 előtte/utána, és mit jelent a jóváhagyás és az elutasítás.

A szabálynév-szótár: config/szabalyok.yaml (egy helyen, mindkét felület innen olvas)."""
from __future__ import annotations

import json
from functools import lru_cache

import yaml

from .config import ROOT, kesz_torzs, vocab

SZABALY_PATH = ROOT / "config" / "szabalyok.yaml"


@lru_cache(maxsize=1)
def szabalyok() -> dict:
    with open(SZABALY_PATH, encoding="utf-8") as fh:
        return yaml.safe_load(fh) or {}


def rule_info(code: str) -> dict:
    x = (szabalyok().get("szabalyok") or {}).get(code) or {}
    return {"kod": code, "nev": x.get("nev", code), "jelentes": x.get("jelentes", "")}


def _hu(group: str, code: str | None) -> str:
    return ((vocab().get(group) or {}).get(code) or {}).get("hu", code or "")


def nivalo_view(m: dict, szinek: list[dict]) -> dict:
    t = kesz_torzs()
    szer = m.get("primary_szer") or {}
    return {"nivalo_id": m.get("nivalo_id"), "label_hu": m.get("label_hu", ""), "label_en": m.get("label_en", ""),
            "tesz_type": m.get("tesz_type"), "tesz_type_hu": _hu("tesz_tipus", m.get("tesz_type")),
            "szer": szer.get("label_hu", ""), "szer_fajta": _hu("szer_elem_fajta", szer.get("kind")),
            "relevance": m.get("relevance"), "kontextus_hu": m.get("kontextus_hu") or "",
            "torzsbol": bool(m.get("nivalo_ref")), "outcome_hu": m.get("outcome_hu", ""),
            "kesz": [{"id": k, "label_hu": t.get(k, {}).get("label_hu", k)} for k in map(_kid, m.get("kesz_ids") or [])],
            "szinek": [{"cand_id": s.get("cand_id"), "alap": bool(s.get("alap_szin")),
                        "kozeg": _hu("kozeg", s.get("kozeg")), "helyzet": _hu("helyzet", s.get("helyzet")),
                        "kulonbseg": s.get("szin_kulonbseg_hu") or ""} for s in szinek]}


def _kid(v) -> str:
    from .schemas import kesz_id

    try:
        return kesz_id(v)
    except ValueError:
        return str(v)


def _diff(a: dict, b: dict) -> list[str]:
    out = []
    if a["tesz_type"] != b["tesz_type"]:
        out.append(f"típus: {a['tesz_type_hu']} ↔ {b['tesz_type_hu']}")
    if a["szer"] != b["szer"]:
        out.append(f"meghatározó SZER-ELEM: {a['szer']} ↔ {b['szer']}")
    ka, kb = {k["label_hu"] for k in a["kesz"]}, {k["label_hu"] for k in b["kesz"]}
    if ka & kb:
        out.append("közös KÉSZ-ség: " + ", ".join(sorted(ka & kb)))
    if ka ^ kb:
        out.append("eltérő KÉSZ-ség: " + ", ".join(sorted(ka - kb)) + " ↔ " + ", ".join(sorted(kb - ka)))
    return out


def _job_review(S, job_id: str, payload: dict) -> dict:
    p1 = S.artifact(job_id, "P1") or {}
    p2 = S.artifact(job_id, "P2") or {}
    final_n = p2.get("final_nivalok") or p1.get("nivalok") or []
    final_s = p2.get("final_szinek") or p1.get("szinek") or []
    by_n = {m["nivalo_id"]: m for m in final_n}
    szin_of = {s.get("cand_id"): s for s in final_s}
    views = {k: nivalo_view(m, [s for s in final_s if s.get("nivalo_id") == k]) for k, m in by_n.items()}
    p1_n = {m.get("nivalo_id"): m for m in p1.get("nivalok") or []}
    p1_s = p1.get("szinek") or []
    # a jelzések újraellenőrzése a mostani szabályokkal (a szabály javulhatott a futás óta)
    now = None
    try:
        from .pipeline import check_rules, final_lists
        from .schemas import P1Output

        magok, final = final_lists(p1, p2)
        now = {(f.rule, tuple(sorted(f.cand_ids))) for f in check_rules(P1Output.model_validate(p1), dict(S.job(job_id)),
                                                                         magok, final)}
        now_rules = {r for r, _ in now}
    except Exception:  # régi vagy hiányos eredmény: csak a mentett jelzés látszik
        now = None
    flags = []
    for f in payload.get("flags") or []:
        ids = []
        for c in f.get("cand_ids") or []:
            nid = c if c in by_n else (szin_of.get(c) or {}).get("nivalo_id")
            if nid and nid not in ids:
                ids.append(nid)
        el = [views[i] for i in ids if i in views]
        x = rule_info(f["rule"]) | {"uzenet": f.get("message", ""), "elemek": el,
                                    "aktualis": None if now is None else f["rule"] in now_rules}
        if f["rule"] == "R18" and len(el) == 2:
            x["kulonbseg"] = _diff(el[0], el[1])
        flags.append(x)
    changes = []
    for ch in p2.get("changes") or []:
        nid = ch.get("nivalo_id") or (szin_of.get(ch.get("cand_id")) or {}).get("nivalo_id")
        if not nid and ch.get("op") == "ADD":  # új TESZ-nivaló: az első hozzáadott
            nid = next((x.get("nivalo_id") for x in ch.get("new_nivalo") or [] if isinstance(x, dict)), None)
        before = p1_n.get(nid) if ch.get("op") != "ADD" else None
        changes.append({"op": ch.get("op"), "nivalo_id": nid, "cand_id": ch.get("cand_id"),
                        "indok": ch.get("reason_hu", ""), "probak": [rule_info(r) for r in ch.get("failed_tests") or []],
                        "elotte": nivalo_view(before, [s for s in p1_s if s.get("nivalo_id") == nid]) if before else None,
                        "utana": views.get(nid)})
    return {"flags": flags, "valtozasok": changes, "kerdesek": payload.get("questions") or [],
            "hianyok": payload.get("gaps") or p2.get("coverage_gaps_hu") or [],
            "verdict": payload.get("verdict"), "nivalo_db": len(final_n), "szin_db": len(final_s)}


def _kesz_irany(payload: dict, graph) -> dict:
    t = kesz_torzs()
    from .config import kesz_hierarchia

    cs = {c["kod"]: c["label_hu"] for c in kesz_hierarchia()["csoportok"]}
    k = t.get(payload.get("id"), {})
    nivalok = []
    if graph is not None:
        nivalok = graph.read("MATCH (g:TeszNivalo)-[e:IGENYEL]->(:Keszseg {id: $id}) RETURN g.label_hu AS nivalo, "
                             "coalesce(e.suly, 1) AS suly ORDER BY suly DESC", id=payload.get("id")) or []
    return {"kesz": {"id": payload.get("id"), "label_hu": k.get("label_hu", payload.get("label_hu", "")),
                     "label_en": k.get("label_en", ""), "csoport": k.get("csoport", ""),
                     "csoport_hu": cs.get(k.get("csoport"), "")},
            "jelenlegi": [_hu("kozeg", i) for i in payload.get("jelenlegi") or []],
            "javasolt": [_hu("kozeg", i) for i in payload.get("javaslat") or []],
            "indok": payload.get("indok", ""), "igenylo_nivalok": nivalok}


def explain(S, row, graph=None) -> dict:
    """Egy szakértői kérés olvasható magyarázata."""
    kind = row["kind"]
    payload = json.loads(row["payload_json"]) if isinstance(row["payload_json"], str) else row["payload_json"]
    tetel = (szabalyok().get("tetelek") or {}).get(kind) or {"nev": kind, "jovahagyas": "", "elutasitas": ""}
    out = {"id": row["id"], "kind": kind, "tetel": tetel, "job": None}
    jid = row["job_id"]
    if jid:
        try:
            job = S.job(jid)
        except KeyError:
            job = {}
        p1 = S.artifact(jid, "P1") or {}
        out["job"] = {"id": jid, "label_hu": p1.get("job_label_hu") or job.get("title", ""),
                      "label_en": job.get("title") or p1.get("job_label_en", "")}
    if kind == "job_review":
        out |= _job_review(S, jid, payload)
    elif kind == "merge_review":
        out["elemek"] = [{"fajta": "TESZ-nivaló" if x.get("kind") == "mag" else "SZER-ELEM", "label_hu": x.get("label"),
                          "id": x.get("id")} for x in payload.get("items") or []]
        out["magyarazat"] = ("A P3 egyesítés ezeknél nem volt biztos, hogy a munkakör eleme azonos-e a gráfban már "
                             "meglévővel; az egyesítés megtörtént, ellenőrizd.")
    elif kind == "kesz_irany_review":
        out |= _kesz_irany(payload, graph)
    elif kind == "kesz_review":
        out |= {"javaslat": {k: payload.get(k) for k in ("label_hu", "label_en", "csoport", "indok")},
                "irany": [_hu("kozeg", i) for i in payload.get("irany") or []],
                "jeloltek": payload.get("nevek") or [], "munkakorok": payload.get("munkakorok") or []}
    elif kind == "split_review":
        out |= {"nivalo": payload.get("nev"), "indok": payload.get("indok", ""), "javaslat": payload.get("javaslat") or []}
    return out


def format_text(x: dict) -> str:
    """A `review` CLI-lista szövege."""
    L = [f"[{x['id']}] {x['tetel']['nev']}" + (f" – {x['job']['label_hu']} ({x['job']['label_en']})" if x.get("job") else "")]

    def niv(v, pre="    "):
        L.append(f"{pre}{v['label_hu']} · {v['tesz_type_hu']} · ‹{v['szer']}›" + (" · törzsből" if v["torzsbol"] else ""))
        if v["kesz"]:
            L.append(f"{pre}  KÉSZ-ség: " + ", ".join(k["label_hu"] for k in v["kesz"]))
        if v["szinek"]:
            L.append(f"{pre}  SZÍN: " + "; ".join(("● " if s["alap"] else "○ ") + f"{s['kozeg']} / {s['helyzet']}"
                                                  for s in v["szinek"]))
    for f in x.get("flags") or []:
        L.append(f"  ⚑ {f['kod']} – {f['nev']}: {f['jelentes']}"
                 + ("  [a mostani szabályok szerint már nem áll fenn]" if f.get("aktualis") is False else ""))
        for v in f["elemek"]:
            niv(v)
        for d in f.get("kulonbseg") or []:
            L.append(f"    eltérés: {d}")
    for c in x.get("valtozasok") or []:
        L.append(f"  P2 {c['op']} ({', '.join(p['kod'] for p in c['probak']) or '-'}): {c['indok']}")
        if c["elotte"]:
            L.append(f"    előtte: {c['elotte']['label_hu']} ‹{c['elotte']['szer']}›")
        if c["utana"]:
            L.append(f"    utána:  {c['utana']['label_hu']} ‹{c['utana']['szer']}›")
    for g in x.get("hianyok") or []:
        L.append(f"  lefedetlen: {g}")
    for e in x.get("elemek") or []:
        L.append(f"  {e['fajta']}: {e['label_hu']}")
    if x["kind"] == "kesz_irany_review":
        k = x["kesz"]
        L.append(f"  {k['label_hu']} ({k['csoport']} {k['csoport_hu']}): {' + '.join(x['jelenlegi'])} → "
                 f"{' + '.join(x['javasolt'])}; {x['indok']}")
        if x["igenylo_nivalok"]:
            L.append("  igénylő TESZ-nivalók: " + ", ".join(r["nivalo"] for r in x["igenylo_nivalok"]))
    if x["kind"] == "kesz_review":
        j = x["javaslat"]
        L.append(f"  {j['label_hu']} / {j['label_en']} ({j['csoport']}, {' + '.join(x['irany'])}); "
                 f"{len(x['munkakorok'])} munkakör; jelöltek: {', '.join(x['jeloltek'])}")
    L.append(f"  Jóváhagyás: {x['tetel']['jovahagyas']}")
    L.append(f"  Elutasítás: {x['tetel']['elutasitas']}")
    return "\n".join(L)


HELP_PATH = ROOT / "docs" / "hasznalati-segedlet.md"


def rules_markdown() -> str:
    """A Használati segéd szabálytáblája a config/szabalyok.yaml-ból (egy forrás)."""
    rows = ["| kód | név | jelentés |", "|---|---|---|"]
    rows += [f"| {k} | {v['nev']} | {v['jelentes']} |" for k, v in (szabalyok().get("szabalyok") or {}).items()]
    return "\n".join(rows)


def help_markdown(sync: bool = False) -> str:
    """A Használati segéd szövege, a szabálytábla a yaml-ból frissítve (sync: a fájlba is visszaírja)."""
    import re

    text = HELP_PATH.read_text(encoding="utf-8")
    new = re.sub(r"(<!-- szabalyok:start -->\n).*?(\n<!-- szabalyok:end -->)",
                 lambda m: m.group(1) + rules_markdown() + m.group(2), text, flags=re.S)
    if sync and new != text:
        HELP_PATH.write_text(new, encoding="utf-8")
    return new
