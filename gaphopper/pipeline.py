"""A feldolgozási sor: P1 felbontás -> P2 bírálat -> P3 egyesítés és gráfba írás; P4a horgonyzás.

A P1 TESZ-MAG-okat és SZÍN-ezett változatokat (TESZ-nivalókat) ad; a bírálat és a gráfba írás
mindkét szinten dolgozik. Próbafutás (dry_run): az LLM-lépések lefutnak, a gráf csak olvasott, a
state.sqlite érintetlen.
"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path

from . import rules
from .config import ROOT, Settings, StepModel, kesz_torzs, prompt, prompt_version, render, settings, vocab
from .embed import get_embedder
from .llm import CACHE_SPLIT, LLMError, ManualStep, call_json, get_llm
from .merge import Resolver
from .normalize import norm_key, stable_id
from .schemas import (MAG_FIELDS, MAG_IDENTITY, P1Output, P2Change, P2Output, P4aOutput, P5MagOutput, Tesz, TeszMag,
                      link_magok)
from .store import Store

MANUAL_DIR = ROOT / "runs" / "manual"


class Context:
    def __init__(self, s: Settings | None = None, with_graph: bool = True,
                 answer_files: dict[str, Path] | None = None, llm_override: dict | None = None):
        self.settings = s or settings()
        self.store = Store(self.settings.state_db)
        self.graph = None
        if with_graph:
            from .graph import Graph

            self.graph = Graph(self.settings)
        self.embedder = get_embedder(self.settings.embed_model, self.settings.embed_provider,
                                     self.settings.ollama_host, self.settings.embed_prefix)
        self.system = prompt("p0_system")
        self.pv = prompt_version()
        self.answer_files = answer_files or {}
        self.llm_override = llm_override or {}
        self.current_job: str | None = None
        self.extra_torzs: list[dict] = []   # próbafutás-láncban a korábbi munkakörök új MAG-jai
        self._torzs: dict[str, dict] | None = None

    # ---------------------------------------------------------------- MAG-törzs (a P1 rendszerpromptjában)
    def mag_torzs(self, refresh: bool = False) -> dict[str, dict]:
        """Rövid kód (T01..) -> törzs-MAG. A gráf legtöbbet használt, ezzel a szótárverzióval készült
        MAG-jai (legfeljebb rules.mag_torzs_max), és próbafutás-láncban a korábbi munkakörök MAG-jai."""
        if self._torzs is None or refresh:
            limit = int(vocab()["rules"].get("mag_torzs_max", 80))
            rows = []
            if self.graph is not None and hasattr(self.graph, "mag_torzs"):
                rows = self.graph.mag_torzs(vocab().get("version", ""), limit)
            seen = {r["id"] for r in rows}
            rows += [r for r in self.extra_torzs if r["id"] not in seen]
            self._torzs = {f"T{i + 1:02d}": r for i, r in enumerate(rows[:limit])}
        return self._torzs

    def p1_system(self) -> str:
        """P0 + KÉSZ-ség-törzs (statikus, gyorsítótárazott) | MAG-törzs (futásonként)."""
        kesz = "\n".join(f"{k[5:]}|{x['en']}|{x['group']}{x['type']}" for k, x in kesz_torzs().items())
        torzs = self.mag_torzs()
        mags = "\n".join(f"{c}|{r['label_hu']}|{r['tesz_type']}" for c, r in torzs.items()) or "(még üres)"
        return (self.system + "\n\nKÉSZ-SÉG-TÖRZS (a kesz_ids-be a sorszám kerül; sorszám|név|csoport+típus, "
                "K = tudás, S = készség, A = képesség):\n" + kesz + CACHE_SPLIT
                + "MAG-TÖRZS (a mag_ref-be a kód kerül; kód|címke|TESZ-típus):\n" + mags)

    def resolve_mag_refs(self, data):
        """P1-válasz előfeldolgozása: a mag_ref-es MAG mezői a törzsből (a modellnek nem kell kiírnia)."""
        if not isinstance(data, dict):
            return data
        torzs = self.mag_torzs()
        by_id = {r["id"]: r for r in torzs.values()}
        for m in data.get("tesz_magok") or []:
            ref = m.get("mag_ref") if isinstance(m, dict) else None
            if not ref:
                continue
            r = torzs.get(ref) or by_id.get(ref)
            if r is None:
                raise ValueError(f"{m.get('mag_id')}: ismeretlen mag_ref '{ref}'; a MAG-törzs kódjai: "
                                 f"{', '.join(list(torzs)[:3])}…{list(torzs)[-1] if torzs else ''} – vagy írj új MAG-ot.")
            m["mag_ref"] = r["id"]
            for k in ("label_hu", "label_en", "outcome_hu", "tesz_type", "primary_szer"):
                m[k] = r.get(k) if r.get(k) is not None else m.get(k)
            if not m.get("kesz_ids"):
                m["kesz_ids"] = r.get("kesz_ids") or []
        return data

    def llm(self, step: str):
        if step in self.llm_override:
            return self.llm_override[step]
        cfg: StepModel = getattr(self.settings, step.lower())
        return get_llm(cfg, step, self.answer_files.get(step), self.settings)

    def logger(self, job_id: str | None, step: str, cfg: StepModel):
        def _log(raw: str, ok: bool, usage: dict) -> None:
            self.store.log(job_id, step, cfg.provider, cfg.model, self.pv, "", raw, ok, usage)
        return _log

    def close(self) -> None:
        if self.graph:
            self.graph.close()


# ------------------------------------------------------------------ P1, P2
def _retry_note(ctx: Context, job_id: str, step: str) -> str:
    err = ctx.store.artifact(job_id, f"{step}_ERR")
    if not err:
        return ""
    return ("\n\nAZ ELŐZŐ KÍSÉRLET NEM FELELT MEG A SÉMÁNAK:\n" + err.get("error", "")[:2000]
            + "\nKerüld el ezt a hibát, és a teljes JSON-t add vissza.")


def build_p1(ctx: Context, job: dict) -> str:
    return (render(prompt("p1_felbontas"), job_record_json=json.dumps(job, ensure_ascii=False, separators=(",", ":")))
            + _retry_note(ctx, job["job_id"], "P1"))


def _sampled(job_id: str, share: float) -> bool:
    """Determinisztikus véletlen minta: ugyanaz a munkakör mindig ugyanúgy esik."""
    h = int(hashlib.sha1(job_id.encode("utf-8")).hexdigest()[:8], 16) / 0xFFFFFFFF
    return h < share


def check_rules(p1: P1Output, job: dict, magok=None, tesz=None, torzs_size: int = 0) -> list:
    return rules.check(p1.tesz if tesz is None else tesz, [k.kor_id for k in p1.tesz_korok], job.get("field", ""),
                       magok=p1.tesz_magok if magok is None else magok, job_title=job.get("title", ""),
                       torzs_size=torzs_size)


def p2_needed(ctx: Context, job: dict, p1: P1Output) -> tuple[bool, str, list]:
    """Kell-e bírálat? auto módban csak szabályjelzésnél és a véletlen mintán."""
    flags = check_rules(p1, job, torzs_size=len(ctx.mag_torzs()))
    mode = ctx.settings.p2_mode
    if mode == "always":
        return True, "mindig", flags
    if mode == "never":
        return False, "kikapcsolva", flags
    if flags:
        return True, "szabályjelzés: " + ", ".join(sorted({f.rule for f in flags})), flags
    if _sampled(job["job_id"], ctx.settings.p2_sample):
        return True, f"minőségi minta ({ctx.settings.p2_sample:.0%})", flags
    return False, "szabályok rendben", flags


def finish_p1(ctx: Context, job: dict, obj: P1Output) -> P1Output:
    obj.job_id = job["job_id"]  # a rekord azonosítója a mérvadó, nem a modellé
    ctx.store.save_artifact(job["job_id"], "P1", obj.model_dump(), ctx.pv)
    ctx.store.clear_artifact(job["job_id"], "P1_ERR")
    need, reason, flags = p2_needed(ctx, job, obj)
    if need:
        ctx.store.set_status(job["job_id"], "P1_KESZ", f"P2 kell: {reason}")
        return obj
    # A bírálat kimarad: a P1 lista a végleges.
    art = {"verdict": "SKIPPED", "p2_reason": reason, "changes": [], "warnings": obj.link_warnings,
           "final_magok": [m.model_dump() for m in obj.tesz_magok],
           "final_tesz": [t.model_dump() for t in obj.tesz],
           "rule_flags_before": [f.as_dict() for f in flags], "rule_flags_after": [f.as_dict() for f in flags]}
    ctx.store.save_artifact(job["job_id"], "P2", art, ctx.pv)
    if rules.needs_expert(flags):
        ctx.store.enqueue(job["job_id"], "job_review", {"verdict": "SKIPPED", "flags": art["rule_flags_after"],
                                                         "questions": [], "gaps": []})
        ctx.store.set_status(job["job_id"], "SZAKERTORE", f"P2 kimaradt ({reason}); szakértői jelzés")
    else:
        ctx.store.set_status(job["job_id"], "P2_ELFOGADVA", f"P2 kimaradt: {reason}")
    return obj


def record_error(ctx: Context, job_id: str, step: str, error: str, back_to: str) -> None:
    """Sémahiba vagy API-hiba: a rekord visszaáll, a hiba a következő kísérlet promptjába kerül."""
    ctx.store.save_artifact(job_id, f"{step}_ERR", {"error": error}, ctx.pv)
    ctx.store.set_status(job_id, back_to, f"{step} hiba: {error[:300]}")


def run_p1(ctx: Context, job: dict) -> P1Output:
    try:
        obj, _ = call_json(ctx.llm("P1"), ctx.p1_system(), build_p1(ctx, job), P1Output,
                           log=ctx.logger(job["job_id"], "P1", ctx.settings.p1), pre=ctx.resolve_mag_refs)
    except LLMError as exc:
        record_error(ctx, job["job_id"], "P1", str(exc), "NYERS")
        raise
    return finish_p1(ctx, job, obj)


def p2_view(p1: P1Output) -> dict:
    """A P1 tömörített nézete a bírálónak: MAG-onként csoportosítva, indoklás, angol címke, definíció és
    alapértékek nélkül; a változat nem ismétli a MAG mezőit."""
    def szer(s, full=False):
        d = {"label_hu": s.label_hu, "kind": s.kind, "role": s.role}
        if full and s.definition_hu:
            d["definition_hu"] = s.definition_hu
        return d

    magok = []
    for m in p1.tesz_magok:
        d = {"mag_id": m.mag_id, "kor_id": m.kor_id, "label_hu": m.label_hu, "outcome_hu": m.outcome_hu,
             "tesz_type": m.tesz_type, "runner_up": m.tesz_type_runner_up, "primary_szer": szer(m.primary_szer, True),
             "relevance": m.relevance,
             "kesz": [f"{k[5:]} {kesz_torzs().get(k, {}).get('en', '')}" for k in m.kesz_ids],
             "kontextus_hu": m.kontextus_hu, "mag_ref": m.mag_ref}
        if m.melyites_jeloltek:
            d["melyites_jeloltek"] = [x.model_dump() for x in m.melyites_jeloltek]
        v = []
        for t in p1.tesz:
            if t.mag_id != m.mag_id:
                continue
            x = {"cand_id": t.cand_id}
            if not t.alap_szin:
                x |= {"alap_szin": False, "szin_kulonbseg_hu": t.szin_kulonbseg_hu}
            x |= {"kozeg": t.kozeg, "helyzet": t.helyzet}
            if t.kozeg_secondary:
                x["kozeg_secondary"] = t.kozeg_secondary
            if t.helyzet_secondary:
                x["helyzet_secondary"] = t.helyzet_secondary
            if t.secondary_szer:
                x["secondary_szer"] = [s.label_hu for s in t.secondary_szer]
            x |= {"frequency": t.frequency, "expected_eqf": t.expected_eqf, "confidence": t.confidence}
            if t.open_question:
                x["open_question"] = t.open_question
            v.append({k: val for k, val in x.items() if val is not None})
        d["v"] = v
        magok.append({k: val for k, val in d.items() if val is not None})
    return {"tesz_korok": [k.model_dump() for k in p1.tesz_korok], "tesz_magok": magok}


def _json(obj) -> str:
    return json.dumps(obj, ensure_ascii=False, separators=(",", ":"))


def build_p2(ctx: Context, job: dict, p1: P1Output) -> tuple[str, list]:
    flags = check_rules(p1, job, torzs_size=len(ctx.mag_torzs()))
    user = render(prompt("p2_biralat"),
                  job_record_json=_json(job),
                  p1_json=_json(p2_view(p1)),
                  rule_flags_json=_json([{k: v for k, v in f.as_dict().items() if v and k != "severity"}
                                         for f in flags]))
    return user + _retry_note(ctx, job["job_id"], "P2"), flags


_MAG_SET = set(TeszMag.model_fields) | {"runner_up"}


def apply_p2(p1_magok: list[TeszMag], p1_tesz: list[Tesz], changes: list[P2Change]
             ) -> tuple[list[TeszMag], list[Tesz], list[str]]:
    """A bírálat eltéréseinek alkalmazása a P1 MAG- és változatlistájára.

    Hibás eltérésnél ValueError (újrapróbálás). A végén az R15 (egy ALAP-SZÍN MAG-onként) javítva,
    a változat nélküli MAG-ok elhagyva – ezekről figyelmeztetés szól.
    """
    if p1_tesz and any(t.mag_id is None for t in p1_tesz):  # régi eredmény: egyelemű MAG-ok
        p1_magok, p1_tesz, _ = link_magok(list(p1_magok), list(p1_tesz), strict=False)
    order = [t.cand_id for t in p1_tesz]
    items: dict[str, dict] = {t.cand_id: t.model_dump(exclude=set(MAG_FIELDS)) for t in p1_tesz}
    mag_order = [m.mag_id for m in p1_magok]
    mags: dict[str, dict] = {m.mag_id: m.model_dump() for m in p1_magok}
    warnings: list[str] = []
    used = set(order)
    used_m = set(mag_order)

    def fresh_id(cid: str, pool: set, prefix: str) -> str:
        if cid and cid not in pool:
            pool.add(cid)
            return cid
        n = len(pool) + 1
        while f"{prefix}{n:02d}" in pool:
            n += 1
        pool.add(f"{prefix}{n:02d}")
        return f"{prefix}{n:02d}"

    def need(cid: str | None, op: str) -> bool:
        if cid in items:
            return True
        if cid in order:  # már elhagyott elemre vonatkozik
            warnings.append(f"{op} {cid}: az elem már nincs a listában.")
            return False
        raise ValueError(f"{op}: ismeretlen cand_id '{cid}'. Csak a P1 cand_id-jeire hivatkozz.")

    def need_mag(mid: str | None, op: str) -> bool:
        if mid in mags:
            return True
        if mid in mag_order:
            warnings.append(f"{op} {mid}: a MAG már nincs a listában.")
            return False
        raise ValueError(f"{op}: ismeretlen mag_id '{mid}'. Csak a meglévő vagy most hozzáadott mag_id-kre hivatkozz.")

    def add_new(ch: P2Change, pos: int, drop_mag: str | None = None) -> None:
        remap: dict[str, str] = {}
        for m in ch.new_mag:
            d = m.model_dump()
            d["mag_id"] = remap[m.mag_id] = fresh_id(m.mag_id, used_m, "M")
            mags[d["mag_id"]] = d
            mag_order.append(d["mag_id"])
        if not ch.new:
            raise ValueError(f"{ch.op}: a 'new' lista üres; add meg a teljes új változatokat.")
        for k, t in enumerate(ch.new):
            d = t.model_dump(exclude=set(MAG_FIELDS))
            d["mag_id"] = remap.get(d.get("mag_id"), d.get("mag_id"))
            if d["mag_id"] not in mags or d["mag_id"] == drop_mag:
                raise ValueError(f"{ch.op}: a(z) {t.cand_id} változat mag_id-je ('{d['mag_id']}') nem létező MAG; "
                                 "adj meg new_mag-ot vagy egy meglévő mag_id-t.")
            d["cand_id"] = fresh_id(d.get("cand_id", ""), used, "C")
            items[d["cand_id"]] = d
            order.insert(pos + k, d["cand_id"])

    for ch in changes:
        if ch.op == "FIX" and not ch.cand_id:
            if not ch.mag_id or not need_mag(ch.mag_id, "FIX"):
                if not ch.mag_id:
                    raise ValueError("FIX: cand_id vagy mag_id kell.")
                continue
            bad = set(ch.set) - _MAG_SET
            if bad:
                raise ValueError(f"FIX {ch.mag_id}: ezek nem MAG-mezők: {sorted(bad)}; változatmezőt cand_id-vel javíts.")
            merged = mags[ch.mag_id] | {k: v for k, v in ch.set.items() if k != "mag_id"}
            if set(ch.set) & set(MAG_IDENTITY) and "mag_ref" not in ch.set:
                merged["mag_ref"] = None  # a kilét változott: már nem a törzs eleme
            try:
                mags[ch.mag_id] = TeszMag.model_validate(merged).model_dump()
            except Exception as exc:
                raise ValueError(f"FIX {ch.mag_id}: a javított MAG nem érvényes: {exc}")
        elif ch.op == "FIX":
            if not need(ch.cand_id, "FIX"):
                continue
            mag_part = {k: v for k, v in ch.set.items() if k in MAG_FIELDS}
            if mag_part:  # a bíráló változaton javított MAG-mezőt: a MAG-ra vonatkozik
                mid = ch.set.get("mag_id", items[ch.cand_id]["mag_id"])
                if need_mag(mid, "FIX"):
                    try:
                        mags[mid] = TeszMag.model_validate(mags[mid] | mag_part).model_dump()
                    except Exception as exc:
                        raise ValueError(f"FIX {ch.cand_id}: a javított MAG ({mid}) nem érvényes: {exc}")
                    warnings.append(f"FIX {ch.cand_id}: {sorted(mag_part)} a(z) {mid} MAG-on javítva.")
            merged = items[ch.cand_id] | {k: v for k, v in ch.set.items() if k != "cand_id" and k not in MAG_FIELDS}
            if merged.get("mag_id") not in mags:
                raise ValueError(f"FIX {ch.cand_id}: ismeretlen mag_id '{merged.get('mag_id')}'.")
            try:
                items[ch.cand_id] = Tesz.model_validate(merged).model_dump(exclude=set(MAG_FIELDS))
            except Exception as exc:
                raise ValueError(f"FIX {ch.cand_id}: a javított változat nem érvényes: {exc}")
        elif ch.op == "DROP" and not ch.cand_id:
            if not ch.mag_id:
                raise ValueError("DROP: cand_id vagy mag_id kell.")
            if need_mag(ch.mag_id, "DROP"):
                mags.pop(ch.mag_id)
                for c in [c for c, d in items.items() if d["mag_id"] == ch.mag_id]:
                    items.pop(c)
        elif ch.op == "DROP":
            if need(ch.cand_id, "DROP"):
                items.pop(ch.cand_id)
        elif ch.op in ("MERGE", "FOLD"):
            if not need(ch.cand_id, ch.op):
                continue
            if ch.into not in items:
                raise ValueError(f"{ch.op} {ch.cand_id}: az 'into' ('{ch.into}') nem létező cand_id.")
            gone = items.pop(ch.cand_id)
            if ch.op == "FOLD":
                tgt = items[ch.into]
                kind = ch.as_ or ("kozeg_secondary" if gone["helyzet"] == tgt["helyzet"] else "helyzet_secondary")
                if kind == "kozeg_secondary":
                    ks = list(tgt.get("kozeg_secondary") or [])
                    if gone["kozeg"] != tgt["kozeg"] and gone["kozeg"] not in ks:
                        if len(ks) >= 2:
                            warnings.append(f"FOLD {ch.cand_id}: {ch.into} már két kísérő KÖZ-eget visel.")
                        else:
                            tgt["kozeg_secondary"] = ks + [gone["kozeg"]]
                elif gone["helyzet"] != tgt["helyzet"]:
                    if tgt.get("helyzet_secondary") and tgt["helyzet_secondary"] != gone["helyzet"]:
                        warnings.append(f"FOLD {ch.cand_id}: {ch.into} másodlagos HELY-zete felülírva.")
                    tgt["helyzet_secondary"] = gone["helyzet"]
        elif ch.op == "REMAG":
            ids = ch.cand_ids or ([ch.cand_id] if ch.cand_id else [])
            if not ids or not ch.mag_id:
                raise ValueError("REMAG: cand_ids és mag_id kell.")
            if not need_mag(ch.mag_id, "REMAG"):
                continue
            for c in ids:
                if not need(c, "REMAG"):
                    continue
                if items[c]["mag_id"] != ch.mag_id:
                    items[c]["mag_id"] = ch.mag_id
                    items[c]["alap_szin"] = False  # a cél MAG ALAP-SZÍN-e marad (R15 javítja, ha nincs)
        elif ch.op == "SPLIT" and not ch.cand_id:
            if not ch.mag_id:
                raise ValueError("SPLIT: cand_id vagy mag_id kell.")
            if not need_mag(ch.mag_id, "SPLIT"):
                continue
            if not ch.new_mag:
                raise ValueError(f"SPLIT {ch.mag_id}: a 'new_mag' lista üres; add meg az új MAG-okat.")
            old = [c for c in order if c in items and items[c]["mag_id"] == ch.mag_id]
            pos = order.index(old[0]) if old else len(order)
            for c in old:
                items.pop(c)
            add_new(ch, pos, drop_mag=ch.mag_id)
            mags.pop(ch.mag_id, None)
        elif ch.op in ("SPLIT", "ADD"):
            if ch.op == "SPLIT" and not need(ch.cand_id, "SPLIT"):
                continue
            pos = order.index(ch.cand_id) + 1 if ch.op == "SPLIT" else len(order)
            if ch.op == "SPLIT":
                items.pop(ch.cand_id)
            add_new(ch, pos)
    magok = [TeszMag.model_validate(mags[m]) for m in mag_order if m in mags]
    tesz = [Tesz.model_validate(items[c]) for c in order if c in items]
    if not tesz:
        raise ValueError("A javítások után egyetlen TESZ-nivaló sem maradt.")
    magok, tesz, w2 = link_magok(magok, tesz, strict=False)
    return magok, tesz, warnings + w2


def finish_p2(ctx: Context, job: dict, p1: P1Output, obj: P2Output, flags: list) -> dict:
    magok, final, warnings = apply_p2(p1.tesz_magok, p1.tesz, obj.changes)
    flags2 = check_rules(p1, job, magok, final, torzs_size=len(ctx.mag_torzs()))
    art = obj.model_dump() | {"final_magok": [m.model_dump() for m in magok],
                              "final_tesz": [t.model_dump() for t in final],
                              "warnings": p1.link_warnings + warnings,
                              "rule_flags_before": [f.as_dict() for f in flags],
                              "rule_flags_after": [f.as_dict() for f in flags2]}
    ctx.store.save_artifact(job["job_id"], "P2", art, ctx.pv)
    ctx.store.clear_artifact(job["job_id"], "P2_ERR")
    if obj.verdict == "EXPERT" or flags2:
        ctx.store.enqueue(job["job_id"], "job_review", {
            "verdict": obj.verdict, "flags": [f.as_dict() for f in flags2],
            "questions": obj.expert_questions_hu, "gaps": obj.coverage_gaps_hu})
        ctx.store.set_status(job["job_id"], "SZAKERTORE",
                             "; ".join(f"{f.rule}: {f.message}" for f in flags2)[:500])
    else:
        ctx.store.set_status(job["job_id"], "P2_ELFOGADVA")
    return art


def run_p2(ctx: Context, job: dict, p1: P1Output) -> dict:
    user, flags = build_p2(ctx, job, p1)
    try:
        obj, _ = call_json(ctx.llm("P2"), ctx.system, user, P2Output,
                           log=ctx.logger(job["job_id"], "P2", ctx.settings.p2),
                           check=lambda o: apply_p2(p1.tesz_magok, p1.tesz, o.changes))
    except LLMError as exc:
        record_error(ctx, job["job_id"], "P2", str(exc), "P1_KESZ")
        raise
    return finish_p2(ctx, job, p1, obj, flags)


def load_p1(ctx: Context, job_id: str) -> P1Output:
    return P1Output.model_validate(ctx.store.artifact(job_id, "P1"))


# ------------------------------------------------------------------ P3 + gráf
def final_lists(p1: P1Output | dict, p2_art: dict | None) -> tuple[list[TeszMag], list[Tesz]]:
    """A végleges MAG- és változatlista a P2-eredményből (régi eredménynél egyelemű MAG-okkal)."""
    if p2_art and p2_art.get("final_tesz"):
        magok = [TeszMag.model_validate(m) for m in p2_art.get("final_magok") or []]
        tesz = [Tesz.model_validate(t) for t in p2_art["final_tesz"]]
    else:
        p1 = p1 if isinstance(p1, P1Output) else P1Output.model_validate(p1)
        magok, tesz = p1.tesz_magok, p1.tesz
    magok, tesz, _ = link_magok(list(magok), list(tesz), strict=False)
    return magok, tesz


def merge_plan(ctx: Context, job: dict, p1_meta: dict, magok: list[TeszMag], final: list[Tesz]) -> tuple:
    """Egyesítés (csak olvas): (WritePlan, összefoglaló, Resolver). A P3-hívás itt fut."""
    import time

    ctx.current_job = job["job_id"]
    started = time.monotonic()
    res = Resolver(ctx)
    labels = [(m.label_hu, "hu") for m in magok] + [(m.label_en, "en") for m in magok]
    res.prefetch_aliases("TESZ_MAG", labels)
    szers = [m.primary_szer for m in magok] + [s for t in final for s in t.secondary_szer]
    res.prefetch_aliases("SZER_ELEM", [(s.label_hu, "hu") for s in szers] + [(s.label_en, "en") for s in szers])
    jid = res.resolve_job(job, p1_meta)
    # 1. gyors út minden elemre, a kétes esetek gyűjtése
    for s in szers:
        res.plan_szer(s)
    planned = {m.mag_id: res.plan_mag(m) for m in magok}
    # 2. egyetlen P3-hívás a kétes esetekre (MAG és SZER-ELEM)
    planned_at = time.monotonic()
    res.decide()
    decided_at = time.monotonic()
    # 3. végleges döntések és az írandók összegyűjtése
    plan = res.plan
    summary = {"job_id": jid, "magok": [], "tesz": [], "review": [], "p3_cases": len(res.cases)}
    mag_ids: dict[str, str] = {}
    for m in magok:
        p = res.szer(m.primary_szer)
        rm = res.mag(m, planned[m.mag_id], p.entity_id)
        mag_ids[m.mag_id] = rm.entity_id
        new = rm.decision not in ("ALIAS", "SAME")
        entry = plan.magok.setdefault(rm.entity_id, {
            "props": {}, "emb": None, "new": False, "tesz_type": m.tesz_type, "primary": p.entity_id,
            "role": m.primary_szer.role, "melyites": [], "kesz_ids": []})
        entry["kesz_ids"] = list(dict.fromkeys(entry["kesz_ids"] + m.kesz_ids))
        if new and not entry["new"]:
            entry["new"] = True
            entry["props"] = {"label_hu": m.label_hu, "label_en": m.label_en, "outcome_hu": m.outcome_hu,
                              "status": "AI_ELLENORZOTT", "prompt_version": ctx.pv, "gen": vocab().get("version", "")}
            if ctx.embedder.enabled:
                entry["emb"] = res.encode(res.mag_text(m))
        entry["melyites"] += [x.model_dump() for x in m.melyites_jeloltek]
        plan.alias("TESZ_MAG", rm.entity_id, [(m.label_hu, "hu"), (m.label_en, "en")] + rm.extra_aliases, rm.via)
        summary["magok"].append({"mag_id": m.mag_id, "graph_id": rm.entity_id, "decision": rm.decision,
                                 "via": rm.via, "label_hu": m.label_hu, "tesz_type": m.tesz_type,
                                 "kesz_ids": m.kesz_ids})
        if rm.needs_review or p.needs_review:
            summary["review"].append({"kind": "mag", "label": m.label_hu, "id": rm.entity_id})
    existing = set()
    tids = [res.tesz_id(mag_ids[t.mag_id], t) for t in final]
    if hasattr(ctx.graph, "existing_ids"):
        existing = ctx.graph.existing_ids("TESZ", tids)
    for t, tid in zip(final, tids):
        gm = mag_ids[t.mag_id]
        secondary = []
        for s in t.secondary_szer:
            rs = res.szer(s)
            secondary.append({"id": rs.entity_id, "role": s.role,
                              "kozeg": s.kozeg or t.kozeg, "helyzet": s.helyzet or t.helyzet})
            if rs.needs_review:
                summary["review"].append({"kind": "szer", "label": s.label_hu, "id": rs.entity_id})
        decision = "SAME" if tid in existing or tid in plan.tesz else "NEW"
        if tid not in plan.tesz:
            plan.tesz[tid] = {
                "mag": gm, "szin": f"{t.kozeg}|{t.helyzet}",
                "props": {"label_hu": t.label_hu, "label_en": t.label_en, "alap_szin": t.alap_szin,
                          "szin_kulonbseg_hu": t.szin_kulonbseg_hu or "", "status": "AI_ELLENORZOTT",
                          "prompt_version": ctx.pv},
                "kozeg": [t.kozeg] + [k for k in t.kozeg_secondary if k != t.kozeg],
                "helyzet": [t.helyzet] + ([t.helyzet_secondary] if t.helyzet_secondary
                                          and t.helyzet_secondary != t.helyzet else []),
                "secondary": secondary}
        plan.alias("TESZ", tid, [(t.label_hu, "hu"), (t.label_en, "en")], "szin")
        plan.links.append({"t": tid, "props": {
            "relevance": t.relevance, "expected_eqf": t.expected_eqf, "frequency": t.frequency,
            "kor_id": t.kor_id, "cand_id": t.cand_id, "mag_cand_id": t.mag_id, "alap_szin": t.alap_szin,
            "kontextus_hu": t.kontextus_hu or "",
            "confidence": t.confidence, "prompt_version": ctx.pv}})
        summary["tesz"].append({"cand_id": t.cand_id, "mag_id": t.mag_id, "tesz_id": tid, "mag_graph_id": gm,
                                "decision": decision, "via": "szin"})
    summary["timings"] = {"planning_seconds": round(planned_at - started, 3),
                          "decision_seconds": round(decided_at - planned_at, 3), **res.timings}
    return plan, summary, res


def write_graph(ctx: Context, job: dict, p1_meta: dict, magok: list[TeszMag], final: list[Tesz]) -> dict:
    if ctx.graph is None:
        raise RuntimeError("A gráfba íráshoz neo4j-kapcsolat kell.")
    import time

    plan, summary, res = merge_plan(ctx, job, p1_meta, magok, final)
    t0 = time.monotonic()
    counts = ctx.graph.write_plan(plan)
    ctx.store.alias_add_many(plan.alias_index)
    ctx._torzs = None  # a következő munkakör P1-e már az új MAG-okat is látja a törzsben
    summary["timings"]["writing_seconds"] = round(time.monotonic() - t0, 3)
    summary["written"] = counts
    if summary["review"]:
        ctx.store.enqueue(job["job_id"], "merge_review", {"items": summary["review"]})
    print(f"P3 {job['job_id']}: {len(magok)} MAG, {len(final)} változat, {summary['p3_cases']} kétes eset; "
          f"idők (mp): {summary['timings']}", flush=True)
    ctx.store.save_artifact(job["job_id"], "P3", summary, ctx.pv)
    ctx.store.set_status(job["job_id"], "GRAFBAN")
    ctx.store.clear_artifact(job["job_id"], "P3_ERR")
    return summary


# ------------------------------------------------------------------ P5: tanúsítási szöveg
def run_p5(ctx: Context, limit: int = 1000, chunk: int = 6, log=print) -> dict:
    """MAG-rubrika (rubric_alap_hu) és változatonként igazolási mondat + SZÍN-rubrika (rubric_szin_hu)
    a gráf azon elemeihez, amelyeknek még nincs (egyszer, nem munkakörönként). chunk: MAG-ok hívásonként."""
    rows = ctx.graph.p5_todo(limit)
    stat = {"mag": 0, "valtozat": 0, "kesz_mag": 0, "kesz_valtozat": 0, "hiba": 0}
    for i in range(0, len(rows), chunk):
        part = []
        for r in rows[i:i + chunk]:
            need_alap = not r.get("rubric_alap_hu")
            item = {"id": r["id"], "label_hu": r["label_hu"], "outcome_hu": r["outcome_hu"],
                    "tesz_type": r["tesz_type"], "primary_szer": r["primary_szer"], "kell_alap": need_alap}
            if not need_alap:
                item["rubric_alap_hu"] = r["rubric_alap_hu"]
            item["valtozatok"] = [{k: v for k, v in x.items() if k != "kesz" and v not in (None, "")}
                                  for x in r["valtozatok"] if not x["kesz"]]
            part.append(item)
        want_m = {x["id"] for x in part if x["kell_alap"]}
        want_v = {v["id"] for x in part for v in x["valtozatok"]}
        stat["mag"] += len(want_m)
        stat["valtozat"] += len(want_v)
        user = render(prompt("p5_tanusitas"), tesz_list_json=_json(part))

        def check(o: P5MagOutput):
            missing = (want_m - {x.id for x in o.magok}) | (want_v - {x.id for x in o.valtozatok})
            if missing:
                raise ValueError(f"Hiányzó id-k: {sorted(missing)}")

        try:
            obj, _ = call_json(ctx.llm("P5"), ctx.system, user, P5MagOutput,
                               log=ctx.logger(None, "P5", ctx.settings.p5), check=check)
        except LLMError as exc:
            stat["hiba"] += len(want_m) + len(want_v)
            log(f"P5 hiba ({len(part)} MAG): {exc}")
            continue
        mrows = [x.model_dump() for x in obj.magok if x.id in want_m]
        vrows = [x.model_dump() for x in obj.valtozatok if x.id in want_v]
        ctx.graph.set_certificates(mrows, vrows, ctx.pv)
        stat["kesz_mag"] += len(mrows)
        stat["kesz_valtozat"] += len(vrows)
        log(f"P5: MAG {stat['kesz_mag']}/{stat['mag']}, változat {stat['kesz_valtozat']}/{stat['valtozat']}")
    return stat


# ------------------------------------------------------------------ vezérlés
def _write_manual(job_id: str, ms: ManualStep) -> Path:
    d = MANUAL_DIR / job_id.replace(":", "_")
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{ms.step}_system.md").write_text(ms.system, encoding="utf-8")
    (d / f"{ms.step}_user.md").write_text(ms.user, encoding="utf-8")
    return d


def process_job(ctx: Context, job_id: str, until: str = "graph", force: bool = False) -> str:
    """Egy munkakör végigvitele. Visszaadja az új állapotot (vagy 'MANUAL:<mappa>')."""
    job = ctx.store.job(job_id)
    status = ctx.store.status(job_id)
    if (status in ("GRAFBAN", "ELVETVE") or status.endswith("_KOTEGBEN")) and not force:
        return status
    ctx.current_job = job_id
    try:
        p1_data = ctx.store.artifact(job_id, "P1")
        fresh = force or not p1_data or status == "NYERS"
        p1 = run_p1(ctx, job) if fresh else P1Output.model_validate(p1_data)
        if until == "p1":
            return ctx.store.status(job_id)
        status = ctx.store.status(job_id)
        p2_art = ctx.store.artifact(job_id, "P2")
        if not (status in ("P2_ELFOGADVA", "SZAKERTORE") and p2_art and (fresh or not force)):
            p2_art = run_p2(ctx, job, p1)
        if until == "p2" or ctx.store.status(job_id) == "SZAKERTORE":
            return ctx.store.status(job_id)
        magok, final = final_lists(p1, p2_art)
        write_graph(ctx, job, p1.model_dump(), magok, final)
        return ctx.store.status(job_id)
    except ManualStep as ms:
        d = _write_manual(job_id, ms)
        return f"MANUAL:{d}"


def approve_job(ctx: Context, queue_id: int, final_override: dict | None = None) -> dict:
    """Szakértői jóváhagyás: a (szerkesztett) végső lista a gráfba kerül.

    final_override: {"final_magok": [...], "final_tesz": [...]} (régi alak: csak a változatlista)."""
    row = next(r for r in ctx.store.queue() if r["id"] == queue_id)
    job = ctx.store.job(row["job_id"])
    p1 = ctx.store.artifact(row["job_id"], "P1") or {}
    p2 = ctx.store.artifact(row["job_id"], "P2") or {}
    if isinstance(final_override, list):
        final_override = {"final_tesz": final_override, "final_magok": p2.get("final_magok", [])}
    magok, final = final_lists(p1, final_override or p2)
    summary = write_graph(ctx, job, p1, magok, final)
    ctx.store.decide(queue_id, "JOVAHAGYVA", {"final_override": bool(final_override)})
    return summary


# ------------------------------------------------------------------ próbafutás (dry run)
DRYRUN_DIR = ROOT / "runs" / "dryrun"


def dry_run(job: dict, s: Settings | None = None, graph=None, llm_override: dict | None = None,
            out_dir: Path | None = None, extra_torzs: list[dict] | None = None) -> dict:
    """Egy munkakör próbafutása: P1 -> (P2, ha a szabályok vagy a minta szerint kell) -> egyesítés csak
    olvasva (a P3-hívás lefut). Sem a gráfot, sem a state.sqlite-ot nem írja; a napló és a köztes
    eredmények memóriában élnek. Kimenet: runs/dryrun/<job>.json."""
    import time

    from .report import with_cost

    s = s or settings()
    mem = Settings(**{**s.__dict__, "state_db": Path(":memory:")})
    ctx = Context(mem, with_graph=False, llm_override=llm_override)
    ctx.graph = graph
    ctx.extra_torzs = list(extra_torzs or [])
    ctx.store.upsert_job(job)
    jid = job["job_id"]
    started = time.monotonic()
    out: dict = {"job_id": jid, "title": job.get("title", ""), "prompt_version": ctx.pv, "dry_run": True}
    try:
        p1 = run_p1(ctx, job)
        p2_art = ctx.store.artifact(jid, "P2") if ctx.store.status(jid) != "P1_KESZ" else run_p2(ctx, job, p1)
        out["p2"] = {"verdict": p2_art.get("verdict"), "reason": p2_art.get("p2_reason", ""),
                     "changes": p2_art.get("changes", []), "warnings": p2_art.get("warnings", [])}
        magok, final = final_lists(p1, p2_art)
        out["rule_flags_p1"] = p2_art.get("rule_flags_before", [])
        out["rule_flags"] = p2_art.get("rule_flags_after", [])
        merge = None
        if graph is not None:
            _, summary, _ = merge_plan(ctx, job, p1.model_dump(), magok, final)
            merge = {m["mag_id"]: m for m in summary["magok"]}
            tmerge = {t["cand_id"]: t for t in summary["tesz"]}
            out["p3_cases"] = summary["p3_cases"]
        out["magok"] = []
        for m in magok:
            vs = [t for t in final if t.mag_id == m.mag_id]
            out["magok"].append({
                "mag_id": m.mag_id, "kor_id": m.kor_id, "label_hu": m.label_hu, "tesz_type": m.tesz_type,
                "primary_szer": m.primary_szer.label_hu, "relevance": m.relevance,
                "melyites_jeloltek": [x.model_dump() for x in m.melyites_jeloltek],
                "mag_ref": m.mag_ref, "kesz_ids": m.kesz_ids, "kontextus_hu": m.kontextus_hu,
                "egyesites": (merge or {}).get(m.mag_id, {}).get("decision"),
                "valtozatok": [{"cand_id": t.cand_id, "alap_szin": t.alap_szin, "label_hu": t.label_hu,
                                "kozeg": t.kozeg, "helyzet": t.helyzet, "szin_kulonbseg_hu": t.szin_kulonbseg_hu,
                                "frequency": t.frequency, "expected_eqf": t.expected_eqf,
                                "egyesites": (tmerge or {}).get(t.cand_id, {}).get("decision") if merge else None}
                               for t in vs]})
        out["szamok"] = {"mag": len(magok), "tesz": len(final),
                         "core_mag": sum(m.relevance == "CORE" for m in magok),
                         "nem_alap": sum(not t.alap_szin for t in final),
                         "melyitesi_jelolt": sum(len(m.melyites_jeloltek) for m in magok),
                         "torzsbol": sum(bool(m.mag_ref) for m in magok),
                         "torzs_meret": len(ctx.mag_torzs())}
        # próbafutás-lánchoz: ennek a munkakörnek az új MAG-jai a következő törzsébe kerülhetnek
        out["new_torzs"] = [{"id": f"dry:{jid}:{m.mag_id}", "label_hu": m.label_hu, "label_en": m.label_en,
                             "outcome_hu": m.outcome_hu, "tesz_type": m.tesz_type,
                             "primary_szer": m.primary_szer.model_dump(), "kesz_ids": m.kesz_ids}
                            for m in magok if not m.mag_ref]
        out["ok"] = True
    except Exception as exc:  # a próbafutás eredménye a hiba is
        out["ok"] = False
        out["error"] = str(exc)[:3000]
    out["failed_calls"] = [{"step": r["step"], "raw": (r["raw_text"] or "")[:800]} for r in ctx.store.db.execute(
        "SELECT step, raw_text FROM llm_log WHERE parsed_ok = 0 ORDER BY id")]
    usage = with_cost(ctx.store.usage())
    out["usage"] = [{k: r[k] for k in ("step", "model", "hivas", "sikeres", "input", "cache_read", "cache_write",
                                       "output", "ido_mp", "koltseg_usd")} for r in usage]
    out["total"] = {"input": sum(r["input"] + r["cache_read"] + r["cache_write"] for r in usage),
                    "output": sum(r["output"] for r in usage),
                    "koltseg_usd": round(sum(r["koltseg_usd"] or 0 for r in usage), 4),
                    "ido_mp": round(time.monotonic() - started, 1)}
    d = out_dir or DRYRUN_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{jid.replace(':', '_').replace('/', '_')}.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    ctx.store.db.close()
    return out


def format_dry_run(out: dict) -> str:
    """A próbafutás eredménye olvasható táblázatként (CLI)."""
    lines = [f"PRÓBAFUTÁS {out['job_id']} – {out.get('title', '')}  (prompt {out['prompt_version']}; "
             "a gráf és a state.sqlite nem változott)"]
    if not out.get("ok"):
        lines.append(f"HIBA: {out.get('error', '')}")
    else:
        n = out["szamok"]
        lines.append(f"MAG: {n['mag']} (CORE {n['core_mag']}, törzsből {n.get('torzsbol', 0)}/{n.get('torzs_meret', 0)})"
                     f" · TESZ-nivaló: {n['tesz']} (nem ALAP-SZÍN: "
                     f"{n['nem_alap']}) · mélyítési jelölt: {n['melyitesi_jelolt']} · P2: {out['p2']['verdict']}"
                     + (f" ({out['p2']['reason']})" if out["p2"].get("reason") else "")
                     + (f" · P3 kétes eset: {out['p3_cases']}" if "p3_cases" in out else ""))
        for m in out["magok"]:
            eg = f" [{m['egyesites']}]" if m.get("egyesites") else ""
            ref = " [törzs]" if m.get("mag_ref") else ""
            lines.append(f"  {m['mag_id']} {m['tesz_type']:<15} {m['relevance']:<10} {m['label_hu']}"
                         f"  ‹{m['primary_szer']}›{ref}{eg}  KÉSZ: {','.join(k[5:] for k in m.get('kesz_ids') or [])}"
                         + (f"  ({m['kontextus_hu']})" if m.get("kontextus_hu") else ""))
            for v in m["valtozatok"]:
                mark = "●" if v["alap_szin"] else "○"
                extra = "" if v["alap_szin"] else f" – {v.get('szin_kulonbseg_hu') or ''}"
                lines.append(f"      {mark} {v['cand_id']} {v['kozeg']}/{v['helyzet']} EQF {v['expected_eqf']}{extra}")
            for x in m["melyites_jeloltek"]:
                lines.append(f"      ◇ mélyítési jelölt: {x['kozeg']}/{x['helyzet']} {x.get('indok_hu', '')}")
        flags = out.get("rule_flags") or []
        lines.append("Szabályjelzések: " + ("; ".join(f"{f['rule']} {f['message']}" for f in flags) or "nincs"))
    for u in out.get("usage", []):
        lines.append(f"  {u['step']} {u['model']}: {u['hivas']} hívás, be {u['input']} (+cache {u['cache_read']}"
                     f"/{u['cache_write']}), ki {u['output']} token, {u['ido_mp']:.1f} mp, "
                     f"{'?' if u['koltseg_usd'] is None else '$' + format(u['koltseg_usd'], '.4f')}")
    t = out.get("total", {})
    lines.append(f"Összesen: be {t.get('input')} / ki {t.get('output')} token, ${t.get('koltseg_usd')}, "
                 f"{t.get('ido_mp')} mp")
    return "\n".join(lines)


# ------------------------------------------------------------------ P4a
ANCHOR_QUERIES = {
    "KOZEG": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:KOZEGBEN_ZAJLIK {primary: true}]->(:Kozeg {id: $v})",
    "HELYZET": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:HELYZETBEN_VEGZENDO {primary: true}]->(:Helyzet {id: $v})",
    "TESZ_TIPUS": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:MAGJA]->(:TeszMag)-[:TIPUSA]->(:TeszTipus {id: $v})",
    "SZER_ELEM": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo) "
                 "WHERE (t)-[:HASZNAL]->(:SzerElem {id: $v}) OR (t)-[:MAGJA]->(:TeszMag)-[:IRANYUL]->(:SzerElem {id: $v})",
    "SZIN": "MATCH (m:MunkaKor)-[:TARTALMAZ_TESZT]->(t:TeszNivalo)-[:SZINBEN]->(:Szin {id: $v})",
}


def run_p4a(ctx: Context, anchor_type: str, anchor_value: str, runs: int = 3, limit: int = 40) -> dict:
    q = ANCHOR_QUERIES[anchor_type] + (
        " WITH t, collect(DISTINCT m.label_hu) AS jobs "
        "MATCH (t)-[:MAGJA]->(g:TeszMag), (t)-[:SZINBEN]->(sz:Szin) "
        "OPTIONAL MATCH (g)-[:IRANYUL]->(s:SzerElem) OPTIONAL MATCH (g)-[:TIPUSA]->(ty) "
        "RETURN t.id AS id, t.label_hu AS label_hu, g.outcome_hu AS outcome_hu, ty.id AS tesz_type, "
        "s.label_hu AS primary_szer, sz.kozeg AS kozeg, sz.helyzet AS helyzet, jobs LIMIT $limit")
    tesz = ctx.graph.run(q, v=anchor_value, limit=limit)
    if not tesz:
        raise ValueError("Ehhez a horgonyhoz nincs TESZ-nivaló a gráfban.")
    existing = ctx.graph.fulltext_candidates("KESZSEG", anchor_value.replace("_", " "), k=10)
    user = render(prompt("p4a_keszseg"), anchor_type=anchor_type, anchor_value=anchor_value,
                  anchor_tesz_list_json=json.dumps(tesz, ensure_ascii=False),
                  skill_candidates_json=json.dumps(existing, ensure_ascii=False, default=str),
                  skill_mentions_json="[]")
    seen: dict[str, dict] = {}
    for _ in range(runs):
        obj, _ = call_json(ctx.llm("P4"), ctx.system, user, P4aOutput,
                           log=ctx.logger(None, "P4a", ctx.settings.p4))
        for sk in obj.skills:
            key = norm_key(sk.label_hu, "hu")
            entry = seen.setdefault(key, {"skill": sk, "hits": 0})
            entry["hits"] += 1
    n = len(tesz)
    written = []
    for key, e in seen.items():
        sk = e["skill"]
        share = len(set(sk.covered_tesz_ids) & {t["id"] for t in tesz}) / n
        # 2. kapu (részben): ismétlődés a futások között + lefedettség a horgony TESZ-ein
        status = "ELLENORZOTT" if e["hits"] >= math.ceil(2 * runs / 3) and share >= 0.7 else "JELOLT"
        sid = sk.skill_id or stable_id("gh_skill", sk.label_hu)
        ctx.graph.run("MERGE (k:Keszseg {id: $id}) ON CREATE SET k.label_hu = $hu, k.label_en = $en, "
                      "k.definition_hu = $d, k.kep_elemek = $kep, k.tud_elemek = $tud, k.status = $st "
                      "SET k.viselkedes_hu = $vis",
                      id=sid, hu=sk.label_hu, en=sk.label_en, d=sk.definition_hu, kep=sk.kep_elemek,
                      tud=sk.tud_elemek, st=status, vis=sk.viselkedes_hu)
        ctx.graph.add_aliases("KESZSEG", sid, [(sk.label_hu, "hu"), (sk.label_en, "en")])
        props = {"erosseg": sk.erosseg, "aspects": ["|".join(a) for a in sk.aspects],
                 "covered_share": round(share, 2), "runs_hit": e["hits"], "runs": runs, "status": status}
        if anchor_type == "SZIN":
            # A KÉSZ-ség-aspektus és a TESZ-nivaló ugyanahhoz a SZÍN-csomóponthoz köt.
            ctx.graph.run("MATCH (k:Keszseg {id: $id}), (s:Szin {id: $v}) "
                          "MERGE (k)-[h:HORGONYOZ]->(s) SET h += $p MERGE (k)-[:ASPEKTUSA]->(s)",
                          id=sid, v=anchor_value, p=props)
        else:
            lbl = {"KOZEG": "Kozeg", "HELYZET": "Helyzet", "TESZ_TIPUS": "TeszTipus", "SZER_ELEM": "SzerElem"}[anchor_type]
            ctx.graph.run(f"MATCH (k:Keszseg {{id: $id}}), (a:{lbl} {{id: $v}}) "
                          "MERGE (k)-[h:HORGONYOZ]->(a) SET h += $p", id=sid, v=anchor_value, p=props)
        written.append({"id": sid, "label_hu": sk.label_hu, "status": status, "share": round(share, 2),
                        "hits": e["hits"]})
    return {"anchor": f"{anchor_type}={anchor_value}", "tesz_db": n, "skills": written}
