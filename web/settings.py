"""
News Digest Web UI — Step 5: Settings page helpers.

Read / write / backup / restore primitives for the editable config and prompt
files. No Flask imports; routes will live in app.py and call into here.

Backup layout: web/backups/<file_key>/<original_filename>.<YYYYMMDD-HHMMSS>.bak
Up to MAX_BACKUPS per file; oldest are auto-pruned on each save.
"""

import json
import re
from datetime import datetime
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# settings.py lives at <project>\web\settings.py
PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"
BACKUPS_DIR = PROJECT_ROOT / "web" / "backups"

MAX_BACKUPS = 10

# Backup filename pattern: <original_filename>.<YYYYMMDD-HHMMSS>.bak
# Example: sources.json.20260503-143022.bak
BACKUP_TIMESTAMP_FMT = "%Y%m%d-%H%M%S"
BACKUP_FILENAME_RE = re.compile(r"^(.+)\.(\d{8}-\d{6})\.bak$")

# ---------------------------------------------------------------------------
# File registry
# ---------------------------------------------------------------------------
# file_key is the URL-safe identifier and the ONLY user-supplied value that
# participates in path resolution. Anything not in this dict is rejected.
FILE_REGISTRY = {
    "sources": {
        "filename": "sources.json",
        "path": CONFIG_DIR / "sources.json",
        "kind": "json",
        "description": "Per-source scraper configuration. Each entry has name, method, enabled, plus method-specific fields.",
    },
    "config": {
        "filename": "config.json",
        "path": CONFIG_DIR / "config.json",
        "kind": "json",
        "description": "API keys, per-layer model assignments, and per-layer LLM settings (max_tokens, reasoning_effort).",
    },
    "pricing": {
        "filename": "pricing.json",
        "path": CONFIG_DIR / "pricing.json",
        "kind": "json",
        "description": "Per-model input/output prices ($/1M tokens). Costs of past runs are locked in cost_log.json and are not affected by edits here.",
    },
    "prompt_batch_western": {
        "filename": "prompt_batch_western.txt",
        "path": CONFIG_DIR / "prompt_batch_western.txt",
        "kind": "text",
        "description": "Layer 2 prompt for the Western batch (Reuters, FT, Straits Times).",
    },
    "prompt_batch_huanqiu": {
        "filename": "prompt_batch_huanqiu.txt",
        "path": CONFIG_DIR / "prompt_batch_huanqiu.txt",
        "kind": "text",
        "description": "Layer 2 prompt for the Huanqiu batch (state-aligned Chinese).",
    },
    "prompt_batch_chinese_indep": {
        "filename": "prompt_batch_chinese_indep.txt",
        "path": CONFIG_DIR / "prompt_batch_chinese_indep.txt",
        "kind": "text",
        "description": "Layer 2 prompt for the independent Chinese batch (Caixin + Zaobao).",
    },
    "prompt_synthesis": {
        "filename": "prompt_synthesis.txt",
        "path": CONFIG_DIR / "prompt_synthesis.txt",
        "kind": "text",
        "description": "Layer 3 prompt — cross-batch merging, continuity, and tier assignment (four explicit stages).",
    },
    "prompt_writer": {
        "filename": "prompt_writer.txt",
        "path": CONFIG_DIR / "prompt_writer.txt",
        "kind": "text",
        "description": "Layer 4 prompt — Markdown digest generation. Contains {{TIMESTAMP}} and {{PAST_RUNS}} placeholders that writer.py substitutes at run time; save them verbatim.",
    },
}


# ---------------------------------------------------------------------------
# Schema validators (per JSON file)
# ---------------------------------------------------------------------------
# Each returns None if data is valid, else a human-readable error message.
# Note: in Python, isinstance(True, int) is True — bools must be excluded
# explicitly when checking numerics.

_VALID_REASONING_EFFORTS = ("low", "medium", "high")


