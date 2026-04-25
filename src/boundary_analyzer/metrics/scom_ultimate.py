from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _compute_table_frequencies(mapping_df: pd.DataFrame) -> dict[str, float]:
    """Compute table frequencies across all endpoint-table mappings.
    
    Tables that appear more often are considered more important.
    Returns: {table_name: frequency}
    """
    table_counts = mapping_df["table"].value_counts()
    total_count = table_counts.sum()
    
    # Normalize to [0, 1] range
    table_freq = (table_counts / total_count).to_dict()
    
    return table_freq


def _compute_endpoint_frequencies(mapping_df: pd.DataFrame) -> dict[str, float]:
    """Compute endpoint frequencies from mapping count.
    
    Endpoints that appear more often are considered more important.
    Returns: {endpoint_key: frequency}
    """
    endpoint_counts = mapping_df.groupby("endpoint_key")["count"].sum()
    total_count = endpoint_counts.sum()
    
    # Normalize to [0, 1] range
    endpoint_freq = (endpoint_counts / total_count).to_dict()
    
    return endpoint_freq


def _weighted_jaccard_similarity(
    set_a: set[str],
    set_b: set[str],
    table_weights: dict[str, float],
) -> float:
    """Compute weighted Jaccard similarity between two table sets.
    
    Formula:
    - Weighted intersection = sum of weights for tables in both sets
    - Weighted union = sum of weights for tables in either set
    - Similarity = weighted_intersection / weighted_union
    
    If a table is not in weights, use default weight (1/n_tables).
    """
    if not set_a and not set_b:
        return 0.0
    
    # Get weights for tables
    weights_a = {t: table_weights.get(t, 1.0) for t in set_a}
    weights_b = {t: table_weights.get(t, 1.0) for t in set_b}
    
    # Weighted intersection
    intersection = set_a & set_b
    weighted_intersection = sum(table_weights.get(t, 1.0) for t in intersection)
    
    # Weighted union
    union = set_a | set_b
    weighted_union = sum(table_weights.get(t, 1.0) for t in union)
    
    if weighted_union == 0:
        return 0.0
    
    return weighted_intersection / weighted_union


def _build_endpoint_table_sets_weighted(
    mapping_df: pd.DataFrame,
    table_weights: dict[str, float],
    endpoint_weights: dict[str, float],
) -> dict[str, dict[str, set[str]]]:
    """Build endpoint -> table sets per service with weights.
    
    Returns: {service_name: {endpoint_key: {table1, table2, ...}}}
    """
    result: dict[str, dict[str, set[str]]] = {}
    
    for service in mapping_df["service_name"].unique():
        service_df = mapping_df[mapping_df["service_name"] == service]
        endpoint_sets: dict[str, set[str]] = {}
        
        for endpoint in service_df["endpoint_key"].unique():
            endpoint_df = service_df[service_df["endpoint_key"] == endpoint]
            tables = set(endpoint_df["table"].tolist())
            endpoint_sets[endpoint] = tables
        
        result[service] = endpoint_sets
    
    return result


