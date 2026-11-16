"""Build a small deployment snapshot without descriptions, refs, or personal data.
Run from repository root: python -m services.export_catalog
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def export(source=ROOT / "data/job_all.jsonl", target=ROOT / "services/data/jobs.json"):
    if not source.exists():
        raise SystemExit(f"Hiányzó munkakör-katalógus: {source}")
    rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
    result = {row["job_id"]: {"title": row["title"], "tasks": row.get("tasks", [])} for row in rows}
    if any(not isinstance(r["tasks"], list) or any(not isinstance(t, str) for t in r["tasks"]) for r in result.values()):
        raise SystemExit("A tasks mezők szöveglisták legyenek.")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n", encoding="utf-8")
    print(f"{len(result)} munkakör exportálva: {target.relative_to(ROOT)}")


if __name__ == "__main__":
    export()
