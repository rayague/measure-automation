from __future__ import annotations

import json
import logging
import re
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
logger.setLevel(logging.INFO)

OTEL_AGENT_VERSION = "v2.14.0"
OTEL_AGENT_JAR = f"opentelemetry-javaagent-{OTEL_AGENT_VERSION}.jar"
OTEL_AGENT_URL = f"https://github.com/open-telemetry/opentelemetry-java-instrumentation/releases/download/{OTEL_AGENT_VERSION}/opentelemetry-javaagent.jar"
_CACHE_DIR = Path.home() / ".cache" / "boundary_analyzer"
AGENT_DIR = _CACHE_DIR / "otel-agent"
AGENT_PATH = AGENT_DIR / OTEL_AGENT_JAR
AGENT_SYMLINK = AGENT_DIR / "opentelemetry-javaagent.jar"

_COMPOSE_SRC = Path(__file__).resolve().parent / "teastore" / "docker-compose-otel.yaml"
_PATCHED_COMPOSE = _CACHE_DIR / "docker-compose-patched.yaml"
COMPOSE_DIR = _COMPOSE_SRC.parent.parent

WEBUI_URL = "http://localhost:8080/tools.descartes.teastore.webui/"
JAEGER_API = "http://localhost:16686/api/traces?service={service}&limit=1000"


def _run(cmd: list[str], cwd: Path | None = None, check: bool = True) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, check=check, capture_output=True, text=True)
    except subprocess.CalledProcessError as e:
        details = e.stderr.strip() if e.stderr else e.stdout.strip() if e.stdout else str(e)
        logger.error(f"Command failed: {' '.join(cmd)}")
        logger.error(f"  -> {details}")
        raise


def _check_docker_healthy() -> bool:
    """Return True if Docker daemon is reachable and functional."""
    try:
        result = subprocess.run(
            ["docker", "info", "--format", "{{.ServerVersion}}"],
            capture_output=True, text=True, timeout=15,
        )
        return result.returncode == 0 and bool(result.stdout.strip())
    except subprocess.TimeoutExpired:
        logger.error("Docker did not respond within 15s.")
        return False
    except FileNotFoundError:
        logger.error("Docker executable not found. Is Docker installed?")
        return False
    except Exception:
        return False


def _hint_docker_restart() -> None:
    logger.error("")
    logger.error("Docker Desktop is not responding. To fix this:")
    logger.error("  1. Right-click the Docker icon in the system tray")
    logger.error("  2. Select Restart")
    logger.error("  Or run in an admin terminal:")
    logger.error("    wsl --shutdown")
    logger.error("    start \"\" \"C:\\Program Files\\Docker\\Docker\\Docker Desktop.exe\"")
    logger.error("  3. Wait for Docker to show \"Engine running\"")
    logger.error("  4. Run mba teastore again")
    logger.error("")


