#!/usr/bin/env python3
"""Autonomous self-healing loop for the Drive sync → folder select → 3-post pipeline.

Runs the end-to-end suite. On any failure it captures stdout/stderr/traceback,
sends that into Cursor Agent (the code-editing context) to apply a patch,
commits the fix, restarts the local backend, and re-runs. Exits 0 only when
the suite is fully green.

Requires CURSOR_API_KEY (https://cursor.com/dashboard/integrations) or an
already-authenticated `cursor agent` CLI session.

Run:
  source .venv/bin/activate && python auto_fix_loop.py
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".auto_fix_loop"
LOG_DIR = STATE_DIR / "logs"
PID_FILE = STATE_DIR / "backend.pid"
SUITE_PYTHON = sys.executable

BACKEND_HOST = "127.0.0.1"
BACKEND_PORT = 8765
HEALTH_URL = "http://{0}:{1}/api/health".format(BACKEND_HOST, BACKEND_PORT)

SECRET_NAME_MARKERS = (
    ".env",
    "credentials.json",
    "client_secret",
    "token.json",
    "drive_oauth_token",
    "service-account",
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("auto_fix")


class SuiteFailure(RuntimeError):
    def __init__(self, summary: str, log_path: Path, fingerprint: str) -> None:
        super().__init__(summary)
        self.summary = summary
        self.log_path = log_path
        self.fingerprint = fingerprint


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def run_cmd(
    argv: List[str],
    *,
    cwd: Optional[Path] = None,
    timeout: Optional[float] = None,
    env: Optional[Dict[str, str]] = None,
    check: bool = False,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        argv,
        cwd=str(cwd or ROOT),
        timeout=timeout,
        env=env,
        text=True,
        capture_output=True,
        check=check,
    )


def combined_output(proc: subprocess.CompletedProcess) -> str:
    parts = []
    if proc.stdout:
        parts.append(proc.stdout)
    if proc.stderr:
        parts.append(proc.stderr)
    return "\n".join(parts).strip()


def find_cursor_cli() -> Optional[Path]:
    found = shutil.which("cursor")
    candidates = [
        Path(found) if found else None,
        Path("/Applications/Cursor.app/Contents/Resources/app/bin/cursor"),
        Path.home() / ".local" / "bin" / "cursor",
    ]
    for path in candidates:
        if path and path.is_file() and os.access(path, os.X_OK):
            return path
    return None


def suite_commands() -> List[Tuple[str, List[str], float]]:
    """Name, argv, timeout-seconds for each pipeline test."""
    return [
        (
            "app_flow",
            [SUITE_PYTHON, str(ROOT / "test_app_flow.py")],
            180.0,
        ),
        (
            "agent_pipeline",
            [SUITE_PYTHON, str(ROOT / "run_agent_tests.py"), "--once"],
            180.0,
        ),
    ]


def write_failure_log(name: str, proc: subprocess.CompletedProcess, extra: str = "") -> Path:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / "{0}_{1}.log".format(utc_stamp(), name)
    body = [
        "command: {0}".format(" ".join(proc.args if isinstance(proc.args, list) else [str(proc.args)])),
        "exit_code: {0}".format(proc.returncode),
        "",
        "----- STDOUT -----",
        proc.stdout or "",
        "",
        "----- STDERR -----",
        proc.stderr or "",
    ]
    if extra:
        body.extend(["", "----- EXTRA -----", extra])
    path.write_text("\n".join(body), encoding="utf-8")
    return path


def fingerprint_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()[:16]


def run_suite() -> None:
    """Raise SuiteFailure on the first failing or crashing test."""
    for name, argv, timeout in suite_commands():
        log.info("Running suite step: %s", name)
        try:
            proc = run_cmd(argv, timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            fake = subprocess.CompletedProcess(argv, 124, stdout=exc.stdout or "", stderr=exc.stderr or "")
            path = write_failure_log(name, fake, extra="TIMEOUT after {0:.0f}s".format(timeout))
            raise SuiteFailure(
                "{0} timed out after {1:.0f}s".format(name, timeout),
                path,
                fingerprint_text(name + ":timeout"),
            ) from exc
        except Exception:
            import traceback

            tb = traceback.format_exc()
            fake = subprocess.CompletedProcess(argv, 1, stdout="", stderr=tb)
            path = write_failure_log(name, fake)
            raise SuiteFailure("{0} threw before completing".format(name), path, fingerprint_text(tb)) from None

        if proc.returncode != 0:
            path = write_failure_log(name, proc)
            output = combined_output(proc)
            raise SuiteFailure(
                "{0} failed with exit {1}".format(name, proc.returncode),
                path,
                fingerprint_text(output or str(proc.returncode)),
            )
        log.info("OK  %s", name)


def build_fix_prompt(failure: SuiteFailure, *, attempt: int, prev_fingerprint: Optional[str]) -> str:
    log_text = failure.log_path.read_text(encoding="utf-8", errors="replace")
    if len(log_text) > 24000:
        log_text = log_text[-24000:]
    same = (
        "The previous patch did NOT change this failure fingerprint. "
        "Do not repeat the same edit. Dig into the actual root cause.\n"
        if prev_fingerprint == failure.fingerprint
        else ""
    )
    return """You are the code-editing agent for VV Luxe Studio.

