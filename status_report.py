"""
status_report.py
----------------
Tracks scraping results per source and produces
a status report — printed to terminal and saved as JSON.
"""

import json
from datetime import datetime, timezone

HEADLINE_WARNING_THRESHOLD = 1

def make_status(source_name, success, count=0, error="", warning=None):
    if warning is None:
        warning = success and count < HEADLINE_WARNING_THRESHOLD
    return {
        "source":          source_name,
        "success":         success,
        "warning":         warning,
        "headlines_found": count,
        "error":           error,
        "timestamp":       datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M")
    }


def print_report(statuses):
    total = len(statuses)
    succeeded = sum(1 for s in statuses if s["success"])
    failed = [s for s in statuses if not s["success"]]

    print(f"\n{'='*50}")
    print(f"SCRAPE REPORT")
    print(f"  Sources succeeded : {succeeded}/{total}")
    for s in statuses:
        icon = "✓" if s["success"] else "✗"
        line = f"  {icon} {s['source']}: {s['headlines_found']} headlines"
        if not s["success"]:
            line += f" — FAILED: {s['error']}"
        print(line)
    print(f"{'='*50}\n")


import os
def save_report(statuses, path=None):
    if path is None:
        path = os.path.join(os.path.dirname(__file__), 'data', 'scrape_status.json')
    with open(path, "w", encoding="utf-8") as f:
        json.dump(statuses, f, indent=2, ensure_ascii=False)