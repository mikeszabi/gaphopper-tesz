import unicodedata
from fastapi import HTTPException
from gaphopper.config import vocab
from .models import (CareerResult, CareerSelections, ComparisonResult, Competency, Difference,
                     GeneratedMap, MapResult, Suggestion)


def norm(text):
    return " ".join(unicodedata.normalize("NFKC", text).casefold().split())


def difference(tasks, baseline, available):
    old = {norm(t): t for t in baseline}
    new = {norm(t): t for t in tasks or []}
    return Difference(baseline_available=available, supplied=tasks is not None,
                      changed=(set(old) != set(new)) if available and tasks is not None else (False if available else None),
                      added=[v for k, v in new.items() if k not in old] if available else [],
                      removed=[v for k, v in old.items() if k not in new] if tasks is not None else [],
                      unchanged=[v for k, v in old.items() if k in new] if tasks is not None else list(old.values()))


class Service:
    def __init__(self, repo, ai):
        self.repo, self.ai = repo, ai

    def resolve(self, job_id=None, title=None):
        jobs = self.repo.jobs()
        if job_id:
            matches = [j for j in jobs if j["id"] == job_id]
        else:
            matches = [j for j in jobs if norm(title or "") in
                       {norm(x.removeprefix("hu:").removeprefix("en:")) for x in
                        [j["title"], j.get("title_en") or "", *j.get("aliases", [])] if x}]
        if len(matches) > 1:
            raise HTTPException(409, detail={"message": "Több munkakör egyezik; válassz job_id alapján.", "jobs": matches})
        return matches[0] if matches else None

    def validate_user(self, ids):
        catalog = {k["id"]: k for k in self.repo.competencies()}
        unknown = sorted(set(ids) - catalog.keys())
        if unknown:
            raise HTTPException(422, detail={"message": "Ismeretlen kompetenciaazonosítók.", "ids": unknown})
        return catalog

    def job_map(self, request):
        job = self.resolve(request.job_id, request.job_title) if request.job_id or request.job_title else None
        if request.job_id and job is None:
            raise HTTPException(404, "Nincs ilyen munkakörazonosító.")
        profile = self.repo.profile(job) if job else {"tasks": [], "triplets": [], "competencies": [], "baseline_available": False}
        diff = difference(request.tasks, profile["tasks"], profile["baseline_available"])
        tasks = request.tasks if request.tasks is not None else profile["tasks"]
        title = job["title"] if job else request.job_title or "Egyedi feladatlista"
        warnings = []
        if not profile["baseline_available"]:
            warnings.append("Nincs elérhető alapfeladatlista; az eltérés nem állapítható meg.")
        if (request.tasks is None or diff.changed is False) and profile["competencies"]:
            return MapResult(job_id=job["id"], job_title=title, source="default", tasks=tasks,
                             user_job_1_difference=diff, user_job_1_triplets=profile["triplets"],
                             user_job_1_competencies=profile["competencies"], ai=None, warnings=warnings)
        if not tasks:
            raise HTTPException(404, "Nincs alap kompetenciatérkép vagy feladatlista; adj meg feladatokat.")
        catalog = self.repo.competencies()
        if not catalog:
            raise HTTPException(503, "Üres a Neo4j kompetenciatörzs.")
        allowed = {k["id"]: k for k in catalog}
        choice = self.ai.choice(request.ai)

        def check(output):
            covered, ids = set(), set()
            for t in output.triplets:
                if t.id in ids:
                    raise ValueError("A tripletazonosítók legyenek egyediek.")
                ids.add(t.id)
                if not set(t.competency_ids) <= allowed.keys():
                    raise ValueError("Ismeretlen kompetenciaazonosító.")
                if any(i < 0 or i >= len(tasks) for i in t.task_indices):
                    raise ValueError("Érvénytelen feladatindex; nullától számozz.")
                covered.update(t.task_indices)
            if covered != set(range(len(tasks))):
                raise ValueError("Minden bemeneti feladathoz kell legalább egy triplet.")

        result = self.ai.ask(choice,
            "A feladatokból képezz TESZ-tripleteket: TESZ-nivaló × KÖZ-eg × HELY-zet. "
            "A TESZ-nivaló általános munkaminta × SZER-ELEM-fajta, a szakmai kontextust az indoklásban őrizd meg. "
            "Minden feladat legyen lefedve (task_indices nullától). Minden triplethez rendelj a törzsből "
            "kompetenciákat és indokold a kapcsolatot. Ne találj ki új kompetenciát.",
            {"job_title": title, "tasks": tasks, "ontology": {k: vocab()[k] for k in
             ("tesz_tipus", "kozeg", "helyzet", "szer_elem_fajta")}, "competencies": catalog}, GeneratedMap, check)
        check(result)
        competencies = {}
        for triplet in result.triplets:
            for cid in set(triplet.competency_ids):
                c = competencies.setdefault(cid, Competency(id=cid, label=allowed[cid]["label"]))
                c.evidence.append(triplet.id)
        warnings.append("AI által javasolt térkép; szakértői ellenőrzés szükséges a véglegesítéshez.")
        return MapResult(job_id=job["id"] if job else None, job_title=title, source="generated", tasks=tasks,
                         user_job_1_difference=diff, user_job_1_triplets=[t.model_dump() for t in result.triplets],
                         user_job_1_competencies=list(competencies.values()), ai=choice, warnings=warnings)

    def compare(self, request):
        catalog = self.validate_user(request.user_competency_map)
        target = self.job_map(request)
        user = set(request.user_competency_map)
        required = {c.id for c in target.user_job_1_competencies}
        return ComparisonResult(job_map=target,
            user_job_competency_matches=[c for c in target.user_job_1_competencies if c.id in user],
            user_job_competency_missing=[c for c in target.user_job_1_competencies if c.id not in user],
            user_competency_extra=[Competency(id=c, label=catalog[c]["label"]) for c in sorted(user - required)],
            coverage=round(len(user & required) / len(required), 4) if required else 0)

    def career(self, request):
        catalog = self.validate_user(request.user_competency_map)
        candidates = self.repo.candidates()
        if not candidates:
            raise HTTPException(404, "Nincs feldolgozott, kompetenciákhoz kapcsolt munkakör.")
        by_id = {c["id"]: c for c in candidates}
        experience_ids, sectors, sector_names, taxonomy, warnings = set(), set(), set(), set(), []
        for exp in request.user_work_experience:
            job = self.resolve(exp.job_id, exp.job_title)
            if exp.job_id and not job:
                raise HTTPException(404, f"Ismeretlen tapasztalati munkakör: {exp.job_id}")
            if job:
                experience_ids.add(job["id"])
                base = by_id.get(job["id"], {})
                sectors.update(base.get("sector_ids", []))
                taxonomy.update(base.get("taxonomy", []))
            if exp.sector:
                sector_names.add(norm(exp.sector))
        if not experience_ids:
            warnings.append("A tapasztalati munkakörök nem oldhatók fel; csak a megadott szektor alapján állapítható meg közelség.")
        user = set(request.user_competency_map)
        for c in candidates:
            required = set(c["competency_ids"])
            c["coverage"] = round(len(user & required) / len(required), 4) if required else 0
            c["near"] = c["id"] in experience_ids or bool(sectors & set(c["sector_ids"]) or taxonomy & set(c["taxonomy"])
                        or sector_names & {norm(n) for n in c["sectors"]})
        ordered = sorted(candidates, key=lambda c: (-c["coverage"], c["id"]))
        # SAFE is always the highest coverage; other categories cannot reuse its jobs.
        safe = ordered[:request.per_category]
        used = {c["id"] for c in safe}
        develop = [c for c in ordered if c["id"] not in used and c["near"] and c["coverage"] < 1][:12]
        alternate = [c for c in ordered if c["id"] not in used and not c["near"] and c["coverage"] > 0][:12]
        pools = {"SAFE": safe, "DEVELOP": develop, "ALTERNATE": alternate}
        counts = {k: min(request.per_category, len(v)) for k, v in pools.items()}
        for category, count in counts.items():
            if count < request.per_category:
                warnings.append(f"{category}: csak {count} igazolható jelölt érhető el.")
        choice = self.ai.choice(request.ai)

        def check(output):
            seen = set()
            actual = {k: 0 for k in pools}
            for s in output.suggestions:
                if s.job_id in seen or s.job_id not in {c["id"] for c in pools[s.category]}:
                    raise ValueError("Csak a kategória jelöltjeiből válassz, ismétlés nélkül.")
                seen.add(s.job_id)
                actual[s.category] += 1
            if actual != counts:
                raise ValueError(f"Kategóriánként pontosan ennyi ajánlás szükséges: {counts}")

        result = self.ai.ask(choice,
            "Válassz a kategóriák előszűrt jelöltjeiből a kért darabszám szerint. SAFE: legjobb egyezések; "
            "DEVELOP: közeli, hiányzó kompetenciákat igénylő szerepek; ALTERNATE: más besorolású/szektorú pálya. "
            "SAFE minden jelöltjét add vissza. Indokold a kompetenciák és a munkatapasztalat alapján, "
            "konkrét fejlesztési javaslattal. Ne állíts nem igazolt képesítést vagy tapasztalatot.",
            {"pools": pools, "counts": counts, "user_competency_map": list(user),
             "user_work_experience": [e.model_dump() for e in request.user_work_experience],
             "competencies": [{"id": k, "label": v["label"]} for k, v in catalog.items()]}, CareerSelections, check)
        check(result)
        suggestions = []
        for s in result.suggestions:
            c = by_id[s.job_id]
            required = set(c["competency_ids"])
            def skill(cid):
                return Competency(id=cid, label=catalog.get(cid, {}).get("label", cid))
            suggestions.append(Suggestion(job_id=c["id"], job_title=c["title"], sectors=c["sectors"],
                category=s.category, reason=s.reason, coverage=c["coverage"],
                matches=[skill(k) for k in sorted(required & user)], missing=[skill(k) for k in sorted(required - user)]))
        return CareerResult(suggestions=suggestions, ai=choice, warnings=warnings, candidate_count=len(candidates))
