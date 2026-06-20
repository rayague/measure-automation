from __future__ import annotations

"""Phased, ordered traffic engine for the MBA (Microservice Boundary Analyzer).

This module provides :class:`OrderedTrafficEngine`, which replaces the purely
random :func:`~boundary_analyzer.auto.traffic.generate_traffic` function with a
deterministic, phase-ordered execution strategy that guarantees:

* POST endpoints receive semantically coherent payloads **before** GETs
  verify the created data — avoiding empty-result queries.
* DELETE endpoints run **only after** data has been seeded, read, and mutated.
* A STRESS phase hammers every endpoint concurrently with configurable
  method-weight distribution.

Phases run in this fixed order:

    PROBE → SEED → READ → MUTATE → STRESS → CLEANUP

Only the STRESS phase uses a :class:`~concurrent.futures.ThreadPoolExecutor`;
every other phase executes sequentially so that ordering guarantees hold.

Typical usage::

    from boundary_analyzer.auto.traffic import TrafficConfig
    from boundary_analyzer.auto.traffic_engine import OrderedTrafficEngine

    engine = OrderedTrafficEngine(
        services=[my_service],
        endpoint_map={"my-service": endpoints},
        config=TrafficConfig(duration=120, workers=8),
        on_endpoint_update=lambda s: ui.update(s),
        on_phase_change=lambda name, idx, total: ui.phase(name, idx, total),
        on_log=lambda msg, lvl: print(f"[{lvl.upper()}] {msg}"),
    )
    result = engine.run()
    print(f"Success rate: {result.success_rate:.1%}")
"""

import logging
import random
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable
from urllib.parse import urljoin

import requests

