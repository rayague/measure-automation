from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from boundary_analyzer._utils import save_csv

"""Build endpoint-to-DB-table mappings by walking parent span chains."""

logger = logging.getLogger(__name__)


def _build_span_lookup(spans_df: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    """Build lookup: (trace_id, span_id) -> span info."""
    lookup = {}
    for _, row in spans_df.iterrows():
        key = (row["trace_id"], row["span_id"])
        lookup[key] = {
            "parent_span_id": row.get("parent_span_id"),
            "service_name": row.get("service_name"),
        }
    return lookup


def _build_endpoint_lookup(endpoints_df: pd.DataFrame) -> dict[tuple[str, str], str]:
    """Build lookup: (trace_id, span_id) -> endpoint_key."""
    lookup = {}
    for _, row in endpoints_df.iterrows():
        key = (row["trace_id"], row["span_id"])
        lookup[key] = row["endpoint_key"]
    return lookup


def _find_endpoint_for_db_span(
    trace_id: str,
    span_id: str,
    span_lookup: dict,
    endpoint_lookup: dict,
    visited: set | None = None,
) -> tuple[str | None, str | None]:
    """Find the endpoint for a DB span by walking up parent chain.

    Returns: (endpoint_key, service_name) or (None, None) if not found.
    """
    if visited is None:
        visited = set()

    # Prevent infinite loops
    if (trace_id, span_id) in visited:
        return None, None
    visited.add((trace_id, span_id))

    # Check if this span is an endpoint
    if (trace_id, span_id) in endpoint_lookup:
        return endpoint_lookup[(trace_id, span_id)], span_lookup.get((trace_id, span_id), {}).get("service_name")

    # Get parent and continue walking up
    span_info = span_lookup.get((trace_id, span_id))
    if not span_info:
        return None, None

    parent_id = span_info.get("parent_span_id")
    if not parent_id or pd.isna(parent_id):
        return None, None

    return _find_endpoint_for_db_span(trace_id, parent_id, span_lookup, endpoint_lookup, visited)


def build_endpoint_table_mapping(
    spans_df: pd.DataFrame,
    endpoints_df: pd.DataFrame,
    db_ops_df: pd.DataFrame,
) -> pd.DataFrame:
    """Map each DB operation to its parent HTTP endpoint via parent-span chain walking."""
    if db_ops_df.empty or endpoints_df.empty:
        return pd.DataFrame(columns=["service_name", "endpoint_key", "table", "count"])

    # Build lookups
    span_lookup = _build_span_lookup(spans_df)
    endpoint_lookup = _build_endpoint_lookup(endpoints_df)

    # Collect mappings
    mappings = []

    for _, db_row in db_ops_df.iterrows():
        trace_id = db_row["trace_id"]
        span_id = db_row["span_id"]
        tables_str = db_row.get("tables", "")

        # Handle NaN values (can be float when read from CSV)
        if pd.isna(tables_str) or not tables_str:
            continue

        # Convert to string if needed
        tables_str = str(tables_str)

        # Find endpoint for this DB span
        endpoint_key, service_name = _find_endpoint_for_db_span(trace_id, span_id, span_lookup, endpoint_lookup)

        if not endpoint_key:
            # Fallback: try the closest endpoint by start_time in the same trace
            trace_endpoints = endpoints_df[endpoints_df["trace_id"] == trace_id]
            if not trace_endpoints.empty and "start_time" in trace_endpoints.columns and "start_time" in db_row.index:
                db_start = db_row["start_time"]
                if pd.notna(db_start):
                    trace_endpoints = trace_endpoints.copy()
                    trace_endpoints["_time_dist"] = (trace_endpoints["start_time"] - db_start).abs()
                    best = trace_endpoints.loc[trace_endpoints["_time_dist"].idxmin()]
                    endpoint_key = best["endpoint_key"]
                    service_name = best.get("service_name", db_row.get("service_name", "unknown"))
                else:
                    endpoint_key = "unknown_endpoint"
                    service_name = db_row.get("service_name", "unknown")
            else:
                endpoint_key = "unknown_endpoint"
                service_name = db_row.get("service_name", "unknown")

        # Split tables and create one row per table
        tables = [t.strip() for t in tables_str.split(",") if t.strip()]
        for table in tables:
            mappings.append(
                {
                    "service_name": service_name,
                    "endpoint_key": endpoint_key,
                    "table": table,
                    "count": 1,
                }
            )

    if not mappings:
        logger.warning(
            "No endpoint-to-table mappings could be built from %d DB operations. "
            "The parent-span chain walking may have failed. Check that HTTP endpoint "
            "spans exist and that DB spans have valid parent span IDs.",
            len(db_ops_df),
        )
        return pd.DataFrame(columns=["service_name", "endpoint_key", "table", "count"])

    unknown_count = sum(1 for m in mappings if m["endpoint_key"] == "unknown_endpoint")
    total_mappings = len(mappings)
    if unknown_count > total_mappings * 0.5:
        logger.warning(
            "%d/%d mappings are 'unknown_endpoint' (>50%%). "
            "Parent-span chain walking failed to link DB spans to HTTP endpoints. "
            "This is often caused by missing parent_span_id values or span ID format "
            "mismatches between traces.",
            unknown_count, total_mappings,
        )

    # Aggregate counts
    df = pd.DataFrame(mappings)
    result = df.groupby(["service_name", "endpoint_key", "table"]).agg({"count": "sum"}).reset_index()

    return result


def save_endpoint_table_map_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save endpoint-to-table mapping to CSV."""
    save_csv(df, output_path)
