"""Universal log file ingestion module for the MBA (Microservice Boundary Analyzer) tool.

This module can parse **any** type of log file a user provides and convert it
into the internal ``spans.csv`` DataFrame format consumed by the SCOM pipeline.

Supported formats
-----------------
+------------------+----------------+---------------------------------------------------+
| Format           | ID             | Source                                            |
+==================+================+===================================================+
| Jaeger JSON      | ``jaeger``     | Jaeger UI export / Jaeger HTTP API                |
+------------------+----------------+---------------------------------------------------+
| Zipkin JSON      | ``zipkin``     | Zipkin v2 REST API JSON                           |
+------------------+----------------+---------------------------------------------------+
| OTLP JSON        | ``otlp``       | OpenTelemetry Protocol JSON export                |
+------------------+----------------+---------------------------------------------------+
| Locust CSV       | ``locust``     | Locust load-testing request statistics CSV        |
+------------------+----------------+---------------------------------------------------+
| nginx / Apache   | ``nginx``      | Combined Access Log Format                        |
+------------------+----------------+---------------------------------------------------+
| W3C / IIS        | ``w3c``        | W3C Extended Log Format (IIS)                     |
+------------------+----------------+---------------------------------------------------+
| Generic SQL      | ``generic_sql``| Django / Flask / Spring / Rails app logs w/ SQL   |
+------------------+----------------+---------------------------------------------------+
| JSON Lines       | ``json_lines`` | One JSON object per log line (structured logging) |
+------------------+----------------+---------------------------------------------------+

Unified spans.csv schema (8 columns)
-------------------------------------
``trace_id``       str  — unique trace identifier (synthetic UUID if absent in source)
``span_id``        str  — unique span identifier
``parent_span_id`` str  — parent span ID (``None`` for root spans)
``service_name``   str  — microservice name
``operation_name`` str  — operation name (e.g. ``"GET /orders"`` or ``"SELECT …"``)
``start_time``     int  — unix timestamp in **microseconds**
``duration``       int  — duration in **microseconds**
``tags``           str  — JSON string of ``list[{"key": str, "value": str}]``

Public API
----------
:func:`detect_format`   — inspect a file and return ``(format_name, confidence)``
:func:`ingest_log_file` — auto-detect + parse, return an :class:`IngestResult`
"""

from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, cast

import pandas as pd

from boundary_analyzer.detection.db_table_extractor import _extract_tables_from_sql

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Schema constants
# ---------------------------------------------------------------------------

#: Canonical column order for the unified spans DataFrame.
SPANS_COLUMNS: list[str] = [
    "trace_id",
    "span_id",
    "parent_span_id",
    "service_name",
    "operation_name",
    "start_time",
    "duration",
    "tags",
]

# ---------------------------------------------------------------------------
# Public result dataclass
# ---------------------------------------------------------------------------


@dataclass
class IngestResult:
    """Result returned by :func:`ingest_log_file`.

    Attributes:
        format_detected:       Format identifier string that was actually used
                               (e.g. ``"jaeger"``, ``"nginx"``).
        format_confidence:     Detector confidence in the range ``[0.0, 1.0]``.
                               A value of 1.0 means the format was identified
                               unambiguously; lower values indicate heuristic
                               guesses.
        spans_df:              Unified spans ``DataFrame`` with the 8-column
                               ``spans.csv`` schema.  May be empty if the file
                               contained no parseable records.
        warnings:              List of non-fatal issues encountered during
                               detection or parsing (e.g. skipped lines, missing
                               fields, fallback format used).
        stats:                 Dictionary of summary statistics with keys:
                               ``total_spans``, ``http_spans``, ``db_spans``,
                               ``services`` (list), ``unique_traces``.
        has_db_info:           ``True`` if at least one DB span was extracted.
        has_trace_correlation: ``True`` if HTTP↔DB parent/child links were
                               established (i.e. some DB span has a non-null
                               ``parent_span_id`` pointing to an HTTP span).
        service_name_used:     The service name that was ultimately stored in the
                               ``service_name`` column (either explicitly provided
                               by the caller or inferred from the file).
    """

    format_detected: str
    format_confidence: float
    spans_df: pd.DataFrame
    warnings: list[str] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)
    has_db_info: bool = False
    has_trace_correlation: bool = False
    service_name_used: str = "unknown"


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _new_uuid() -> str:
    """Return a freshly generated random UUID as a 32-character hex string."""
    return uuid.uuid4().hex


def _make_tag(key: str, value: str) -> dict[str, str]:
    """Construct a single OpenTelemetry-style ``{key, value}`` tag dict."""
    return {"key": key, "value": str(value)}


def _tags_to_json(tags: list[dict[str, str]]) -> str:
    """Serialise a list of tag dicts to a compact JSON string."""
    return json.dumps(tags, separators=(",", ":"))


def _empty_df() -> pd.DataFrame:
    """Return an empty DataFrame with the canonical 8-column schema."""
    return pd.DataFrame(columns=SPANS_COLUMNS)


def _ensure_schema(df: pd.DataFrame) -> pd.DataFrame:
    """Guarantee that *df* has exactly the 8 required columns in the right order.

    Missing columns are added as ``None``; extra columns are dropped.
    """
    for col in SPANS_COLUMNS:
        if col not in df.columns:
            df = df.copy()
            df[col] = None
    return cast(pd.DataFrame, df[SPANS_COLUMNS].copy())  # type: ignore[return-value]


def _compute_stats(df: pd.DataFrame) -> dict[str, Any]:
    """Compute summary statistics over the unified spans DataFrame.

    Returns a dict with keys: ``total_spans``, ``http_spans``, ``db_spans``,
    ``services``, ``unique_traces``.
    """
    if df.empty:
        return {
            "total_spans": 0,
            "http_spans": 0,
            "db_spans": 0,
            "services": [],
            "unique_traces": 0,
        }

    tags_series = df["tags"].fillna("[]").astype(str)
    http_mask = tags_series.str.contains("http.method", na=False, regex=False)
    db_mask = tags_series.str.contains("db.system", na=False, regex=False) | tags_series.str.contains("db.statement", na=False, regex=False)

    return {
        "total_spans": len(df),
        "http_spans": int(http_mask.sum().item()),  # type: ignore[union-attr]
        "db_spans": int(db_mask.sum().item()),  # type: ignore[union-attr]
        "services": sorted(df["service_name"].dropna().unique().tolist()),
        "unique_traces": int(df["trace_id"].nunique()),
    }


def _read_file_with_fallback(file_path: Path, encoding: str = "utf-8") -> str:
    """Read *file_path* as text, falling back to ``latin-1`` then ``cp1252``.

    Args:
        file_path: Path of the file to read.
        encoding:  Preferred encoding (tried first).

    Returns:
        The full text content of the file.

    Raises:
        ValueError: If the file cannot be decoded with any of the tried encodings.
    """
    for enc in [encoding, "latin-1", "cp1252"]:
        try:
            return file_path.read_text(encoding=enc)
        except (UnicodeDecodeError, LookupError):
            continue
    raise ValueError(f"Cannot decode '{file_path}' with encodings: {encoding}, latin-1, cp1252. The file may be binary or use an unsupported character set.")


def _parse_iso_timestamp_to_us(ts_str: str) -> int:
    """Parse an ISO 8601 (or common variant) timestamp string to microseconds since epoch.

    Tries multiple format strings in order.  Returns ``0`` if none matches.

    Args:
        ts_str: Raw timestamp string, e.g. ``"2026-06-19T15:23:01.123Z"``.

    Returns:
        Integer microseconds since the Unix epoch, or ``0`` on failure.
    """
    ts_str = ts_str.strip().rstrip("Z")
    # Strip fractional timezone offset from ISO 8601 (+00:00 style)
    ts_str = re.sub(r"[+-]\d{2}:\d{2}$", "", ts_str).strip()

    formats = [
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1_000_000)
        except ValueError:
            continue
    return 0


