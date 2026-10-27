import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from .config import settings

_lock = threading.Lock()


def _connect() -> sqlite3.Connection:
    db = sqlite3.connect(settings.localforge_data_dir / "localforge.sqlite3")
    db.row_factory = sqlite3.Row
    return db


def initialize() -> None:
    with _connect() as db:
        db.execute("""
            CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                status TEXT NOT NULL,
                progress REAL NOT NULL DEFAULT 0,
                params TEXT NOT NULL,
                inputs TEXT NOT NULL,
                outputs TEXT NOT NULL DEFAULT '[]',
                error TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                stage TEXT NOT NULL DEFAULT 'Queued'
            )
        """)
        columns = {row[1] for row in db.execute("PRAGMA table_info(jobs)")}
        if "stage" not in columns:
            db.execute("ALTER TABLE jobs ADD COLUMN stage TEXT NOT NULL DEFAULT 'Queued'")


def create_job(kind: str, params: dict[str, Any], inputs: dict[str, str], status: str = "queued") -> dict[str, Any]:
    now = datetime.now(timezone.utc).isoformat()
    job = {
        "id": uuid4().hex,
        "kind": kind,
        "status": status,
        "progress": 0.0,
        "params": params,
        "inputs": inputs,
        "outputs": [],
        "error": None,
        "created_at": now,
        "updated_at": now,
        "stage": "Queued" if status == "queued" else "Preparing",
    }
    with _lock, _connect() as db:
        db.execute(
            "INSERT INTO jobs (id, kind, status, progress, params, inputs, outputs, error, created_at, updated_at, stage) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (job["id"], kind, status, 0, json.dumps(params), json.dumps(inputs),
             "[]", None, now, now, job["stage"]),
        )
    return job


def set_inputs_and_queue(job_id: str, inputs: dict[str, str]) -> None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as db:
        db.execute(
            "UPDATE jobs SET inputs = ?, status = 'queued', stage = 'Queued', updated_at = ? WHERE id = ?",
            (json.dumps(inputs), now, job_id),
        )


def _decode(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    item = dict(row)
    for key in ("params", "inputs", "outputs"):
        item[key] = json.loads(item[key])
    return item


def get_job(job_id: str) -> dict[str, Any] | None:
    with _connect() as db:
        return _decode(db.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())


def list_jobs() -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM jobs ORDER BY created_at DESC LIMIT 100").fetchall()
    return [_decode(row) for row in rows]


def list_jobs_by_status(status: str) -> list[dict[str, Any]]:
    with _connect() as db:
        rows = db.execute("SELECT * FROM jobs WHERE status = ? ORDER BY created_at", (status,)).fetchall()
    return [_decode(row) for row in rows]


def update_job(job_id: str, **changes: Any) -> None:
    allowed = {"status", "progress", "inputs", "outputs", "error", "stage"}
    values, clauses = [], []
    for key, value in changes.items():
        if key not in allowed:
            continue
        clauses.append(f"{key} = ?")
        values.append(json.dumps(value) if key in {"inputs", "outputs"} else value)
    clauses.append("updated_at = ?")
    values.extend([datetime.now(timezone.utc).isoformat(), job_id])
    with _lock, _connect() as db:
        db.execute(f"UPDATE jobs SET {', '.join(clauses)} WHERE id = ?", values)


def next_queued_job() -> dict[str, Any] | None:
    with _lock, _connect() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM jobs WHERE status = 'queued' ORDER BY created_at LIMIT 1").fetchone()
        if row:
            db.execute("UPDATE jobs SET status = 'running', stage = 'Preparing' WHERE id = ?", (row["id"],))
    return _decode(row)


def retry_job(job_id: str) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as db:
        cursor = db.execute(
            "UPDATE jobs SET status = 'queued', stage = 'Queued', progress = 0.0, outputs = '[]', error = NULL, updated_at = ? WHERE id = ? AND status = 'failed'",
            (now, job_id),
        )
    return get_job(job_id) if cursor.rowcount else None


def cancel_job(job_id: str) -> dict[str, Any] | None:
    now = datetime.now(timezone.utc).isoformat()
    with _lock, _connect() as db:
        db.execute(
            "UPDATE jobs SET status = 'failed', stage = 'Cancelled', error = 'Cancelled by user', updated_at = ? WHERE id = ?",
            (now, job_id),
        )
    return get_job(job_id)


def delete_job(job_id: str) -> bool:
    with _lock, _connect() as db:
        cur = db.execute("DELETE FROM jobs WHERE id = ?", (job_id,))
        return cur.rowcount > 0
