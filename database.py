"""
database.py
-----------
Manages SQLite database for headlines, scrape run status, and persistent
headline ID assignment.

Tables:
  scrape_runs    - one row per source per run, with status and metadata
  headlines      - all scraped and cleaned headlines, grouped by run_id
  headline_ids   - persistent URL → H-ID mapping (1 to 99999, wraps around)
  id_counter     - tracks the next H-ID to assign

12-hour rule:
  If a new scrape occurs within 12 hours of the last run,
  it overwrites that run's records rather than creating a new batch.

Persistent H-IDs (Phase 2):
  Each unique URL gets a persistent H-ID. When a URL repeats across runs,
  the same H-ID is reused. This enables continuity detection in the AI pipeline.
  ID pool is 1 to 99999. When counter exceeds 99999, it wraps to 1,
  recycling the oldest H-ID by overwriting its row.

Invoked by scraper.py via save_to_database() and assign_ids_bulk().
"""

import os
import sqlite3
from datetime import datetime, timezone, timedelta


# ── Path ──────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(ROOT, 'data', 'news.db')

H_ID_MIN = 1
H_ID_MAX = 99999


# ── Connection ────────────────────────────────────────────────────────────────
def get_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Schema ────────────────────────────────────────────────────────────────────
def init_database():
    """Creates tables if they don't exist. Safe to call repeatedly."""
    conn = get_connection()
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS scrape_runs (
            run_id      TEXT NOT NULL,
            source      TEXT NOT NULL,
            success     INTEGER NOT NULL,
            warning     INTEGER NOT NULL DEFAULT 0,
            count       INTEGER NOT NULL DEFAULT 0,
            error       TEXT DEFAULT '',
            timestamp   TEXT NOT NULL,
            PRIMARY KEY (run_id, source)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS headlines (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id        TEXT NOT NULL,
            media         TEXT NOT NULL,
            title         TEXT NOT NULL,
            time          TEXT,
            language      TEXT,
            section       TEXT,
            url           TEXT NOT NULL,
            summary       TEXT DEFAULT '',
            homepage      INTEGER DEFAULT 0,
            h_id          INTEGER,
            UNIQUE(run_id, url)
        )
    """)

    # Migrations: silently ignored if column already exists
    migrations = [
        "ALTER TABLE headlines ADD COLUMN summary TEXT DEFAULT ''",
        "ALTER TABLE headlines ADD COLUMN homepage INTEGER DEFAULT 0",
        "ALTER TABLE headlines ADD COLUMN h_id INTEGER",
    ]
    for migration in migrations:
        try:
            c.execute(migration)
        except Exception:
            pass

    # Persistent H-ID table
    c.execute("""
        CREATE TABLE IF NOT EXISTS headline_ids (
            h_id        INTEGER PRIMARY KEY,
            url         TEXT NOT NULL UNIQUE,
            title       TEXT,
            media       TEXT,
            first_seen  TEXT
        )
    """)

    # ID counter — single row keyed by name
    c.execute("""
        CREATE TABLE IF NOT EXISTS id_counter (
            name   TEXT PRIMARY KEY,
            value  INTEGER NOT NULL
        )
    """)

    c.execute("INSERT OR IGNORE INTO id_counter (name, value) VALUES ('headline', 0)")

    conn.commit()
    conn.close()


# ── Run ID ────────────────────────────────────────────────────────────────────
def make_run_id(dt):
    """Consistent run ID from a datetime: '2026-04-24T16:00'"""
    return dt.strftime("%Y-%m-%dT%H:%M")


# ── 12-hour rule ──────────────────────────────────────────────────────────────
def find_recent_run(conn, now, threshold_hours=12):
    c = conn.cursor()
    c.execute("""
        SELECT run_id, MAX(timestamp) as latest
        FROM scrape_runs
        GROUP BY run_id
        ORDER BY latest DESC
        LIMIT 1
    """)
    row = c.fetchone()
    if not row or not row["latest"]:
        return None

    try:
        raw = row["latest"]
        raw = raw.replace(" UTC", "").replace(" ", "T")
        last_time = datetime.fromisoformat(raw)
        if last_time.tzinfo is None:
            last_time = last_time.replace(tzinfo=timezone.utc)
        delta = now - last_time
        if delta < timedelta(hours=threshold_hours):
            return row["run_id"]
    except Exception as e:
        print(f"  Warning: could not parse last run timestamp: {e}")

    return None


def delete_run(conn, run_id):
    c = conn.cursor()
    c.execute("DELETE FROM headlines WHERE run_id = ?", (run_id,))
    c.execute("DELETE FROM scrape_runs WHERE run_id = ?", (run_id,))
    conn.commit()
    print(f"  Database: overwriting run {run_id} (within 12-hour window)")


# ── Persistent H-ID assignment ────────────────────────────────────────────────
def lookup_or_assign_h_id(conn, url, title="", media=""):
    """
    Look up h_id for a URL. If exists, reuse. If not, assign next available.
    Wraps around at 99999 → 1, overwriting oldest record at that ID.
    Returns the h_id (integer) or None if URL is empty.
    """
    if not url:
        return None

    c = conn.cursor()

    c.execute("SELECT h_id FROM headline_ids WHERE url = ?", (url,))
    row = c.fetchone()
    if row:
        return row["h_id"]

    c.execute("SELECT value FROM id_counter WHERE name = 'headline'")
    counter_row = c.fetchone()
    next_id = (counter_row["value"] if counter_row else 0) + 1

    if next_id > H_ID_MAX:
        next_id = H_ID_MIN

    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    c.execute("""
        INSERT OR REPLACE INTO headline_ids (h_id, url, title, media, first_seen)
        VALUES (?, ?, ?, ?, ?)
    """, (next_id, url, title, media, now_str))

    c.execute("UPDATE id_counter SET value = ? WHERE name = 'headline'", (next_id,))

    return next_id


def assign_ids_bulk(headlines):
    """
    For a list of headline dicts, ensure each has a persistent h_id field.
    Modifies the list in place. Returns the same list.

    Optimization: bulk-fetches all existing URL→h_id mappings in one query,
    then only inserts missing URLs. ~50× faster than per-headline lookup.
    """
    init_database()
    conn = get_connection()
    new_count = 0
    reused_count = 0

    try:
        # Collect all valid URLs from this batch
        urls = [h.get("url", "") for h in headlines if h.get("url", "")]
        if not urls:
            for h in headlines:
                h["h_id"] = None
            return headlines

        # Bulk lookup: SELECT all existing URL→h_id mappings in one query
        c = conn.cursor()
        url_to_hid = {}
        # SQLite can handle ~999 params per query — chunk if needed
        CHUNK = 900
        for i in range(0, len(urls), CHUNK):
            batch = urls[i:i + CHUNK]
            placeholders = ",".join("?" * len(batch))
            c.execute(
                f"SELECT url, h_id FROM headline_ids WHERE url IN ({placeholders})",
                batch
            )
            for row in c.fetchall():
                url_to_hid[row["url"]] = row["h_id"]

        # Walk headlines: reuse existing IDs, assign new ones for unknowns
        for h in headlines:
            url = h.get("url", "")
            if not url:
                h["h_id"] = None
                continue

            if url in url_to_hid:
                h["h_id"] = url_to_hid[url]
                reused_count += 1
            else:
                h["h_id"] = lookup_or_assign_h_id(
                    conn,
                    url,
                    title=h.get("title", ""),
                    media=h.get("media", "") or h.get("source", "")
                )
                # Cache so duplicates within this batch reuse too
                url_to_hid[url] = h["h_id"]
                new_count += 1

        conn.commit()
    finally:
        conn.close()

    print(f"  H-IDs: {new_count} newly assigned, {reused_count} reused from previous runs")
    return headlines


# ── Lookup ────────────────────────────────────────────────────────────────────
def lookup_h_id_record(h_id):
    """Returns the record for an H-ID (url, title, media) or None."""
    init_database()
    conn = get_connection()
    c = conn.cursor()
    c.execute("SELECT * FROM headline_ids WHERE h_id = ?", (h_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ── Writers ───────────────────────────────────────────────────────────────────
def insert_headlines(conn, run_id, headlines):
    c = conn.cursor()
    inserted = 0
    for h in headlines:
        try:
            c.execute("""
                INSERT OR IGNORE INTO headlines
                    (run_id, media, title, time, language, section, url, summary, homepage, h_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id,
                h.get("media", ""),
                h.get("title", ""),
                h.get("time", ""),
                h.get("language", ""),
                h.get("section", ""),
                h.get("url", ""),
                h.get("summary", ""),
                1 if h.get("homepage") else 0,
                h.get("h_id"),
            ))
            inserted += c.rowcount
        except Exception as e:
            print(f"  Warning: could not insert headline: {e}")
    conn.commit()
    return inserted


