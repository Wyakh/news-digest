"""
batch_processor.py
------------------
Layer 2: per-source-ecosystem clustering and filtering.

Input:  headline dicts + one batch prompt file
Output: {"clusters": [...]} with headline IDs only; URLs are reattached later.
Returns: (batch_digest, tokens_in, tokens_out, error)
"""

import json
import os

from provider import call_llm, get_llm_settings


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "config.json")
LAYER_NAME = "batch"

SYSTEM_PROMPT = "You are a professional news analyst. Always respond with valid JSON only."

BATCH_SCHEMA = {
    "type": "object",
    "properties": {
        "clusters": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "canonical_title": {"type": "string"},
                    "tags": {"type": "array", "items": {"type": "string"}},
                    "headline_ids": {
                        "type": "array",
                        "items": {
                            "anyOf": [
                                {"type": "integer"},
                                {"type": "string"},
                            ]
                        },
                    },
                },
                "required": ["canonical_title", "tags", "headline_ids"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["clusters"],
    "additionalProperties": False,
}


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt(prompt_filename):
    path = os.path.join(ROOT, "config", prompt_filename)
    with open(path, "r", encoding="utf-8") as f:
        return f.read().strip()


def build_batch_prompt(prompt_instructions, headlines):
    """Headlines are passed with IDs only; the model references headline IDs."""
    lines = []
    for h in headlines:
        hid = h.get("id", "")
        media = h.get("media", "")
        title = h.get("title", "")
        section = h.get("section", "")
        homepage = " [homepage]" if h.get("homepage") is True else ""
        lines.append(f"{hid} | [{media}] [{section}]{homepage} {title}")

    return f"{prompt_instructions}\n\nHEADLINES:\n" + "\n".join(lines)


def call_batch_llm(prompt, model, config):
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
        output_schema=BATCH_SCHEMA,
        schema_name="batch_digest",
    )

def parse_batch_digest(raw):
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"

    if "clusters" not in data:
        return None, "Response missing 'clusters' key"
    return data, ""


def process_batch(batch_name, headlines, prompt_filename):
    """
    Called by ai_engine.py for each batch.
    Returns: (batch_digest_dict, tokens_in, tokens_out, error_str)
    """
    if not headlines:
        return {"batch_name": batch_name, "clusters": []}, 0, 0, "No headlines in batch"

    config = load_config()
    model = config.get("models", {}).get(LAYER_NAME)
    if not model:
        raise ValueError(f"Missing models.{LAYER_NAME} in config.json")

    print(f"  [Batch: {batch_name}] {len(headlines)} headlines → {model}")

    try:
        prompt_instructions = load_prompt(prompt_filename)
    except Exception as e:
        return None, 0, 0, f"Could not load prompt {prompt_filename}: {e}"

    prompt = build_batch_prompt(prompt_instructions, headlines)

    try:
        raw, tokens_in, tokens_out = call_batch_llm(prompt, model, config)
    except Exception as e:
        return None, 0, 0, f"LLM call failed: {e}"

    digest, error = parse_batch_digest(raw)
    if error:
        return None, tokens_in, tokens_out, error

    digest["batch_name"] = batch_name
    cluster_count = len(digest.get("clusters", []))
    print(f"  [Batch: {batch_name}] → {cluster_count} clusters | "
          f"{tokens_in} in / {tokens_out} out tokens")

    return digest, tokens_in, tokens_out, ""


if __name__ == "__main__":
    test_headlines = [
        {"id": 1, "media": "Reuters", "title": "Iran nuclear talks resume", "section": "WORLD"},
        {"id": 2, "media": "Financial Times", "title": "US-Iran negotiations restart", "section": "WORLD"},
        {"id": 3, "media": "Straits Times", "title": "Iran says open to talks", "section": "WORLD"},
    ]
    digest, tin, tout, err = process_batch("western_test", test_headlines, "prompt_batch_western.txt")
    print(f"ERROR: {err}" if err else json.dumps(digest, indent=2, ensure_ascii=False))
