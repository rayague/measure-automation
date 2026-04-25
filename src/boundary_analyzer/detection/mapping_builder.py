from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _build_span_lookup(spans_df: pd.DataFrame) -> dict[str, dict[str, Any]]:
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
    """Build mapping from endpoints to tables.
    
    Uses parent chain walking to find the best endpoint for each DB span.
    """
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
        
        if not tables_str:
            continue
        
        # Find endpoint for this DB span
        endpoint_key, service_name = _find_endpoint_for_db_span(
            trace_id, span_id, span_lookup, endpoint_lookup
        )
        
        if not endpoint_key:
            # Fallback: use the first endpoint in the same trace
            trace_endpoints = endpoints_df[endpoints_df["trace_id"] == trace_id]
            if not trace_endpoints.empty:
                endpoint_key = trace_endpoints.iloc[0]["endpoint_key"]
                service_name = trace_endpoints.iloc[0]["service_name"]
        
        if not endpoint_key:
            endpoint_key = "unknown_endpoint"
            service_name = db_row.get("service_name", "unknown")
        
        # Split tables and create one row per table
        tables = [t.strip() for t in tables_str.split(",") if t.strip()]
        for table in tables:
            mappings.append({
                "service_name": service_name,
                "endpoint_key": endpoint_key,
                "table": table,
                "count": 1,
            })
    
    if not mappings:
        return pd.DataFrame(columns=["service_name", "endpoint_key", "table", "count"])
    
    # Aggregate counts
    df = pd.DataFrame(mappings)
    result = df.groupby(
        ["service_name", "endpoint_key", "table"]
    ).agg({"count": "sum"}).reset_index()
    
    return result


def save_endpoint_table_map_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save endpoint-table mapping to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
