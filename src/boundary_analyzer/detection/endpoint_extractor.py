from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd

from boundary_analyzer.detection.endpoint_normalizer import (
    build_endpoint_key,
    extract_tags_from_span,
)


def _is_endpoint_span(row: pd.Series) -> bool:
    """Check if a span is an HTTP endpoint span.
    
    Heuristic:
    - Has http.method tag, OR
    - Has http.route or http.target tag, OR
    - operation_name starts with HTTP method
    """
    # Check tags if available
    tags_str = row.get("tags", "")
    if tags_str:
        try:
            tags = json.loads(tags_str)
            for tag in tags:
                key = tag.get("key", "")
                if key in ["http.method", "http.route", "http.target"]:
                    return True
        except (json.JSONDecodeError, TypeError):
            pass
    
    # Fallback: operation_name heuristic
    operation = str(row.get("operation_name", "")).upper()
    http_methods = ["GET ", "POST ", "PUT ", "DELETE ", "PATCH ", "HEAD ", "OPTIONS "]
    
    for method in http_methods:
        if operation.startswith(method):
            return True
    
    return False


def extract_endpoints(spans_df: pd.DataFrame, normalize: bool = True) -> pd.DataFrame:
    """Extract endpoint spans from spans DataFrame with normalization.
    
    Input columns: trace_id, span_id, parent_span_id, service_name, operation_name, start_time, duration, tags
    Output columns: service_name, endpoint_key, span_id, trace_id
    
    Args:
        normalize: Whether to normalize dynamic parameters in routes
    """
    if spans_df.empty:
        return pd.DataFrame(columns=["service_name", "endpoint_key", "span_id", "trace_id"])
    
    # Find endpoint spans
    is_endpoint = spans_df.apply(_is_endpoint_span, axis=1)
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
