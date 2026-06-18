from __future__ import annotations

import logging
from pathlib import Path

from boundary_analyzer.llm.client import _call_ollama, call_llm
from boundary_analyzer.llm.context import (
    build_project_context,
    format_context_for_prompt,
)
from boundary_analyzer.llm.prompts import build_instrumentation_prompt

logger = logging.getLogger(__name__)


def _validate_python(code: str, label: str = "generated_instrumentation.py") -> bool:
    """Return True if code is valid Python syntax."""
    try:
        compile(code, label, "exec")
        return True
    except SyntaxError:
        return False


def generate_instrumentation(
    project_path: Path,
    jaeger_host: str = "localhost",
    jaeger_port: int = 4317,
) -> str | None:
    """Generate OTel instrumentation for a microservice project using the LLM.

    Tries OpenRouter first (if API key is configured), then falls back to a
    local Ollama instance. Returns None only if both options fail or produce
    invalid code.

    Args:
        project_path: Path to the microservice project root.
        jaeger_host: Hostname of the Jaeger/OTel collector (default: localhost).
        jaeger_port: gRPC port of the Jaeger/OTel collector (default: 4317).

    Returns:
        The modified main.py content as a string, or None if all LLM options
        fail or produce invalid Python.
    """
    context = build_project_context(project_path)
    context_text = format_context_for_prompt(context)

    prompt = build_instrumentation_prompt(
        context_text,
        jaeger_host=jaeger_host,
        jaeger_port=jaeger_port,
        context=context,
    )

    # ── Attempt 1: OpenRouter (via call_llm, which also tries Ollama as fallback) ──
    result = call_llm(prompt, temperature=0.1, max_tokens=4000)

    if result is not None and not result.startswith("ERROR:") and _validate_python(result):
        return result

    # Log why the first attempt failed
    if result is None:
        logger.warning("OpenRouter/Ollama call returned None for %s", project_path)
    elif result.startswith("ERROR:"):
        logger.warning("LLM refused to instrument %s: %s", project_path, result)
    else:
        logger.warning("OpenRouter/Ollama generated invalid Python for %s", project_path)

    # ── Attempt 2: Local Ollama directly (catches case where OpenRouter
    #    had a key and tried its models, but never reached Ollama) ──
    logger.info("Retrying with local Ollama for %s...", project_path)
    ollama_result = _call_ollama(prompt, temperature=0.1, max_tokens=4000)

    if ollama_result is not None and not ollama_result.startswith("ERROR:") and _validate_python(ollama_result):
        logger.info("Ollama succeeded for %s", project_path)
        return ollama_result

    if ollama_result is None:
        logger.warning("Ollama also returned None for %s", project_path)
    elif ollama_result.startswith("ERROR:"):
        logger.warning("Ollama also refused to instrument %s: %s", project_path, ollama_result)
    else:
        logger.warning("Ollama also generated invalid Python for %s", project_path)

    return None
