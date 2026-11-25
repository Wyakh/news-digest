"""
writer.py
---------
Layer 4: writes the human-readable Markdown digest from Layer 3 output.

The writer receives source IDs but not URLs. It writes [Hxx] placeholders;
url_resolver.py replaces those placeholders after the LLM call.

Returns: (markdown_with_placeholders, structured_synthesis, tokens_in, tokens_out, error)
"""

import json
import os
from datetime import datetime, timezone
from provider import call_llm, get_llm_settings


ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CONFIG_PATH = os.path.join(ROOT, "config", "config.json")
PROMPT_PATH = os.path.join(ROOT, "config", "prompt_writer.txt")
LAYER_NAME = "writer"

SYSTEM_PROMPT = (
    "You are a professional news editor writing a structured digest in Markdown. "
    "Always respond with valid JSON containing the markdown."
)

WRITER_SCHEMA = {
    "type": "object",
    "properties": {
        "markdown": {"type": "string"},
    },
    "required": ["markdown"],
    "additionalProperties": False,
}


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_prompt():
    with open(PROMPT_PATH, "r", encoding="utf-8") as f:
        return f.read().strip()


def remove_source_urls(synthesis):
    """Return a copy of synthesis where sources keep id, media, and title only."""
    stripped = {"stories": []}
    for story in synthesis.get("stories", []):
        new_story = dict(story)
        new_story["sources"] = [
            {
                "id": source.get("id", ""),
                "media": source.get("media", ""),
                "title": source.get("title", ""),
            }
            for source in story.get("sources", [])
        ]
        stripped["stories"].append(new_story)
    return stripped


def build_writer_prompt(prompt_instructions, synthesis, past_briefs=None):
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    prompt_instructions = prompt_instructions.replace("{{TIMESTAMP}}", now)

    if past_briefs:
        run_ids = ", ".join(b["run_id"] for b in past_briefs)
        past_runs_line = f"*Reviewed past digests: {run_ids}*"
    else:
        past_runs_line = ""
    prompt_instructions = prompt_instructions.replace("{{PAST_RUNS}}", past_runs_line)

    past_text = ""
    if past_briefs:
        past_text = (
            "\n=== PAST DIGEST SKELETONS "
            "(for tone continuity — do NOT repeat phrasings, do NOT reference yesterday explicitly) ===\n"
        )
        for brief in past_briefs:
            past_text += f"\n[{brief['run_id']}]\n"
            past_text += f"Overview: {brief.get('overview', '')}\n"
            primary = brief.get("primary_titles_and_descriptions", "")
            if primary:
                past_text += f"Primary stories:\n{primary}\n"

    stories_text = json.dumps(synthesis, ensure_ascii=False, separators=(",", ":"))
    return f"{prompt_instructions}\n{past_text}\n=== STRUCTURED STORIES ===\n{stories_text}"


def call_writer_llm(prompt, model, config):
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
        output_schema=WRITER_SCHEMA,
        schema_name="digest_markdown",
    )


def parse_writer_response(raw):
    try:
        data = json.loads(raw.strip())
    except json.JSONDecodeError as e:
        return None, f"JSON parse error: {e}"

    if "markdown" not in data:
        return None, "Response missing 'markdown' key"
    return data, ""


def write_digest(synthesis, past_briefs=None):
    if not synthesis or not synthesis.get("stories"):
        return None, None, 0, 0, "No stories to write"

    config = load_config()
    model = config.get("models", {}).get(LAYER_NAME)
    if not model:
        raise ValueError(f"Missing models.{LAYER_NAME} in config.json")

    story_count = len(synthesis.get("stories", []))
    print(f"\n  [Writer] {story_count} stories → {model}")

    try:
        prompt_instructions = load_prompt()
    except Exception as e:
        return None, None, 0, 0, f"Could not load writer prompt: {e}"

    stripped = remove_source_urls(synthesis)
    prompt = build_writer_prompt(prompt_instructions, stripped, past_briefs)

    try:
        raw, tokens_in, tokens_out = call_writer_llm(prompt, model, config)
    except Exception as e:
        return None, None, 0, 0, f"LLM call failed: {e}"

    parsed, error = parse_writer_response(raw)
    if error:
        return None, None, tokens_in, tokens_out, error

    markdown = parsed.get("markdown", "")
    print(f"  [Writer] → {len(markdown)} chars markdown (with placeholders) | "
          f"{tokens_in} in / {tokens_out} out tokens")

    structured = dict(synthesis)
    structured["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    return markdown, structured, tokens_in, tokens_out, ""
