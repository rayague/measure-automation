from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import pandas as pd

from boundary_analyzer._utils import save_csv

"""Build endpoint-to-DB-table mappings by walking parent span chains."""

logger = logging.getLogger(__name__)


def _normalize_id(val: Any) -> str:
    """Normalize a trace/span ID to a plain string.

    Pandas often reads hex IDs from CSV as float (NaN for missing) or int.
    This ensures consistent dict-key matching across DataFrames.
    """
    if val is None or (isinstance(val, float) and pd.isna(val)):
        return ""
    if isinstance(val, float):
        # e.g. 1.23456e+15 → "1234560000000000"
        return str(int(val))
    if isinstance(val, int):
        return str(val)
    return str(val).strip()


def _build_span_lookup(spans_df: pd.DataFrame) -> dict[tuple[str, str], dict[str, Any]]:
    """Build lookup: (trace_id, span_id) -> span info."""
    lookup: dict[tuple[str, str], dict[str, Any]] = {}
    mismatches = 0
    for _, row in spans_df.iterrows():
        trace_id = _normalize_id(row.get("trace_id"))
        span_id = _normalize_id(row.get("span_id"))
        if not trace_id or not span_id:
            mismatches += 1
            continue
        key = (trace_id, span_id)
        lookup[key] = {
            "parent_span_id": _normalize_id(row.get("parent_span_id")),
            "service_name": row.get("service_name"),
        }
    if mismatches:
        logger.debug("Skipped %d span rows with empty trace_id or span_id", mismatches)
    return lookup


def _build_endpoint_lookup(endpoints_df: pd.DataFrame) -> dict[tuple[str, str], str]:
    """Build lookup: (trace_id, span_id) -> endpoint_key."""
    lookup: dict[tuple[str, str], str] = {}
    mismatches = 0
    for _, row in endpoints_df.iterrows():
        trace_id = _normalize_id(row.get("trace_id"))
        span_id = _normalize_id(row.get("span_id"))
        if not trace_id or not span_id:
            mismatches += 1
            continue
        key = (trace_id, span_id)
        lookup[key] = row["endpoint_key"]
    if mismatches:
        logger.debug("Skipped %d endpoint rows with empty trace_id or span_id", mismatches)
    return lookup


def _find_endpoint_for_db_span(
    trace_id: str,
    span_id: str,
    span_lookup: dict,
    endpoint_lookup: dict,
    visited: set | None = None,
    depth: int = 0,
) -> tuple[str | None, str | None]:
    """Find the endpoint for a DB span by walking up parent chain.

    Returns: (endpoint_key, service_name) or (None, None) if not found.
    """
    if visited is None:
        visited = set()

    tid = _normalize_id(trace_id)
    sid = _normalize_id(span_id)

    if not tid or not sid:
        if depth == 0:
            logger.debug("Empty trace_id or span_id in DB span — cannot walk chain")
        return None, None

    # Prevent infinite loops
    if (tid, sid) in visited:
        if depth == 0:
            logger.debug("Cycle detected for (%s, %s) — aborting chain walk", tid, sid)
        return None, None
    visited.add((tid, sid))

    # Check if this span is an endpoint
    ep_entry = endpoint_lookup.get((tid, sid))
    if ep_entry is not None:
        if depth > 0:
            logger.debug(
                "  chain walk: (%s, %s) → endpoint %s after %d hop(s)",
                tid, sid, ep_entry, depth,
            )
        svc = span_lookup.get((tid, sid), {}).get("service_name")
        return ep_entry, svc

    # Get parent and continue walking up
    span_info = span_lookup.get((tid, sid))
    if span_info is None:
        if depth == 0:
            logger.debug(
                "  chain walk: (%s, %s) not found in span lookup — "
                "trace_id/span_id format mismatch between DataFrames",
                tid, sid,
            )
        return None, None

    parent_id = _normalize_id(span_info.get("parent_span_id"))
    if not parent_id:
        if depth == 0:
            logger.debug(
                "  chain walk: (%s, %s) has no parent span — reached root without finding endpoint",
                tid, sid,
            )
        return None, None

    return _find_endpoint_for_db_span(tid, parent_id, span_lookup, endpoint_lookup, visited, depth + 1)


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

    # Pre-check: count distinct trace_id/span_id in each table
    db_key_count = len(db_ops_df[["trace_id", "span_id"]].drop_duplicates())
    ep_key_count = len(endpoints_df[["trace_id", "span_id"]].drop_duplicates())
    span_key_count = len(spans_df[["trace_id", "span_id"]].drop_duplicates())
    logger.debug(
        "Mapping inputs: %d DB spans, %d endpoint spans, %d total spans "
        "(by trace_id/span_id pairs)",
        db_key_count, ep_key_count, span_key_count,
    )

    # Collect mappings
    mappings = []
    db_spans_no_endpoint = 0
    db_spans_no_parent = 0
    db_spans_found = 0

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
            db_spans_no_endpoint += 1
            endpoint_key = "unknown_endpoint"
            service_name = db_row.get("service_name", "unknown")
        else:
            db_spans_found += 1

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

    logger.debug(
        "Chain-walk results: %d DB spans matched to endpoints, "
        "%d fell to fallback (unknown_endpoint), %d had no parent",
        db_spans_found, db_spans_no_endpoint, db_spans_no_parent,
    )

    if not mappings:
        logger.warning(
            "No endpoint-to-table mappings could be built from %d DB operations "
            "(chain-walk: %d found, %d fell back, %d no parent). "
            "The parent-span chain walking may have failed. Check that HTTP endpoint "
            "spans exist and that DB spans have valid parent span IDs.",
            len(db_ops_df), db_spans_found, db_spans_no_endpoint, db_spans_no_parent,
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
