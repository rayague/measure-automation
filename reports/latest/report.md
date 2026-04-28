# Microservice Boundary Analysis Report
**Generated:** 2026-04-28 17:02:36
**Threshold Method:** percentile
**SCOM Threshold Used:** 0.98905
**SCOM Method:** weighted-table-weighted-endpoint-weighted
---
## Summary
- **Total Services:** 4
- **Suspicious Services (SCOM < 0.98905):** 1
- **Safe Services (SCOM >= 0.98905):** 3

## Suspicious Services
These services have low cohesion. They may have a boundary problem.

| Rank | Service | SCOM | Endpoints | Tables |
|------|---------|------|-----------|--------|
| 1 | inventory-service | 0.9562 | 3 | 3 |

### Why they are suspicious (simple English)

- **inventory-service**
  - SCOM is 0.9562. This is below the threshold 0.98905.
  - This service has 3 endpoints and 3 tables/collections.
  - Low cohesion can mean the service does many different things.

## Full Service Ranking
Services ranked by SCOM score (lowest first).

| Rank | Service | SCOM | Endpoints | Tables | Suspicious |
|------|---------|------|-----------|--------|------------|
| 1 | inventory-service | 0.9562 | 3 | 3 | Yes |
| 2 | notification-service | 1.0000 | 3 | 2 | No |
| 3 | order-service | 1.0000 | 4 | 3 | No |
| 4 | user-service | 1.0000 | 3 | 2 | No |

## Notes
- SCOM (Service Cohesion Measure) method: weighted-table-weighted-endpoint-weighted.
- A service is suspicious if its SCOM score is below the threshold.
- Low cohesion may indicate that the service boundary is not optimal.

