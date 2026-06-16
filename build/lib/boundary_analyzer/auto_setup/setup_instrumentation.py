from __future__ import annotations

import argparse
import difflib
import json
import logging
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import requests

from boundary_analyzer.auto_setup._detect import (
    JAEGER_GRPC_PORT,
    JAEGER_UI_PORT,
    SUPPORTED_FRAMEWORKS,
    detect_framework,
)
from boundary_analyzer.auto_setup._install import install_packages

logger = logging.getLogger(__name__)


def info(msg: str, *args: object) -> None:
    logger.info(msg, *args)


def ok(msg: str, *args: object) -> None:
    logger.info("✔ " + msg, *args)


def warn(msg: str, *args: object) -> None:
    logger.warning(msg, *args)


def error(msg: str, *args: object) -> None:
    logger.error(msg, *args)


def step(n: int, msg: str) -> None:
    logger.info("\n%s\nSTEP %d: %s\n%s", "─" * 60, n, msg, "─" * 60)


def tip(msg: str, *args: object) -> None:
    logger.info("TIP: " + msg, *args)


def generate_instrumentation_file(
    framework: str,
    project_path: Path,
    service_name: str,
    jaeger_host: str,
) -> Path:
    lang = SUPPORTED_FRAMEWORKS[framework]["lang"]
    templates_dir = Path(__file__).parent

    template_file = templates_dir / f"{framework}_wrapper.{'py' if lang == 'python' else 'js' if lang == 'js' else 'php'}"
    if not template_file.exists():
        ext = "py" if lang == "python" else "js" if lang == "js" else "php"
        template_file = templates_dir / f"generic_wrapper.{ext}"

    if not template_file.exists():
        error(f"Template not found: {template_file}")
        sys.exit(1)

    content = template_file.read_text(encoding="utf-8")
    content = content.replace("{{SERVICE_NAME}}", service_name)
    content = content.replace("{{JAEGER_HOST}}", jaeger_host)
    content = content.replace("{{JAEGER_GRPC_PORT}}", str(JAEGER_GRPC_PORT))

    ext = "py" if lang == "python" else "js" if lang == "js" else "php"
    out_file = project_path / f"otel_instrumentation.{ext}"
    out_file.write_text(content, encoding="utf-8")

    ok(f"Instrumentation file created: {out_file}")
    return out_file


