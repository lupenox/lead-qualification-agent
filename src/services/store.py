from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from config import Settings, get_settings
from schemas import QualificationResult


SCHEMA = """
CREATE TABLE IF NOT EXISTS leads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    company_name TEXT NOT NULL,
    website TEXT NOT NULL,
    route TEXT NOT NULL,
    score INTEGER NOT NULL,
    payload TEXT NOT NULL,
    created_at TEXT NOT NULL
);
"""


def _connect(path: Path) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute(SCHEMA)
    return conn


def persist_result(result: QualificationResult, settings: Settings | None = None) -> int:
    settings = settings or get_settings()
    with _connect(settings.database_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO leads (company_name, website, route, score, payload, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                result.company_name,
                result.website,
                result.route.value,
                result.score.total,
                result.model_dump_json(),
                result.created_at.isoformat(),
            ),
        )
        conn.commit()
        return int(cursor.lastrowid)


def list_leads(settings: Settings | None = None, limit: int = 50) -> list[dict]:
    settings = settings or get_settings()
    if not settings.database_path.exists():
        return []
    with _connect(settings.database_path) as conn:
        rows = conn.execute(
            "SELECT id, company_name, website, route, score, created_at FROM leads ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(row) for row in rows]


def get_lead(lead_id: int, settings: Settings | None = None) -> dict | None:
    settings = settings or get_settings()
    if not settings.database_path.exists():
        return None
    with _connect(settings.database_path) as conn:
        row = conn.execute("SELECT * FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if row is None:
        return None
    payload = json.loads(row["payload"])
    payload["lead_id"] = row["id"]
    return payload
