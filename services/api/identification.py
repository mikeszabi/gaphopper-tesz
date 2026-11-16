from fastapi import HTTPException
from gaphopper.llm import LLMError
from .models import (CompetencyIdentification, DocumentMatch, IdentificationResult, JobIdentification)
from .service import norm


def identify(service, document, kind, filename=None, only_ids=None):
    """only_ids: a munkakör-jelöltek szűkítése (a labor csak a felbontott, profillal bíró munkakörök közül választ)."""
    if kind == 'job':
        rows = [j for j in service.repo.jobs() if only_ids is None or j['id'] in only_ids]
        catalog = {j['id']:j for j in rows}
        candidates = [{'id':j['id'],'title':j['title'],'title_en':j.get('title_en'),
                       'aliases':j.get('aliases',[])[:6]} for j in rows]
        schema = JobIdentification
        instruction = ('Állapítsd meg a szövegben leírt munkakört a munkakör-adatbázis jelöltjeiből. '
                       'Legfeljebb 3, megalapozott találatot adj, confidence szerint csökkenő sorrendben. '
                       'A tényleges feladatokat és felelősségeket vizsgáld, ne csak a címet. '
                       'Ha nincs megfelelő munkakör, matches legyen üres. Az extracted_tasks mezőbe '
                       'csak a szövegben szereplő munkaköri feladatokat foglald össze.')
    else:
        rows = service.repo.competencies()
        catalog = {c['id']:c for c in rows}
        candidates = rows
        schema = CompetencyIdentification
        instruction = ('A CV vagy más szakmai szöveg alapján azonosítsd a személy alátámasztott kompetenciáit '
                       'a kompetencia-adatbázisból. Csak konkrét tevékenységgel, tapasztalattal vagy explicit '
                       'készségállítással igazolt kompetenciát adj vissza. Ne feltételezd a munkakör összes '
                       'kompetenciájának meglétét a pozíciónév alapján. Ne következtess névből, életkorból, '
                       'nemből, nemzetiségből, egészségből vagy más érzékeny személyes jellemzőből. '
                       'Ha nincs igazolt kompetencia, matches legyen üres. '
                       'Minden találathoz becsüld meg az eqf mezőben (1-8), milyen EQF-szinten gyakorolja a személy '
                       'az adott kompetenciát a bizonyíték szerint: az EQF a tudás mélysége, a műveletek összetettsége, '
                       'valamint az önállóság és felelősség (3: rutinfeladat felügyelet mellett; 4: önálló munka '
                       'ismert keretek között, mások rutinmunkájának felügyelete; 5: önálló, változó helyzetekben is, '
                       'kisebb csapat irányítása; 6: összetett, kiszámíthatatlan helyzetek, szakmai döntések és '
                       'felelősség; 7: új megközelítések, stratégiai felelősség, szakterület fejlesztése; 8: kutatási '
                       'élvonal, a szakterület megújítása). Az eqf mezőbe a személy egészére vonatkozó becslés '
                       'kerüljön (level 1-8 és rövid magyar indoklás: végzettség, beosztás, felelősség, önállóság). '
                       'Ha a szöveg nem ad elég alapot a szintre, a találat eqf mezőjét hagyd üresen.')
    if not catalog:
        raise HTTPException(503, 'Üres a kereséshez szükséges adatbázis.')
    choice = service.ai.choice(document.ai)

    def check(output):
        seen = set()
        for match in output.matches:
            if match.id not in catalog or match.id in seen:
                raise ValueError('A találatnak egyedi, az adatbázisban létező azonosítót kell megadnia.')
            seen.add(match.id)
            if any(norm(quote) not in norm(document.text) for quote in match.evidence):
                raise ValueError('Minden evidence elem a bemeneti szövegből vett szó szerinti idézet legyen.')

    result = service.ai.ask(choice, instruction +
        ' Minden találatot rövid, szó szerinti forrásidézetekkel (evidence) és magyar indoklással támassz alá. '
        'A confidence a saját bizonyossági becslésed (0..1), nem statisztikai valószínűség. '
        'Bizonytalanságot és hiányzó adatokat a warnings mezőben jelezz.',
        {'text':document.text,'candidates':candidates}, schema, check)
    try:
        check(result)
    except ValueError as exc:
        raise LLMError('Érvénytelen dokumentumazonosítási válasz.') from exc
    matches = [DocumentMatch(**m.model_dump(), label=catalog[m.id].get('title',catalog[m.id].get('label',m.id)))
               for m in sorted(result.matches,key=lambda m:(-m.confidence,m.id))]
    warnings = list(result.warnings)
    if not matches:
        warnings.append('A szöveg alapján nem találtunk kellően alátámasztott adatbázis-elemet.')
    warnings.append('AI-javaslat; a találatokat a forrásidézetek alapján ellenőrizd.')
    return IdentificationResult(kind=kind, matches=matches, ai=choice, source_filename=filename,
        selected_job_id=matches[0].id if kind=='job' and matches else None,
        user_competency_map=[m.id for m in matches] if kind=='competencies' else [],
        extracted_tasks=result.extracted_tasks if kind=='job' else [],
        person_eqf=getattr(result,'eqf',None) if kind=='competencies' else None, warnings=warnings)
