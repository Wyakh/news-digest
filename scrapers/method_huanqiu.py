"""
method_huanqiu.py
-----------------
Scrapes 环球网 (huanqiu.com) using two complementary approaches:

1. SUBDOMAIN API: Calls /api/list for each subdomain section.
   Returns full articles with timestamps and summaries.
   Paginated, stops at 48-hour cutoff.

2. HOMEPAGE SIGNAL: Fetches www.huanqiu.com via BeautifulSoup.
   Extracts article IDs from links.
   Tags matching subdomain articles as "homepage" (editorial importance signal).
   Does not add new articles — only tags existing ones.

Entry point: scrape_huanqiu(source) for subdomain sources
             run_homepage_tagging(results) called by scraper.py after all sources
"""

import requests
from bs4 import BeautifulSoup
from datetime import datetime, timezone, timedelta
from urllib.parse import urlparse


HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
}

MAX_AGE_HOURS = 48
ARTICLES_PER_PAGE = 24

SKIP_SUBDOMAINS = {
    "m", "rs1", "rs2", "corp", "paper", "bd", "td",
    "ipengtai", "subscribe", "english", "russian",
    "spanish", "french", "arabic", "v", "look"
}

SUBDOMAIN_NODES = {
    "world.huanqiu.com": '"/e3pmh22ph/e3pmh2398","/e3pmh22ph/e3pmh26vv","/e3pmh22ph/e3pn6efsl","/e3pmh22ph/efp8fqe21"',
    "china.huanqiu.com": '"/e3pmh1nnq/e3pmh1obd","/e3pmh1nnq/e3pn61c2g","/e3pmh1nnq/e3pn6eiep","/e3pmh1nnq/e3pra70uk","/e3pmh1nnq/e5anm31jb","/e3pmh1nnq/e7tl4e309"',
    "mil.huanqiu.com":   '"/e3pmh1dm8/e3pmt7hva","/e3pmh1dm8/e3pmtdr2r","/e3pmh1dm8/e3pn62l96","/e3pmh1dm8/e3pn6f3oh"',
    "taiwan.huanqiu.com": '"/e3pmh1fmg/e3pmh1g6o","/e3pmh1fmg/e3pmt8gfv","/e3pmh1fmg/e3pmt8i3n","/e3pmh1fmg/e3pmt8lic","/e3pmh1fmg/e3pmunk13","/e3pmh1fmg/e3pn6elbj"',
    "opinion.huanqiu.com": '"/e3pmub6h5/e3pmub75a","/e3pmub6h5/e3pn00if8","/e3pmub6h5/e3pn03vit","/e3pmub6h5/e3pn4bi4t","/e3pmub6h5/e3pr9baf6","/e3pmub6h5/e3prafm0g","/e3pmub6h5/e3prcgifj","/e3pmub6h5/e81curi71","/e3pmub6h5/e81cv14rf","/e3pmub6h5/e81cv14rf/e81cv52ha","/e3pmub6h5/e81cv14rf/e81cvaa3q","/e3pmub6h5/e81cv14rf/e81cvcd7e","/e3pmub6h5/e81cv14rf/fiqa8nr3d","/e3pmub6h5/e81cv14rf/fkmm2pfjb","/e3pmub6h5/eo1dckao0","/e3pmub6h5/f1nptsfsh","/e3pmub6h5/fdark2mrt","/e3pmub6h5/e81cvuabi"',
    "finance.huanqiu.com": '"/e3pmh1hmp/e3pmh28kq","/e3pmh1hmp/e3pn61chp","/e3pmh1hmp/e3ptkencb","/e3pmh1hmp/e3pn47c56","/e3pmh1hmp/e3pn61an9","/e3pmh1hmp/e7i6qafud","/e3pmh1hmp/e3pn47igg","/e3pmh1hmp/e3pmh1iab","/e3pmh1hmp/e3pn61831","/e3pmh1hmp/e3pn62ihu","/e3pmh1hmp/e3pmh2bsv","/e3pmh1hmp/e3pmh2bsv/e3pn603rc","/e3pmh1hmp/e3pn61fkq","/e3pmh1hmp/fu13fv4i5","/e3pmh1hmp/g4di1h1pa","/e3pmh1hmp/g4di1pm70","/e3pmh1hmp/g4di1sg18"',
    "tech.huanqiu.com":  '"/e3pmh164r/e3pmh33i9","/e3pmh164r/e3pmtm015","/e3pmh164r/e3pn60k1f","/e3pmh164r/e3pmh3dh4","/e3pmh164r/e3pn46ot6","/e3pmh164r/e3pmtmdvg","/e3pmh164r/e3pmh2hq8","/e3pmh164r/e3pn4sfhb","/e3pmh164r/e3pmtod3t","/e3pmh164r/e3pn4gh77","/e3pmh164r/e3pmtlao3"',
    "capital.huanqiu.com": '"/e5d59phvs/e5d5m10mv","/e5d59phvs/e63j24g2m","/e5d59phvs/evq0lg1h3","/e5d59phvs/evq0lkmga","/e5d59phvs/evq1mo2qf","/e5d59phvs/evq1mo2qf/evq1msmdn","/e5d59phvs/evq1mv740","/e5d59phvs/f29f7sbm1"',
}


