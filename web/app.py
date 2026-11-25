"""
News Digest Web UI — Step 1: read-only digest viewer.

Run from project root with venv active:
    python web/app.py

The browser opens only after this server is ready, at the shared launch.json
address (default http://127.0.0.1:5000). For tray controls, use news.bat instead.
"""

import json
import re
import sys
import threading
import webbrowser
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from launch_config import load_launch_settings

HOST, PORT, BASE_URL = load_launch_settings(Path(__file__).resolve().parent.parent)

from markdown_it import MarkdownIt
from flask import Flask, abort, jsonify, redirect, render_template, request, send_from_directory, url_for
import pricing as pricing_helper
import runner
import settings as settings_helper
import comments as comments_module
import comment_widget
import starred as starred_helper

# CommonMark-compliant parser (same engine VS Code uses).
# html=True allows raw HTML passthrough (for any future <div class="chinese"> wrappers).
md_parser = MarkdownIt("commonmark", {"html": True})

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
# app.py lives in the project's web directory.
# Project root is its parent's parent.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DIGESTS_DIR = PROJECT_ROOT / "data" / "digests"
CSS_FILE = PROJECT_ROOT / "config" / "markdown-style.css"

# Filenames look like: 2026-04-27-2131.md
# Capture groups: year, month, day, hour, minute
DIGEST_FILENAME_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})-(\d{2})(\d{2})\.md$")

# ---------------------------------------------------------------------------
# Flask app
# ---------------------------------------------------------------------------
app = Flask(__name__)


def list_digests():
    """
    Scan data/digests/ for files matching YYYY-MM-DD-HHMM.md.
    Returns a list of dicts: {"run_id": "2026-04-27-2131", "dt": datetime}
    sorted newest-first. Files that don't match the pattern are skipped.
    """
    if not DIGESTS_DIR.exists():
        return []

    digests = []
    for path in DIGESTS_DIR.iterdir():
        match = DIGEST_FILENAME_RE.match(path.name)
        if not match:
            continue  # skip .json, .pdf, summary.txt, anything else
        year, month, day, hour, minute = (int(g) for g in match.groups())
        try:
            dt = datetime(year, month, day, hour, minute)
        except ValueError:
            continue  # skip impossible dates like 2026-13-45
        run_id = path.stem  # filename without .md
        digests.append({"run_id": run_id, "dt": dt})

    digests.sort(key=lambda d: d["dt"], reverse=True)
    return digests


@app.route("/")
def index():
    """List page: all past digests, newest first."""
    return render_template("index.html", digests=list_digests())


@app.route("/digest/<run_id>")
def digest(run_id):
    """Render a single digest as HTML using the project CSS, with comment
    widgets injected after each primary/secondary story h3."""
    if not DIGEST_FILENAME_RE.match(run_id + ".md"):
        abort(404)

    digest_path = DIGESTS_DIR / f"{run_id}.md"
    if not digest_path.exists():
        abort(404)

    raw_md = digest_path.read_text(encoding="utf-8")
    html_content = md_parser.render(raw_md)

    # Step 6c: inject comment widgets keyed by chain_id.
    return_to = url_for("digest", run_id=run_id)
    html_content = comment_widget.inject_widgets(
        html_content,
        run_id,
        comments_module,
        return_to,
    )

    return render_template(
        "digest.html",
        run_id=run_id,
        html_content=html_content,
    )

@app.route("/digest-css")
def digest_css():
    """
    Serve the project's markdown-style.css live (no copy).
    This way edits to the original immediately affect the web UI.
    """
    if not CSS_FILE.exists():
        abort(404)
    return send_from_directory(
        CSS_FILE.parent, CSS_FILE.name, mimetype="text/css"
    )


# ---------------------------------------------------------------------------
# Auto-open browser on startup
# ---------------------------------------------------------------------------
def open_browser():
    """Open the default browser at the shared configured app URL."""
    webbrowser.open(BASE_URL)


@app.route("/status")
def status():
    """Per-source scrape health + latest AI run summary."""
    status_path = PROJECT_ROOT / "data" / "scrape_status.json"
    sources = []
    if status_path.exists():
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                sources = json.load(f)
        except (json.JSONDecodeError, OSError):
            sources = []

    # Find latest digest's summary.txt
    digests = list_digests()
    latest_summary = None
    if digests:
        latest_run_id = digests[0]["run_id"]
        summary_path = DIGESTS_DIR / f"{latest_run_id}-summary.txt"
        if summary_path.exists():
            latest_summary = {
                "run_id": latest_run_id,
                "text": summary_path.read_text(encoding="utf-8"),
            }

    return render_template("status.html", sources=sources, latest_summary=latest_summary)


