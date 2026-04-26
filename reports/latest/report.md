# Microservice Boundary Analysis Report
**Generated:** 2026-04-26 17:18:49
**SCOM Threshold:** 0.5
---
## Summary
- **Total Services:** 4
- **Suspicious Services (SCOM < 0.5):** 1
- **Safe Services (SCOM >= 0.5):** 3

## Suspicious Services
These services have low cohesion and may have problematic boundaries.

| Rank | Service | SCOM | Endpoints | Tables |
|------|---------|------|-----------|--------|
| 1 | inventory-service | 0.7835 | 3 | 3 |

## Full Service Ranking
Services ranked by SCOM score (lowest first).

| Rank | Service | SCOM | Endpoints | Tables | Suspicious |
|------|---------|------|-----------|--------|------------|
| 1 | inventory-service | 0.7835 | 3 | 3 | Yes |
| 2 | notification-service | 0.8105 | 3 | 2 | No |
| 3 | order-service | 0.8640 | 4 | 3 | No |
| 4 | user-service | 1.0000 | 3 | 2 | No |

## Notes
- SCOM (Service Cohesion Measure) uses Jaccard similarity of endpoint-table sets.
- A service is suspicious if its SCOM score is below the threshold.
- Low cohesion may indicate that the service boundary is not optimal.

