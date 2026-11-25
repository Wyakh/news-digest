"""
chain_ids.py
------------
Step 6a: Continuity chain identifiers.

Each story in a synthesizer-produced JSON gets a `chain_id` field that
persists across runs as long as the synthesizer correctly identifies a
predecessor via `previous_reference_run` + `previous_reference_title`.

The chain_id is minted deterministically in Python — NOT by the LLM —
to avoid hallucinated/colliding IDs. The synthesizer's job is only to
identify the predecessor; this module does the bookkeeping.

Format: "chn_" + 8 hex chars (e.g. "chn_a3f4d2e1"). 32 bits of entropy
is plenty for a single-user local app.

This module is read-only on past digests; it does not modify them. It
mutates the in-memory synthesis dict by adding `chain_id` to each story.
"""

import secrets


def mint_chain_id():
    """Generate a new chain_id. 32 bits of randomness, prefixed."""
    return "chn_" + secrets.token_hex(4)


def build_past_chain_index(past_structured):
    """Build a lookup: (run_id, canonical_title) -> chain_id.

    Reads from digests already loaded by digest_storage.load_recent_structured().
    Each digest dict has '_run_id' or 'run_id' set.

    Stories without a chain_id (e.g. pre-deployment digests on Day 1) are
    skipped silently — they cannot serve as predecessors.
    """
    index = {}
    for d in past_structured:
        run_id = d.get("_run_id") or d.get("run_id")
        if not run_id:
            continue
        for s in d.get("stories", []):
            title = s.get("canonical_title")
            chain_id = s.get("chain_id")
            if title and chain_id:
                index[(run_id, title)] = chain_id
    return index


def assign_chain_ids(synthesis, past_structured):
    """Assign chain_id to every story in the synthesis dict (mutated in place).

    For each story:
      - If previous_reference_run + previous_reference_title resolve to a past
        story that has a chain_id → inherit it (continuing chain).
      - Otherwise → mint a new chain_id (new chain, OR a chain whose
        predecessor isn't in our 3-run window, OR the synthesizer's pointer
        is broken).

    Returns (new, inherited, broken_pointer) counts for logging.
      new              = no previous_reference_run set (genuinely new story)
      inherited        = predecessor found in index
      broken_pointer   = predecessor referenced but not found
                          (Day 1 norm; later, anomalies worth noting)
    """
    index = build_past_chain_index(past_structured)
    new_count = 0
    inherited_count = 0
    broken_count = 0

    for story in synthesis.get("stories", []):
        prev_run = story.get("previous_reference_run")
        prev_title = story.get("previous_reference_title")

        if prev_run and prev_title:
            chain_id = index.get((prev_run, prev_title))
            if chain_id:
                story["chain_id"] = chain_id
                inherited_count += 1
            else:
                # Predecessor pointer set but not resolvable. Causes:
                #   - Day 1 (past JSONs have no chain_ids yet)
                #   - Predecessor older than the n=3 window of past_structured
                #   - Synthesizer hallucinated/typoed the pointer
                # In all cases: treat as new chain. The chain "breaks" but
                # the story still gets an ID and can accumulate comments.
                story["chain_id"] = mint_chain_id()
                broken_count += 1
        else:
            story["chain_id"] = mint_chain_id()
            new_count += 1

    return new_count, inherited_count, broken_count


def reconcile_chain_ids(structured, synthesis):
    """Defensive: ensure chain_ids present in `synthesis` are also present in
    `structured` (the writer's output). If the writer pass strips unknown
    fields, this restores them by matching on canonical_title.

    Returns the count of chain_ids restored. If the writer preserved the
    field, this is 0 (no-op).
    """
    if not synthesis or not structured:
        return 0
    title_to_chain = {
        s.get("canonical_title"): s.get("chain_id")
        for s in synthesis.get("stories", [])
        if s.get("canonical_title") and s.get("chain_id")
    }
    restored = 0
    for s in structured.get("stories", []):
        if s.get("chain_id"):
            continue
        cid = title_to_chain.get(s.get("canonical_title"))
        if cid:
            s["chain_id"] = cid
            restored += 1
    return restored


