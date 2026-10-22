"""P3 – egyesítés: alias-találat -> hasonlósági keresés -> küszöbök -> LLM a kétes sávban.

Munkakörönként egyetlen LLM-hívás: előbb minden SZER-ELEM és TESZ-nivaló a gyors úton dől el
(alias, vektor, küszöbök), a kétes esetek gyűjtőbe kerülnek, és egy kérésben mennek a P3-nak.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field

from .config import prompt, render
from .llm import ManualLLM, call_json
from .normalize import detect_lang, norm_key, stable_id
from .schemas import P3Decision, P3Output, SzerElem, Tesz


@dataclass
class Resolution:
    entity_id: str
    decision: str          # ALIAS | SAME | NEW | VARIANT | NARROWER | BROADER
    target_id: str | None = None
    via: str = ""          # alias | auto | llm | manual
    needs_review: bool = False
    extra_aliases: list = field(default_factory=list)


@dataclass
class _Case:
    case_id: str
    entity_type: str
    candidate: dict
    matches: list[dict]


class Resolver:
    def __init__(self, ctx):
        self.ctx = ctx  # pipeline.Context
        self._embeddings: dict[str, list[float]] = {}
        self._contexts: dict[str, dict] = {}
        self.timings: dict[str, float] = {}
        self.cases: list[_Case] = []
        self.decisions: dict[str, P3Decision | None] = {}
        self._szer: dict[str, Resolution | str] = {}   # kulcs -> döntés vagy függő case_id
        self._szer_obj: dict[str, SzerElem] = {}

    def encode(self, text: str) -> list[float]:
        # Scope to this job: reuse search vectors when writing new entities.
        if text not in self._embeddings:
            self._embeddings[text] = self.ctx.embedder.encode(text)
        return self._embeddings[text]

    def _context(self, entity_id: str) -> dict:
        if entity_id not in self._contexts:
            self._contexts[entity_id] = self.ctx.graph.tesz_context(entity_id)
        return self._contexts[entity_id]

    # ---------------------------------------------------------------- közös
    def _alias_hit(self, entity_type: str, labels: list[tuple[str, str]]) -> str | None:
        for text, lang in labels:
            if not text:
                continue
            key = norm_key(text, lang)
            hit = self.ctx.store.alias_lookup(key, entity_type) or self.ctx.graph.find_by_alias(entity_type, key)
            if hit:
                return hit
        return None

    def _remember(self, entity_type: str, entity_id: str, labels: list[tuple[str, str]], source: str) -> None:
        clean = [(t, l) for t, l in labels if t and t.strip()]
        for text, lang in clean:
            self.ctx.store.alias_add(norm_key(text, lang), lang, text, entity_type, entity_id, source)
        self.ctx.graph.add_aliases(entity_type, entity_id, clean)

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
        cases = [{"case_id": c.case_id, "entity_type": c.entity_type, "candidate": c.candidate,
                  "matches": c.matches} for c in pending]
        user = render(prompt("p3_egyesites"), cases_json=json.dumps(cases, ensure_ascii=False, default=str, separators=(",", ":")))
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
            self._remember("SZER_ELEM", top["props"]["id"], labels, "auto")
            self._szer[key] = Resolution(top["props"]["id"], "SAME", top["props"]["id"], "auto")
            return
        slim = [{k: c["props"].get(k) for k in ("id", "label_hu", "label_en", "kind", "definition_hu")}
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
            self._remember("SZER_ELEM", ans.target_id, labels + [(a.text, a.lang) for a in ans.new_aliases], "llm")
            res = Resolution(ans.target_id, ans.decision, ans.target_id, "llm", ans.confidence < 0.6)
        else:
            res = self._new_szer(new_id, s, "llm")
            if ans.decision == "NARROWER" and ans.target_id:
                self.ctx.graph.link_szer_broader(new_id, ans.target_id)
                res.decision, res.target_id = "NARROWER", ans.target_id
            res.needs_review = ans.confidence < 0.6
        self._szer[key] = res
        return res

    def _new_szer(self, sid: str, s: SzerElem, via: str) -> Resolution:
        self.ctx.graph.upsert_szer(sid, {"label_hu": s.label_hu, "label_en": s.label_en, "kind": s.kind,
                                         "definition_hu": s.definition_hu, "status": "JAVASOLT"})
        self._remember("SZER_ELEM", sid, [(s.label_hu, "hu"), (s.label_en, "en")], via)
        if self.ctx.embedder.enabled:
            self.ctx.graph.set_embedding("SZER_ELEM", sid,
                                         self.encode(f"{s.label_hu} | {s.label_en} | {s.definition_hu}"))
        return Resolution(sid, "NEW", None, via)

    # ---------------------------------------------------------------- TESZ-nivaló
    def plan_tesz(self, t: Tesz) -> Resolution | str | None:
        """Gyors út. Visszaad: Resolution (eldőlt), case_id (LLM dönt) vagy None (új elem)."""
        labels = [(t.label_hu, "hu"), (t.label_en, "en")]
        hit = self._alias_hit("TESZ", labels)
        if hit:
            return Resolution(hit, "ALIAS", hit, "alias")
        cands, is_vec = self._candidates("TESZ", f"{t.label_hu} | {t.label_en} | {t.outcome_hu}")
        if not cands or (is_vec and cands[0]["score"] < self.ctx.settings.sim_lo):
            return None
        primary = self._szer.get(self._szer_key(t.primary_szer))
        primary_id = primary.entity_id if isinstance(primary, Resolution) else None
        top = cands[0]
        if is_vec and top["score"] >= self.ctx.settings.sim_hi and primary_id:
            cx = self._context(top["props"]["id"])
            if (cx.get("tesz_type") == t.tesz_type and cx.get("primary_szer_id") == primary_id
                    and cx.get("helyzet") == t.helyzet):
                return Resolution(top["props"]["id"], "SAME", top["props"]["id"], "auto")
        enriched = []
        for c in cands[:5]:
            cx = self._context(c["props"]["id"])
            enriched.append({"id": c["props"]["id"], "label_hu": c["props"].get("label_hu"),
                             "outcome_hu": c["props"].get("outcome_hu"), "tesz_type": cx.get("tesz_type"),
                             "primary_szer": cx.get("primary_szer_label"), "helyzet": cx.get("helyzet"),
                             "score": round(c["score"], 3)})
        cand = t.model_dump(include={"label_hu", "label_en", "outcome_hu", "tesz_type", "kozeg", "helyzet"}) \
            | {"primary_szer": t.primary_szer.label_hu}
        return self._add_case("T", "TESZ", cand, enriched)

    def tesz(self, t: Tesz, planned: Resolution | str | None, primary_szer_id: str) -> Resolution:
        new_id = stable_id("gh_tesz", t.label_hu, t.tesz_type, primary_szer_id, t.helyzet)
        if isinstance(planned, Resolution):
            return planned
        if planned is None:
            return Resolution(new_id, "NEW", None, "auto")
        ans = self.decisions.get(planned)
        if ans is None:
            return Resolution(new_id, "NEW", None, "manual", needs_review=True)
        if ans.decision in ("SAME", "BROADER") and ans.target_id:
            extra = [(a.text, a.lang) for a in ans.new_aliases]
            return Resolution(ans.target_id, "SAME", ans.target_id, "llm", ans.confidence < 0.6, extra)
        if ans.decision == "VARIANT" and ans.target_id:
            return Resolution(new_id, "VARIANT", ans.target_id, "llm", ans.confidence < 0.6)
        return Resolution(new_id, "NEW", None, "llm", ans.confidence < 0.6)

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
        self.ctx.graph.upsert_job(jid, props)
        self._remember("MUNKA_KOR", jid, labels, job.get("source", "catalog") or "catalog")
        return jid