Attempt {attempt}. A local end-to-end test for Google Drive sync, manual folder
selection, and the 3-post generation pipeline failed. Apply a real code patch
that makes the suite pass. Do not weaken, skip, or delete tests.

Constraints:
- Do NOT git commit, git push, or amend. The orchestrator commits after you finish.
- Do NOT modify auto_fix_loop.py, .env, credentials, or OAuth token files.
- Do NOT pass redirect_uri= into Flow.authorization_url() (hardcoded HTTPS callback).
- Keep the pipeline: Browse Drive / folder ID → POST /api/studio/backlog/process-folder
  → DailyBacklogWorker.process_folder → materialize_package → Ideal Row Post_1/2/3.
- Prefer the smallest correct fix.

{same}Failure summary: {summary}
Failure fingerprint: {fingerprint}
Log file: {log_path}

----- captured traceback / logs -----
{log_text}
----- end -----

After editing, stop. The orchestrator will commit, restart the backend, and re-run
test_app_flow.py plus run_agent_tests.py --once.
""".format(
        attempt=attempt,
        same=same,
        summary=failure.summary,
        fingerprint=failure.fingerprint,
        log_path=failure.log_path,
        log_text=log_text,
    )


def apply_patch_via_cursor_cli(
    prompt: str,
    *,
    cursor_bin: Path,
    chat_id: Optional[str],
    model: str,
    timeout: float,
) -> str:
    env = os.environ.copy()
    argv = [
        str(cursor_bin),
        "agent",
        "--print",
        "--force",
        "--trust",
        "--workspace",
        str(ROOT),
        "--output-format",
        "text",
        "--sandbox",
        "disabled",
    ]
    if model:
        argv.extend(["--model", model])
    if chat_id:
        argv.extend(["--resume", chat_id])
    argv.append(prompt)

    log.info("Invoking Cursor Agent CLI (%s)…", cursor_bin)
    proc = run_cmd(argv, timeout=timeout, env=env)
    output = combined_output(proc)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "last_agent_output.txt").write_text(output, encoding="utf-8")
    if proc.returncode != 0:
        raise RuntimeError(
            "Cursor Agent CLI exited {0}: {1}".format(proc.returncode, output[-4000:])
        )
    return output


def apply_patch_via_sdk(prompt: str, *, model: str, timeout: float) -> str:
    """Local Cursor SDK agent (Python 3.10+ and cursor-sdk required)."""
    try:
        from cursor_sdk import Agent, AgentOptions, CursorAgentError, LocalAgentOptions
    except Exception as exc:
        raise RuntimeError("cursor-sdk is not importable: {0}".format(exc)) from exc

    api_key = (os.environ.get("CURSOR_API_KEY") or "").strip() or None
    log.info("Invoking Cursor SDK Agent.prompt (local cwd=%s)…", ROOT)
    started = time.time()
    try:
        result = Agent.prompt(
            prompt,
            AgentOptions(
                api_key=api_key,
                model=model or "composer-2.5",
                name="vv-luxe-auto-fix",
                local=LocalAgentOptions(cwd=str(ROOT), setting_sources=["project"]),
            ),
        )
    except CursorAgentError as err:
        retryable = getattr(err, "is_retryable", False)
        raise RuntimeError(
            "Cursor SDK failed to start (retryable={0}): {1}".format(retryable, err)
        ) from err

    elapsed = time.time() - started
    if elapsed > timeout:
        log.warning("SDK run finished after timeout budget (%.0fs)", elapsed)
    status = getattr(result, "status", None)
    text = getattr(result, "result", None) or ""
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    (STATE_DIR / "last_agent_output.txt").write_text(text, encoding="utf-8")
    if status == "error":
        raise RuntimeError("Cursor SDK run error: {0}".format(text[-4000:]))
    if status not in (None, "finished"):
        raise RuntimeError("Cursor SDK run status={0}".format(status))
    return text


def apply_patch(prompt: str, args: argparse.Namespace, chat_id: Optional[str]) -> Optional[str]:
    """Return a chat id to resume when using the CLI."""
    cursor_bin = find_cursor_cli()
    errors: List[str] = []

    if args.backend == "sdk" or (args.backend == "auto" and sys.version_info >= (3, 10)):
        try:
            apply_patch_via_sdk(prompt, model=args.model, timeout=args.agent_timeout)
            return chat_id
        except Exception as exc:
            errors.append("sdk: {0}".format(exc))
            if args.backend == "sdk":
                raise

    if args.backend in ("auto", "cli"):
        if not cursor_bin:
            errors.append("cli: cursor binary not found")
        else:
            apply_patch_via_cursor_cli(
                prompt,
                cursor_bin=cursor_bin,
                chat_id=chat_id,
                model=args.model,
                timeout=args.agent_timeout,
            )
            return chat_id

    raise RuntimeError(
        "No code-editing backend available. Install Cursor.app (cursor agent CLI) "
        "or `pip install cursor-sdk` on Python 3.10+ and set CURSOR_API_KEY. "
        "Details: {0}".format(" | ".join(errors) or "none")
    )


def is_secret_path(path: str) -> bool:
    lowered = path.lower()
    return any(marker in lowered for marker in SECRET_NAME_MARKERS)


def changed_files() -> List[str]:
    proc = run_cmd(["git", "status", "--porcelain"], check=True)
    files: List[str] = []
    for raw in (proc.stdout or "").splitlines():
        if not raw.strip():
            continue
        # status is 2 chars + space; handle rename "R  a -> b"
        path = raw[3:].strip()
        if " -> " in path:
            path = path.split(" -> ", 1)[1]
        files.append(path)
    return files


def commit_fix(attempt: int, failure: SuiteFailure) -> bool:
    files = changed_files()
    if not files:
        log.warning("Agent produced no git changes; skipping commit.")
        return False

    blocked = [p for p in files if is_secret_path(p)]
    if blocked:
        raise RuntimeError("Refusing to commit secret-like paths: {0}".format(", ".join(blocked)))

    skip = {".auto_fix_loop", "auto_fix_loop.py"}
    staged: List[str] = []
    for path in files:
        if path.startswith(".auto_fix_loop/") or path in skip:
            continue
        staged.append(path)

    if not staged:
        log.warning("Only loop artifacts changed; skipping commit.")
        return False

    run_cmd(["git", "add", "--"] + staged, check=True)
    message = (
        "fix: heal Drive pipeline failure ({fingerprint})\n"
        "\n"
        "Auto-fix loop attempt {attempt}. {summary}\n".format(
            fingerprint=failure.fingerprint,
            attempt=attempt,
            summary=failure.summary,
        )
    )
    commit = run_cmd(["git", "commit", "-m", message])
    if commit.returncode != 0:
        output = combined_output(commit)
        if "nothing to commit" in output.lower():
            return False
        raise RuntimeError("git commit failed: {0}".format(output))
    log.info("Committed fix for attempt %d (%s)", attempt, failure.fingerprint)
    return True


def listening_pid(port: int) -> Optional[int]:
    proc = run_cmd(["lsof", "-tiTCP:{0}".format(port), "-sTCP:LISTEN"])
    text = (proc.stdout or "").strip()
    if proc.returncode != 0 or not text:
        return None
    try:
        return int(text.splitlines()[0])
    except ValueError:
        return None


def wait_for_health(timeout: float) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            import urllib.request

            with urllib.request.urlopen(HEALTH_URL, timeout=2) as resp:
                if getattr(resp, "status", 200) == 200:
                    return True
        except Exception:
            time.sleep(0.5)
    return False


def restart_backend(*, start_if_missing: bool, health_timeout: float) -> None:
    existing = listening_pid(BACKEND_PORT)
    python = ROOT / ".venv" / "bin" / "python"
    if not python.is_file():
        python = Path(SUITE_PYTHON)

    if existing is None and not start_if_missing:
        log.info("No backend on :%s — skip restart (tests boot in-process).", BACKEND_PORT)
        return

    if existing is not None:
        log.info("Restarting backend pid %s on :%s", existing, BACKEND_PORT)
        try:
            os.kill(existing, signal.SIGTERM)
        except OSError as exc:
            log.warning("Could not signal pid %s: %s", existing, exc)
        deadline = time.time() + 15
        while time.time() < deadline and listening_pid(BACKEND_PORT) == existing:
            time.sleep(0.2)

    STATE_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "backend_restart.log"
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handle = open(log_path, "ab")
    spawned = subprocess.Popen(
        [str(python), str(ROOT / "backend" / "main.py")],
        cwd=str(ROOT),
        stdout=handle,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    PID_FILE.write_text(str(spawned.pid), encoding="utf-8")
    log.info("Started backend pid %s — waiting for %s", spawned.pid, HEALTH_URL)
    if wait_for_health(health_timeout):
        log.info("Backend healthy.")
        return
    log.warning("Backend did not become healthy within %.0fs (continuing to tests).", health_timeout)


def push_branch() -> None:
    branch = run_cmd(["git", "rev-parse", "--abbrev-ref", "HEAD"], check=True).stdout.strip()
    if branch in {"main", "master"}:
        log.warning("Refusing to auto-push %s. Pass a feature branch if you want remote deploy.", branch)
        return
    log.info("Pushing %s to origin (no force)…", branch)
    proc = run_cmd(["git", "push", "-u", "origin", "HEAD"])
    if proc.returncode != 0:
        raise RuntimeError("git push failed: {0}".format(combined_output(proc)))


def parse_args(argv: Optional[List[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Self-healing Drive pipeline test loop")
    parser.add_argument("--max-iterations", type=int, default=0, help="0 = loop until green")
    parser.add_argument("--retry-sec", type=float, default=3.0)
    parser.add_argument("--agent-timeout", type=float, default=600.0)
    parser.add_argument("--model", default="composer-2.5")
    parser.add_argument(
        "--backend",
        choices=("auto", "cli", "sdk"),
        default="auto",
        help="Code-editing backend: Cursor CLI, Python SDK, or auto-detect",
    )
    parser.add_argument("--no-commit", action="store_true", help="Apply patches but do not git commit")
    parser.add_argument("--push", action="store_true", help="Push the current non-main branch after a commit")
    parser.add_argument("--no-restart", action="store_true", help="Do not restart the local backend")
    parser.add_argument(
        "--start-service",
        action="store_true",
        help="Boot backend/main.py if nothing is listening on :8765",
    )
    parser.add_argument("--health-timeout", type=float, default=60.0)
    parser.add_argument("--tests-only", action="store_true", help="Run the suite once and exit (no patch loop)")
    return parser.parse_args(argv)


def main(argv: Optional[List[str]] = None) -> int:
    args = parse_args(argv)
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    if args.tests_only:
        try:
            run_suite()
        except SuiteFailure as failure:
            log.error("%s — see %s", failure.summary, failure.log_path)
            return 1
        log.info("SUCCESS — suite green.")
        return 0

    attempt = 0
    prev_fingerprint: Optional[str] = None
    chat_id: Optional[str] = None
    history_path = STATE_DIR / "history.jsonl"

    while True:
        attempt += 1
        log.info("════ Iteration %d — running Drive / folder / post-generation suite", attempt)
        try:
            run_suite()
            log.info("SUCCESS — zero test failures after %d iteration(s).", attempt)
            return 0
        except SuiteFailure as failure:
            log.error("%s (fingerprint=%s)", failure.summary, failure.fingerprint)
            log.error("Captured logs: %s", failure.log_path)
            with history_path.open("a", encoding="utf-8") as history:
                history.write(
                    json.dumps(
                        {
                            "attempt": attempt,
                            "summary": failure.summary,
                            "fingerprint": failure.fingerprint,
                            "log": str(failure.log_path),
                            "at": utc_stamp(),
                        }
                    )
                    + "\n"
                )

            if args.max_iterations and attempt >= args.max_iterations:
                log.error("Still failing after %d iteration(s).", attempt)
                return 1

            prompt = build_fix_prompt(
                failure, attempt=attempt, prev_fingerprint=prev_fingerprint
            )
            try:
                chat_id = apply_patch(prompt, args, chat_id)
            except Exception:
                log.exception("Patch step failed; will retry the loop.")
                time.sleep(args.retry_sec)
                prev_fingerprint = failure.fingerprint
                continue

            if not args.no_commit:
                try:
                    committed = commit_fix(attempt, failure)
                except Exception:
                    log.exception("Commit failed; will retry the loop.")
                    time.sleep(args.retry_sec)
                    prev_fingerprint = failure.fingerprint
                    continue
                if committed and args.push:
                    try:
                        push_branch()
                    except Exception:
                        log.exception("Push/deploy failed; tests will still re-run locally.")

            if not args.no_restart:
                try:
                    restart_backend(
                        start_if_missing=args.start_service,
                        health_timeout=args.health_timeout,
                    )
                except Exception:
                    log.exception("Backend restart failed; continuing to re-test.")

            prev_fingerprint = failure.fingerprint
            time.sleep(args.retry_sec)


if __name__ == "__main__":
    sys.exit(main())
