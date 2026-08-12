"""Atomic JSON persistence with file locking for studio configuration."""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

try:
    import fcntl  # Unix file locking (macOS/Linux)
except ImportError:  # pragma: no cover
    fcntl = None  # type: ignore


def read_json(path: Path, default: dict[str, Any] | list[Any] | None = None) -> Any:
    """Read JSON file; return default if missing or corrupt."""
    if not path.is_file():
        return default if default is not None else {}
    try:
        with path.open("r", encoding="utf-8") as fh:
            if fcntl:
                fcntl.flock(fh.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(fh)
            finally:
                if fcntl:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("Failed to read JSON %s: %s", path, exc)
        return default if default is not None else {}


def write_json(path: Path, data: Any, *, indent: int = 2) -> None:
    """Atomically write JSON (temp file + rename) with exclusive lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.stem}_",
        suffix=".tmp",
    )
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            if fcntl:
                fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            json.dump(data, fh, indent=indent)
            fh.flush()
            os.fsync(fh.fileno())
        tmp_path.replace(path)
    except Exception:
        tmp_path.unlink(missing_ok=True)
        raise


def update_json(path: Path, updater: Callable[[Any], Any], default: dict[str, Any] | None = None) -> Any:
    """Read-modify-write JSON atomically."""
    current = read_json(path, default=default or {})
    updated = updater(current)
    write_json(path, updated)
    return updated
