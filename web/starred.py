"""
starred.py
----------
Step 6e (part 1): persistent star toggles for continuity chains.

Storage: data/starred_chains.json — a JSON array of chain_id strings.
Atomic writes (.tmp + os.replace), same pattern as comments.py.
"""

import json
import os
import tempfile
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
STARRED_PATH = PROJECT_ROOT / "data" / "starred_chains.json"


def _load_all():
    """Read the starred set. Returns set() if missing or empty.
    Raises ValueError on malformed JSON — bad data should be visible."""
    if not STARRED_PATH.exists():
        return set()
    raw = STARRED_PATH.read_text(encoding="utf-8").strip()
    if not raw:
        return set()
    data = json.loads(raw)
    if not isinstance(data, list):
        raise ValueError(f"starred_chains.json must be a JSON array, got {type(data).__name__}")
    return set(data)


def _save_all(chain_ids_set):
    """Atomic write: temp file in same dir, then os.replace."""
    STARRED_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".starred-", suffix=".tmp", dir=str(STARRED_PATH.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            # Sorted output for stable diffs / inspectability.
            json.dump(sorted(chain_ids_set), f, indent=2, ensure_ascii=False)
        os.replace(tmp_path, STARRED_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def get_all():
    """Return the set of starred chain_ids."""
    return _load_all()


def is_starred(chain_id):
    return chain_id in _load_all()


def toggle(chain_id):
    """Add chain_id if not present, remove if present.
    Returns the new state (True = starred, False = unstarred)."""
    if not chain_id:
        raise ValueError("chain_id is required")
    starred = _load_all()
    if chain_id in starred:
        starred.discard(chain_id)
        new_state = False
    else:
        starred.add(chain_id)
        new_state = True
    _save_all(starred)
    return new_state


# Standalone test
if __name__ == "__main__":
    import shutil

    print("Testing starred.py...\n")
    test_dir = Path(tempfile.mkdtemp(prefix="starred-test-"))
    real_path = STARRED_PATH
    STARRED_PATH = test_dir / "starred_chains.json"

    try:
        assert _load_all() == set()
        assert is_starred("chn_x") is False
        print("Test 1 OK: empty state")

        assert toggle("chn_a") is True
        assert is_starred("chn_a") is True
        assert toggle("chn_b") is True
        assert get_all() == {"chn_a", "chn_b"}
        print("Test 2 OK: toggle adds, get_all returns set")

        assert toggle("chn_a") is False
        assert is_starred("chn_a") is False
        assert get_all() == {"chn_b"}
        print("Test 3 OK: toggle removes")

        # File is well-formed JSON array, sorted
        data = json.loads(STARRED_PATH.read_text(encoding="utf-8"))
        assert data == ["chn_b"]
        print("Test 4 OK: file is valid sorted JSON array")

        try:
            toggle("")
            assert False, "expected ValueError"
        except ValueError:
            pass
        print("Test 5 OK: empty chain_id rejected")

        print("\nAll starred.py tests passed.")
    finally:
        shutil.rmtree(test_dir, ignore_errors=True)
