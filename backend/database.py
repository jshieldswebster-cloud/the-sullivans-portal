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
    conn = sqlite3.connect(DATABASE_PATH, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def _migrate_schema(conn: sqlite3.Connection) -> None:
    """Apply incremental schema migrations."""
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

        CREATE TABLE IF NOT EXISTS studio_jobs (
            id TEXT PRIMARY KEY,
            job_type TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending',
            progress INTEGER NOT NULL DEFAULT 0,
            message TEXT NOT NULL DEFAULT 'Queued',
            meta TEXT NOT NULL DEFAULT '{}',
            output_path TEXT,
            output_url TEXT,
            error TEXT,
            created_at TEXT NOT NULL,
            completed_at TEXT,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_studio_jobs_status ON studio_jobs(status);
        CREATE INDEX IF NOT EXISTS idx_studio_jobs_type ON studio_jobs(job_type);

        CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY,
            category TEXT NOT NULL,
            event_slug TEXT NOT NULL,
            event_name TEXT NOT NULL,
            ideal_row_path TEXT,
            status TEXT NOT NULL DEFAULT 'active',
            metadata TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            UNIQUE(category, event_slug)
        );

        CREATE INDEX IF NOT EXISTS idx_events_category ON events(category);

        CREATE TABLE IF NOT EXISTS calendar_entries (
            id TEXT PRIMARY KEY,
            event_id TEXT,
            category TEXT NOT NULL,
            event_name TEXT NOT NULL,
            post_number INTEGER,
            scheduled_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'scheduled',
            notes TEXT DEFAULT '',
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (event_id) REFERENCES events(id) ON DELETE SET NULL
        );

        CREATE INDEX IF NOT EXISTS idx_calendar_scheduled ON calendar_entries(scheduled_at);

        CREATE TABLE IF NOT EXISTS studio_settings (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL DEFAULT '{}',
            updated_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS drive_projects (
            id TEXT PRIMARY KEY,
            drive_folder_id TEXT NOT NULL UNIQUE,
            category TEXT NOT NULL,
            event_name TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'pending_review',
            cover_drive_id TEXT,
            carousel_drive_ids TEXT NOT NULL DEFAULT '[]',
            reel_drive_ids TEXT NOT NULL DEFAULT '[]',
            asset_count INTEGER NOT NULL DEFAULT 0,
            parse_warnings TEXT NOT NULL DEFAULT '[]',
            audio_track_id TEXT,
            local_paths TEXT NOT NULL DEFAULT '{}',
            reel_job_id TEXT,
            synced_at TEXT NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_drive_projects_status ON drive_projects(status);
        CREATE INDEX IF NOT EXISTS idx_drive_projects_category ON drive_projects(category);
        """
    )
    _migrate_drive_project_columns(conn)


def _migrate_drive_project_columns(conn: sqlite3.Connection) -> None:
    """Add staging/backlog columns to drive_projects if missing."""
    existing = {
        row[1]
        for row in conn.execute("PRAGMA table_info(drive_projects)").fetchall()
    }
    additions = [
        ("queue_name", "TEXT"),
        ("staging_path", "TEXT"),
        ("batch_date", "TEXT"),
    ]
    for col, col_type in additions:
        if col not in existing:
            conn.execute(f"ALTER TABLE drive_projects ADD COLUMN {col} {col_type}")
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_drive_projects_queue ON drive_projects(queue_name)"
    )


def init_db() -> None:
    with get_connection() as conn:
        _migrate_schema(conn)


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


# ── Persistent studio job queue ─────────────────────────────────────────────


def create_studio_job(
    job_id: str,
    job_type: str,
    *,
    meta: dict[str, Any] | None = None,
    message: str = "Queued",
) -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO studio_jobs (id, job_type, status, progress, message, meta, created_at, updated_at)
            VALUES (?, ?, 'pending', 0, ?, ?, ?, ?)
            """,
            (job_id, job_type, message, json.dumps(meta or {}), now, now),
        )


def get_studio_job(job_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute("SELECT * FROM studio_jobs WHERE id = ?", (job_id,)).fetchone()
    if not row:
        return None
    data = dict(row)
    data["meta"] = json.loads(data.get("meta") or "{}")
    return data


def update_studio_job(
    job_id: str,
    *,
    status: str | None = None,
    progress: int | None = None,
    message: str | None = None,
    meta: dict[str, Any] | None = None,
    output_path: str | None = None,
    output_url: str | None = None,
    error: str | None = None,
) -> None:
    fields: list[str] = ["updated_at = ?"]
    values: list[Any] = [_utc_now()]
    if status is not None:
        fields.append("status = ?")
        values.append(status)
    if progress is not None:
        fields.append("progress = ?")
        values.append(progress)
    if message is not None:
        fields.append("message = ?")
        values.append(message)
    if meta is not None:
        fields.append("meta = ?")
        values.append(json.dumps(meta))
    if output_path is not None:
        fields.append("output_path = ?")
        values.append(output_path)
    if output_url is not None:
        fields.append("output_url = ?")
        values.append(output_url)
    if error is not None:
        fields.append("error = ?")
        values.append(error)
    if status == "completed":
        fields.append("completed_at = ?")
        values.append(_utc_now())
    values.append(job_id)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE studio_jobs SET {', '.join(fields)} WHERE id = ?",
            values,
        )


def list_studio_jobs(
    job_type: str | None = None,
    *,
    limit: int = 50,
) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if job_type:
            rows = conn.execute(
                """
                SELECT * FROM studio_jobs WHERE job_type = ?
                ORDER BY created_at DESC LIMIT ?
                """,
                (job_type, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM studio_jobs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["meta"] = json.loads(data.get("meta") or "{}")
        out.append(data)
    return out


def count_running_studio_jobs() -> int:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM studio_jobs WHERE status = 'running'"
        ).fetchone()
    return int(row["c"]) if row else 0


# ── Events registry ─────────────────────────────────────────────────────────


def upsert_event(
    event_id: str,
    *,
    category: str,
    event_slug: str,
    event_name: str,
    ideal_row_path: str | None = None,
    status: str = "active",
    metadata: dict[str, Any] | None = None,
) -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO events (id, category, event_slug, event_name, ideal_row_path, status, metadata, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                event_name = excluded.event_name,
                ideal_row_path = excluded.ideal_row_path,
                status = excluded.status,
                metadata = excluded.metadata,
                updated_at = excluded.updated_at
            """,
            (
                event_id,
                category,
                event_slug,
                event_name,
                ideal_row_path,
                status,
                json.dumps(metadata or {}),
                now,
                now,
            ),
        )


def list_events(category: str | None = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if category:
            rows = conn.execute(
                "SELECT * FROM events WHERE category = ? ORDER BY updated_at DESC",
                (category,),
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM events ORDER BY updated_at DESC").fetchall()
    out = []
    for row in rows:
        data = dict(row)
        data["metadata"] = json.loads(data.get("metadata") or "{}")
        out.append(data)
    return out


# ── Content calendar ────────────────────────────────────────────────────────


def upsert_calendar_entry(
    entry_id: str,
    *,
    category: str,
    event_name: str,
    scheduled_at: str,
    post_number: int | None = None,
    event_id: str | None = None,
    status: str = "scheduled",
    notes: str = "",
) -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO calendar_entries
                (id, event_id, category, event_name, post_number, scheduled_at, status, notes, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                scheduled_at = excluded.scheduled_at,
                status = excluded.status,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (
                entry_id,
                event_id,
                category,
                event_name,
                post_number,
                scheduled_at,
                status,
                notes,
                now,
                now,
            ),
        )


def list_calendar_entries(
    *,
    from_date: str | None = None,
    to_date: str | None = None,
) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if from_date and to_date:
            rows = conn.execute(
                """
                SELECT * FROM calendar_entries
                WHERE scheduled_at >= ? AND scheduled_at <= ?
                ORDER BY scheduled_at ASC
                """,
                (from_date, to_date),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM calendar_entries ORDER BY scheduled_at ASC"
            ).fetchall()
    return [dict(row) for row in rows]


def delete_calendar_entry(entry_id: str) -> None:
    with get_connection() as conn:
        conn.execute("DELETE FROM calendar_entries WHERE id = ?", (entry_id,))


# ── Key-value studio settings ───────────────────────────────────────────────


def get_studio_setting(key: str, default: dict[str, Any] | None = None) -> dict[str, Any]:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT value FROM studio_settings WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return default or {}
    try:
        return json.loads(row["value"])
    except json.JSONDecodeError:
        return default or {}


def set_studio_setting(key: str, value: dict[str, Any]) -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO studio_settings (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at
            """,
            (key, json.dumps(value), now),
        )


# ── Drive project review queue ──────────────────────────────────────────────


def upsert_drive_project(row: dict[str, Any]) -> None:
    now = _utc_now()
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO drive_projects (
                id, drive_folder_id, category, event_name, status,
                cover_drive_id, carousel_drive_ids, reel_drive_ids,
                asset_count, parse_warnings, audio_track_id, local_paths,
                reel_job_id, synced_at, created_at, updated_at,
                queue_name, staging_path, batch_date
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(drive_folder_id) DO UPDATE SET
                category = excluded.category,
                event_name = excluded.event_name,
                cover_drive_id = excluded.cover_drive_id,
                carousel_drive_ids = excluded.carousel_drive_ids,
                reel_drive_ids = excluded.reel_drive_ids,
                asset_count = excluded.asset_count,
                parse_warnings = excluded.parse_warnings,
                synced_at = excluded.synced_at,
                updated_at = excluded.updated_at,
                queue_name = COALESCE(drive_projects.queue_name, excluded.queue_name),
                staging_path = COALESCE(drive_projects.staging_path, excluded.staging_path),
                batch_date = COALESCE(drive_projects.batch_date, excluded.batch_date),
                status = CASE
                    WHEN drive_projects.status IN ('approved', 'published', 'review_for_posting', 'processing') THEN drive_projects.status
                    ELSE excluded.status
                END
            """,
            (
                row["id"],
                row["drive_folder_id"],
                row["category"],
                row["event_name"],
                row.get("status", "pending_review"),
                row.get("cover_drive_id"),
                json.dumps(row.get("carousel_drive_ids") or []),
                json.dumps(row.get("reel_drive_ids") or []),
                int(row.get("asset_count") or 0),
                json.dumps(row.get("parse_warnings") or []),
                row.get("audio_track_id"),
                json.dumps(row.get("local_paths") or {}),
                row.get("reel_job_id"),
                row.get("synced_at") or now,
                row.get("created_at") or now,
                now,
                row.get("queue_name"),
                row.get("staging_path"),
                row.get("batch_date"),
            ),
        )


def get_drive_project(project_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM drive_projects WHERE id = ?", (project_id,)
        ).fetchone()
    return _drive_project_from_row(row) if row else None


def get_drive_project_by_folder(drive_folder_id: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT * FROM drive_projects WHERE drive_folder_id = ?", (drive_folder_id,)
        ).fetchone()
    return _drive_project_from_row(row) if row else None


def list_drive_projects(
    *,
    status: str | None = None,
    category: str | None = None,
    queue_name: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("status = ?")
        params.append(status)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if queue_name:
        clauses.append("queue_name = ?")
        params.append(queue_name)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    params.append(limit)
    with get_connection() as conn:
        rows = conn.execute(
            f"SELECT * FROM drive_projects {where} ORDER BY synced_at DESC LIMIT ?",
            params,
        ).fetchall()
    return [_drive_project_from_row(r) for r in rows]


def update_drive_project(project_id: str, **fields: Any) -> None:
    allowed = {
        "status", "audio_track_id", "local_paths", "reel_job_id",
        "cover_drive_id", "carousel_drive_ids", "reel_drive_ids", "parse_warnings",
        "queue_name", "staging_path", "batch_date",
    }
    sets: list[str] = ["updated_at = ?"]
    values: list[Any] = [_utc_now()]
    for key, val in fields.items():
        if key not in allowed:
            continue
        sets.append(f"{key} = ?")
        if key in ("carousel_drive_ids", "reel_drive_ids", "local_paths", "parse_warnings"):
            values.append(json.dumps(val))
        else:
            values.append(val)
    values.append(project_id)
    with get_connection() as conn:
        conn.execute(
            f"UPDATE drive_projects SET {', '.join(sets)} WHERE id = ?",
            values,
        )


def _drive_project_from_row(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    for key in ("carousel_drive_ids", "reel_drive_ids", "parse_warnings"):
        try:
            data[key] = json.loads(data.get(key) or "[]")
        except json.JSONDecodeError:
            data[key] = []
    try:
        data["local_paths"] = json.loads(data.get("local_paths") or "{}")
    except json.JSONDecodeError:
        data["local_paths"] = {}
    return data


def count_drive_projects_by_status(status: str, *, batch_date: str | None = None) -> int:
    clauses = ["status = ?"]
    params: list[Any] = [status]
    if batch_date:
        clauses.append("batch_date = ?")
        params.append(batch_date)
    where = " AND ".join(clauses)
    with get_connection() as conn:
        row = conn.execute(
            f"SELECT COUNT(*) AS n FROM drive_projects WHERE {where}",
            params,
        ).fetchone()
    return int(row["n"]) if row else 0


def list_unbatched_drive_folder_ids() -> set[str]:
    """Drive folder IDs already batch-processed or in-flight."""
    with get_connection() as conn:
        rows = conn.execute(
            """
            SELECT drive_folder_id FROM drive_projects
            WHERE status IN ('review_for_posting', 'approved', 'published', 'processing')
            """
        ).fetchall()
    return {r["drive_folder_id"] for r in rows}