# ══════════════════════════════════════════════════════════════════════════════
#  UTILITIES
# ══════════════════════════════════════════════════════════════════════════════

def parse_ctime(ctime_str):
    if not ctime_str:
        return None
    try:
        return datetime.fromtimestamp(int(ctime_str) / 1000, tz=timezone.utc)
    except Exception:
        return None


def format_time(dt):
    if not dt:
        return ""
    return dt.strftime("%Y-%m-%d %H:%M UTC")


def extract_aid_from_url(url):
    path = urlparse(url).path
    parts = [p for p in path.split("/") if p]
    if len(parts) >= 2 and parts[0] in ("article", "gallery"):
        return parts[1]
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  SUBDOMAIN API FETCHER
# ══════════════════════════════════════════════════════════════════════════════

def fetch_api_page(host, nodes, offset, limit):
    url = f"https://{host}/api/list"
    params = {"node": nodes, "offset": offset, "limit": limit}
    resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
    resp.raise_for_status()
    return resp.json()


def fetch_subdomain(host, nodes, section, name, max_age_hours=MAX_AGE_HOURS):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=max_age_hours)
    results = []
    seen = set()
    offset = 0
    limit = ARTICLES_PER_PAGE
    stop = False

    while not stop:
        try:
            data = fetch_api_page(host, nodes, offset, limit)
        except Exception as e:
            if not results:
                return [], False, f"API call failed: {e}"
            break

        items = [i for i in data.get("list", []) if i.get("aid")]
        if not items:
            break

        for item in items:
            aid = item.get("aid", "")
            if aid in seen:
                continue

            ctime_str = item.get("ctime", "")
            dt = parse_ctime(ctime_str)

            if dt and dt < cutoff:
                stop = True
                break

            seen.add(aid)
            title = item.get("title", "").strip()
            if not title:
                continue

            item_host = item.get("host", host)
            article_url = f"https://{item_host}/article/{aid}/"
            summary = item.get("summary", "").strip()

            results.append({
                "title":         title,
                "url":           article_url,
                "source":        name,
                "method":        "huanqiu_api",
                "section":       section,
                "language":      "ZH",
                "published_fmt": format_time(dt),
                "summary":       summary,
                "aid":           aid,
                "homepage":      False,
            })

        if len(items) < limit:
            break

        offset += limit

    return results, True, ""


# ══════════════════════════════════════════════════════════════════════════════
#  HOMEPAGE SIGNAL
# ══════════════════════════════════════════════════════════════════════════════

def fetch_homepage_aids():
    try:
        resp = requests.get("https://www.huanqiu.com", headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        aids = set()

        for a in soup.find_all("a", href=True):
            href = a.get("href", "").strip()
            if href.startswith("//"):
                href = "https:" + href
            if not href.startswith("http"):
                continue
            if "huanqiu.com" not in href:
                continue
            subdomain = urlparse(href).netloc.split(".")[0]
            if subdomain in SKIP_SUBDOMAINS:
                continue
            path = urlparse(href).path
            if not any(t in path for t in ["/article/", "/gallery/"]):
                continue
            aid = extract_aid_from_url(href)
            if aid:
                aids.add(aid)

        return aids, True, ""

    except Exception as e:
        return set(), False, f"Homepage fetch failed: {e}"


def apply_homepage_tags(results, homepage_aids):
    tagged = 0
    for item in results:
        if item.get("aid") in homepage_aids:
            item["homepage"] = True
            tagged += 1
    return tagged


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN ENTRY POINTS
# ══════════════════════════════════════════════════════════════════════════════

def scrape_huanqiu(source):
    name     = source["name"]
    page_url = source["page_url"]
    section  = source.get("section_label", "综合")
    host     = urlparse(page_url).netloc

    nodes = SUBDOMAIN_NODES.get(host)
    if not nodes:
        return [], False, f"No node config found for {host}"

    print(f"  [{name}] Fetching via API: {host}")
    results, success, error = fetch_subdomain(
        host, nodes, section, name,
        max_age_hours=source.get("max_age_hours", MAX_AGE_HOURS)
    )
    print(f"  [{name}] → {len(results)} articles "
          f"(last {source.get('max_age_hours', MAX_AGE_HOURS)}hrs)")
    return results, success, error


def run_homepage_tagging(all_results):
    print("  [环球网-首页] Fetching homepage signal...")
    homepage_aids, success, error = fetch_homepage_aids()

    if not success:
        print(f"    → Homepage signal failed: {error}")
        return all_results

    tagged = apply_homepage_tags(all_results, homepage_aids)
    print(f"    → {len(homepage_aids)} homepage links found, {tagged} articles tagged")
    return all_results