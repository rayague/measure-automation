from __future__ import annotations

"""Threshold rules and suspicious-service detection for SCOM scores."""


def get_threshold(settings: dict | None = None) -> float:
    """Get the SCOM threshold for flagging suspicious services.

    Defaults to 0.5; can be overridden via ``settings['scom_threshold']``.
    """
    if settings:
        return float(settings.get("scom_threshold", 0.5))
    return 0.5


def is_suspicious(scom_score: float, threshold: float) -> bool:
    """Return True if the SCOM score is below the threshold (i.e. suspicious)."""
    return scom_score < threshold
