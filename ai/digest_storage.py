"""
digest_storage.py
-----------------
Handles all digest file I/O for the AI pipeline.

Phase 2:
  - Story IDs ABANDONED — continuity uses persistent H-ID overlap
  - 12-hour overwrite rule: re-run within 12 hours deletes prior run's
    .md, .json, -summary.txt (NOT .pdf), and saves fresh files at current time
  - load_recent_structured(n) — past JSON digests for synthesizer
  - load_recent_skeletons(n)  — past markdown skeletons for writer
  - load_recent_ripped_md(n)  — past markdown ripped of URLs for synthesizer

Files written per run:
  data/digests/YYYY-MM-DD-HHMM.md
  data/digests/YYYY-MM-DD-HHMM.json
  data/digests/YYYY-MM-DD-HHMM-summary.txt
  data/cost_log.json (appended)
"""

import os
import json
import re
from datetime import datetime, timezone, timedelta
from glob import glob


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIGEST_DIR = os.path.join(ROOT, 'data', 'digests')
COST_LOG_PATH = os.path.join(ROOT, 'data', 'cost_log.json')


# ── Setup ─────────────────────────────────────────────────────────────────────
def ensure_directory():
    os.makedirs(DIGEST_DIR, exist_ok=True)


def make_run_id():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d-%H%M")


# ── 12-hour overwrite rule ────────────────────────────────────────────────────
def find_recent_ai_run(threshold_hours=12):
    ensure_directory()
    # Check all three artifact types — a partial run may have only some
    candidates = []
    for ext in [".json", ".md", "-summary.txt"]:
        candidates.extend(glob(os.path.join(DIGEST_DIR, f"*{ext}")))
    if not candidates:
        return None
    
    # Extract run_id from each filename and find the newest
    run_ids = set()
    for path in candidates:
        basename = os.path.basename(path)
        for ext in [".json", ".md", "-summary.txt"]:
            if basename.endswith(ext):
                run_ids.add(basename[:-len(ext)])
                break
    
    if not run_ids:
        return None
    
    basename = max(run_ids)  # lexicographic sort works for YYYY-MM-DD-HHMM


def delete_run_files(run_id):
    """
    Delete .md, .json, -summary.txt for a run_id.
    Explicitly does NOT touch .pdf files (user-generated, preserved).
    """
    ensure_directory()
    deleted = []
    for ext in [".md", ".json", "-summary.txt"]:
        path = os.path.join(DIGEST_DIR, run_id + ext)
        if os.path.exists(path):
            try:
                os.remove(path)
                deleted.append(os.path.basename(path))
            except Exception as e:
                print(f"  Warning: could not delete {path}: {e}")
    if deleted:
        print(f"  Deleted prior run files: {', '.join(deleted)}")


# ── Save functions ────────────────────────────────────────────────────────────
def save_markdown(run_id, markdown_content):
    ensure_directory()
    path = os.path.join(DIGEST_DIR, f"{run_id}.md")
    with open(path, 'w', encoding='utf-8') as f:
        f.write(markdown_content)
    return path


