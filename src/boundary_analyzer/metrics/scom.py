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
            svc = str(service_name) if service_name and str(service_name).strip() else "unknown_service"
            all_eps.setdefault(svc, set()).update(group["endpoint_key"].dropna().astype(str).unique())

    if endpoints_df is not None and not endpoints_df.empty:
        for service_name, group in endpoints_df.groupby("service_name"):
            svc = str(service_name) if service_name and str(service_name).strip() else "unknown_service"
            all_eps.setdefault(svc, set()).update(group["endpoint_key"].dropna().astype(str).unique())

    return all_eps


def _build_endpoint_table_sets(
    mapping_df: pd.DataFrame,
    endpoints_df: pd.DataFrame | None,
) -> dict[str, dict[str, set[str]]]:
    """Build endpoint -> table sets per service, including endpoints with no DB ops (empty set)."""
    all_endpoints_by_service = _build_all_endpoints_by_service(mapping_df, endpoints_df)
    result: dict[str, dict[str, set[str]]] = {}

    if not mapping_df.empty:
        for service_name, service_df in mapping_df.groupby("service_name"):
            svc = str(service_name) if service_name and str(service_name).strip() else "unknown_service"
            endpoint_sets: dict[str, set[str]] = {}
            for endpoint_key, ep_df in service_df.groupby("endpoint_key"):
                endpoint_sets[str(endpoint_key)] = set(ep_df["table"].dropna().astype(str).tolist())
            result[svc] = endpoint_sets

    for service_name, eps in all_endpoints_by_service.items():
        svc = result.setdefault(service_name, {})
        for ep in eps:
            svc.setdefault(ep, set())

    return result


def _get_endpoint_frequencies(endpoints_df: pd.DataFrame | None, mapping_df: pd.DataFrame) -> dict[str, float]:
    """Compute normalized endpoint frequencies. 
    
    If endpoints_df is available, we count the exact number of spans per endpoint.
    Otherwise, we attempt a fallback using mapping_df (which is less accurate since it misses DB-less endpoints).
    """
    if endpoints_df is not None and not endpoints_df.empty and "endpoint_key" in endpoints_df.columns:
        counts = endpoints_df["endpoint_key"].value_counts()
    elif not mapping_df.empty and "endpoint_key" in mapping_df.columns and "count" in mapping_df.columns:
        counts = mapping_df.groupby("endpoint_key")["count"].sum()
    else:
        return {}

    total = float(counts.sum())
    if total <= 0:
        return {}

    return (counts / total).to_dict()


def _compute_service_scom(
    endpoint_sets: dict[str, set[str]],
    endpoint_frequencies: dict[str, float],
    use_endpoint_weighting: bool
) -> float:
    """Compute SCOM mathematically matching the paper's definition.
    
    Paper formula:
    CI(e_i, e_j) = |A(e_i) ∩ A(e_j)|
    CI_max = max_{i!=j} (min(|A(e_i)|, |A(e_j)|))
    
    If unweighted (use_endpoint_weighting = False):
    N = |E|(|E|-1)/2
    SCOM = (sum_{i<j} CI(e_i, e_j)) / (N * CI_max)
    
    If weighted (use_endpoint_weighting = True):
    w_ij = freq(e_i) * freq(e_j)
    SCOM = (sum_{i<j} w_ij * CI(e_i, e_j)) / (sum_{i<j} w_ij * CI_max)
    """
    endpoints = list(endpoint_sets.keys())
    endpoints_count = len(endpoints)

    # "La définition attribue une valeur de cohésion de 0 aux services ayant moins de deux points d'accès"
    if endpoints_count < 2:
        return 0.0

    # 1. Compute CI_max globally for the service
    ci_max = 0
    for i in range(endpoints_count):
        for j in range(i + 1, endpoints_count):
            overlap_possible = min(len(endpoint_sets[endpoints[i]]), len(endpoint_sets[endpoints[j]]))
            ci_max = max(ci_max, overlap_possible)

    # Cannot normalize if max possible overlap is 0
    if ci_max <= 0:
        return 0.0

    # 2. Compute the SCOM sum
    ci_sum_weighted = 0.0
    weight_sum = 0.0

    for i in range(endpoints_count):
        for j in range(i + 1, endpoints_count):
            ep_a = endpoints[i]
            ep_b = endpoints[j]

            # Intersection length is the Connection Intensity (CI)
            ci = len(endpoint_sets[ep_a] & endpoint_sets[ep_b])

            if use_endpoint_weighting:
                w = endpoint_frequencies.get(ep_a, 1.0) * endpoint_frequencies.get(ep_b, 1.0)
            else:
                w = 1.0

            ci_sum_weighted += w * ci
            weight_sum += w

    if weight_sum <= 0:
        return 0.0

    return ci_sum_weighted / (weight_sum * ci_max)


def compute_scom(
    mapping_df: pd.DataFrame,
    endpoints_df: pd.DataFrame | None = None,
    use_endpoint_weighting: bool = True
) -> pd.DataFrame:
    """Compute SCOM scores for all services faithfully based on the academic paper.
    
    Args:
        mapping_df: Endpoint to DB table mapping
        endpoints_df: Traces of endpoints (used to correctly identify endpoints with no DB operations and calculate frequencies)
        use_endpoint_weighting: If True, uses endpoint invocation frequency as weights. If False, all endpoints have equal weight.
    """
    if mapping_df.empty and (endpoints_df is None or endpoints_df.empty):
        return pd.DataFrame(columns=[
            "service_name", "scom_score", "endpoints_count", "tables_count", "method"
        ])

    endpoint_frequencies = _get_endpoint_frequencies(endpoints_df, mapping_df) if use_endpoint_weighting else {}
    endpoint_table_sets = _build_endpoint_table_sets(mapping_df, endpoints_df)
    
    results: list[dict[str, Any]] = []

    for service_name, endpoint_sets in endpoint_table_sets.items():
        scom = _compute_service_scom(
            endpoint_sets=endpoint_sets,
            endpoint_frequencies=endpoint_frequencies,
            use_endpoint_weighting=use_endpoint_weighting
        )

        endpoints_count = len(endpoint_sets)
        lookup_service = service_name if service_name != "unknown_service" else ""
        service_df = mapping_df[mapping_df["service_name"] == lookup_service] if not mapping_df.empty else pd.DataFrame()
        tables_count = int(service_df["table"].nunique()) if not service_df.empty else 0

        results.append({
            "service_name": service_name,
            "scom_score": round(float(scom), 4),
            "endpoints_count": endpoints_count,
            "tables_count": tables_count,
            "method": "weighted" if use_endpoint_weighting else "unweighted"
        })

    return pd.DataFrame(results)


def save_scom_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save SCOM scores to CSV."""
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(output_path, index=False)
