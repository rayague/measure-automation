"""Track a single endpoint's data-cohesion across saved runs.

Answers, for one endpoint the user cares about: *which tables does it touch,
how often, and how much data does it share with its sibling endpoints —
and how has that evolved run over run?*

The per-endpoint score is derived from the same Connection Intensity used
by SCOM (see :mod:`boundary_analyzer.metrics.scom`):

    overlap(e, e') = |A(e) ∩ A(e')| / min(|A(e)|, |A(e')|)   in [0, 1]

    endpoint_cohesion(e) = mean over sibling endpoints e' of overlap(e, e')

1.0 means every sibling shares as many tables with *e* as it possibly
could; 0.0 means *e* shares no table with any sibling. Endpoints with no
siblings (single-endpoint service) have no defined score (``None``).

Data source: each saved run's ``interim/endpoint_table_map.csv``
(``service_name, endpoint_key, table, count``), persisted by the run
registry since v0.7.8. Endpoints that never touched a table do not appear
in the mapping and therefore report "no table data" — honestly — rather
than a fabricated zero.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from boundary_analyzer.auto.run_registry import get_run_path, list_runs, load_run_meta

logger = logging.getLogger(__name__)

#: Locations of the endpoint→table mapping inside a run directory, in
#: preference order (registry copies to interim/; some older runs kept it
#: at the top level).
_MAPPING_CANDIDATES = ("interim/endpoint_table_map.csv", "endpoint_table_map.csv")


@dataclass
class EndpointSnapshot:
    """One endpoint's data-access state in one saved run."""

    run_id: str
    timestamp: str
    service_name: str
    endpoint_key: str
    tables: dict[str, int] = field(default_factory=dict)  # table -> access count
    sibling_count: int = 0
    cohesion: float | None = None  # mean overlap with siblings, None if no siblings

    @property
    def total_accesses(self) -> int:
        return sum(self.tables.values())


def _load_mapping(run_dir: Path) -> pd.DataFrame | None:
    for rel in _MAPPING_CANDIDATES:
        p = run_dir / rel
        if p.exists():
            try:
                df = pd.read_csv(p)
            except (OSError, pd.errors.EmptyDataError, pd.errors.ParserError) as e:
                logger.warning("Unreadable mapping in %s: %s", p, e)
                return None
            required = {"service_name", "endpoint_key", "table", "count"}
            if required.issubset(df.columns):
                return df
            logger.warning("Mapping %s lacks expected columns %s", p, required - set(df.columns))
            return None
    return None


def _endpoint_cohesion(
    endpoint_key: str,
    service_df: pd.DataFrame,
) -> tuple[float | None, int]:
    """Compute the endpoint's mean table-overlap with its sibling endpoints.

    Returns ``(cohesion, sibling_count)``. ``cohesion`` is ``None`` when the
    endpoint has no siblings with table data (nothing to overlap with).
    """
    table_sets: dict[str, set[str]] = {
        str(ep): set(grp["table"].dropna().astype(str))
        for ep, grp in service_df.groupby("endpoint_key")
    }
    mine = table_sets.get(endpoint_key)
    if mine is None:
        return None, 0

    siblings = {ep: tabs for ep, tabs in table_sets.items() if ep != endpoint_key}
    if not siblings:
        return None, 0

    overlaps: list[float] = []
    for tabs in siblings.values():
        denom = min(len(mine), len(tabs))
        overlaps.append((len(mine & tabs) / denom) if denom else 0.0)
    return sum(overlaps) / len(overlaps), len(siblings)


def track_endpoint(
    pattern: str,
    service: str | None = None,
    max_runs: int = 20,
    data_root: Path | None = None,
) -> list[EndpointSnapshot]:
    """Collect snapshots of every endpoint matching *pattern* across runs.

    Args:
        pattern:   Case-insensitive substring matched against endpoint keys
                   (e.g. ``"/orders"`` or ``"GET /users"``). An exact key
                   works too.
        service:   Restrict to one service name (case-insensitive), if given.
        max_runs:  How many most-recent runs to inspect.
        data_root: Registry location override (defaults to the resolved
                   central registry).

    Returns:
        Snapshots ordered oldest→newest (natural reading order for trends),
        possibly covering several endpoints/services when the pattern is
        broad. Runs without mapping data, or without a matching endpoint,
        contribute no snapshot.
    """
    pattern_l = pattern.lower()
    service_l = service.lower() if service else None

    snapshots: list[EndpointSnapshot] = []
    runs = list_runs(data_root)[:max_runs] if data_root is not None else list_runs()[:max_runs]

    for run in runs:
        run_id = str(run.get("id", ""))
        run_dir = get_run_path(run_id, data_root) if data_root is not None else get_run_path(run_id)
        if not run_dir:
            continue
        mapping = _load_mapping(Path(run_dir))
        if mapping is None or mapping.empty:
            continue

        meta = (load_run_meta(run_id, data_root) if data_root is not None else load_run_meta(run_id)) or {}
        timestamp = str(meta.get("timestamp", run.get("timestamp", "")))

        hits = mapping[mapping["endpoint_key"].astype(str).str.lower().str.contains(pattern_l, regex=False)]
        if service_l:
            hits = hits[hits["service_name"].astype(str).str.lower() == service_l]
        if hits.empty:
            continue

        for (svc, ep), grp in hits.groupby(["service_name", "endpoint_key"]):
            svc, ep = str(svc), str(ep)
            service_df = mapping[mapping["service_name"].astype(str) == svc]
            cohesion, sibling_count = _endpoint_cohesion(ep, service_df)
            snapshots.append(
                EndpointSnapshot(
                    run_id=run_id,
                    timestamp=timestamp,
                    service_name=svc,
                    endpoint_key=ep,
                    tables={str(r["table"]): int(r["count"]) for _, r in grp.iterrows()},
                    sibling_count=sibling_count,
                    cohesion=cohesion,
                )
            )

    snapshots.reverse()  # list_runs is newest-first; trends read oldest→newest
    return snapshots
