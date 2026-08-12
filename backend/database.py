"""SQLite persistence for categorized images and generation jobs."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Generator

from backend.config import DATABASE_PATH, ensure_directories


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def get_connection() -> Generator[sqlite3.Connection, None, None]:
    ensure_directories()
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_db() -> None:
    with get_connection() as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS images (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filepath TEXT NOT NULL UNIQUE,
                categories TEXT NOT NULL DEFAULT '[]',
                primary_category TEXT,
                confidence REAL,
                metadata TEXT DEFAULT '{}',
                created_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS jobs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_type TEXT NOT NULL,
                category TEXT,
                status TEXT NOT NULL DEFAULT 'pending',
                input_paths TEXT NOT NULL DEFAULT '[]',
                output_path TEXT,
                error TEXT,
                created_at TEXT NOT NULL,
                completed_at TEXT
            );

            CREATE INDEX IF NOT EXISTS idx_images_primary_category
                ON images(primary_category);
            CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
            """
        )


def insert_image(
    filename: str,
    filepath: str,
    categories: list[str],
    primary_category: str | None,
    confidence: float | None,
    metadata: dict[str, Any] | None = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO images (filename, filepath, categories, primary_category, confidence, metadata, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(filepath) DO UPDATE SET
                categories = excluded.categories,
                primary_category = excluded.primary_category,
                confidence = excluded.confidence,
                metadata = excluded.metadata
            RETURNING id
            """,
            (
                filename,
                filepath,
                json.dumps(categories),
                primary_category,
                confidence,
                json.dumps(metadata or {}),
                _utc_now(),
            ),
        )
        row = cursor.fetchone()
        return int(row["id"])


def list_images(category: str | None = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if category:
            rows = conn.execute(
                """
                SELECT * FROM images
                WHERE primary_category = ? OR categories LIKE ?
                ORDER BY created_at DESC
                """,
                (category, f'%"{category}"%'),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM images ORDER BY created_at DESC"
            ).fetchall()
    return [dict(row) for row in rows]


def create_job(
    job_type: str,
    input_paths: list[str],
    category: str | None = None,
) -> int:
    with get_connection() as conn:
        cursor = conn.execute(
            """
            INSERT INTO jobs (job_type, category, input_paths, created_at)
            VALUES (?, ?, ?, ?)
            RETURNING id
            """,
            (job_type, category, json.dumps(input_paths), _utc_now()),
        )
        row = cursor.fetchone()
        return int(row["id"])


def update_job(
    job_id: int,
    *,
    status: str | None = None,
    output_path: str | None = None,
    error: str | None = None,
) -> None:
    fields: list[str] = []
    values: list[Any] = []
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if output_path is not None:
        fields.append("output_path = ?")
        values.append(output_path)
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    if status == "completed":
        fields.append("completed_at = ?")
        values.append(_utc_now())
    if not fields:
        return
    values.append(job_id)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE jobs SET {', '.join(fields)} WHERE id = ?",
            values,
        )


def list_jobs(job_type: str | None = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if job_type:
            rows = conn.execute(
                "SELECT * FROM jobs WHERE job_type = ? ORDER BY created_at DESC",
                (job_type,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM jobs ORDER BY created_at DESC"
            ).fetchall()
    return [dict(row) for row in rows]