def _parse_common_log_timestamp(ts_str: str) -> int:
    """Parse a Combined Log Format timestamp like ``'19/Jun/2026:15:23:01 +0000'``.

    The timezone offset is stripped before parsing; the result is treated as UTC.

    Args:
        ts_str: Raw timestamp string from a log line (without surrounding brackets).

    Returns:
        Integer microseconds since the Unix epoch, or ``0`` on failure.
    """
    ts_str = ts_str.strip()
    # Remove timezone offset (e.g. " +0000" or " -0500")
    ts_str = re.sub(r"\s*[+-]\d{4}$", "", ts_str).strip()
    formats = [
        "%d/%b/%Y:%H:%M:%S",
        "%d/%b/%Y %H:%M:%S",
        "%d/%b/%Y:%H:%M",
    ]
    for fmt in formats:
        try:
            dt = datetime.strptime(ts_str, fmt).replace(tzinfo=timezone.utc)
            return int(dt.timestamp() * 1_000_000)
        except ValueError:
            continue
    return 0


def _infer_service_from_path(file_path: Path) -> str:
    """Infer a service name from the log filename by stripping common suffixes.

    For example, ``"orders-access.log"`` becomes ``"orders"``.

    Args:
        file_path: Path of the log file.

    Returns:
        A lower-cased service name string, or ``"unknown-service"`` if the stem
        is empty after stripping.
    """
    stem = file_path.stem.lower()
    for suffix in (
        "-access",
        "_access",
        "-error",
        "_error",
        "-request",
        "_request",
        "-app",
        "_app",
        ".log",
    ):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
    return stem.strip("-_") or "unknown-service"


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

#: Maximum bytes to read for content-based sniffing.
_SNIFF_BYTES: int = 8192

# Pre-compiled patterns used during detection (kept module-level for speed).
_DETECT_NGINX_RE = re.compile(
    r"\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}"
    r"\s+-\s+\S+\s+"
    r"\[\d{2}/\w+/\d{4}:\d{2}:\d{2}:\d{2}",
)
_DETECT_HTTP_RE = re.compile(
    r"\b(GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+(/\S+)",
    re.IGNORECASE,
)
_DETECT_SQL_RE = re.compile(
    r"\b(SELECT|INSERT|UPDATE|DELETE|FROM)\s+",
    re.IGNORECASE,
)


def detect_format(file_path: Path, encoding: str = "utf-8") -> tuple[str, float]:
    """Detect the log format of *file_path* using extension and content heuristics.

    Detection priority:
        1. File extension (``".json"``, ``".csv"``) combined with content sniff.
        2. Content-based heuristics on the first 8 KB.
        3. Fallback: ``"generic_sql"`` (most permissive parser).

    Args:
        file_path: Path to the log file.
        encoding:  Encoding to use when reading the sniff buffer.

    Returns:
        A ``(format_name, confidence)`` tuple where ``confidence`` is in
        ``[0.0, 1.0]``.

    Format names returned:
        ``"jaeger"``, ``"zipkin"``, ``"otlp"``, ``"locust"``, ``"nginx"``,
        ``"w3c"``, ``"generic_sql"``, ``"json_lines"``
    """
    ext = file_path.suffix.lower()

    try:
        raw = _read_file_with_fallback(file_path, encoding)
    except Exception:
        return "generic_sql", 0.1

    sniff = raw[:_SNIFF_BYTES]
    sniff_stripped = sniff.strip()

    # ------------------------------------------------------------------
    # 1. JSON extension → try to parse and inspect top-level structure
    # ------------------------------------------------------------------
    if ext == ".json":
        # Try to parse just the sniff portion; if truncated, fall back to
        # string-based heuristics.
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            data = None

        if data is not None:
            if isinstance(data, dict):
                if "resourceSpans" in data:
                    return "otlp", 1.0
                root = data.get("jaeger_response", data)
                traces = root.get("data", [])
                if traces and isinstance(traces[0], dict) and "traceID" in traces[0]:
                    return "jaeger", 1.0
                # Loose check: any nested traceID + spans
                if '"traceID"' in sniff and '"spans"' in sniff:
                    return "jaeger", 0.9
            if isinstance(data, list) and data and isinstance(data[0], dict):
                first = data[0]
                if "traceId" in first and "localEndpoint" in first:
                    return "zipkin", 1.0
                if "traceID" in first and "spans" in first:
                    return "jaeger", 0.9

    # ------------------------------------------------------------------
    # 2. CSV extension
    # ------------------------------------------------------------------
    if ext == ".csv":
        if "Request Count" in sniff and "Failure Count" in sniff:
            return "locust", 1.0

    # ------------------------------------------------------------------
    # 3. Content-based heuristics (format-agnostic, any extension)
    # ------------------------------------------------------------------

    # OTLP JSON
    if '"resourceSpans"' in sniff:
        return "otlp", 0.97

    # Jaeger JSON (object or array variants)
    if '"traceID"' in sniff and '"spans"' in sniff:
        return "jaeger", 0.92

    # Zipkin JSON array
    if sniff_stripped.startswith("[") and '"traceId"' in sniff and '"localEndpoint"' in sniff:
        return "zipkin", 0.95

    # Locust CSV (may not have .csv extension)
    if "Type,Name,Request Count" in sniff or ("Request Count" in sniff and "Failure Count" in sniff and "Median Response Time" in sniff):
        return "locust", 0.97

    # W3C / IIS Extended Log
    if "#Fields:" in sniff or ("#Version:" in sniff and "#Date:" in sniff):
        return "w3c", 0.97

    # nginx / Apache Combined Log Format
    if _DETECT_NGINX_RE.search(sniff):
        return "nginx", 0.92

    # JSON Lines: count valid JSON objects among the first 20 non-empty lines
    _valid_json_lines = 0
    _total_nonempty = 0
    for _line in sniff.splitlines()[:30]:
        _line = _line.strip()
        if not _line:
            continue
        _total_nonempty += 1
        if _line.startswith("{") and _line.endswith("}"):
            try:
                json.loads(_line)
                _valid_json_lines += 1
            except json.JSONDecodeError:
                pass
    if _total_nonempty > 0 and _valid_json_lines / max(_total_nonempty, 1) >= 0.5 and _valid_json_lines >= 3:
        return "json_lines", 0.88

    # Generic SQL + HTTP: look for both HTTP verbs and SQL keywords
    has_http = bool(_DETECT_HTTP_RE.search(sniff))
    has_sql = bool(_DETECT_SQL_RE.search(sniff))

    if has_http and has_sql:
        return "generic_sql", 0.82
    if has_sql:
        return "generic_sql", 0.65
    if has_http:
        # Looks like a plain access log but didn't match nginx pattern
        return "nginx", 0.50

    # Final fallback
    return "generic_sql", 0.30


# ---------------------------------------------------------------------------
# Parser: Jaeger JSON
# ---------------------------------------------------------------------------


