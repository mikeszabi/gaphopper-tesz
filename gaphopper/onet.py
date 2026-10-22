"""O*NET feladatlisták (Task Statements) betöltése a munkakör-rekordok `tasks` mezőjébe.

Forrás: https://www.onetcenter.org/database.html → „Task Statements” (.txt vagy .xlsx),
opcionálisan „Task Ratings” a fontossági sorrendhez. Licenc: CC BY 4.0 (a forrást a rekord jelöli).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path


def _rows(path: Path) -> list[dict]:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        import openpyxl

        ws = openpyxl.load_workbook(p, read_only=True, data_only=True).worksheets[0]
        it = ws.iter_rows(values_only=True)
        header = [str(h).strip() if h is not None else "" for h in next(it)]
        return [{header[i]: r[i] for i in range(len(header))} for r in it if r and r[0]]
    with open(p, encoding="utf-8-sig", errors="replace", newline="") as fh:
        return list(csv.DictReader(fh, delimiter="\t" if p.suffix.lower() in (".txt", ".tsv") else ","))


def _col(row: dict, *names: str):
    for n in names:
        if n in row:
            return row[n]
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def load_tasks(statements: Path, ratings: Path | None = None) -> dict[str, dict]:
    """{soc_kód: {"core": [...], "supplemental": [...]}} fontossági sorrendben (ha van Task Ratings)."""
    importance: dict[tuple[str, str], float] = {}
    if ratings:
        for r in _rows(ratings):
            if str(_col(r, "Scale ID")).strip() == "IM":
                try:
                    importance[(str(_col(r, "O*NET-SOC Code")).strip(), str(_col(r, "Task ID")).strip())] = float(
                        _col(r, "Data Value"))
                except (TypeError, ValueError):
                    pass
    out: dict[str, list] = {}
    for i, r in enumerate(_rows(statements)):
        code = str(_col(r, "O*NET-SOC Code") or "").strip()
        task = str(_col(r, "Task") or "").strip()
        if not code or not task:
            continue
        tid = str(_col(r, "Task ID") or "").strip()
        ttype = str(_col(r, "Task Type") or "").strip().lower()
        out.setdefault(code, []).append((-importance.get((code, tid), 0.0), i, ttype, task))
    res = {}
    for code, items in out.items():
        items.sort()
        res[code] = {"core": [t for *_, ty, t in items if ty != "supplemental"],
                     "supplemental": [t for *_, ty, t in items if ty == "supplemental"]}
    return res


def enrich(jobs_path: Path, statements: Path, ratings: Path | None = None, out_path: Path | None = None,
           include_supplemental: bool = False, release: str = "") -> dict:
    tasks = load_tasks(statements, ratings)
    lines = Path(jobs_path).read_text(encoding="utf-8").splitlines()
    recs, hit, miss = [], 0, []
    for line in lines:
        if not line.strip():
            continue
        r = json.loads(line)
        code = (r.get("codes") or {}).get("onet") or (r["job_id"][5:] if r["job_id"].startswith("onet:") else "")
        t = tasks.get(code)
        if t:
            r["tasks"] = t["core"] + (t["supplemental"] if include_supplemental else [])
            if t["supplemental"] and not include_supplemental:
                r["tasks_supplemental"] = t["supplemental"]
            r["tasks_source"] = f"O*NET{(' ' + release) if release else ''} Task Statements, CC BY 4.0"
            hit += 1
        elif code:
            miss.append(code)
        recs.append(r)
    target = Path(out_path or jobs_path)
    target.write_text("".join(json.dumps(r, ensure_ascii=False, default=str) + "\n" for r in recs), encoding="utf-8")
    return {"records": len(recs), "with_tasks": hit, "missing": miss, "out": str(target)}