def start_jaeger() -> bool:
    info("Starting Jaeger via Docker...")

    if not shutil.which("docker"):
        warn("Docker not found. Please install Docker and try again.")
        warn("Download: https://www.docker.com/products/docker-desktop")
        tip("You can also start Jaeger manually:\n  docker run -d --name jaeger \\\n    -p 16686:16686 -p 4317:4317 \\\n    jaegertracing/all-in-one:latest")
        return False

    subprocess.run(["docker", "rm", "-f", "jaeger"], capture_output=True)

    cmd = [
        "docker",
        "run",
        "-d",
        "--name",
        "jaeger",
        "-p",
        f"{JAEGER_UI_PORT}:{JAEGER_UI_PORT}",
        "-p",
        f"{JAEGER_GRPC_PORT}:{JAEGER_GRPC_PORT}",
        "-p",
        "6831:6831/udp",
        "-p",
        "14268:14268",
        "jaegertracing/all-in-one:latest",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        error(f"Failed to start Jaeger:\n{result.stderr}")
        return False

    info("Waiting for Jaeger to start...")
    time.sleep(3)
    ok(f"Jaeger is running! Open http://localhost:{JAEGER_UI_PORT} in your browser.")
    return True


INTEGRATION_INSTRUCTIONS = {
    "flask": {
        "file": "app.py  (or wherever you create your Flask app)",
        "code": textwrap.dedent("""\
            from otel_instrumentation import init_tracing
            init_tracing()
        """),
    },
    "fastapi": {
        "file": "main.py  (or wherever you create your FastAPI app)",
        "code": textwrap.dedent("""\
            from fastapi import FastAPI
            from otel_instrumentation import init_tracing
            app = FastAPI()
            init_tracing(app)
        """),
    },
    "django": {
        "file": "manage.py  OR  wsgi.py / asgi.py",
        "code": textwrap.dedent("""\
            from otel_instrumentation import init_tracing
            init_tracing()
        """),
    },
    "djangorest": {
        "file": "manage.py  OR  wsgi.py / asgi.py",
        "code": textwrap.dedent("""\
            from otel_instrumentation import init_tracing
            init_tracing()
        """),
    },
    "starlette": {
        "file": "main.py  (or wherever you create your Starlette app)",
        "code": textwrap.dedent("""\
            from starlette.applications import Starlette
            from otel_instrumentation import init_tracing
            app = Starlette()
            init_tracing(app)
        """),
    },
    "tornado": {
        "file": "main.py  (or wherever you start your Tornado server)",
        "code": textwrap.dedent("""\
            from otel_instrumentation import init_tracing
            init_tracing()
        """),
    },
    "express": {
        "file": "app.js  or  index.js  (before you define any routes)",
        "code": "require('./otel_instrumentation');\n",
    },
    "nextjs": {
        "file": "instrumentation.ts  (create this file at the root of your project)",
        "code": textwrap.dedent("""\
            export async function register() {
              if (process.env.NEXT_RUNTIME === 'nodejs') {
                await import('./otel_instrumentation');
              }
            }
        """),
    },
    "nestjs": {
        "file": "main.ts  (before NestFactory.create(...))",
        "code": "import './otel_instrumentation';\n",
    },
    "laravel": {
        "file": "bootstrap/app.php  (after the Application is created)",
        "code": "require __DIR__.'/../otel_instrumentation.php';\n",
    },
}


def print_integration_instructions(framework: str, instrumentation_file: Path) -> None:
    instructions = INTEGRATION_INSTRUCTIONS.get(framework)
    if not instructions:
        warn("No specific instructions available for this framework.")
        return

    display = SUPPORTED_FRAMEWORKS[framework]["display"]

    logger.info("\n" + "═" * 60)
    logger.info("  ACTION REQUIRED \u2013 Add instrumentation to your %s app", display)
    logger.info("═" * 60)
    logger.info("\n  1. Open this file in your project:")
    logger.info("       %s", instructions["file"])
    logger.info("\n  2. Add these lines of code:\n")
    for line in instructions["code"].splitlines():
        logger.info("       %s", line)
    logger.info("\n  3. Restart your application.")
    logger.info("\n  4. Send some HTTP requests to your app (use your browser or")
    logger.info("       a tool like curl / Postman / Locust).")
    logger.info("\n  5. Come back here and press ENTER to start the analysis.")
    logger.info("═" * 60 + "\n")


def collect_traces(service_name: str, jaeger_host: str, output_path: Path, limit: int = 500) -> bool:
    url = f"http://{jaeger_host}:{JAEGER_UI_PORT}/api/traces?service={service_name}&limit={limit}"

    info("Collecting traces from Jaeger for service '%s'...", service_name)
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()
    except requests.RequestException as e:
        error("Could not reach Jaeger at %s", url)
        error(str(e))
        tip("Make sure Jaeger is running and your app has sent some requests.")
        return False

    traces = data.get("data", [])
    if not traces:
        warn("No traces found. Did you send requests to your app?")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    ok("Collected %d trace(s). Saved to: %s", len(traces), output_path)
    return True


def run_scom_analysis(traces_path: Path, service_name: str, output_dir: Path) -> None:
    info("Running SCOM cohesion analysis...")

    possible_paths = [
        Path(__file__).parent.parent / "pipeline" / "run_pipeline.py",
        Path(__file__).parent.parent / "run_pipeline.py",
        Path(__file__).parent.parent.parent / "run_pipeline.py",
    ]

    analyzer = None
    for p in possible_paths:
        if p.exists():
            analyzer = p
            break

    if analyzer is None:
        warn("Could not find the SCOM analysis pipeline script (run_pipeline.py).")
        warn("Please run the analysis manually with:")
        tip("python run_pipeline.py --traces %s --service %s", traces_path, service_name)
        return

    cmd = [
        sys.executable,
        str(analyzer),
        "--traces",
        str(traces_path),
        "--service",
        service_name,
        "--output",
        str(output_dir),
    ]

    result = subprocess.run(cmd, text=True, encoding="utf-8", errors="replace")
    if result.returncode == 0:
        ok("SCOM analysis complete! Check the output directory for the report.")
    else:
        error("SCOM analysis failed. Check the logs above.")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(
        description="Auto-setup OpenTelemetry instrumentation for microservice analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          python setup_instrumentation.py --project-path ./my-service
          python setup_instrumentation.py --project-path ./my-service --framework fastapi
          python setup_instrumentation.py --project-path ./my-service \\
            --service-name order-service --jaeger-host 192.168.1.10
          python setup_instrumentation.py --project-path ./my-service --no-jaeger
          python setup_instrumentation.py --project-path ./my-service --methods ALL
        """),
    )

    ap.add_argument("--project-path", required=True, help="Path to the microservice project to instrument")
    ap.add_argument(
        "--framework",
        default="",
        choices=list(SUPPORTED_FRAMEWORKS.keys()) + [""],
        help="Force a specific framework (default: auto-detect)",
    )
    ap.add_argument("--service-name", default="", help="Name of the service (default: folder name)")
    ap.add_argument("--jaeger-host", default="localhost", help="Host where Jaeger is running (default: localhost)")
    ap.add_argument("--no-jaeger", action="store_true", help="Skip starting Jaeger (use if it is already running)")
    ap.add_argument("--no-install", action="store_true", help="Skip package installation (use if already installed)")
    ap.add_argument("--traces-output", default="", help="Where to save collected traces JSON (default: ./traces/)")
    ap.add_argument("--trace-limit", type=int, default=500, help="Maximum number of traces to collect (default: 500)")
    ap.add_argument(
        "--llm",
        action="store_true",
        help="Use AI (LLM) to generate instrumentation instead of templates. Requires OPENROUTER_API_KEY env var.",
    )

    args = ap.parse_args(argv)

    project_path = Path(args.project_path).resolve()
    if not project_path.exists():
        error("Project path not found: %s", project_path)
        sys.exit(1)

    service_name = args.service_name or project_path.name
    traces_dir = Path(args.traces_output) if args.traces_output else project_path / "traces"
    traces_file = traces_dir / f"{service_name}_traces.json"

    logger.info("\n" + "═" * 60)
    logger.info("  OpenTelemetry Auto-Setup \u2014 Boundary Analyzer")
    logger.info("═" * 60)

    step(1, "Detecting framework")

    if args.framework:
        framework = args.framework
        info("Framework forced by user: %s", SUPPORTED_FRAMEWORKS[framework]["display"])
    else:
        framework = detect_framework(project_path)
        if framework == "unknown":
            error("Could not detect the framework automatically.")
            error("Please use --framework to specify one of: " + ", ".join(SUPPORTED_FRAMEWORKS.keys()))
            sys.exit(1)
        ok("Detected framework: %s", SUPPORTED_FRAMEWORKS[framework]["display"])

    lang = SUPPORTED_FRAMEWORKS[framework]["lang"]
    info("Language: %s | Service: %s", lang.upper(), service_name)

    step(2, "Installing OpenTelemetry packages")

    if args.no_install:
        info("Skipping package installation (--no-install flag set).")
    else:
        install_packages(framework, project_path)

    step(3, "Generating instrumentation file")

    if args.llm and lang == "python":
        info("Using AI (LLM) to generate instrumentation...")
        from boundary_analyzer.llm import generate_instrumentation as llm_generate

        llm_code = llm_generate(project_path, jaeger_host=args.jaeger_host)
        if llm_code:
            main_file = project_path / "app" / "main.py"
            if not main_file.exists():
                main_file = project_path / "main.py"
            if main_file.exists():
                original = main_file.read_text(encoding="utf-8")
                if original == llm_code:
                    ok("Code already instrumented \u2014 no changes needed.")
                    instrumentation_file = main_file
                else:
                    diff = difflib.unified_diff(
                        original.splitlines(keepends=True),
                        llm_code.splitlines(keepends=True),
                        fromfile=str(main_file),
                        tofile=str(main_file) + " (instrumented)",
                    )
                    logger.info("Proposed changes:")
                    for line in diff:
                        logger.info("  %s", line.rstrip())
                    try:
                        answer = input("Apply these changes? [y/N] ").strip().lower()
                    except (KeyboardInterrupt, EOFError):
                        answer = "n"

                    if answer == "y":
                        backup = main_file.with_suffix(".py.bak")
                        main_file.rename(backup)
                        main_file.write_text(llm_code, encoding="utf-8")
                        ok("Instrumentation written to %s", main_file)
                        ok("Original backed up to %s", backup)
                        instrumentation_file = main_file
                    else:
                        info("Changes not applied.")
                        llm_code = None
            else:
                warn("Could not find main.py to write LLM-generated code.")
                llm_code = None
        else:
            warn("AI generation failed (syntax error, LLM error, or no API key).")

        if llm_code is None:
            warn("Falling back to standard template generation...")
            instrumentation_file = generate_instrumentation_file(framework, project_path, service_name, args.jaeger_host)
    else:
        instrumentation_file = generate_instrumentation_file(framework, project_path, service_name, args.jaeger_host)

    step(4, "Starting Jaeger")

    if args.no_jaeger:
        info("Skipping Jaeger start (--no-jaeger flag set).")
    else:
        started = start_jaeger()
        if not started:
            warn("Jaeger is not running (or I could not start it).")
            tip("You can continue if Jaeger is already running elsewhere. If not, start Jaeger and run this command again, or use --no-jaeger.")

    step(5, "Integration instructions")
    print_integration_instructions(framework, instrumentation_file)

    try:
        input("  Press ENTER when your app is restarted and you have sent some traffic...")
    except (KeyboardInterrupt, EOFError):
        logger.info("\n")
        warn("Setup interrupted. Run the script again when ready.")
        sys.exit(0)

    step(6, "Collecting traces from Jaeger")

    success = collect_traces(service_name, args.jaeger_host, traces_file, args.trace_limit)
    if not success:
        warn("Could not collect traces. Try again after sending more traffic.")
        sys.exit(1)

    step(7, "Running SCOM cohesion analysis")

    output_dir = project_path / "scom_report"
    run_scom_analysis(traces_file, service_name, output_dir)

    logger.info("\n" + "═" * 60)
    ok("All done!")
    logger.info("  Traces saved : %s", traces_file)
    logger.info("  Report dir  : %s", output_dir)
    logger.info("  Jaeger UI   : http://%s:%d", args.jaeger_host, JAEGER_UI_PORT)
    logger.info("═" * 60 + "\n")


if __name__ == "__main__":
    main()
