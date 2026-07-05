from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from requests import RequestException

from boundary_analyzer.settings_loader import get_settings_path, get_traces_dir, load_settings

logger = logging.getLogger(__name__)

CACHE_FILE = ".trace_cache.json"
CACHE_TTL_MULTIPLIER = 0.8
ENV_SKIP_CACHE = "BOUNDARY_ANALYZER_SKIP_CACHE"


def _lookback_str(minutes: int) -> str:
    return f"{int(minutes)}m"


def _get_cache_path(output_dir: Path) -> Path:
    return output_dir / CACHE_FILE


def _load_cache(output_dir: Path) -> dict[str, Any]:
    cache_path = _get_cache_path(output_dir)
    if cache_path.exists():
        return json.loads(cache_path.read_text())
    return {}


def _save_cache(output_dir: Path, cache: dict[str, Any]) -> None:
    _get_cache_path(output_dir).write_text(json.dumps(cache, indent=2))


def _is_cache_valid(cache: dict[str, Any], service_name: str, lookback_minutes: int) -> bool:
    entry = cache.get("services", {}).get(service_name)
    if not entry:
        return False
    fetched_at = entry.get("fetched_at", 0)
    ttl_seconds = int(lookback_minutes * 60 * CACHE_TTL_MULTIPLIER)
    return time.time() - fetched_at < ttl_seconds


def main() -> int:
    settings_path = get_settings_path()
    settings = load_settings(settings_path)

    jaeger_base_url = settings.jaeger_base_url.rstrip("/")
    service_name = settings.service_name
    lookback_minutes = settings.lookback_minutes
    limit_traces = settings.limit_traces
    output_dir = get_traces_dir(settings)

    output_dir.mkdir(parents=True, exist_ok=True)

    if service_name == "YOUR_SERVICE_NAME":
        logger.error("Error: Please set 'service_name' in settings.yaml")
        logger.error("Settings file: %s", settings_path)
        return 2

    # ── Cache check ─────────────────────────────────────────────────────────
    skip_cache = os.environ.get(ENV_SKIP_CACHE, "").strip() == "1"
    if not skip_cache:
        cache = _load_cache(output_dir)
        if _is_cache_valid(cache, service_name, lookback_minutes):
            logger.info(
                "Using cached traces for '%s' (fetched within last %d min). Set %s=1 to force re-fetch.",
                service_name,
                lookback_minutes,
                ENV_SKIP_CACHE,
            )
            return 0
    else:
        cache = {}

    services_url = f"{jaeger_base_url}/api/services"
    try:
        services_resp = requests.get(services_url, timeout=30)
        services_resp.raise_for_status()
    except RequestException as e:
        logger.error("Error: I cannot reach Jaeger.")
        logger.error("URL: %s", services_url)
        logger.error("Check: Is Jaeger running? Is the port correct?")
        logger.error("Details: %s", e)
        return 3

    url = f"{jaeger_base_url}/api/traces"
    all_traces: list[dict[str, Any]] = []
    total_traces = 0
    offset = 0

    try:
        while True:
            params = {
                "service": service_name,
                "lookback": _lookback_str(lookback_minutes),
                "limit": str(limit_traces),
                "offset": str(offset),
            }
            resp = requests.get(url, params=params, timeout=60)
            resp.raise_for_status()
            data = resp.json()

            batch = data.get("data", [])
            all_traces.extend(batch)
            total_traces = data.get("total", 0) or len(all_traces)
            current_limit = data.get("limit", limit_traces)

            if not batch or offset + current_limit >= total_traces:
                break
            offset += current_limit
    except RequestException as e:
        logger.error("Error: Jaeger call failed.")
        logger.error("URL: %s", url)
        logger.error("Details: %s", e)
        return 4

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_service = service_name.replace("/", "_").replace("\\", "_").replace(" ", "_")
    out_file = output_dir / f"jaeger_traces_{safe_service}_{ts}.json"

    payload = {
        "export_meta": {
            "jaeger_base_url": jaeger_base_url,
            "service": service_name,
            "lookback_minutes": lookback_minutes,
            "limit_traces": limit_traces,
            "total_traces": total_traces,
            "export_unix_time": int(time.time()),
            "export_file": str(out_file).replace("\\", "/"),
        },
        "jaeger_response": {"data": all_traces, "total": total_traces, "limit": limit_traces},
    }

    with out_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    # ── Update cache ─────────────────────────────────────────────────────────
    cache.setdefault("services", {})[service_name] = {
        "trace_ids": [t.get("traceID") for t in all_traces],
        "fetched_at": int(time.time()),
        "lookback_minutes": lookback_minutes,
        "total_traces": total_traces,
    }
    _save_cache(output_dir, cache)

    logger.info("Saved %d/%d traces to: %s", len(all_traces), total_traces, out_file)

    if not all_traces:
        logger.error("No traces collected for service '%s' in the lookback window.", service_name)
        return 5

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
