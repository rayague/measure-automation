# Changelog

## v0.8.2 (2026-07-06)

### TeaStore Docker cleanup fix — leftover containers no longer break the next run

Found live: `mba teastore` failed with `ports are not available: exposing port
TCP 0.0.0.0:8080` because a container from an earlier failed run was never
removed. Root cause was two independent bugs in `teastore_runner.py`:

- **`docker_compose_up()` was called before the `try`/`finally` block** in
  `run_teastore()`. If it raised partway through (port conflict, image pull
  failure, anything), the `finally` cleanup never ran, so any containers that
  *did* start were left running forever. Moved the call inside `try`.
- **`_docker_cleanup_teastore()`'s pre-flight `docker compose down -v` targeted
  the wrong compose file** (`_COMPOSE_SRC`, inside the installed package under
  a `teastore/` folder — Compose project name `teastore`) instead of the file
  actually used to start the containers (`_PATCHED_COMPOSE`, under
  `~/.cache/boundary_analyzer/` — project name `boundary_analyzer`). The
  cleanup therefore silently targeted a project that was never running.
- **The container/network prune fallback used an invalid filter**:
  `docker container prune --filter name=...` and `docker network prune
  --filter name=...` both reject `name` (the daemon only supports `until` and
  `label` for these two commands — `Error response from daemon: invalid
  filter 'name'`), so this fallback has silently done nothing since it was
  written. Replaced with `docker ps -aq --filter name=...` /
  `docker network ls -q --filter name=...` followed by explicit `rm -f`.
- 10 tests added for the cleanup fix (`tests/test_teastore_runner.py`).
  595 passed, no regressions.

## v0.8.1 (2026-07-05)

### TeaStore traffic fix, universal ingestion hardening, span dedup

- **TeaStore traffic generator fixed**: was requesting `/category/1`, `/product/1`
  (path segments) which 404 against TeaStore's real routing — verified against
  TeaStore's own source (`CategoryServlet.java`/`ProductServlet.java`) that IDs are
  read from query-string params (`?category=1&page=1`, `?id=1`). The generator now
  discovers real category/product IDs by parsing the live index/category pages
  instead of guessing IDs, with a static-page fallback if the catalog is empty.
- **Span deduplication in the ingestion pipeline** (`run_pipeline.py`): traces
  repeated across multiple input files (e.g. Jaeger's per-service export returning
  the full multi-service trace once per participating service) are now deduplicated
  by `(trace_id, span_id)` before analysis, fixing inflated endpoint/table frequency
  counts that skewed the weighted SCOM score.
- **Universal log ingestion `raw_text` guaranteed fallback**: any non-empty file
  that matches none of the 8 structured formats (Jaeger/Zipkin/OTLP/Locust/nginx/
  W3C/generic_sql/json_lines) is now still ingested — one event span per line,
  with best-effort inline HTTP/SQL recognition — instead of raising an error.
  Ingestion report now surfaces per-file span/HTTP/DB counts, low-confidence
  warnings, and duplicate-span counts.
- **CLI fixes**: `mba benchmark teastore --wait` default 300→900s (was timing out
  before TeaStore's 6 JVMs finish starting, inconsistent with `mba teastore`'s
  own 900s default); TeaStore benchmark description corrected MySQL (was
  mislabeled PostgreSQL).
- **Live terminal dashboard version fix** (`auto/live_ui.py`): version string was
  hardcoded to a stale `"0.7.8"` literal; now reads `boundary_analyzer.__version__`.
- **Tests**: added `tests/test_log_ingestion.py` (23 tests — the ingestion module
  had zero prior coverage), `tests/test_pipeline_dedup.py`, `tests/test_teastore_runner.py`
  (7 tests for the ID-discovery fix). 592 passed, 3 skipped, no regressions.

## v0.8.0 (2026-07-05)

### Major — TeaStore benchmark automated, CLI overhaul

- **TeaStore real ID discovery**: traffic generator now scrapes the live TeaStore
  homepage to discover real category/product IDs, fixing broken URLs like
  `/category/1` (404) → `/category?category=1` (200).
- **TeaStore runner module** (`auto/teastore_runner.py`): OTel agent management,
  Docker pre-flight checks, `--prune` flag, user-friendly error hints,
  parallel container startup, 3x-consecutive-200 health verification.
- **CLI**: `--wait` default 300→900, `--prune` flag for `mba teastore`,
  `runs compare` JSON null-safe key lookup.
