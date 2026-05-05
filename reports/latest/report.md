# Microservice Boundary Analysis Report
**Generated:** 2026-05-05 21:27:17
**Threshold Method:** percentile
**SCOM Threshold Used:** 0.979175
**SCOM Method:** weighted
---
## Summary
- **Total Services:** 4
- **Suspicious Services (SCOM < 0.979175):** 1
- **Safe Services (SCOM >= 0.979175):** 3

## Suspicious Services
These services have low cohesion. They may have a boundary problem.

| Rank | Service | SCOM | Endpoints | Tables |
|------|---------|------|-----------|--------|
| 1 | inventory-service | 0.9167 | 3 | 3 |

### Why they are suspicious (simple English)

- **inventory-service**
  - SCOM is 0.9167. This is below the threshold 0.979175.
  - This service has 3 endpoints and 3 tables/collections.
  - Low cohesion can mean the service does many different things.

## Full Service Ranking
Services ranked by SCOM score (lowest first).

| Rank | Service | SCOM | Endpoints | Tables | Suspicious |
|------|---------|------|-----------|--------|------------|
| 1 | inventory-service | 0.9167 | 3 | 3 | Yes |
| 2 | notification-service | 1.0000 | 3 | 2 | No |
| 3 | order-service | 1.0000 | 4 | 3 | No |
| 4 | user-service | 1.0000 | 3 | 2 | No |

## Notes
- SCOM (Service Cohesion Measure) method: weighted.
- A service is suspicious if its SCOM score is below the threshold.
- Low cohesion may indicate that the service boundary is not optimal.

