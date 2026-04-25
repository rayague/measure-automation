from __future__ import annotations


def get_threshold(settings: dict | None = None) -> float:
    """Get the SCOM threshold for flagging suspicious services.
    
    Default threshold: 0.5
    You can override this in settings.yaml.
    """
    if settings:
        return float(settings.get("scom_threshold", 0.5))
    return 0.5


def is_suspicious(scom_score: float, threshold: float) -> bool:
    """Check if a service is suspicious based on SCOM score.
    
    A service is suspicious if SCOM < threshold.
    """
    return scom_score < threshold
