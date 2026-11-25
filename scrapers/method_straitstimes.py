"""
method_straitstimes.py
----------------------
Scrapes The Straits Times using their monthly sitemap.

URL: https://www.straitstimes.com/sitemap/YYYY/MM/feeds.xml
- No news:title tags — titles reconstructed from URL slugs
- lastmod field used for precise 48-hour time filtering
- Section extracted from URL path first segment
- Fetches current month + previous month (for cross-month coverage)
- Parallel sub-sitemap fetching via ThreadPoolExecutor

Standard output dict contract:
  title, url, source, method, section, language, published_fmt,
  summary, homepage (always False — no homepage signal for ST)
"""

import requests
import xml.etree.ElementTree as ET
import re
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed


HEADERS = {
    "User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"
}

MAX_AGE_HOURS = 48

# Sections to exclude — filtered by URL path segment
EXCLUDE_SECTIONS = {
    "sport", "life", "multimedia", "podcasts",
    "videos", "photos", "entertainment", "myfunds"
}

# Minimum slug length to be a real article (not a section index page)
MIN_SLUG_PARTS = 2


# ── Utilities ─────────────────────────────────────────────────────────────────

def slug_to_title(url):
    """
    Reconstruct a readable title from the URL slug.
    e.g. /world/europe/hungarys-orban-will-not-sit-in-parliament
    → "Hungarys orban will not sit in parliament"
    """
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if not parts:
        return ""
    # Last part is the article slug
    slug = parts[-1]
    # Replace hyphens with spaces, capitalize first word
    title = slug.replace("-", " ")
    return title.capitalize()


def extract_section(url):
    """First path segment = section."""
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    return parts[0].upper() if parts else "GENERAL"


def parse_lastmod(lastmod_str):
    """Parse ISO 8601 lastmod timestamp."""
    if not lastmod_str:
        return None
    try:
        # Format: 2026-04-25T16:11:31.000Z
        clean = lastmod_str.replace("Z", "+00:00")
        return datetime.fromisoformat(clean)
    except Exception:
        return None


def format_time(dt):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def is_valid_article_url(url):
    """Filter out section index pages and non-article URLs."""
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    # Must have at least 2 path segments (section + slug)
    if len(parts) < MIN_SLUG_PARTS:
        return False
    # Section must not be excluded
    if parts[0].lower() in EXCLUDE_SECTIONS:
        return False
    # Must look like an article slug (contains hyphens)
    if "-" not in parts[-1]:
        return False
    return True


# ── Sitemap fetcher ───────────────────────────────────────────────────────────

def fetch_sitemap(sitemap_url, cutoff):
    """
    Fetch and parse one monthly sitemap XML.
    Returns list of article dicts within cutoff.
    """
    results = []
    try:
        resp = requests.get(sitemap_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

        for url_el in root.findall('sm:url', ns):
            loc = url_el.findtext('sm:loc', namespaces=ns)
            lastmod = url_el.findtext('sm:lastmod', namespaces=ns)

            if not loc:
                continue
            if not is_valid_article_url(loc):
                continue

            dt = parse_lastmod(lastmod)
            if dt and dt < cutoff:
                continue

            title = slug_to_title(loc)
            if not title or len(title) < 10:
                continue

            results.append({
                "title":         title,
                "url":           loc,
                "source":        "Straits Times",
                "method":        "straitstimes",
                "section":       extract_section(loc),
                "language":      "EN",
                "published_fmt": format_time(dt),
                "summary":       "",
                "homepage":      None,
            })

    except Exception as e:
        print(f"    Warning: failed to fetch {sitemap_url}: {e}")

    return results


def get_sitemap_urls():
    """
    Generate sitemap URLs for current month and previous month
    to ensure cross-month coverage (e.g. articles from April 30
    still captured when running in May).
    """
    now = datetime.now(timezone.utc)
    urls = []

    # Current month
    urls.append(
        f"https://www.straitstimes.com/sitemap/"
        f"{now.year}/{now.month:02d}/feeds.xml"
    )

    # Previous month
    first_of_month = now.replace(day=1)
    prev = first_of_month - timedelta(days=1)
    urls.append(
        f"https://www.straitstimes.com/sitemap/"
        f"{prev.year}/{prev.month:02d}/feeds.xml"
    )

    return urls


# ── Main entry point ──────────────────────────────────────────────────────────

def scrape_straitstimes(source):
    """
    Called by scraper.py.
    Returns: (results list, success bool, error string)
    """
    name = source["name"]
    max_age = source.get("max_age_hours", MAX_AGE_HOURS)
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age)

    print(f"  [{name}] Fetching via monthly sitemap...")

    sitemap_urls = get_sitemap_urls()

    # Fetch both months in parallel
    all_results = []
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(fetch_sitemap, url, cutoff): url
            for url in sitemap_urls
        }
        for future in as_completed(futures):
            try:
                all_results.extend(future.result())
            except Exception as e:
                print(f"    Warning: sitemap fetch error: {e}")

    # Deduplicate by URL
    seen = set()
    deduped = []
    for item in all_results:
        if item["url"] not in seen:
            seen.add(item["url"])
            deduped.append(item)

    # Sort by time descending
    deduped.sort(
        key=lambda x: x["published_fmt"] or "",
        reverse=True
    )

    if not deduped:
        return [], False, "No articles found after filtering"

    print(f"  [{name}] → {len(deduped)} articles (last {max_age}hrs)")
    return deduped, True, ""