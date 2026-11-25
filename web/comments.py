"""
comments.py
-----------
Step 6b: Storage for editorial comments anchored to continuity chains.

Each comment is attached to a chain_id (assigned by ai/chain_ids.py).
Comments persist across runs because chain_ids do.

Storage: data/comments.json — a flat array of comment dicts.
Writes are atomic (.tmp + os.replace) so a mid-write crash never
leaves a partial / corrupt file.

No backups. Delete is permanent (per design decision).

Comment shape:
{
  "id":              "cmt_<8 hex>",
  "chain_id":        "chn_<8 hex>",
  "text":            "...",
  "created_run_id":  "YYYY-MM-DD-HHMM",   # the digest the user was viewing
  "created_at":      "YYYY-MM-DDTHH:MM:SSZ",
  "edited_at":       "YYYY-MM-DDTHH:MM:SSZ"
}
"""

import json
import os
import secrets
import tempfile
from datetime import datetime, timezone
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
COMMENTS_PATH = PROJECT_ROOT / "data" / "comments.json"


# ── Internal helpers ─────────────────────────────────────────────────────────
def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mint_comment_id():
    return "cmt_" + secrets.token_hex(4)


def _load_all():
    """Read the entire comments.json. Returns [] if file missing or empty.

    Raises if the file exists but is not valid JSON — that's a real problem
    the caller should see, not silently mask."""
    if not COMMENTS_PATH.exists():
        return []
    raw = COMMENTS_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return []
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"comments.json must be a JSON array, got {type(data).__name__}")
    return data