- **Dashboard fixes**: `app.py`, `callbacks.py`, `charts.py` — null guards,
  SCOM display, heatmap fallbacks.
- **Log ingestion**: `raw_text` format added, `generic_sql` multi-line
  correlation improved.
- **Deleted dead code**: `metrics/cohesion_rules.py` removed.
- **590 tests pass**, 3 skipped (no regressions).

## v0.7.8 (2026-06-19)

### Critical fixes — Dashboard heatmap now works end-to-end

- **Dashboard heatmap now has data (Problem B)**: 
  - `orchestrator.py` now includes `mapping_df` in `report.scom_results` alongside `scom_df`/`rank_df`.
  - `cli.py` now reads `interim/endpoint_table_map.csv` and adds it to `report.scom_results["mapping_df"]`.
  - `run_registry.py:save_run()` explicitly saves `mapping_df` to `interim/endpoint_table_map.csv` in the run directory (independent of temp-dir copy).
  - `run_registry.py:save_run()` also copies `interim/` and `processed/` directories from the temp directory for full data preservation.
  - `_load_endpoint_table_map_from` falls back to `endpoint_table_map.csv` at run dir root with proper try/except.
  - `_build_heatmap` guards every column access (`service_name`, `endpoint_key`, `table`, `count`) — returns empty figure gracefully if any column is missing.
- **Epoch date "1970-01-01" fixed (Problem C)**: `_data_provenance_card` now tries multiple `service_rank.csv` paths (processed/ then root), and reads `meta.json` timestamp for accurate "Generated" date.
- **Avg SCOM label clarified (Problem D)**: Changed from "Avg SCOM" to "Avg SCOM (all)" to distinguish from individual service SCOM.
- **`_discover_endpoints_for_service` service_dir fix**: When `entry_points` contain absolute paths, use the parent directory as `service_dir` instead of `project_info.root_dir`.

### Previous v0.7.7 (already published)

- `_build_heatmap` KeyError 'count' — wrong fallback `service_scom.csv` removed
- CLI `runs show` showed `?` for SCOM=0.0 — `or` bug fixed with `is not None`
- All French labels removed (English: "Very cohesive", "Cohesive", etc.)
- DataTable hover CSS + "Cohésion" → "Cohesion"

## v0.7.6 (2026-06-19)

### Bug fix

- **`_load_llm_analysis` NameError**: `REPORT_FILE` imported as `_REPORT_FILE` but used without underscore. Fixed by using `_REPORT_FILE`.

## v0.7.5 (2026-06-19)

### Dashboard & SCOM classification

- **Dashboard**: `_load_service_rank_from`, `_load_endpoint_table_map_from`, and `_get_data_freshness` now fall back to run-registry filenames (`service_rank.csv`, `service_scom.csv`, `meta.json`) when the old pipeline paths (`processed/`, `interim/`) don't exist. Fixes `UPDATED: unknown` on the dashboard.
- **SCOM cohesion labels**: New `classify_scom()` function in `_utils.py` with thresholds (≥0.8 Très cohésif, ≥0.5 Cohésif, ≥0.3 Peu cohésif, <0.3 Pas cohésif). Added "Cohésion" column to both `mba runs show` CLI output and the dashboard table.
- **Tests**: 567 passed, 0 failed — no regressions.

## v0.7.4 (2026-06-19)

### Bug fixes & resilience improvements

- **Version sync**: `__version__` bumped from 0.6.6 → 0.7.4 to match `pyproject.toml`.
- **Jaeger reset** (`_ensure_jaeger_ports_free`): Added port-based container lookup (`docker ps --filter publish=<port>`) alongside name-based lookup. Fixes `--reset-jaeger` failing when Jaeger container has a different name.
- **Jaeger reset** (`_reset_jaeger_container`): Now accepts `otlp_port`, searches by both name and published port, passes `otlp_port` to `start_jaeger()`. Also called in the local-process deployment branch.
- **Trace isolation** (`_export_jaeger_traces`): Added `start_time` parameter. Traces are now filtered client-side by span `startTime`, preventing old traces from polluting SCOM analysis across runs.
- **Alpine Dockerfile** (`_generate_otel_dockerfile`): Fixed line-index shift logic — uses `num_inserted` counter instead of hardcoded `+1`, preventing ENTRYPOINT corruption on Alpine images.
- **Report path** (`orchestrator.py`): `output_dir` is now only deleted if analysis step failed. Temp dir cleaned in `cli.py` after `save_run`.
- **DNS fallback** (`_build_compose_override`): When `include_jaeger=False`, `otel_host` is forced to `host.docker.internal` so services never depend on fragile Docker DNS resolution.
- **Java volume quoting**: Removed nested double quotes in volume mount string.

