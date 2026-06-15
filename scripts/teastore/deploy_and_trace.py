"""Deploy TeaStore with OpenTelemetry Java agent, generate traffic, export traces.

Usage:
    python scripts/teastore/deploy_and_trace.py --output data/teastore_run_001

Workflow:
    1. Download OTel Java agent JAR if missing
    2. Start TeaStore + Jaeger via docker compose
    3. Wait for all services to be ready
    4. Generate load (HTTP requests via Python requests)
    5. Export traces from Jaeger API
    6. Run SCOM analysis pipeline
    7. Clean up (optional)
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import sys
import time
import webbrowser
from argparse import ArgumentParser
from pathlib import Path

import requests

logging.basicConfig(level=logging.INFO, format="[%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

OTEL_AGENT_VERSION = "v2.14.0"
OTEL_AGENT_JAR = f"opentelemetry-javaagent-{OTEL_AGENT_VERSION}.jar"
OTEL_AGENT_URL = (
    f"https://github.com/open-telemetry/opentelemetry-java-instrumentation/"
    f"releases/download/{OTEL_AGENT_VERSION}/opentelemetry-javaagent.jar"
)
COMPOSE_FILE = Path(__file__).resolve().parent / "docker-compose-otel.yaml"
COMPOSE_DIR = COMPOSE_FILE.parent
AGENT_DIR = COMPOSE_DIR / "otel-agent"
AGENT_PATH = AGENT_DIR / OTEL_AGENT_JAR
AGENT_SYMLINK = AGENT_DIR / "opentelemetry-javaagent.jar"

WEBUI_URL = "http://localhost:8080/tools.descartes.teastore.webui/"
JAEGER_API = "http://localhost:16686/api/traces?service=teastore-{service}&limit=1000"



def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)


def download_otel_agent() -> Path:
    AGENT_DIR.mkdir(parents=True, exist_ok=True)

    if AGENT_SYMLINK.exists():
        logger.info(f"OTel agent already exists: {AGENT_SYMLINK}")
        return AGENT_SYMLINK

    logger.info(f"Downloading OpenTelemetry Java agent {OTEL_AGENT_VERSION}...")
    logger.info(f"  URL: {OTEL_AGENT_URL}")
    logger.info(f"  -> {AGENT_PATH}")

    try:
        resp = requests.get(OTEL_AGENT_URL, stream=True, timeout=120)
        resp.raise_for_status()
        with open(AGENT_PATH, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
    except Exception as e:
        logger.error(f"Failed to download agent: {e}")
        logger.error("Please download manually from:")
        logger.error(f"  {OTEL_AGENT_URL}")
        logger.error(f"  Save to: {AGENT_PATH}")
        sys.exit(1)

    logger.info(f"Downloaded {AGENT_PATH.stat().st_size / 1024 / 1024:.1f} MB")

    shutil.copy2(AGENT_PATH, AGENT_SYMLINK)
    logger.info(f"Copied to: {AGENT_SYMLINK}")

    return AGENT_SYMLINK


def docker_compose_up() -> None:
    logger.info("Starting TeaStore + Jaeger via docker compose...")
    result = _run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "up", "-d"],
        cwd=COMPOSE_DIR,
    )
    if result.stdout:
        logger.info(result.stdout.strip())
    if result.stderr:
        logger.warning(result.stderr.strip())
    logger.info("Containers started")


def wait_for_services(timeout: int = 300, interval: int = 5) -> float:
    logger.info(f"Waiting for TeaStore WebUI at {WEBUI_URL} (timeout={timeout}s)...")
    start = time.time()
    while time.time() - start < timeout:
        try:
            resp = requests.get(WEBUI_URL, timeout=5)
            if resp.status_code == 200:
                elapsed = time.time() - start
                logger.info(f"WebUI ready after {elapsed:.0f}s")
                return elapsed
        except (requests.RequestException, OSError):
            pass
        time.sleep(interval)
    raise TimeoutError(f"TeaStore WebUI not ready after {timeout}s")


def generate_traffic(duration_sec: int = 60, interval_sec: float = 2.0) -> None:
    logger.info(f"Generating traffic for {duration_sec}s (request every {interval_sec}s)...")

    paths = [
        "/tools.descartes.teastore.webui/",
        "/tools.descartes.teastore.webui/category/1",
        "/tools.descartes.teastore.webui/category/2",
        "/tools.descartes.teastore.webui/product/1",
        "/tools.descartes.teastore.webui/product/2",
        "/tools.descartes.teastore.webui/cart",
        "/tools.descartes.teastore.webui/login",
        "/tools.descartes.teastore.webui/",
        "/tools.descartes.teastore.webui/category/3",
        "/tools.descartes.teastore.webui/product/3",
    ]

    start = time.time()
    sent = 0
    failed = 0
    while time.time() - start < duration_sec:
        path = paths[sent % len(paths)]
        url = f"http://localhost:8080{path}"
        try:
            requests.get(url, timeout=5)
            sent += 1
            if sent % 10 == 0:
                logger.info(f"  ... {sent} requests sent ({failed} failed)")
        except (requests.RequestException, OSError):
            failed += 1
        time.sleep(interval_sec)

    logger.info(f"Traffic done: {sent} sent, {failed} failed")


def export_traces(output_dir: Path, services: list[str] | None = None) -> dict[str, Path]:
    if services is None:
        services = [
            "teastore-registry", "teastore-persistence", "teastore-auth",
            "teastore-image", "teastore-recommender", "teastore-webui",
        ]

    traces_dir = output_dir / "raw" / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    logger.info("Exporting traces from Jaeger...")
    exported: dict[str, Path] = {}

    for svc in services:
        url = JAEGER_API.format(service=svc)
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            data = resp.json()
            traces = data.get("data", [])
            if not traces:
                logger.warning(f"  {svc}: no traces found")
                continue
            file_path = traces_dir / f"{svc}.json"
            with open(file_path, "w") as f:
                json.dump({"data": traces}, f, indent=2)
            span_count = sum(len(t.get("spans", [])) for t in traces)
            logger.info(f"  {svc}: {len(traces)} traces, {span_count} spans -> {file_path.name}")
            exported[svc] = file_path
        except (requests.RequestException, OSError, json.JSONDecodeError) as e:
            logger.warning(f"  {svc}: export failed: {e}")

    total = sum(len(json.loads(p.read_bytes()).get("data", [])) for p in traces_dir.glob("*.json"))
    logger.info(f"Total: {len(exported)} services, {total} traces in {traces_dir}")
    return exported


def run_scom_analysis(output_dir: Path, threshold: float = 0.5, skip_no_db: bool = True) -> bool:
    logger.info("Running SCOM analysis pipeline...")
    sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "src"))

    try:
        from boundary_analyzer.pipeline.run_pipeline import run_pipeline
    except ImportError:
        logger.error("Cannot import boundary_analyzer. Run from project root or install package.")
        return False

    rc = run_pipeline(
        traces=output_dir / "raw" / "traces",
        output_dir=output_dir,
        scom_method="weighted",
        threshold_method="fixed",
        fixed_threshold=threshold,
        exclude_services=["teastore-registry"],
        exclude_health_routes=True,
        exclude_http_client_spans=True,
        exclude_unknown_endpoint=True,
        skip_no_db_services=skip_no_db,
    )

    if rc == 0:
        logger.info("SCOM analysis complete")
        return True
    logger.error(f"Pipeline returned exit code {rc}")
    return False


def docker_compose_down() -> None:
    logger.info("Stopping containers...")
    result = _run(
        ["docker", "compose", "-f", str(COMPOSE_FILE), "down", "-v"],
        cwd=COMPOSE_DIR,
    )
    if result.stdout:
        logger.info(result.stdout.strip())
    logger.info("Containers stopped and cleaned up")


def open_jaeger_ui() -> None:
    webbrowser.open("http://localhost:16686")


def run_teastore(
    output: str = "data/teastore_run",
    duration: int = 60,
    wait: int = 300,
    threshold: float = 0.5,
    skip_no_db: bool = True,
    cleanup: bool = True,
    skip_pipeline: bool = False,
    jaeger_ui: bool = False,
    download_only: bool = False,
) -> int:
    output_dir = Path(output)

    if jaeger_ui:
        cleanup = False

    download_otel_agent()

    if download_only:
        logger.info("Agent downloaded. Exiting (--download-only).")
        return 0

    docker_compose_up()

    try:
        wait_for_services(timeout=wait)

        if jaeger_ui:
            open_jaeger_ui()

        generate_traffic(duration_sec=duration)

        logger.info("Waiting 5s for span flush...")
        time.sleep(5)

        export_traces(output_dir)

        if not skip_pipeline:
            run_scom_analysis(output_dir, threshold=threshold, skip_no_db=skip_no_db)
        else:
            logger.info("Skipping SCOM analysis (--skip-pipeline)")

    except KeyboardInterrupt:
        logger.warning("Interrupted by user")
    except TimeoutError as e:
        logger.error(str(e))
        return 1
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        import traceback
        traceback.print_exc()
        return 1
    finally:
        if cleanup:
            docker_compose_down()
        else:
            logger.info("Containers left running (--no-cleanup or --jaeger-ui)")

    logger.info("Done")
    return 0


def main() -> int:
    ap = ArgumentParser(description="Deploy TeaStore with OTel, generate traffic, export traces, run SCOM")
    ap.add_argument("--output", default="data/teastore_run",
                    help="Output directory for traces and SCOM results")
    ap.add_argument("--duration", type=int, default=60,
                    help="Traffic generation duration in seconds (default: 60)")
    ap.add_argument("--wait", type=int, default=300,
                    help="Max wait time for TeaStore startup in seconds (default: 300)")
    ap.add_argument("--threshold", type=float, default=0.5,
                    help="SCOM fixed threshold (default: 0.5)")
    ap.add_argument("--no-skip-no-db", action="store_false", dest="skip_no_db",
                    help="Include services with no DB tables in SCOM ranking")
    ap.add_argument("--no-cleanup", action="store_false", dest="cleanup",
                    help="Do NOT stop containers after finishing")
    ap.add_argument("--skip-pipeline", action="store_true",
                    help="Skip SCOM analysis pipeline (export traces only)")
    ap.add_argument("--jaeger-ui", action="store_true",
                    help="Open Jaeger UI in browser (implies --no-cleanup)")
    ap.add_argument("--download-only", action="store_true",
                    help="Only download the OTel agent, do not deploy")

    args = ap.parse_args()
    return run_teastore(
        output=args.output,
        duration=args.duration,
        wait=args.wait,
        threshold=args.threshold,
        skip_no_db=args.skip_no_db,
        cleanup=args.cleanup,
        skip_pipeline=args.skip_pipeline,
        jaeger_ui=args.jaeger_ui,
        download_only=args.download_only,
    )


if __name__ == "__main__":
    raise SystemExit(main())