def _docker_cleanup_teastore() -> None:
    """Remove leftover teastore containers/networks from previous runs."""
    try:
        subprocess.run(
            ["docker", "compose", "-f", str(_COMPOSE_SRC), "down", "-v"],
            cwd=COMPOSE_DIR, capture_output=True, text=True, timeout=30,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["docker", "container", "prune", "-f", "--filter", "name=boundary_analyzer"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass
    try:
        subprocess.run(
            ["docker", "network", "prune", "-f", "--filter", "name=boundary_analyzer"],
            capture_output=True, text=True, timeout=15,
        )
    except Exception:
        pass


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


def _prepare_compose_file() -> Path:
    """Patch compose YAML with absolute agent volume path, return path to patched file."""
    text = _COMPOSE_SRC.read_text(encoding="utf-8")
    abs_agent = str(AGENT_DIR.resolve())
    text = text.replace("./otel-agent:/otel", f"{abs_agent}:/otel")
    _CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _PATCHED_COMPOSE.write_text(text, encoding="utf-8")
    logger.info(f"Patched compose file: agent volume -> {abs_agent}")
    return _PATCHED_COMPOSE


def docker_compose_up() -> None:
    if not _check_docker_healthy():
        _hint_docker_restart()
        raise RuntimeError("Docker is not healthy. Cannot start TeaStore.")

    _docker_cleanup_teastore()

    compose_file = _prepare_compose_file()
    logger.info("Starting TeaStore + Jaeger via docker compose...")
    try:
        result = _run(
            ["docker", "compose", "-f", str(compose_file), "up", "-d"],
            cwd=COMPOSE_DIR,
        )
    except subprocess.CalledProcessError as e:
        stderr = e.stderr or ""
        if "500 Internal Server Error" in stderr:
            logger.error("Docker Desktop returned an internal error (HTTP 500).")
            logger.error("The Docker daemon may be overloaded or in a broken state.")
            _hint_docker_restart()
        elif "image" in stderr and "not found" in stderr.lower():
            logger.error("One or more TeaStore Docker images could not be pulled.")
            logger.error("Run: docker compose -f \"{}\" pull".format(str(_COMPOSE_SRC)))
        raise

    if result.stdout:
        logger.info(result.stdout.strip())
    if result.stderr:
        logger.warning(result.stderr.strip())
    logger.info("Containers started")


def wait_for_services(timeout: int = 900, interval: int = 10) -> float:
    logger.info(f"Waiting for TeaStore WebUI at {WEBUI_URL} (timeout={timeout}s)...")
    start = time.time()
    next_log = 60
    consecutive_ok = 0
    while time.time() - start < timeout:
        try:
            resp = requests.get(WEBUI_URL, timeout=20)
            if resp.status_code == 200:
                consecutive_ok += 1
                if consecutive_ok >= 3:
                    elapsed = time.time() - start
                    logger.info(f"WebUI ready after {elapsed:.0f}s (3/3 OK)")
                    logger.info("Waiting 30s for services to stabilize...")
                    time.sleep(30)
                    return elapsed
            else:
                consecutive_ok = 0
        except requests.ReadTimeout:
            consecutive_ok = 0
        except (requests.RequestException, OSError):
            consecutive_ok = 0
        elapsed = time.time() - start
        if elapsed >= next_log:
            logger.info(f"  ... still waiting ({elapsed:.0f}s / {timeout}s, consecutive_ok={consecutive_ok})")
            next_log += 60
        time.sleep(interval)
    raise TimeoutError(f"TeaStore WebUI not ready after {timeout}s")


#: TeaStore's WebUI is a set of plain Java Servlets mapped to *exact* paths
#: (``@WebServlet("/category")``, ``@WebServlet("/product")``) that read the
#: entity ID from a **query string** parameter (``?category=<id>``,
#: ``?id=<id>``), not from a path segment. Requesting e.g. ``/category/1``
#: therefore 404s — no servlet is mapped to that path — and the WebUI never
#: calls persistence-service to actually browse the catalog. Verified against
#: DescartesResearch/TeaStore's CategoryServlet.java and ProductServlet.java.
_CATEGORY_LINK_RE = re.compile(r"category\?category=(\d+)")
_PRODUCT_LINK_RE = re.compile(r"product\?id=(\d+)")


def _discover_category_ids(base_url: str, timeout: int = 15) -> list[int]:
    """Discover real category IDs by parsing the links on the TeaStore index page.

    The index page always lists every category in its navigation bar
    (``IndexServlet`` fetches all categories unconditionally), so this works
    regardless of how the database was seeded/sized.
    """
    try:
        resp = requests.get(f"{base_url}/", timeout=timeout)
        resp.raise_for_status()
        return sorted({int(m) for m in _CATEGORY_LINK_RE.findall(resp.text)})
    except (requests.RequestException, OSError):
        return []


def _discover_product_ids(base_url: str, category_id: int, timeout: int = 15) -> list[int]:
    """Discover real product IDs by parsing the product listing of one category page."""
    try:
        resp = requests.get(f"{base_url}/category", params={"category": category_id, "page": 1}, timeout=timeout)
        resp.raise_for_status()
        return sorted({int(m) for m in _PRODUCT_LINK_RE.findall(resp.text)})
    except (requests.RequestException, OSError):
        return []


def _build_traffic_paths(base_url: str) -> list[str]:
    """Build request paths using real category/product IDs discovered from the
    running catalog, instead of guessed path segments the WebUI doesn't route.

    Falls back to browsing only the static pages (index/login/cart) if no
    category could be discovered (e.g. an empty or misconfigured database) —
    still valid traffic, just without category/product coverage.
    """
    category_ids = _discover_category_ids(base_url)

    product_ids: list[int] = []
    for cid in category_ids[:3]:
        product_ids.extend(_discover_product_ids(base_url, cid)[:3])

    paths = ["/tools.descartes.teastore.webui/", "/tools.descartes.teastore.webui/login"]
    for cid in category_ids[:5]:
        paths.append(f"/tools.descartes.teastore.webui/category?category={cid}&page=1")
    for pid in product_ids[:5]:
        paths.append(f"/tools.descartes.teastore.webui/product?id={pid}")
    paths.append("/tools.descartes.teastore.webui/cart")

    if not category_ids:
        logger.warning(
            "Could not discover any TeaStore category IDs from the index page — "
            "the catalog may be empty or the database still generating. Falling back "
            "to static pages only (index/login/cart); category/product traffic will be skipped."
        )
    else:
        logger.info(f"Discovered {len(category_ids)} categor{'y' if len(category_ids) == 1 else 'ies'} and {len(product_ids)} product(s) to exercise.")

    return paths


def generate_traffic(duration_sec: int = 60, interval_sec: float = 2.0, base_url: str = "http://localhost:8080") -> None:
    logger.info(f"Generating traffic for {duration_sec}s (request every {interval_sec}s)...")

    paths = _build_traffic_paths(base_url)

    start = time.time()
    sent = 0
    failed = 0
    while time.time() - start < duration_sec:
        path = paths[sent % len(paths)]
        url = f"{base_url}{path}"
        try:
            requests.get(url, timeout=15)
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
            "teastore-registry",
            "teastore-persistence",
            "teastore-auth",
            "teastore-image",
            "teastore-recommender",
            "teastore-webui",
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
    compose_file = _PATCHED_COMPOSE if _PATCHED_COMPOSE.exists() else _COMPOSE_SRC
    logger.info("Stopping containers...")
    result = _run(
        ["docker", "compose", "-f", str(compose_file), "down", "-v"],
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
    wait: int = 900,
    threshold: float = 0.5,
    skip_no_db: bool = True,
    cleanup: bool = True,
    skip_pipeline: bool = False,
    jaeger_ui: bool = False,
    download_only: bool = False,
    prune: bool = False,
) -> int:
    output_dir = Path(output)

    if jaeger_ui:
        cleanup = False

    if prune:
        logger.info("Pruning leftover teastore containers...")
        _docker_cleanup_teastore()

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
    except RuntimeError as e:
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
    ap.add_argument("--output", default="data/teastore_run", help="Output directory for traces and SCOM results")
    ap.add_argument("--duration", type=int, default=60, help="Traffic generation duration in seconds (default: 60)")
    ap.add_argument("--wait", type=int, default=900, help="Max wait time for TeaStore startup in seconds (default: 900)")
    ap.add_argument("--threshold", type=float, default=0.5, help="SCOM fixed threshold (default: 0.5)")
    ap.add_argument("--no-skip-no-db", action="store_false", dest="skip_no_db", help="Include services with no DB tables in SCOM ranking")
    ap.add_argument("--no-cleanup", action="store_false", dest="cleanup", help="Do NOT stop containers after finishing")
    ap.add_argument("--skip-pipeline", action="store_true", help="Skip SCOM analysis pipeline (export traces only)")
    ap.add_argument("--jaeger-ui", action="store_true", help="Open Jaeger UI in browser (implies --no-cleanup)")
    ap.add_argument("--download-only", action="store_true", help="Only download the OTel agent, do not deploy")

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
