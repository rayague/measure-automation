#!/usr/bin/env python3
"""
setup_instrumentation.py
========================
Automatically sets up OpenTelemetry tracing on any microservice project.

SUPPORTED FRAMEWORKS:
  Python  : Flask, FastAPI, Django, Django REST Framework, Starlette, Tornado
  PHP     : Laravel
  JS / TS : Express.js, Next.js, Nest.js

WHAT THIS SCRIPT DOES AUTOMATICALLY:
  1. Detects the language and framework of your project
  2. Installs the required OpenTelemetry packages
  3. Generates an instrumentation file inside your project
  4. Starts Jaeger (the trace collector) using Docker
  5. Tells you exactly what 2-3 lines to add in your app
  6. Waits for you to restart your app
  7. Collects the traces and runs the SCOM analysis

HOW TO RUN:
  python setup_instrumentation.py --project-path /path/to/your/project

  # With a custom Jaeger host:
  python setup_instrumentation.py --project-path ./my-service --jaeger-host localhost

  # Force a specific framework (skip auto-detection):
  python setup_instrumentation.py --project-path ./my-service --framework fastapi
"""

import os
import sys
import json
import time
import shutil
import argparse
import platform
import subprocess
import textwrap
from pathlib import Path

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 – CONSTANTS
# ─────────────────────────────────────────────────────────────────────────────

# We are running on Windows if this is True
IS_WINDOWS = platform.system() == "Windows"

# Default Jaeger ports
JAEGER_GRPC_PORT  = 4317   # OpenTelemetry sends traces here
JAEGER_UI_PORT    = 16686  # You open this in your browser to see traces

# All frameworks we support, grouped by language
SUPPORTED_FRAMEWORKS = {
    # ── Python ────────────────────────────────────────────────────────────────
    "flask":      {"lang": "python", "display": "Flask"},
    "fastapi":    {"lang": "python", "display": "FastAPI"},
    "django":     {"lang": "python", "display": "Django"},
    "djangorest": {"lang": "python", "display": "Django REST Framework"},
    "starlette":  {"lang": "python", "display": "Starlette"},
    "tornado":    {"lang": "python", "display": "Tornado"},
    # ── PHP ───────────────────────────────────────────────────────────────────
    "laravel":    {"lang": "php",    "display": "Laravel"},
    # ── JavaScript / TypeScript ───────────────────────────────────────────────
    "express":    {"lang": "js",     "display": "Express.js"},
    "nextjs":     {"lang": "js",     "display": "Next.js"},
    "nestjs":     {"lang": "js",     "display": "Nest.js"},
}

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 – PRETTY PRINTING HELPERS
# Simple functions to print colored messages in the terminal.
# ─────────────────────────────────────────────────────────────────────────────

def _color(code, text):
    """Wrap text in an ANSI color code (works on Linux/Mac; Windows 10+ too)."""
    if IS_WINDOWS:
        # Enable ANSI on Windows terminal
        os.system("")
    return f"\033[{code}m{text}\033[0m"

def info(msg):    print(_color("36", f"[INFO]  {msg}"))   # cyan
def ok(msg):      print(_color("32", f"[OK]    {msg}"))   # green
def warn(msg):    print(_color("33", f"[WARN]  {msg}"))   # yellow
def error(msg):   print(_color("31", f"[ERROR] {msg}"))   # red
def step(n, msg): print(_color("35", f"\n{'─'*60}\nSTEP {n}: {msg}\n{'─'*60}"))  # purple
def tip(msg):     print(_color("33", f"\n  💡 {msg}\n"))

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 – FRAMEWORK DETECTION
# We look at the files inside the project to guess which framework it uses.
# ─────────────────────────────────────────────────────────────────────────────

