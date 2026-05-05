# Microservice Boundary Analysis Report
**Generated:** 2026-05-02 21:47:57
**Threshold Method:** percentile
**SCOM Threshold Used:** 1.0
**SCOM Method:** weighted-table-weighted-endpoint-weighted
---
## Summary
- **Total Services:** 1
- **Suspicious Services (SCOM < 1.0):** 0
- **Safe Services (SCOM >= 1.0):** 1

## Suspicious Services
No suspicious services found. All services have good cohesion.

## Full Service Ranking
Services ranked by SCOM score (lowest first).

| Rank | Service | SCOM | Endpoints | Tables | Suspicious |
|------|---------|------|-----------|--------|------------|
| 1 | scenario2 | 1.0000 | 1 | 4 | No |

## Notes
- SCOM (Service Cohesion Measure) method: weighted-table-weighted-endpoint-weighted.
- A service is suspicious if its SCOM score is below the threshold.
- Low cohesion may indicate that the service boundary is not optimal.