## v0.7.2 (2026-06-19)

### Traffic gen for POST endpoints, Jaeger isolation, endpoint count display

- **traffic.py**: POST/PUT/PATCH without OpenAPI schema now guesses a JSON body from the endpoint path (e.g. `/employees/insert` → `{"name":"...", ...}`, `/delete` → `{"id":1}`). Falls back through multiple body shapes on 4xx.
- **orchestrator.py**: New `_reset_jaeger_container()` — stops/removes existing Jaeger and starts fresh, activated by `--reset-jaeger` CLI flag.
- **cli.py**: Added `--reset-jaeger` flag to `mba full`.
- **run_registry.py**: `_build_run_meta` falls back to SCOM CSV endpoint count when `project.services` have empty endpoints (fixes `Endpoints: 0` in `mba runs show`).

## v0.7.1 (2026-06-19)

### Hotfix — missing `import socket` in deploy.py

- **deploy.py**: `_is_port_in_use()` used `socket` without importing it, crashing the pipeline before any deploy could start. Added `import socket`.

## v0.7.0 (2026-06-19)

### Full audit — 71 bugs fixed (11 P0, 17 P1, 43 P2)

- **11 P0 fixes**: entry_points[0] IndexError, health_endpoint=None→URL, 4xx treated healthy, error msg empty list, temp dirs leak, pandas import order, hardcoded report path, bool(NaN)=True, logging.basicConfig no-op, Windows zombies (`_kill_process_tree`)
- **17 P1 fixes**: Docker DNS order, CWD in check_container_alive, host.docker.internal Linux, empty ProjectInfo, lookback CLI arg, dashboard dropdown, TOCTOU runs.json, flush delay, --data-dir ignored, multi-network connect, Alpine build deps, LLM multi-lang, per-service Dockerfile, volume path quoting, fallback CID, LLM numbered backups, zero table falsy, dashboard KeyErrors
- **Run comparison**: `mba runs compare` — side-by-side SCOM per service with Δ column
- **SCOM trend chart**: multi-run timeline per service in dashboard
- **Process management**: cross-platform `_kill_process_tree()` (Unix SIGKILL / Windows taskkill)
- **Adaptive polling**: hardcoded `time.sleep(3/5)` replaced with adaptive Jaeger API poll and trace wait

## v0.6.6 (2026-06-18)

### Fix traces never reaching external Jaeger (SCOM=0 root cause)

- **deploy.py**: `_resolve_external_jaeger_host()` now returns the Jaeger **container name** instead of its bridge-network IP. After `docker compose up`, `_connect_jaeger_to_compose_network()` attaches the external Jaeger container to the compose project's user-defined network. Services resolve the Jaeger hostname via Docker DNS instead of trying (and failing) to reach an IP on a separate bridge network.

  **Before**: `OTEL_EXPORTER_OTLP_ENDPOINT=http://172.17.0.2:4318` — unreachable from compose user-defined network.
  **After**: `OTEL_EXPORTER_OTLP_ENDPOINT=http://mba-jaeger:4318` — resolves via DNS on the shared compose network.

  Falls back to bridge gateway IP or `host.docker.internal` if no Jaeger container is found.

## v0.6.5 (2026-06-18)

### SCOM robustness, Jaeger reachability, container health, new `analyze` command

- **mapping_builder.py**: `_normalize_id()` fixes SCOM=0 root cause — trace_id/span_id now consistently converted to strings across DataFrames, preventing dict-key lookup failures when pandas reads hex IDs as float from CSV. Added debug logging for chain-walk statistics (found/fallback/no-parent counts).
- **deploy.py**: `_resolve_external_jaeger_host()` replaces raw `host.docker.internal` with Docker container IP resolution (works in Alpine/musl containers) and Docker bridge gateway fallback — `host.docker.internal` is last resort.
- **deploy.py**: `_check_container_alive()` — post-deploy container health check. If a container has exited/crashed, captures `docker logs --tail 20` and surfaces it in the deployment result with a clear error message.
- **cli.py**: New `mba analyze <traffic_file>` subcommand — runs SCOM pipeline (steps 2–8) on an existing Jaeger JSON traces file without deployment or trace collection. Supports `--language`, `--skip-no-db`, `--threshold`, `--dashboard`.
- **cli.py**: `--language` flag added to `mba analyze` and `mba full` — bypasses auto-detection for non-Python projects.

