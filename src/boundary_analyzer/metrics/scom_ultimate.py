from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


def _build_all_endpoints_by_service(
    mapping_df: pd.DataFrame,
    endpoints_df: pd.DataFrame | None,
) -> dict[str, set[str]]:
    """Return {service_name: {endpoint_key,...}} including endpoints without DB ops."""
    all_eps: dict[str, set[str]] = {}

    if not mapping_df.empty:
        for service_name, group in mapping_df.groupby("service_name"):
            all_eps.setdefault(str(service_name), set()).update(group["endpoint_key"].dropna().astype(str).unique())

    if endpoints_df is not None and not endpoints_df.empty:
        for service_name, group in endpoints_df.groupby("service_name"):
            all_eps.setdefault(str(service_name), set()).update(group["endpoint_key"].dropna().astype(str).unique())

    return all_eps


def _build_endpoint_table_sets_with_optional_endpoints(
    mapping_df: pd.DataFrame,
    endpoints_df: pd.DataFrame | None,
) -> dict[str, dict[str, set[str]]]:
    """Build endpoint -> table sets per service, optionally including empty sets."""
    all_endpoints_by_service = _build_all_endpoints_by_service(mapping_df, endpoints_df)

    result: dict[str, dict[str, set[str]]] = {}

    # Build from mapping (tables observed)
    if not mapping_df.empty:
        for service_name, service_df in mapping_df.groupby("service_name"):
            endpoint_sets: dict[str, set[str]] = {}
            for endpoint_key, ep_df in service_df.groupby("endpoint_key"):
                endpoint_sets[str(endpoint_key)] = set(ep_df["table"].dropna().astype(str).tolist())
            result[str(service_name)] = endpoint_sets

    # Ensure endpoints without DB are present with empty set
    for service_name, eps in all_endpoints_by_service.items():
        svc = result.setdefault(service_name, {})
        for ep in eps:
            svc.setdefault(ep, set())

    return result


def _compute_table_frequencies(mapping_df: pd.DataFrame) -> dict[str, float]:
    """Compute table frequencies across all endpoint-table mappings.
    
    Tables that appear more often are considered more important.
    Returns: {table_name: frequency}
    """
    if mapping_df.empty or "table" not in mapping_df.columns:
        return {}

    table_counts = mapping_df["table"].value_counts()
    total_count = float(table_counts.sum())
    if total_count <= 0:
        return {}

    # Normalize to [0, 1] range
    return (table_counts / total_count).to_dict()


def _compute_endpoint_frequencies(mapping_df: pd.DataFrame) -> dict[str, float]:
    """Compute endpoint frequencies from mapping count.
    
    Endpoints that appear more often are considered more important.
    Returns: {endpoint_key: frequency}
    """
    if mapping_df.empty or "endpoint_key" not in mapping_df.columns or "count" not in mapping_df.columns:
        return {}

    endpoint_counts = mapping_df.groupby("endpoint_key")["count"].sum()
    total_count = float(endpoint_counts.sum())
    if total_count <= 0:
        return {}

    # Normalize to [0, 1] range
    return (endpoint_counts / total_count).to_dict()


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
    
    # Weighted intersection
    intersection = set_a & set_b
    weighted_intersection = sum(table_weights.get(t, 1.0) for t in intersection)
    
    # Weighted union
    union = set_a | set_b
    weighted_union = sum(table_weights.get(t, 1.0) for t in union)
    
    if weighted_union == 0:
        return 0.0
    
    return weighted_intersection / weighted_union


def _format_weighted_method(use_table_weighting: bool, use_endpoint_weighting: bool) -> str:
    method_parts: list[str] = []
    if use_table_weighting:
        method_parts.append("table-weighted")
    if use_endpoint_weighting:
        method_parts.append("endpoint-weighted")
    return "weighted-" + "-".join(method_parts) if method_parts else "simple"


def _compute_weighted_service_scom(
    endpoint_sets: dict[str, set[str]],
    table_weights: dict[str, float],
    endpoint_weights: dict[str, float],
    use_endpoint_weighting: bool,
) -> float:
    endpoints = list(endpoint_sets.keys())
    endpoints_count = len(endpoints)

    if endpoints_count == 0:
        return 0.0
    if endpoints_count == 1:
        return 1.0

    similarities: list[float] = []
    weights: list[float] = []

    for i in range(endpoints_count):
        for j in range(i + 1, endpoints_count):
            endpoint_a = endpoints[i]
            endpoint_b = endpoints[j]

            similarities.append(
                _weighted_jaccard_similarity(
                    endpoint_sets[endpoint_a],
                    endpoint_sets[endpoint_b],
                    table_weights,
                )
            )

            if use_endpoint_weighting:
                weights.append(endpoint_weights.get(endpoint_a, 1.0) * endpoint_weights.get(endpoint_b, 1.0))
            else:
                weights.append(1.0)

    total_weight = sum(weights)
    if total_weight > 0:
        return sum(s * w for s, w in zip(similarities, weights)) / total_weight
    return sum(similarities) / len(similarities) if similarities else 0.0


def _simple_scom_for_endpoint_sets(endpoint_sets: dict[str, set[str]]) -> float:
    endpoints = list(endpoint_sets.keys())
    endpoints_count = len(endpoints)

    if endpoints_count == 0:
        return 0.0
    if endpoints_count == 1:
        return 1.0

    similarities: list[float] = []
    for i in range(endpoints_count):
        for j in range(i + 1, endpoints_count):
            similarities.append(
                _weighted_jaccard_similarity(
                    endpoint_sets[endpoints[i]],
                    endpoint_sets[endpoints[j]],
                    {},
                )
            )

    return sum(similarities) / len(similarities) if similarities else 0.0


