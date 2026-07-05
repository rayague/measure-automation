from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from boundary_analyzer._utils import save_csv

"""Read Jaeger JSON trace exports into a unified pandas DataFrame."""

logger = logging.getLogger(__name__)


def _find_parent_span_id(span: dict[str, Any]) -> str | None:
    """Get parent span ID from references."""
    refs = span.get("references", [])
    for ref in refs:
        if ref.get("refType") == "CHILD_OF":
            return ref.get("spanID")

    # Fallback for Jaeger JSON that uses an explicit parentSpanID field
    parent_span_id = span.get("parentSpanID")
    if parent_span_id:
        return str(parent_span_id)

    return None


def _get_service_name(span: dict[str, Any], processes: dict[str, Any] | None = None) -> str:
    """Get service name from span process.

    Handles both formats:
    - Old: span has process directly: span["process"]["serviceName"]
    - New: trace has processes dict, span has processID referencing it
    """
    # Try new format first (processID referencing trace-level processes)
    process_id = span.get("processID")
    if process_id and processes and process_id in processes:
        return processes[process_id].get("serviceName", "")

    # Fallback to old format (process embedded in span)
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
    try:
        with file_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
        logger.warning("Skipping malformed trace file %s: %s", file_path.name, e)
        return []

    if isinstance(data, list):
        data = {"data": data}
    jaeger_response = data.get("jaeger_response", data)
    traces = jaeger_response.get("data", [])

    rows = []
    for trace in traces:
        trace_id = trace.get("traceID", "")
        spans = trace.get("spans", [])
        # New Jaeger format: processes are at trace level, spans reference them via processID
        processes = trace.get("processes", {})

        for span in spans:
            row = {
                "trace_id": trace_id,
                "span_id": span.get("spanID", ""),
                "parent_span_id": _find_parent_span_id(span),
                "service_name": _get_service_name(span, processes),
                "operation_name": span.get("operationName", ""),
                "start_time": span.get("startTime", 0),
                "duration": span.get("duration", 0),
                "tags": _extract_tags_as_json(span),
            }
            rows.append(row)

    return rows


def read_all_traces(traces_dir: Path) -> pd.DataFrame:
    """Read all Jaeger JSON trace files from a directory into a single DataFrame."""
    all_rows: list[dict[str, Any]] = []

    json_files = list(traces_dir.glob("*.json"))

    for file_path in json_files:
        try:
            rows = _read_one_trace_file(file_path)
            all_rows.extend(rows)
        except Exception as e:
            logger.warning("Failed to read trace file %s: %s", file_path.name, e)

    df = pd.DataFrame(all_rows)

    if df.empty:
        df = pd.DataFrame(
            columns=[
                "trace_id",
                "span_id",
                "parent_span_id",
                "service_name",
                "operation_name",
                "start_time",
                "duration",
                "tags",
            ]
        )

    return df


def save_spans_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save parsed spans DataFrame to CSV."""
    save_csv(df, output_path)
