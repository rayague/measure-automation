from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import requests
from requests import RequestException
import yaml


def _lookback_str(minutes: int) -> str:
    return f"{int(minutes)}m"


def main() -> int:
    settings_path = Path("config/settings.yaml")
    with settings_path.open("r", encoding="utf-8") as f:
        settings = yaml.safe_load(f)

    jaeger_base_url = str(settings["jaeger_base_url"]).rstrip("/")
    service_name = str(settings["service_name"])
    lookback_minutes = int(settings.get("lookback_minutes", 10))
    limit_traces = int(settings.get("limit_traces", 20))
    output_dir = Path(settings.get("output_dir", "data/raw/traces"))

    output_dir.mkdir(parents=True, exist_ok=True)

    if service_name == "YOUR_SERVICE_NAME":
        print("Error: Please set 'service_name' in config/settings.yaml")
        return 2

    services_url = f"{jaeger_base_url}/api/services"
    try:
        services_resp = requests.get(services_url, timeout=30)
        services_resp.raise_for_status()
    except RequestException as e:
        print("Error: I cannot reach Jaeger.")
        print(f"URL: {services_url}")
        print("Check: Is Jaeger running? Is the port correct?")
        print(f"Details: {e}")
        return 3

    url = f"{jaeger_base_url}/api/traces"
    params = {
        "service": service_name,
        "lookback": _lookback_str(lookback_minutes),
        "limit": str(limit_traces),
    }

    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        data = resp.json()
    except RequestException as e:
        print("Error: Jaeger call failed.")
        print(f"URL: {url}")
        print(f"Params: {params}")
        print(f"Details: {e}")
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
            "export_unix_time": int(time.time()),
            "export_file": str(out_file).replace("\\", "/"),
        },
        "jaeger_response": data,
    }

    with out_file.open("w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)

    trace_count = len(data.get("data", []))
    print(f"Saved {trace_count} traces to: {out_file}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