from boundary_analyzer.auto.models import Endpoint, ServiceInfo
from boundary_analyzer.auto.traffic import (
    TrafficConfig,
    _generate_request_body,
    _guess_body_from_path,
    _send_request,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Phase enumeration
# ---------------------------------------------------------------------------


class Phase(str, Enum):
    """Ordered execution phases for structured CRUD traffic generation.

    Each value doubles as its string label (lower-case).  Use
    :attr:`label` for the upper-case version used in callbacks and logs.

    Phases are always executed in the order defined in :data:`PHASE_ORDER`.
    """

    PROBE = "probe"
    SEED = "seed"
    READ = "read"
    MUTATE = "mutate"
    STRESS = "stress"
    CLEANUP = "cleanup"

    @property
    def label(self) -> str:
        """Upper-case label used in callbacks and log messages."""
        return self.value.upper()


#: Canonical execution order.  Do **not** reorder without updating the
#: phase-timing logic in :meth:`OrderedTrafficEngine._compute_phase_durations`.
PHASE_ORDER: list[Phase] = [
    Phase.PROBE,
    Phase.SEED,
    Phase.READ,
    Phase.MUTATE,
    Phase.STRESS,
    Phase.CLEANUP,
]

#: STRESS-phase method-weight distribution (values must sum to ≤ 1.0).
_STRESS_WEIGHTS: dict[str, float] = {
    "GET": 0.60,
    "POST": 0.25,
    "PUT": 0.10,
    "DELETE": 0.05,
}

# ---------------------------------------------------------------------------
# Dataclasses
# ---------------------------------------------------------------------------


@dataclass
class EndpointStatus:
    """Per-endpoint execution state tracked across all phases.

    An instance is created for every known endpoint at engine initialisation
    and updated thread-safely after each HTTP request.

    Attributes:
        service_name: Name of the owning microservice.
        method: HTTP verb (``"GET"``, ``"POST"``, …).
        path: URL path template — may contain ``{param}`` tokens.
        phase: Current lifecycle label.  One of ``"pending"``,
            ``"probing"``, ``"active"``, ``"success"``, ``"failed"``,
            ``"skipped"``.
        http_status: Most recently observed HTTP response status code.
            ``0`` indicates a network-level failure with no response.
        attempts: Total requests dispatched to this endpoint (all phases).
        successes: Requests that received a non-5xx HTTP response.
        db_ops_triggered: Populated externally by the caller from Jaeger /
            metrics data after the engine run completes.
        response_ms: Round-trip latency of the most recent request, in ms.
        last_error: Human-readable description of the last failure, or
            an empty string when the last attempt succeeded.
        last_payload: Indicative JSON body dispatched on the last mutating
            request (POST / PUT / PATCH).  Values are approximate because
            :func:`~boundary_analyzer.auto.traffic._send_request` re-generates
            random field values at send time.
    """

    service_name: str
    method: str
    path: str
    phase: str = "pending"
    http_status: int = 0
    attempts: int = 0
    successes: int = 0
    db_ops_triggered: int = 0
    response_ms: float = 0.0
    last_error: str = ""
    last_payload: dict = field(default_factory=dict)

    @property
    def key(self) -> str:
        """Canonical string identifier: ``"service_name::METHOD /path"``."""
        return f"{self.service_name}::{self.method.upper()} {self.path}"


@dataclass
class PhaseResult:
    """Aggregate statistics collected during a single execution phase.

    Attributes:
        phase: Phase name (matches the :class:`Phase` enum *value*, e.g.
            ``"SEED"``).
        requests_sent: Total HTTP requests dispatched in this phase.
        requests_ok: Requests that received a non-5xx HTTP response.
        endpoints_reached: Canonical keys of endpoints with ≥ 1 success.
        duration_seconds: Wall-clock seconds spent in this phase.
    """

    phase: str
    requests_sent: int = 0
    requests_ok: int = 0
    endpoints_reached: set = field(default_factory=set)
    duration_seconds: float = 0.0


@dataclass
class EngineResult:
    """Top-level result returned by :meth:`OrderedTrafficEngine.run`.

    Attributes:
        total_requests: All HTTP requests dispatched across every phase.
        successful_requests: Requests that received a non-5xx response.
        failed_requests: Requests that resulted in a 5xx or network error.
        endpoints_tested: Unique endpoints with at least one attempt.
        endpoints_ok: Unique endpoints with at least one successful response.
        phases: Ordered list of :class:`PhaseResult` objects, one per phase.
        endpoint_statuses: Final per-endpoint status map.
            Key format: ``"service_name::METHOD /path"``.
        duration_seconds: Total wall-clock seconds for the full engine run.
    """

    total_requests: int = 0
    successful_requests: int = 0
    failed_requests: int = 0
    endpoints_tested: int = 0
    endpoints_ok: int = 0
    phases: list[PhaseResult] = field(default_factory=list)
    endpoint_statuses: dict[str, EndpointStatus] = field(default_factory=dict)
    duration_seconds: float = 0.0

    @property
    def success_rate(self) -> float:
        """Fraction of successful requests in the range ``[0.0, 1.0]``."""
        if self.total_requests == 0:
            return 0.0
        return self.successful_requests / self.total_requests


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _ep_key(service_name: str, method: str, path: str) -> str:
    """Return the canonical :class:`EndpointStatus` dictionary key."""
    return f"{service_name}::{method.upper()} {path}"


def _entity_from_path(path: str) -> str:
    """Extract a normalised entity name from the last meaningful path segment.

    Path-parameter tokens (``{id}``) and version prefixes (``v1``, ``v2``)
    are stripped.  Returns ``"item"`` as an ultimate fallback.

    Examples::

        _entity_from_path("/api/v1/users/{id}")  # → "users"
        _entity_from_path("/products")           # → "products"
        _entity_from_path("/v2/orders/{oid}/items/{iid}")  # → "items"
    """
    skip = {"api", "v1", "v2", "v3", "v4"}
    parts = [p for p in path.lower().split("/") if p and p not in skip]
    for part in reversed(parts):
        clean = re.sub(r"\{[^}]+\}", "", part).strip("_-")
        if clean:
            return clean
    return "item"


def _build_entity_schema(lower_path: str) -> dict[str, Any]:
    """Construct an OpenAPI-compatible ``object`` schema from path keywords.

    The schema is designed to work optimally with
    :func:`~boundary_analyzer.auto.traffic._generate_request_body`: field
    names such as ``"email"`` and ``"username"`` trigger smart value
    generation inside :func:`~boundary_analyzer.auto.traffic._generate_value`
    (e.g. ``"email"`` → RFC 5321 address format, ``"id"`` → UUID hex).

    Args:
        lower_path: Lower-cased URL path string used for keyword matching.

    Returns:
        An ``{"type": "object", "properties": {...}}`` dict ready to be
        passed as ``request_body`` to
        :func:`~boundary_analyzer.auto.traffic._send_request`.
    """
    # --- Authentication / session paths ---
    if any(kw in lower_path for kw in ("login", "signin", "authenticate", "session")):
        return {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "password": {"type": "string"},
            },
        }

    # --- Registration / onboarding paths ---
    if any(kw in lower_path for kw in ("register", "signup", "enroll", "onboard")):
        return {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "email": {"type": "string"},
                "password": {"type": "string"},
            },
        }

    # --- User / account / profile paths ---
    if any(kw in lower_path for kw in ("user", "account", "profile", "member", "customer", "contact")):
        return {
            "type": "object",
            "properties": {
                "username": {"type": "string"},
                "email": {"type": "string"},
                "password": {"type": "string"},
                "name": {"type": "string"},
            },
        }

    # --- Product / catalogue paths ---
    if any(kw in lower_path for kw in ("product", "catalog", "catalogue", "sku", "good", "inventory")):
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "price": {"type": "number"},
                "quantity": {"type": "integer"},
                "description": {"type": "string"},
            },
        }

    # --- Item / entity paths (generic e-commerce items) ---
    if "item" in lower_path:
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "string"},
                "quantity": {"type": "integer"},
            },
        }

    # --- Order / cart / checkout paths ---
    if any(kw in lower_path for kw in ("order", "purchase", "cart", "checkout", "basket")):
        return {
            "type": "object",
            "properties": {
                "quantity": {"type": "integer"},
                "status": {"type": "string"},
                "total": {"type": "number"},
            },
        }

    # --- Content / blogging paths ---
    if any(kw in lower_path for kw in ("post", "article", "blog", "comment", "message", "thread")):
        return {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "content": {"type": "string"},
                "author": {"type": "string"},
            },
        }

    # --- Explicit CRUD verb paths ---
    if any(kw in lower_path for kw in ("insert", "create", "add", "new")):
        return {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "value": {"type": "string"},
                "description": {"type": "string"},
            },
        }

    if any(kw in lower_path for kw in ("update", "edit", "modify", "patch")):
        return {
            "type": "object",
            "properties": {
                "id": {"type": "integer"},
                "name": {"type": "string"},
                "value": {"type": "string"},
            },
        }

    # --- Generic fallback ---
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "value": {"type": "string"},
            "description": {"type": "string"},
        },
    }


