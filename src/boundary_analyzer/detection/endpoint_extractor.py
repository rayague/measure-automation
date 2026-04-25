from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _extract_endpoint_key(operation_name: str) -> str:
    """Build endpoint key from operation name.
    
    Simple version: use operation_name directly.
    Later we can parse http.method and http.route from span tags.
    """
    return operation_name.strip()


def _is_endpoint_span(row: pd.Series) -> bool:
    """Check if a span is an HTTP endpoint span.
    
    Simple heuristic for MVP:
    - operation_name starts with HTTP method (GET, POST, PUT, DELETE, PATCH)
    """
    operation = str(row.get("operation_name", "")).upper()
    http_methods = ["GET ", "POST ", "PUT ", "DELETE ", "PATCH ", "HEAD ", "OPTIONS "]
    
    for method in http_methods:
        if operation.startswith(method):
            return True
    
    return False


def extract_endpoints(spans_df: pd.DataFrame) -> pd.DataFrame:
    """Extract endpoint spans from spans DataFrame.
    
    Input columns: trace_id, span_id, parent_span_id, service_name, operation_name, start_time, duration
    Output columns: service_name, endpoint_key, span_id, trace_id
    """
    if spans_df.empty:
        return pd.DataFrame(columns=["service_name", "endpoint_key", "span_id", "trace_id"])
    
    # Find endpoint spans
    is_endpoint = spans_df.apply(_is_endpoint_span, axis=1)
    endpoint_spans = spans_df[is_endpoint].copy()
    
    if endpoint_spans.empty:
        return pd.DataFrame(columns=["service_name", "endpoint_key", "span_id", "trace_id"])
    
    # Build endpoint key
    endpoint_spans["endpoint_key"] = endpoint_spans["operation_name"].apply(_extract_endpoint_key)
    
    # Select and rename columns
    result = endpoint_spans[["service_name", "endpoint_key", "span_id", "trace_id"]].copy()
    
    return result


def save_endpoints_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save endpoints DataFrame to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