def compute_weighted_scom(
    mapping_df: pd.DataFrame,
    use_table_weighting: bool = True,
    use_endpoint_weighting: bool = True,
    endpoints_df: pd.DataFrame | None = None,
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
    if mapping_df.empty and (endpoints_df is None or endpoints_df.empty):
        return pd.DataFrame(columns=[
            "service_name", "scom_score", "endpoints_count", "tables_count", "method"
        ])
    
    table_weights = _compute_table_frequencies(mapping_df) if use_table_weighting else {}
    endpoint_weights = _compute_endpoint_frequencies(mapping_df) if use_endpoint_weighting else {}

    endpoint_table_sets = _build_endpoint_table_sets_with_optional_endpoints(mapping_df, endpoints_df)
    results: list[dict[str, Any]] = []

    for service_name, endpoint_sets in endpoint_table_sets.items():
        scom = _compute_weighted_service_scom(
            endpoint_sets,
            table_weights=table_weights,
            endpoint_weights=endpoint_weights,
            use_endpoint_weighting=use_endpoint_weighting,
        )

        endpoints_count = len(endpoint_sets)
        service_df = mapping_df[mapping_df["service_name"] == service_name] if not mapping_df.empty else pd.DataFrame()
        tables_count = int(service_df["table"].nunique()) if not service_df.empty else 0

        results.append({
            "service_name": service_name,
            "scom_score": round(float(scom), 4),
            "endpoints_count": endpoints_count,
            "tables_count": tables_count,
            "method": _format_weighted_method(use_table_weighting, use_endpoint_weighting),
        })
    
    return pd.DataFrame(results)


def compute_simple_scom(mapping_df: pd.DataFrame, endpoints_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Compute simple SCOM (unweighted Jaccard) for comparison.
    
    This is the MVP version for comparison with weighted SCOM.
    """
    if mapping_df.empty and (endpoints_df is None or endpoints_df.empty):
        return pd.DataFrame(columns=[
            "service_name", "scom_score", "endpoints_count", "tables_count", "method"
        ])
    
    # Build endpoint-table sets (no weights), optionally including endpoints without DB
    endpoint_table_sets = _build_endpoint_table_sets_with_optional_endpoints(mapping_df, endpoints_df)

    results: list[dict[str, Any]] = []
    
    for service_name, endpoint_sets in endpoint_table_sets.items():
        endpoints_count = len(endpoint_sets)
        scom = _simple_scom_for_endpoint_sets(endpoint_sets)

        service_df = mapping_df[mapping_df["service_name"] == service_name] if not mapping_df.empty else pd.DataFrame()
        tables_count = int(service_df["table"].nunique()) if not service_df.empty else 0
        
        results.append({
            "service_name": service_name,
            "scom_score": round(scom, 4),
            "endpoints_count": endpoints_count,
            "tables_count": tables_count,
            "method": "simple",
        })
    
    return pd.DataFrame(results)


def _paper_scom_for_endpoint_sets(endpoint_sets: dict[str, set[str]]) -> float:
    endpoints = list(endpoint_sets.keys())
    endpoints_count = len(endpoints)

    if endpoints_count < 2:
        return 0.0

    ci_max = 0
    for i in range(endpoints_count):
        for j in range(i + 1, endpoints_count):
            ci_max = max(ci_max, min(len(endpoint_sets[endpoints[i]]), len(endpoint_sets[endpoints[j]])))

    if ci_max <= 0:
        return 0.0

    ci_sum = 0
    for i in range(endpoints_count):
        for j in range(i + 1, endpoints_count):
            ci_sum += len(endpoint_sets[endpoints[i]] & endpoint_sets[endpoints[j]])

    n_pairs = endpoints_count * (endpoints_count - 1) / 2
    return (ci_sum / (n_pairs * ci_max)) if n_pairs > 0 else 0.0


def compute_paper_scom(mapping_df: pd.DataFrame, endpoints_df: pd.DataFrame | None = None) -> pd.DataFrame:
    """Compute SCOM exactly as described in the paper (endpoint-table adaptation).

    Definitions:
    - For each endpoint e, A(e) = set of accessed tables.
    - Connection intensity: CI(e_i,e_j) = |A(e_i) ∩ A(e_j)|.
    - CI_max = max_{i!=j} min(|A(e_i)|, |A(e_j)|).
    - N = |E|(|E|-1)/2.
    - SCOM = (1 / (N * CI_max)) * sum_{i<j} CI(e_i,e_j).

    Special cases (per paper):
    - |E| < 2 => SCOM = 0.0
    - CI_max == 0 => SCOM = 0.0 (cannot normalize)

    Notes:
    - endpoints_df is optional; when provided, endpoints without DB ops are included with A(e) = ∅.
    """
    if mapping_df.empty and (endpoints_df is None or endpoints_df.empty):
        return pd.DataFrame(columns=[
            "service_name", "scom_score", "endpoints_count", "tables_count", "method"
        ])

    endpoint_table_sets = _build_endpoint_table_sets_with_optional_endpoints(mapping_df, endpoints_df)
    results: list[dict[str, Any]] = []

    for service_name, endpoint_sets in endpoint_table_sets.items():
        endpoints_count = len(endpoint_sets)
        scom = _paper_scom_for_endpoint_sets(endpoint_sets)

        service_df = mapping_df[mapping_df["service_name"] == service_name] if not mapping_df.empty else pd.DataFrame()
        tables_count = int(service_df["table"].nunique()) if not service_df.empty else 0

        results.append({
            "service_name": service_name,
            "scom_score": round(float(scom), 4),
            "endpoints_count": endpoints_count,
            "tables_count": tables_count,
            "method": "paper",
        })

    return pd.DataFrame(results)


def save_scom_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save SCOM scores to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
