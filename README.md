# measure-automation

A tool for analyzing microservice boundaries using runtime traces from OpenTelemetry/Jaeger. It computes Service Cohesion Measure (SCOM) to detect services with low cohesion that may have wrong boundaries.

## Prerequisites

- Python 3.11+
- Jaeger instance running (http://localhost:16686 by default)
- Your services instrumented with OpenTelemetry

## Installation

```powershell
python -m pip install -e .
```

Or install dependencies manually:

```powershell
python -m pip install requests pandas pyyaml dash plotly
```

## Configuration

Edit `config/settings.yaml`:

```yaml
jaeger_base_url: "http://localhost:16686"
service_name: "YOUR_SERVICE_NAME"  # Set to a real service from Jaeger
lookback_minutes: 10
limit_traces: 20
output_dir: "data/raw/traces"

# SCOM calculation method
# - "paper": CI/CImax normalization from the paper (endpoints < 2 => 0)
# - "weighted": weighted Jaccard (legacy)
# - "simple": unweighted Jaccard (legacy)
scom_method: "weighted"  # Options: "paper", "weighted" or "simple"
table_weighting: true
endpoint_weighting: true

# Threshold method for suspicious services
threshold_method: "percentile"  # Options: "percentile", "zscore", or "fixed"
threshold_percentile: 25.0
threshold_zscore: -1.5
scom_threshold: 0.5
```

## Pipeline Steps

Run the pipeline in order:

## One-command (Professional) Usage

After installation, you can run the full pipeline with a single command.

```powershell
boundary-analyzer run
```

Equivalent:

```powershell
python -m boundary_analyzer run
```

### Options

- **`--skip-collect`**
  Skips Step 01 (Jaeger trace collection) and reuses the existing traces in the folder configured by `output_dir` in `config/settings.yaml`.

- **`--dashboard`**
  Launches the dashboard after the pipeline completes.

- **`--data-dir <path>`**
  Base directory containing `interim/` and `processed/` for the dashboard (default: `data`).

- **`--dash-host <host>`**
  Dashboard bind host (default: `127.0.0.1`). Use `0.0.0.0` to expose on LAN.

- **`--dash-port <port>`**
  Dashboard port (default: `8050`).

- **`--settings <path>`**
  Validates that the settings file exists. The pipeline steps currently read `config/settings.yaml`.

### Examples

Run everything (collect traces + compute results + report):

```powershell
boundary-analyzer run
```

Reuse traces already collected and open the dashboard:

```powershell
boundary-analyzer run --skip-collect --dashboard
```

Launch only the dashboard:

```powershell
boundary-analyzer dashboard
```

The dashboard is available by default at:

`http://127.0.0.1:8050`

Launch the dashboard for a different results folder:

```powershell
boundary-analyzer dashboard --data-dir .\demo-service\scom_report
```

## Setup mode (when your project has no Jaeger / OpenTelemetry)

If your target project is not instrumented yet (no OpenTelemetry, no Jaeger), you can use the auto-setup command.
It will:
- detect the framework
- install OpenTelemetry packages (unless you pass `--no-install`)
- generate an instrumentation file for your app
- start Jaeger (unless you pass `--no-jaeger`)
- ask you to restart your app and send some traffic
- collect traces and run the analysis

```powershell
boundary-analyzer setup --project-path .\path\to\your-service
```

Common options:

- **`--framework <name>`**: force a framework instead of auto-detect
- **`--service-name <name>`**: set the Jaeger service name
- **`--no-jaeger`**: skip starting Jaeger (use if already running)
- **`--no-install`**: skip installing OpenTelemetry packages
- **`--jaeger-host <host>`**: Jaeger host (default: `localhost`)

Example:

```powershell
boundary-analyzer setup --project-path .\demo-service --service-name demo-service
```

Run setup and open the dashboard on the generated results:

```powershell
boundary-analyzer setup --project-path .\demo-service --service-name demo-service --dashboard
```

### Step 01: Collect traces from Jaeger

Collects trace data from Jaeger API for a specific service.

```powershell
python .\src\boundary_analyzer\pipeline\step_01_collect_traces.py
```

**Output:** `data/raw/traces/jaeger_traces_{service}_{timestamp}.json`

### Step 02: Read and flatten traces

Reads all trace files and flattens spans into a CSV format.

```powershell
python .\src\boundary_analyzer\pipeline\step_02_read_traces.py
```

**Output:** `data/interim/spans.csv`

### Step 03: Find endpoints

Extracts HTTP endpoints from spans (method + route normalization).

```powershell
python .\src\boundary_analyzer\pipeline\step_03_find_endpoints.py
```

**Output:** `data/interim/endpoints.csv`

### Step 04: Find database tables

Extracts database table names from SQL operations in spans.

```powershell
python .\src\boundary_analyzer\pipeline\step_04_find_db_tables.py
```

**Output:** `data/interim/db_operations.csv`

### Step 05: Build endpoint-table mapping

Links endpoints to database tables by walking the span parent chain.

```powershell
python .\src\boundary_analyzer\pipeline\step_05_build_mapping.py
```

**Output:** `data/interim/endpoint_table_map.csv`

### Step 06: Compute SCOM scores

Calculates Service Cohesion Measure for each service using weighted Jaccard similarity.

```powershell
python .\src\boundary_analyzer\pipeline\step_06_compute_scom.py
```

**Output:** `data/processed/service_scom.csv`

### Step 07: Rank and flag suspicious services

Applies statistical threshold (percentile, Z-score, or fixed) to flag services with low cohesion.

```powershell
python .\src\boundary_analyzer\pipeline\step_07_rank_and_flag.py
```

**Output:** 
- `data/processed/service_rank.csv`
- `data/processed/suspicious_services.csv`

### Step 08: Generate report

Creates a Markdown report with analysis results.

```powershell
python .\src\boundary_analyzer\pipeline\step_08_make_report.py
```

**Output:** `reports/latest/report.md`

## Dashboard

Launch the interactive dashboard to visualize results:

```powershell
python .\src\boundary_analyzer\dashboard\app.py
```

The dashboard will be available at `http://localhost:8050`

## Quick Start

Run all steps in sequence:

```powershell
python .\src\boundary_analyzer\pipeline\step_01_collect_traces.py
python .\src\boundary_analyzer\pipeline\step_02_read_traces.py
python .\src\boundary_analyzer\pipeline\step_03_find_endpoints.py
python .\src\boundary_analyzer\pipeline\step_04_find_db_tables.py
python .\src\boundary_analyzer\pipeline\step_05_build_mapping.py
python .\src\boundary_analyzer\pipeline\step_06_compute_scom.py
python .\src\boundary_analyzer\pipeline\step_07_rank_and_flag.py
python .\src\boundary_analyzer\pipeline\step_08_make_report.py
python .\src\boundary_analyzer\dashboard\app.py
```

## Documentation

See `docs/research_method.md` for detailed information about:
- Core concepts (coupling, cohesion, wrong cuts)
- Analysis method and limitations
- SCOM calculation formula
- Threshold selection methods
- Future improvements
