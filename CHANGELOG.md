# Changelog

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
