from __future__ import annotations

from pathlib import Path

from boundary_analyzer.llm.client import call_llm
from boundary_analyzer.llm.context import (
    build_project_context,
    format_context_for_prompt,
)
from boundary_analyzer.llm.prompts import build_instrumentation_prompt


def generate_instrumentation(
    project_path: Path,
    jaeger_host: str = "localhost",
    jaeger_port: int = 4317,
) -> str | None:
    """Generate OTel instrumentation for a microservice project using the LLM.

    Args:
        project_path: Path to the microservice project root.
        jaeger_host: Hostname of the Jaeger/OTel collector (default: localhost).
        jaeger_port: gRPC port of the Jaeger/OTel collector (default: 4317).

    Returns:
        The modified main.py content as a string, or None if the LLM call fails,
        the project cannot be analysed, or the generated code has syntax errors.
    """
    context = build_project_context(project_path)
    context_text = format_context_for_prompt(context)

    prompt = build_instrumentation_prompt(
        context_text,
        jaeger_host=jaeger_host,
        jaeger_port=jaeger_port,
    )

    result = call_llm(prompt, temperature=0.1, max_tokens=4000)

    if result is None:
        return None

    if result.startswith("ERROR:"):
        return None

    # Validate that the LLM output is valid Python syntax before returning
    try:
        compile(result, "generated_instrumentation.py", "exec")
    except SyntaxError as e:
        return None

    return result
