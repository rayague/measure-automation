from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from boundary_analyzer.detection.endpoint_normalizer import (
    build_endpoint_key,
    extract_tags_from_span,
)

HEALTH_KEYWORDS: frozenset[str] = frozenset({
    "health", "healthz", "readyz", "livez",
    "metrics", "favicon.ico",
})


def _is_endpoint_span(
    row: pd.Series,
    exclude_health_routes: bool = True,
    exclude_http_client_spans: bool = True,
) -> bool:
    """Check if a span is an HTTP endpoint span.

    Heuristic:
    - Has http.method tag, OR
    - Has http.route or http.target tag, OR
    - operation_name starts with HTTP method

    Args:
        exclude_health_routes: If True, filters out infrastructure endpoints
            (/health, /metrics, /favicon.ico, etc.)
        exclude_http_client_spans: If True, filters out HTTP client spans
            (http send, http receive, etc.)
    """
    operation = str(row.get("operation_name", ""))

    # Exclude HTTP client spans (e.g. "GET /students/ http send")
    if exclude_http_client_spans and (
        " http send" in operation.lower()
        or " http receive" in operation.lower()
        or " http request" in operation.lower()
        or " http response" in operation.lower()
    ):
        return False

    # Check tags if available
    tags_str = row.get("tags", "")
    tags: list[dict[str, Any]] = []
    if tags_str:
        try:
            tags = json.loads(tags_str)
        except (json.JSONDecodeError, TypeError):
            pass

    has_http_tag = False
    for tag in tags:
        key = tag.get("key", "")
        if key in ["http.method", "http.route", "http.target"]:
            has_http_tag = True
            break

    if not has_http_tag:
        # Fallback: operation_name heuristic
        operation_upper = operation.upper()
        http_methods = ["GET ", "POST ", "PUT ", "DELETE ", "PATCH ", "HEAD ", "OPTIONS "]
        for method in http_methods:
            if operation_upper.startswith(method):
                has_http_tag = True
                break

    if not has_http_tag:
        return False

    # Exclude health/infrastructure routes (segment-based matching)
    if exclude_health_routes:
        route = ""
        for tag in tags:
            k = tag.get("key", "")
            if k in ("http.route", "http.target"):
                route = str(tag.get("value", ""))
                break
        if not route and " " in operation:
            route = operation.split(" ", 1)[1]
        if any(part in HEALTH_KEYWORDS for part in route.strip("/").split("/") if part):
            return False

    return True


def extract_endpoints(
    spans_df: pd.DataFrame,
    normalize: bool = True,
    exclude_health_routes: bool = True,
    exclude_http_client_spans: bool = True,
) -> pd.DataFrame:
    """Extract endpoint spans from spans DataFrame with normalization.

    Input columns: trace_id, span_id, parent_span_id, service_name,
                   operation_name, start_time, duration, tags
    Output columns: service_name, endpoint_key, span_id, trace_id

    Args:
        normalize: Whether to normalize dynamic parameters in routes
        exclude_health_routes: Filter out /health, /metrics, etc.
        exclude_http_client_spans: Filter out HTTP client spans
    """
    if spans_df.empty:
        return pd.DataFrame(columns=["service_name", "endpoint_key", "span_id", "trace_id"])

    # Find endpoint spans
    is_endpoint = spans_df.apply(
        lambda row: _is_endpoint_span(
            row,
            exclude_health_routes=exclude_health_routes,
            exclude_http_client_spans=exclude_http_client_spans,
        ),
        axis=1,
    )
    endpoint_spans = spans_df[is_endpoint].copy()

    if endpoint_spans.empty:
        return pd.DataFrame(columns=["service_name", "endpoint_key", "span_id", "trace_id"])

    # Build endpoint key using normalizer with tags
    def build_key_from_row(row: pd.Series) -> str:
        operation_name = str(row.get("operation_name", ""))
        tags_str = row.get("tags", "")
        tags = []
        if tags_str:
            try:
                tags = json.loads(tags_str)
            except (json.JSONDecodeError, TypeError):
                tags = []
        return build_endpoint_key(operation_name, tags, normalize=normalize)

    endpoint_spans["endpoint_key"] = endpoint_spans.apply(build_key_from_row, axis=1)

    # Select and rename columns
    result = endpoint_spans[["service_name", "endpoint_key", "span_id", "trace_id"]].copy()

    return result


def save_endpoints_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save endpoints DataFrame to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