def detect_framework(project_path: Path) -> str:
    """
    Look at the project files and return the framework name.
    Returns one of the keys in SUPPORTED_FRAMEWORKS, or 'unknown'.
    """
    files = list(project_path.rglob("*"))
    filenames = {f.name.lower() for f in files if f.is_file()}
    all_text  = _read_project_text(project_path, max_files=30)

    # ── PHP: Laravel ──────────────────────────────────────────────────────────
    if "artisan" in filenames or "composer.json" in filenames:
        composer = project_path / "composer.json"
        if composer.exists() and "laravel" in composer.read_text(errors="ignore").lower():
            return "laravel"

    # ── JavaScript / TypeScript ───────────────────────────────────────────────
    pkg = project_path / "package.json"
    if pkg.exists():
        pkg_text = pkg.read_text(errors="ignore").lower()
        if "@nestjs/core" in pkg_text:
            return "nestjs"
        if "next" in pkg_text and '"next"' in pkg_text:
            return "nextjs"
        if "express" in pkg_text:
            return "express"

    # ── Python: check requirements.txt / pyproject.toml / imports ────────────
    req_files = ["requirements.txt", "pyproject.toml", "setup.py", "Pipfile"]
    req_text  = ""
    for rf in req_files:
        f = project_path / rf
        if f.exists():
            req_text += f.read_text(errors="ignore").lower()

    combined = req_text + all_text.lower()

    if "fastapi" in combined:
        return "fastapi"
    if "djangorestframework" in combined or "rest_framework" in combined:
        return "djangorest"
    if "django" in combined:
        return "django"
    if "starlette" in combined:
        return "starlette"
    if "tornado" in combined:
        return "tornado"
    if "flask" in combined:
        return "flask"

    return "unknown"


def _read_project_text(project_path: Path, max_files=30) -> str:
    """
    Read the first `max_files` source files and return all text combined.
    Used to detect imports and framework clues.
    """
    extensions = {".py", ".js", ".ts", ".php", ".json"}
    skip_dirs  = {"node_modules", ".git", "vendor", "__pycache__", ".next", "dist"}
    text = ""
    count = 0

    for f in project_path.rglob("*"):
        if count >= max_files:
            break
        if any(part in skip_dirs for part in f.parts):
            continue
        if f.suffix.lower() in extensions and f.is_file():
            try:
                text += f.read_text(errors="ignore")
                count += 1
            except Exception:
                pass

    return text

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 – PACKAGE INSTALLATION
# Install the right OpenTelemetry packages for each language.
# ─────────────────────────────────────────────────────────────────────────────

# OpenTelemetry packages needed for each framework
PYTHON_BASE_PACKAGES = [
    "opentelemetry-api",
    "opentelemetry-sdk",
    "opentelemetry-exporter-otlp-proto-grpc",
    "opentelemetry-instrumentation",
]

FRAMEWORK_PACKAGES = {
    "flask":      ["opentelemetry-instrumentation-flask",   "opentelemetry-instrumentation-sqlalchemy"],
    "fastapi":    ["opentelemetry-instrumentation-fastapi", "opentelemetry-instrumentation-sqlalchemy"],
    "django":     ["opentelemetry-instrumentation-django",  "opentelemetry-instrumentation-sqlalchemy"],
    "djangorest": ["opentelemetry-instrumentation-django",  "opentelemetry-instrumentation-sqlalchemy"],
    "starlette":  ["opentelemetry-instrumentation-starlette", "opentelemetry-instrumentation-sqlalchemy"],
    "tornado":    ["opentelemetry-instrumentation-tornado"],
    "laravel":    [],  # handled via composer
    "express":    [],  # handled via npm
    "nextjs":     [],  # handled via npm
    "nestjs":     [],  # handled via npm
}

JS_NPM_PACKAGES = [
    "@opentelemetry/sdk-node",
    "@opentelemetry/api",
    "@opentelemetry/auto-instrumentations-node",
    "@opentelemetry/exporter-trace-otlp-grpc",
    "@grpc/grpc-js",
]

NESTJS_EXTRA_PACKAGES = [
    "@opentelemetry/instrumentation-http",
    "@opentelemetry/instrumentation-express",
]

LARAVEL_COMPOSER_PACKAGES = [
    "open-telemetry/sdk",
    "open-telemetry/exporter-otlp-grpc",
    "open-telemetry/opentelemetry-auto-laravel",
]