def _is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _validate_config_json(data):
    """Schema for config/config.json."""
    if not isinstance(data, dict):
        return "Top-level must be an object."

    for key in ("openai_api_key", "anthropic_api_key"):
        if key in data and not isinstance(data[key], str):
            return f"'{key}' must be a string (use \"\" if unused)."

    if "models" not in data:
        return "Missing required key: 'models'."
    if not isinstance(data["models"], dict):
        return "'models' must be an object."
    for layer in ("batch", "synthesis", "writer"):
        if layer not in data["models"]:
            return f"Missing required key: 'models.{layer}'."
        v = data["models"][layer]
        if not isinstance(v, str) or not v:
            return f"'models.{layer}' must be a non-empty string."

    if "llm_settings" not in data:
        return "Missing required key: 'llm_settings'."
    if not isinstance(data["llm_settings"], dict):
        return "'llm_settings' must be an object."
    for layer in ("batch", "synthesis", "writer"):
        if layer not in data["llm_settings"]:
            return f"Missing required key: 'llm_settings.{layer}'."
        cfg = data["llm_settings"][layer]
        if not isinstance(cfg, dict):
            return f"'llm_settings.{layer}' must be an object."
        if "max_tokens" not in cfg:
            return f"Missing required key: 'llm_settings.{layer}.max_tokens'."
        if (not isinstance(cfg["max_tokens"], int)
                or isinstance(cfg["max_tokens"], bool)
                or cfg["max_tokens"] <= 0):
            return f"'llm_settings.{layer}.max_tokens' must be a positive integer."
        if "reasoning_effort" in cfg:
            if cfg["reasoning_effort"] not in _VALID_REASONING_EFFORTS:
                return (f"'llm_settings.{layer}.reasoning_effort' must be one of "
                        f"'low', 'medium', 'high'.")
        if "temperature" in cfg and not _is_number(cfg["temperature"]):
            return f"'llm_settings.{layer}.temperature' must be a number."
    return None


def _validate_pricing_json(data):
    """Schema for config/pricing.json."""
    if not isinstance(data, dict):
        return "Top-level must be an object mapping model names to price entries."
    for model, entry in data.items():
        if not isinstance(entry, dict):
            return f"Entry for '{model}' must be an object."
        for field in ("input_per_1m", "output_per_1m"):
            if field not in entry:
                return f"Entry for '{model}' missing required field '{field}'."
            if not _is_number(entry[field]) or entry[field] < 0:
                return f"Entry for '{model}': '{field}' must be a non-negative number."
    return None


def _validate_sources_json(data):
    """Schema for config/sources.json. Method-specific fields vary, so only
    the universal minimum (name, method, enabled) is enforced."""
    if not isinstance(data, dict):
        return "Top-level must be an object."
    if "sources" not in data:
        return "Missing required key: 'sources'."
    if not isinstance(data["sources"], list):
        return "'sources' must be an array."
    for i, entry in enumerate(data["sources"]):
        if not isinstance(entry, dict):
            return f"sources[{i}] must be an object."
        for field in ("name", "method", "enabled"):
            if field not in entry:
                return f"sources[{i}] missing required field '{field}'."
        if not isinstance(entry["name"], str) or not entry["name"]:
            return f"sources[{i}].name must be a non-empty string."
        if not isinstance(entry["method"], str) or not entry["method"]:
            return f"sources[{i}].method must be a non-empty string."
        if not isinstance(entry["enabled"], bool):
            return f"sources[{i}].enabled must be true or false."
    return None


_SCHEMA_VALIDATORS = {
    "config": _validate_config_json,
    "pricing": _validate_pricing_json,
    "sources": _validate_sources_json,
}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------
def _entry(file_key):
    """Look up file_key; raise KeyError with a clear message if unknown."""
    if file_key not in FILE_REGISTRY:
        raise KeyError(f"Unknown settings file key: {file_key!r}")
    return FILE_REGISTRY[file_key]


def _backup_subdir(file_key):
    """Per-file-key backup subdirectory. Does not create it."""
    return BACKUPS_DIR / file_key


def _list_backup_paths(file_key):
    """Backup paths for this key, newest first. Ignores anything that doesn't
    match '<expected_filename>.<YYYYMMDD-HHMMSS>.bak'."""
    subdir = _backup_subdir(file_key)
    if not subdir.exists():
        return []
    expected_filename = _entry(file_key)["filename"]
    matches = []
    for path in subdir.iterdir():
        if not path.is_file():
            continue
        m = BACKUP_FILENAME_RE.match(path.name)
        if not m or m.group(1) != expected_filename:
            continue
        matches.append(path)
    # Timestamp is zero-padded YYYYMMDD-HHMMSS → lexical sort = chronological.
    matches.sort(key=lambda p: p.name, reverse=True)
    return matches