def _parse_jaeger(
    content: str,
    service_name_override: str = "",
) -> tuple[pd.DataFrame, list[str]]:
    """Parse a Jaeger JSON trace export into the unified spans DataFrame.

    Handles two structural variants:

    *Standard*::

        {
          "data": [
            {
              "traceID": "abc123",
              "spans": [{"spanID": "...", "operationName": "...", "tags": [...]}],
              "processes": {"p1": {"serviceName": "orders"}}
            }
          ]
        }

    *Wrapped*::

        {"jaeger_response": {"data": [...]}}

    Spans that reference their process via ``processID`` are resolved against
    the trace-level ``processes`` dictionary.  Spans that embed a ``process``
    object directly are also handled.

    Parent span IDs are extracted from ``references[].refType == "CHILD_OF"``
    first, then from the ``parentSpanID`` field as a fallback.

    Args:
        content:               Raw JSON string of the Jaeger export.
        service_name_override: If non-empty, overrides all extracted service names.

    Returns:
        A ``(DataFrame, warnings)`` tuple.  The DataFrame uses the 8-column schema.

    Raises:
        ValueError: If the JSON cannot be decoded or has an unexpected root type.
    """
    warnings: list[str] = []

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Jaeger JSON parse error: {exc}") from exc

    if isinstance(data, dict):
        # Unwrap optional jaeger_response envelope
        data = data.get("jaeger_response", data)
        traces = data.get("data", [])
    elif isinstance(data, list):
        traces = data
    else:
        raise ValueError(f"Unexpected Jaeger JSON root type: {type(data).__name__}. Expected dict with 'data' key, or list of traces.")

    rows: list[dict[str, Any]] = []

    for trace_idx, trace in enumerate(traces):
        if not isinstance(trace, dict):
            warnings.append(f"Jaeger: skipping trace at index {trace_idx} — not a dict ({type(trace).__name__})")
            continue

        trace_id = str(trace.get("traceID", _new_uuid()))
        spans = trace.get("spans", [])
        processes: dict[str, Any] = trace.get("processes", {})

        for span_idx, span in enumerate(spans):
            if not isinstance(span, dict):
                warnings.append(f"Jaeger trace {trace_id}: skipping non-dict span at index {span_idx}")
                continue

            # Resolve service name
            if service_name_override:
                svc = service_name_override
            else:
                process_id = span.get("processID", "")
                process = processes.get(process_id) if process_id else span.get("process", {})
                if not isinstance(process, dict):
                    process = {}
                svc = process.get("serviceName", "unknown")

            # Parent span reference
            parent_id: str | None = None
            for ref in span.get("references", []):
                if isinstance(ref, dict) and ref.get("refType") == "CHILD_OF":
                    parent_id = ref.get("spanID") or None
                    break
            if parent_id is None:
                parent_id = span.get("parentSpanID") or None

            # Tags: Jaeger uses list[{key, value, type}]
            raw_tags = span.get("tags", [])
            tags: list[dict[str, str]] = []
            for t in raw_tags:
                if isinstance(t, dict) and t.get("key"):
                    tags.append(_make_tag(str(t["key"]), str(t.get("value", ""))))

            rows.append(
                {
                    "trace_id": trace_id,
                    "span_id": str(span.get("spanID", _new_uuid())),
                    "parent_span_id": parent_id,
                    "service_name": svc,
                    "operation_name": str(span.get("operationName", "")),
                    "start_time": int(span.get("startTime", 0)),
                    "duration": int(span.get("duration", 0)),
                    "tags": _tags_to_json(tags),
                }
            )

    if not rows:
        warnings.append("Jaeger parser produced zero spans — file may be empty or structurally malformed")
        return _empty_df(), warnings

    return pd.DataFrame(rows), warnings


# ---------------------------------------------------------------------------
# Parser: Zipkin JSON
# ---------------------------------------------------------------------------


def _parse_zipkin(
    content: str,
    service_name_override: str = "",
) -> tuple[pd.DataFrame, list[str]]:
    """Parse Zipkin v2 JSON format into the unified spans DataFrame.

    Zipkin v2 is a flat JSON **array** of span objects.  Each span looks like::

        {
          "traceId": "abc123",
          "id": "def456",
          "parentId": "ghi789",
          "name": "get /orders",
          "kind": "SERVER",
          "timestamp": 1234567890000000,
          "duration": 12000,
          "localEndpoint": {"serviceName": "orders"},
          "tags": {"http.method": "GET", "http.path": "/orders"}
        }

    Tags in Zipkin v2 are a flat ``{string: string}`` dictionary, which is
    converted to the ``[{"key": ..., "value": ...}]`` list format.  The span
    ``kind`` field is also stored as the ``span.kind`` tag.

    Args:
        content:               Raw JSON string.
        service_name_override: Optional service name override.

    Returns:
        A ``(DataFrame, warnings)`` tuple.

    Raises:
        ValueError: If the JSON cannot be decoded or is not a list.
    """
    warnings: list[str] = []

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Zipkin JSON parse error: {exc}") from exc

    if isinstance(data, dict):
        # Some exporters wrap the array: {"spans": [...]} or {"data": [...]}
        data = data.get("spans", data.get("data", []))

    if not isinstance(data, list):
        raise ValueError(f"Zipkin JSON root must be a list of spans, got {type(data).__name__}")

    rows: list[dict[str, Any]] = []

    for idx, span in enumerate(data):
        if not isinstance(span, dict):
            warnings.append(f"Zipkin: skipping non-dict entry at index {idx}")
            continue

        trace_id = str(span.get("traceId", _new_uuid()))
        span_id = str(span.get("id", _new_uuid()))
        parent_id: str | None = span.get("parentId") or None

        # Service name from localEndpoint
        if service_name_override:
            svc = service_name_override
        else:
            endpoint = span.get("localEndpoint", {})
            svc = endpoint.get("serviceName", "unknown") if isinstance(endpoint, dict) else "unknown"

        operation = str(span.get("name", ""))
        timestamp_us = int(span.get("timestamp", 0))
        duration_us = int(span.get("duration", 0))

        # Tags: flat string→string dict in Zipkin v2
        raw_tags = span.get("tags", {})
        tags: list[dict[str, str]] = []
        if isinstance(raw_tags, dict):
            for k, v in raw_tags.items():
                tags.append(_make_tag(str(k), str(v)))

        # Span kind
        kind = str(span.get("kind", "")).strip()
        if kind:
            tags.append(_make_tag("span.kind", kind.lower()))

        rows.append(
            {
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_id,
                "service_name": svc,
                "operation_name": operation,
                "start_time": timestamp_us,
                "duration": duration_us,
                "tags": _tags_to_json(tags),
            }
        )

    if not rows:
        warnings.append("Zipkin parser produced zero spans — array may be empty")
        return _empty_df(), warnings

    return pd.DataFrame(rows), warnings


# ---------------------------------------------------------------------------
# Parser: OTLP JSON
# ---------------------------------------------------------------------------

#: Mapping from OTLP integer span kind to a human-readable string tag value.
_OTLP_KIND_MAP: dict[int, str] = {
    0: "unspecified",
    1: "internal",
    2: "server",
    3: "client",
    4: "producer",
    5: "consumer",
}


def _otlp_scalar(value_obj: Any) -> str:
    """Extract the scalar string representation from an OTLP attribute value object.

    OTLP attribute values are typed objects like::

        {"stringValue": "orders"}
        {"intValue": "42"}
        {"doubleValue": 3.14}
        {"boolValue": true}
        {"arrayValue": {"values": [...]}}

    Args:
        value_obj: The raw ``value`` field from an OTLP attribute dict.

    Returns:
        A string representation of the value.
    """
    if not isinstance(value_obj, dict):
        return str(value_obj)
    for primitive_key in ("stringValue", "intValue", "doubleValue", "boolValue"):
        if primitive_key in value_obj:
            return str(value_obj[primitive_key])
    # Array value
    arr_val = value_obj.get("arrayValue", {})
    if isinstance(arr_val, dict):
        items = arr_val.get("values", [])
        return ",".join(_otlp_scalar(v) for v in items)
    return str(value_obj)