def install_packages(framework: str, project_path: Path):
    """
    Install the required OpenTelemetry packages for the given framework.
    Uses pip for Python, npm for JS/TS, composer for PHP.
    """
    lang = SUPPORTED_FRAMEWORKS[framework]["lang"]

    if lang == "python":
        _pip_install(PYTHON_BASE_PACKAGES + FRAMEWORK_PACKAGES.get(framework, []))

    elif lang == "js":
        pkgs = JS_NPM_PACKAGES[:]
        if framework == "nestjs":
            pkgs += NESTJS_EXTRA_PACKAGES
        _npm_install(pkgs, project_path)

    elif lang == "php":
        _composer_install(LARAVEL_COMPOSER_PACKAGES, project_path)


def _pip_install(packages: list):
    """Run pip install for a list of Python packages."""
    info(f"Installing Python packages: {', '.join(packages)}")
    cmd = [sys.executable, "-m", "pip", "install", "--quiet"] + packages
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error(f"pip install failed:\n{result.stderr}")
        sys.exit(1)
    ok("Python packages installed.")


def _npm_install(packages: list, project_path: Path):
    """Run npm install for a list of Node.js packages."""
    info(f"Installing Node.js packages: {', '.join(packages)}")
    npm = "npm.cmd" if IS_WINDOWS else "npm"
    cmd = [npm, "install", "--save"] + packages
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_path)
    if result.returncode != 0:
        error(f"npm install failed:\n{result.stderr}")
        sys.exit(1)
    ok("Node.js packages installed.")


def _composer_install(packages: list, project_path: Path):
    """Run composer require for a list of PHP packages."""
    info(f"Installing PHP packages via Composer: {', '.join(packages)}")
    composer = "composer.bat" if IS_WINDOWS else "composer"
    cmd = [composer, "require"] + packages
    result = subprocess.run(cmd, capture_output=True, text=True, cwd=project_path)
    if result.returncode != 0:
        error(f"composer require failed:\n{result.stderr}")
        sys.exit(1)
    ok("PHP packages installed.")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 – INSTRUMENTATION FILE GENERATION
# Write the framework-specific instrumentation file into the project.
# ─────────────────────────────────────────────────────────────────────────────

def generate_instrumentation_file(
    framework: str,
    project_path: Path,
    service_name: str,
    jaeger_host: str,
) -> Path:
    """
    Copy + fill the right template for the detected framework.
    Returns the path of the generated file.
    """
    lang = SUPPORTED_FRAMEWORKS[framework]["lang"]
    templates_dir = Path(__file__).parent / "templates"

    # Pick the right template file
    template_file = templates_dir / f"{framework}_wrapper.{'py' if lang == 'python' else 'js' if lang == 'js' else 'php'}"

    # Fall back to a generic template if the specific one doesn't exist
    if not template_file.exists():
        ext = "py" if lang == "python" else "js" if lang == "js" else "php"
        template_file = templates_dir / f"generic_wrapper.{ext}"

    if not template_file.exists():
        error(f"Template not found: {template_file}")
        sys.exit(1)

    # Read template and replace placeholders
    content = template_file.read_text(encoding="utf-8")
    content = content.replace("{{SERVICE_NAME}}", service_name)
    content = content.replace("{{JAEGER_HOST}}", jaeger_host)
    content = content.replace("{{JAEGER_GRPC_PORT}}", str(JAEGER_GRPC_PORT))

    # Write the file into the project
    ext = "py" if lang == "python" else "js" if lang == "js" else "php"
    out_file = project_path / f"otel_instrumentation.{ext}"
    out_file.write_text(content, encoding="utf-8")

    ok(f"Instrumentation file created: {out_file}")
    return out_file

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 – JAEGER SETUP
# Start the Jaeger container using Docker so traces have somewhere to go.
# ─────────────────────────────────────────────────────────────────────────────