@app.route("/cost")
def cost():
    """30-day total/average, last-5-runs average, last-3-runs individual."""
    cost_log_path = PROJECT_ROOT / "data" / "cost_log.json"
    if not cost_log_path.exists():
        return render_template("cost.html", has_data=False)

    try:
        with open(cost_log_path, "r", encoding="utf-8") as f:
            entries = json.load(f)
    except (json.JSONDecodeError, OSError):
        return render_template("cost.html", has_data=False)

    if not entries:
        return render_template("cost.html", has_data=False)

    pricing = pricing_helper.load_pricing()
    models = pricing_helper.load_models()

    enriched = []
    for entry in entries:
        ts = pricing_helper.entry_timestamp(entry)
        c = pricing_helper.entry_total_cost(entry, pricing, models)
        enriched.append({
            "run_id": entry.get("run_id", ""),
            "timestamp": ts,
            "cost": c,
        })

    # Newest first.
    enriched.sort(
        key=lambda e: e["timestamp"] or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )

    # Last 30 days
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=30)
    recent_30 = [e for e in enriched if e["timestamp"] and e["timestamp"] >= cutoff]
    last_30_total = sum(e["cost"] for e in recent_30)
    last_30_avg = (last_30_total / len(recent_30)) if recent_30 else 0.0

    # Last 5 runs
    last_5 = enriched[:5]
    last_5_avg = (sum(e["cost"] for e in last_5) / len(last_5)) if last_5 else 0.0

    # Last 3 runs (individual)
    last_3 = enriched[:3]

    return render_template(
        "cost.html",
        has_data=True,
        last_30_count=len(recent_30),
        last_30_total=last_30_total,
        last_30_avg=last_30_avg,
        last_5_count=len(last_5),
        last_5_avg=last_5_avg,
        last_3=last_3,
    )


@app.route("/run")
def run_page():
    """The Run page with three buttons and live status."""
    return render_template("run.html")


@app.route("/run/precheck")
def run_precheck():
    """Smart 12-hour check for the chosen job."""
    job = request.args.get("job", "")
    if job not in ("scraper", "ai", "both"):
        return jsonify({"error": "invalid job"}), 400
    return jsonify(runner.precheck(job))


@app.route("/run/start", methods=["POST"])
def run_start():
    """Spawn the chosen job."""
    payload = request.get_json(silent=True) or {}
    job = payload.get("job")
    override = payload.get("override")
    ok, message = runner.start(job, override)
    if not ok:
        return jsonify({"ok": False, "message": message}), 400
    return jsonify({"ok": True, "message": message})


@app.route("/run/status")
def run_status():
    """Live status for AJAX polling."""
    return jsonify(runner.status_response())


@app.route("/run/delete-latest-ai", methods=["POST"])
def run_delete_latest_ai():
    """Delete the most recent AI digest's files + cost_log entry."""
    ok, message, run_id = runner.delete_latest_ai_run()
    status = 200 if ok else 400
    return jsonify({"ok": ok, "message": message, "run_id": run_id}), status

# ── Inspect: landing ────────────────────────────────────────────────────────
@app.route("/inspect")
def inspect_page():
    return render_template("inspect.html")


