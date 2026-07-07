# Benchmark Campaign — Real-World Validation Results

This document records **actual, reproducible test runs** of MBA against real
microservice applications, one per supported language stack. Every number
here comes from a run that actually happened, on the date indicated, with
the tool version indicated. Nothing in this file is projected, estimated,
or assumed.

Conventions:
- ✅ **Validated** — full end-to-end run completed, SCOM computed, results inspected.
- ⚠️ **Partial** — the run completed but with caveats described honestly.
- ❌ **Failed** — the run did not complete; the failure and its cause are documented.
- ⬜ **Not yet run** — no claim is made.

| Language | Application | Source | Status | Details |
|---|---|---|---|---|
| Java | TeaStore (6 services) | [DescartesResearch/TeaStore](https://github.com/DescartesResearch/TeaStore) | ✅ Validated | [below](#java--teastore) |
| Python | Flask scenario apps (3 designs) | supervisor-provided test scenarios | ✅ Validated | [below](#python--flask-scenarios) |
| Node.js | react-express-mysql | [docker/awesome-compose](https://github.com/docker/awesome-compose/tree/master/react-express-mysql) | ✅ Validated (pipeline; app has no table-touching SQL) | [below](#nodejs--react-express-mysql) |
| PHP | apache-php | [docker/awesome-compose](https://github.com/docker/awesome-compose/tree/master/apache-php) | ⚠️ Partial (pipeline OK; PHP ext required for tracing) | [below](#php--apache-php) |

---

## Java — TeaStore

**Command:** `mba teastore --duration 90 --wait 900`
**Tool version:** 0.8.2 + pending fixes (commit `137c831`)
**Date:** 2026-07-07 · **Two complete runs** (60s and 90s traffic)

### What happened (second run)

- All 6 TeaStore services + MariaDB + Jaeger deployed via Docker Compose,
  with the OpenTelemetry Java agent injected automatically (no source changes).
- WebUI became ready after ~340s (6 JVMs warming up — this is why the
  default `--wait` is 900s).
- The tool discovered the live catalog by parsing the running application:
  **5 categories, 9 products** (real IDs, not assumed).
- Traffic: **99 requests sent, 0 failed.**
- Export: **3,487 traces / ~9,300 spans** across all 6 services.
- **6,704 duplicate spans** (Jaeger's per-service export duplicates
  multi-service traces) were detected and removed before analysis.
- TeaStore's startup database seeding (SQL bursts with no HTTP parent) was
  correctly excluded from endpoint mapping (reported as `unknown_endpoint`
  and filtered out of SCOM).

### Results

| Service | SCOM (weighted) | Endpoints | Tables |
|---|---|---|---|
| teastore-persistence | 0.018 (run 2) / 0.115 (run 1) | 8 | 6 |

The tool correctly identified `teastore-persistence` as the **only**
database-backed service (this matches TeaStore's actual architecture), and
recovered its real JAX-RS route templates mapped to real MySQL tables:

```
GET /tools.descartes.teastore.persistence/rest/categories            → persistencecategory
GET /tools.descartes.teastore.persistence/rest/orderitems            → persistenceorder, persistenceorderitem, persistenceproduct, persistenceuser
GET /tools.descartes.teastore.persistence/rest/products/category/{…} → persistenceproduct
GET /tools.descartes.teastore.persistence/rest/products/count/{…}    → persistenceproduct
GET /tools.descartes.teastore.persistence/rest/orders                → persistenceorder
GET /tools.descartes.teastore.persistence/rest/generatedb/finished   → databasemanagemententity
```

### Honest caveats

- The weighted SCOM differed between the two runs (0.115 vs 0.018) because
  the traffic mixes differed (run 2 exercised product pages). The weighted
  variant measures cohesion *under the observed workload* — comparing
  weighted scores across runs requires comparable traffic. Report the
  unweighted score alongside it for cross-run comparisons.
- Jaeger's export API caps at 1,000 traces per service; the registry and
  persistence services hit that cap. Frequencies for the busiest services
  are therefore computed on a truncated sample.

---

## Python — Flask scenarios

**Command:** `mba full .` (per scenario)
**Tool version:** 0.8.0 · **Date:** 2026-07-05 · run by the project author

Three Flask + PostgreSQL applications (supervisor-provided) with
deliberately different boundary designs, each deployed, instrumented,
traffic-generated, and analyzed fully automatically:

| Scenario | Design intent | Traffic | SCOM (weighted) |
|---|---|---|---|
| scenario1 | Wrong cut (2 mixed domains) | 126 req (9 failed, 7.1%) | 0.0568 |
| scenario2 | Scattered (every endpoint hits all 4 tables) | 120 req (0 failed) | 0.2482 |
| scenario3 | Well-cut | 119 req (0 failed) | 0.1630 |

**Finding:** scenario1 < scenario3 as designed. scenario2 scored *highest*,
not lowest — a genuine scope boundary of the SCOM metric (it measures
data-sharing overlap, not query efficiency; endpoints that all query the
same tables have maximal overlap by definition). Documented in the paper's
Threats to Validity.

---

## Node.js — react-express-mysql

**Command:** `mba full . --duration 60 [--reset-jaeger]`
**Source:** Docker's official samples — Express backend + MariaDB + React frontend,
Docker secrets, multi-stage build, modern `compose.yaml` naming, named networks.
**Date:** 2026-07-07 · **14 iterative runs** — each failure was a real
genericity defect in the tool, fixed and committed before the next run.

### Campaign log (honest, run by run)

| Run | Reached | Defect found → fixed |
|---|---|---|
| 1 | discovery | `compose.yaml` (Compose-spec canonical name) not recognized — discovery silently missed the compose file, fell back to host-process deploy |
| 2 | build | 300s hard build timeout killed a legitimate first build (391s measured); Node OTel injection assumed the package exists in the app image; OTLP endpoint pointed at the gRPC port with an HTTP-protocol SDK |
| 3 | deploy ✔ | endpoint discovery returned `[]` for every non-Python service → zero traffic |
| 4 | traffic ✔ 114/114 | provisioning marker checked a file layout modern OTel packages no longer have → `NODE_OPTIONS` silently omitted → app ran untraced; a 9-hour-old zombie Jaeger was absorbing/serving all trace queries |
| 5 | deploy ✔ | TCP port readiness lies behind docker-proxy → traffic fired while the app was still waiting on its DB: 118/118 failed |
| 6 | deploy ✔ | honest HTTP readiness now in place, but its 60s budget was shorter than the app's real cold start (~2 min: fresh MariaDB init + npm + nodemon + OTel bootstrap) |
| 7-8 | traffic ✔, collect ✔ | split-brain Jaeger: a leftover compose-managed Jaeger carried the `mba-jaeger` DNS alias on the app networks, so apps exported to one instance while collection queried another; Compose-v2 project names with hyphens were also mangled to underscores, breaking the early network join |
| 9-13 | deploy ✔ | the backend never passed HTTP readiness: NODE_OPTIONS loads OTel into every node process (npm, nodemon, healthchecks) and the cloud resource detectors wait out network timeouts; measured boot 5m40s vs 300s budget → traffic only exercised the frontend. Fixed: targeted instrumentation set, local-only detectors, 600s budget |
| 14 | **✅ complete, exit 0** | full pipeline through SCOM; and the final twist below |

### Mechanisms validated live during this campaign

- OTel Node modules provisioned once on the host and bind-mounted read-only;
  the register entrypoint loads in a real unmodified container:
  *"OpenTelemetry automatic instrumentation started successfully"*.
- Spans from an instrumented container reach Jaeger over both tested routes:
  the compose network (service alias) and `host.docker.internal:4318`
  (verified: service appears in Jaeger's `/api/services`).
- Express route extraction recovers the sample's real routes (`GET /`,
  `GET /healthz`) from its source.

### Final result (run 14, exit 0)

The complete pipeline now runs end-to-end on this project:

- 2/2 services deployed and HTTP-ready; 128 requests, 0 failed.
- The backend's knex/mysql2 queries are traced: **35 `select` spans + 35
  knex spans** confirmed in Jaeger from the run's own traffic, correctly
  exported and extracted as 70 DB operations by the pipeline.
- SCOM: `backend 0.0 (2 endpoints, 0 tables)`, `frontend 0.0 (6 endpoints,
  0 tables)` — **and 0 tables is the correct answer**: this sample's only
  SQL statement is `select VERSION()`, a metadata query that touches no
  table at all. There is nothing for endpoint→table mapping to map.

### Honest status

**✅ Pipeline validated end-to-end on Node.js.** Every stage demonstrably
works on a real unmodified project: compose discovery, self-contained OTel
injection, endpoint discovery from Express sources, traffic, knex/mysql2
span tracing, export, DB-operation extraction, and honest SCOM reporting.
This particular reference app has no table-touching queries, so its SCOM is
legitimately zero-table; a Node project with real business queries would be
needed to produce a non-trivial SCOM figure. No such figure is claimed.

**9 real genericity defects** were found and fixed against this single
project (see the campaign log above) — itself a key empirical finding about
"fully automatic" claims.

---

## PHP — apache-php

**Command:** `mba full . --duration 45`
**Source:** Docker's official samples — Apache + PHP, single service, no database.
**Date:** 2026-07-07 · 1 run.

### Result

| Step | Outcome |
|---|---|
| Discover | ✅ 1 PHP service detected from `compose.yaml` |
| Deploy | ✅ 1/1 ready (HTTP readiness) |
| Traffic | ✅ 88 requests, 0 failed |
| Collect | ❌ No traces — expected, see below |

### Honest status

**Partial, with a documented structural limitation.** The deployment,
discovery, and traffic pipeline works on a real PHP project. However, PHP
tracing requires the **OpenTelemetry PHP extension compiled into the
image** — unlike Node.js (where MBA bind-mounts pure-JS modules from a host
cache), a PHP extension is a compiled `.so` that cannot be injected into an
arbitrary image at deploy time. The injected `OTEL_PHP_AUTOLOAD_ENABLED`
env vars are inert without it. Consequently:

- PHP projects whose images already ship the OTel extension will be traced;
- stock PHP images (like this sample) produce **no spans**, and MBA reports
  "No traces found" rather than fabricating results.

Additionally, this sample has no database, so SCOM would be empty by
design even with tracing. A PHP benchmark with real DB access (e.g. a
Laravel + MySQL application with the OTel extension) would be needed for a
PHP SCOM number; none is claimed here.
