"""
ai_engine.py
------------
Orchestrator for the 4-layer AI digest pipeline.

Phase 2:
- Persistent H-IDs come from headlines.json (assigned by scraper via DB)
- Synthesizer receives past structured JSONs AND past ripped MD descriptions
- Writer receives past skeletons (overview + primary titles+descriptions)
- 12-hour overwrite rule: delete prior run's .md/.json/-summary.txt
  (NEVER .pdf), then save current run with FRESH timestamp
- No more story IDs

Run manually:  python ai/ai_engine.py
"""

import argparse
import os
import sys
import json
import asyncio
from datetime import datetime, timezone

# Path setup
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'ai'))

from batch_processor import process_batch
from synthesizer import synthesize
from writer import write_digest
import digest_storage
import id_mapper
import url_resolver
import chain_ids


HEADLINES_PATH = os.path.join(ROOT, 'data', 'headlines.json')


# ── Batch definitions ─────────────────────────────────────────────────────────
BATCH_CONFIG = [
    {
        "name": "western",
        "label": "Western (Reuters + FT + Straits Times)",
        "media_match": ["Reuters", "Financial Times", "Straits Times"],
        "prompt_file": "prompt_batch_western.txt"
    },
    {
        "name": "chinese_state",
        "label": "Chinese State (环球网)",
        "media_prefix": ["环球网"],
        "prompt_file": "prompt_batch_huanqiu.txt"
    },
    {
        "name": "chinese_indep",
        "label": "Chinese Independent (财新 + 联合早报)",
        "media_prefix": ["财新", "联合早报"],
        "prompt_file": "prompt_batch_chinese_indep.txt"
    },
]