### Tests
- 3 new tests: container alive without Docker, external Jaeger host resolution, trace_id/span_id format mismatch cross-DataFrame mapping

## v0.6.4 (2026-06-18)

### Fix SCOM = 0 services and missing DB instrumentation

- **deploy.py**: Added `_OTEL_DB_PACKAGES` (psycopg2, sqlalchemy, dbapi, pymongo, redis, mysql, pymysql) — DB instrumentation packages are now installed in the Docker image, fixing `Psycopg2Instrumentor` import failures and missing `db.system` spans
- **db_table_extractor.py**: Warning logged when 0 DB spans are found among total spans, guiding users to check DB instrumentation
- **mapping_builder.py**: Warning logged when >50% of endpoint-to-table mappings are `unknown_endpoint`, flagging parent-span chain walking failures
- **orchestrator.py**: Case-insensitive service name matching between discovered services and Jaeger; warning when discovered service names are absent from Jaeger

## v0.6.3 (2026-06-18)

### Reuse manually started Jaeger in Docker Compose

- **deploy.py**: `_resolve_compose_jaeger()` — when Jaeger is already healthy on ports 4318/16686 (e.g. `docker run --name jaeger`), MBA reuses it instead of failing with "port already in use". Compose services reach it via `host.docker.internal:4318` with `extra_hosts: host-gateway`
- **deploy.py**: `_build_compose_override()` accepts `include_jaeger` and `otel_host` for external Jaeger mode
- **deploy.py**: clearer error when ports are busy but Jaeger is not healthy

## v0.6.0 (2026-06-18)

### Port Conflict Detection & Recovery

- **deploy.py**: `_ensure_jaeger_ports_free()` — proactive check before `docker compose up`. Frees ports 4318/16686 by force-removing zombie `mba-jaeger` container. Clear error if another process holds the port
- **deploy.py**: `_parse_docker_error()` — scans streaming output for 4 known patterns ("port is already allocated", "cannot connect to daemon", "permission denied", "no such image") and produces a specific fix message instead of the generic "check syntax"
- **orchestrator.py**: `_try_cleanup()` now force-removes `mba-jaeger` container after every run to prevent zombie containers

### LLM Reliability (OpenRouter + Ollama)

- **prompts.py**: New rule #8 — LLM is explicitly allowed to add OTel instrumentation around database operations as long as the original query logic is unchanged. Prevents false refusals like "Cannot instrument database queries"
- **instrumentation.py**: Two-stage retry — if OpenRouter fails (None, refusal, or syntax error), automatically retries with local Ollama before giving up
- **orchestrator.py**: Clear messages showing what was tried ("OpenRouter API key detected — will fall back to local Ollama if needed") and actionable tips ("Install Ollama (ollama.com) and pull qwen2.5-coder")

## v0.5.0 (2026-06-18)

### Robustness & Performance

- **deploy.py**: Threaded streaming for `docker compose up` — real-time output on stderr with 60-line rotating tail for error diagnostics, 300s timeout, `proc.stdout.close()` on Windows to unblock reader thread
- **deploy.py**: Platform-aware Docker daemon timeout — 25s on Windows (WSL2/Hyper-V latency), 10s on Linux
- **deploy.py**: Deduplicated `_find_otel_dockerfiles` → `find_otel_dockerfiles` (public), removed duplicate from orchestrator
- **orchestrator.py**: Proactive Docker check with visible "waiting up to 60s..." feedback before any deploy; `_ensure_docker()` with real elapsed time reporting
- **instrumentation_marker.py**: `cleanup_orphans()` — scans for orphan `.mba_bak`, `.mba-Dockerfile`, `.mba-compose-override.yml` without marker (pre-v0.4.0 compat). Uses `os.walk` with directory pruning to skip `.venv`/`node_modules`/`__pycache__`
- **prompts.py**: LLM sentinel `jaeger_host="env"` now tells the model to read `os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", ...)` at runtime instead of generating `http://env:4318`
- **orchestrator.py**: LLM instrumentation passes `"env"` for Docker Compose projects, `"127.0.0.1"` for local projects

