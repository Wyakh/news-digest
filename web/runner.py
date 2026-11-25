"""
web/runner.py

Subprocess manager for scraper.py and ai/ai_engine.py.
Single-job-at-a-time lock; no concurrent runs.

Spawns subprocesses with stdout/stderr redirected to a log file in
data/run_logs/. The web app reads that log for live status display.
"""

import json
import os
import re
import subprocess
import sys
import threading
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRAPER_PATH = PROJECT_ROOT / "scraper.py"
AI_ENGINE_PATH = PROJECT_ROOT / "ai" / "ai_engine.py"
LOG_DIR = PROJECT_ROOT / "data" / "run_logs"
DIGESTS_DIR = PROJECT_ROOT / "data" / "digests"
SCRAPE_STATUS_PATH = PROJECT_ROOT / "data" / "scrape_status.json"

DIGEST_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})\.md$")

# Auto-create log dir
LOG_DIR.mkdir(parents=True, exist_ok=True)

_lock = threading.Lock()
_state = {
    "active": False,
    "job": None,            # "scraper" | "ai" | "both"
    "phase": None,          # for "both": "scraper" | "ai"
    "started_at": None,
    "completed_at": None,
    "log_path": None,
    "exit_code": None,
    "error_msg": None,
    "override_mode": None,  # "default" | "no_override"
}


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def _set_state(**kwargs):
    with _lock:
        _state.update(kwargs)


def _make_log_path(job):
    timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M%S")
    return LOG_DIR / f"{job}-{timestamp}.log"