def _prune_backups(file_key, keep=MAX_BACKUPS):
    """Delete oldest backups beyond `keep`. Silent on errors."""
    paths = _list_backup_paths(file_key)
    for path in paths[keep:]:
        try:
            path.unlink()
        except OSError:
            pass


def _create_backup(file_key):
    """Snapshot the current on-disk contents into the per-key backup subdir.
    Returns the new backup Path, or None if there's nothing to back up
    (source file does not exist — first-ever save of a brand-new file)."""
    entry = _entry(file_key)
    src = entry["path"]
    if not src.exists():
        return None
    subdir = _backup_subdir(file_key)
    subdir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime(BACKUP_TIMESTAMP_FMT)
    dst = subdir / f"{entry['filename']}.{timestamp}.bak"
    # Copy as bytes to avoid any encoding round-trip on the snapshot itself.
    dst.write_bytes(src.read_bytes())
    _prune_backups(file_key)
    return dst


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def list_files():
    """Listing for the Settings index page. Returns one dict per registered
    file with display metadata."""
    out = []
    for key, entry in FILE_REGISTRY.items():
        path = entry["path"]
        exists = path.exists()
        if exists:
            stat = path.stat()
            size = stat.st_size
            mtime = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S")
        else:
            size = 0
            mtime = None
        out.append({
            "key": key,
            "filename": entry["filename"],
            "kind": entry["kind"],
            "description": entry["description"],
            "exists": exists,
            "size": size,
            "mtime": mtime,
            "backup_count": len(_list_backup_paths(key)),
        })
    return out


def read_file(file_key):
    """Raw text contents. Empty string if file is missing (unusual but possible)."""
    entry = _entry(file_key)
    if not entry["path"].exists():
        return ""
    return entry["path"].read_text(encoding="utf-8")


def validate_content(file_key, content):
    """Returns (ok: bool, error_message: str or None).

    JSON files: parsed via json.loads(), then schema-checked if a validator
    is registered for this key. Text files always pass."""
    entry = _entry(file_key)
    if entry["kind"] == "json":
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            return False, f"Invalid JSON: {e.msg} (line {e.lineno}, column {e.colno})"
        validator = _SCHEMA_VALIDATORS.get(file_key)
        if validator is not None:
            err = validator(data)
            if err:
                return False, f"Schema error: {err}"
    return True, None


def save_file(file_key, content):
    """Validate, back up the previous on-disk version, then write new content.
    Returns the backup Path (or None if no prior file existed).
    Raises ValueError if the content fails validation."""
    ok, err = validate_content(file_key, content)
    if not ok:
        raise ValueError(err)
    backup_path = _create_backup(file_key)
    entry = _entry(file_key)
    entry["path"].write_text(content, encoding="utf-8")
    return backup_path


def list_backups(file_key):
    """Backups for this file, newest first. Each item: {filename, timestamp, size}.
    `timestamp` is reformatted to 'YYYY-MM-DD HH:MM:SS' for display."""
    out = []
    for path in _list_backup_paths(file_key):
        m = BACKUP_FILENAME_RE.match(path.name)
        ts_raw = m.group(2) if m else ""
        try:
            ts_pretty = datetime.strptime(ts_raw, BACKUP_TIMESTAMP_FMT).strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            ts_pretty = ts_raw
        out.append({
            "filename": path.name,
            "timestamp": ts_pretty,
            "size": path.stat().st_size,
        })
    return out


def read_backup(file_key, backup_filename):
    """Read a specific backup as text. The backup_filename is validated to
    belong to file_key (regex match + filename prefix check), so no path
    traversal is possible. Raises FileNotFoundError if the backup is invalid
    or missing."""
    entry = _entry(file_key)
    m = BACKUP_FILENAME_RE.match(backup_filename)
    if not m or m.group(1) != entry["filename"]:
        raise FileNotFoundError(f"Backup {backup_filename!r} does not belong to {file_key!r}")
    path = _backup_subdir(file_key) / backup_filename
    if not path.exists():
        raise FileNotFoundError(f"Backup file does not exist: {path}")
    return path.read_text(encoding="utf-8")
