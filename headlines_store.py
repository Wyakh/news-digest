"""
headlines_store.py
------------------
Saves scraped headlines to a JSON file.
Two-phase pattern (Phase 2): build the slim list first, then assign
persistent H-IDs in scraper.py, then write the file.

Public functions:
  build_slim(results, sources)  -> slim list (no file written)
  write_headlines_file(slim)    -> writes data/headlines.json
  save_headlines(results, sources, defer_save=False) -> compatibility wrapper
"""

import json
import os
from datetime import datetime, timezone

STORE_PATH = os.path.join(os.path.dirname(__file__), 'data', 'headlines.json')


def build_slim(results, sources=None):
    """
    Apply per-source section exclusion and build the slim headline list.
    Does NOT write the file. Returns slim list ready for H-ID assignment.
    """
    exclude_map = {}
    if sources:
        for s in sources:
            name = s.get("name", "")
            exclude_map[name] = [
                sec.upper() for sec in s.get("sections_exclude", [])
            ]

    slim = []
    removed = 0
    for item in results:
        media = item.get("source", "")
        section = item.get("section", "").upper()

        excluded_sections = exclude_map.get(media, [])
        if section in excluded_sections:
            removed += 1
            continue

        slim.append({
            "media":    item.get("source", ""),
            "title":    item.get("title", ""),
            "time":     item.get("published_fmt", ""),
            "language": item.get("language", ""),
            "section":  item.get("section", ""),
            "url":      item.get("url", ""),
            "summary":  item.get("summary", ""),
            "homepage": item.get("homepage", False),
            "h_id":     None,   # filled in later by database.assign_ids_bulk
        })

    if removed:
        print(f"  Section filter: removed {removed} headlines")

    return slim


def write_headlines_file(slim):
    """Write slim list to data/headlines.json."""
    output = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "total": len(slim),
        "headlines": slim
    }
    os.makedirs(os.path.dirname(STORE_PATH), exist_ok=True)
    with open(STORE_PATH, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2, ensure_ascii=False)
    print(f"Headlines saved: {STORE_PATH} ({len(slim)} items)")


def save_headlines(results, sources=None, defer_save=False):
    """
    Backwards-compatible wrapper.
    If defer_save=True, returns slim without writing.
    If defer_save=False, builds and writes the file (no H-IDs assigned).
    """
    slim = build_slim(results, sources)
    if not defer_save:
        write_headlines_file(slim)
    return slim


def load_headlines():
    """Load previously saved headlines."""
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("headlines", [])
    except FileNotFoundError:
        return []