# Changelog

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
