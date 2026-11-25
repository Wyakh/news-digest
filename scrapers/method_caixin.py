"""
method_caixin.py
----------------
Scrapes 财新网 (caixin.com) using Playwright with shared browser session.

Architecture:
  - One browser launch per scrape run
  - Homepage visited first to establish session cookies
  - All subdomain pages scraped within same browser context
  - Homepage (requests-based) used as importance signal only:
      articles found on homepage → homepage=True
      articles only on homepage → discarded
  - Deduplication by article ID (9-digit number in URL)

Entry points:
  scrape_caixin_subdomains(sources) — called by scraper.py for all caixin_sub sources
  apply_caixin_homepage_signal(results) — called by scraper.py after subdomains
"""

import re
import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse
import asyncio
from playwright.async_api import async_playwright
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from status_report import make_status


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

MAX_AGE_DAYS = 2

SKIP_SUBDOMAINS = {
    "photos", "video", "blog", "mall", "mobile", "corp",
    "conferences", "promote", "course", "mini", "weekly",
    "caixindata", "cxdata", "stock", "entities", "wenews",
    "datanews", "bijiao", "cnreform", "topics", "science",
    "k", "en", "gateway"
}


# ── Utilities ─────────────────────────────────────────────────────────────────

def extract_article_id(url):
    """Extract 9+ digit article ID from URL."""
    match = re.search(r'/(\d{9,})\.html', url)
    return match.group(1) if match else None


def extract_date_from_url(url):
    match = re.search(r'/(\d{4}-\d{2}-\d{2})/', url)
    if match:
        try:
            return datetime.strptime(
                match.group(1), "%Y-%m-%d"
            ).replace(tzinfo=timezone.utc)
        except Exception:
            pass
    return None


def extract_section_from_url(url):
    subdomain = urlparse(url).netloc.split(".")[0]
    return subdomain if subdomain != "www" else "综合"


def is_within_age(url, max_days=MAX_AGE_DAYS):
    dt = extract_date_from_url(url)
    if not dt:
        return True  # keep if no date found
    cutoff = datetime.now(timezone.utc) - timedelta(days=max_days)
    return dt >= cutoff


def parse_articles_from_html(html, source_name, section):
    """Parse article links from rendered HTML. Returns list of article dicts."""
    soup = BeautifulSoup(html, "html.parser")
    results = []
    seen_ids = set()

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip()

        # Strip fragments
        href = href.split("#")[0]

        if "caixin.com" not in href:
            continue
        if not href.startswith("http"):
            continue

        article_id = extract_article_id(href)
        if not article_id:
            continue
        if article_id in seen_ids:
            continue
        if not is_within_age(href):
            continue

        title = (a.get("title") or a.get_text(strip=True) or "").strip()
        if not title or len(title) < 5:
            continue

        seen_ids.add(article_id)
        results.append({
            "title":         title,
            "url":           href,
            "source":        source_name,
            "method":        "caixin",
            "section":       section,
            "language":      "ZH",
            "published_fmt": extract_date_from_url(href).strftime("%Y-%m-%d") if extract_date_from_url(href) else "",
            "summary":       "",
            "article_id":    article_id,
            "homepage":      False,
        })

    return results


# ── Playwright subdomain scraper ──────────────────────────────────────────────

