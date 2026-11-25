"""
method_reuters.py
-----------------
Scrapes Reuters headlines via Google News RSS.

Reuters switched its own site to a default-disallow allowlist in mid-2026,
which 401/403s the old Arc outbound sitemap feed for self-hosted scrapers.
Google's crawler is on Reuters' allowlist, so Reuters content is still
reachable through Google News. This module queries Google News RSS once per
Reuters section (in parallel) and normalizes the results into the same output
shape the rest of the pipeline expects.

Output contract (unchanged): returns (results, success, error); each result is
a dict with keys: title, url, source, method, section, language,
published_fmt, summary, homepage.

Notes / known trade-offs:
  - The article URL is Google News' encoded redirect
    (news.google.com/rss/articles/...), not a clean reuters.com URL. It is
    deterministic per article, so per-URL H-ID continuity holds run-to-run.
    It resolves to the real Reuters page when opened in a browser.
  - Section is taken from the query (reliable), not parsed from the URL.
  - Google News caps a single feed at ~100 items, so querying per section
    both restores coverage and supplies the section label.
"""

import re
import requests
import xml.etree.ElementTree as ET
from email.utils import parsedate_to_datetime
from urllib.parse import quote
from concurrent.futures import ThreadPoolExecutor, as_completed


# Reuters URL sections to query if the source entry doesn't specify its own.
DEFAULT_SECTIONS = [
    "world", "business", "markets",
    "technology", "legal", "sustainability",
]

# ~48h to match the project's scrape window (with overlap).
WHEN_WINDOW = "48h"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
}


def clean_text(text):
    if not text:
        return ""
    return re.sub(r'\s+', ' ', text).strip()


def strip_publisher_suffix(title):
    """Google News appends ' - Reuters' to titles. Remove it."""
    return re.sub(r'\s+-\s+Reuters(?:\.com)?\s*$', '', title).strip()


def is_valid_headline(text):
    if not text or len(text) < 15 or len(text) > 300:
        return False
    skip = ["sign in", "subscribe", "log in", "newsletter", "cookie",
            "privacy", "terms", "advertise", "contact", "about",
            "登录", "订阅", "注册", "click here", "read more"]
    return not any(s in text.lower() for s in skip)


def format_pubdate(rfc822_str):
    """Google News RSS pubDate is RFC 822, e.g. 'Mon, 17 Nov 2026 14:30:00 GMT'."""
    if not rfc822_str:
        return ""
    try:
        dt = parsedate_to_datetime(rfc822_str)
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    except Exception:
        return rfc822_str


def _build_feed_url(section):
    query = f"site:reuters.com/{section} when:{WHEN_WINDOW}"
    return (
        "https://news.google.com/rss/search"
        f"?q={quote(query)}&hl=en-US&gl=US&ceid=US:en"
    )


def _fetch_section_feed(section, source_name):
    """Fetch and parse one Google News RSS section feed. Runs in parallel.

    Returns (items, error_str). error_str is '' on success. A section that
    simply has no articles in the window returns ([], '')."""
    url = _build_feed_url(section)
    items = []
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)

        for item in root.findall('.//item'):
            raw_title = clean_text(item.findtext('title') or "")
            link = clean_text(item.findtext('link') or "")
            if not raw_title or not link:
                continue

            title = strip_publisher_suffix(raw_title)
            if not is_valid_headline(title):
                continue

            items.append({
                "title":         title,
                "url":           link,
                "source":        source_name,
                "method":        "reuters",
                "section":       section.upper(),
                "language":      "EN",
                "published_fmt": format_pubdate(item.findtext('pubDate') or ""),
                "summary":       "",
                "homepage":      None,
            })

    except Exception as e:
        return [], f"{section}: {e}"

    return items, ""


def scrape_reuters(source):
    """
    Main entry point called by scraper.py.
    Returns: (results list, success bool, error string)
    """
    name = source["name"]

    # Resolve section list: config override, else default. Honor sections_exclude
    # (case-insensitive) so it stays meaningful under the new method.
    sections = source.get("sections", DEFAULT_SECTIONS)
    exclude = {s.lower() for s in source.get("sections_exclude", [])}
    sections = [s for s in sections if s.lower() not in exclude]

    if not sections:
        return [], False, "No sections to query (all excluded?)"

    print(f"  [{name}] Fetching via Google News RSS ({len(sections)} sections)...")

    all_items = []
    errors = []

    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(_fetch_section_feed, section, name): section
            for section in sections
        }
        for future in as_completed(futures):
            items, err = future.result()
            all_items.extend(items)
            if err:
                errors.append(err)
                print(f"    Warning: section feed failed — {err}")

    # Deduplicate by URL (article token lives in the path; drop query/fragment).
    seen = set()
    results = []
    for item in all_items:
        key = item["url"].split("?")[0].rstrip("/").split("#")[0]
        if key not in seen:
            seen.add(key)
            results.append(item)

    print(f"  [{name}] → {len(results)} articles")

    # Partial success is still success. Only fail hard if we got nothing.
    if not results:
        detail = "; ".join(errors) if errors else "no articles in window"
        return [], False, f"No Reuters articles returned ({detail})"

    return results, True, ""
