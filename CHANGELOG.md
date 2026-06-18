# Changelog

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