async def scrape_all_subdomains(sources):
    all_results = []
    statuses = []

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent=HEADERS["User-Agent"],
            locale="zh-CN"
        )

        # Establish session via homepage — one page, done once
        print("  [财新] Establishing session via homepage...")
        try:
            home_page = await context.new_page()
            await home_page.goto(
                "https://www.caixin.com",
                wait_until="domcontentloaded",
                timeout=30000
            )
            await home_page.wait_for_timeout(3000)
            await home_page.close()
        except Exception as e:
            print(f"  [财新] Homepage session failed: {e}")
            await browser.close()
            for s in sources:
                statuses.append(make_status(s["name"], False, 0,
                                "Session establishment failed"))
            return [], statuses

        # Scrape all subdomains in parallel — each gets its own tab
        async def scrape_one(source):
            name    = source["name"]
            url     = source["page_url"]
            section = source.get("section_label", "财新")
            print(f"  [{name}] Scraping {url}...")
            page = await context.new_page()
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(3000)
                html = await page.content()
                results = parse_articles_from_html(html, name, section)
                print(f"  [{name}] → {len(results)} articles")
                status = make_status(name, True, len(results), "",
                                     warning=len(results) < 10)
                return results, status
            except Exception as e:
                print(f"    → Failed: {e}")
                return [], make_status(name, False, 0, str(e))
            finally:
                await page.close()

        # Run all tabs in parallel
        tab_results = await asyncio.gather(
            *[scrape_one(s) for s in sources],
            return_exceptions=True
        )

        await browser.close()

    for i, outcome in enumerate(tab_results):
        if isinstance(outcome, Exception):
            statuses.append(make_status(sources[i]["name"], False, 0, str(outcome)))
        else:
            results, status = outcome
            all_results.extend(results)
            statuses.append(status)

    # Dedup across subdomains by article ID
    seen_ids = set()
    deduped = []
    for item in all_results:
        aid = item["article_id"]
        if aid not in seen_ids:
            seen_ids.add(aid)
            deduped.append(item)

    return deduped, statuses


def scrape_caixin_subdomains(sources):
    """Synchronous entry point called by scraper.py."""
    return asyncio.run(scrape_all_subdomains(sources))


# ── Homepage signal ───────────────────────────────────────────────────────────

def fetch_caixin_homepage_ids():
    """
    Fetches www.caixin.com via requests (no Playwright needed).
    Returns set of article IDs appearing on homepage.
    """
    try:
        resp = requests.get("https://www.caixin.com", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        ids = set()
        for a in soup.find_all("a", href=True):
            href = a.get("href","").strip().split("#")[0]
            if "caixin.com" not in href:
                continue
            subdomain = urlparse(href).netloc.split(".")[0]
            if subdomain in SKIP_SUBDOMAINS:
                continue
            aid = extract_article_id(href)
            if aid:
                ids.add(aid)
        return ids, True, ""
    except Exception as e:
        return set(), False, str(e)


def apply_caixin_homepage_signal(subdomain_results):
    print("  [财新-首页] Fetching homepage...")
    try:
        resp = requests.get("https://www.caixin.com", headers=HEADERS, timeout=15)
        resp.raise_for_status()
    except Exception as e:
        print(f"    → Homepage fetch failed: {e}")
        return subdomain_results, 0, 0, str(e)

    soup = BeautifulSoup(resp.text, "html.parser")
    subdomain_id_map = {
        item["article_id"]: item
        for item in subdomain_results
        if item.get("article_id")
    }

    tagged = 0
    added = 0
    seen_ids = set(subdomain_id_map.keys())
    extra_articles = []

    for a in soup.find_all("a", href=True):
        href = a.get("href", "").strip().split("#")[0]
        if not href.startswith("http"):
            continue
        if "caixin.com" not in href:
            continue
        subdomain = urlparse(href).netloc.split(".")[0]
        if subdomain in SKIP_SUBDOMAINS:
            continue
        article_id = extract_article_id(href)
        if not article_id:
            continue
        if article_id in seen_ids:
            if article_id in subdomain_id_map:
                subdomain_id_map[article_id]["homepage"] = True
                tagged += 1
            continue
        seen_ids.add(article_id)

        title = (a.get("title") or a.get_text(strip=True) or "").strip()
        if not title or len(title) < 5:
            continue

        extra_articles.append({
            "title":         title,
            "url":           href,
            "source":        "财新-首页",
            "method":        "caixin",
            "section":       "homepage",
            "language":      "ZH",
            "published_fmt": extract_date_from_url(href).strftime("%Y-%m-%d")
                             if extract_date_from_url(href) else "",
            "summary":       "",
            "article_id":    article_id,
            "homepage":      True,
        })
        added += 1

    print(f"  [财新-首页] → {tagged} subdomain articles tagged, {added} homepage-only articles added")
    return subdomain_results + extra_articles, tagged, added, ""