# ── Headline loading and splitting ────────────────────────────────────────────
def load_headlines():
    if not os.path.exists(HEADLINES_PATH):
        return None, "headlines.json not found — run scraper.py first"
    try:
        with open(HEADLINES_PATH, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get("headlines", []), ""
    except Exception as e:
        return None, f"Could not read headlines.json: {e}"


def split_into_batches(headlines):
    batches = {b["name"]: [] for b in BATCH_CONFIG}
    unmatched = []
    for h in headlines:
        media = h.get("media", "")
        matched = False
        for batch in BATCH_CONFIG:
            if "media_match" in batch and media in batch["media_match"]:
                batches[batch["name"]].append(h)
                matched = True
                break
            if "media_prefix" in batch:
                if any(media.startswith(p) for p in batch["media_prefix"]):
                    batches[batch["name"]].append(h)
                    matched = True
                    break
        if not matched:
            unmatched.append(media)

    if unmatched:
        from collections import Counter
        counts = Counter(unmatched)
        print(f"  Warning: {len(unmatched)} headlines from unmatched media:")
        for media, count in counts.most_common(5):
            print(f"    {media}: {count}")

    return batches


# ── Async batch runner ────────────────────────────────────────────────────────
async def run_batches_parallel(batches_data):
    loop = asyncio.get_event_loop()
    tasks = []
    batch_names = []

    for batch_config in BATCH_CONFIG:
        name = batch_config["name"]
        headlines = batches_data.get(name, [])
        prompt_file = batch_config["prompt_file"]
        if headlines:
            tasks.append(loop.run_in_executor(
                None, process_batch, name, headlines, prompt_file
            ))
            batch_names.append(name)
        else:
            print(f"  [Batch: {name}] skipped — no headlines")

    if not tasks:
        return [], 0, 0

    results = await asyncio.gather(*tasks, return_exceptions=True)

    digests = []
    total_in = 0
    total_out = 0
    for name, result in zip(batch_names, results):
        if isinstance(result, Exception):
            print(f"  [Batch: {name}] FAILED with exception: {result}")
            continue
        digest, tin, tout, error = result
        total_in += tin
        total_out += tout
        if error:
            print(f"  [Batch: {name}] FAILED: {error}")
            continue
        digests.append(digest)

    return digests, total_in, total_out


# ── Main runner ───────────────────────────────────────────────────────────────
def run_pipeline(respect_recent_run=True):
    print(f"\n{'='*60}")
    print(f"AI DIGEST PIPELINE — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*60}\n")

    # ── Step 1: Apply 12-hour overwrite rule (delete old, use fresh timestamp) ─
    if respect_recent_run:
        prior_run = digest_storage.find_recent_ai_run(threshold_hours=12)
        if prior_run:
            # Sanity check: warn if the prior run is from a different calendar date
            try:
                prior_dt = datetime.strptime(prior_run, "%Y-%m-%d-%H%M").replace(tzinfo=timezone.utc)
                now = datetime.now(timezone.utc)
                if prior_dt.date() != now.date():
                    print(f"  WARNING: prior run {prior_run} is from a different calendar date — deletion will lose past digest")
            except Exception:
                pass
            print(f"  12-hour rule: deleting prior run {prior_run}")
            digest_storage.delete_run_files(prior_run)
    else:
        print(f"  --no-override: 12-hour rule skipped; previous run kept")

    run_id = digest_storage.make_run_id()
    print(f"  New run: {run_id}\n")

    layer_costs = {}
    summary_lines = [f"Run: {run_id}", ""]

    # ── Step 2: Load headlines (already have persistent H-IDs from scraper) ───
    headlines, error = load_headlines()
    if error:
        print(f"ERROR: {error}")
        return
    print(f"Loaded {len(headlines)} headlines from headlines.json")

    # Verify all headlines have h_id
    missing_ids = sum(1 for h in headlines if h.get("h_id") is None)
    if missing_ids:
        print(f"  Warning: {missing_ids} headlines missing h_id — re-run scraper")

    # ── Step 3: Build id_map for URL reattachment + tag headlines with "id" ──
    id_map, id_headlines = id_mapper.assign_ids(headlines)
    print(f"Built ID map: {len(id_map)} headlines indexed by H-ID")

    # ── Step 4: Load past digests for continuity ─────────────────────────────
    past_structured = digest_storage.load_recent_structured(n=3, exclude_run_id=run_id)
    past_ripped_md = digest_storage.load_recent_ripped_md(n=3, exclude_run_id=run_id)
    past_skeletons = digest_storage.load_recent_skeletons(n=3, exclude_run_id=run_id)
    print(f"Past context: {len(past_structured)} structured JSONs, "
          f"{len(past_ripped_md)} ripped MDs, "
          f"{len(past_skeletons)} skeletons")
    summary_lines.append(
        f"Past context: {len(past_structured)} JSONs, {len(past_ripped_md)} ripped MDs"
    )

    # ── Step 5: Split into batches ───────────────────────────────────────────
    batches_data = split_into_batches(id_headlines)
    print(f"\nBatch distribution:")
    for batch_config in BATCH_CONFIG:
        name = batch_config["name"]
        count = len(batches_data.get(name, []))
        print(f"  {batch_config['label']}: {count} headlines")
    summary_lines.append(f"Batches: " + " | ".join(
        f"{b['name']}={len(batches_data.get(b['name'], []))}" for b in BATCH_CONFIG
    ))

    # ── Step 6: Layer 2 — batch processing ───────────────────────────────────
    print(f"\n--- Layer 2: Batch Processing ---")
    batch_digests, batch_in, batch_out = asyncio.run(
        run_batches_parallel(batches_data)
    )
    layer_costs["batch_total"] = {"in": batch_in, "out": batch_out}

    expected_batches = sum(
        1 for b in BATCH_CONFIG
        if batches_data.get(b["name"])
    )

    if len(batch_digests) < expected_batches:
        print(f"ERROR: Only {len(batch_digests)}/{expected_batches} batches succeeded.")
        summary_lines.append(
            f"FAILED: Only {len(batch_digests)}/{expected_batches} batches succeeded"
        )
        digest_storage.save_summary(run_id, summary_lines)
        digest_storage.append_cost_log(run_id, layer_costs)
        return

    summary_lines.append(f"Batch digests produced: {len(batch_digests)}/3")

    # ── Step 7: Layer 3 — synthesis with continuity context ──────────────────
    print(f"\n--- Layer 3: Synthesis ---")
    synthesis, syn_in, syn_out, error = synthesize(
        batch_digests,
        past_digests=past_structured,
        past_ripped_md=past_ripped_md
    )
    layer_costs["synthesis"] = {"in": syn_in, "out": syn_out}

    if error:
        print(f"ERROR: Synthesis failed: {error}")
        summary_lines.append(f"FAILED at synthesis: {error}")
        digest_storage.save_summary(run_id, summary_lines)
        digest_storage.append_cost_log(run_id, layer_costs)
        return

    story_count = len(synthesis.get("stories", []))
    primary = sum(1 for s in synthesis["stories"] if s.get("tier") == "primary")
    secondary = sum(1 for s in synthesis["stories"] if s.get("tier") == "secondary")
    signal = sum(1 for s in synthesis["stories"] if s.get("tier") == "signal")
    developing = sum(1 for s in synthesis["stories"] if s.get("is_developing"))
    no_new = sum(1 for s in synthesis["stories"] if s.get("no_new_info"))
    significant = sum(1 for s in synthesis["stories"] if s.get("is_continuation_significant"))

    summary_lines.append(
        f"Stories: {story_count} (primary={primary}, secondary={secondary}, signal={signal})"
    )
    summary_lines.append(
        f"Continuity: {developing} developing, {no_new} no-new-info, {significant} significant escalations"
    )
    print(f"  [Continuity] {developing} developing, {no_new} no-new-info, {significant} significant")


    # ── Step 7b: Assign chain_ids for continuity tracking ─────────────────────
    new_n, inh_n, brk_n = chain_ids.assign_chain_ids(synthesis, past_structured)
    print(f"  [Chain IDs] {inh_n} inherited, {new_n} new, {brk_n} broken predecessor")
    summary_lines.append(f"Chain IDs: {inh_n} inherited, {new_n} new, {brk_n} broken")

    # ── Step 8: Reattach URLs from id_map ────────────────────────────────────
    print(f"\n--- Reattaching URLs from ID map ---")
    synthesis = id_mapper.reattach_urls(synthesis, id_map)
    total_sources = sum(len(s.get("sources", [])) for s in synthesis.get("stories", []))
    print(f"  → {total_sources} total source links reattached across {story_count} stories")

    # ── Step 9: Layer 4 — writer with skeleton context ───────────────────────
    print(f"\n--- Layer 4: Writer ---")
    markdown, structured, wr_in, wr_out, error = write_digest(
        synthesis,
        past_briefs=past_skeletons
    )
    layer_costs["writer"] = {"in": wr_in, "out": wr_out}

    if error:
        print(f"ERROR: Writer failed: {error}")
        summary_lines.append(f"FAILED at writer: {error}")
        digest_storage.save_summary(run_id, summary_lines)
        digest_storage.append_cost_log(run_id, layer_costs)
        return

    # ── Step 9b: Reconcile chain_ids in case the writer dropped them ─────────
    restored = chain_ids.reconcile_chain_ids(structured, synthesis)
    if restored:
        print(f"  [Chain IDs] {restored} restored after writer pass (writer drops unknown fields)")


    # ── Step 10: Resolve URL placeholders ────────────────────────────────────
    print(f"\n--- Resolving URL placeholders ---")
    markdown, resolve_stats = url_resolver.resolve_placeholders(markdown, id_map)
    print(f"  → {resolve_stats['resolved']} placeholders resolved, "
          f"{resolve_stats['missing']} missing, "
          f"{resolve_stats['leaked_urls']} leaked URLs")
    if resolve_stats['missing_ids']:
        print(f"  Warning: missing IDs: {resolve_stats['missing_ids'][:10]}")
    summary_lines.append(
        f"URL resolution: {resolve_stats['resolved']} ok, "
        f"{resolve_stats['missing']} missing, "
        f"{resolve_stats['leaked_urls']} leaked"
    )

    # ── Step 11: Save outputs ────────────────────────────────────────────────
    print(f"\n--- Saving outputs ---")
    md_path = digest_storage.save_markdown(run_id, markdown)
    json_path = digest_storage.save_structured(run_id, structured)
    print(f"  Markdown: {md_path}")
    print(f"  JSON:     {json_path}")

    # Token totals + cost
    total_in = batch_in + syn_in + wr_in
    total_out = batch_out + syn_out + wr_out
    summary_lines.append("")
    summary_lines.append(f"Tokens: {total_in} in / {total_out} out (total: {total_in + total_out})")

    # Load model assignments and pricing.
    # Models can be edited in config/config.json.
    # Prices can be edited in config/pricing.json.
    config_path = os.path.join(ROOT, 'config', 'config.json')
    pricing_path = os.path.join(ROOT, 'config', 'pricing.json')
    with open(config_path, 'r', encoding='utf-8') as f:
        cfg = json.load(f)
    with open(pricing_path, 'r', encoding='utf-8') as f:
        pricing = json.load(f)
    models = cfg.get("models", {})
    llm_settings = cfg.get("llm_settings", {})
    
    summary_lines.append(
        "Models: " + json.dumps(models, ensure_ascii=False, separators=(",", ":"))
    )
    summary_lines.append(
        "LLM settings: " + json.dumps(llm_settings, ensure_ascii=False, separators=(",", ":"))
    )

    def _layer_cost(tin, tout, model_name):
        p = pricing.get(model_name)
        if not p:
            print(f"  Warning: no pricing for model '{model_name}', cost set to 0")
            return 0.0
        return (tin / 1_000_000) * p["input_per_1m"] + (tout / 1_000_000) * p["output_per_1m"]

    batch_cost = _layer_cost(batch_in, batch_out, models.get("batch", ""))
    syn_cost   = _layer_cost(syn_in,   syn_out,   models.get("synthesis", ""))
    wr_cost    = _layer_cost(wr_in,    wr_out,    models.get("writer", ""))
    cost_estimate = batch_cost + syn_cost + wr_cost

    # Store per-layer cost into layer_costs so it gets written into cost_log.json.
    # This locks in the cost at write time — past entries don't change if pricing edits happen later.
    layer_costs["batch_total"]["cost"] = round(batch_cost, 4)
    layer_costs["synthesis"]["cost"]   = round(syn_cost,   4)
    layer_costs["writer"]["cost"]      = round(wr_cost,    4)

    summary_lines.append(f"Estimated cost: ${cost_estimate:.4f}")

    digest_storage.save_summary(run_id, summary_lines)
    digest_storage.append_cost_log(run_id, layer_costs)

    print(f"\n{'='*60}")
    print(f"COMPLETE")
    print(f"  Total tokens: {total_in + total_out}")
    print(f"  Estimated cost: ${cost_estimate:.4f}")
    print(f"  Output: {md_path}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AI digest pipeline")
    parser.add_argument(
        "--no-override",
        action="store_true",
        help="Skip the 12-hour rule; previous digest files are kept and a new "
             "run is saved alongside.",
    )
    args = parser.parse_args()
    run_pipeline(respect_recent_run=not args.no_override)