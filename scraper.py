"""
scraper.py
----------
Master runner. Reads sources.json, calls the right method for each source,
then prints and saves the report.

Phase 2 addition:
  After deduplication, headlines receive persistent H-IDs from the database
  (assign_ids_bulk). Repeat URLs reuse their previous H-ID; new URLs get
  fresh sequential IDs (1 to 99999, wraps around).

Run: python scraper.py
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'scrapers'))

from scrapers.method_reuters import scrape_reuters
from scrapers.method_huanqiu import scrape_huanqiu, run_homepage_tagging
from scrapers.method_caixin import scrape_caixin_subdomains, apply_caixin_homepage_signal
from scrapers.method_zaobao import scrape_zaobao
from scrapers.method_ft import scrape_ft
from scrapers.method_straitstimes import scrape_straitstimes
from status_report import make_status, print_report, save_report
from headlines_store import build_slim, write_headlines_file
from database import save_to_database, assign_ids_bulk


def load_sources():
    path = os.path.join(os.path.dirname(__file__), 'config', 'sources.json')
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)["sources"]


# ── Per-source wrappers for async execution ───────────────────────────────────

async def run_single_source(source):
    method = source["method"]
    name = source["name"]
    loop = asyncio.get_event_loop()

    print(f"  [{name}] method: {method}")

    if method == "reuters":
        results, success, error = await loop.run_in_executor(
            None, scrape_reuters, source
        )
    elif method == "huanqiu":
        results, success, error = await loop.run_in_executor(
            None, scrape_huanqiu, source
        )
    elif method == "straitstimes":
        results, success, error = await loop.run_in_executor(
            None, scrape_straitstimes, source
        )
    elif method == "zaobao":
        results, success, error = await loop.run_in_executor(
            None, scrape_zaobao, source
        )
    elif method == "ft":
        results, success, error = await loop.run_in_executor(
            None, scrape_ft, source
        )
    elif method == "rss":
        results, success, error = [], False, "RSS method not yet implemented"
    else:
        results, success, error = [], False, f"Unknown method: {method}"

    status = make_status(name, success, len(results), error)
    return results, status


async def run_caixin_group(sources):
    loop = asyncio.get_event_loop()
    results, statuses = await loop.run_in_executor(
        None, scrape_caixin_subdomains, sources
    )
    results, tagged, added, error = await loop.run_in_executor(
        None, apply_caixin_homepage_signal, results
    )

    homepage_status = make_status(
        "财新-首页",
        success=error == "",
        count=added,
        error=error
    )
    statuses.append(homepage_status)

    return results, statuses


async def run_playwright_group(sources):
    statuses = [
        make_status(s["name"], False, 0, "Playwright method not yet implemented")
        for s in sources
    ]
    return [], statuses


# ── Master runner ─────────────────────────────────────────────────────────────

async def run_all(respect_recent_run=True):
    sources = load_sources()
    enabled = [s for s in sources if s.get("enabled", True)]

    print(f"\nStarting scrape: {len(enabled)} source(s) enabled\n")

    single_sources    = [s for s in enabled if s["method"] not in ("playwright", "caixin_sub")]
    caixin_sources    = [s for s in enabled if s["method"] == "caixin_sub"]
    playwright_sources = [s for s in enabled if s["method"] == "playwright"]

    tasks = []
    task_labels = []

    for source in single_sources:
        tasks.append(run_single_source(source))
        task_labels.append(("single", source))

    if caixin_sources:
        tasks.append(run_caixin_group(caixin_sources))
        task_labels.append(("caixin_group", caixin_sources))

    if playwright_sources:
        tasks.append(run_playwright_group(playwright_sources))
        task_labels.append(("playwright_group", playwright_sources))

    print(f"  Running {len(tasks)} task(s) in parallel...\n")
    task_results = await asyncio.gather(*tasks, return_exceptions=True)

    all_results = []
    all_statuses = []

    for i, (label, meta) in enumerate(task_labels):
        outcome = task_results[i]

        if isinstance(outcome, Exception):
            if label == "single":
                all_statuses.append(make_status(meta["name"], False, 0, str(outcome)))
            else:
                for s in meta:
                    all_statuses.append(make_status(s["name"], False, 0, str(outcome)))
            continue

        if label == "single":
            results, status = outcome
            all_results.extend(results)
            all_statuses.append(status)
        elif label in ("caixin_group", "playwright_group"):
            results, statuses = outcome
            all_results.extend(results)
            all_statuses.extend(statuses)

    # Huanqiu homepage tagging
    huanqiu_results = [r for r in all_results if r.get("method") == "huanqiu_api"]
    if huanqiu_results:
        non_huanqiu = [r for r in all_results if r.get("method") != "huanqiu_api"]
        huanqiu_results = run_homepage_tagging(huanqiu_results)
        all_results = non_huanqiu + huanqiu_results

    # Global deduplication by URL
    seen = set()
    deduped = []
    for item in all_results:
        key = item["url"].split("?")[0].rstrip("/").split("#")[0]
        if key not in seen:
            seen.add(key)
            deduped.append(item)

    # Report and save
    print_report(all_statuses)
    save_report(all_statuses)

    # Phase 2: build slim, assign persistent H-IDs, then write file
    slim = build_slim(deduped, sources=enabled)

    print(f"\n  Assigning persistent H-IDs to {len(slim)} headlines...")
    assign_ids_bulk(slim)

    write_headlines_file(slim)
    save_to_database(slim, all_statuses, respect_recent_run=respect_recent_run)

    print(f"Total unique headlines: {len(deduped)}")
    return deduped, all_statuses


def run_scraper(respect_recent_run=True):
    return asyncio.run(run_all(respect_recent_run=respect_recent_run))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="News scraper")
    parser.add_argument(
        "--no-override",
        action="store_true",
        help="Skip the 12-hour rule; new run is saved alongside the recent run "
             "instead of overwriting it.",
    )
    args = parser.parse_args()
    run_scraper(respect_recent_run=not args.no_override)