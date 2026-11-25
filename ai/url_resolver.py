"""
url_resolver.py
---------------
Post-processes writer output to replace [Hxx] placeholders with
actual markdown links from the id_map.

Patterns handled:
  [H47]                 → [Reuters](https://reuters.com/...)
  [H47, H123, H891]     → [Reuters](url1), [FT](url2), [Straits Times](url3)

Also detects and warns about any URLs the writer leaked directly
(in case it didn't follow instructions).
"""

import re


# Match single placeholder [H47]
SINGLE_PATTERN = re.compile(r'\[H(\d+)\]')

# Match leaked URLs for diagnostic warning
URL_PATTERN = re.compile(r'https?://[^\s<>")\]]+')


def resolve_placeholders(markdown, id_map):
    """
    Replace [Hxx] placeholders in markdown with full markdown links
    drawing media name and URL from id_map.

    Returns: (resolved_markdown, stats_dict)
      stats_dict: {"resolved": N, "missing": N, "leaked_urls": N}
    """
    stats = {"resolved": 0, "missing": 0, "leaked_urls": 0, "missing_ids": []}

    def replace_one(match):
        hid = "H" + match.group(1)
        h = id_map.get(hid)
        if not h:
            stats["missing"] += 1
            stats["missing_ids"].append(hid)
            return match.group(0)
        media = h.get("media", "")
        url = h.get("url", "")
        title = h.get("title", "")
        if not url:
            stats["missing"] += 1
            return f"[{media}] — {title}"
        stats["resolved"] += 1
        return f"[{media}]({url}) — {title}"

    # Diagnostic: count URLs in the WRITER's output (before resolution)
    # These would be URLs the writer typed directly instead of using placeholders
    pre_resolution_urls = URL_PATTERN.findall(markdown)
    stats["leaked_urls"] = len(pre_resolution_urls)

    resolved = SINGLE_PATTERN.sub(replace_one, markdown)

    return resolved, stats


# ── Standalone test ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    test_md = """
# Test
Iran story covered by [H1], [H2], and [H3].

## Single source
- **Reuters** [H4]: leaked URL https://example.com here
"""
    test_id_map = {
        "H1": {"media": "Reuters", "url": "https://reuters.com/iran"},
        "H2": {"media": "FT", "url": "https://ft.com/iran"},
        "H3": {"media": "环球网", "url": "https://huanqiu.com/iran"},
        "H4": {"media": "财新", "url": ""},  # missing URL
    }
    resolved, stats = resolve_placeholders(test_md, test_id_map)
    print("RESOLVED:")
    print(resolved)
    print("\nSTATS:", stats)