def _save_all(comments):
    """Write atomically: write to a temp file in the same directory, then
    os.replace (atomic on Windows + POSIX) to swap it into place."""
    COMMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Same directory => same filesystem => atomic rename.
    fd, tmp_path = tempfile.mkstemp(
        prefix=".comments-", suffix=".tmp", dir=str(COMMENTS_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(comments, f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, COMMENTS_PATH)
    except Exception:
        # Best-effort cleanup on failure.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def _find_index(comments, comment_id):
    for i, c in enumerate(comments):
        if c.get("id") == comment_id:
            return i
    return -1


# ── Public API: CRUD ─────────────────────────────────────────────────────────
def add(chain_id, text, created_run_id):
    """Create a new comment. Returns the new comment dict.

    `text` is stripped; empty text raises ValueError (UI must catch this
    and show a friendly message, but enforcement lives here too)."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Comment text cannot be empty.")
    if not chain_id:
        raise ValueError("chain_id is required.")
    if not created_run_id:
        raise ValueError("created_run_id is required.")

    comments = _load_all()
    now = _now_iso()
    new_comment = {
        "id": _mint_comment_id(),
        "chain_id": chain_id,
        "text": text,
        "created_run_id": created_run_id,
        "created_at": now,
        "edited_at": now,
    }
    comments.append(new_comment)
    _save_all(comments)
    return new_comment


def edit(comment_id, text):
    """Update a comment's text and bump edited_at. Returns the updated dict.
    Raises KeyError if comment_id doesn't exist, ValueError if text empty."""
    text = (text or "").strip()
    if not text:
        raise ValueError("Comment text cannot be empty.")

    comments = _load_all()
    idx = _find_index(comments, comment_id)
    if idx < 0:
        raise KeyError(f"Comment not found: {comment_id}")

    comments[idx]["text"] = text
    comments[idx]["edited_at"] = _now_iso()
    _save_all(comments)
    return comments[idx]


def delete(comment_id):
    """Remove a comment. Permanent — no backup, no undo.
    Returns True if a comment was removed, False if it didn't exist
    (idempotent — double-clicking 'Delete' shouldn't error)."""
    comments = _load_all()
    idx = _find_index(comments, comment_id)
    if idx < 0:
        return False
    comments.pop(idx)
    _save_all(comments)
    return True


def get(comment_id):
    """Return one comment dict or None."""
    comments = _load_all()
    idx = _find_index(comments, comment_id)
    return comments[idx] if idx >= 0 else None


# ── Public API: queries for the UI ───────────────────────────────────────────
def list_for_chain(chain_id):
    """All comments for one chain, newest edit first.
    Used by the inline widget on a digest page."""
    comments = [c for c in _load_all() if c.get("chain_id") == chain_id]
    comments.sort(key=lambda c: c.get("edited_at", ""), reverse=True)
    return comments


def chains_with_comments():
    """All chain_ids that have ≥1 comment, with rollup metadata for the
    Notes page row.

    Each entry:
      {
        "chain_id":          "chn_...",
        "comment_count":     int,
        "latest_edited_at":  ISO string,   # most-recent edit anywhere in chain
        "latest_text":       str,          # text of the most-recently-edited comment
      }

    Ordered by latest_edited_at desc — same order the Notes page renders."""
    by_chain = {}
    for c in _load_all():
        cid = c.get("chain_id")
        if not cid:
            continue
        bucket = by_chain.setdefault(cid, {
            "chain_id": cid,
            "comment_count": 0,
            "latest_edited_at": "",
            "latest_text": "",
        })
        bucket["comment_count"] += 1
        if c.get("edited_at", "") > bucket["latest_edited_at"]:
            bucket["latest_edited_at"] = c.get("edited_at", "")
            bucket["latest_text"] = c.get("text", "")
    return sorted(by_chain.values(), key=lambda b: b["latest_edited_at"], reverse=True)


def search_text(query):
    """Comments whose text contains `query` (case-insensitive substring),
    grouped by chain. Returns the same shape as chains_with_comments(),
    filtered to chains with ≥1 matching comment.

    If query is empty, returns chains_with_comments() unchanged."""
    q = (query or "").strip().lower()
    if not q:
        return chains_with_comments()

    matching_chain_ids = {
        c["chain_id"]
        for c in _load_all()
        if c.get("chain_id") and q in (c.get("text", "")).lower()
    }
    return [b for b in chains_with_comments() if b["chain_id"] in matching_chain_ids]


# ── Standalone test ──────────────────────────────────────────────────────────
# Run via: python web/comments.py
# Uses a temporary file path; does not touch real data/comments.json.
if __name__ == "__main__":
    import shutil

    print("Testing comments.py...\n")

    # Redirect storage to a throwaway temp file
    test_dir = Path(tempfile.mkdtemp(prefix="comments-test-"))
    real_path = COMMENTS_PATH
    COMMENTS_PATH = test_dir / "comments.json"
    print(f"Using temp file: {COMMENTS_PATH}\n")

    try:
        # Test 1: empty state
        assert _load_all() == []
        assert chains_with_comments() == []
        assert list_for_chain("chn_anything") == []
        print("Test 1 OK: empty state returns empty lists")

        # Test 2: add three comments across two chains
        c1 = add("chn_aaaa", "First note on chain A", "2026-05-04-1229")
        c2 = add("chn_aaaa", "Second note on chain A", "2026-05-04-1229")
        c3 = add("chn_bbbb", "Note on chain B", "2026-05-05-0900")
        assert c1["id"].startswith("cmt_")
        assert c1["created_at"] == c1["edited_at"]
        assert _load_all().__len__() == 3
        print("Test 2 OK: added 3 comments, IDs minted, timestamps set")

        # Test 3: list_for_chain
        chain_a = list_for_chain("chn_aaaa")
        assert len(chain_a) == 2
        # Newest edit first; c2 was added second so its edited_at >= c1's
        assert chain_a[0]["id"] == c2["id"]
        print("Test 3 OK: list_for_chain returns newest-edit first")

        # Test 4: edit bumps edited_at and reorders
        import time
        time.sleep(1.1)  # ensure ISO-second granularity differs
        edited = edit(c1["id"], "Edited first note")
        assert edited["text"] == "Edited first note"
        assert edited["edited_at"] > edited["created_at"]
        chain_a_after = list_for_chain("chn_aaaa")
        assert chain_a_after[0]["id"] == c1["id"], "edited comment should be on top"
        print("Test 4 OK: edit bumps edited_at, list re-sorts")

        # Test 5: chains_with_comments rollup
        chains = chains_with_comments()
        assert len(chains) == 2
        # chn_aaaa just got an edit, should be on top
        assert chains[0]["chain_id"] == "chn_aaaa"
        assert chains[0]["comment_count"] == 2
        assert chains[0]["latest_text"] == "Edited first note"
        assert chains[1]["chain_id"] == "chn_bbbb"
        assert chains[1]["comment_count"] == 1
        print("Test 5 OK: rollup correct, chain order by latest_edited_at desc")

        # Test 6: search by text (case-insensitive substring)
        results = search_text("EDITED")
        assert len(results) == 1
        assert results[0]["chain_id"] == "chn_aaaa"
        results = search_text("note")
        assert len(results) == 2  # both chains have "note" somewhere
        results = search_text("xyznotpresent")
        assert results == []
        results = search_text("")
        assert len(results) == 2  # empty query = chains_with_comments()
        print("Test 6 OK: search_text case-insensitive substring works")

        # Test 7: delete (existing + nonexistent)
        assert delete(c3["id"]) is True
        assert delete(c3["id"]) is False  # idempotent
        assert delete("cmt_doesnotexist") is False
        chains = chains_with_comments()
        assert len(chains) == 1  # chn_bbbb gone now
        print("Test 7 OK: delete is permanent + idempotent")

        # Test 8: empty text rejected on add and edit
        try:
            add("chn_aaaa", "   ", "2026-05-04-1229")
            assert False, "expected ValueError"
        except ValueError:
            pass
        try:
            edit(c1["id"], "")
            assert False, "expected ValueError"
        except ValueError:
            pass
        print("Test 8 OK: empty text rejected on add and edit")

        # Test 9: edit nonexistent raises KeyError
        try:
            edit("cmt_doesnotexist", "anything")
            assert False, "expected KeyError"
        except KeyError:
            pass
        print("Test 9 OK: edit on missing comment raises KeyError")

        # Test 10: file is well-formed JSON after all ops
        raw = COMMENTS_PATH.read_text(encoding="utf-8")
        data = json.loads(raw)
        assert isinstance(data, list)
        assert len(data) == 2  # c1 (edited) + c2
        print("Test 10 OK: comments.json is valid JSON array")

        print("\nAll comments.py tests passed.")
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