### Tests
- 565 tests (+7 new), 0 regressions
- 7 new tests: `build_instrumentation_prompt(jaeger_host="env")` sentinel, `_extract_host_port()` with all formats including `127.0.0.1:5000:5000`

## v0.4.0 (2026-06-17)

### Version-Aware Instrumentation System (new feature)

- **NEW**: `.mba-instrumented` marker file written after successful deploy, recording version, mode, and all artifacts created (backups, Dockerfile overrides, compose overrides)
- **NEW**: `check_stale_instrumentation()` detects instrumentation from a different MBA version at the start of `mba full` and automatically cleans up before re-instrumenting
- **NEW**: `cleanup_instrumentation()` restores backup files (`.mba_bak` → original), deletes generated `.mba-Dockerfile` and `.mba-compose-override.yml` files
- **NEW**: On each run, if marker exists with a different version, cleanup runs automatically before discovery

### Docker Compose Robustness (bug fixes)

- **deploy.py**: Added `subprocess.TimeoutExpired` handler in `deploy_docker_compose()` — previously an unhandled crash; now produces a clear `DOCKER_COMPOSE_FAILED` error
- **deploy.py**: `_generate_otel_dockerfile()` now logs warnings on all 7 silent failure paths instead of returning `(None, None)` with no user feedback
- **discover.py**: Fixed port extraction from Docker Compose YAML. The old `p.rsplit(":", 1)[0].rsplit(":", 1)[0]` was broken for `host_ip:host_port:container_port` format (e.g., `127.0.0.1:5000:5000`). Now uses a proper `_extract_host_port()` helper

### LLM Chain Improvements (bug fixes + diagnostics)

- **instrumentation.py**: Added `logger.warning()` for each reason the LLM returns `None`: API/Ollama failure, `"ERROR:"` refusal (with the actual reason), and `SyntaxError` in generated code. Previously all three were silent
- **context.py**: Extended `_find_main_file()` to recognize all entry point names from the Python plugin: `run.py`, `manage.py`, `wsgi.py`, `api.py` (in addition to existing `main.py`, `app.py`, `server.py`). Also checks subdirectories (`app/`, `src/`, `application/`) for all these names
- **context.py**: Added `"language"` key to context dict (value: `"python"`) so the prompt template correctly shows `"Language: python"` instead of duplicating the framework name
- **prompts.py**: Fixed `"Language:"` label to read `context.get('language', 'python')` instead of `context.get('framework', 'unknown')`

## v0.3.11 (2026-06-17)

### Fix Docker daemon detection on Windows

## v0.3.10 (2026-06-17)

### Docker error messages now accurate

- **deploy.py**: `deploy_docker_compose()` and `start_jaeger()` now distinguish between Docker not installed (`DOCKER_NOT_FOUND`) and Docker daemon not running (`DOCKER_DAEMON_DOWN`). Users with Docker installed but Desktop not launched now see: *"Docker is installed but the daemon is not running — Start Docker Desktop and wait for it to be ready."* instead of the misleading *"Docker is required but was not found."*

## v0.3.9 (2026-06-17)

### Bug fixes and robustness improvements

- **orchestrator.py**: Fixed `'ServiceInfo' object has no attribute 'root_dir'` crash when LLM instrumentation tries to read the service path. Now uses `entry_points[0].path.parent` instead.
- **deploy.py**: Replaced `_docker_available()` with 3-functions: `_docker_installed()`, `_docker_daemon_ready()`, and retry-based `_docker_available()` (3 attempts × 3s). Uses `docker version --format` which is 10× faster than `docker info`.
- **deploy.py**: Added Jaeger health check after `docker compose up` — explicitly waits for port 16686 and verifies `/api/services` endpoint.
- **deploy.py**: `cleanup_docker_compose()` now checks Docker availability first — skips cleanly if the daemon is not responding.
- **deploy.py**: Reduced timeouts — compose up 300s→120s, compose down 60s→15s, docker check 10s→5s.
- **orchestrator.py**: `_try_cleanup()` is now protected against `KeyboardInterrupt` — clean message instead of traceback.
- **cli.py**: Top-level `KeyboardInterrupt` handler — returns exit code 130 with clean message.
- **deploy.py**: `cleanup_docker_compose` no longer raises on failure (`check=True` removed, `subprocess.CalledProcessError` handled gracefully).
- **All 561 tests pass with zero regressions.**

