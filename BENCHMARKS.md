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
| Node.js | react-express-mysql | [docker/awesome-compose](https://github.com/docker/awesome-compose/tree/master/react-express-mysql) | ⬜ In progress | [below](#nodejs--react-express-mysql) |
| PHP | — | — | ⬜ Not yet run | |
| .NET | — | — | ⬜ Not yet run | |

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

**Command:** `mba full . --duration 60`
**Source:** Docker's official samples — Express backend + MariaDB + React frontend,
Docker secrets, multi-stage build.
**Status:** ⬜ run in progress; this section will be filled with the actual
outcome (success or documented failure) once complete.

---

## PHP — not yet run

No end-to-end run has been performed. The Laravel plugin has unit tests
only. No empirical claim is made.

## .NET — not yet run

No end-to-end run has been performed. The .NET agent-injection path has
unit tests only. No empirical claim is made.