def compute_weighted_scom(
    mapping_df: pd.DataFrame,
    use_table_weighting: bool = True,
    use_endpoint_weighting: bool = True,
) -> pd.DataFrame:
    """Compute weighted SCOM cohesion score for each service.
    
    Weighted SCOM Formula:
    - For each service, get endpoint-table sets
    - Compute table frequencies (if use_table_weighting)
    - Compute endpoint frequencies (if use_endpoint_weighting)
    - For each pair of endpoints, compute weighted Jaccard similarity
    - Weight endpoint pairs by endpoint frequency (if use_endpoint_weighting)
    - SCOM = weighted average of similarities
    
    Special cases:
    - 0 endpoints: SCOM = 0.0 (no data)
    - 1 endpoint: SCOM = 1.0 (perfect by default)
    
    Args:
        mapping_df: DataFrame with columns service_name, endpoint_key, table, count
        use_table_weighting: Whether to weight tables by frequency
        use_endpoint_weighting: Whether to weight endpoint pairs by frequency
    
    Returns:
        DataFrame with columns: service_name, scom_score, endpoints_count, tables_count, method
    """
    if mapping_df.empty:
        return pd.DataFrame(columns=[
            "service_name", "scom_score", "endpoints_count", "tables_count", "method"
        ])
    
    # Compute weights
    table_weights = {}
    endpoint_weights = {}
    
    if use_table_weighting:
        table_weights = _compute_table_frequencies(mapping_df)
    
    if use_endpoint_weighting:
        endpoint_weights = _compute_endpoint_frequencies(mapping_df)
    
    # Build endpoint-table sets
    endpoint_table_sets = _build_endpoint_table_sets_weighted(
        mapping_df, table_weights, endpoint_weights
    )
    
    results = []
    
    for service_name, endpoint_sets in endpoint_table_sets.items():
        endpoints = list(endpoint_sets.keys())
        endpoints_count = len(endpoints)
        
        if endpoints_count == 0:
            scom = 0.0
        elif endpoints_count == 1:
            scom = 1.0
        else:
            # Compute weighted average similarity across all pairs
            similarities = []
            weights = []
            
            for i in range(endpoints_count):
                for j in range(i + 1, endpoints_count):
                    endpoint_a = endpoints[i]
                    endpoint_b = endpoints[j]
                    
                    sim = _weighted_jaccard_similarity(
                        endpoint_sets[endpoint_a],
                        endpoint_sets[endpoint_b],
                        table_weights,
                    )
                    similarities.append(sim)
                    
                    # Weight by endpoint frequency if enabled
                    if use_endpoint_weighting:
                        weight = (
                            endpoint_weights.get(endpoint_a, 1.0) *
                            endpoint_weights.get(endpoint_b, 1.0)
                        )
                        weights.append(weight)
                    else:
                        weights.append(1.0)
            
            # Weighted average
            total_weight = sum(weights)
            if total_weight > 0:
                weighted_sum = sum(s * w for s, w in zip(similarities, weights))
                scom = weighted_sum / total_weight
            else:
                scom = sum(similarities) / len(similarities) if similarities else 0.0
        
        # Count unique tables for this service
        service_df = mapping_df[mapping_df["service_name"] == service_name]
        tables_count = service_df["table"].nunique()
        
        method_parts = []
        if use_table_weighting:
            method_parts.append("table-weighted")
        if use_endpoint_weighting:
            method_parts.append("endpoint-weighted")
        method_str = "weighted-" + "-".join(method_parts) if method_parts else "simple"
        
        results.append({
            "service_name": service_name,
            "scom_score": round(scom, 4),
            "endpoints_count": endpoints_count,
            "tables_count": tables_count,
            "method": method_str,
        })
    
    return pd.DataFrame(results)


def compute_simple_scom(mapping_df: pd.DataFrame) -> pd.DataFrame:
    """Compute simple SCOM (unweighted Jaccard) for comparison.
    
    This is the MVP version for comparison with weighted SCOM.
    """
    if mapping_df.empty:
        return pd.DataFrame(columns=[
            "service_name", "scom_score", "endpoints_count", "tables_count", "method"
        ])
    
    # Build endpoint-table sets (no weights)
    endpoint_table_sets = _build_endpoint_table_sets_weighted(mapping_df, {}, {})
    
    results = []
    
    for service_name, endpoint_sets in endpoint_table_sets.items():
        endpoints = list(endpoint_sets.keys())
        endpoints_count = len(endpoints)
        
        if endpoints_count == 0:
            scom = 0.0
        elif endpoints_count == 1:
            scom = 1.0
        else:
            # Simple Jaccard similarity (unweighted)
            similarities = []
            for i in range(endpoints_count):
                for j in range(i + 1, endpoints_count):
                    sim = _weighted_jaccard_similarity(
                        endpoint_sets[endpoints[i]],
                        endpoint_sets[endpoints[j]],
                        {},  # No table weights
                    )
                    similarities.append(sim)
            
            scom = sum(similarities) / len(similarities) if similarities else 0.0
        
        # Count unique tables for this service
        service_df = mapping_df[mapping_df["service_name"] == service_name]
        tables_count = service_df["table"].nunique()
        
        results.append({
            "service_name": service_name,
            "scom_score": round(scom, 4),
            "endpoints_count": endpoints_count,
            "tables_count": tables_count,
            "method": "simple",
        })
    
    return pd.DataFrame(results)


def save_scom_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save SCOM scores to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