# ── Recency checks ──────────────────────────────────────────────────────────
def _scraper_recency_hours(now):
    if not SCRAPE_STATUS_PATH.exists():
        return None
    try:
        with open(SCRAPE_STATUS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        return None
    if not data:
        return None
    timestamps = []
    for s in data:
        ts = s.get("timestamp", "")
        try:
            dt = datetime.fromisoformat(ts)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            timestamps.append(dt)
        except ValueError:
            continue
    if not timestamps:
        return None
    return (now - max(timestamps)).total_seconds() / 3600.0


def _ai_recency_hours(now):
    if not DIGESTS_DIR.exists():
        return None
    candidates = []
    for path in DIGESTS_DIR.iterdir():
        m = DIGEST_FILENAME_RE.match(path.name)
        if not m:
            continue
        try:
            dt = datetime(*[int(g) for g in m.groups()]).replace(tzinfo=timezone.utc)
            candidates.append(dt)
        except ValueError:
            continue
    if not candidates:
        return None
    return (now - max(candidates)).total_seconds() / 3600.0


def _find_latest_digest_run_id():
    if not DIGESTS_DIR.exists():
        return None
    latest = None
    for path in DIGESTS_DIR.iterdir():
        m = DIGEST_FILENAME_RE.match(path.name)
        if not m:
            continue
        try:
            dt = datetime(*[int(g) for g in m.groups()])
            candidate = (dt, path.stem)
            if latest is None or candidate > latest:
                latest = candidate
        except ValueError:
            continue
    return latest[1] if latest is not None else None


def precheck(job):
    """
    Smart 12-hour check: scraper checks scrape recency, ai checks digest
    recency, both checks both. Returns dict with needs_confirm flag.
    """
    result = {
        "scraper_within_12h": False,
        "ai_within_12h": False,
        "scraper_age_hours": None,
        "ai_age_hours": None,
    }
    now = datetime.now(timezone.utc)

    if job in ("scraper", "both"):
        age = _scraper_recency_hours(now)
        if age is not None:
            result["scraper_age_hours"] = round(age, 2)
            if age < 12:
                result["scraper_within_12h"] = True

    if job in ("ai", "both"):
        age = _ai_recency_hours(now)
        if age is not None:
            result["ai_age_hours"] = round(age, 2)
            if age < 12:
                result["ai_within_12h"] = True

    result["needs_confirm"] = result["scraper_within_12h"] or result["ai_within_12h"]
    return result


# ── Job spawning ────────────────────────────────────────────────────────────
def start(job, override_mode):
    """Spawn a job. Returns (ok, message)."""
    if job not in ("scraper", "ai", "both"):
        return False, f"Unknown job: {job}"
    if override_mode not in ("auto", "override", "no_override"):
        return False, f"Unknown override mode: {override_mode}"

    with _lock:
        if _state["active"]:
            return False, "A run is already active"
        log_path = _make_log_path(job)
        _state.update({
            "active": True,
            "job": job,
            "phase": None,
            "started_at": _now_iso(),
            "completed_at": None,
            "log_path": str(log_path),
            "exit_code": None,
            "error_msg": None,
            "override_mode": override_mode,
        })

    thread = threading.Thread(
        target=_run_thread,
        args=(job, override_mode, log_path),
        daemon=True,
    )
    thread.start()
    return True, "Started"


def _build_args(script_path, override_mode):
    args = [sys.executable, str(script_path)]
    if override_mode == "no_override":
        args.append("--no-override")
    return args


def _run_subprocess(script_path, override_mode, logf):
    args = _build_args(script_path, override_mode)
    # Force UTF-8 for the subprocess's stdout/stderr.
    # Without this, Windows defaults to the system code page (GBK on zh-CN),
    # which crashes on Unicode characters like ✓ when piping to a file.
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    proc = subprocess.Popen(
        args,
        stdout=logf,
        stderr=subprocess.STDOUT,
        cwd=str(PROJECT_ROOT),
        env=env,
    )
    proc.wait()
    return proc.returncode


def _run_thread(job, override_mode, log_path):
    try:
        with open(log_path, "ab") as logf:
            if job == "scraper":
                _set_state(phase="scraper")
                rc = _run_subprocess(SCRAPER_PATH, override_mode, logf)
                _finish(rc)

            elif job == "ai":
                _set_state(phase="ai")
                rc = _run_subprocess(AI_ENGINE_PATH, override_mode, logf)
                _finish(rc)

            elif job == "both":
                _set_state(phase="scraper")
                logf.write(b"\n=== SCRAPER PHASE ===\n")
                logf.flush()
                rc = _run_subprocess(SCRAPER_PATH, override_mode, logf)
                if rc != 0:
                    _finish(rc, error_msg="Scraper failed; AI engine not started")
                    return
                _set_state(phase="ai")
                logf.write(b"\n=== AI ENGINE PHASE ===\n")
                logf.flush()
                rc = _run_subprocess(AI_ENGINE_PATH, override_mode, logf)
                _finish(rc)
    except Exception as e:
        _finish(-1, error_msg=f"Runner exception: {e}")


def _finish(exit_code, error_msg=None):
    _set_state(
        active=False,
        completed_at=_now_iso(),
        exit_code=exit_code,
        error_msg=error_msg,
        phase=None,
    )


# ── Status ──────────────────────────────────────────────────────────────────
def _log_tail(n=10):
    with _lock:
        log_path = _state["log_path"]
    if not log_path:
        return []
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            if n > 0:
                lines = deque(f, maxlen=n)
            else:
                # Preserve the original slicing behavior for zero/negative n.
                lines = f.readlines()[-n:]
        return [line.rstrip() for line in lines]
    except OSError:
        return []


def status_response():
    with _lock:
        s = dict(_state)

    elapsed = None
    if s["started_at"]:
        try:
            start_dt = datetime.fromisoformat(s["started_at"])
            end_dt = (
                datetime.fromisoformat(s["completed_at"])
                if s["completed_at"] else datetime.now(timezone.utc)
            )
            elapsed = int((end_dt - start_dt).total_seconds())
        except ValueError:
            pass

    # Build a result link for completed runs
    result_link = None
    if not s["active"] and s["exit_code"] == 0 and s["job"]:
        if s["job"] in ("ai", "both"):
            run_id = _find_latest_digest_run_id()
            if run_id:
                result_link = {"label": f"View digest {run_id}", "url": f"/digest/{run_id}"}
        elif s["job"] == "scraper":
            result_link = {"label": "View status page", "url": "/status"}

    return {
        "active": s["active"],
        "job": s["job"],
        "phase": s["phase"],
        "started_at": s["started_at"],
        "completed_at": s["completed_at"],
        "exit_code": s["exit_code"],
        "error_msg": s["error_msg"],
        "override_mode": s["override_mode"],
        "elapsed_seconds": elapsed,
        "log_tail": _log_tail(10),
        "result_link": result_link,
    }

# ── Delete most recent AI run ───────────────────────────────────────────────
def delete_latest_ai_run():
    """
    Delete the most recent AI digest:
      - data/digests/<run_id>.md
      - data/digests/<run_id>.json
      - data/digests/<run_id>-summary.txt
      - matching entry in data/cost_log.json

    PDF files are never touched (they are user-generated).
    Returns (ok, message, run_id_or_None).
    """
    # Refuse if a job is currently running.
    with _lock:
        if _state["active"]:
            return False, "Cannot delete while a run is in progress", None

    run_id = _find_latest_digest_run_id()
    if not run_id:
        return False, "No AI digest found to delete", None

    deleted = []
    not_found = []

    for suffix in (".md", ".json", "-summary.txt"):
        path = DIGESTS_DIR / f"{run_id}{suffix}"
        if path.exists():
            try:
                path.unlink()
                deleted.append(path.name)
            except OSError as e:
                return False, f"Could not delete {path.name}: {e}", run_id
        else:
            not_found.append(path.name)

    # Remove matching cost_log.json entry.
    cost_log_path = PROJECT_ROOT / "data" / "cost_log.json"
    cost_entry_removed = False
    if cost_log_path.exists():
        try:
            with open(cost_log_path, "r", encoding="utf-8") as f:
                entries = json.load(f)
            new_entries = [e for e in entries if e.get("run_id") != run_id]
            if len(new_entries) != len(entries):
                with open(cost_log_path, "w", encoding="utf-8") as f:
                    json.dump(new_entries, f, ensure_ascii=False, indent=2)
                cost_entry_removed = True
        except (OSError, json.JSONDecodeError) as e:
            return False, f"Files deleted but could not update cost_log.json: {e}", run_id

    parts = [f"Deleted {len(deleted)} file(s) for run {run_id}"]
    if cost_entry_removed:
        parts.append("cost_log entry removed")
    if not_found:
        parts.append(f"(not found: {', '.join(not_found)})")
    return True, "; ".join(parts), run_id