def _resolve_path_params_with_id(path: str, seeded_id: Any) -> str:
    """Replace the first ID-like path parameter token with a concrete *seeded_id*.

    Tokens matched: ``{id}``, ``{pk}``, ``{uid}``, ``{user_id}``,
    ``{item_id}``, ``{object_id}``.  Other parameter tokens are left intact
    so that :func:`~boundary_analyzer.auto.traffic._send_request` can fill
    them in with its own random-generation logic.

    Args:
        path: URL path template (e.g. ``"/users/{id}/orders/{oid}"``).
        seeded_id: A concrete entity ID harvested during the SEED phase.

    Returns:
        Path with the first ID-like token replaced by ``str(seeded_id)``.
    """
    _id_tokens = frozenset(("id", "pk", "uid", "user_id", "item_id", "object_id"))

    replaced = False

    def _replacer(m: re.Match) -> str:  # type: ignore[type-arg]
        nonlocal replaced
        if not replaced and m.group(1).lower() in _id_tokens:
            replaced = True
            return str(seeded_id)
        return m.group(0)

    return re.sub(r"\{(\w+)\}", _replacer, path)


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------


class OrderedTrafficEngine:
    """Execute HTTP traffic against microservices in CRUD-ordered phases.

    Traffic is sent in a fixed phase sequence (PROBE → SEED → READ →
    MUTATE → STRESS → CLEANUP) that mirrors a real CRUD lifecycle,
    ensuring database write operations are triggered reliably.

    Phases PROBE through CLEANUP execute **sequentially** within a single
    thread; only the STRESS phase uses a
    :class:`~concurrent.futures.ThreadPoolExecutor` with
    ``config.workers`` workers.

    Thread safety:
        All mutations to shared state (:attr:`_statuses`,
        :attr:`_seeded_ids`) are guarded by :attr:`_lock`.  Callback
        invocations occur outside the lock to avoid deadlocks.

    Args:
        services: Discovered services from
            :mod:`boundary_analyzer.auto.models`.
        endpoint_map: ``service_name → list[Endpoint]`` mapping.
        config: Traffic parameters — duration, worker count, request
            timeout, auth token, base URL, and sleep interval bounds.
        on_endpoint_update: Optional callback invoked **after every
            request** with a snapshot :class:`EndpointStatus`.  Called
            outside the internal lock.
        on_phase_change: Optional callback invoked at **each phase
            transition** with ``(phase_name, phase_index, total_phases)``.
        on_log: Optional callback for log entries.  Receives
            ``(message, level)`` where *level* is a :mod:`logging`-style
            string (``"debug"``, ``"info"``, ``"warning"``, ``"error"``).
    """

    def __init__(
        self,
        services: list[ServiceInfo],
        endpoint_map: dict[str, list[Endpoint]],
        config: TrafficConfig,
        on_endpoint_update: Callable[[EndpointStatus], None] | None = None,
        on_phase_change: Callable[[str, int, int], None] | None = None,
        on_log: Callable[[str, str], None] | None = None,
    ) -> None:
        self._services = services
        self._endpoint_map = endpoint_map
        self._config = config
        self._on_endpoint_update = on_endpoint_update
        self._on_phase_change = on_phase_change
        self._on_log = on_log

        # Shared mutable state — always access under _lock
        self._lock = threading.Lock()
        self._statuses: dict[str, EndpointStatus] = {}
        # IDs harvested during SEED phase; injected into MUTATE/CLEANUP paths
        self._seeded_ids: dict[str, list[Any]] = {}  # entity_name → [id, …]

        self._engine_start: float = 0.0

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run(self) -> EngineResult:
        """Execute all phases in order and return aggregate statistics.

        This method is **blocking**; it returns only after every phase has
        completed or its time budget has been exhausted.  Exceptions raised
        by individual requests are caught and recorded — the engine never
        propagates network errors to the caller.

        Returns:
            A fully populated :class:`EngineResult` containing per-phase
            breakdowns and final :class:`EndpointStatus` for every endpoint.
        """
        self._engine_start = time.monotonic()
        result = EngineResult()

        self._init_statuses()

        phase_durations = self._compute_phase_durations()
        total_phases = len(PHASE_ORDER)

        for phase_idx, phase in enumerate(PHASE_ORDER):
            phase_label = phase.label
            budget = phase_durations[phase]

            self._notify_phase_change(phase_label, phase_idx + 1, total_phases)
            self._log(
                f"[{phase_label}] Starting phase {phase_idx + 1}/{total_phases} — budget: {budget:.1f}s",
                "info",
            )

            phase_start = time.monotonic()
            phase_result = PhaseResult(phase=phase_label)

            if phase is Phase.PROBE:
                self._run_probe(phase_result)
            elif phase is Phase.SEED:
                self._run_seed(phase_result, budget)
            elif phase is Phase.READ:
                self._run_read(phase_result, budget)
            elif phase is Phase.MUTATE:
                self._run_mutate(phase_result, budget)
            elif phase is Phase.STRESS:
                self._run_stress(phase_result, budget)
            elif phase is Phase.CLEANUP:
                self._run_cleanup(phase_result)

            phase_result.duration_seconds = time.monotonic() - phase_start
            result.phases.append(phase_result)

            result.total_requests += phase_result.requests_sent
            result.successful_requests += phase_result.requests_ok
            result.failed_requests += phase_result.requests_sent - phase_result.requests_ok

            self._log(
                f"[{phase_label}] Finished — {phase_result.requests_ok}/{phase_result.requests_sent} OK in {phase_result.duration_seconds:.2f}s",
                "info",
            )

        # Finalise aggregate totals
        with self._lock:
            result.endpoint_statuses = dict(self._statuses)

        result.endpoints_tested = sum(1 for s in result.endpoint_statuses.values() if s.attempts > 0)
        result.endpoints_ok = sum(1 for s in result.endpoint_statuses.values() if s.successes > 0)
        result.duration_seconds = time.monotonic() - self._engine_start
        return result

    # ------------------------------------------------------------------
    # Phase runners
    # ------------------------------------------------------------------

    def _run_probe(self, phase_result: PhaseResult) -> None:
        """Send exactly one GET request per unique endpoint path.

        The goal is liveness detection — verifying that each service is
        reachable before heavier traffic begins.  A GET is used regardless
        of the endpoint's declared HTTP method.  Failed probes are logged
        but do **not** abort subsequent phases.
        """
        for service in self._services:
            base_url = self._base_url(service)
            endpoints = self._endpoint_map.get(service.name, [])
            seen_paths: set[str] = set()

            for ep in endpoints:
                if ep.path in seen_paths:
                    continue
                seen_paths.add(ep.path)

                # Synthesise a GET probe even if the declared method differs
                probe_ep = (
                    ep
                    if ep.method.upper() == "GET"
                    else Endpoint(
                        method="GET",
                        path=ep.path,
                        params=ep.params,
                        auth_required=ep.auth_required,
                    )
                )

                ok, _code = self._execute_endpoint(
                    service.name,
                    probe_ep,
                    Phase.PROBE.label,
                    base_url,
                    try_times=1,
                )
                phase_result.requests_sent += 1
                if ok:
                    phase_result.requests_ok += 1
                    phase_result.endpoints_reached.add(_ep_key(service.name, probe_ep.method, probe_ep.path))

    def _run_seed(self, phase_result: PhaseResult, budget: float) -> None:
        """Execute every POST endpoint sequentially to seed database state.

        Each POST uses a semantically coherent schema (via
        :meth:`_build_payload`) so that validation passes and real rows are
        created.  On success, :meth:`_harvest_seed_ids` issues a follow-up
        GET to collect entity IDs for the MUTATE phase.
        """
        deadline = time.monotonic() + budget

        for service in self._services:
            if time.monotonic() >= deadline:
                self._log(
                    f"[{Phase.SEED.label}] Budget exhausted — skipping remaining services",
                    "warning",
                )
                break

            base_url = self._base_url(service)
            post_eps = [ep for ep in self._endpoint_map.get(service.name, []) if ep.method.upper() == "POST"]

            for ep in post_eps:
                if time.monotonic() >= deadline:
                    break

                ok, _code = self._execute_endpoint(service.name, ep, Phase.SEED.label, base_url, try_times=2)
                phase_result.requests_sent += 1
                if ok:
                    phase_result.requests_ok += 1
                    phase_result.endpoints_reached.add(_ep_key(service.name, ep.method, ep.path))
                    self._harvest_seed_ids(ep, base_url)

                self._sleep_interval()

    def _run_read(self, phase_result: PhaseResult, budget: float) -> None:
        """Execute every GET endpoint sequentially to verify seeded data.

        Triggers SELECT operations and confirms that data created in the
        SEED phase is queryable.  Skips further services if the time budget
        runs out.
        """
        deadline = time.monotonic() + budget

        for service in self._services:
            if time.monotonic() >= deadline:
                self._log(
                    f"[{Phase.READ.label}] Budget exhausted — skipping remaining services",
                    "warning",
                )
                break

            base_url = self._base_url(service)
            get_eps = [ep for ep in self._endpoint_map.get(service.name, []) if ep.method.upper() == "GET"]

            for ep in get_eps:
                if time.monotonic() >= deadline:
                    break

                ok, _code = self._execute_endpoint(service.name, ep, Phase.READ.label, base_url, try_times=1)
                phase_result.requests_sent += 1
                if ok:
                    phase_result.requests_ok += 1
                    phase_result.endpoints_reached.add(_ep_key(service.name, ep.method, ep.path))

                self._sleep_interval()

    def _run_mutate(self, phase_result: PhaseResult, budget: float) -> None:
        """Execute every PUT / PATCH endpoint, injecting real seeded IDs.

        Before each request, :meth:`_inject_seeded_id` replaces ID-like
        path tokens (``{id}``, ``{pk}``, …) with an ID harvested during
        the SEED phase.  This ensures UPDATE statements operate on existing
        rows rather than phantom IDs.  Falls back to random generation when
        no seeded IDs are available.
        """
        deadline = time.monotonic() + budget

        for service in self._services:
            if time.monotonic() >= deadline:
                self._log(
                    f"[{Phase.MUTATE.label}] Budget exhausted — skipping remaining services",
                    "warning",
                )
                break

            base_url = self._base_url(service)
            mutate_eps = [ep for ep in self._endpoint_map.get(service.name, []) if ep.method.upper() in ("PUT", "PATCH")]

            for ep in mutate_eps:
                if time.monotonic() >= deadline:
                    break

                # Resolve path params with a harvested ID when possible
                effective_ep = self._inject_seeded_id(ep)

                ok, _code = self._execute_endpoint(
                    service.name,
                    effective_ep,
                    Phase.MUTATE.label,
                    base_url,
                    try_times=2,
                )
                # Always track using the original (template) path for consistency
                phase_result.requests_sent += 1
                if ok:
                    phase_result.requests_ok += 1
                    phase_result.endpoints_reached.add(_ep_key(service.name, ep.method, ep.path))

                self._sleep_interval()

    def _run_stress(self, phase_result: PhaseResult, budget: float) -> None:
        """Fire all endpoints concurrently at full frequency with weighted selection.

        Uses :class:`~concurrent.futures.ThreadPoolExecutor` with
        ``config.workers`` workers.  Endpoint selection is weighted:

        ============  ======
        Method        Weight
        ============  ======
        GET           60 %
        POST          25 %
        PUT / PATCH   10 %
        DELETE         5 %
        ============  ======

        Futures are drained in a non-blocking poll loop to avoid unbounded
        memory growth, with a final drain pass after the deadline.
        """
        deadline = time.monotonic() + budget
        stress_lock = threading.Lock()

        # Build per-method buckets: (service_name, endpoint, base_url)
        # Each entry is a 3-tuple; we use a plain list to avoid TypeAlias issues.
        buckets: dict[str, list] = {m: [] for m in ("GET", "POST", "PUT", "PATCH", "DELETE")}
        for service in self._services:
            base_url = self._base_url(service)
            for ep in self._endpoint_map.get(service.name, []):
                m = ep.method.upper()
                if m in buckets:
                    buckets[m].append((service.name, ep, base_url))

        # Merge PATCH into PUT for weighted selection
        combined_put: list = buckets["PUT"] + buckets["PATCH"]
        all_eps: list = [item for pool in buckets.values() for item in pool]

        def _pick() -> tuple[str, Endpoint, str] | None:
            """Select an endpoint according to the configured weight table."""
            roll = random.random()
            cumulative = 0.0
            for method, weight in _STRESS_WEIGHTS.items():
                cumulative += weight
                if roll < cumulative:
                    pool = combined_put if method == "PUT" else buckets.get(method, [])
                    if pool:
                        return random.choice(pool)
            return random.choice(all_eps) if all_eps else None

        def _stress_worker() -> tuple[bool, str]:
            pick = _pick()
            if pick is None:
                return False, "no-endpoints"
            svc_name, ep, base_url = pick
            ok, _code = self._execute_endpoint(svc_name, ep, Phase.STRESS.label, base_url, try_times=1)
            return ok, _ep_key(svc_name, ep.method, ep.path)

        pending: list = []

        with ThreadPoolExecutor(max_workers=self._config.workers) as executor:
            while time.monotonic() < deadline:
                pending.append(executor.submit(_stress_worker))

                # Non-blocking drain of completed futures
                still_pending: list = []
                for fut in pending:
                    if fut.done():
                        try:
                            ok, ep_key = fut.result()
                            with stress_lock:
                                phase_result.requests_sent += 1
                                if ok:
                                    phase_result.requests_ok += 1
                                    phase_result.endpoints_reached.add(ep_key)
                        except Exception as exc:
                            logger.debug("Stress future failed: %s", exc)
                            with stress_lock:
                                phase_result.requests_sent += 1
                    else:
                        still_pending.append(fut)
                pending = still_pending

                self._sleep_interval()

            # Final blocking drain after deadline
            if pending:
                drain_timeout = max(self._config.timeout * 2, 10)
                try:
                    for fut in as_completed(pending, timeout=drain_timeout):
                        try:
                            ok, ep_key = fut.result()
                            with stress_lock:
                                phase_result.requests_sent += 1
                                if ok:
                                    phase_result.requests_ok += 1
                                    phase_result.endpoints_reached.add(ep_key)
                        except Exception as exc:
                            logger.debug("Stress drain future failed: %s", exc)
                            with stress_lock:
                                phase_result.requests_sent += 1
                except TimeoutError:
                    logger.debug("Stress drain timed out with %d pending futures", len(pending))

    def _run_cleanup(self, phase_result: PhaseResult) -> None:
        """Execute every DELETE endpoint sequentially to remove seeded data.

        Runs without a time cap — every DELETE endpoint is always attempted
        once.  Path parameters are resolved with harvested seeded IDs where
        possible, so deletions target the same rows created during SEED.
        """
        for service in self._services:
            base_url = self._base_url(service)
            delete_eps = [ep for ep in self._endpoint_map.get(service.name, []) if ep.method.upper() == "DELETE"]

            for ep in delete_eps:
                effective_ep = self._inject_seeded_id(ep)
                ok, _code = self._execute_endpoint(
                    service.name,
                    effective_ep,
                    Phase.CLEANUP.label,
                    base_url,
                    try_times=1,
                )
                phase_result.requests_sent += 1
                if ok:
                    phase_result.requests_ok += 1
                    phase_result.endpoints_reached.add(_ep_key(service.name, ep.method, ep.path))

                self._sleep_interval()

    # ------------------------------------------------------------------
    # Core execution method
    # ------------------------------------------------------------------

    def _execute_endpoint(
        self,
        service_name: str,
        endpoint: Endpoint,
        phase_label: str,
        base_url: str,
        try_times: int = 1,
    ) -> tuple[bool, int]:
        """Send an HTTP request to *endpoint* and maintain tracking state.

        This is the single chokepoint for all network I/O.  It:

        1. Calls :meth:`_build_payload` to produce a phase-aware
           ``request_body`` schema for mutating methods.
        2. Calls :func:`~boundary_analyzer.auto.traffic._send_request` to
           perform the actual HTTP call (with retry logic on failure).
        3. Pre-computes an *indicative* body for observability using
           :func:`~boundary_analyzer.auto.traffic._generate_request_body`
           and :func:`~boundary_analyzer.auto.traffic._guess_body_from_path`
           so callers can inspect :attr:`EndpointStatus.last_payload`.
        4. Updates :class:`EndpointStatus` thread-safely.
        5. Invokes the ``on_endpoint_update`` and ``on_log`` callbacks.

        Args:
            service_name: Name of the owning microservice.
            endpoint: Target endpoint descriptor.  The path may already
                have ID tokens resolved (e.g. from :meth:`_inject_seeded_id`).
            phase_label: Current execution phase label for status/log entries.
            base_url: Resolved base URL of the form ``http://host:port``.
            try_times: Maximum number of attempts before giving up.

        Returns:
            ``(success, http_status_code)`` where *success* is ``True`` for
            any non-5xx response, and *http_status_code* is the last observed
            status code (``0`` on network error).
        """
        # Use original template path as the stable tracking key even if
        # the endpoint already has path params resolved.
        raw_path = endpoint.path
        key = _ep_key(service_name, endpoint.method, raw_path)

        # --- Build phase-aware request body schema ---
        smart_schema = self._build_payload(endpoint, phase_label)

        effective_ep = Endpoint(
            method=endpoint.method,
            path=endpoint.path,
            params=endpoint.params,
            request_body=(smart_schema if smart_schema is not None else endpoint.request_body),
            auth_required=endpoint.auth_required,
            is_graphql=endpoint.is_graphql,
            graphql_field=endpoint.graphql_field,
            graphql_args=endpoint.graphql_args,
        )

        # --- Pre-compute indicative payload for observability ---
        # _generate_request_body and _guess_body_from_path produce a concrete
        # example body from the schema/path.  The bytes actually sent by
        # _send_request will have freshly randomised field values, so this is
        # approximate — useful for dashboards and debugging, not exact replay.
        recorded_payload: dict[str, Any] = {}
        if endpoint.method.upper() in ("POST", "PUT", "PATCH"):
            if smart_schema is not None:
                body_sample = _generate_request_body(smart_schema, endpoint_path=endpoint.path)
                if isinstance(body_sample, dict):
                    recorded_payload = body_sample
            else:
                recorded_payload = _guess_body_from_path(endpoint.path)

        # --- Attempt loop with simple exponential back-off ---
        success = False
        status_code = 0
        last_err = ""
        elapsed_ms = 0.0

        for attempt in range(max(1, try_times)):
            t0 = time.monotonic()
            try:
                ok, code, _url = _send_request(
                    method=effective_ep.method,
                    base_url=base_url,
                    path=effective_ep.path,
                    params=effective_ep.params,
                    request_body=effective_ep.request_body,
                    config=self._config,
                    endpoint=effective_ep,
                )
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                status_code = code

                if ok:
                    success = True
                    last_err = ""
                    break

                last_err = f"HTTP {code}"

            except Exception as exc:
                elapsed_ms = (time.monotonic() - t0) * 1000.0
                last_err = str(exc)
                logger.debug(
                    "Request exception [%s %s]: %s",
                    endpoint.method,
                    endpoint.path,
                    exc,
                )

            if attempt < try_times - 1:
                time.sleep(0.15 * (2**attempt))  # 150 ms, 300 ms, …

        # --- Thread-safe status update ---
        with self._lock:
            st = self._statuses.get(key)
            if st is None:
                st = EndpointStatus(
                    service_name=service_name,
                    method=endpoint.method,
                    path=raw_path,
                )
                self._statuses[key] = st

            st.attempts += 1
            st.http_status = status_code
            st.response_ms = elapsed_ms
            st.last_error = last_err

            if recorded_payload:
                st.last_payload = recorded_payload

            if success:
                st.successes += 1
                st.phase = "success"
            elif last_err:
                st.phase = "failed"
            else:
                st.phase = phase_label.lower()

            # Snapshot *outside* the lock for the callback
            snapshot = EndpointStatus(
                service_name=st.service_name,
                method=st.method,
                path=st.path,
                phase=st.phase,
                http_status=st.http_status,
                attempts=st.attempts,
                successes=st.successes,
                db_ops_triggered=st.db_ops_triggered,
                response_ms=st.response_ms,
                last_error=st.last_error,
                last_payload=dict(st.last_payload),
            )

        # Callbacks run outside the lock
        self._notify_endpoint_update(snapshot)

        log_level = "info" if success else "warning"
        self._log(
            f"[{phase_label}] {endpoint.method} {raw_path} → HTTP {status_code} ({elapsed_ms:.0f} ms) " + ("OK" if success else f"FAIL: {last_err}"),
            log_level,
        )

        return success, status_code

    # ------------------------------------------------------------------
    # Payload / schema building
    # ------------------------------------------------------------------

    def _build_payload(
        self,
        endpoint: Endpoint,
        phase_label: str,
    ) -> dict[str, Any] | None:
        """Return an OpenAPI-compatible ``request_body`` schema for mutating methods.

        The returned schema is passed directly to
        :func:`~boundary_analyzer.auto.traffic._send_request` as the
        ``request_body`` argument.  Inside ``_send_request``,
        :func:`~boundary_analyzer.auto.traffic._generate_request_body`
        turns the schema into a concrete JSON body using
        :func:`~boundary_analyzer.auto.traffic._generate_value`, which applies
        name-based heuristics (e.g. ``"email"`` → valid address format).

        Strategy:

        * **SEED phase** — build an entity-aware schema from path keywords.
          If the endpoint already carries a valid ``object`` schema, that
          schema is preferred as it contains the ground-truth field names.
        * **MUTATE phase** — same as SEED, but augmented with an ``"id"``
          field so that update requests carry a primary-key reference.  Real
          IDs are injected into the *path* by :meth:`_inject_seeded_id`
          rather than into the body schema.
        * **STRESS / PROBE / READ / CLEANUP** — return ``None``.
          :func:`~boundary_analyzer.auto.traffic._send_request` will call
          :func:`~boundary_analyzer.auto.traffic._guess_body_from_path`
          as its own fallback for any mutating methods encountered.
        * Non-mutating methods (GET, DELETE, HEAD, OPTIONS) — always ``None``.

        Args:
            endpoint: The endpoint for which a payload schema is needed.
            phase_label: Upper-case label of the current phase.

        Returns:
            A ``{"type": "object", "properties": {...}}`` schema dict, or
            ``None`` when the caller should delegate entirely to
            :func:`~boundary_analyzer.auto.traffic._send_request`.
        """
        if endpoint.method.upper() not in ("POST", "PUT", "PATCH"):
            return None

        lower_path = endpoint.path.lower()

        # --- SEED: entity-coherent schema ---
        if phase_label == Phase.SEED.label:
            # Prefer the endpoint's own schema if it is a valid object schema
            if endpoint.request_body and endpoint.request_body.get("type") == "object" and endpoint.request_body.get("properties"):
                return endpoint.request_body
            return _build_entity_schema(lower_path)

        # --- MUTATE: same as SEED but with an explicit id/pk field ---
        if phase_label == Phase.MUTATE.label:
            if endpoint.request_body and endpoint.request_body.get("type") == "object" and endpoint.request_body.get("properties"):
                base = dict(endpoint.request_body)
                props: dict[str, Any] = dict(base.get("properties", {}))
            else:
                base = _build_entity_schema(lower_path)
                props = dict(base.get("properties", {}))

            # Ensure at least one primary-key field exists in the update body
            if "id" not in props and "pk" not in props:
                props["id"] = {"type": "integer"}

            return {**base, "properties": props}

        # For STRESS, PROBE, READ, CLEANUP: let _send_request decide
        return None

    # ------------------------------------------------------------------
    # Seeded-ID management
    # ------------------------------------------------------------------

    def _harvest_seed_ids(self, endpoint: Endpoint, base_url: str) -> None:
        """Issue a best-effort GET to harvest entity IDs after a successful POST.

        Strips trailing ``/{param}`` tokens from the endpoint path to derive
        a collection URL (e.g. ``/users/{id}`` → ``/users``), then GETs it.
        IDs found in the response are stored in :attr:`_seeded_ids` for later
        use in :meth:`_inject_seeded_id`.

        This method is entirely non-blocking for the engine; all exceptions
        are suppressed.

        Args:
            endpoint: The POST endpoint that was just called successfully.
            base_url: Service base URL (``http://host:port``).
        """
        try:
            # Derive collection URL by removing trailing path-param segments
            collection_path = re.sub(r"/\{[^}]+\}(?:/.*)?$", "", endpoint.path)
            collection_path = collection_path or "/"

            url = urljoin(base_url, collection_path)
            headers: dict[str, str] = {"Accept": "application/json"}
            if self._config.auth_token:
                headers["Authorization"] = f"Bearer {self._config.auth_token}"

            resp = requests.get(url, headers=headers, timeout=self._config.timeout)
            if resp.status_code != 200:
                return

            data = resp.json()
            entity = _entity_from_path(endpoint.path)
            ids: list[Any] = []

            def _extract(obj: Any) -> None:
                """Recursively pull id/pk values from dicts or lists."""
                if isinstance(obj, dict):
                    for id_key in ("id", "pk", "uid", "_id", "objectId"):
                        if id_key in obj:
                            ids.append(obj[id_key])
                            return
                elif isinstance(obj, list):
                    for item in obj[:20]:  # cap traversal at 20 items
                        _extract(item)

            if isinstance(data, list):
                _extract(data)
            elif isinstance(data, dict):
                # Unwrap common API envelope keys before extracting IDs
                for wrap_key in ("data", "items", "results", "content", "records"):
                    if isinstance(data.get(wrap_key), list):
                        _extract(data[wrap_key])
                        break
                else:
                    _extract(data)

            if ids:
                with self._lock:
                    bucket = self._seeded_ids.setdefault(entity, [])
                    for eid in ids:
                        if eid not in bucket:
                            bucket.append(eid)

        except Exception as exc:
            logger.debug("ID harvest skipped for %s: %s", endpoint.path, exc)

    def _pick_seeded_id(self, path: str) -> Any | None:
        """Return a harvested entity ID relevant to *path*, or ``None``.

        First looks for an entity-specific bucket matching the path's final
        segment; falls back to any available ID across all entities.

        Args:
            path: The URL path whose entity type guides bucket selection.

        Returns:
            A seeded entity ID value (str or int), or ``None``.
        """
        with self._lock:
            if not self._seeded_ids:
                return None
            entity = _entity_from_path(path)
            ids = self._seeded_ids.get(entity, [])
            if ids:
                return random.choice(ids)
            # Cross-entity fallback for paths without a specific match
            all_ids = [eid for bucket in self._seeded_ids.values() for eid in bucket]
            return random.choice(all_ids) if all_ids else None

    def _inject_seeded_id(self, endpoint: Endpoint) -> Endpoint:
        """Return an endpoint with ID-like path parameters resolved from harvested data.

        Calls :func:`_resolve_path_params_with_id` with a seeded ID
        obtained via :meth:`_pick_seeded_id`.  If no seeded ID is
        available, or the path has no ID-like tokens, the original
        endpoint is returned unchanged and
        :func:`~boundary_analyzer.auto.traffic._send_request` will
        generate random path parameters as usual.

        Args:
            endpoint: The original endpoint with a path template.

        Returns:
            A new :class:`~boundary_analyzer.auto.models.Endpoint` with
            the resolved path, or the original ``endpoint`` if no
            substitution was made.
        """
        seeded_id = self._pick_seeded_id(endpoint.path)
        if seeded_id is None:
            return endpoint

        resolved_path = _resolve_path_params_with_id(endpoint.path, seeded_id)
        if resolved_path == endpoint.path:
            return endpoint

        return Endpoint(
            method=endpoint.method,
            path=resolved_path,
            params=endpoint.params,
            request_body=endpoint.request_body,
            auth_required=endpoint.auth_required,
            is_graphql=endpoint.is_graphql,
            graphql_field=endpoint.graphql_field,
            graphql_args=endpoint.graphql_args,
        )

    # ------------------------------------------------------------------
    # Utility / initialisation
    # ------------------------------------------------------------------

    def _init_statuses(self) -> None:
        """Pre-populate :attr:`_statuses` for every known endpoint.

        This is called once at the start of :meth:`run` so that
        ``endpoint_statuses`` in the final :class:`EngineResult` reflects
        every endpoint even if it was never reached (``phase="pending"``).
        """
        for service in self._services:
            for ep in self._endpoint_map.get(service.name, []):
                key = _ep_key(service.name, ep.method, ep.path)
                if key not in self._statuses:
                    self._statuses[key] = EndpointStatus(
                        service_name=service.name,
                        method=ep.method,
                        path=ep.path,
                        phase="pending",
                    )

    def _base_url(self, service: ServiceInfo) -> str:
        """Build the base URL for *service* using its configured port.

        Args:
            service: Service whose port should be appended to the base URL.

        Returns:
            A URL string such as ``"http://127.0.0.1:8080"`` or, when the
            service has no port, just ``config.base_url``.
        """
        if service.port:
            return f"{self._config.base_url}:{service.port}"
        return self._config.base_url

    def _compute_phase_durations(self) -> dict[Phase, float]:
        """Allocate the total :attr:`~TrafficConfig.duration` across phases.

        Budget allocation formula::

            PROBE   = min(5 s,  5 % of total)
            remaining = total − PROBE
            SEED    = 20 % of remaining
            READ    = 30 % of remaining
            MUTATE  = 15 % of remaining
            STRESS  = 25 % of remaining
            CLEANUP = 10 % of remaining

        The CLEANUP phase receives only 10 % of the remaining budget but
        :meth:`_run_cleanup` does not enforce a deadline; every DELETE
        endpoint is always attempted.  The allocation is intended to
        communicate priority weighting rather than hard cut-offs.

        Returns:
            Mapping from :class:`Phase` to its allocated seconds
            (``float``).
        """
        total = float(self._config.duration)
        probe_time = min(5.0, total * 0.05)
        remaining = total - probe_time

        return {
            Phase.PROBE: probe_time,
            Phase.SEED: remaining * 0.20,
            Phase.READ: remaining * 0.30,
            Phase.MUTATE: remaining * 0.15,
            Phase.STRESS: remaining * 0.25,
            Phase.CLEANUP: remaining * 0.10,
        }

    def _sleep_interval(self) -> None:
        """Sleep for a random duration within the configured interval bounds."""
        time.sleep(random.uniform(self._config.interval_min, self._config.interval_max))

    # ------------------------------------------------------------------
    # Callback helpers
    # ------------------------------------------------------------------

    def _notify_endpoint_update(self, status: EndpointStatus) -> None:
        """Fire the ``on_endpoint_update`` callback with a status snapshot.

        Any exception raised by the callback is caught and logged at
        ``DEBUG`` level so that a misbehaving callback never crashes the
        engine.

        Args:
            status: An immutable snapshot of the endpoint's current state.
        """
        if self._on_endpoint_update is not None:
            try:
                self._on_endpoint_update(status)
            except Exception as exc:
                logger.debug("on_endpoint_update callback raised: %s", exc)

    def _notify_phase_change(self, phase_name: str, phase_num: int, total: int) -> None:
        """Fire the ``on_phase_change`` callback.

        Args:
            phase_name: Upper-case phase label (e.g. ``"SEED"``).
            phase_num: 1-based index of the current phase.
            total: Total number of phases.
        """
        if self._on_phase_change is not None:
            try:
                self._on_phase_change(phase_name, phase_num, total)
            except Exception as exc:
                logger.debug("on_phase_change callback raised: %s", exc)

    def _log(self, message: str, level: str = "info") -> None:
        """Emit a log entry via the standard :mod:`logging` logger and ``on_log``.

        Args:
            message: Human-readable log message.
            level: A :mod:`logging`-compatible level name such as ``"info"``
                or ``"warning"``.  Invalid names fall back to ``logger.info``.
        """
        log_fn = getattr(logger, level, logger.info)
        log_fn("%s", message)
        if self._on_log is not None:
            try:
                self._on_log(message, level)
            except Exception as exc:
                logger.debug("on_log callback raised: %s", exc)