def _parse_otlp(
    content: str,
    service_name_override: str = "",
) -> tuple[pd.DataFrame, list[str]]:
    """Parse OTLP JSON (OpenTelemetry Protocol) format into the unified spans DataFrame.

    Expected root structure::

        {
          "resourceSpans": [
            {
              "resource": {
                "attributes": [{"key": "service.name", "value": {"stringValue": "orders"}}]
              },
              "scopeSpans": [
                {
                  "spans": [
                    {
                      "traceId": "...", "spanId": "...", "parentSpanId": "...",
                      "name": "GET /orders",
                      "kind": 2,
                      "startTimeUnixNano": "1718809381000000000",
                      "endTimeUnixNano":   "1718809381012000000",
                      "attributes": [{"key": "http.method", "value": {"stringValue": "GET"}}]
                    }
                  ]
                }
              ]
            }
          ]
        }

    ``kind`` values: 0=unspecified, 1=internal, 2=**server**, 3=client,
    4=producer, 5=consumer.

    Time fields (``startTimeUnixNano``, ``endTimeUnixNano``) are nanosecond
    integers stored as JSON strings and are converted to microseconds.

    Args:
        content:               Raw JSON string.
        service_name_override: Optional service name override.

    Returns:
        A ``(DataFrame, warnings)`` tuple.

    Raises:
        ValueError: If the JSON cannot be decoded or ``resourceSpans`` is absent.
    """
    warnings: list[str] = []

    try:
        data = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"OTLP JSON parse error: {exc}") from exc

    resource_spans = data.get("resourceSpans", [])
    if not resource_spans:
        raise ValueError("OTLP JSON has no 'resourceSpans' key or it is empty. Ensure the file is a valid OTLP JSON export.")

    rows: list[dict[str, Any]] = []

    for rs_idx, rs in enumerate(resource_spans):
        if not isinstance(rs, dict):
            warnings.append(f"OTLP: skipping non-dict resourceSpans[{rs_idx}]")
            continue

        # Service name from resource attributes
        resource_attrs = rs.get("resource", {}).get("attributes", [])
        resource_svc = "unknown"
        for attr in resource_attrs:
            if isinstance(attr, dict) and attr.get("key") == "service.name":
                resource_svc = _otlp_scalar(attr.get("value", ""))
                break

        svc = service_name_override if service_name_override else resource_svc

        for scope_spans in rs.get("scopeSpans", []):
            if not isinstance(scope_spans, dict):
                continue
            for span in scope_spans.get("spans", []):
                if not isinstance(span, dict):
                    continue

                trace_id = str(span.get("traceId", _new_uuid()))
                span_id = str(span.get("spanId", _new_uuid()))
                parent_id: str | None = span.get("parentSpanId") or None

                # Times in nanoseconds (stored as strings in JSON)
                start_nano = int(span.get("startTimeUnixNano", "0") or "0")
                end_nano = int(span.get("endTimeUnixNano", "0") or "0")
                start_us = start_nano // 1_000
                duration_us = max(0, (end_nano - start_nano) // 1_000)

                # Span kind → tag
                kind_int = int(span.get("kind", 0))
                kind_str = _OTLP_KIND_MAP.get(kind_int, "unspecified")

                # Attributes → tags
                raw_attrs = span.get("attributes", [])
                tags: list[dict[str, str]] = []
                for attr in raw_attrs:
                    if isinstance(attr, dict) and attr.get("key"):
                        tags.append(_make_tag(str(attr["key"]), _otlp_scalar(attr.get("value", ""))))
                tags.append(_make_tag("span.kind", kind_str))

                rows.append(
                    {
                        "trace_id": trace_id,
                        "span_id": span_id,
                        "parent_span_id": parent_id,
                        "service_name": svc,
                        "operation_name": str(span.get("name", "")),
                        "start_time": start_us,
                        "duration": duration_us,
                        "tags": _tags_to_json(tags),
                    }
                )

    if not rows:
        warnings.append("OTLP parser produced zero spans — all scopeSpans may be empty")
        return _empty_df(), warnings

    return pd.DataFrame(rows), warnings


# ---------------------------------------------------------------------------
# Parser: Locust CSV
# ---------------------------------------------------------------------------

#: Locust CSV columns we care about (subset for service metrics).
_LOCUST_SKIP_NAMES: frozenset[str] = frozenset({"aggregated", "total", ""})

#: HTTP methods Locust typically records.
_HTTP_METHODS: frozenset[str] = frozenset({"GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS", "TRACE", "CONNECT"})


def _parse_locust(
    content: str,
    service_name_override: str = "",
    file_path: Path | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Parse a Locust request statistics CSV into the unified spans DataFrame.

    Locust CSV format (typical columns)::

        Type,Name,Request Count,Failure Count,Median Response Time,
        Average Response Time,Min Response Time,Max Response Time,
        Average Content Size,Requests/s,Failures/s,50%,66%,...

    Since Locust does not record individual request timestamps, one **synthetic**
    span is created per ``(Type, Name)`` row.  The ``Average Response Time``
    (milliseconds) is used as the span duration.

    Trace IDs are deterministic per ``(method, endpoint)`` pair using
    ``uuid.uuid5``, so re-ingesting the same file produces the same IDs.

    Tags produced:
        - ``http.method``   — CSV ``Type`` column
        - ``http.route``    — CSV ``Name`` column
        - ``span.kind``     — ``"server"``
        - ``source_format`` — ``"locust"``
        - ``error.rate``    — ``Failure Count / Request Count`` (if available)

    Args:
        content:               Raw CSV string.
        service_name_override: Optional service name override.
        file_path:             Original file path (used to infer service name).

    Returns:
        A ``(DataFrame, warnings)`` tuple.
    """
    warnings: list[str] = []
    svc = service_name_override or (_infer_service_from_path(file_path) if file_path else "locust-service")

    try:
        reader = csv.DictReader(io.StringIO(content))
    except Exception as exc:
        raise ValueError(f"Locust CSV parse error: {exc}") from exc

    rows: list[dict[str, Any]] = []

    for line_no, row_dict in enumerate(reader, start=2):  # start=2 because header is line 1
        method = str(row_dict.get("Type", "")).strip().upper()
        name = str(row_dict.get("Name", "")).strip()

        # Skip aggregated / empty summary rows
        if name.lower() in _LOCUST_SKIP_NAMES:
            continue

        if not name:
            warnings.append(f"Locust CSV line {line_no}: empty endpoint name, skipping")
            continue

        # Average response time (milliseconds → microseconds)
        try:
            avg_ms = float(row_dict.get("Average Response Time", 0) or 0)
        except (ValueError, TypeError):
            avg_ms = 0.0
        duration_us = int(avg_ms * 1_000)

        # Deterministic trace ID per (method, endpoint)
        trace_id = uuid.uuid5(uuid.NAMESPACE_URL, f"{method}:{name}").hex
        span_id = _new_uuid()

        tags: list[dict[str, str]] = [
            _make_tag("http.method", method),
            _make_tag("http.route", name),
            _make_tag("span.kind", "server"),
            _make_tag("source_format", "locust"),
        ]

        # Error rate tag
        try:
            failure_count = int(row_dict.get("Failure Count", 0) or 0)
            request_count = int(row_dict.get("Request Count", 0) or 0)
            if request_count > 0:
                error_rate = round(failure_count / request_count, 4)
                tags.append(_make_tag("error.rate", str(error_rate)))
                tags.append(_make_tag("request.count", str(request_count)))
        except (ValueError, TypeError, ZeroDivisionError):
            pass

        # Throughput
        try:
            rps = float(row_dict.get("Requests/s", 0) or 0)
            tags.append(_make_tag("throughput.rps", str(round(rps, 3))))
        except (ValueError, TypeError):
            pass

        rows.append(
            {
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": None,
                "service_name": svc,
                "operation_name": f"{method} {name}",
                "start_time": 0,  # Locust CSV has no per-request timestamp
                "duration": duration_us,
                "tags": _tags_to_json(tags),
            }
        )

    if not rows:
        warnings.append("Locust parser produced zero rows — CSV may be empty or all rows are aggregated")
        return _empty_df(), warnings

    return pd.DataFrame(rows), warnings


# ---------------------------------------------------------------------------
# Parser: nginx / Apache Combined Access Log
# ---------------------------------------------------------------------------

#: Regex for the Combined Log Format used by nginx and Apache.
#: Groups: ip, date, method, path, proto, status, bytes, referer, agent
_NGINX_RE = re.compile(
    r"(?P<ip>\S+)"  # client IP or hostname
    r"\s+\S+\s+\S+"  # ident, auth user
    r"\s+\[(?P<date>[^\]]+)\]"  # [day/Mon/year:HH:MM:SS tz]
    r'\s+"(?P<method>\S+)\s+(?P<path>\S+)'  # "METHOD /path
    r'(?:\s+(?P<proto>[^"]*))?"'  # PROTO/version"
    r"\s+(?P<status>\d{3})"  # status code
    r"\s+(?P<bytes>\S+)"  # response bytes (or -)
    r'(?:\s+"(?P<referer>[^"]*)"'  # "Referer" (optional)
    r'\s+"(?P<agent>[^"]*)")?',  # "User-Agent" (optional)
    re.IGNORECASE,
)


def _parse_nginx(
    content: str,
    service_name_override: str = "",
    file_path: Path | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Parse nginx / Apache Combined Access Log format into the unified spans DataFrame.

    Each log line becomes **one span** in **one trace** (no parent/child
    relationships are derivable from a plain access log).

    Tags produced per span:
        - ``http.method``      — HTTP verb
        - ``http.route``       — Request path (no query string)
        - ``http.status_code`` — HTTP response status
        - ``span.kind``        — ``"server"``
        - ``net.peer.ip``      — Client IP address
        - ``http.user_agent``  — User-Agent (if present)

    Lines that do not match the Combined Log Format pattern are skipped, and
    a warning is added if more than zero lines were skipped.

    Args:
        content:               Raw log file content as a string.
        service_name_override: Optional service name override.
        file_path:             Original file path (used to infer service name).

    Returns:
        A ``(DataFrame, warnings)`` tuple.
    """
    warnings: list[str] = []
    svc = service_name_override or (_infer_service_from_path(file_path) if file_path else "web-service")

    rows: list[dict[str, Any]] = []
    skipped = 0

    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue

        m = _NGINX_RE.match(line)
        if not m:
            skipped += 1
            continue

        method = m.group("method").upper()
        path = m.group("path")
        # Strip query string from path for the operation name
        path_clean = path.split("?")[0]
        status = m.group("status")
        date_str = m.group("date")
        start_us = _parse_common_log_timestamp(date_str)

        tags: list[dict[str, str]] = [
            _make_tag("http.method", method),
            _make_tag("http.route", path_clean),
            _make_tag("http.status_code", status),
            _make_tag("span.kind", "server"),
        ]

        client_ip = m.group("ip") or ""
        if client_ip and client_ip != "-":
            tags.append(_make_tag("net.peer.ip", client_ip))

        agent = m.group("agent") or ""
        if agent and agent != "-":
            tags.append(_make_tag("http.user_agent", agent[:200]))

        rows.append(
            {
                "trace_id": _new_uuid(),
                "span_id": _new_uuid(),
                "parent_span_id": None,
                "service_name": svc,
                "operation_name": f"{method} {path_clean}",
                "start_time": start_us,
                "duration": 0,  # plain access logs do not record response time
                "tags": _tags_to_json(tags),
            }
        )

    if skipped > 0:
        warnings.append(f"nginx parser skipped {skipped} line(s) that did not match the Combined Log Format pattern")

    if not rows:
        warnings.append("nginx/Apache parser produced zero spans — log may be empty or use a non-standard format")
        return _empty_df(), warnings

    return pd.DataFrame(rows), warnings


# ---------------------------------------------------------------------------
# Parser: W3C Extended Log Format / IIS
# ---------------------------------------------------------------------------


def _parse_w3c(
    content: str,
    service_name_override: str = "",
    file_path: Path | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Parse W3C Extended Log Format (IIS / some Apache configs) into the unified spans DataFrame.

    W3C logs start with directive lines::

        #Version: 1.0
        #Date: 2026-06-19 00:00:00
        #Fields: date time cs-method cs-uri-stem sc-status sc-bytes time-taken

    Data lines are space-separated and match the ``#Fields`` declaration order.

    Common ``#Fields`` tokens used:
        - ``date``, ``time``           — for ``start_time``
        - ``cs-method``                — HTTP verb
        - ``cs-uri-stem``              — path (without query)
        - ``sc-status``                — HTTP status code
        - ``time-taken``               — response time in **milliseconds** (IIS default)
        - ``c-ip``                     — client IP address
        - ``cs-bytes``                 — request bytes

    Tags produced: ``http.method``, ``http.route``, ``http.status_code``,
    ``span.kind``, ``net.peer.ip`` (if available).

    Args:
        content:               Raw log file content as a string.
        service_name_override: Optional service name override.
        file_path:             Original file path (used to infer service name).

    Returns:
        A ``(DataFrame, warnings)`` tuple.
    """
    warnings: list[str] = []
    svc = service_name_override or (_infer_service_from_path(file_path) if file_path else "iis-service")

    fields: list[str] = []
    rows: list[dict[str, Any]] = []
    skipped = 0
    found_fields_directive = False

    for lineno, line in enumerate(content.splitlines(), start=1):
        line = line.rstrip("\r\n")
        if not line:
            continue

        if line.startswith("#Fields:"):
            fields = line[len("#Fields:") :].strip().split()
            found_fields_directive = True
            continue

        if line.startswith("#"):
            # Other directives (#Version, #Date, #Software …) — ignored
            continue

        if not fields:
            # Data lines before any #Fields directive
            skipped += 1
            continue

        parts = line.split()
        if len(parts) < len(fields):
            skipped += 1
            continue

        row_map: dict[str, str] = dict(zip(fields, parts))

        date_val = row_map.get("date", "")
        time_val = row_map.get("time", "")
        dt_str = f"{date_val} {time_val}".strip()
        start_us = _parse_iso_timestamp_to_us(dt_str) if dt_str else 0

        # HTTP method — W3C uses cs-method
        method = str(row_map.get("cs-method", row_map.get("cs(method)", row_map.get("method", "GET")))).upper()

        # URI path — W3C uses cs-uri-stem (path only, no query string)
        path = str(row_map.get("cs-uri-stem", row_map.get("cs-uri", row_map.get("uri", "/"))))

        # Status code
        status = str(row_map.get("sc-status", row_map.get("status", "200")))

        # Response time: IIS time-taken is in milliseconds
        time_taken_raw = row_map.get("time-taken", row_map.get("timetaken", "0"))
        try:
            duration_us = int(float(time_taken_raw) * 1_000)
        except (ValueError, TypeError):
            duration_us = 0

        tags: list[dict[str, str]] = [
            _make_tag("http.method", method),
            _make_tag("http.route", path),
            _make_tag("http.status_code", status),
            _make_tag("span.kind", "server"),
        ]

        # Client IP
        client_ip = row_map.get("c-ip", row_map.get("c(ip)", ""))
        if client_ip and client_ip not in ("-", ""):
            tags.append(_make_tag("net.peer.ip", client_ip))

        rows.append(
            {
                "trace_id": _new_uuid(),
                "span_id": _new_uuid(),
                "parent_span_id": None,
                "service_name": svc,
                "operation_name": f"{method} {path}",
                "start_time": start_us,
                "duration": duration_us,
                "tags": _tags_to_json(tags),
            }
        )

    if not found_fields_directive:
        warnings.append("W3C parser: no '#Fields:' directive found — cannot determine column order")

    if skipped > 0:
        warnings.append(f"W3C parser skipped {skipped} line(s) with insufficient fields")

    if not rows:
        warnings.append("W3C parser produced zero spans — file may be empty or missing a valid #Fields directive")
        return _empty_df(), warnings

    return pd.DataFrame(rows), warnings


# ---------------------------------------------------------------------------
# Parser: Generic application log with SQL correlation
# ---------------------------------------------------------------------------

#: Timestamp patterns tried in order of specificity.
_TS_PATTERNS: list[re.Pattern[str]] = [
    # ISO 8601 with optional fractional seconds and optional Z/offset
    re.compile(
        r"(\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})?)",
    ),
    # Date and time without T separator
    re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"),
    # Common log date: 19/Jun/2026:15:23:01
    re.compile(r"(\d{2}/\w{3}/\d{4}[: ]\d{2}:\d{2}:\d{2})"),
]

#: Matches an HTTP request line (METHOD /path [HTTP/x.x] [status] [duration])
_HTTP_LINE_RE = re.compile(
    r"(?P<method>GET|POST|PUT|DELETE|PATCH|HEAD|OPTIONS)\s+"
    r"(?P<path>/[^\s\"']*)"
    r"(?:\s+HTTP/[^\s\"']*)?"
    r"(?:\s+(?P<status>[1-5]\d{2}))?"
    r"(?:\s+(?P<duration_ms>\d+(?:\.\d+)?)ms)?",
    re.IGNORECASE,
)

#: Django-style SQL: ``(0.012) SELECT "orders_order"."id" … ; args=(...)``
_DJANGO_SQL_RE = re.compile(
    r"\((?P<time_s>[\d.]+)\)\s+"
    r"(?P<sql>(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\s+.{3,600}?)"
    r"(?=;\s*(?:args=|\Z)|$)",
    re.IGNORECASE | re.DOTALL,
)

#: SQLAlchemy engine log: ``INFO sqlalchemy.engine.Engine SELECT …``
_SQLA_SQL_RE = re.compile(
    r"(?:sqlalchemy\.engine|Engine)\s*[:\-]\s*"
    r"(?P<sql>(?:SELECT|INSERT|UPDATE|DELETE|CREATE|ALTER|DROP)\s+.{3,600}?)(?=;|\Z|$)",
    re.IGNORECASE | re.DOTALL,
)

#: Generic SQL keyword at start of a SQL fragment in a log line.
_GENERIC_SQL_RE = re.compile(
    r"(?:^|(?<=[\s\"'`]))"
    r"(?P<sql>(?:SELECT|INSERT\s+INTO|UPDATE|DELETE\s+FROM|CREATE\s+TABLE|ALTER\s+TABLE)"
    r"\s+.{3,400}?)(?=;|\n|$)",
    re.IGNORECASE | re.DOTALL,
)


def _extract_timestamp_from_line(line: str) -> int:
    """Try to find and parse a timestamp from any position in *line*.

    Args:
        line: A single stripped log line.

    Returns:
        Microseconds since the Unix epoch, or ``0`` if no timestamp was found.
    """
    for pattern in _TS_PATTERNS:
        m = pattern.search(line)
        if m:
            ts_str = m.group(1)
            ts = _parse_iso_timestamp_to_us(ts_str)
            if ts:
                return ts
            ts = _parse_common_log_timestamp(ts_str)
            if ts:
                return ts
    return 0


def _extract_sql_from_line(line: str) -> str | None:
    """Extract a SQL statement from a single log line.

    Tries (in order):
        1. Django ``(time) SQL;`` format
        2. SQLAlchemy Engine log format
        3. Generic SQL keyword start

    Args:
        line: A single stripped log line.

    Returns:
        The extracted SQL string, or ``None`` if no SQL was found.
    """
    m = _DJANGO_SQL_RE.search(line)
    if m:
        return m.group("sql").strip()

    m = _SQLA_SQL_RE.search(line)
    if m:
        return m.group("sql").strip()

    m = _GENERIC_SQL_RE.search(line)
    if m:
        return m.group("sql").strip()

    return None


def _detect_db_system_from_context(line: str, sql: str) -> str:
    """Heuristically detect the database system from surrounding log context.

    Args:
        line: The full log line (may contain logger names, driver names, etc.).
        sql:  The extracted SQL statement.

    Returns:
        A normalised DB system string (e.g. ``"postgresql"``, ``"mysql"``).
    """
    combined = (line + " " + sql).lower()

    if any(k in combined for k in ("postgresql", "psycopg", "pg_catalog")):
        return "postgresql"
    if any(k in combined for k in ("mysql", "pymysql", "mysqlclient")):
        return "mysql"
    if "sqlite" in combined:
        return "sqlite"
    if "oracle" in combined:
        return "oracle"
    if any(k in combined for k in ("mssql", "sqlserver", "pyodbc", "pymssql")):
        return "mssql"
    if "mongodb" in combined:
        return "mongodb"
    if any(k in combined for k in ("redis", "memcached")):
        return "redis"
    return "sql"


def _parse_generic_sql(
    content: str,
    service_name_override: str = "",
    file_path: Path | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Parse a generic application log with HTTP requests and SQL queries.

    This is the most powerful and most permissive parser in the module.
    It handles logs from Django, Flask/Werkzeug, SQLAlchemy, Spring Boot,
    Ruby on Rails, and any framework that writes HTTP request lines and SQL
    queries into the same log stream.

    **Correlation algorithm**:
        1. Scan each line of the log in sequence.
        2. When an HTTP request line is detected (``GET /path``, ``POST /path``,
           …), a new root HTTP span is started.
        3. SQL lines that appear **after** an HTTP line and **before** the next
           HTTP line are treated as DB child spans of that HTTP span.
        4. SQL lines that appear **before** any HTTP line are emitted as orphaned
           root spans (their ``parent_span_id`` is ``None``).
        5. At end-of-file, the final HTTP span and all remaining SQL spans are
           flushed.

    Example input handled::

        2026-06-19 15:23:01 INFO django.request: GET /orders/ 200 45ms
        2026-06-19 15:23:01 DEBUG django.db.backends: (0.012) SELECT "orders_order"."id" FROM "orders_order"; args=()
        2026-06-19 15:23:01 DEBUG django.db.backends: (0.003) SELECT "users_user"."id" FROM "users_user" WHERE id=1; args=(1,)
        2026-06-19 15:23:02 INFO django.request: POST /orders/ 201 120ms
        2026-06-19 15:23:02 DEBUG django.db.backends: (0.005) INSERT INTO "orders_order" ("user_id") VALUES (1); args=(...)

    Tags produced for HTTP spans:
        ``http.method``, ``http.route``, ``http.status_code`` (if found),
        ``span.kind: server``

    Tags produced for DB spans:
        ``db.system``, ``db.statement``, ``db.tables`` (comma-separated table
        names extracted via :func:`_extract_tables_from_sql`),
        ``span.kind: client``

    Args:
        content:               Raw log file content as a string.
        service_name_override: Optional service name override.
        file_path:             Original file path (used to infer service name).

    Returns:
        A ``(DataFrame, warnings)`` tuple.
    """
    warnings: list[str] = []
    svc = service_name_override or (_infer_service_from_path(file_path) if file_path else "app-service")

    rows: list[dict[str, Any]] = []

    # ---- mutable state for the correlator ----
    # Pending SQL statements collected since the last HTTP line.
    pending_sql: list[tuple[str, int, str]] = []  # (sql_text, ts_us, raw_line)

    # Current open HTTP span fields
    cur_http_span_id: str | None = None
    cur_http_trace_id: str | None = None
    cur_http_start_us: int = 0
    cur_http_method: str = ""
    cur_http_path: str = ""
    cur_http_status: str = ""
    cur_http_duration_us: int = 0

    # ------------------------------------------------------------------
    # Inner helpers — defined as closures so they share mutable state
    # via the enclosing scope.  Only pending_sql is *reassigned* (needs
    # nonlocal); all other mutations are in-place (rows.append) or reads.
    # ------------------------------------------------------------------

    def _emit_http_span() -> None:
        """Append the current HTTP span to *rows*."""
        if cur_http_span_id is None:
            return
        tags: list[dict[str, str]] = [
            _make_tag("http.method", cur_http_method),
            _make_tag("http.route", cur_http_path),
            _make_tag("span.kind", "server"),
        ]
        if cur_http_status:
            tags.append(_make_tag("http.status_code", cur_http_status))
        rows.append(
            {
                "trace_id": cur_http_trace_id,
                "span_id": cur_http_span_id,
                "parent_span_id": None,
                "service_name": svc,
                "operation_name": f"{cur_http_method} {cur_http_path}",
                "start_time": cur_http_start_us,
                "duration": cur_http_duration_us,
                "tags": _tags_to_json(tags),
            }
        )

    def _emit_pending_sql(parent_trace: str | None, parent_span: str | None) -> None:
        """Append all pending SQL spans to *rows*, then clear the queue."""
        nonlocal pending_sql
        for sql_text, sql_ts, sql_line in pending_sql:
            tables = _extract_tables_from_sql(sql_text)
            db_system = _detect_db_system_from_context(sql_line, sql_text)
            sql_span_id = _new_uuid()
            trace_id = parent_trace or _new_uuid()
            tags: list[dict[str, str]] = [
                _make_tag("db.system", db_system),
                _make_tag("db.statement", sql_text[:500]),
                _make_tag("span.kind", "client"),
            ]
            if tables:
                tags.append(_make_tag("db.tables", ",".join(tables)))
            rows.append(
                {
                    "trace_id": trace_id,
                    "span_id": sql_span_id,
                    "parent_span_id": parent_span,
                    "service_name": svc,
                    "operation_name": sql_text[:120],
                    "start_time": sql_ts or cur_http_start_us,
                    "duration": 0,
                    "tags": _tags_to_json(tags),
                }
            )
        pending_sql = []

    # ------------------------------------------------------------------
    # Main scan loop
    # ------------------------------------------------------------------
    for line in content.splitlines():
        line_stripped = line.strip()
        if not line_stripped:
            continue

        ts_us = _extract_timestamp_from_line(line_stripped)
        sql_text = _extract_sql_from_line(line_stripped)
        http_m = _HTTP_LINE_RE.search(line_stripped) if not sql_text else None

        if http_m:
            # ── New HTTP line: flush previous span + its SQL children ──
            if cur_http_span_id is not None:
                _emit_pending_sql(cur_http_trace_id, cur_http_span_id)
                _emit_http_span()

            # Start new HTTP span
            cur_http_method = http_m.group("method").upper()
            cur_http_path = http_m.group("path")
            cur_http_status = http_m.group("status") or ""
            try:
                raw_dur = http_m.group("duration_ms") or "0"
                cur_http_duration_us = int(float(raw_dur) * 1_000)
            except (ValueError, TypeError):
                cur_http_duration_us = 0
            cur_http_start_us = ts_us
            cur_http_trace_id = _new_uuid()
            cur_http_span_id = _new_uuid()

        elif sql_text:
            # Accumulate SQL for the current HTTP span (or as orphans)
            pending_sql.append((sql_text, ts_us, line_stripped))

    # ── End-of-file flush ──
    if cur_http_span_id is not None:
        _emit_pending_sql(cur_http_trace_id, cur_http_span_id)
        _emit_http_span()
    else:
        # Orphaned SQL lines (log has no HTTP request lines at all)
        _emit_pending_sql(None, None)

    if not rows:
        warnings.append("generic_sql parser found no recognisable HTTP or SQL lines — the file may be a pure text log or use non-standard formatting")
        return _empty_df(), warnings

    return pd.DataFrame(rows), warnings


# ---------------------------------------------------------------------------
# Parser: JSON Lines
# ---------------------------------------------------------------------------

#: Fields tried when looking up the service name in a JSON Lines record.
_JSONL_SVC_KEYS: tuple[str, ...] = ("service", "service_name", "serviceName", "app", "application")

#: Fields tried when looking up the timestamp in a JSON Lines record.
_JSONL_TS_KEYS: tuple[str, ...] = ("timestamp", "time", "@timestamp", "ts", "datetime")

#: Fields tried when looking up the span duration in a JSON Lines record.
_JSONL_DUR_KEYS: tuple[str, ...] = (
    "duration_ms",
    "elapsed_ms",
    "response_time_ms",
    "duration",
    "elapsed",
)


def _parse_json_lines(
    content: str,
    service_name_override: str = "",
    file_path: Path | None = None,
) -> tuple[pd.DataFrame, list[str]]:
    """Parse JSON Lines format (one JSON object per line) into the unified spans DataFrame.

    Each line may represent an HTTP span, a DB span, or a generic log event.
    The parser inspects each record's fields to determine its type:

    **HTTP span** — detected when the record has a ``method`` field AND a
    ``path``/``url``/``uri`` field::

        {"timestamp": "2026-06-19T15:23:01Z", "service": "orders",
         "method": "GET", "path": "/orders", "status": 200, "duration_ms": 45}

    **DB span** — detected when the record has a ``sql``/``query``/
    ``db_statement`` field::

        {"timestamp": "2026-06-19T15:23:01Z", "service": "orders",
         "sql": "SELECT * FROM orders WHERE user_id = 1"}

    **Generic event** — any other record; all scalar fields are stored as tags
    and the event message (``message``/``msg``/``event``) is the operation name.

    Pre-existing ``span_id``, ``trace_id``, and ``parent_span_id`` fields are
    preserved if present; otherwise synthetic UUIDs are generated.

    Duration conversion:
        - Keys ending in ``_ms`` (e.g. ``duration_ms``) → converted from ms to µs.
        - Other duration keys → treated as already in microseconds.

    Args:
        content:               Raw newline-delimited JSON text.
        service_name_override: Optional service name override.
        file_path:             Original file path (used to infer service name).

    Returns:
        A ``(DataFrame, warnings)`` tuple.
    """
    warnings: list[str] = []
    fallback_svc = service_name_override or (_infer_service_from_path(file_path) if file_path else "unknown-service")

    rows: list[dict[str, Any]] = []
    failed_lines = 0

    for lineno, raw_line in enumerate(content.splitlines(), start=1):
        raw_line = raw_line.strip()
        if not raw_line:
            continue

        try:
            obj = json.loads(raw_line)
        except json.JSONDecodeError:
            failed_lines += 1
            continue

        if not isinstance(obj, dict):
            continue

        # ── Service name ──
        svc = service_name_override
        if not svc:
            for key in _JSONL_SVC_KEYS:
                if key in obj and obj[key]:
                    svc = str(obj[key])
                    break
            svc = svc or fallback_svc

        # ── Timestamp ──
        ts_str = ""
        for key in _JSONL_TS_KEYS:
            if key in obj:
                ts_str = str(obj[key])
                break
        start_us = _parse_iso_timestamp_to_us(ts_str) if ts_str else 0

        # ── Duration ──
        duration_us = 0
        for key in _JSONL_DUR_KEYS:
            if key in obj:
                try:
                    val = float(obj[key])
                    # Keys ending in _ms → milliseconds → convert to µs
                    duration_us = int(val * 1_000) if key.endswith("_ms") else int(val)
                    break
                except (ValueError, TypeError):
                    pass

        # ── Span type detection ──
        method = str(obj.get("method", "")).upper()
        path = str(obj.get("path", obj.get("url", obj.get("uri", ""))))
        status = str(obj.get("status", obj.get("status_code", obj.get("http_status", ""))))
        sql_val = str(obj.get("sql", obj.get("query", obj.get("db_statement", ""))))

        # Normalise "None" strings from Python repr
        path = "" if path in ("None", "null") else path
        sql_val = "" if sql_val in ("None", "null") else sql_val
        status = "" if status in ("None", "null") else status

        tags: list[dict[str, str]] = []

        if sql_val:
            # ── DB span ──
            db_system = str(obj.get("db_system", obj.get("db", "sql")))
            tables = _extract_tables_from_sql(sql_val)
            tags = [
                _make_tag("db.system", db_system),
                _make_tag("db.statement", sql_val[:500]),
                _make_tag("span.kind", "client"),
            ]
            if tables:
                tags.append(_make_tag("db.tables", ",".join(tables)))
            operation_name = sql_val[:120]

        elif method and path:
            # ── HTTP span ──
            tags = [
                _make_tag("http.method", method),
                _make_tag("http.route", path),
                _make_tag("span.kind", "server"),
            ]
            if status:
                tags.append(_make_tag("http.status_code", status))
            operation_name = f"{method} {path}"

        else:
            # ── Generic log event ──
            _reserved = set(_JSONL_SVC_KEYS) | set(_JSONL_TS_KEYS) | set(_JSONL_DUR_KEYS)
            for k, v in obj.items():
                if k in _reserved:
                    continue
                if isinstance(v, (str, int, float, bool)) and v is not None:
                    tags.append(_make_tag(str(k), str(v)))
            operation_name = str(obj.get("message", obj.get("msg", obj.get("event", "unknown"))))

        # ── IDs ──
        span_id = str(obj.get("span_id", obj.get("spanId", obj.get("spanID", _new_uuid()))))
        trace_id = str(obj.get("trace_id", obj.get("traceId", obj.get("traceID", _new_uuid()))))
        parent_id: str | None = obj.get("parent_span_id") or obj.get("parentSpanId") or obj.get("parentSpanID") or None
        if parent_id is not None:
            parent_id = str(parent_id)

        rows.append(
            {
                "trace_id": trace_id,
                "span_id": span_id,
                "parent_span_id": parent_id,
                "service_name": svc,
                "operation_name": operation_name,
                "start_time": start_us,
                "duration": duration_us,
                "tags": _tags_to_json(tags),
            }
        )

    if failed_lines > 0:
        warnings.append(f"JSON Lines parser failed to decode {failed_lines} line(s) — they may be truncated or contain invalid JSON")

    if not rows:
        warnings.append("JSON Lines parser produced zero spans — all lines may have failed to parse")
        return _empty_df(), warnings

    return pd.DataFrame(rows), warnings


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

#: Map from format identifier to the corresponding parser function.
_PARSER_MAP: dict[str, Any] = {
    "jaeger": _parse_jaeger,
    "zipkin": _parse_zipkin,
    "otlp": _parse_otlp,
    "locust": _parse_locust,
    "nginx": _parse_nginx,
    "w3c": _parse_w3c,
    "generic_sql": _parse_generic_sql,
    "json_lines": _parse_json_lines,
}

#: Parsers that accept a ``file_path`` keyword argument for service name inference.
_PATH_AWARE_PARSERS: frozenset[str] = frozenset({"locust", "nginx", "w3c", "generic_sql", "json_lines"})


def _call_parser(
    fmt: str,
    content: str,
    service_name_override: str,
    file_path: Path,
) -> tuple[pd.DataFrame, list[str]]:
    """Call the parser for *fmt* and return ``(DataFrame, warnings)``.

    Args:
        fmt:                   Format identifier (must be a key of :data:`_PARSER_MAP`).
        content:               Full file content as a string.
        service_name_override: Service name override (may be empty).
        file_path:             Original file path passed to path-aware parsers.

    Raises:
        ValueError: If *fmt* is not a recognised format identifier.
    """
    parser = _PARSER_MAP.get(fmt)
    if parser is None:
        raise ValueError(f"Unknown format '{fmt}'. Supported formats: {sorted(_PARSER_MAP)}")

    if fmt in _PATH_AWARE_PARSERS:
        return parser(content, service_name_override, file_path)
    return parser(content, service_name_override)


# ---------------------------------------------------------------------------
# Main public API
# ---------------------------------------------------------------------------


def ingest_log_file(
    file_path: Path,
    service_name: str = "",
    format_hint: str = "",
    encoding: str = "utf-8",
) -> IngestResult:
    """Auto-detect the format of any log file and parse it into the MBA spans DataFrame.

    This is the **primary entry point** for all log file ingestion in the MBA
    tool.  It:

    1. Reads the file (with encoding fallbacks).
    2. Auto-detects the format (unless *format_hint* overrides detection).
    3. Calls the appropriate parser.
    4. Falls back through alternative parsers if the primary one produces no rows.
    5. Validates and coerces the output to the canonical 8-column schema.
    6. Returns a rich :class:`IngestResult` with statistics and metadata.

    Supported formats
    -----------------
    ``"jaeger"``       — Jaeger JSON export (``{"data": [...]}`` or wrapped)
    ``"zipkin"``       — Zipkin v2 JSON (flat array of span objects)
    ``"otlp"``         — OpenTelemetry Protocol JSON (``{"resourceSpans": [...]}`` )
    ``"locust"``       — Locust request statistics CSV
    ``"nginx"``        — nginx / Apache Combined Access Log
    ``"w3c"``          — W3C Extended Log Format (IIS)
    ``"generic_sql"``  — Django / Flask / Spring / Rails app log with HTTP + SQL
    ``"json_lines"``   — Structured logging in JSON Lines format

    Args:
        file_path:    Path to the log file to ingest.  The file must exist and
                      be a regular text file.
        service_name: Override the auto-detected or inferred service name for
                      all spans.  If empty, the parser infers a name from the
                      file content or the filename.
        format_hint:  Skip auto-detection and use this format identifier directly.
                      Must be one of the supported format IDs listed above.
                      Pass an empty string (the default) to enable auto-detection.
        encoding:     The text encoding to try first.  On failure the module
                      automatically retries with ``"latin-1"`` and ``"cp1252"``.

    Returns:
        An :class:`IngestResult` instance containing:

        - ``format_detected``       — the format actually used for parsing
        - ``format_confidence``     — detector confidence (0.0–1.0)
        - ``spans_df``              — unified DataFrame (8-column spans.csv schema)
        - ``warnings``              — list of non-fatal issues
        - ``stats``                 — dict with ``total_spans``, ``http_spans``,
          ``db_spans``, ``services``, ``unique_traces``
        - ``has_db_info``           — whether any DB spans were extracted
        - ``has_trace_correlation`` — whether HTTP↔DB parent/child links exist
        - ``service_name_used``     — the service name stored in ``service_name`` column

    Raises:
        FileNotFoundError: If *file_path* does not exist on disk.
        ValueError:        If the file is empty, cannot be decoded in any
                           supported encoding, or cannot be parsed by **any**
                           available parser.

    Examples:
        >>> from pathlib import Path
        >>> from boundary_analyzer.parsing.log_ingestion import ingest_log_file
        >>>
        >>> result = ingest_log_file(Path("traces/export.json"))
        >>> print(result.format_detected, result.stats)
        jaeger {'total_spans': 142, 'http_spans': 98, 'db_spans': 44, ...}
        >>>
        >>> result2 = ingest_log_file(
        ...     Path("logs/django.log"),
        ...     service_name="orders",
        ...     format_hint="generic_sql",
        ... )
        >>> print(result2.has_trace_correlation)
        True
    """
    file_path = Path(file_path)

    if not file_path.exists():
        raise FileNotFoundError(f"Log file not found: {file_path}")

    # ── Read file ──
    content = _read_file_with_fallback(file_path, encoding)

    if not content.strip():
        raise ValueError(f"Log file is empty: {file_path}")

    # ── Format detection ──
    if format_hint:
        fmt = format_hint.lower().strip()
        if fmt not in _PARSER_MAP:
            raise ValueError(f"Unknown format_hint '{fmt}'. Supported formats: {sorted(_PARSER_MAP)}")
        confidence = 1.0
        logger.info(
            "Ingesting '%s' with user-supplied format hint '%s'",
            file_path.name,
            fmt,
        )
    else:
        fmt, confidence = detect_format(file_path, encoding)
        logger.info(
            "Ingesting '%s': detected format='%s' (confidence=%.2f)",
            file_path.name,
            fmt,
            confidence,
        )

    # ── Parse with fallback chain ──
    all_warnings: list[str] = []
    df = _empty_df()
    final_fmt = fmt

    # Build the order of formats to try: detected format first, then the rest
    # ordered by decreasing likelihood (structured formats before text heuristics).
    _fallback_order: list[str] = [fmt] + [f for f in ["jaeger", "zipkin", "otlp", "json_lines", "locust", "w3c", "nginx", "generic_sql"] if f != fmt]

    last_error: Exception | None = None
    parse_succeeded = False

    for attempt_fmt in _fallback_order:
        try:
            df, parser_warnings = _call_parser(attempt_fmt, content, service_name, file_path)
            all_warnings.extend(parser_warnings)

            if not df.empty:
                if attempt_fmt != fmt:
                    all_warnings.append(f"Detected format '{fmt}' produced no spans; successfully parsed as '{attempt_fmt}'")
                    final_fmt = attempt_fmt
                    confidence = max(0.1, confidence - 0.25)
                parse_succeeded = True
                break

        except Exception as exc:
            last_error = exc
            logger.debug(
                "Parser '%s' raised %s: %s",
                attempt_fmt,
                type(exc).__name__,
                exc,
            )
            continue

    if not parse_succeeded:
        tried_str = ", ".join(f"'{f}'" for f in _fallback_order)
        raise ValueError(f"Could not parse '{file_path.name}' with any supported format (tried: {tried_str}). Last error: {last_error}")

    # ── Schema coercion ──
    df = _ensure_schema(df)

    df["start_time"] = pd.Series(pd.to_numeric(df["start_time"], errors="coerce")).fillna(0).astype("int64")  # type: ignore[call-overload]
    df["duration"] = pd.Series(pd.to_numeric(df["duration"], errors="coerce")).fillna(0).astype("int64")  # type: ignore[call-overload]
    df["service_name"] = df["service_name"].fillna("unknown").astype(str)
    df["operation_name"] = df["operation_name"].fillna("").astype(str)
    df["trace_id"] = df["trace_id"].fillna("").astype(str)
    df["span_id"] = df["span_id"].fillna("").astype(str)
    df["tags"] = df["tags"].fillna("[]").astype(str)

    # ── Statistics ──
    stats = _compute_stats(df)

    has_db_info = stats["db_spans"] > 0

    has_trace_correlation = False
    if has_db_info and not df.empty:
        db_mask = df["tags"].str.contains("db.system", na=False, regex=False)
        if db_mask.any():
            has_trace_correlation = bool(df.loc[db_mask, "parent_span_id"].notna().any())

    # ── Determine the service name actually used ──
    if service_name:
        service_name_used = service_name
    elif not df.empty:
        mode_result = df["service_name"].mode()
        service_name_used = str(mode_result.iloc[0]) if not mode_result.empty else "unknown"
    else:
        service_name_used = "unknown"

    return IngestResult(
        format_detected=final_fmt,
        format_confidence=round(confidence, 4),
        spans_df=df,
        warnings=all_warnings,
        stats=stats,
        has_db_info=has_db_info,
        has_trace_correlation=has_trace_correlation,
        service_name_used=service_name_used,
    )