def save_structured(run_id, structured_data):
    ensure_directory()
    path = os.path.join(DIGEST_DIR, f"{run_id}.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(structured_data, f, indent=2, ensure_ascii=False)
    return path


def save_summary(run_id, summary_lines):
    ensure_directory()
    path = os.path.join(DIGEST_DIR, f"{run_id}-summary.txt")
    with open(path, 'w', encoding='utf-8') as f:
        f.write("\n".join(summary_lines))
    return path


def append_cost_log(run_id, layer_costs):
    ensure_directory()
    log_entry = {
        "run_id": run_id,
        "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "layers": layer_costs
    }

    if os.path.exists(COST_LOG_PATH):
        try:
            with open(COST_LOG_PATH, 'r', encoding='utf-8') as f:
                log = json.load(f)
        except Exception:
            log = []
    else:
        log = []

    log.append(log_entry)

    with open(COST_LOG_PATH, 'w', encoding='utf-8') as f:
        json.dump(log, f, indent=2, ensure_ascii=False)


# ── Load functions for Phase 2 continuity ────────────────────────────────────
def list_recent_digests(n=3, exclude_run_id=None, ext=".json"):
    """
    Return paths to the N most recent files of given extension, newest first.
    """
    ensure_directory()
    pattern = os.path.join(DIGEST_DIR, f"*{ext}")
    files = sorted(glob(pattern), reverse=True)
    if exclude_run_id:
        excluded = os.path.join(DIGEST_DIR, exclude_run_id + ext)
        files = [f for f in files if os.path.abspath(f) != os.path.abspath(excluded)]
    return files[:n]


def load_recent_structured(n=3, exclude_run_id=None):
    """
    Load the last N structured digests as list of dicts.
    Each dict has _run_id added for reference.
    Used by synthesizer for continuity matching via headline_ids overlap.
    """
    paths = list_recent_digests(n, exclude_run_id, ext=".json")
    digests = []
    for p in paths:
        try:
            with open(p, 'r', encoding='utf-8') as f:
                d = json.load(f)
            run_id = os.path.basename(p).replace(".json", "")
            d["_run_id"] = run_id
            digests.append(d)
        except Exception as e:
            print(f"  Warning: could not load {p}: {e}")
    return digests


def load_recent_ripped_md(n=3, exclude_run_id=None):
    """
    Load the last N markdown digests, ripped of URLs and source bullets.
    For each digest, returns: run_id + Important+Secondary stories' titles
    and descriptions only. Used by synthesizer alongside structured JSON.
    """
    paths = list_recent_digests(n, exclude_run_id, ext=".md")
    out = []
    for path in paths:
        run_id = os.path.basename(path).replace(".md", "")
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            ripped = _rip_md_for_synthesizer(content)
            if ripped:
                out.append({"run_id": run_id, "ripped": ripped})
        except Exception as e:
            print(f"  Warning: could not rip {path}: {e}")
    return out


def load_recent_skeletons(n=3, exclude_run_id=None):
    """
    Extract a compact 'skeleton' from each of the last N markdown digests:
      - the Today's Overview paragraph
      - each Important Story's title and description
    Used by writer for narrative tone continuity.
    """
    paths = list_recent_digests(n, exclude_run_id, ext=".md")

    skeletons = []
    for path in paths:
        run_id = os.path.basename(path).replace(".md", "")
        try:
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
            overview = _extract_overview_section(content)
            primary = _extract_primary_skeleton(content)
            if overview or primary:
                skeletons.append({
                    "run_id": run_id,
                    "overview": overview,
                    "primary_titles_and_descriptions": primary
                })
        except Exception as e:
            print(f"  Warning: could not extract skeleton from {path}: {e}")
    return skeletons


# ── Internal regex helpers ───────────────────────────────────────────────────
def _extract_overview_section(markdown):
    """Extract text between '## Today's Overview' and the next '##' heading."""
    match = re.search(
        r"##\s*Today'?s?\s*Overview\s*\n+(.*?)(?=\n##\s|\Z)",
        markdown,
        re.DOTALL | re.IGNORECASE
    )
    if match:
        return match.group(1).strip()
    return ""


def _extract_primary_skeleton(markdown):
    """
    From Important Stories AND Secondary Stories sections, extract each
    ### title + description pair. Skip Tags/Sources/Continued covering.
    Returns compact text representation for writer's tone continuity.
    """
    out = []

    for section_name in ["Important Stories", "Secondary Stories"]:
        section_match = re.search(
            rf"##\s*{re.escape(section_name)}\s*\n+(.*?)(?=\n##\s|\Z)",
            markdown,
            re.DOTALL | re.IGNORECASE
        )
        if not section_match:
            continue

        section = section_match.group(1)

        stories = re.findall(
            r"###\s*(.+?)\n+(.+?)(?=\n\*\*Tags|\n###|\Z)",
            section,
            re.DOTALL
        )

        for title, desc in stories:
            title = title.strip()
            desc = desc.strip()
            if title.startswith("Continued covering"):
                continue
            sentences = re.split(r'(?<=[.!?])\s+', desc)
            compact_desc = " ".join(sentences[:2]).strip()
            out.append(f"- {title}\n  {compact_desc}")

    return "\n".join(out)


def _rip_md_for_synthesizer(markdown):
    """
    Rip a writer's .md file down to title+description per story for both
    Important Stories and Secondary Stories. Strip Tags/Sources/URL bullets.
    Skip Today's Overview, Notable Single-Source Items, "Still developing".

    Output format:
      ### Story title
      Description text.

      ### Another title
      Description text.
    """
    output_chunks = []

    for section_name in ["Important Stories", "Secondary Stories"]:
        section_match = re.search(
            rf"##\s*{re.escape(section_name)}\s*\n+(.*?)(?=\n##\s|\Z)",
            markdown,
            re.DOTALL | re.IGNORECASE
        )
        if not section_match:
            continue

        section = section_match.group(1)

        # Match each story: ### title + everything until next ### or **Tags
        stories = re.findall(
            r"###\s*(.+?)\n+(.+?)(?=\n\*\*Tags|\n###|\Z)",
            section,
            re.DOTALL
        )

        if stories:
            output_chunks.append(f"### Section: {section_name}")
            for title, desc in stories:
                title = title.strip()
                desc = desc.strip()
                # Skip "Continued covering" — one-sentence placeholder, not substantive
                if title.startswith("Continued covering"):
                    continue
                # Drop italic continuation lines
                desc = re.sub(r"^\*Continuing.*?\*\s*$", "", desc, flags=re.MULTILINE).strip()
                output_chunks.append(f"### {title}\n{desc}")
                
    return "\n\n".join(output_chunks)


# ── Standalone test ───────────────────────────────────────────────────────────
if __name__ == "__main__":
    print("Testing digest_storage.py Phase 2 v2...\n")

    print("Test 1: find_recent_ai_run")
    r = find_recent_ai_run(threshold_hours=12)
    print(f"  Recent run within 12hrs: {r}\n")

    print("Test 2: load_recent_structured")
    s = load_recent_structured(n=3)
    print(f"  Recent structured: {len(s)} digests\n")

    print("Test 3: load_recent_skeletons")
    sk = load_recent_skeletons(n=3)
    print(f"  Recent skeletons: {len(sk)}")
    if sk:
        print(f"    First overview: {sk[0]['overview'][:100]}...")
    print()

    print("Test 4: load_recent_ripped_md")
    r = load_recent_ripped_md(n=3)
    print(f"  Recent ripped: {len(r)}")
    if r:
        print(f"    First sample: {r[0]['ripped'][:200]}...")
    print()

    print("All Phase 2 storage tests complete.")