"""
method_zaobao.py
----------------
Scrapes 联合早报 (Lianhe Zaobao) using monthly sitemaps + parallel title fetch.

Strategy:
  1. Fetch monthly sitemap (current + previous month)
  2. Filter by target sections and 48-hour lastmod cutoff
  3. Fetch each article page in parallel to extract og:title
  4. Return standard output dict

Target sections (configured in sources.json):
  news/world, news/china, news/sea, finance/world, finance/china

No homepage signal for Zaobao.
"""

import requests
import xml.etree.ElementTree as ET
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}
GOOGLEBOT = {"User-Agent": "Googlebot/2.1 (+http://www.google.com/bot.html)"}

MAX_AGE_HOURS = 48
MAX_TITLE_WORKERS = 20


# ── Utilities ─────────────────────────────────────────────────────────────────

def parse_lastmod(lastmod_str):
    if not lastmod_str:
        return None
    try:
        return datetime.fromisoformat(lastmod_str.replace("Z", "+00:00"))
    except Exception:
        return None


def format_time(dt):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def extract_section(url):
    """
    Extract two-level section from URL path.
    /news/world/story... → WORLD
    /finance/china/story... → CHINA (finance)
    Returns (section_label, parent_category)
    """
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2:
        return parts[1].upper(), parts[0].upper()
    elif parts:
        return parts[0].upper(), ""
    return "GENERAL", ""


def get_sitemap_urls():
    """Current month + previous month sitemap URLs."""
    now = datetime.now(timezone.utc)
    first = now.replace(day=1)
    prev = first - timedelta(days=1)
    return [
        f"https://www.zaobao.com.sg/sitemaps/sitemap-{now.year}{now.month:02d}.xml",
        f"https://www.zaobao.com.sg/sitemaps/sitemap-{prev.year}{prev.month:02d}.xml",
    ]


def is_target_section(url, target_sections):
    """
    Check if URL path matches any of the target sections.
    target_sections: list of path prefixes e.g. ["news/world", "finance/china"]
    """
    path = urlparse(url).path.lstrip("/")
    return any(path.startswith(sec) for sec in target_sections)


# ── Sitemap fetcher ───────────────────────────────────────────────────────────

def fetch_sitemap_entries(sitemap_url, cutoff, target_sections):
    """
    Fetch one monthly sitemap and return filtered (url, lastmod_dt) tuples.
    """
    entries = []
    try:
        resp = requests.get(sitemap_url, headers=GOOGLEBOT, timeout=15)
        resp.raise_for_status()
        root = ET.fromstring(resp.content)
        ns = {'sm': 'http://www.sitemaps.org/schemas/sitemap/0.9'}

        for url_el in root.findall('sm:url', ns):
            loc = url_el.findtext('sm:loc', namespaces=ns)
            lastmod = url_el.findtext('sm:lastmod', namespaces=ns)

            if not loc:
                continue
            if not is_target_section(loc, target_sections):
                continue

            dt = parse_lastmod(lastmod)
            if dt and dt < cutoff:
                continue

            entries.append((loc, dt))

    except Exception as e:
        print(f"    Warning: sitemap fetch failed {sitemap_url}: {e}")

    return entries


# ── Article title fetcher ─────────────────────────────────────────────────────

def fetch_article_title(url):
    """
    Fetch one article page and extract og:title (preferred) or <title>.
    Returns (url, title) tuple.
    """
    try:
        resp = requests.get(url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        resp.encoding = "utf-8"  # force UTF-8 — Zaobao serves UTF-8 but doesn't always declare it
        soup = BeautifulSoup(resp.text, "html.parser")

        # Prefer og:title — actual headline without site suffix
        og = soup.find("meta", property="og:title")
        if og and og.get("content", "").strip():
            return url, og["content"].strip()

        # Fallback: <title> tag, strip " | 联合早报" suffix
        title_tag = soup.find("title")
        if title_tag and title_tag.text.strip():
            title = title_tag.text.strip()
            for suffix in [" | 联合早报", " - 联合早报", " | Zaobao"]:
                title = title.replace(suffix, "")
            return url, title.strip()

    except Exception as e:
        pass  # silently skip — article will be excluded

    return url, ""


def fetch_titles_parallel(entries, max_workers=MAX_TITLE_WORKERS):
    """
    Fetch titles for all entries in parallel.
    Returns dict: {url: title}
    """
    url_to_title = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(fetch_article_title, url): url
            for url, _ in entries
        }
        for future in as_completed(futures):
            url, title = future.result()
            if title:
                url_to_title[url] = title
    return url_to_title


# ── Main entry point ──────────────────────────────────────────────────────────

def scrape_zaobao(source):
    """
    Called by scraper.py.
    Returns: (results list, success bool, error string)
    """
    name = source["name"]
    max_age = source.get("max_age_hours", MAX_AGE_HOURS)
    target_sections = source.get("target_sections", [
        "news/world", "news/china", "news/sea",
        "finance/world", "finance/china"
    ])
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age)

    print(f"  [{name}] Fetching via monthly sitemap...")

    # Step 1: fetch both sitemaps in parallel
    sitemap_urls = get_sitemap_urls()
    all_entries = []

    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = {
            executor.submit(
                fetch_sitemap_entries, url, cutoff, target_sections
            ): url for url in sitemap_urls
        }
        for future in as_completed(futures):
            all_entries.extend(future.result())

    # Deduplicate by URL
    seen = set()
    deduped_entries = []
    for url, dt in all_entries:
        if url not in seen:
            seen.add(url)
            deduped_entries.append((url, dt))

    if not deduped_entries:
        return [], False, "No articles found after section/time filtering"

    print(f"  [{name}] → {len(deduped_entries)} URLs after filtering, fetching titles...")

    # Step 2: fetch titles in parallel
    url_to_title = fetch_titles_parallel(deduped_entries)

    # Step 3: build results
    results = []
    for url, dt in deduped_entries:
        title = url_to_title.get(url, "")
        if not title:
            continue  # skip articles where title fetch failed

        section, parent = extract_section(url)

        results.append({
            "title":         title,
            "url":           url,
            "source":        name,
            "method":        "zaobao",
            "section":       f"{parent}/{section}" if parent else section,
            "language":      "ZH",
            "published_fmt": format_time(dt),
            "summary":       "",
            "homepage":      None,
        })

    # Sort newest first
    results.sort(key=lambda x: x["published_fmt"] or "", reverse=True)

    print(f"  [{name}] → {len(results)} articles with titles")
    return results, True, ""