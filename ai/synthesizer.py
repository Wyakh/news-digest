"""
synthesizer.py
--------------
Layer 3: cross-batch synthesis.

Input:  batch digests + recent past digests for continuity
Output: {"stories": [...]} ready for URL reattachment and writing
Returns: (synthesis_dict, tokens_in, tokens_out, error)
"""

import json
import os
from provider import call_llm, get_llm_settings


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "config.json")
PROMPT_PATH = os.path.join(ROOT, "config", "prompt_synthesis.txt")
LAYER_NAME = "synthesis"

SYSTEM_PROMPT = (
    "You are a senior news analyst synthesizing across source ecosystems. "
    "Always respond with valid JSON only."
)

HEADLINE_ID_SCHEMA = {
    "anyOf": [
        {"type": "integer"},
        {"type": "string"},
    ]
}

OPTIONAL_STRING_SCHEMA = {
    "anyOf": [
        {"type": "string"},
        {"type": "null"},
    ]
}

SYNTHESIS_SCHEMA = {
    "type": "object",
    "properties": {
        "stories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "tier": {"type": "string", "enum": ["primary", "secondary", "signal"]},
                    "canonical_title": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "ecosystems_present": {"type": "array", "items": {"type": "string"}},
                    "headline_ids": {"type": "array", "items": HEADLINE_ID_SCHEMA},
                    "is_developing": {"type": "boolean"},
                    "previous_reference_run": OPTIONAL_STRING_SCHEMA,
                    "previous_reference_title": OPTIONAL_STRING_SCHEMA,
                    "no_new_info": {"type": "boolean"},
                    "is_continuation_significant": {"type": "boolean"},
                    "was_signal_in_past": {"type": "boolean"},
                },
                "required": [
                    "tier",
                    "canonical_title",
                    "tags",
                    "ecosystems_present",
                    "headline_ids",
                    "is_developing",
                    "previous_reference_run",
                    "previous_reference_title",
                    "no_new_info",
                    "is_continuation_significant",
                    "was_signal_in_past",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["stories"],
    "additionalProperties": False,
}


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt():
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


def build_synthesis_prompt(prompt_instructions, batch_digests, past_digests=None, past_ripped_md=None):
    batches_text = ""
    for batch in batch_digests:
        name = batch.get("batch_name", "unknown")
        clusters = batch.get("clusters", [])
        batches_text += f"\n=== BATCH: {name} ({len(clusters)} clusters) ===\n"
        for i, c in enumerate(clusters, 1):
            ids_str = ", ".join(str(x) for x in c.get("headline_ids", []))
            batches_text += (
                f"{i}. {c.get('canonical_title', '')}\n"
                f"   tags: {', '.join(c.get('tags', []))}\n"
                f"   headline_ids: {ids_str}\n"
            )

    past_struct_text = ""
    if past_digests:
        past_struct_text = "\n=== PAST DIGESTS — STRUCTURED (for continuity matching) ===\n"
        past_struct_text += (
            "Each past story shows run_id, canonical_title, tags, tier, and headline_ids. "
            "Match today's clusters by H-ID overlap and thematic similarity.\n"
        )
        for d in past_digests:
            run_id = d.get("_run_id") or d.get("run_id", "unknown")
            past_struct_text += f"\n--- Past run: {run_id} ---\n"
            for s in d.get("stories", []):
                title = s.get("canonical_title", "")
                tier = s.get("tier", "")
                tags = ", ".join(s.get("tags", []))
                hids = []
                if s.get("headline_ids"):
                    hids = [str(x) for x in s["headline_ids"]]
                elif s.get("sources"):
                    for src in s["sources"]:
                        sid = src.get("id", "")
                        if sid:
                            hids.append(sid.replace("H", ""))
                past_struct_text += (
                    f"  ({tier}) {title}\n"
                    f"    tags: {tags}\n"
                    f"    headline_ids: {', '.join(hids)}\n"
                )

    past_md_text = ""
    if past_ripped_md:
        past_md_text = "\n=== PAST DIGESTS — DESCRIPTIONS (for thematic matching) ===\n"
        for entry in past_ripped_md:
            run_id = entry.get("run_id", "unknown")
            ripped = entry.get("ripped", "")
            past_md_text += f"\n--- Past run: {run_id} ---\n{ripped}\n"

    return f"{prompt_instructions}\n{past_struct_text}{past_md_text}\n=== TODAY'S CLUSTERS ==={batches_text}"


def call_synthesis_llm(prompt, model, config):
    temperature, max_tokens, reasoning_effort = get_llm_settings(
        config,
        LAYER_NAME
    )

    return call_llm(
        prompt,
        model,
        config,
        SYSTEM_PROMPT,
        temperature=temperature,
        max_tokens=max_tokens,
        reasoning_effort=reasoning_effort,
        output_schema=SYNTHESIS_SCHEMA,
        schema_name="synthesis",
    )


def parse_synthesis(raw):
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"

    if "stories" not in data:
        return None, "Response missing 'stories' key"
    return data, ""


def synthesize(batch_digests, past_digests=None, past_ripped_md=None):
    """Called by ai_engine.py after all batches complete."""
    if not batch_digests:
        return None, 0, 0, "No batch digests provided"

    config = load_config()
    model = config.get("models", {}).get(LAYER_NAME)
    if not model:
        raise ValueError(f"Missing models.{LAYER_NAME} in config.json")

    total_clusters = sum(len(b.get("clusters", [])) for b in batch_digests)
    print(f"\n  [Synthesis] {total_clusters} clusters across {len(batch_digests)} batches → {model}")

    try:
        prompt_instructions = load_prompt()
    except Exception as e:
        return None, 0, 0, f"Could not load synthesis prompt: {e}"

    prompt = build_synthesis_prompt(prompt_instructions, batch_digests, past_digests, past_ripped_md)

    try:
        raw, tokens_in, tokens_out = call_synthesis_llm(prompt, model, config)
    except Exception as e:
        return None, 0, 0, f"LLM call failed: {e}"

    digest, error = parse_synthesis(raw)
    if error:
        return None, tokens_in, tokens_out, error

    story_count = len(digest.get("stories", []))
    primary = sum(1 for s in digest["stories"] if s.get("tier") == "primary")
    secondary = sum(1 for s in digest["stories"] if s.get("tier") == "secondary")
    signal = sum(1 for s in digest["stories"] if s.get("tier") == "signal")
    print(f"  [Synthesis] → {story_count} stories "
          f"(primary: {primary}, secondary: {secondary}, signal: {signal}) | "
          f"{tokens_in} in / {tokens_out} out tokens")

    return digest, tokens_in, tokens_out, ""


if __name__ == "__main__":
    fake_batches = [
        {
            "batch_name": "western",
            "clusters": [
                {"canonical_title": "Iran talks resume", "tags": ["Iran"], "headline_ids": [1, 2]},
            ],
        }
    ]
    result, tin, tout, err = synthesize(fake_batches)
    print(f"ERROR: {err}" if err else json.dumps(result, indent=2, ensure_ascii=False))
