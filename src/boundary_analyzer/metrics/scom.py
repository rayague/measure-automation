from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _jaccard_similarity(set_a: set, set_b: set) -> float:
    """Compute Jaccard similarity between two sets.
    
    Formula: |A ∩ B| / |A ∪ B|
    
    Returns 0.0 if both sets are empty.
    """
    if not set_a and not set_b:
        return 0.0
    
    intersection = len(set_a & set_b)
    union = len(set_a | set_b)
    
    if union == 0:
        return 0.0
    
    return intersection / union


def _build_endpoint_table_sets(mapping_df: pd.DataFrame) -> dict[str, dict[str, set[str]]]:
    """Build endpoint -> table sets per service.
    
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


def compute_scom(mapping_df: pd.DataFrame) -> pd.DataFrame:
    """Compute SCOM cohesion score for each service.
    
    SCOM Formula:
    - For each service, get all endpoint-table sets
    - For each pair of endpoints, compute Jaccard similarity
    - SCOM = average Jaccard similarity across all endpoint pairs
    
    Special cases:
    - 0 endpoints: SCOM = 0.0 (no data)
    - 1 endpoint: SCOM = 1.0 (perfect by default)
    """
    if mapping_df.empty:
        return pd.DataFrame(columns=["service_name", "scom_score", "endpoints_count", "tables_count"])
    
    endpoint_table_sets = _build_endpoint_table_sets(mapping_df)
    
    results = []
    
    for service_name, endpoint_sets in endpoint_table_sets.items():
        endpoints = list(endpoint_sets.keys())
        endpoints_count = len(endpoints)
        
        if endpoints_count == 0:
            scom = 0.0
        elif endpoints_count == 1:
            scom = 1.0
        else:
            # Compute average Jaccard similarity across all pairs
            similarities = []
            for i in range(endpoints_count):
                for j in range(i + 1, endpoints_count):
                    sim = _jaccard_similarity(
                        endpoint_sets[endpoints[i]],
                        endpoint_sets[endpoints[j]]
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
        })
    
    return pd.DataFrame(results)


def save_scom_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save SCOM scores to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
