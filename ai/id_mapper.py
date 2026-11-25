"""
id_mapper.py
------------
Phase 2: H-IDs are now persistent (assigned by the scraper via database.py).
Headlines arrive at the AI engine with an "h_id" field already populated.

This module:
  - Builds a lookup map from h_id -> headline data (for URL reattachment)
  - Tags headlines with a string "id" field "H{h_id}" for AI consumption
  - Reattaches URLs to synthesis output by looking up h_ids
"""


def assign_ids(headlines):
    """
    Headlines arrive with persistent h_id (integer) from the scraper.
    Build:
      - id_map: {"H4521": {full headline data}, "H4522": {...}}
      - id_headlines: same headlines but with "id" string field added
                      (e.g. "H4521") for use in batch processing prompts

    Returns: (id_map, id_headlines)
    """
    id_map = {}
    id_headlines = []
    skipped_no_id = 0

    for h in headlines:
        h_id_int = h.get("h_id")
        if h_id_int is None:
            skipped_no_id += 1
            continue

        hid_str = f"H{h_id_int}"
        id_map[hid_str] = h
        id_headlines.append({**h, "id": hid_str})

    if skipped_no_id:
        print(f"  Warning: {skipped_no_id} headlines had no h_id and were skipped")

    return id_map, id_headlines


def reattach_urls_to_story(story, id_map):
    """
    Given a story dict with either 'headline_ids' (Phase 2) or 'source_ids'
    (Phase 1, transitional), expand into full sources list.
    Source IDs may be integers (e.g. 4521) or strings (e.g. "H4521").
    """
    # Try new field first, fall back to old field
    raw_ids = story.get("headline_ids") or story.get("source_ids") or []
    sources = []
    for h_id in raw_ids:
        if isinstance(h_id, int):
            hid_str = f"H{h_id}"
        elif isinstance(h_id, str):
            hid_str = h_id if h_id.startswith("H") else f"H{h_id}"
        else:
            continue

        h = id_map.get(hid_str)
        if not h:
            continue
        sources.append({
            "id":    hid_str,
            "media": h.get("media", ""),
            "title": h.get("title", ""),
            "url":   h.get("url", ""),
        })
    story["sources"] = sources
    return story


def reattach_urls(synthesis, id_map):
    """
    Walk through synthesis output, expand headline_ids into full source dicts.
    Returns a new synthesis dict with sources populated alongside headline_ids.
    """
    out = dict(synthesis)
    out["stories"] = [
        reattach_urls_to_story(dict(s), id_map)
        for s in synthesis.get("stories", [])
    ]
    return out