# ── Standalone test ──────────────────────────────────────────────────────────
# Run via: python ai/chain_ids.py
# Tests with synthetic data; does not touch any real digest files.
if __name__ == "__main__":
    print("Testing chain_ids.py...\n")

    # Test 1: mint produces unique IDs of expected shape
    a, b = mint_chain_id(), mint_chain_id()
    assert a.startswith("chn_") and len(a) == 12
    assert a != b
    print(f"Test 1 OK: minted {a}, {b}")

    # Test 2: index built correctly from past data
    past = [
        {
            "_run_id": "2026-05-01-0900",
            "stories": [
                {"canonical_title": "Story A", "chain_id": "chn_aaaa1111"},
                {"canonical_title": "Story B", "chain_id": "chn_bbbb2222"},
            ],
        },
        {
            "_run_id": "2026-05-02-0900",
            "stories": [
                # Day 1 simulation: this story has no chain_id, should be skipped
                {"canonical_title": "Story Z"},
            ],
        },
    ]
    idx = build_past_chain_index(past)
    assert idx == {
        ("2026-05-01-0900", "Story A"): "chn_aaaa1111",
        ("2026-05-01-0900", "Story B"): "chn_bbbb2222",
    }
    print(f"Test 2 OK: index has {len(idx)} entries (Story Z skipped, no chain_id)")

    # Test 3: inheritance, new, broken-pointer counts
    synthesis = {
        "stories": [
            {  # inherits from Story A
                "canonical_title": "Story A continues",
                "previous_reference_run": "2026-05-01-0900",
                "previous_reference_title": "Story A",
            },
            {  # genuinely new
                "canonical_title": "Story C",
                "previous_reference_run": None,
                "previous_reference_title": None,
            },
            {  # broken pointer (typo in title)
                "canonical_title": "Story D",
                "previous_reference_run": "2026-05-01-0900",
                "previous_reference_title": "Story typo",
            },
        ]
    }
    new_n, inh_n, brk_n = assign_chain_ids(synthesis, past)
    assert (new_n, inh_n, brk_n) == (1, 1, 1), f"expected (1,1,1), got ({new_n},{inh_n},{brk_n})"
    assert synthesis["stories"][0]["chain_id"] == "chn_aaaa1111"  # inherited
    assert synthesis["stories"][1]["chain_id"].startswith("chn_")  # new
    assert synthesis["stories"][2]["chain_id"].startswith("chn_")  # broken → fresh
    assert synthesis["stories"][1]["chain_id"] != synthesis["stories"][2]["chain_id"]
    print(f"Test 3 OK: 1 inherited (chn_aaaa1111), 1 new, 1 broken-pointer")

    # Test 4: reconciliation restores chain_ids dropped by a writer
    structured_after_writer = {
        "stories": [
            # Same titles, no chain_id (simulating writer dropping the field)
            {"canonical_title": "Story A continues"},
            {"canonical_title": "Story C"},
            {"canonical_title": "Story D"},
        ]
    }
    restored = reconcile_chain_ids(structured_after_writer, synthesis)
    assert restored == 3
    assert structured_after_writer["stories"][0]["chain_id"] == "chn_aaaa1111"
    print(f"Test 4 OK: {restored} chain_ids restored after simulated writer drop")

    # Test 5: reconciliation is a no-op when writer preserved the field
    structured_preserved = {
        "stories": [
            {"canonical_title": "Story A continues", "chain_id": "chn_aaaa1111"},
        ]
    }
    restored = reconcile_chain_ids(structured_preserved, synthesis)
    assert restored == 0
    print(f"Test 5 OK: 0 restored when writer preserved chain_ids (no-op)")

    print("\nAll chain_ids.py tests passed.")