def insert_statuses(conn, run_id, statuses):
    c = conn.cursor()
    for s in statuses:
        try:
            c.execute("""
                INSERT OR REPLACE INTO scrape_runs
                    (run_id, source, success, warning, count, error, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                run_id,
                s.get("source", ""),
                1 if s.get("success") else 0,
                1 if s.get("warning") else 0,
                s.get("headlines_found", 0),
                s.get("error", ""),
                s.get("timestamp", ""),
            ))
        except Exception as e:
            print(f"  Warning: could not insert status: {e}")
    conn.commit()


# ── Main entry point ──────────────────────────────────────────────────────────
def save_to_database(headlines, statuses, respect_recent_run=True):
    init_database()
    conn = get_connection()
    now = datetime.now(timezone.utc)

    existing_run_id = None
    if respect_recent_run:
        existing_run_id = find_recent_run(conn, now, threshold_hours=12)

    if existing_run_id:
        delete_run(conn, existing_run_id)
        run_id = existing_run_id
    else:
        run_id = make_run_id(now)
        print(f"  Database: new run {run_id}")

    inserted = insert_headlines(conn, run_id, headlines)
    insert_statuses(conn, run_id, statuses)
    conn.close()

    print(f"  Database: {inserted} headlines saved, "
          f"{len(statuses)} source statuses saved")
    return run_id


# ── Readers ──────────────────────────────────────────────────────────────────
def load_recent_headlines(limit_runs=1):
    conn = get_connection()
    c = conn.cursor()
    c.execute("""
        SELECT DISTINCT run_id FROM scrape_runs
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit_runs,))
    run_ids = [row["run_id"] for row in c.fetchall()]

    if not run_ids:
        conn.close()
        return []

    placeholders = ",".join("?" * len(run_ids))
    c.execute(f"""
        SELECT * FROM headlines
        WHERE run_id IN ({placeholders})
        ORDER BY time DESC
    """, run_ids)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def load_run_history(limit_days=10):
    conn = get_connection()
    c = conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=limit_days))
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M")
    c.execute("""
        SELECT * FROM scrape_runs
        WHERE timestamp >= ?
        ORDER BY timestamp DESC
    """, (cutoff_str,))
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


# ── Maintenance ──────────────────────────────────────────────────────────────
def trim_old_records(keep_days=30):
    conn = get_connection()
    c = conn.cursor()
    cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days))
    cutoff_str = cutoff.strftime("%Y-%m-%dT%H:%M")

    c.execute("""
        SELECT DISTINCT run_id FROM scrape_runs
        WHERE timestamp < ?
    """, (cutoff_str,))
    old_runs = [row["run_id"] for row in c.fetchall()]

    for run_id in old_runs:
        delete_run(conn, run_id)

    conn.close()
    print(f"  Trimmed {len(old_runs)} old runs (older than {keep_days} days)")