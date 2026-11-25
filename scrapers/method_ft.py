"""
method_ft.py
------------
Scrapes Financial Times via official RSS feeds.
FT intentionally exposes RSS at /?format=rss pattern.

Strategy:
  - Fetch 12 section feeds in parallel
  - Deduplicate by URL across feeds
  - Section labeled from which feed first found the article
  - No title fetching needed — RSS provides full titles
  - No homepage signal (homepage RSS only 10 items, subset of others)
  - homepage=None (concept not applicable)
"""

import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

MAX_AGE_HOURS = 48

# All confirmed working feeds with section labels
FT_FEEDS = [
    ("/world?format=rss",             "WORLD"),
    ("/economics?format=rss",         "ECONOMICS"),
    ("/markets?format=rss",           "MARKETS"),
    ("/politics?format=rss",          "POLITICS"),
    ("/technology?format=rss",        "TECHNOLOGY"),
    ("/companies?format=rss",         "COMPANIES"),
    ("/trade?format=rss",             "TRADE"),
    ("/chinese-economy?format=rss",   "CHINA-ECONOMY"),
    ("/emerging-markets?format=rss",  "EMERGING-MARKETS"),
    ("/world/asia-pacific?format=rss","ASIA-PACIFIC"),
    ("/world/us?format=rss",          "US"),
    ("/world/europe?format=rss",      "EUROPE"),
]


# ── Utilities ─────────────────────────────────────────────────────────────────

def parse_pubdate(pubdate_str):
    """Parse RFC 2822 pubDate from RSS e.g. 'Sat, 25 Apr 2026 17:13:00 GMT'"""
    if not pubdate_str:
        return None
    try:
        return parsedate_to_datetime(pubdate_str)
    except Exception:
        return None


def format_time(dt):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


# ── Single feed fetcher ───────────────────────────────────────────────────────

def fetch_feed(path, section, cutoff):
    """
    Fetch one FT RSS feed and return list of article dicts.
    Returns empty list on failure.
    """
    url = f"https://www.ft.com{path}"
    results = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for item in root.findall(".//item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            pubdate = item.findtext("pubDate") or ""

            if not title or not link:
                continue

            dt = parse_pubdate(pubdate)
            if dt and dt < cutoff:
                continue

            results.append({
                "title":         title,
                "url":           link,
                "section":       section,
                "published_fmt": format_time(dt),
                "dt":            dt,
            })

    except Exception as e:
        print(f"    Warning: failed feed {path}: {e}")

    return results


# ── Main entry point ──────────────────────────────────────────────────────────

def scrape_ft(source):
    """
    Called by scraper.py.
    Returns: (results list, success bool, error string)
    """
    name = source["name"]
    max_age = source.get("max_age_hours", MAX_AGE_HOURS)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age)

    print(f"  [{name}] Fetching {len(FT_FEEDS)} RSS feeds in parallel...")

    # Fetch all feeds in parallel, track per-feed results
    feed_results = {}
    with ThreadPoolExecutor(max_workers=12) as executor:
        futures = {
            executor.submit(fetch_feed, path, section, cutoff): (path, section)
            for path, section in FT_FEEDS
        }
        for future in as_completed(futures):
            path, section = futures[future]
            items = future.result()
            feed_results[(path, section)] = items

    # Print per-feed status
    for (path, section), items in sorted(feed_results.items(), key=lambda x: x[0][1]):
        print(f"    [{section}] {len(items)} articles")

    # Flatten and deduplicate by URL
    all_items = [item for items in feed_results.values() for item in items]
    seen_urls = set()
    results = []
    for item in all_items:
        if item["url"] in seen_urls:
            continue
        seen_urls.add(item["url"])
        results.append({
            "title":         item["title"],
            "url":           item["url"],
            "source":        name,
            "method":        "ft",
            "section":       item["section"],
            "language":      "EN",
            "published_fmt": item["published_fmt"],
            "summary":       "",
            "homepage":      None,
        })

    results.sort(key=lambda x: x["published_fmt"] or "", reverse=True)

    if not results:
        return [], False, "No articles found across all feeds"

    # Print dedup summary
    total_raw = sum(len(v) for v in feed_results.values())
    print(f"  [{name}] → {total_raw} total, {len(results)} unique after dedup")

    return results, True, ""