def start_jaeger():
    """
    Start Jaeger using Docker.
    Jaeger is the tool that collects and shows all the traces.
    """
    info("Starting Jaeger via Docker...")

    # Check if Docker is available
    if not shutil.which("docker"):
        warn("Docker not found. Please install Docker and try again.")
        warn("Download: https://www.docker.com/products/docker-desktop")
        tip("You can also start Jaeger manually:\n"
            "  docker run -d --name jaeger \\\n"
            "    -p 16686:16686 -p 4317:4317 \\\n"
            "    jaegertracing/all-in-one:latest")
        return False

    # Stop any old Jaeger container that might already be running
    subprocess.run(
        ["docker", "rm", "-f", "jaeger"],
        capture_output=True
    )

    # Start a fresh Jaeger container
    cmd = [
        "docker", "run", "-d",
        "--name", "jaeger",
        "-p", f"{JAEGER_UI_PORT}:{JAEGER_UI_PORT}",  # browser UI
        "-p", f"{JAEGER_GRPC_PORT}:{JAEGER_GRPC_PORT}",  # traces input
        "-p", "6831:6831/udp",                         # UDP compact
        "-p", "14268:14268",                           # HTTP collector
        "jaegertracing/all-in-one:latest",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        error(f"Failed to start Jaeger:\n{result.stderr}")
        return False

    # Wait a moment for Jaeger to be ready
    info("Waiting for Jaeger to start...")
    time.sleep(3)
    ok(f"Jaeger is running! Open http://localhost:{JAEGER_UI_PORT} in your browser.")
    return True

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 – INTEGRATION INSTRUCTIONS
# Tell the user exactly what lines to add in their app.
# ─────────────────────────────────────────────────────────────────────────────

# Instructions differ by framework
INTEGRATION_INSTRUCTIONS = {

    "flask": {
        "file": "app.py  (or wherever you create your Flask app)",
        "code": textwrap.dedent("""\
            # ── Add these lines AT THE TOP of your main file ──
            from otel_instrumentation import init_tracing
            init_tracing()
            # ─────────────────────────────────────────────────
        """),
    },

    "fastapi": {
        "file": "main.py  (or wherever you create your FastAPI app)",
        "code": textwrap.dedent("""\
            # ── Add these lines AT THE TOP of your main file ──
            from otel_instrumentation import init_tracing
            init_tracing()
            # ─────────────────────────────────────────────────
        """),
    },

    "django": {
        "file": "manage.py  OR  wsgi.py / asgi.py",
        "code": textwrap.dedent("""\
            # ── Add these lines BEFORE django.setup() ──
            from otel_instrumentation import init_tracing
            init_tracing()
            # ───────────────────────────────────────────
        """),
    },

    "djangorest": {
        "file": "manage.py  OR  wsgi.py / asgi.py",
        "code": textwrap.dedent("""\
            # ── Add these lines BEFORE django.setup() ──
            from otel_instrumentation import init_tracing
            init_tracing()
            # ───────────────────────────────────────────
        """),
    },

    "starlette": {
        "file": "main.py  (or wherever you create your Starlette app)",
        "code": textwrap.dedent("""\
            # ── Add these lines AT THE TOP of your main file ──
            from otel_instrumentation import init_tracing
            init_tracing()
            # ─────────────────────────────────────────────────
        """),
    },

    "tornado": {
        "file": "main.py  (or wherever you start your Tornado server)",
        "code": textwrap.dedent("""\
            # ── Add these lines AT THE TOP of your main file ──
            from otel_instrumentation import init_tracing
            init_tracing()
            # ─────────────────────────────────────────────────
        """),
    },

    "express": {
        "file": "app.js  or  index.js  (before you define any routes)",
        "code": textwrap.dedent("""\
            // ── Add this line AT THE VERY TOP (first line of the file) ──
            require('./otel_instrumentation');
            // ─────────────────────────────────────────────────────────────
        """),
    },

    "nextjs": {
        "file": "instrumentation.ts  (create this file at the root of your project)",
        "code": textwrap.dedent("""\
            // ── Paste this inside instrumentation.ts ──
            export async function register() {
              if (process.env.NEXT_RUNTIME === 'nodejs') {
                await import('./otel_instrumentation');
              }
            }
            // ──────────────────────────────────────────
            // Also add this to next.config.js:
            //   experimental: { instrumentationHook: true }
        """),
    },

    "nestjs": {
        "file": "main.ts  (before NestFactory.create(...))",
        "code": textwrap.dedent("""\
            // ── Add this import AT THE VERY TOP of main.ts ──
            import './otel_instrumentation';
            // ─────────────────────────────────────────────────
        """),
    },

    "laravel": {
        "file": "bootstrap/app.php  (after the Application is created)",
        "code": textwrap.dedent("""\
            // ── Add these lines after $app = new Application(...) ──
            require __DIR__.'/../otel_instrumentation.php';
            // ────────────────────────────────────────────────────────
        """),
    },
}


def print_integration_instructions(framework: str, instrumentation_file: Path):
    """Print a clear, step-by-step guide for the user."""
    instructions = INTEGRATION_INSTRUCTIONS.get(framework)
    if not instructions:
        warn("No specific instructions available for this framework.")
        return

    display = SUPPORTED_FRAMEWORKS[framework]["display"]

    print("\n" + "═" * 60)
    print(f"  ACTION REQUIRED – Add instrumentation to your {display} app")
    print("═" * 60)
    print(f"\n  1. Open this file in your project:")
    print(f"       {instructions['file']}")
    print(f"\n  2. Add these lines of code:\n")
    for line in instructions["code"].splitlines():
        print(f"       {line}")
    print(f"\n  3. Restart your application.")
    print(f"\n  4. Send some HTTP requests to your app (use your browser or")
    print(f"       a tool like curl / Postman / Locust).")
    print(f"\n  5. Come back here and press ENTER to start the analysis.")
    print("═" * 60 + "\n")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 – TRACE COLLECTION
# Pull traces from Jaeger HTTP API and save them as JSON.
# ─────────────────────────────────────────────────────────────────────────────

def collect_traces(service_name: str, jaeger_host: str, output_path: Path, limit: int = 500) -> bool:
    """
    Ask Jaeger for traces and save them to a JSON file.
    Returns True if we got at least one trace.
    """
    import urllib.request
    import urllib.error

    url = (
        f"http://{jaeger_host}:{JAEGER_UI_PORT}/api/traces"
        f"?service={service_name}&limit={limit}"
    )

    info(f"Collecting traces from Jaeger for service '{service_name}'...")
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        error(f"Could not reach Jaeger at {url}")
        error(str(e))
        tip("Make sure Jaeger is running and your app has sent some requests.")
        return False

    traces = data.get("data", [])
    if not traces:
        warn("No traces found. Did you send requests to your app?")
        return False

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)

    ok(f"Collected {len(traces)} trace(s). Saved to: {output_path}")
    return True

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 – SCOM ANALYSIS RUNNER
# Call the existing analysis pipeline with the collected traces.
# ─────────────────────────────────────────────────────────────────────────────

