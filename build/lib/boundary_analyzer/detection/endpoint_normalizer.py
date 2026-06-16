from __future__ import annotations

import re
from typing import Any

"""Normalize HTTP endpoint routes and extract tags from span data."""


def _extract_http_method(operation_name: str, tags: list[dict[str, Any]]) -> str:
    """Extract HTTP method from operation_name or tags.

    Priority:
    1. http.method tag (OpenTelemetry standard)
    2. operation_name prefix (e.g., "GET /orders")
    3. Default to empty string
    """
    # Check tags first (OpenTelemetry standard)
    for tag in tags:
        key = tag.get("key", "")
        if key == "http.method":
            return str(tag.get("value", "")).upper()

    # Fallback: parse from operation_name
    operation_upper = operation_name.upper()
    methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
    for method in methods:
        if operation_upper.startswith(method + " "):
            return method

    return ""


def _extract_http_route(operation_name: str, tags: list[dict[str, Any]]) -> str:
    """Extract HTTP route from operation_name or tags.

    Priority:
    1. http.route tag (OpenTelemetry standard, already normalized)
    2. http.target tag (may have dynamic parameters)
    3. http.url tag (full URL, extract path)
    4. operation_name (fallback)
    """
    # Check tags first
    for tag in tags:
        key = tag.get("key", "")
        if key == "http.route":
            return str(tag.get("value", ""))
        if key == "http.target":
            return str(tag.get("value", ""))
        if key == "http.url":
            url = str(tag.get("value", ""))
            # Extract path from URL
            if "://" in url:
                path = url.split("://", 1)[1].split("/", 1)[1] if "/" in url.split("://", 1)[1] else ""
                return "/" + path
            return url

    # Fallback: use operation_name
    # Remove HTTP method prefix if present
    operation_upper = operation_name.upper()
    methods = ["GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"]
    for method in methods:
        if operation_upper.startswith(method + " "):
            return operation_name[len(method) + 1 :]

    return operation_name


def _normalize_dynamic_parameters(route: str) -> str:
    """Normalize dynamic parameters in route.

    Examples:
    /orders/123 → /orders/{id}
    /users/abc/profile → /users/{id}/profile
    /products/456/reviews/789 → /products/{id}/reviews/{review_id}

    Pattern: Replace numeric IDs and UUID-like strings with {id} or {uuid}
    """
    # Replace numeric IDs
    route = re.sub(r"/\d+(?=/|$)", "/{id}", route)

    # Replace UUID-like strings (32 or 36 hex characters)
    route = re.sub(
        r"/[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}(?=/|$)",
        "/{uuid}",
        route,
    )

    # Replace 32-char hex strings (UUID without dashes)
    route = re.sub(r"/[0-9a-fA-F]{32}(?=/|$)", "/{uuid}", route)

    # Note: the previous heuristic replaced any 8+ alphanumeric segment with {id},
    # which caused false positives on legitimate route segments like
    # "employees" (9 chars), "products" (8 chars) or "scenario1" (9 chars, contains digit).
    # The numeric and UUID patterns above correctly handle real dynamic parameters.
    # Non-numeric, non-UUID segments are intentionally kept as-is.

    return route


def build_endpoint_key(
    operation_name: str,
    tags: list[dict[str, Any]],
    normalize: bool = True,
) -> str:
    """Build a normalized endpoint key (``METHOD /route``) from span data.

    Args:
        operation_name: Span operation name
        tags: Span tags list
        normalize: Whether to normalize dynamic parameters

    Returns:
        Endpoint key in format ``METHOD /route``, e.g. ``GET /orders/{id}``
    """
    method = _extract_http_method(operation_name, tags)
    route = _extract_http_route(operation_name, tags)

    if normalize:
        route = _normalize_dynamic_parameters(route)

    if method and route:
        return f"{method} {route}"
    elif route:
        return route
    else:
        return operation_name


def extract_tags_from_span(span: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tags from a span dict, supporting both Jaeger and OTel formats.

    Jaeger format: ``span["tags"]`` as list of ``{key, type, value}``
    OTel format: ``span["attributes"]`` as dict
    """
    # Try tags first (Jaeger)
    if "tags" in span:
        return span["tags"]

    # Try attributes (OTel)
    if "attributes" in span:
        return [{"key": k, "value": v} for k, v in span["attributes"].items()]

    return []
