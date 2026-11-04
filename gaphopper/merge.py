"""P3 – egyesítés két lépcsőben: MAG-egyezés (LLM a kétes sávban), SZÍN-egyezés (determinisztikus).

1. SZER-ELEM és TESZ-MAG: alias-találat -> hasonlósági keresés -> küszöbök -> egy P3-hívás a kétes
   esetekre munkakörönként. A MAG-egyezésben a KÖZ-eg és a HELY-zet nem számít.
2. TESZ-nivaló (változat): a kilét stable_id(gh_tesz, mag_id, kozeg, helyzet); ha a MAG-on már van
   ilyen SZÍN, SAME, különben új változat ugyanazon MAG alatt. Ehhez nem kell LLM.

A Resolver semmit nem ír: az írandókat összegyűjti (WritePlan), a pipeline egy tranzakcióban,
UNWIND-del kötegelve írja ki (próbafutásnál pedig sehogy).
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .config import prompt, render
from .llm import ManualLLM, call_json
from .normalize import detect_lang, norm_key, stable_id
from .schemas import P3Decision, P3Output, SzerElem, Tesz, TeszMag


@dataclass
class Resolution:
    entity_id: str
    decision: str          # ALIAS | SAME | NEW | NARROWER | BROADER
    target_id: str | None = None
    via: str = ""          # alias | auto | llm | manual | szin
    needs_review: bool = False
    extra_aliases: list = field(default_factory=list)


@dataclass
class _Case:
    case_id: str
    entity_type: str
    candidate: dict
    matches: list[dict]


@dataclass
class WritePlan:
    """Az egyesítés eredménye írásra készen; a graph.write_plan() egy tranzakcióban írja."""
    job: dict = field(default_factory=dict)                  # {id, props}
    szer: dict[str, dict] = field(default_factory=dict)      # új SZER-ELEM: id -> props (+embedding)
    szer_broader: list[dict] = field(default_factory=list)   # {a, b}
    magok: dict[str, dict] = field(default_factory=dict)     # TESZ-MAG: id -> {props, new, tesz_type, primary, melyites}
    tesz: dict[str, dict] = field(default_factory=dict)      # TESZ-nivaló: id -> {...}
    links: list[dict] = field(default_factory=list)          # MunkaKor -> TeszNivalo
    aliases: dict[tuple[str, str], list] = field(default_factory=dict)  # (típus, id) -> [(szöveg, nyelv)]
    alias_index: list[tuple] = field(default_factory=list)   # SQLite alias-index sorai

    def alias(self, entity_type: str, entity_id: str, labels: list[tuple[str, str]], source: str) -> None:
        clean = [(t, l) for t, l in labels if t and t.strip()]
        lst = self.aliases.setdefault((entity_type, entity_id), [])
        for text, lang in clean:
            if (text, lang) not in lst:
                lst.append((text, lang))
            self.alias_index.append((norm_key(text, lang), lang, text, entity_type, entity_id, source))


class Resolver:
    def __init__(self, ctx):
        self.ctx = ctx  # pipeline.Context
        self._embeddings: dict[str, list[float]] = {}
        self._contexts: dict[str, dict] = {}
        self._alias_cache: dict[tuple[str, str], str | None] = {}
        self.timings: dict[str, float] = {}
        self.cases: list[_Case] = []
        self.decisions: dict[str, P3Decision | None] = {}
        self._szer: dict[str, Resolution | str] = {}   # kulcs -> döntés vagy függő case_id
        self._szer_obj: dict[str, SzerElem] = {}
        self.plan = WritePlan()

    def encode(self, text: str) -> list[float]:
        # Munkakörönkénti gyorsítótár: a keresés vektora íráskor újra felhasználható.
        if text not in self._embeddings:
            self._embeddings[text] = self.ctx.embedder.encode(text)
        return self._embeddings[text]

    def _context(self, entity_id: str) -> dict:
        if entity_id not in self._contexts:
            self._contexts[entity_id] = self.ctx.graph.mag_context(entity_id)
        return self._contexts[entity_id]

    # ---------------------------------------------------------------- közös
    def prefetch_aliases(self, entity_type: str, labels: list[tuple[str, str]]) -> None:
        """Egy gráflekérdezés a munkakör összes nevére (soronkénti keresés helyett)."""
        keys = sorted({norm_key(t, l) for t, l in labels if t and t.strip()})
        todo = [k for k in keys if (entity_type, k) not in self._alias_cache]
        if not todo:
            return
        found = self.ctx.graph.find_by_aliases(entity_type, todo) if hasattr(self.ctx.graph, "find_by_aliases") else {}
        for k in todo:
            self._alias_cache[(entity_type, k)] = found.get(k)

    def _alias_hit(self, entity_type: str, labels: list[tuple[str, str]]) -> str | None:
        for text, lang in labels:
            if not text:
                continue
            key = norm_key(text, lang)
            hit = self.ctx.store.alias_lookup(key, entity_type)
            if hit:
                return hit
            ck = (entity_type, key)
            if ck not in self._alias_cache:
                self._alias_cache[ck] = self.ctx.graph.find_by_alias(entity_type, key)
            if self._alias_cache[ck]:
                return self._alias_cache[ck]
        return None

    def _candidates(self, entity_type: str, text: str) -> tuple[list[dict], bool]:
        """(jelöltek, vektoros-e). Beágyazás nélkül teljes szöveges index."""
        emb = self.ctx.embedder
        if emb.enabled:
            return self.ctx.graph.vector_candidates(entity_type, self.encode(text)), True
        return self.ctx.graph.fulltext_candidates(entity_type, text), False

    def _add_case(self, prefix: str, entity_type: str, candidate: dict, matches: list[dict]) -> str:
        cid = f"{prefix}{sum(1 for c in self.cases if c.case_id.startswith(prefix)) + 1}"
        self.cases.append(_Case(cid, entity_type, candidate, matches))
        return cid

    def decide(self) -> None:
        """A gyűjtött kétes esetek eldöntése egyetlen P3-hívással."""
        pending = [c for c in self.cases if c.case_id not in self.decisions]
        if not pending:
            return
        llm = self.ctx.llm("P3")
        if isinstance(llm, ManualLLM):
            for c in pending:
                self.decisions[c.case_id] = None  # kézi módban a kétes eset szakértőhöz megy
            return
        cases = [{"case_id": c.case_id, "type": c.entity_type, "candidate": c.candidate,
                  "matches": c.matches} for c in pending]
        user = render(prompt("p3_egyesites"),
                      cases_json=json.dumps(cases, ensure_ascii=False, default=str, separators=(",", ":")))
        started = time.monotonic()
        obj, _ = call_json(llm, prompt("p3_system"), user, P3Output,
                           log=self.ctx.logger(self.ctx.current_job, "P3", self.ctx.settings.p3))
        self.timings["model_seconds"] = round(time.monotonic() - started, 3)
        by_id = {d.case_id: d for d in obj.decisions}
        for c in pending:
            self.decisions[c.case_id] = by_id.get(c.case_id)

    # ---------------------------------------------------------------- SZER-ELEM
    @staticmethod
    def _szer_key(s: SzerElem) -> str:
        return f"{norm_key(s.label_hu, 'hu')}|{s.kind}"

    def plan_szer(self, s: SzerElem) -> None:
        key = self._szer_key(s)
        if key in self._szer:
            return
        self._szer_obj[key] = s
        labels = [(s.label_hu, "hu"), (s.label_en, "en")]
        hit = self._alias_hit("SZER_ELEM", labels)
        if hit:
            self._szer[key] = Resolution(hit, "ALIAS", hit, "alias")
            return
        cands, is_vec = self._candidates("SZER_ELEM", f"{s.label_hu} | {s.label_en} | {s.definition_hu}")
        new_id = stable_id("gh_szer", s.label_hu, s.kind)
        if not cands or (is_vec and cands[0]["score"] < self.ctx.settings.sim_lo):
            self._szer[key] = self._new_szer(new_id, s, "auto")
            return
        top = cands[0]
        if is_vec and top["score"] >= self.ctx.settings.sim_hi and top["props"].get("kind") == s.kind:
            self.plan.alias("SZER_ELEM", top["props"]["id"], labels, "auto")
            self._szer[key] = Resolution(top["props"]["id"], "SAME", top["props"]["id"], "auto")
            return
        slim = [{k: c["props"].get(k) for k in ("id", "label_hu", "kind", "definition_hu")}
                | {"score": round(c["score"], 3)} for c in cands[:5]]
        self._szer[key] = self._add_case("S", "SZER_ELEM", s.model_dump(include={
            "label_hu", "label_en", "kind", "definition_hu"}), slim)

    def szer(self, s: SzerElem) -> Resolution:
        """A SZER-ELEM végleges feloldása (a decide() után)."""
        key = self._szer_key(s)
        r = self._szer[key]
        if isinstance(r, Resolution):
            return r
        ans = self.decisions.get(r)
        s = self._szer_obj[key]
        labels = [(s.label_hu, "hu"), (s.label_en, "en")]
        new_id = stable_id("gh_szer", s.label_hu, s.kind)
        if ans is None:
            res = self._new_szer(new_id, s, "manual")
            res.needs_review = True
        elif ans.decision in ("SAME", "BROADER") and ans.target_id:
            self.plan.alias("SZER_ELEM", ans.target_id, labels + [(a.text, a.lang) for a in ans.new_aliases], "llm")
            res = Resolution(ans.target_id, ans.decision, ans.target_id, "llm", ans.confidence < 0.6)
        else:
            res = self._new_szer(new_id, s, "llm")
            if ans.decision == "NARROWER" and ans.target_id:
                self.plan.szer_broader.append({"a": new_id, "b": ans.target_id})
                res.decision, res.target_id = "NARROWER", ans.target_id
            res.needs_review = ans.confidence < 0.6
        self._szer[key] = res
        return res

    def _new_szer(self, sid: str, s: SzerElem, via: str) -> Resolution:
        props = {"label_hu": s.label_hu, "label_en": s.label_en, "kind": s.kind,
                 "definition_hu": s.definition_hu, "status": "JAVASOLT"}
        if self.ctx.embedder.enabled:
            props["embedding"] = self.encode(f"{s.label_hu} | {s.label_en} | {s.definition_hu}")
        self.plan.szer.setdefault(sid, props)
        self.plan.alias("SZER_ELEM", sid, [(s.label_hu, "hu"), (s.label_en, "en")], via)
        return Resolution(sid, "NEW", None, via)

    # ---------------------------------------------------------------- TESZ-MAG
    @staticmethod
    def mag_text(m: TeszMag) -> str:
        return f"{m.label_hu} | {m.label_en} | {m.outcome_hu}"

    def plan_mag(self, m: TeszMag) -> Resolution | str | None:
        """Gyors út. Visszaad: Resolution (eldőlt), case_id (LLM dönt) vagy None (új MAG).

        A MAG-törzsből választott MAG (mag_ref) hívás nélkül feloldódik."""
        if m.mag_ref:
            return Resolution(m.mag_ref, "SAME", m.mag_ref, "torzs")
        labels = [(m.label_hu, "hu"), (m.label_en, "en")]
        hit = self._alias_hit("TESZ_MAG", labels)
        if hit:
            return Resolution(hit, "ALIAS", hit, "alias")
        cands, is_vec = self._candidates("TESZ_MAG", self.mag_text(m))
        if not cands or (is_vec and cands[0]["score"] < self.ctx.settings.sim_lo):
            return None
        primary = self._szer.get(self._szer_key(m.primary_szer))
        primary_id = primary.entity_id if isinstance(primary, Resolution) else None
        top = cands[0]
        if is_vec and top["score"] >= self.ctx.settings.sim_hi and primary_id:
            cx = self._context(top["props"]["id"])
            if cx.get("tesz_type") == m.tesz_type and cx.get("primary_szer_id") == primary_id:
                return Resolution(top["props"]["id"], "SAME", top["props"]["id"], "auto")
        enriched = []
        for c in cands[:5]:
            cx = self._context(c["props"]["id"])
            enriched.append({"id": c["props"]["id"], "label_hu": c["props"].get("label_hu"),
                             "outcome_hu": c["props"].get("outcome_hu"), "tesz_type": cx.get("tesz_type"),
                             "primary_szer": cx.get("primary_szer_label"), "score": round(c["score"], 3)})
        cand = m.model_dump(include={"label_hu", "outcome_hu", "tesz_type"}) | {"primary_szer": m.primary_szer.label_hu}
        return self._add_case("M", "TESZ_MAG", cand, enriched)

    def mag(self, m: TeszMag, planned: Resolution | str | None, primary_szer_id: str) -> Resolution:
        new_id = stable_id("gh_mag", m.tesz_type, primary_szer_id, m.label_hu)
        if isinstance(planned, Resolution):
            return planned
        if planned is None:
            return Resolution(new_id, "NEW", None, "auto")
        ans = self.decisions.get(planned)
        if ans is None:
            return Resolution(new_id, "NEW", None, "manual", needs_review=True)
        if ans.decision in ("SAME", "BROADER") and ans.target_id:
            # M1-őr: a MAG kiléte a TESZ-típus és a meghatározó SZER-ELEM; ha a törzselemé más, az LLM SAME
            # döntését nem fogadjuk el (új MAG, szakértői átnézésre).
            cx = self._context(ans.target_id)
            if cx.get("tesz_type") != m.tesz_type or (cx.get("primary_szer_id") or primary_szer_id) != primary_szer_id:
                return Resolution(new_id, "NEW", ans.target_id, "llm-elutasitva", needs_review=True)
            extra = [(a.text, a.lang) for a in ans.new_aliases]
            return Resolution(ans.target_id, "SAME", ans.target_id, "llm", ans.confidence < 0.6, extra)
        return Resolution(new_id, "NEW", None, "llm", ans.confidence < 0.6)

    # ---------------------------------------------------------------- TESZ-nivaló (SZÍN)
    @staticmethod
    def tesz_id(mag_id: str, t: Tesz) -> str:
        return stable_id("gh_tesz", mag_id, t.kozeg, t.helyzet)

    # ---------------------------------------------------------------- MUNKA-KÖR
    def resolve_job(self, job: dict, p1_meta: dict) -> str:
        """Katalógusrekordnál a job_id a kanonikus azonosító; a címek aliasként gyűlnek.

        Hirdetéseknél (job_id nélkül vagy 'ad:' előtaggal) alias-találatot keresünk.
        """
        jid = job["job_id"]
        title = job.get("title", "")
        labels = [(title, detect_lang(title)), (p1_meta.get("job_label_hu", ""), "hu"),
                  (p1_meta.get("job_label_en", ""), "en")]
        labels += [(a["text"], a["lang"]) for a in p1_meta.get("title_aliases", [])]
        if jid.startswith("ad:"):
            hit = self._alias_hit("MUNKA_KOR", labels)
            if hit:
                jid = hit
        props = {"label_hu": p1_meta.get("job_label_hu") or title, "label_en": p1_meta.get("job_label_en", ""),
                 "field": job.get("field", ""), "entry_route": job.get("entry_route", ""),
                 "source": job.get("source", ""), "purpose_hu": p1_meta.get("purpose_hu", "")}
        self.plan.job = {"id": jid, "props": props}
        self.plan.alias("MUNKA_KOR", jid, labels, job.get("source", "catalog") or "catalog")
        return jid