def run_scom_analysis(traces_path: Path, service_name: str, output_dir: Path):
    """
    Run the SCOM analysis pipeline on the collected traces.
    This calls the existing boundary_analyzer pipeline.
    """
    info("Running SCOM cohesion analysis...")

    # Try to find the analyzer script relative to this file
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
        tip(f"python run_pipeline.py --traces {traces_path} --service {service_name}")
        return

    cmd = [
        sys.executable, str(analyzer),
        "--traces", str(traces_path),
        "--service", service_name,
        "--output", str(output_dir),
    ]

    result = subprocess.run(cmd, text=True)
    if result.returncode == 0:
        ok("SCOM analysis complete! Check the output directory for the report.")
    else:
        error("SCOM analysis failed. Check the logs above.")

# ─────────────────────────────────────────────────────────────────────────────
# SECTION 10 – MAIN ENTRY POINT
# Tie all steps together.
# ─────────────────────────────────────────────────────────────────────────────

def main():
    ap = argparse.ArgumentParser(
        description="Auto-setup OpenTelemetry instrumentation for microservice analysis.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=textwrap.dedent("""\
        Examples:
          # Auto-detect framework and run everything
          python setup_instrumentation.py --project-path ./my-service

          # Force a framework (skip detection)
          python setup_instrumentation.py --project-path ./my-service --framework fastapi

          # Custom service name and Jaeger host
          python setup_instrumentation.py --project-path ./my-service \\
            --service-name order-service --jaeger-host 192.168.1.10

          # Skip Jaeger start (already running)
          python setup_instrumentation.py --project-path ./my-service --no-jaeger

          # Collect ALL HTTP methods (for a deeper load test)
          python setup_instrumentation.py --project-path ./my-service --methods ALL
        """)
    )

    ap.add_argument("--project-path", required=True,
                    help="Path to the microservice project to instrument")

    ap.add_argument("--framework", default="",
                    choices=list(SUPPORTED_FRAMEWORKS.keys()) + [""],
                    help="Force a specific framework (default: auto-detect)")

    ap.add_argument("--service-name", default="",
                    help="Name of the service (default: folder name)")

    ap.add_argument("--jaeger-host", default="localhost",
                    help="Host where Jaeger is running (default: localhost)")

    ap.add_argument("--no-jaeger", action="store_true",
                    help="Skip starting Jaeger (use if it is already running)")

    ap.add_argument("--no-install", action="store_true",
                    help="Skip package installation (use if already installed)")

    ap.add_argument("--traces-output", default="",
                    help="Where to save collected traces JSON (default: ./traces/)")

    ap.add_argument("--trace-limit", type=int, default=500,
                    help="Maximum number of traces to collect (default: 500)")

    args = ap.parse_args()

    project_path = Path(args.project_path).resolve()
    if not project_path.exists():
        error(f"Project path not found: {project_path}")
        sys.exit(1)

    # Default service name = folder name
    service_name = args.service_name or project_path.name

    # Default traces output
    traces_dir = Path(args.traces_output) if args.traces_output else project_path / "traces"
    traces_file = traces_dir / f"{service_name}_traces.json"

    print("\n" + "═" * 60)
    print(f"  OpenTelemetry Auto-Setup — Boundary Analyzer")
    print("═" * 60)

    # ── STEP 1: Detect framework ──────────────────────────────────────────────
    step(1, "Detecting framework")

    if args.framework:
        framework = args.framework
        info(f"Framework forced by user: {SUPPORTED_FRAMEWORKS[framework]['display']}")
    else:
        framework = detect_framework(project_path)
        if framework == "unknown":
            error("Could not detect the framework automatically.")
            error("Please use --framework to specify one of: " + ", ".join(SUPPORTED_FRAMEWORKS.keys()))
            sys.exit(1)
        ok(f"Detected framework: {SUPPORTED_FRAMEWORKS[framework]['display']}")

    lang = SUPPORTED_FRAMEWORKS[framework]["lang"]
    info(f"Language: {lang.upper()} | Service: {service_name}")

    # ── STEP 2: Install packages ──────────────────────────────────────────────
    step(2, "Installing OpenTelemetry packages")

    if args.no_install:
        info("Skipping package installation (--no-install flag set).")
    else:
        install_packages(framework, project_path)

    # ── STEP 3: Generate instrumentation file ─────────────────────────────────
    step(3, "Generating instrumentation file")

    instrumentation_file = generate_instrumentation_file(
        framework, project_path, service_name, args.jaeger_host
    )

    # ── STEP 4: Start Jaeger ──────────────────────────────────────────────────
    step(4, "Starting Jaeger")

    if args.no_jaeger:
        info("Skipping Jaeger start (--no-jaeger flag set).")
    else:
        start_jaeger()

    # ── STEP 5: Show integration instructions ─────────────────────────────────
    step(5, "Integration instructions")

    print_integration_instructions(framework, instrumentation_file)

    # Wait for the user to restart their app
    try:
        input("  Press ENTER when your app is restarted and you have sent some traffic...")
    except KeyboardInterrupt:
        print("\n")
        warn("Setup interrupted. Run the script again when ready.")
        sys.exit(0)

    # ── STEP 6: Collect traces ────────────────────────────────────────────────
    step(6, "Collecting traces from Jaeger")

    success = collect_traces(service_name, args.jaeger_host, traces_file, args.trace_limit)
    if not success:
        warn("Could not collect traces. Try again after sending more traffic.")
        sys.exit(1)

    # ── STEP 7: Run SCOM analysis ─────────────────────────────────────────────
    step(7, "Running SCOM cohesion analysis")

    output_dir = project_path / "scom_report"
    run_scom_analysis(traces_file, service_name, output_dir)

    # ── DONE ──────────────────────────────────────────────────────────────────
    print("\n" + "═" * 60)
    ok("All done!")
    print(f"  Traces saved : {traces_file}")
    print(f"  Report dir  : {output_dir}")
    print(f"  Jaeger UI   : http://{args.jaeger_host}:{JAEGER_UI_PORT}")
    print("═" * 60 + "\n")


if __name__ == "__main__":
    main()
