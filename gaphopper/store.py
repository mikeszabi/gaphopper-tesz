"""Futási állapot, napló, köztes eredmények és alias-index SQLite-ban."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

STATUSES = ["NYERS", "P1_KOTEGBEN", "P1_KESZ", "P2_KOTEGBEN", "P2_ELFOGADVA", "SZAKERTORE", "GRAFBAN",
            "ELVETVE", "HIBA"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
  job_id TEXT PRIMARY KEY, record_json TEXT NOT NULL, status TEXT NOT NULL,
  input_hash TEXT, note TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS artifacts (
  job_id TEXT, step TEXT, prompt_version TEXT, json TEXT, created_at TEXT,
  PRIMARY KEY (job_id, step));
CREATE TABLE IF NOT EXISTS llm_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, step TEXT, provider TEXT, model TEXT,
  prompt_version TEXT, input_hash TEXT, raw_text TEXT, parsed_ok INTEGER, usage_json TEXT,
  created_at TEXT);
CREATE TABLE IF NOT EXISTS review_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, kind TEXT, payload_json TEXT,
  status TEXT DEFAULT 'NYITOTT', created_at TEXT, decided_at TEXT, decision_json TEXT);
CREATE TABLE IF NOT EXISTS alias_index (
  key TEXT, lang TEXT, text TEXT, entity_type TEXT, entity_id TEXT, source TEXT,
  count INTEGER DEFAULT 1, first_seen TEXT, last_seen TEXT, status TEXT DEFAULT 'AUTO',
  PRIMARY KEY (key, entity_type));
CREATE TABLE IF NOT EXISTS batches (
  batch_id TEXT PRIMARY KEY, step TEXT, provider TEXT, model TEXT, prompt_version TEXT,
  map_json TEXT, status TEXT DEFAULT 'BEKULDVE', created_at TEXT, collected_at TEXT);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha(obj) -> str:
    return hashlib.sha1(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:12]


class Store:
    def __init__(self, path: Path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        # timeout + WAL: a felület olvashat, miközben a háttérben futó parancs ír
        self.db = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript(SCHEMA)

    # --- munkakörök ---
    def upsert_job(self, record: dict) -> bool:
        """True, ha új vagy megváltozott rekord (ilyenkor NYERS állapotba kerül)."""
        h = sha(record)
        row = self.db.execute("SELECT input_hash FROM jobs WHERE job_id=?", (record["job_id"],)).fetchone()
        if row and row["input_hash"] == h:
            return False
        self.db.execute(
            "INSERT INTO jobs(job_id, record_json, status, input_hash, updated_at) VALUES (?,?,?,?,?) "
            "ON CONFLICT(job_id) DO UPDATE SET record_json=excluded.record_json, status='NYERS', "
            "input_hash=excluded.input_hash, updated_at=excluded.updated_at",
            (record["job_id"], json.dumps(record, ensure_ascii=False), "NYERS", h, now()))
        self.db.commit()
        return True

    def job(self, job_id: str) -> dict:
        row = self.db.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if not row:
            raise KeyError(job_id)
        return json.loads(row["record_json"])

    def status(self, job_id: str) -> str:
        row = self.db.execute("SELECT status FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        return row["status"] if row else ""

    def set_status(self, job_id: str, status: str, note: str = "") -> None:
        self.db.execute("UPDATE jobs SET status=?, note=?, updated_at=? WHERE job_id=?",
                        (status, note, now(), job_id))
        self.db.commit()

    def jobs(self, status: str | None = None) -> list[sqlite3.Row]:
        if status:
            return self.db.execute("SELECT * FROM jobs WHERE status=? ORDER BY job_id", (status,)).fetchall()
        return self.db.execute("SELECT * FROM jobs ORDER BY job_id").fetchall()

    # --- köztes eredmények és napló ---
    def save_artifact(self, job_id: str, step: str, data: dict, prompt_version: str) -> None:
        self.db.execute(
            "INSERT OR REPLACE INTO artifacts VALUES (?,?,?,?,?)",
            (job_id, step, prompt_version, json.dumps(data, ensure_ascii=False), now()))
        self.db.commit()

    def clear_artifact(self, job_id: str, step: str) -> None:
        self.db.execute("DELETE FROM artifacts WHERE job_id=? AND step=?", (job_id, step))
        self.db.commit()

    def artifact(self, job_id: str, step: str) -> dict | None:
        row = self.db.execute("SELECT json FROM artifacts WHERE job_id=? AND step=?", (job_id, step)).fetchone()
        return json.loads(row["json"]) if row else None

    def log(self, job_id, step, provider, model, prompt_version, input_hash, raw, ok, usage) -> None:
        self.db.execute(
            "INSERT INTO llm_log(job_id, step, provider, model, prompt_version, input_hash, raw_text, "
            "parsed_ok, usage_json, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (job_id, step, provider, model, prompt_version, input_hash, raw, int(ok),
             json.dumps(usage, default=str), now()))
        self.db.commit()

    # --- szakértői sor ---
    def enqueue(self, job_id: str, kind: str, payload: dict) -> int:
        cur = self.db.execute(
            "INSERT INTO review_queue(job_id, kind, payload_json, created_at) VALUES (?,?,?,?)",
            (job_id, kind, json.dumps(payload, ensure_ascii=False), now()))
        self.db.commit()
        return cur.lastrowid

    def queue(self, status: str = "NYITOTT") -> list[sqlite3.Row]:
        return self.db.execute("SELECT * FROM review_queue WHERE status=? ORDER BY id", (status,)).fetchall()

    def update_payload(self, qid: int, payload: dict) -> None:
        self.db.execute("UPDATE review_queue SET payload_json=? WHERE id=?", (json.dumps(payload, ensure_ascii=False), qid))
        self.db.commit()

    def decide(self, qid: int, status: str, decision: dict | None = None) -> None:
        self.db.execute("UPDATE review_queue SET status=?, decided_at=?, decision_json=? WHERE id=?",
                        (status, now(), json.dumps(decision or {}, ensure_ascii=False), qid))
        self.db.commit()

    # --- alias-index (keresőtábla, nem entitás) ---
    def alias_lookup(self, key: str, entity_type: str) -> str | None:
        row = self.db.execute("SELECT entity_id FROM alias_index WHERE key=? AND entity_type=?",
                              (key, entity_type)).fetchone()
        return row["entity_id"] if row else None

    def alias_add(self, key: str, lang: str, text: str, entity_type: str, entity_id: str, source: str) -> None:
        t = now()
        self.db.execute(
            "INSERT INTO alias_index(key, lang, text, entity_type, entity_id, source, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(key, entity_type) DO UPDATE SET "
            "count=count+1, last_seen=excluded.last_seen",
            (key, lang, text, entity_type, entity_id, source, t, t))
        self.db.commit()

    def alias_add_many(self, rows: list[tuple]) -> None:
        """(key, lang, text, entity_type, entity_id, source) sorok egy tranzakcióban."""
        if not rows:
            return
        t = now()
        self.db.executemany(
            "INSERT INTO alias_index(key, lang, text, entity_type, entity_id, source, first_seen, last_seen) "
            "VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(key, entity_type) DO UPDATE SET "
            "count=count+1, last_seen=excluded.last_seen", [(*r, t, t) for r in rows])
        self.db.commit()

    def alias_repoint(self, old_id: str, new_id: str) -> None:
        self.db.execute("UPDATE alias_index SET entity_id=? WHERE entity_id=?", (new_id, old_id))
        self.db.commit()

    # --- kötegek ---
    def save_batch(self, batch_id: str, step: str, provider: str, model: str, prompt_version: str,
                   mapping: dict) -> None:
        self.db.execute("INSERT INTO batches(batch_id, step, provider, model, prompt_version, map_json, "
                        "created_at) VALUES (?,?,?,?,?,?,?)",
                        (batch_id, step, provider, model, prompt_version, json.dumps(mapping), now()))
        self.db.commit()

    def batches(self, only_open: bool = True) -> list[sqlite3.Row]:
        q = "SELECT * FROM batches" + (" WHERE status='BEKULDVE'" if only_open else "") + " ORDER BY created_at"
        return self.db.execute(q).fetchall()

    def close_batch(self, batch_id: str, status: str = "BEGYUJTVE") -> None:
        self.db.execute("UPDATE batches SET status=?, collected_at=? WHERE batch_id=?", (status, now(), batch_id))
        self.db.commit()

    # --- költség- és időnapló ---
    def usage(self, job_ids: list[str] | None = None) -> list[dict]:
        """llm_log összesítve munkakör, lépés, modell és promptverzió szerint."""
        q = "SELECT job_id, step, provider, model, prompt_version, parsed_ok, usage_json, created_at FROM llm_log"
        rows = self.db.execute(q + " ORDER BY id").fetchall()
        agg: dict[tuple, dict] = {}
        for r in rows:
            if job_ids and r["job_id"] not in job_ids:
                continue
            u = json.loads(r["usage_json"] or "{}")
            # A hosszú kontextus díjsávját hívásonként kell meghatározni.
            prompt_tokens = (int(u.get("input_tokens") or 0) + int(u.get("cache_read_input_tokens") or 0)
                             + int(u.get("cache_creation_input_tokens") or 0))
            band = 200001 if prompt_tokens > 200000 else 0
            k = (r["job_id"] or "-", r["step"], r["provider"], r["model"], r["prompt_version"], band)
            a = agg.setdefault(k, {"job_id": k[0], "step": k[1], "provider": k[2], "model": k[3],
                                   "prompt_version": k[4], "pricing_input_tokens": band,
                                   "hivas": 0, "sikeres": 0, "input": 0, "cache_read": 0, "cache_write": 0,
                                   "output": 0, "ido_mp": 0.0})
            a["hivas"] += 1
            a["sikeres"] += int(r["parsed_ok"])
            a["input"] += int(u.get("input_tokens") or 0)
            a["cache_read"] += int(u.get("cache_read_input_tokens") or 0)
            a["cache_write"] += int(u.get("cache_creation_input_tokens") or 0)
            a["output"] += int(u.get("output_tokens") or 0)
            a["ido_mp"] += (u.get("duration_ms") or 0) / 1000
        return list(agg.values())