# ── Inspect: headlines list with filters ────────────────────────────────────
@app.route("/headlines")
def headlines_page():
    import re
    from urllib.parse import urlencode

    headlines_path = PROJECT_ROOT / "data" / "headlines.json"
    status_path = PROJECT_ROOT / "data" / "scrape_status.json"

    if not headlines_path.exists():
        return render_template("headlines.html", has_data=False)

    try:
        with open(headlines_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        all_headlines = data.get("headlines", [])
    except (OSError, json.JSONDecodeError):
        return render_template("headlines.html", has_data=False)

    DEFAULT_LIMIT = 100

    # Read filters from query string.
    filters = {
        "media": request.args.get("media", ""),
        "language": request.args.get("language", ""),
        "section_contains": request.args.get("section_contains", "").strip(),
        # Drop the empty-string "(none)" sentinel from the list.
        "section_match": [s for s in request.args.getlist("section_match") if s],
        "title_patterns": request.args.get("title_patterns", "").strip(),
    }
    show_all = (request.args.get("limit", "") == "all")

    # Apply filters.
    filtered = all_headlines
    if filters["media"]:
        filtered = [h for h in filtered if h.get("media") == filters["media"]]
    if filters["language"]:
        filtered = [h for h in filtered if h.get("language") == filters["language"]]

    if filters["section_contains"]:
        needle = filters["section_contains"].lower()
        filtered = [h for h in filtered if needle in (h.get("section") or "").lower()]

    if filters["section_match"]:
        targets = {s.lower() for s in filters["section_match"]}
        filtered = [h for h in filtered if (h.get("section") or "").lower() in targets]

    # Title patterns: split on commas, strip optional surrounding quotes from each piece.
    # Each resulting non-empty pattern is a case-insensitive substring match (OR-combined).
    # If any pattern is whitespace-only, return nothing (safety against accidental "match all").
    if filters["title_patterns"]:
        raw_pieces = filters["title_patterns"].split(",")
        patterns = []
        any_blank = False
        for piece in raw_pieces:
            p = piece.strip()
            # Strip a leading and/or trailing double quote if present.
            if p.startswith('"'):
                p = p[1:]
            if p.endswith('"'):
                p = p[:-1]
            if not p.strip():
                if piece.strip():
                    # The piece had content (quotes) but was empty after stripping → blank pattern
                    any_blank = True
                # Else: piece was empty (e.g. trailing comma) — silently ignored
                continue
            patterns.append(p)

        if any_blank:
            filtered = []
        elif patterns:
            patterns_lower = [p.lower() for p in patterns]
            filtered = [
                h for h in filtered
                if any(p in (h.get("title") or "").lower() for p in patterns_lower)
            ]
        # else: no patterns extracted at all → no title filter applied

    filtered_count = len(filtered)

    # Apply display limit.
    displayed = filtered if show_all else filtered[:DEFAULT_LIMIT]

    # Distinct media names.
    all_media = sorted({h.get("media", "") for h in all_headlines if h.get("media")})

    # Distinct sections, deduped case-insensitively, sorted.
    section_canonical = {}
    for h in all_headlines:
        s = h.get("section")
        if s:
            key = s.lower()
            if key not in section_canonical:
                section_canonical[key] = s
    all_sections = sorted(section_canonical.values(), key=lambda x: x.lower())

    # Latest scrape timestamp.
    scrape_timestamp = None
    if status_path.exists():
        try:
            with open(status_path, "r", encoding="utf-8") as f:
                status_data = json.load(f)
            timestamps = [s.get("timestamp", "") for s in status_data if s.get("timestamp")]
            if timestamps:
                scrape_timestamp = max(timestamps)
        except (OSError, json.JSONDecodeError):
            pass

    # Build toggle URLs that preserve all other query params (including multi-valued ones).
    def _build_toggle_url(limit_value):
        pairs = []
        for k, v_list in request.args.lists():
            if k == "limit":
                continue
            for v in v_list:
                pairs.append((k, v))
        if limit_value:
            pairs.append(("limit", limit_value))
        url = url_for("headlines_page")
        return url + ("?" + urlencode(pairs) if pairs else "")

    show_all_url = _build_toggle_url("all")
    default_url = _build_toggle_url(None)

    return render_template(
        "headlines.html",
        has_data=True,
        headlines=displayed,
        displayed_count=len(displayed),
        filtered_count=filtered_count,
        total_count=len(all_headlines),
        filters=filters,
        all_media=all_media,
        all_sections=all_sections,
        scrape_timestamp=scrape_timestamp,
        default_limit=DEFAULT_LIMIT,
        show_all=show_all,
        show_all_url=show_all_url,
        default_url=default_url,
    )


# ── Inspect: per-run raw structured JSON ────────────────────────────────────
@app.route("/digest/<run_id>/raw")
def digest_raw(run_id):
    if not DIGEST_FILENAME_RE.match(run_id + ".md"):
        abort(404)
    json_path = DIGESTS_DIR / f"{run_id}.json"
    if not json_path.exists():
        abort(404)

    try:
        with open(json_path, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, json.JSONDecodeError):
        abort(500)

    stories = data.get("stories", [])

    tier_counts = {"primary": 0, "secondary": 0, "signal": 0}
    flag_counts = {"developing": 0, "no_new_info": 0, "significant": 0, "was_signal": 0}
    for s in stories:
        tier = s.get("tier", "")
        if tier in tier_counts:
            tier_counts[tier] += 1
        if s.get("is_developing"): flag_counts["developing"] += 1
        if s.get("no_new_info"): flag_counts["no_new_info"] += 1
        if s.get("is_continuation_significant"): flag_counts["significant"] += 1
        if s.get("was_signal_in_past"): flag_counts["was_signal"] += 1

    return render_template(
        "digest_raw.html",
        run_id=run_id,
        stories=stories,
        tier_counts=tier_counts,
        flag_counts=flag_counts,
    )


# ── Inspect: per-source headline trends across recent runs ──────────────────
@app.route("/source-trends")
def source_trends_page():
    """Reads news.db's scrape_runs archive to build a per-source × per-run grid."""
    import sqlite3

    db_path = PROJECT_ROOT / "data" / "news.db"
    if not db_path.exists():
        return render_template("source_trends.html", has_data=False)

    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        # Most recent ~10 distinct run_ids.
        c.execute("""
            SELECT DISTINCT run_id, MAX(timestamp) AS ts
            FROM scrape_runs
            GROUP BY run_id
            ORDER BY ts DESC
            LIMIT 10
        """)
        recent_runs = [(row["run_id"], row["ts"]) for row in c.fetchall()]

        if not recent_runs:
            conn.close()
            return render_template("source_trends.html", has_data=False)

        run_ids = [r[0] for r in recent_runs]
        placeholders = ",".join("?" * len(run_ids))
        c.execute(f"""
            SELECT run_id, source, count, warning
            FROM scrape_runs
            WHERE run_id IN ({placeholders})
        """, run_ids)
        rows = c.fetchall()
        conn.close()
    except sqlite3.Error:
        return render_template("source_trends.html", has_data=False)

    # Build {source: {run_id: {count, warning, present}}}.
    grid = {}
    for row in rows:
        src = row["source"]
        grid.setdefault(src, {})[row["run_id"]] = {
            "count": row["count"] or 0,
            "warning": bool(row["warning"]),
            "present": True,
        }

    # Format timestamps for column headers (run_ids look like "2026-04-27T21:27").
    run_timestamps = [r[0].replace("T", " ") for r in recent_runs]

    # Build (source, [cell, cell, ...]) pairs in original run order.
    source_rows = []
    for source in sorted(grid.keys()):
        row = []
        for run_id, _ in recent_runs:
            if run_id in grid[source]:
                row.append(grid[source][run_id])
            else:
                row.append({"count": 0, "warning": False, "present": False})
        source_rows.append((source, row))

    return render_template(
        "source_trends.html",
        has_data=True,
        run_count=len(recent_runs),
        run_timestamps=run_timestamps,
        source_rows=source_rows,
    )

# ── Settings: edit configuration files and AI prompts ──────────────────────
@app.route("/settings")
def settings_index():
    """List all editable configuration files and prompts."""
    return render_template("settings.html", files=settings_helper.list_files())


@app.route("/settings/<file_key>")
def settings_edit(file_key):
    """Editor for a single file. Optional ?restore=<backup_filename> loads
    backup contents into the textarea instead of the current file."""
    if file_key not in settings_helper.FILE_REGISTRY:
        abort(404)

    restore = request.args.get("restore", "")
    restore_label = None
    if restore:
        try:
            content = settings_helper.read_backup(file_key, restore)
        except FileNotFoundError:
            abort(404)
        # Find the matching display timestamp.
        for b in settings_helper.list_backups(file_key):
            if b["filename"] == restore:
                restore_label = b["timestamp"]
                break
    else:
        content = settings_helper.read_file(file_key)

    entry = settings_helper.FILE_REGISTRY[file_key]
    return render_template(
        "settings_edit.html",
        file_key=file_key,
        filename=entry["filename"],
        kind=entry["kind"],
        description=entry["description"],
        content=content,
        restore_label=restore_label,
        saved=(request.args.get("saved") == "1"),
        error=None,
    )


@app.route("/settings/<file_key>/save", methods=["POST"])
def settings_save(file_key):
    """Validate and write submitted content; redirect to editor on success,
    re-render with error on validation failure."""
    if file_key not in settings_helper.FILE_REGISTRY:
        abort(404)
    content = request.form.get("content", "")
    try:
        settings_helper.save_file(file_key, content)
    except ValueError as e:
        entry = settings_helper.FILE_REGISTRY[file_key]
        return render_template(
            "settings_edit.html",
            file_key=file_key,
            filename=entry["filename"],
            kind=entry["kind"],
            description=entry["description"],
            content=content,
            restore_label=None,
            saved=False,
            error=str(e),
        )
    return redirect(url_for("settings_edit", file_key=file_key, saved=1))


@app.route("/settings/<file_key>/backups")
def settings_backups(file_key):
    """List timestamped backups for a single file."""
    if file_key not in settings_helper.FILE_REGISTRY:
        abort(404)
    entry = settings_helper.FILE_REGISTRY[file_key]
    return render_template(
        "settings_backups.html",
        file_key=file_key,
        filename=entry["filename"],
        backups=settings_helper.list_backups(file_key),
    )


# ── Comments: add / edit / delete editorial notes on continuity chains ─────
def _safe_return_to(default_url):
    """Validate the return_to form field. Only allow same-origin paths to
    prevent open-redirect. If invalid or missing, fall back to default_url."""
    rt = request.form.get("return_to", "")
    if rt.startswith("/") and not rt.startswith("//"):
        return rt
    return default_url


@app.route("/comments/add", methods=["POST"])
def comments_add():
    chain_id = request.form.get("chain_id", "").strip()
    created_run_id = request.form.get("created_run_id", "").strip()
    text = request.form.get("text", "")
    fallback = url_for("index")
    if not chain_id or not created_run_id:
        abort(400)
    try:
        new_comment = comments_module.add(chain_id, text, created_run_id)
    except ValueError:
        # Empty text: just bounce back without saving.
        return redirect(_safe_return_to(fallback))
    return_to = _safe_return_to(url_for("digest", run_id=created_run_id))
    # Append the chain anchor so the page scrolls to the just-added comment.
    if "#" not in return_to:
        return_to = f"{return_to}#chain-{new_comment['chain_id']}"
    return redirect(return_to)


@app.route("/comments/<comment_id>/edit", methods=["POST"])
def comments_edit(comment_id):
    text = request.form.get("text", "")
    fallback = url_for("index")
    try:
        comments_module.edit(comment_id, text)
    except (KeyError, ValueError):
        # Missing or empty: bounce back silently.
        return redirect(_safe_return_to(fallback))
    return redirect(_safe_return_to(fallback))


@app.route("/comments/<comment_id>/delete", methods=["POST"])
def comments_delete(comment_id):
    fallback = url_for("index")
    comments_module.delete(comment_id)  # idempotent; ignore return value
    return redirect(_safe_return_to(fallback))


# ── Notes: list all chains with editorial comments ─────────────────────────

# Cap how many digests the Notes page scans for chain appearances.
# 60 ≈ a month of twice-daily runs; well past any realistic chain lifespan.
NOTES_DIGEST_SCAN_LIMIT = 60


def _run_id_sortkey(c):
    """Sort key for comments: (created_run_id, created_at). Stable ordering
    by associated story run, ties broken by wall-clock creation time."""
    return (c.get("created_run_id", ""), c.get("created_at", ""))


def _scan_chain_appearances(chain_id):
    """Find all digest runs where this chain_id appears, newest-first.
    Returns list of {"run_id", "story"}.

    Used by the Notes page row builder. Bounded by NOTES_DIGEST_SCAN_LIMIT.
    """
    appearances = []
    if not DIGESTS_DIR.exists():
        return appearances
    for entry in list_digests()[:NOTES_DIGEST_SCAN_LIMIT]:
        run_id = entry["run_id"]
        json_path = DIGESTS_DIR / f"{run_id}.json"
        if not json_path.exists():
            continue
        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        for story in data.get("stories", []):
            if story.get("chain_id") == chain_id:
                appearances.append({"run_id": run_id, "story": story})
                break  # at most one match per run
    return appearances


def _build_notes_row(chain_id, all_chain_comments, starred_set):
    """Build one row dict for the Notes page.

    `all_chain_comments`: the full list of comments for this chain (any order).
    `starred_set`: set of starred chain_ids (passed in to avoid re-reading
                   starred.json per row)."""
    appearances = _scan_chain_appearances(chain_id)
    latest = appearances[0] if appearances else None

    # Sort comments by associated creation time (created_run_id, then created_at).
    # Newest first.
    sorted_comments = sorted(all_chain_comments, key=_run_id_sortkey, reverse=True)
    preview = sorted_comments[0] if sorted_comments else None
    past = sorted_comments[1:] if len(sorted_comments) > 1 else []

    # latest_run_id used for default sort: newest associated run wins.
    latest_run_id = latest["run_id"] if latest else (
        preview.get("created_run_id") if preview else ""
    )

    return {
        "chain_id": chain_id,
        "is_starred": chain_id in starred_set,
        "latest_run_id": latest["run_id"] if latest else None,
        "latest_title": (
            latest["story"].get("canonical_title", "(no title)")
            if latest else "(story no longer in recent digests)"
        ),
        "latest_tags": latest["story"].get("tags", []) if latest else [],
        "sources": latest["story"].get("sources", []) if latest else [],
        "comment_count": len(sorted_comments),
        "preview_comment": preview,
        "past_comments": past,
        # Hidden field for sorting at the end:
        "_sort_run_id": latest_run_id,
    }


@app.route("/notes")
def notes_page():
    """List all continuity chains with at least one comment.

    Query params:
      ?q=<text>          case-insensitive substring search on comment text
      ?starred_only=1    only show starred chains
    """
    query = request.args.get("q", "").strip()
    starred_only = request.args.get("starred_only") == "1"

    starred_set = starred_helper.get_all()

    # Group all comments by chain_id.
    by_chain = {}
    q_lower = query.lower() if query else ""
    for c in comments_module._load_all():
        cid = c.get("chain_id")
        if not cid:
            continue
        # Apply text search on raw comment text. We track ANY match per chain;
        # the chain row appears if at least one of its comments matches.
        if q_lower:
            if q_lower not in c.get("text", "").lower():
                # Still need to keep this comment in the chain's full list
                # in case ANOTHER comment matches — collect all, filter chain-level after.
                pass
        by_chain.setdefault(cid, []).append(c)

    # Chain-level filter for search: keep only chains where ≥1 comment matched.
    if q_lower:
        by_chain = {
            cid: comments
            for cid, comments in by_chain.items()
            if any(q_lower in c.get("text", "").lower() for c in comments)
        }

    # Chain-level filter for starred-only.
    if starred_only:
        by_chain = {cid: cs for cid, cs in by_chain.items() if cid in starred_set}

    # Build rows.
    rows = [_build_notes_row(cid, cs, starred_set) for cid, cs in by_chain.items()]

    # Sort: latest associated story run desc, ties by chain_id for stability.
    rows.sort(key=lambda r: (r["_sort_run_id"], r["chain_id"]), reverse=True)

    # Build return URL preserving filters (so star/edit/delete bounce back here).
    from urllib.parse import urlencode
    qs_pairs = []
    if query: qs_pairs.append(("q", query))
    if starred_only: qs_pairs.append(("starred_only", "1"))
    return_to_url = url_for("notes_page")
    if qs_pairs:
        return_to_url = f"{return_to_url}?{urlencode(qs_pairs)}"

    return render_template(
        "notes.html",
        rows=rows,
        query=query,
        starred_only=starred_only,
        return_to_url=return_to_url,
    )


@app.route("/chains/<chain_id>/star", methods=["POST"])
def chain_star_toggle(chain_id):
    """Toggle starred state for a chain. Redirects back to return_to."""
    try:
        starred_helper.toggle(chain_id)
    except ValueError:
        abort(400)
    return_to = _safe_return_to(url_for("notes_page"))
    return redirect(return_to)

if __name__ == "__main__":
    # Optional web-only entry point: never open an unrelated listener on bind failure.
    from werkzeug.serving import make_server
    with make_server(HOST, PORT, app, threaded=True) as server:
        opened = False
        service_actions = server.service_actions

        def ready():
            global opened
            if not opened:
                opened = True
                threading.Thread(target=open_browser, daemon=True).start()
            service_actions()

        server.service_actions = ready
        server.serve_forever(poll_interval=0.1)