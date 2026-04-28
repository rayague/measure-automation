# Microservice Boundary Analysis Report
**Generated:** 2026-04-28 11:51:06
**SCOM Threshold:** 0.5
---
## Summary
- **Total Services:** 4
- **Suspicious Services (SCOM < 0.5):** 0
- **Safe Services (SCOM >= 0.5):** 4

## Suspicious Services
No suspicious services found. All services have good cohesion.

## Full Service Ranking
Services ranked by SCOM score (lowest first).

| Rank | Service | SCOM | Endpoints | Tables | Suspicious |
|------|---------|------|-----------|--------|------------|
| 1 | inventory-service | 0.7835 | 3 | 3 | No |
| 2 | notification-service | 0.8105 | 3 | 2 | No |
| 3 | order-service | 0.8640 | 4 | 3 | No |
| 4 | user-service | 1.0000 | 3 | 2 | No |

## Notes
- SCOM (Service Cohesion Measure) uses Jaccard similarity of endpoint-table sets.
- A service is suspicious if its SCOM score is below the threshold.
- Low cohesion may indicate that the service boundary is not optimal.

