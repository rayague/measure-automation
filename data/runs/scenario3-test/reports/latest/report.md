# Microservice Boundary Analysis Report
**Generated:** 2026-05-02 23:03:17
**Threshold Method:** percentile
**SCOM Threshold Used:** 0.0602
**SCOM Method:** weighted-table-weighted-endpoint-weighted
---
## Summary
- **Total Services:** 1
- **Suspicious Services (SCOM < 0.0602):** 0
- **Safe Services (SCOM >= 0.0602):** 1

## Suspicious Services
No suspicious services found. All services have good cohesion.

## Full Service Ranking
Services ranked by SCOM score (lowest first).

| Rank | Service | SCOM | Endpoints | Tables | Suspicious |
|------|---------|------|-----------|--------|------------|
| 1 | scenario3 | 0.0602 | 4 | 4 | No |

## Notes
- SCOM (Service Cohesion Measure) method: weighted-table-weighted-endpoint-weighted.
- A service is suspicious if its SCOM score is below the threshold.
- Low cohesion may indicate that the service boundary is not optimal.

