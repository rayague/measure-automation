from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def _find_parent_span_id(span: dict[str, Any]) -> str | None:
    """Get parent span ID from references."""
    refs = span.get("references", [])
    for ref in refs:
        if ref.get("refType") == "CHILD_OF":
            return ref.get("spanID")
    return None


def _get_service_name(span: dict[str, Any]) -> str:
    """Get service name from span process."""
    process = span.get("process", {})
    return process.get("serviceName", "")


def _extract_tags_as_json(span: dict[str, Any]) -> str:
    """Extract span tags as JSON string for storage.
    
    This preserves tag information for later use in endpoint normalization.
    """
    tags = span.get("tags", [])
    if not tags:
        # Try attributes (OTel format)
        attributes = span.get("attributes", {})
        if attributes:
            tags = [{"key": k, "value": v} for k, v in attributes.items()]
    
    if tags:
        return json.dumps(tags)
    return ""


def _read_one_trace_file(file_path: Path) -> list[dict[str, Any]]:
    """Read one Jaeger export file and return list of span rows."""
    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    jaeger_response = data.get("jaeger_response", data)
    traces = jaeger_response.get("data", [])

    rows = []
    for trace in traces:
        trace_id = trace.get("traceID", "")
        spans = trace.get("spans", [])

        for span in spans:
            row = {
                "trace_id": trace_id,
                "span_id": span.get("spanID", ""),
                "parent_span_id": _find_parent_span_id(span),
                "service_name": _get_service_name(span),
                "operation_name": span.get("operationName", ""),
                "start_time": span.get("startTime", 0),
                "duration": span.get("duration", 0),
                "tags": _extract_tags_as_json(span),
            }
            rows.append(row)

    return rows


def read_all_traces(traces_dir: Path) -> pd.DataFrame:
    """Read all JSON trace files and return DataFrame."""
    all_rows: list[dict[str, Any]] = []

    json_files = list(traces_dir.glob("*.json"))

    for file_path in json_files:
        rows = _read_one_trace_file(file_path)
        all_rows.extend(rows)

    df = pd.DataFrame(all_rows)

    if df.empty:
        df = pd.DataFrame(columns=[
            "trace_id", "span_id", "parent_span_id",
            "service_name", "operation_name", "start_time", "duration", "tags"
        ])

    return df


def save_spans_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save spans DataFrame to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
