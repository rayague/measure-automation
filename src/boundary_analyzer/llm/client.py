from __future__ import annotations

import json
import os
import time
from typing import Any

import requests


DEFAULT_MODEL = "qwen/qwen3-coder:free"
FALLBACK_MODEL = "openrouter/free"
DEFAULT_TIMEOUT = 60
ENV_API_KEY = "OPENROUTER_API_KEY"
API_URL = "https://openrouter.ai/api/v1/chat/completions"
MAX_RETRIES = 3


def call_llm(
    prompt: str,
    temperature: float = 0.1,
    max_tokens: int = 4000,
    model: str | None = None,
) -> str | None:
    """Call the LLM once with a single prompt.

    Args:
        prompt: The full prompt to send.
        temperature: 0.1 for code, 0.3-0.4 for analysis.
        max_tokens: Maximum tokens in response.
        model: Override the default model.

    Returns:
        The response text, or None if configuration is missing or call fails.
    """
    api_key = os.environ.get(ENV_API_KEY, "").strip()
    if not api_key:
        return None

    # Try primary model first, then fallback model
    models_to_try = [model or DEFAULT_MODEL, FALLBACK_MODEL]

    for resolved_model in models_to_try:
        for attempt in range(MAX_RETRIES):
            try:
                resp = requests.post(
                    API_URL,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                        "HTTP-Referer": "https://boundary-analyzer.local",
                    },
                    json={
                        "model": resolved_model,
                        "messages": [{"role": "user", "content": prompt}],
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                    },
                    timeout=DEFAULT_TIMEOUT,
                )

                if resp.status_code == 429:
                    try:
                        body = resp.json()
                        retry_after = (
                            body.get("error", {})
                            .get("metadata", {})
                            .get("retry_after_seconds", 10)
                        )
                        if isinstance(retry_after, str):
                            retry_after = int(retry_after)
                        wait = min(int(retry_after) + 2, 30)
                    except Exception:
                        wait = 10
                    if attempt < MAX_RETRIES - 1:
                        time.sleep(wait)
                        continue
                    # All retries exhausted for this model, try next one
                    break

                resp.raise_for_status()
                data: dict[str, Any] = resp.json()
                content = data.get("choices", [{}])[0].get("message", {}).get("content")
                if content and isinstance(content, str) and len(content.strip()) >= 1:
                    return content.strip()
                break

            except requests.Timeout:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(3)
                    continue
                break

            except requests.RequestException:
                if attempt < MAX_RETRIES - 1:
                    time.sleep(3)
                    continue
                break

    return None
