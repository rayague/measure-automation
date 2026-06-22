<div align="center">

# 🔬 MBA — Microservice Boundary Analyzer

**Detect bad microservice boundaries using runtime observability and data-cohesion metrics.**

[![Python](https://img.shields.io/badge/Python-3.11%2B-blue?logo=python)](https://python.org)
[![License](https://img.shields.io/badge/License-MIT-green)](LICENSE)
[![Version](https://img.shields.io/badge/version-0.8.0-cyan)](CHANGELOG.md)
[![OpenTelemetry](https://img.shields.io/badge/OpenTelemetry-ready-orange?logo=opentelemetry)](https://opentelemetry.io)
[![Jaeger](https://img.shields.io/badge/Jaeger-integrated-blue)](https://jaegertracing.io)

</div>

---

## Table of Contents

- [What is MBA?](#what-is-mba)
- [The Problem — Wrong Cuts in Microservices](#the-problem--wrong-cuts-in-microservices)
- [The SCOM Metric — How MBA Measures Cohesion](#the-scom-metric--how-mba-measures-cohesion)
- [Architecture Overview](#architecture-overview)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Quick Start — Zero to Results in 3 Commands](#quick-start--zero-to-results-in-3-commands)
- [Command Reference](#command-reference)
  - [mba full — Fully Automatic Analysis](#mba-full--fully-automatic-analysis)
  - [mba run — Pipeline on Existing Traces](#mba-run--pipeline-on-existing-traces)
  - [mba analyze — Analyze a Traces File](#mba-analyze--analyze-a-traces-file)
  - [mba ingest — Universal Log File Analysis](#mba-ingest--universal-log-file-analysis)
  - [mba benchmark — Known Microservice Benchmarks](#mba-benchmark--known-microservice-benchmarks)
  - [mba dashboard — Interactive Web UI](#mba-dashboard--interactive-web-ui)
  - [mba setup — Add OpenTelemetry to Your App](#mba-setup--add-opentelemetry-to-your-app)
  - [mba runs — Manage Historical Runs](#mba-runs--manage-historical-runs)
  - [mba teastore — TeaStore Benchmark](#mba-teastore--teastore-benchmark)
- [The Analysis Pipeline — 8 Steps Explained](#the-analysis-pipeline--8-steps-explained)
- [Phased Traffic Engine — How mba full Generates Traffic](#phased-traffic-engine--how-mba-full-generates-traffic)
- [Real-Time Terminal Dashboard — MBA Live UI](#real-time-terminal-dashboard--mba-live-ui)
- [Universal Log Ingestion — Supported Formats](#universal-log-ingestion--supported-formats)
- [The Interactive Web Dashboard](#the-interactive-dashboard)
- [LLM-Assisted Features](#llm-assisted-features)
- [Supported Languages and Frameworks](#supported-languages-and-frameworks)
- [Output Files and Artifacts](#output-files-and-artifacts)
- [Run Registry — History and Comparisons](#run-registry--history-and-comparisons)
- [Configuration Reference](#configuration-reference)
- [Understanding Your Results](#understanding-your-results)
- [Troubleshooting](#troubleshooting)

---

## What is MBA?

**MBA (Microservice Boundary Analyzer)** is a command-line tool that automatically detects architectural problems in microservice systems. Specifically, it identifies **Wrong Cuts** — services that group together unrelated business responsibilities — by analyzing runtime database access patterns collected via OpenTelemetry and Jaeger.

MBA takes a microservice project, runs it in an isolated Docker environment, generates realistic HTTP traffic against all discovered endpoints, and then mathematically computes how cohesive each service is by measuring which endpoints share database tables. Services where endpoints access completely disjoint sets of tables are flagged as potential Wrong Cuts.

The entire process — from code discovery to instrumentation, deployment, traffic generation, trace collection, SCOM computation, and interactive visualization — can be run with a **single command**:

```bash
mba full ./my-microservice-project
```

---

## The Problem — Wrong Cuts in Microservices

When teams design microservices, they often split services along **technical boundaries** (e.g., "all the CRUD for users goes here") rather than **business domain boundaries**. This creates what researchers call a **Wrong Cut**: a service that violates the Single Responsibility Principle at the service level.

### What a Wrong Cut looks like

A service with a Wrong Cut might have endpoints like these inside a single service:

```
GET  /invoice     → reads:  invoices table
POST /hotel       → reads:  reservations table
GET  /user        → reads:  users table
```

Each endpoint accesses **completely different data**. There is no shared database state between them. They don't belong to the same business domain — they just happen to be deployed in the same process. This is effectively a **distributed monolith in disguise**.

### The consequences

| Problem | Impact |
|---|---|
| High coupling between unrelated business domains | Any change to one domain requires touching the other |
| Larger blast radius for failures | A bug in the invoice logic can take down hotel reservations |
| Impossible to scale domains independently | You must scale the entire service even if only one part needs it |
| Team coordination overhead | Multiple teams "own" the same service |
| Harder to extract and refactor later | Technical debt compounds over time |

### How MBA detects them

MBA applies the principle: **if endpoints within a service do not share database tables, they likely do not share a business capability**.

> **Strong data cohesion → healthy boundary**
> **Weak data cohesion → potential Wrong Cut**

---

## The SCOM Metric — How MBA Measures Cohesion

MBA computes the **SCOM (Service COhesion Metric)**, adapted from the classical OO metric **SCOM (Sensitive Class Cohesion Metric)** by Counsell et al. (2006).

The original OO metric measured how much **methods** within a class share the same **attributes**. MBA translates this to microservices:

| OO world | Microservices world |
|---|---|
| Class | Microservice |
| Method | HTTP Endpoint |
| Attribute | Database table accessed |

### The formula

For a service with endpoints `e₁, e₂, ..., eₙ` and `A(eᵢ)` = set of tables accessed by endpoint `eᵢ`:

```
CI(eᵢ, eⱼ) = |A(eᵢ) ∩ A(eⱼ)|       ← Connection Intensity between two endpoints

CI_max = max_{i≠j} ( min(|A(eᵢ)|, |A(eⱼ)|) )   ← Maximum possible CI for this service

SCOM = Σᵢ<ⱼ [ wᵢⱼ · CI(eᵢ, eⱼ) ]
       ────────────────────────────
       CI_max · Σᵢ<ⱼ [ wᵢⱼ ]
```

Where `wᵢⱼ = freq(eᵢ) × freq(eⱼ)` is the **frequency weight** of each endpoint pair, computed from actual runtime invocation counts in the traces.

### Interpretation

| SCOM Score | Label | Meaning |
|---|---|---|
| `0.8 – 1.0` | **Very cohesive** | All endpoints share the same data. Perfect domain focus. |
| `0.5 – 0.8` | **Cohesive** | Strong overlap, minor divergence. Generally healthy. |
| `0.3 – 0.5` | **Weakly cohesive** | Some unrelated data access. Investigate further. |
| `0.0 – 0.3` | **Not cohesive** | Endpoints access disjoint tables. Likely Wrong Cut. |
| `0.0` (exactly) | **Suspect** | No shared tables at all, or only a single endpoint. |

### What makes MBA's SCOM different from a naive formula

- **Frequency weighting**: High-traffic endpoint pairs contribute more to the score than rarely-called ones. This reflects real workload conditions.
- **Includes endpoints with no DB ops**: An endpoint that performs no database operations (e.g., a pure computation) is included with an empty table set. This is a deliberate design choice — such endpoints contribute to the cohesion penalty.
- **Normalization by CI_max**: The score is normalized to `[0, 1]` relative to the maximum possible cohesion for that particular service, making scores comparable across services of different sizes.

---

## Architecture Overview

```
┌─────────────────────────────────────────────────────────────────┐
│                        mba full ./project                       │
└──────────────────────────┬──────────────────────────────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │  1. DISCOVER                        │
        │  Language/framework detection       │
        │  Entry point discovery              │
        │  Endpoint extraction (AST / LLM)    │
        └──────────────────┬──────────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │  2. DEPLOY                          │
        │  Start Jaeger (Docker)              │
        │  Inject OpenTelemetry               │
        │  Start services (Docker Compose)    │
        └──────────────────┬──────────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │  3. TRAFFIC                         │
        │  Auto-discover live endpoints       │
        │  Generate realistic HTTP traffic    │
        │  Concurrent workers                 │
        └──────────────────┬──────────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │  4. COLLECT                         │
        │  Export traces from Jaeger API      │
        │  Wait for trace propagation         │
        └──────────────────┬──────────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │  5. ANALYZE (8-step pipeline)       │
        │  Parse spans → extract endpoints    │
        │  Extract DB table names from SQL    │
        │  Build endpoint → table mapping     │
        │  Compute weighted SCOM per service  │
        │  Apply threshold → flag suspects    │
        │  Generate Markdown report           │
        └──────────────────┬──────────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │  6. CLEANUP                         │
        │  Stop Docker containers             │
        │  Remove instrumentation             │
        │  Save run to registry               │
        └──────────────────┬──────────────────┘
                           │
        ┌──────────────────▼──────────────────┐
        │  Dashboard (optional)               │
        │  Interactive Plotly/Dash UI         │
        │  SCOM scores, heatmaps, radar       │
        │  Service detail views               │
        │  Historical trend comparison        │
        └─────────────────────────────────────┘
```

---

## Prerequisites

| Requirement | Version | Purpose |
|---|---|---|
| **Python** | 3.11 or higher | Runtime |
| **Docker** | Any recent version | Deploying Jaeger and services |
| **Docker Compose** | v2+ (`docker compose`) | Orchestrating multi-service projects |
| **pip** | Any recent version | Package installation |

> **Note**: Docker Desktop must be running before any `mba full` or `mba teastore` command. The tool checks for Docker availability at startup and gives a clear error if it is not found.

Optional but recommended:

| Optional | Purpose |
|---|---|
| **Ollama** (with `qwen2.5-coder` model) | Local LLM for smarter instrumentation (free) |
| **OpenRouter API key** | Cloud LLM fallback for instrumentation and report generation |

---

## Installation

### From source (recommended)

```bash
git clone https://github.com/rayague/measure-automation.git
cd measure-automation
pip install -e .
```

The `-e` flag installs in **editable mode**: any changes you make to the source are reflected immediately without reinstalling.

### Standard install

```bash
pip install .
```

### Verify installation

```bash
mba --version
# MBA v0.7.8 - Microservice Boundary Analyzer
```

Both `mba` and `boundary-analyzer` are registered as entry points and work identically.

---

## Quick Start — Zero to Results in 3 Commands

### Scenario A — Analyze your own Docker Compose project

```bash
# 1. Navigate to your project
cd ./my-microservice-project

# 2. Run the full automatic analysis
mba full . --llm --reset-jaeger

# 3. View the results in the dashboard
mba dashboard --run <run-id-shown-in-output>
```

### Scenario B — Analyze any existing log file

```bash
# Jaeger JSON, Zipkin, OTLP, nginx log, Django log, Locust CSV — MBA auto-detects the format
mba ingest ./traces/my_traces.json
mba ingest ./logs/nginx-access.log
mba ingest ./logs/django.log --format generic_sql
mba ingest ./locust_requests.csv --format locust

# Open dashboard after analysis
mba ingest ./traces/my_traces.json --dashboard
```

### Scenario C — TeaStore benchmark (no project needed)

```bash
# Download, deploy, and analyze the official TeaStore benchmark
mba teastore --duration 120
```

---

## Command Reference

### `mba full` — Fully Automatic Analysis

The flagship command. Detects your project, instruments it, deploys it, generates traffic, and computes SCOM — all automatically.

```bash
mba full [PROJECT_DIR] [OPTIONS]
```

**Arguments:**

| Argument | Default | Description |
|---|---|---|
| `PROJECT_DIR` | `.` (current directory) | Path to the microservice project to analyze |

**Traffic options:**

| Flag | Default | Description |
|---|---|---|
| `--duration <seconds>` | `60` | How long to generate HTTP traffic against all discovered endpoints |
| `--workers <n>` | `5` | Number of concurrent traffic generation threads |

**Jaeger options:**

| Flag | Default | Description |
|---|---|---|
| `--jaeger-port <port>` | `16686` | Port for the Jaeger web UI and API |
| `--otlp-port <port>` | `4318` | OTLP HTTP port that services send traces to |
| `--reset-jaeger` | off | Stop and remove any existing Jaeger container before starting, preventing trace pollution from previous runs. **Highly recommended for reproducible results.** |
| `--lookback <minutes>` | `10` | How far back in time to look for traces in Jaeger after traffic generation |

**Settings options:**

| Flag | Default | Description |
|---|---|---|
| `--language <lang>` | auto-detected | Force a specific language (`python`, `java`, `node`, `php`, `dotnet`). Useful if detection fails. |
| `--llm` | off | Use LLM to generate smarter instrumentation code and richer endpoint payloads |
| `--verbose` / `-v` | off | Show detailed output: payloads, HTTP responses, endpoint discovery |
| `--exclude-services <names...>` | none | Service names to exclude from SCOM analysis (e.g. `--exclude-services gateway`) |
| `--no-clean` | off | Keep Docker containers and Jaeger running after the analysis finishes |

**Example — production-quality run:**

```bash
mba full ./my-project \
  --duration 120 \
  --workers 8 \
  --llm \
  --reset-jaeger \
  --lookback 15 \
  --verbose
```

**What happens step by step:**

1. **Discover** — MBA scans the project directory for Docker Compose files, Dockerfiles, entry points (`app.py`, `main.py`, `manage.py`, etc.), and reads them to determine the language, framework, and exposed ports.
2. **Instrument** — MBA injects an OpenTelemetry wrapper (a modified Dockerfile and entrypoint) into each detected service so it exports traces to Jaeger. Original files are backed up.
3. **Deploy** — Starts Jaeger (as a Docker container) and then starts all services via `docker compose up`.
4. **Traffic** — Discovers all live HTTP endpoints by making test requests, then generates concurrent traffic using realistic payloads for each endpoint for the specified `--duration`. POST/PUT endpoints receive generated request bodies.
5. **Collect** — Waits for Jaeger trace propagation and exports all traces via the Jaeger API.
6. **Analyze** — Runs the 8-step SCOM pipeline (see below) on the collected traces.
7. **Cleanup** — Stops Docker containers, removes OTel instrumentation, restores original files.
8. **Save** — Saves the complete run to a local registry (`data/runs/<timestamp>_<project>/`).

---

### `mba run` — Pipeline on Existing Traces

Run the SCOM analysis pipeline when you already have Jaeger set up and running with your own instrumented services. This command does not deploy anything — it just collects traces from a running Jaeger instance and computes SCOM.

```bash
mba run [OPTIONS]
```

**Pipeline options:**

| Flag | Default | Description |
|---|---|---|
| `--skip-collect` | off | Skip Step 1 (trace collection). Use traces already on disk. |
| `--skip-no-db-services` | off | Exclude services with zero database tables from the SCOM ranking |
| `--no-clean` | off | Keep intermediate files from previous runs |
| `--new-dir <name>` | none | Save this run in `data/runs/<name>/` (keeps runs isolated) |
| `--output-dir <path>` | from `settings.yaml` | Where to save collected traces |
| `--settings <path>` | `config/settings.yaml` | Path to the settings YAML file |
| `--llm` | off | Use LLM to generate the final analysis report |

**Dashboard options:**

| Flag | Default | Description |
|---|---|---|
| `--dashboard` | off | Open the interactive dashboard after the pipeline completes |
| `--data-dir <path>` | `data` | Folder containing pipeline results for the dashboard |
| `--dash-host <host>` | `127.0.0.1` | Dashboard bind address |
| `--dash-port <port>` | `8050` | Dashboard port |

**Typical usage:**

```bash
# Services already running with OTel, Jaeger already up
mba run

# Reuse traces from a previous collection
mba run --skip-collect --dashboard

# Save each run separately
mba run --new-dir experiment-1
```

> **Configuration**: `mba run` reads Jaeger connection details and service names from `config/settings.yaml`. See [Configuration Reference](#configuration-reference).

---

### `mba analyze` — Analyze a Traces File

Analyze a pre-exported Jaeger traces JSON file without any collection or deployment. The most lightweight path to SCOM scores.

```bash
mba analyze <TRAFFIC_FILE> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|---|---|
| `TRAFFIC_FILE` | Path to a Jaeger JSON traces file |

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--output-dir <path>` | `data/analysis` | Where to save all pipeline results |
| `--threshold <value>` | `0.5` | Fixed SCOM threshold below which a service is flagged as suspicious |
| `--skip-no-db-services` | off | Exclude services with no detected database tables |
| `--language <lang>` | auto | Force a language for trace parsing |
| `--llm` | off | Use LLM to generate the report |
| `--dashboard` | off | Open the dashboard after analysis |

**Example:**

```bash
# Export traces from Jaeger manually, then analyze
mba analyze ./traces/export_2026.json \
  --output-dir ./results/run-42 \
  --threshold 0.3 \
  --dashboard
```

---

### `mba ingest` — Universal Log File Analysis

Analyze **any log file** — Jaeger, Zipkin, OTLP, nginx, Django/Flask/SQLAlchemy app logs, Locust CSV, W3C/IIS, JSON Lines. The format is auto-detected from file content. No deployment or trace collection needed.

```bash
mba ingest <LOG_FILE> [OPTIONS]
```

**Arguments:**

| Argument | Description |
|---|---|
| `LOG_FILE` | Path to any log/trace file (see supported formats below) |

**Ingestion options:**

| Flag | Default | Description |
|---|---|---|
| `--format <name>` | auto | Force a format: `jaeger`, `zipkin`, `otlp`, `locust`, `nginx`, `w3c`, `generic_sql`, `json_lines` |
| `--service-name <name>` | from file | Override the service name in the log |
| `--encoding <enc>` | `utf-8` | Text encoding of the file |

**Output options:**

| Flag | Default | Description |
|---|---|---|
| `--output-dir <path>` | `data/ingest` | Where to save pipeline results |
| `--threshold <value>` | `0.5` | Fixed SCOM threshold for suspicious classification |
| `--dashboard` | off | Open the interactive dashboard after analysis |

**Examples:**

```bash
# Jaeger JSON export — auto-detected
mba ingest ./traces.json

# nginx access log
mba ingest /var/log/nginx/access.log --service-name my-api

# Django development server log (HTTP + SQL auto-correlated)
mba ingest ./django.log --format generic_sql --dashboard

# Locust CSV request statistics
mba ingest ./locust_requests.csv

# Zipkin or OTLP traces
mba ingest ./zipkin-export.json
mba ingest ./otlp-export.json
```

**What `mba ingest` does:**

1. Auto-detects the file format with a confidence score
2. Parses the file into the internal spans schema (endpoint + DB operation extraction)
3. For formats with HTTP→SQL correlation (e.g. Django logs), links DB spans to their parent HTTP spans
4. Runs the full 8-step SCOM pipeline
5. Prints an ingestion stats table (spans, HTTP spans, DB spans, correlation quality)
6. Prints the per-service SCOM ranking table
7. Saves to the run registry and optionally opens the dashboard

> **Note on DB info**: Formats like nginx and Locust CSV contain HTTP traffic but no database operations. For these, `mba ingest` computes SCOM using path-based table heuristics and clearly labels the results as estimated.

---

### `mba benchmark` — Known Microservice Benchmarks

Run SCOM analysis on well-known microservice benchmark applications, or get step-by-step setup instructions for ones requiring manual deployment.

```bash
mba benchmark [NAME] [OPTIONS]
```

**Available benchmarks:**

| Name | Application | Services | Language | Automated |
|---|---|---|---|---|
| `teastore` | TeaStore (e-commerce) | 6 | Java | ✅ Fully automated |
| `hotel` | DeathStarBench Hotel Reservation | 5 | Go / Python | Manual setup guide |
| `boutique` | Google Online Boutique | 11 | Polyglot | Manual setup guide |
| `sockshop` | Weaveworks Sock Shop | 8 | Polyglot | Manual setup guide |

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--output <path>` | `data/benchmark` | Output directory |
| `--duration <seconds>` | `60` | Traffic generation duration |
| `--workers <n>` | `5` | Traffic workers |
| `--threshold <value>` | `0.5` | SCOM threshold |
| `--no-cleanup` | off | Keep containers running after analysis |
| `--jaeger-ui` | off | Open Jaeger UI after the run |
| `--dashboard` | off | Open MBA dashboard after the run |

**Examples:**

```bash
# List all benchmarks
mba benchmark

# Run TeaStore (fully automated — downloads, deploys, generates traffic, computes SCOM)
mba benchmark teastore --duration 120 --dashboard

# Get setup instructions for Hotel Reservation
mba benchmark hotel

# Get setup instructions for Google Online Boutique
mba benchmark boutique
```

---

### `mba dashboard` — Interactive Web UI

Launch the interactive web dashboard to visualize SCOM results. Works with any saved run or a data directory produced by `mba run` or `mba analyze`.

```bash
mba dashboard [OPTIONS]
```

**Options:**

| Flag | Default | Description |
|---|---|---|
| `--run <run-id>` | most recent | Run ID to display. Use `mba runs list` to see available IDs. |
| `--data-dir <path>` | `data` | Folder with pipeline results (alternative to `--run`) |
| `--dash-host <host>` | `127.0.0.1` | Bind address. Use `0.0.0.0` to expose on your network. |
| `--dash-port <port>` | `8050` | HTTP port for the dashboard |

**Examples:**

```bash
# Open the most recent run
mba dashboard

# Open a specific historical run
mba dashboard --run 20260619_154801_scenario3

# Expose on the network
mba dashboard --dash-host 0.0.0.0 --dash-port 8080
```

The dashboard is available at `http://127.0.0.1:8050` by default.

See [The Interactive Dashboard](#the-interactive-dashboard) for a full description of every panel.

---

### `mba setup` — Add OpenTelemetry to Your App

Automatically add OpenTelemetry instrumentation to an existing Python project that does not yet export traces. MBA detects your framework, installs the necessary packages, and generates an instrumentation bootstrap file.

```bash
mba setup --project-path <PATH> [OPTIONS]
```

**Project options:**

| Flag | Required | Description |
|---|---|---|
| `--project-path <path>` | **yes** | Path to the microservice project folder |
| `--framework <name>` | no | Force a framework: `fastapi`, `flask`, `django`, `starlette`, `tornado`. Default: auto-detect |
| `--service-name <name>` | no | Service name to use in Jaeger (default: folder name) |
| `--traces-output <path>` | no | Where to save collected traces |
| `--trace-limit <n>` | `500` | Max number of traces to collect |

**Jaeger options:**

| Flag | Default | Description |
|---|---|---|
| `--jaeger-host <host>` | `localhost` | Jaeger server address |
| `--no-jaeger` | off | Skip starting Jaeger (use if it is already running) |

**Extra options:**

| Flag | Description |
|---|---|
| `--no-install` | Do not run `pip install` (use if packages are already installed) |
| `--dashboard` | Open the dashboard when the analysis is complete |
| `--llm` | Use LLM to generate the instrumentation code |

**Supported frameworks:**

- FastAPI
- Flask
- Django
- Starlette
- Tornado

**Example:**

```bash
mba setup --project-path ./my-flask-api \
           --service-name my-flask-api \
           --framework flask \
           --dashboard
```

**What setup does:**

1. Detects language and framework by scanning imports, entry files, and dependency files
2. Installs `opentelemetry-*` packages via pip (unless `--no-install`)
3. Generates a wrapper file (e.g., `otel_bootstrap.py`) that configures the OTLP exporter
4. Starts a local Jaeger instance
5. Asks you to restart your app with the bootstrap
6. Waits for you to send traffic (or generates it automatically)
7. Collects traces and runs SCOM

---

### `mba runs` — Manage Historical Runs

Every execution of `mba full`, `mba run`, or `mba analyze` is automatically saved to a local registry. Use `mba runs` subcommands to list, inspect, compare, or delete past runs.

#### `mba runs list`

```bash
mba runs list [--json]
```

Lists all saved runs, newest first. Output includes: timestamp, run ID, project name, number of services, and success status.

```
  Saved runs (4 total)

  last → 2026-06-19 17:10:07  20260619_171007_scenario3  ✔  scenario3  (2 services)
         2026-06-19 15:48:01  20260619_154801_scenario3  ✔  scenario3  (2 services)
         2026-06-18 09:22:14  20260618_092214_teastore    ✔  teastore   (6 services)
         2026-06-17 14:55:03  20260617_145503_hotel        ✔  hotel      (5 services)
```

Use `--json` for machine-readable output (one JSON object per line).

#### `mba runs show <run-id>`

```bash
mba runs show 20260619_154801_scenario3 [--json]
```

Detailed view of a single run: date, project, language, duration, traffic stats, services, and a SCOM table:

```
  Run: 20260619_154801_scenario3
  Date: 2026-06-19 15:48:01   Project: scenario3   Language: python
  Duration: 407.5s   Status: ✔ Success
  Endpoints: 5   Tables: 8
  Traffic: 868 req (867 ok, 1 failed)

┌─────────────┬───────────┬────────┬────────┬────────┐
│ Service     │ Endpoints │ Tables │ SCOM   │ Status │
├─────────────┼───────────┼────────┼────────┼────────┤
│ scenario3   │ 5         │ 4      │ 0.0435 │ ✔      │
│ setup       │ 5         │ 4      │ 0.0000 │ ⚠      │
└─────────────┴───────────┴────────┴────────┴────────┘
```

#### `mba runs compare [run-id-1] [run-id-2]`

```bash
mba runs compare 20260619_154801_scenario3 20260619_171007_scenario3
```

Side-by-side SCOM comparison between two runs. Highlights improvements (Δ) and regressions:

```
  SCOM Comparison

  Service      2026-06-19  2026-06-19  Δ
  scenario3    0.0435      0.1820      +0.1385
  setup        0.0000      0.0000      +0.0000
```

Use `--json` for programmatic integration (CI/CD pipelines, automated regression detection).

#### `mba runs delete <run-id>`

```bash
mba runs delete 20260619_154801_scenario3
```

Permanently deletes a run's directory and removes it from the registry index.

---

### `mba teastore` — TeaStore Benchmark

Deploy and analyze the [TeaStore](https://github.com/DescartesResearch/TeaStore) benchmark application — a well-known research microservices system — without any project of your own.

```bash
mba teastore [OPTIONS]
```

TeaStore consists of **6 Java microservices** connected via REST and a shared database. It is the standard benchmark used in the original SCOM paper.

**Run options:**

| Flag | Default | Description |
|---|---|---|
| `--output <path>` | `data/teastore_run` | Save folder for traces and results |
| `--duration <seconds>` | `60` | Traffic generation duration |
| `--wait <seconds>` | `300` | Max time to wait for TeaStore services to start |
| `--download-only` | off | Only download the OpenTelemetry Java agent, do not start anything |

**SCOM options:**

| Flag | Default | Description |
|---|---|---|
| `--threshold <value>` | `0.5` | SCOM threshold for suspicious classification |
| `--no-skip-no-db` | off | Also analyze services that do not access a database |
| `--skip-pipeline` | off | Only export traces, do not run SCOM analysis |

**Docker options:**

| Flag | Description |
|---|---|
| `--no-cleanup` | Keep Docker containers running after the analysis |
| `--jaeger-ui` | Open the Jaeger web UI after the run (implies `--no-cleanup`) |

**Example:**

```bash
# Full TeaStore analysis with 2 minutes of traffic
mba teastore --duration 120

# Just get traces without analysis
mba teastore --skip-pipeline --no-cleanup
```

---

## The Analysis Pipeline — 8 Steps Explained

When MBA analyzes traces (whether collected automatically via `mba full` or provided as a file via `mba analyze`), it runs 8 sequential pipeline steps. Each step reads from and writes to well-defined CSV files.

### Step 1 — Collect Traces

**Input:** Jaeger API (`http://localhost:16686`)
**Output:** `data/raw/traces/jaeger_traces_<service>_<timestamp>.json`

Queries the Jaeger API for all available traces in the configured lookback window. Saves raw JSON files compatible with the Jaeger export format.

### Step 2 — Read and Flatten Traces

**Input:** `data/raw/traces/*.json`
**Output:** `data/interim/spans.csv`

Reads all trace JSON files, traverses every trace and every span, and flattens them into a single CSV with columns including: `trace_id`, `span_id`, `parent_span_id`, `service_name`, `operation_name`, `duration_us`, and all `tags` normalized into typed columns.

This is the foundation of the entire pipeline — every subsequent step reads from `spans.csv`.

### Step 3 — Find HTTP Endpoints

**Input:** `data/interim/spans.csv`
**Output:** `data/interim/endpoints.csv`

Identifies spans that represent **server-side HTTP operations** (i.e., spans that received an HTTP request, not spans that made one). Extracts:

- HTTP method (`GET`, `POST`, `PUT`, `DELETE`, etc.)
- Route path with normalized parameters (e.g., `/users/{id}` instead of `/users/123`)
- Service name
- Invocation count (how many times this endpoint was called)

Health/metrics/infrastructure endpoints (`/health`, `/metrics`, `/ready`, `/ping`, etc.) are automatically excluded.

### Step 4 — Find Database Tables

**Input:** `data/interim/spans.csv`
**Output:** `data/interim/db_operations.csv`

Extracts **all spans that represent a database operation** and identifies the table (or collection) being accessed. Supports:

| Database system | Detection method |
|---|---|
| PostgreSQL / MySQL | Parses `db.statement` tag (SQL `FROM`, `INSERT INTO`, `UPDATE`, `JOIN` clauses) |
| MongoDB | Extracts collection name from `db.mongodb.collection` or the operation command |
| Redis | Uses key prefix patterns to infer a logical "table" |
| Generic SQL | Regex-based SQL statement parser covering all common DML/DDL patterns |

### Step 5 — Build Endpoint → Table Mapping

**Input:** `spans.csv`, `endpoints.csv`, `db_operations.csv`
**Output:** `data/interim/endpoint_table_map.csv`

The most critical step. For each database span, MBA walks **up the span parent chain** until it reaches the root HTTP endpoint span. This establishes which database tables are accessed as a consequence of which endpoint being called.

The output is a table with columns: `service_name`, `endpoint_key`, `table`, `count` (how many times this endpoint accessed this table across all traces).

This mapping is the data used to compute SCOM and to render the heatmap in the dashboard.

### Step 6 — Compute SCOM Scores

**Input:** `data/interim/endpoint_table_map.csv`, `data/interim/endpoints.csv`
**Output:** `data/processed/service_scom.csv`

Computes the weighted SCOM score for every service using the formula described in [The SCOM Metric](#the-scom-metric--how-mba-measures-cohesion).

Key behaviors:
- Services with fewer than 2 endpoints always receive a SCOM of `0.0` (as defined by the paper)
- Endpoints with no database operations are included as members of the service with an empty table set (they contribute to the cohesion penalty)
- Weighting uses the actual invocation frequency of each endpoint pair
- Services with only one unique table always receive a SCOM of `1.0` (maximum cohesion)

### Step 7 — Rank and Flag Suspicious Services

**Input:** `data/processed/service_scom.csv`
**Output:** `data/processed/service_rank.csv`, `data/processed/suspicious_services.csv`

Sorts services by SCOM score (lowest first = worst cohesion first) and applies a threshold to classify each service as **healthy** or **suspect**. Three threshold methods are available:

| Method | How it works | Best for |
|---|---|---|
| `percentile` | Services below the Nth percentile of all SCOM scores are flagged (default: 25th percentile) | Relative ranking in large systems |
| `zscore` | Services more than N standard deviations below the mean are flagged (default: −1.5) | Systems with a clear bimodal distribution |
| `fixed` | Any service below a fixed threshold is flagged (default: 0.5) | Strict policy enforcement |

The `service_rank.csv` file adds: `rank`, `threshold_value`, `threshold_method`, and `is_suspicious` columns.

### Step 8 — Generate Report

**Input:** `data/processed/service_rank.csv`, `data/processed/suspicious_services.csv`
**Output:** `reports/latest/report.md` (and saved to the run directory)

Generates a structured Markdown report summarizing:
- All services ranked by SCOM score
- Suspected Wrong Cuts with per-service explanation
- Endpoint → table breakdown for each service
- SCOM methodology reference
- **Data Sources section** — when ingesting external logs, shows per-file parsing stats (format detected, confidence, DB info availability, correlation quality)
- **Dual-column SCOM table** — when using `--scom-method paper`, shows both unweighted (Section III-C formula) and weighted (Section IV-B extension) scores, matching Table I of the ICSA26 paper
- (With `--llm`) An AI-powered narrative analysis interpreting the results in business terms

---

## Phased Traffic Engine — How mba full Generates Traffic

When you run `mba full`, the traffic generation is not random. It executes in **6 deterministic phases** that guarantee database operations are triggered in the correct order:

| Phase | What happens | Why this order |
|---|---|---|
| **PROBE** | One GET per endpoint — liveness check | Detect dead endpoints before heavy traffic |
| **SEED** | All POST endpoints first, with coherent payloads | Create data in the database **before** reading it |
| **READ** | All GET endpoints | Trigger SELECT operations on newly created data |
| **MUTATE** | All PUT/PATCH endpoints, with real IDs from SEED | UPDATE operations on rows that actually exist |
| **STRESS** | All endpoints concurrently (GET 60%, POST 25%, PUT 10%, DELETE 5%) | Realistic load, sufficient trace volume |
| **CLEANUP** | DELETE endpoints last | Never destroy data before reads can happen |

**Phase time budget** (as fraction of `--duration`):
PROBE 5% → SEED 20% → READ 30% → MUTATE 15% → STRESS 25% → CLEANUP 5%

**Smart payload generation**: POST endpoints receive semantically coherent payloads based on path keywords. A path containing `user` gets `{username, email, password}`. A path containing `order` gets `{quantity, status, total}`. The SEED phase harvests real entity IDs from POST responses and injects them into path parameters for the MUTATE and CLEANUP phases.

Only the STRESS phase uses concurrent workers. All other phases execute sequentially to preserve ordering guarantees.

---

## Real-Time Terminal Dashboard — MBA Live UI

During `mba full`, a **Rich live terminal dashboard** displays in real time as traffic is generated. You do not need to run any extra command — it activates automatically.

```
╔══ MBA — Microservice Boundary Analyzer ═══════════════════════════════════════════════╗
║  ◈ MBA — Microservice Boundary Analyzer           v0.8.0         ║
║  Project: scenario3  ·  Services: 2  ·  Duration: 60s  ·  Workers: 5  ║
╠═══════════════════════════════════════════════════════════════╝
  ✔ DISCOVER  ✔ DEPLOY  ● TRAFFIC  ○ COLLECT  ○ ANALYZE  ○ CLEANUP
  PHASE 2/6 — READ  ████████░░░░  58%  14s / 18s
╔═════════════════════╗ COVERAGE ═══════════════════════════════════╗
  Svc    Method  Path    Status    Requests sent      234
  scen3  GET     /ord..  ✔ 200   Succeeded          231  (98.7%)
  scen3  POST    /ins..  ✔ 201   Endpoints w/ DB    3/5  ▪▪▪▫▫
  scen3  DELETE  /del..  ⊗ skip  DB ops triggered   12
╠═══════════════════════════════════════════════════════════════╝
  14:23:02  [READ]   GET  /orders → 200 OK  8ms
  14:23:03  [READ]   GET  /employees → 200 OK  11ms
```

**What the Live UI shows:**
- **Pipeline bar**: `✔ DISCOVER ✔ DEPLOY ● TRAFFIC ○ COLLECT ○ ANALYZE ○ CLEANUP` — updates in real time
- **Phase progress bar**: current phase name, phase number (e.g. `2/6`), elapsed/remaining time
- **Endpoint table**: every endpoint with method (color-coded), path, live status (⏳/✔/✗/⊗), HTTP status code, response time, DB dot indicators (`▪▪▪` = DB ops triggered)
- **Coverage panel**: requests sent/succeeded/failed, endpoints reached, DB ops count
- **Log stream**: last 5 log entries with timestamp and phase label

---

## Universal Log Ingestion — Supported Formats

MBA can accept log files from virtually any source and convert them into the internal span format for SCOM analysis. The format is detected automatically.

| Format ID | File types | What is extracted | SCOM quality |
|---|---|---|---|
| `jaeger` | `.json` | Endpoints + DB ops + parent-child links | ✅ Exact |
| `zipkin` | `.json` | Endpoints + DB ops + parent-child links | ✅ Exact |
| `otlp` | `.json` | Endpoints + DB ops + parent-child links | ✅ Exact |
| `generic_sql` | `.log`, `.txt` | HTTP lines + SQL queries — **auto-correlated** | ✅ Good (Django, Flask, SQLAlchemy, Spring) |
| `json_lines` | `.log`, `.jsonl` | HTTP/DB/event records per line | ✅ Good |
| `locust` | `.csv` | Endpoint stats (call count, response time) | ⚠ Heuristic (no DB) |
| `nginx` | `.log`, `.txt` | HTTP access log | ⚠ Heuristic (no DB) |
| `w3c` | `.log`, `.txt` | IIS Extended Log Format | ⚠ Heuristic (no DB) |

### HTTP→SQL correlation for application logs

For `generic_sql` format (Django, SQLAlchemy, Flask logs), MBA automatically correlates SQL queries to their parent HTTP request:

```
2026-06-19 15:23:01 INFO django.request: GET /orders/ 200 45ms    ← HTTP span (root)
2026-06-19 15:23:01 DEBUG django.db.backends: SELECT ... FROM orders  ← DB child span
2026-06-19 15:23:01 DEBUG django.db.backends: SELECT ... FROM users   ← DB child span
2026-06-19 15:23:02 INFO django.request: POST /orders/ 201 120ms   ← new HTTP span
2026-06-19 15:23:02 DEBUG django.db.backends: INSERT INTO orders ...  ← DB child span
```

SQL lines that appear after an HTTP line and before the next HTTP line are automatically assigned as child spans of that HTTP endpoint. This enables proper endpoint→table mapping and accurate SCOM computation from plain log files, without any instrumentation changes.

### When DB info is missing

For `nginx`, `locust`, and `w3c` formats, no database information is available. MBA will:
- Warn you clearly: `⚠ No DB operations found — SCOM will use path-based table heuristics`
- Compute an estimated SCOM based on URL path structure
- Label the results as estimated in the report

---

## The Interactive Dashboard

The dashboard is a **Plotly/Dash web application** that provides a complete visual interface for exploring SCOM results. Launch it with:

```bash
mba dashboard --run <run-id>
```

### Navigation header

The top bar shows the tool name and version, a **Run Selector** dropdown (to switch between historical runs without restarting the dashboard), a reload button, and the data source path with the timestamp of the current run.

### Overview page — KPI cards

Four summary cards at the top of the overview:

| Card | What it shows |
|---|---|
| **Total Services** | Total number of services analyzed in this run |
| **Suspicious** | Services flagged as potential Wrong Cuts |
| **Healthy** | Services above the cohesion threshold |
| **Avg SCOM (all)** | Mean SCOM across all services in this run |

### Overview page — SCOM Distribution chart

A violin/box plot showing the distribution of SCOM scores split by "Healthy" vs "Suspicious" classification. Hover over any point to see the service name and exact score.

### Overview page — SCOM Trend chart

A multi-line chart showing how SCOM scores have evolved across recent runs (automatically loaded from the run registry). This lets you track whether architectural improvements are actually moving the needle.

### Overview page — Service Cohesion Ranking

A horizontal bar chart with all services ordered by SCOM score. A vertical dashed line shows the computed threshold. Red bars = suspect, cyan bars = healthy.

### Overview page — All Services table

A sortable, filterable data table with one row per service:

| Column | Description |
|---|---|
| **#** | Rank (1 = lowest cohesion = worst) |
| **Service** | Service name |
| **SCOM** | Computed SCOM score (4 decimal places) |
| **Cohesion** | Human-readable label (Very cohesive / Cohesive / Weakly cohesive / Not cohesive) |
| **Endpoints** | Number of distinct HTTP endpoints |
| **Tables** | Number of distinct database tables accessed |
| **Status** | `✓ healthy` or `⚠ suspect` |

**Click any row to open the Service Detail View.**

### Service detail view — what you see when you click a service

Clicking a row in the overview table opens a full detail page for that service:

**Status banner**: Color-coded panel explaining whether the service is cohesive or a potential Wrong Cut, with a plain-language explanation of what was found.

**Metric cards**: SCOM score, rank, endpoint count, and table count for this specific service.

**Endpoint × Table Access Heatmap**: A heatmap where:
- Rows = HTTP endpoints
- Columns = database tables
- Cell value = number of times that endpoint accessed that table

A perfectly cohesive service has a dense, filled heatmap. A Wrong Cut has isolated rows with no shared columns.

**Multi-Metric Radar chart**: A normalized radar chart across 5 axes: SCOM Score, Endpoint Density, Table Diversity, Cohesion Rank, and Health Index.

### Overview page — Data Provenance card

Shows exactly what data the dashboard is displaying:
- Full path to the data directory
- Number of traces and spans collected
- Number of services and suspicious count
- Exact timestamp from `meta.json` (the actual run time, never an epoch default)

### Overview page — Definitions card

A built-in glossary of all metrics and concepts shown in the dashboard, so you never need to leave the UI to understand what you are looking at.

### AI Analysis card

If the run was generated with `--llm`, this card shows the AI-powered narrative analysis from the report, rendered as rich text inside the dashboard.

---

## LLM-Assisted Features

MBA can optionally use a Large Language Model for two purposes. All LLM features are **opt-in** via the `--llm` flag.

### 1. Smarter instrumentation code generation

When `--llm` is set, MBA asks the LLM to generate the OpenTelemetry bootstrap file for each service, tailored to the detected framework and existing code structure. This produces more accurate instrumentation than the static templates, especially for non-standard project layouts.

### 2. AI-powered report generation

When `--llm` is set, the final report includes a `## AI-Powered Analysis` section where the LLM provides:
- Plain-language interpretation of each service's SCOM score
- Hypothesis about what business domains might be mixed
- Concrete refactoring recommendations

### LLM backend priority

MBA uses a **cascading fallback** strategy:

1. **OpenRouter** (cloud) — if `OPENROUTER_API_KEY` is set in your environment
2. **Ollama** (local) — if Ollama is installed and `qwen2.5-coder` is available
3. **Static template** — fallback if both LLM options fail (no error, just no AI-generated content)

**Setting up OpenRouter:**

```bash
export OPENROUTER_API_KEY="sk-or-..."
mba full . --llm
```

**Setting up Ollama (free, local):**

```bash
# Install Ollama from https://ollama.com
ollama pull qwen2.5-coder
mba full . --llm   # will use Ollama automatically
```

---

## Supported Languages and Frameworks

### Python (full support)

| Framework | Auto-detected | Instrumented |
|---|---|---|
| Flask | ✅ | ✅ |
| FastAPI | ✅ | ✅ |
| Django / DRF | ✅ | ✅ |
| Starlette | ✅ | ✅ |
| Tornado | ✅ | ✅ |

Detection uses: `requirements.txt`, `pyproject.toml`, `setup.py`, import analysis, and entry point filename heuristics.

### Java (trace analysis, partial instrumentation)

Requires the OpenTelemetry Java agent (`opentelemetry-javaagent.jar`). MBA downloads it automatically for `mba teastore`. Manual Java projects require pre-attaching the agent; MBA handles trace parsing and SCOM computation.

### Node.js / JavaScript

Trace parsing supported. Auto-instrumentation via the `@opentelemetry/auto-instrumentations-node` package is generated by MBA.

### PHP

Trace parsing supported. Instrumentation via the OpenTelemetry PHP extension.

### .NET

Trace parsing supported. Instrumentation via the OpenTelemetry .NET SDK.

> **Framework detection confidence**: MBA assigns a confidence score to each detection result. If confidence is low, it falls back to static templates and logs a warning. Use `--language` to force the correct language if auto-detection fails.

---

## Output Files and Artifacts

After a successful run, the following files are saved to the run registry at `data/runs/<run-id>/`:

```
data/runs/20260619_154801_scenario3/
├── meta.json                           ← Complete run metadata (timestamp, services, SCOM, traffic stats)
├── report.md                           ← Full Markdown analysis report
├── service_rank.csv                    ← All services ranked by SCOM with flags
├── service_scom.csv                    ← Raw SCOM scores before ranking
├── suspicious_services.csv             ← Only the flagged services
└── interim/
    └── endpoint_table_map.csv          ← Endpoint → table mapping (used by dashboard heatmap)
```

### `meta.json` — Run metadata

```json
{
  "id": "20260619_154801_scenario3",
  "timestamp": "2026-06-19T15:48:01.819840",
  "project_name": "scenario3",
  "language": "python",
  "duration_seconds": 407.5,
  "all_success": true,
  "endpoints_total": 10,
  "tables_total": 8,
  "traffic_requests": 868,
  "traffic_ok": 867,
  "traffic_failed": 1,
  "scom_results": [
    { "service_name": "scenario3", "scom_score": 0.0435, "endpoints_count": 5, "tables_count": 4 },
    { "service_name": "setup",     "scom_score": 0.0000, "endpoints_count": 5, "tables_count": 4 }
  ],
  "errors": [],
  "warnings": []
}
```

### `service_rank.csv` — Per-service SCOM results

| service_name | scom_score | endpoints_count | tables_count | method | rank | threshold_value | threshold_method | is_suspicious |
|---|---|---|---|---|---|---|---|---|
| setup | 0.0000 | 5 | 4 | weighted | 1 | 0.010875 | percentile | True |
| scenario3 | 0.0435 | 5 | 4 | weighted | 2 | 0.010875 | percentile | False |

### `interim/endpoint_table_map.csv` — Endpoint-to-table mapping

| service_name | endpoint_key | table | count |
|---|---|---|---|
| scenario3 | GET /scenario3/employees | employees | 12 |
| scenario3 | GET /scenario3/employees | customers | 8 |
| scenario3 | GET /scenario3/orders | orders | 15 |
| scenario3 | GET /scenario3/orders | products | 11 |

---

## Run Registry — History and Comparisons

Every run is persisted to `data/runs/` with a `runs.json` index. The index is updated atomically to avoid corruption during concurrent runs.

```
data/
└── runs/
    ├── runs.json                          ← Master index of all runs
    ├── last_run.txt                       ← ID of the most recent run
    ├── 20260619_154801_scenario3/
    │   └── (files described above)
    └── 20260618_092214_teastore/
        └── (files described above)
```

### Accessing runs programmatically

```python
from boundary_analyzer.auto.run_registry import list_runs, load_run_meta, get_run_path

# List all runs
runs = list_runs()  # newest first
for r in runs:
    print(r["id"], r["timestamp"], r["project_name"])

# Load a specific run's metadata
meta = load_run_meta("20260619_154801_scenario3")
print(meta["scom_results"])

# Get the filesystem path to a run's directory
path = get_run_path("20260619_154801_scenario3")
```

---

## Configuration Reference

`mba run` reads from a YAML configuration file (default: `config/settings.yaml`). `mba full` and `mba analyze` do not need this file — all settings are passed as CLI arguments.

```yaml
# config/settings.yaml

# Jaeger connection
jaeger_base_url: "http://localhost:16686"
service_name: "my-service"          # Exact service name as it appears in Jaeger
lookback_minutes: 10                # How far back to look for traces
limit_traces: 500                   # Max traces to collect per run

# Output
output_dir: "data/raw/traces"       # Where to save raw trace JSON files

# SCOM computation
scom_method: "weighted"             # "weighted" (default) | "paper" | "simple"
                                    #   weighted  — w_ij = freq(e_i) × freq(e_j)  [production default]
                                    #   paper     — exact ICSA26 formula, unweighted + also reports weighted
                                    #   simple    — unweighted only, quick sanity check
table_weighting: true               # Weight tables by access count (for future extensions)
endpoint_weighting: true            # Weight endpoint pairs by invocation frequency (used by 'weighted')  

# Threshold for suspicious classification
threshold_method: "percentile"      # "percentile" | "zscore" | "fixed"
threshold_percentile: 25.0          # Used when threshold_method = "percentile"
threshold_zscore: -1.5              # Used when threshold_method = "zscore"
scom_threshold: 0.5                 # Used when threshold_method = "fixed"
```

### SCOM method comparison

| Method | Formula | Report output | Best for |
|---|---|---|---|
| `weighted` | `SCOM = Σ wᵢⱼ·CI / (Σ wᵢⱼ × CI_max)` where `wᵢⱼ = freq(eᵢ)×freq(eⱼ)` | Single SCOM score | Production systems with uneven traffic |
| `paper` | Exact Section III-C formula: `SCOM = Σ CI / (N × CI_max)` (unweighted) **+** also computes weighted for comparison | **Two columns** — matching Table I/II of the ICSA26 paper | Academic reproducibility |
| `simple` | Same as `paper` but does not compute the weighted variant | Single SCOM score | Quick sanity checks |

> **Why does `paper` produce two columns?** The ICSA26 paper (Section IV-B) reports both unweighted and weighted SCOM for each service. Using `--scom-method paper` reproduces this exact output in `service_scom.csv` and in the Markdown report.

### Threshold method guide

| Method | When to use |
|---|---|
| `percentile` | Relative analysis: "flag the bottom 25% of services" |
| `zscore` | Statistical outliers: flag services unusually far below the mean |
| `fixed` | Policy: "all services must have SCOM ≥ 0.5" |

---

## Understanding Your Results

### A service has SCOM = 0.0 — is it definitely a Wrong Cut?

Not necessarily. SCOM = 0.0 means one of three things:

1. **Genuinely bad boundary**: endpoints access completely different tables → likely Wrong Cut.
2. **Single endpoint**: a service with fewer than 2 endpoints always receives 0.0 by definition.
3. **No database operations**: a service that does no database work (e.g., a pure computation service or API gateway) will show 0.0.

Always look at the **endpoint count** and **table count** alongside the score. A service with 1 endpoint and 0 tables has SCOM=0.0 for structural reasons, not because of a Wrong Cut.

### A service has SCOM = 1.0 — is it perfect?

SCOM = 1.0 means all endpoint pairs share all database tables. This is the ideal case for a domain-focused service. However, it does not mean the service has no problems — it could still be doing too much within one domain. SCOM measures **data cohesion**, not service size or complexity.

### Why is SCOM different from what I calculated manually?

MBA uses **frequency-weighted SCOM**. The weight of each endpoint pair is `freq(eᵢ) × freq(eⱼ)`, derived from actual trace counts. Endpoints that are called rarely have less influence on the score than high-traffic endpoints. A manual calculation with uniform weights will give a different result — this is intentional.

### The heatmap is empty in the dashboard

The heatmap requires `interim/endpoint_table_map.csv` in the run directory. This file is created by Step 5 of the pipeline and is saved with all runs from v0.7.8 onward. Runs created with earlier versions may not have this file.

### "Avg SCOM (all)" vs individual service SCOM

The **Avg SCOM (all)** card shows the arithmetic mean of SCOM scores across **all services** in the run, including setup/infrastructure services that often have 0.0. The per-row SCOM is the individual service score. Always compare them in context.

---

## Troubleshooting

### Docker not found

```
[ERROR] Docker is not running or not accessible.
```

**Fix**: Start Docker Desktop, wait for it to initialize, then retry.

### Jaeger service name mismatch

```
[WARNING] Service name mismatch between discovery and Jaeger: discovered=['scenario3'],
          not found in Jaeger.
```

**Cause**: The `OTEL_SERVICE_NAME` environment variable in the service's Docker environment does not match the service name detected by MBA.

**Fix**: Use `--reset-jaeger` to clear old trace data, or check that `OTEL_SERVICE_NAME` is set correctly in your `docker-compose.yml`.

### mba.exe locked on Windows (cannot reinstall)

```
ERROR: [WinError 32] The process cannot access the file because it is being
used by another process: 'mba.exe'
```

**Cause**: A dashboard process is still running.

**Fix**: Close all running dashboard processes, then reinstall.

### Dashboard shows 500 error when clicking a service

**Cause**: Installed package is out of date relative to source.

**Fix**:
```bash
# Patch the installed files manually (when mba.exe is locked)
python -c "
import shutil, os
src = 'src/boundary_analyzer'
dst_base = 'C:/Users/<user>/AppData/Roaming/Python/Python313/site-packages/boundary_analyzer'
for f in ['dashboard/app.py', 'dashboard/layout_components.py']:
    shutil.copy2(os.path.join(src, f), os.path.join(dst_base, f))
"
```

Or close the dashboard and run `pip install -e .` to switch to editable mode.

### All services show SCOM = 0.0

**Causes**:
- The services are not exporting traces to Jaeger. Check that `OTEL_EXPORTER_OTLP_ENDPOINT` is set and points to the correct Jaeger container.
- Traffic was generated but no database operations were traced. Check that database instrumentation is enabled (PostgreSQL → `opentelemetry-instrumentation-psycopg2`, MongoDB → `opentelemetry-instrumentation-pymongo`, etc.)
- Use `--reset-jaeger` to avoid contamination from previous runs.

### LLM features not working

```
[WARNING] OpenRouter/Ollama call returned None for ./my-service
[WARNING] All LLM options failed — using static template
```

**Fix for OpenRouter**: Set `OPENROUTER_API_KEY` in your environment.

**Fix for Ollama**:
```bash
# Install Ollama from https://ollama.com then:
ollama pull qwen2.5-coder
```

The tool falls back gracefully to static templates — no functionality is lost, only the AI-generated content.

### Debug mode

Set `MBA_DEBUG=1` to get full stack traces for unexpected errors:

```bash
MBA_DEBUG=1 mba full . --verbose
```

---

## License

MIT — see [LICENSE](LICENSE) for details.

---

## Acknowledgments

MBA implements the SCOM metric adapted from:

> Counsell, S., Swift, S., & Crampton, J. (2006). *The interpretation and utility of three cohesion metrics for object-oriented design*. ACM Transactions on Software Engineering and Methodology, 15(2), 123–149.

The Wrong Cut concept is described in:

> Richardson, C. (2019). *Microservices Patterns*. Manning Publications.

TeaStore benchmark:

> Kounev, S., et al. (2018). *TeaStore: A Micro-Service Reference Application for Benchmarking, Model-Driven Performance Prediction, and Resource Management*. ICPE 2018.

---

<div align="center">
<sub>Built with Python · OpenTelemetry · Jaeger · Dash · Plotly</sub>
</div>
