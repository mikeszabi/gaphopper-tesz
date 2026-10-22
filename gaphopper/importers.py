"""Bemeneti rekordok beolvasása: Excel (Munkakörök + Forrás lap), JSONL, CSV."""
from __future__ import annotations

import csv
import json
from datetime import date
from pathlib import Path

FIELDS = ["job_id", "title", "field", "description", "tasks", "requirements", "conditions",
          "entry_route", "source", "source_url", "fetched_at", "locale"]


def normalize_record(r: dict, source: str = "") -> dict:
    rec = {k: r.get(k, "") for k in FIELDS}
    rec.update({k: v for k, v in r.items() if k not in rec})
    if not rec["job_id"]:
        raise ValueError(f"Hiányzó job_id: {r}")
    tasks = rec.get("tasks") or []
    if isinstance(tasks, str):
        tasks = [t.strip() for t in tasks.split(";") if t.strip()]
    rec["tasks"] = tasks
    rec["source"] = rec["source"] or source
    rec["fetched_at"] = rec["fetched_at"] or date.today().isoformat()
    rec["locale"] = rec["locale"] or "hu-HU"
    return rec


def _sheet_rows(ws, header_first: str) -> list[dict]:
    rows = list(ws.iter_rows(values_only=True))
    hi = next(i for i, r in enumerate(rows) if r and str(r[0]).strip() == header_first)
    header = [str(h).strip() if h is not None else "" for h in rows[hi]]
    out = []
    for r in rows[hi + 1:]:
        if not r or r[0] is None:
            continue
        out.append({header[i]: r[i] for i in range(min(len(header), len(r))) if header[i]})
    return out


def _txt(v) -> str:
    return "" if v is None else str(v).strip()


def from_xlsx(path: Path) -> list[dict]:
    """A munkakör-táblázat: 'Munkakörök' lap (rank, code, title, cluster, field, route, skills,
    description, ...) és opcionálisan 'Forrás' lap (code, condition, evidence_task, ...).
    A táblázat kész TESZ- és triplet-lapjait szándékosan NEM olvassuk be: a felbontás a munkakör
    leírásából induljon, és csak utólag vessük össze a meglévő tervvel."""
    import openpyxl

    wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
    jobs = _sheet_rows(wb["Munkakörök"], "rank")
    extra = {}
    if "Forrás" in wb.sheetnames:
        extra = {r["code"]: r for r in _sheet_rows(wb["Forrás"], "code")}
    jobs.sort(key=lambda j: (j.get("rank") is None, j.get("rank") or 0))
    out = []
    for j in jobs:
        x = extra.get(j["code"], {})
        title = _txt(j.get("title"))
        alt = []
        for t in (j.get("official_title"), x.get("official_title"), x.get("bls_title")):
            t = _txt(t)
            if t and t.casefold() != title.casefold() and t.casefold() not in [a.casefold() for a in alt]:
                alt.append(t)
        rec = {
            "job_id": f"onet:{j['code']}",
            "title": title,
            "alt_titles": alt,
            "codes": {"onet": _txt(j["code"]), "soc": _txt(x.get("bls_code"))},
            "rank": j.get("rank"),
            "tier": _txt(x.get("tier")),
            "field": " / ".join(_txt(v) for v in (j.get("cluster"), j.get("field"), x.get("other_fields")) if _txt(v)),
            "description": _txt(j.get("description")),
            "requirements": _txt(j.get("skills")),
            "conditions": _txt(x.get("condition")),
            "entry_route": _txt(j.get("route")),
            "first_step": _txt(x.get("first_step")),
            "outlook": _txt(x.get("outlook")),
            "resilience": _txt(x.get("resilience")),
            "evidence_hint": _txt(x.get("evidence_task")),
            "source": f"{Path(path).name} (Munkakörök + Forrás lap)",
            "source_url": _txt(j.get("profile") or x.get("profile")),
            "source_lang": "en",
            "locale": "hu-HU",
        }
        out.append(normalize_record({k: v for k, v in rec.items() if v not in ("", None, [], {})} | {"job_id": rec["job_id"]},
                                    source=Path(path).name))
    return out


def from_jsonl(path: Path) -> list[dict]:
    with open(path, encoding="utf-8") as fh:
        return [normalize_record(json.loads(line), Path(path).name) for line in fh if line.strip()]


def from_csv(path: Path) -> list[dict]:
    with open(path, encoding="utf-8-sig", newline="") as fh:
        return [normalize_record(r, Path(path).name) for r in csv.DictReader(fh)]


def load(path: str | Path) -> list[dict]:
    p = Path(path)
    if p.suffix.lower() in (".xlsx", ".xlsm"):
        return from_xlsx(p)
    if p.suffix.lower() in (".jsonl", ".ndjson"):
        return from_jsonl(p)
    if p.suffix.lower() == ".csv":
        return from_csv(p)
    if p.suffix.lower() == ".json":
        data = json.loads(p.read_text(encoding="utf-8"))
        return [normalize_record(r, p.name) for r in (data if isinstance(data, list) else [data])]
    raise ValueError(f"Ismeretlen formátum: {p.suffix}")
