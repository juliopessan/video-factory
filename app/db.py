"""Camada de persistencia (SQLite). Sem ORM: o esquema e pequeno e estavel."""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime, timezone
from typing import Any, Iterable

from .config import settings

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
    id          TEXT PRIMARY KEY,
    name        TEXT NOT NULL,
    created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS generations (
    id                  TEXT PRIMARY KEY,
    project_id          TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    parent_id           TEXT REFERENCES generations(id),
    root_id             TEXT NOT NULL,
    batch_id            TEXT,
    label               TEXT,
    mode                TEXT NOT NULL,
    prompt              TEXT NOT NULL,
    resolution          TEXT NOT NULL,
    aspect_ratio        TEXT NOT NULL,
    duration_seconds    INTEGER NOT NULL,
    cumulative_seconds  INTEGER NOT NULL,
    cost_units          REAL NOT NULL DEFAULT 0,
    status              TEXT NOT NULL,
    provider            TEXT NOT NULL,
    interaction_id      TEXT,
    error               TEXT,
    asset_path          TEXT,
    mime_type           TEXT,
    meta                TEXT NOT NULL DEFAULT '{}',
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_generations_project ON generations(project_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_generations_root ON generations(root_id);

CREATE TABLE IF NOT EXISTS pipelines (
    id          TEXT PRIMARY KEY,
    project_id  TEXT NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
    title       TEXT NOT NULL,
    context     TEXT NOT NULL,
    story       TEXT NOT NULL,
    storyboard  TEXT NOT NULL,
    status      TEXT NOT NULL,
    error       TEXT,
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_renders (
    pipeline_id     TEXT NOT NULL REFERENCES pipelines(id) ON DELETE CASCADE,
    generation_id   TEXT NOT NULL REFERENCES generations(id),
    segment_index   INTEGER NOT NULL,
    created_at      TEXT NOT NULL,
    PRIMARY KEY (pipeline_id, segment_index)
);

CREATE TABLE IF NOT EXISTS assets (
    id          TEXT PRIMARY KEY,
    project_id  TEXT REFERENCES projects(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    filename    TEXT NOT NULL,
    path        TEXT NOT NULL,
    mime_type   TEXT NOT NULL,
    size        INTEGER NOT NULL,
    duration_seconds REAL,
    created_at  TEXT NOT NULL
);
"""


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    if conn is None:
        conn = sqlite3.connect(settings.db_path, timeout=30, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        _local.conn = conn
    return conn


def init_db() -> None:
    conn = connect()
    conn.executescript(SCHEMA)
    # bancos criados antes da medicao de duracao nao tem a coluna
    columns = {row["name"] for row in conn.execute("PRAGMA table_info(assets)")}
    if "duration_seconds" not in columns:
        conn.execute("ALTER TABLE assets ADD COLUMN duration_seconds REAL")
    conn.commit()


def execute(sql: str, params: Iterable[Any] = ()) -> None:
    conn = connect()
    conn.execute(sql, tuple(params))
    conn.commit()


def query(sql: str, params: Iterable[Any] = ()) -> list[dict]:
    rows = connect().execute(sql, tuple(params)).fetchall()
    return [dict(r) for r in rows]


def query_one(sql: str, params: Iterable[Any] = ()) -> dict | None:
    row = connect().execute(sql, tuple(params)).fetchone()
    return dict(row) if row else None


def insert(table: str, data: dict) -> None:
    cols = ", ".join(data)
    marks = ", ".join("?" for _ in data)
    execute(f"INSERT INTO {table} ({cols}) VALUES ({marks})", list(data.values()))


def update(table: str, row_id: str, data: dict) -> None:
    data = {**data, "updated_at": now()} if table == "generations" else dict(data)
    sets = ", ".join(f"{k} = ?" for k in data)
    execute(f"UPDATE {table} SET {sets} WHERE id = ?", [*data.values(), row_id])


def loads_meta(row: dict) -> dict:
    row = dict(row)
    try:
        row["meta"] = json.loads(row.get("meta") or "{}")
    except json.JSONDecodeError:
        row["meta"] = {}
    return row
