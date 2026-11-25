"""
Helpers for reading pricing config and computing costs from cost_log entries.

Used by app.py for the /cost route.
"""

import re
import json
from datetime import datetime
from pathlib import Path



PROJECT_ROOT = Path(__file__).resolve().parent.parent
PRICING_PATH = PROJECT_ROOT / "config" / "pricing.json"
CONFIG_PATH = PROJECT_ROOT / "config" / "config.json"

LAYER_NAMES = ("batch_total", "synthesis", "writer")
# Maps cost_log layer name -> config.json model key.
LAYER_TO_MODEL_KEY = {
    "batch_total": "batch",
    "synthesis": "synthesis",
    "writer": "writer",
}


# Used by the summary-fallback path.
ESTIMATED_COST_RE = re.compile(r"Estimated cost:\s*\$([0-9]+\.[0-9]+)")
DIGESTS_DIR = PROJECT_ROOT / "data" / "digests"


def cost_from_summary(run_id):
    """
    Read 'Estimated cost: $X.XXXX' from the run's summary.txt.
    Returns float on success, None if file missing or pattern not found.
    """
    summary_path = DIGESTS_DIR / f"{run_id}-summary.txt"
    if not summary_path.exists():
        return None
    try:
        text = summary_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = ESTIMATED_COST_RE.search(text)
    if not match:
        return None
    try:
        return float(match.group(1))
    except ValueError:
        return None

def load_pricing():
    if not PRICING_PATH.exists():
        return {}
    with open(PRICING_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_models():
    if not CONFIG_PATH.exists():
        return {}
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f).get("models", {})


def _compute_layer_cost(tin, tout, model_name, pricing):
    p = pricing.get(model_name)
    if not p:
        return 0.0
    return (tin / 1_000_000) * p["input_per_1m"] + (tout / 1_000_000) * p["output_per_1m"]


def entry_total_cost(entry, pricing, models):
    """
    Total cost for a cost_log entry, in priority order:
      1. Sum of stored per-layer 'cost' fields (locked-in at write time).
      2. Estimated cost parsed from the matching summary.txt.
      3. Live computation from token counts × current pricing.
    """
    layers = entry.get("layers", {})

    # Path 1: locked-in.
    if all("cost" in layers.get(name, {}) for name in LAYER_NAMES):
        return sum(layers[name]["cost"] for name in LAYER_NAMES)

    # Path 2: parse from summary file (preserves actual historical cost).
    run_id = entry.get("run_id", "")
    if run_id:
        summary_cost = cost_from_summary(run_id)
        if summary_cost is not None:
            return summary_cost

    # Path 3: live recompute (last resort).
    total = 0.0
    for layer_name in LAYER_NAMES:
        layer = layers.get(layer_name, {})
        model_key = LAYER_TO_MODEL_KEY[layer_name]
        model_name = models.get(model_key, "")
        total += _compute_layer_cost(
            layer.get("in", 0), layer.get("out", 0), model_name, pricing
        )
    return total


def entry_timestamp(entry):
    """Parse the 'timestamp' field (ISO UTC like 2026-04-27T21:37:30Z) into a datetime."""
    ts = entry.get("timestamp", "")
    if not ts:
        return None
    try:
        # Replace Z with +00:00 for fromisoformat compatibility.
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None