## v0.3.8 (2026-06-17)

### Consolidation — single-service orchestrator

- **deploy.py**: Python services always use OTLP HTTP/4318 (removed conditional gRPC fallback). Smart Jaeger detection (`_jaeger_alive`, `_docker_container_exists`) with 3-case restart logic. New `DOCKER_START_FAILED` error code.
- **discover.py**: Service deduplication by `(name, deployment)`. Subdirectory scanning for monorepos (`_is_service_dir`, `_discover_subdirectory_services`).
- **orchestrator.py**: New `_llm_instrument_services` step called between discovery and deploy, triggered by `--llm` flag + `OPENROUTER_API_KEY`. Falls back silently to Dockerfile patching.
- **prompts.py**: Universal framework-agnostic prompt replaces FastAPI/Flask-only prompt. Python reference appendix (FastAPI, Flask, Django, SQLAlchemy).
- **instrumentation.py**: Passes structured `context` dict for richer prompts.
- **Tests**: All 561 pass with updated env vars and prompt text.

## v0.3.7 (2026-06-16)

### Bug fixes
- Pipeline crash when no services are flagged suspicious (`EmptyDataError` on empty CSV). Added size check and try/except in `report_builder.py`.

## v0.3.6 (2026-06-16)

### Features
- ENTRYPOINT injected directly into `.mba-Dockerfile` instead of compose `entrypoint` override (Docker Compose v5 on Windows clears CMD when entrypoint is set in YAML)
- `opentelemetry-distro` added as runtime dependency (provides `OpenTelemetryConfigurator` entry point, needed for SDK config from env vars)
- Windows console encoding fix: `sys.stdout.reconfigure(encoding='utf-8')` in CLI module

## v0.3.5 (2026-06-16)

### Features
- Build-time OTel install: generates `.mba-Dockerfile` with `RUN pip install opentelemetry-distro opentelemetry-instrumentation-flask` etc. at build time
- Compose override points `build.dockerfile` to `.mba-Dockerfile`
- Cleanup of `.mba-Dockerfile` files after analysis

## v1.0.0 (2026-06-11)

### Features
- **SCOM pipeline** : computes Service-COhesion Metric from Jaeger traces (health filtering, endpoint extraction, DB table detection, endpoint-table mapping, threshold analysis, report generation)
- **CLI tool** : `mba` / `boundary-analyzer` commands (`run`, `setup`, `dashboard`, `teastore`)
- **Auto-instrumentation** : auto-detects Python microservices (FastAPI, Flask, Django), injects OpenTelemetry, collects traces via Jaeger, runs SCOM analysis
- **TeaStore support** : Docker Compose deployment with OTel Java agent, traffic generator, trace exporter, full SCOM pipeline
- **Dashboard** : interactive Dash web UI for SCOM results
- **LLM analysis** (optional) : AI-powered narrative report via OpenRouter (Qwen), disabled by default

### Improvements
- Segment-based health matching (`HEALTH_KEYWORDS`) instead of fragile `endswith` — `/health/all`, `/auth/health`, `/ready/isready`, `/metrics` (via `http.target`) correctly filtered
- `--skip-no-db-services` flag to exclude stateless services (proxy, orchestrator, etc.) from SCOM ranking
- `run_teastore()` function extracted for programmatic access

### Bug fixes
- MissingGreenlet in classroom-repository (added `selectinload`)
- datetime timezone-aware comparison in enrollment-service
- `academic_year` int→str conversion in enrollment-service
- Scope bug in `cleaned_parts` variable in CLI cleanup logic
- SQLAlchemy duplicate instrumentation (event listeners only, no `SQLAlchemyInstrumentor`/`AsyncPGInstrumentor`)
- `[project.scripts]` whitespace in pyproject.toml

### Tests
- 74 tests total (58 existing + 16 TeaStore)
- TeaStore synthetic fixtures (persistence-service with 5 tables, auth-service without DB)
- 3 test classes : TeaStorePipelineTest, TeaStoreSkipNoDbTest, TeaStoreNoFilterTest

### Infrastructure
- CI via GitHub Actions (`.github/workflows/ci.yml`) — Python 3.11 × 3.12
- `mba` CLI alias alongside `boundary-analyzer`
- Version bump to 0.2.0
