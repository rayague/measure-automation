from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from boundary_analyzer._utils import save_csv

"""Compute the Service COhesion Metric (SCOM) based on endpoint-to-table mappings."""


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
        ep_set: dict[str, set[str]] = result.setdefault(service_name, {})
        for ep in eps:
            ep_set.setdefault(ep, set())

    return result


def _get_endpoint_frequencies_by_service(
    endpoints_df: pd.DataFrame | None,
    mapping_df: pd.DataFrame,
) -> dict[str, dict[str, float]]:
    """Compute normalized endpoint frequencies per service.

    Returns {service_name: {endpoint_key: frequency}}.
    This ensures frequencies are not diluted by endpoints from other services.
    """
    frequencies: dict[str, dict[str, float]] = {}

    if endpoints_df is not None and not endpoints_df.empty and "endpoint_key" in endpoints_df.columns:
        for service_name, group in endpoints_df.groupby("service_name"):
            counts = group["endpoint_key"].value_counts()
            total = float(counts.sum())
            if total > 0:
                svc_key = str(service_name) if service_name and str(service_name).strip() else "unknown_service"
                frequencies[svc_key] = (counts / total).to_dict()
    elif not mapping_df.empty and "endpoint_key" in mapping_df.columns and "count" in mapping_df.columns:
        for service_name, group in mapping_df.groupby("service_name"):
            counts = group.groupby("endpoint_key")["count"].sum()
            total = float(counts.sum())
            if total > 0:
                svc_key = str(service_name) if service_name and str(service_name).strip() else "unknown_service"
                frequencies[svc_key] = (counts / total).to_dict()

    return frequencies


def _compute_service_scom(
    endpoint_sets: dict[str, set[str]],
    endpoint_frequencies: dict[str, float],
    use_endpoint_weighting: bool,
    service_name: str = "",
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
                if w == 0:
                    w = 1e-10
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
    use_endpoint_weighting: bool = True,
    exclude_services: list[str] | None = None,
    exclude_unknown_endpoint: bool = True,
    skip_no_db_services: bool = False,
) -> pd.DataFrame:
    """Compute per-service SCOM scores from the endpoint-to-table mapping.

    Args:
        mapping_df: Endpoint-to-DB-table mapping
        endpoints_df: Endpoint spans (used for frequency weighting and zero-DB endpoints)
        use_endpoint_weighting: Weight by endpoint invocation frequency
        exclude_services: Optional service names to exclude
        exclude_unknown_endpoint: Filter out unknown_endpoint rows
        skip_no_db_services: Exclude services with zero DB tables
    """
    if mapping_df.empty and (endpoints_df is None or endpoints_df.empty):
        return pd.DataFrame(columns=["service_name", "scom_score", "endpoints_count", "tables_count", "method"])

    # Exclude services before any computation
    if exclude_services:
        if not mapping_df.empty and "service_name" in mapping_df.columns:
            mapping_df = mapping_df[~mapping_df["service_name"].isin(exclude_services)]
        if endpoints_df is not None and not endpoints_df.empty and "service_name" in endpoints_df.columns:
            endpoints_df = endpoints_df[~endpoints_df["service_name"].isin(exclude_services)]

    # Exclude unknown endpoint entries that could not be traced back to any HTTP endpoint
    if exclude_unknown_endpoint:
        if not mapping_df.empty and "endpoint_key" in mapping_df.columns:
            mapping_df = mapping_df[mapping_df["endpoint_key"] != "unknown_endpoint"]
        if endpoints_df is not None and not endpoints_df.empty and "endpoint_key" in endpoints_df.columns:
            endpoints_df = endpoints_df[endpoints_df["endpoint_key"] != "unknown_endpoint"]

    endpoint_frequencies_by_service = (
        _get_endpoint_frequencies_by_service(endpoints_df, mapping_df) if use_endpoint_weighting else {}
    )
    endpoint_table_sets = _build_endpoint_table_sets(mapping_df, endpoints_df)

    results: list[dict[str, Any]] = []

    for service_name, endpoint_sets in endpoint_table_sets.items():
        svc_frequencies = endpoint_frequencies_by_service.get(service_name, {})
        scom = _compute_service_scom(
            endpoint_sets=endpoint_sets,
            endpoint_frequencies=svc_frequencies,
            use_endpoint_weighting=use_endpoint_weighting,
            service_name=service_name,
        )

        endpoints_count = len(endpoint_sets)
        lookup_service = service_name if service_name != "unknown_service" else ""
        service_df = (
            mapping_df[mapping_df["service_name"] == lookup_service] if not mapping_df.empty else pd.DataFrame()
        )
        tables_count = int(service_df["table"].nunique()) if not service_df.empty else 0

        results.append(
            {
                "service_name": service_name,
                "scom_score": round(float(scom), 4),
                "endpoints_count": endpoints_count,
                "tables_count": tables_count,
                "method": "weighted" if use_endpoint_weighting else "unweighted",
            }
        )

    df = pd.DataFrame(results)
    if skip_no_db_services and not df.empty:
        df = df[df["tables_count"] > 0].reset_index(drop=True)
    return df


def save_scom_csv(df: pd.DataFrame, output_path: Path) -> None:
    """Save computed SCOM scores to CSV."""
    save_csv(df, output_path)
