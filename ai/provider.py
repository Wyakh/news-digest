"""
provider.py
-----------
Provider-neutral LLM call router for the digest pipeline.

Provider is detected from the model name prefix:
  "claude-" -> Anthropic API
  "gpt-"    -> OpenAI API
  "o"       -> OpenAI API

Returns: (raw_text, tokens_in, tokens_out)
"""


def get_llm_settings(config, layer):
    settings = config.get("llm_settings", {}).get(layer)
    if settings is None:
        raise ValueError(f"Missing llm_settings.{layer} in config.json")

    if "max_tokens" not in settings:
        raise ValueError(f"Missing llm_settings.{layer}.max_tokens in config.json")

    temperature = settings.get("temperature")
    max_tokens = int(settings["max_tokens"])
    reasoning_effort = settings.get("reasoning_effort")

    return temperature, max_tokens, reasoning_effort


def call_llm(
    prompt,
    model,
    config,
    system_prompt,
    temperature,
    max_tokens,
    reasoning_effort=None,
    output_schema=None,
    schema_name="response",
    ):
    """
    Route to the correct provider and return (raw_text, tokens_in, tokens_out).
    """
    if model.startswith("claude-"):
        return _call_anthropic(
            prompt,
            model,
            config,
            system_prompt,
            temperature,
            max_tokens,
            output_schema=output_schema,
        )

    if model.startswith("gpt-") or model.startswith("o"):
        return _call_openai(
            prompt,
            model,
            config,
            system_prompt,
            temperature,
            max_tokens,
            reasoning_effort=reasoning_effort,
            output_schema=output_schema,
            schema_name=schema_name,
        )

    raise ValueError(
        f"Cannot determine provider for model '{model}'. "
        f"Expected prefix 'claude-', 'gpt-', or 'o'."
    )


def _close_client(client):
    """Best-effort cleanup must not change a result or mask a request error."""
    try:
        client.close()
    except Exception:
        pass


def _call_anthropic(
    prompt,
    model,
    config,
    system_prompt,
    temperature,
    max_tokens,
    output_schema=None,
):
    import anthropic

    api_key = config.get("anthropic_api_key", "")
    if not api_key or api_key in ("YOUR_ANTHROPIC_KEY_HERE", "PASTE_YOUR_KEY_HERE"):
        raise ValueError("No anthropic_api_key set in config.json")

    request = {
        "model": model,
        "system": system_prompt,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }

    if output_schema:
        request["output_config"] = {
            "format": {
                "type": "json_schema",
                "schema": output_schema,
            }
        }

    client = anthropic.Anthropic(api_key=api_key)
    try:
        raw_parts = []

        with client.messages.stream(**request) as stream:
            for text in stream.text_stream:
                raw_parts.append(text)

            final_message = stream.get_final_message()

        raw = "".join(raw_parts).strip()
        tokens_in = final_message.usage.input_tokens
        tokens_out = final_message.usage.output_tokens

        print(
            f"    [Anthropic] stop_reason={final_message.stop_reason}, "
            f"max_tokens={max_tokens}, "
            f"{tokens_in} in / {tokens_out} out"
        )

        return raw, tokens_in, tokens_out
    finally:
        _close_client(client)


def _call_openai(
    prompt,
    model,
    config,
    system_prompt,
    temperature,
    max_tokens,
    reasoning_effort=None,
    output_schema=None,
    schema_name="response",
):
    from openai import OpenAI

    api_key = config.get("openai_api_key", "")
    if not api_key or api_key in ("YOUR_OPENAI_KEY_HERE", "PASTE_YOUR_KEY_HERE"):
        raise ValueError("No openai_api_key set in config.json")

    response_format = {"type": "json_object"}
    if output_schema:
        response_format = {
            "type": "json_schema",
            "json_schema": {
                "name": schema_name,
                "strict": True,
                "schema": output_schema,
            },
        }

    request = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": prompt},
        ],
        "max_completion_tokens": max_tokens,
        "response_format": response_format,
    }

    # GPT-5 / o-series reasoning models generally do not support custom temperature.
    # If temperature is default 1, omitting it is equivalent and safer.
    supports_temperature = not (
        model.startswith("gpt-5") or model.startswith("o")
    )

    if supports_temperature:
        request["temperature"] = temperature

    if reasoning_effort:
        request["reasoning_effort"] = reasoning_effort

    client = OpenAI(api_key=api_key)
    try:
        response = client.chat.completions.create(**request)

        raw = response.choices[0].message.content
        tokens_in = response.usage.prompt_tokens
        tokens_out = response.usage.completion_tokens
        return raw, tokens_in, tokens_out
    finally:
        _close_client(client)
