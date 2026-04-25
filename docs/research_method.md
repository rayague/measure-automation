# Research Method and Limitations

## Core Concepts

### Coupling vs Cohesion vs Wrong Cuts

**Coupling**
- Definition: How much one service depends on another service
- High coupling: Service A cannot work without Service B
- Examples: Service A calls Service B, Service A and Service B share the same database table
- Measurement: Count of inter-service calls, shared tables, or API dependencies

**Cohesion**
- Definition: How related the parts inside one service are
- High cohesion: Endpoints in the service use related database tables
- Low cohesion: Endpoints in the service use unrelated database tables
- Measurement: Similarity of endpoint-table sets (SCOM - Service Cohesion Measure)

**Wrong Cuts**
- Definition: Service boundaries that are not optimal
- Signs of wrong cuts:
  - Low cohesion inside the service (endpoints share few tables)
  - High coupling with other services (many cross-service calls or shared tables)
- Action: Redraw the boundary to improve cohesion and reduce coupling

---

## Analysis Method

### Runtime-based analysis
- This tool uses runtime traces from OpenTelemetry and Jaeger
- It does not use static code analysis
- It analyzes real traffic and database operations
- Advantage: Captures actual behavior, not intended design

### Database-centric assumption
- This tool focuses on database-centric microservices
- It assumes each service has database operations
- It extracts table names from SQL or MongoDB operations
- **Limitation:** For services without database operations, this method cannot compute cohesion

### Endpoint → table relation
- The core relation is: each endpoint uses a set of database tables
- If endpoints in a service use similar tables, cohesion is high
- If endpoints use very different tables, cohesion is low

---

## Current Implementation

### What is implemented
- Trace collection from Jaeger
- Span parsing and flattening (includes tags for better endpoint extraction)
- Endpoint detection with normalization (HTTP method + route)
- Database table extraction (MVP: simple regex on SQL)
- Endpoint-table mapping (uses parent chain walking)
- Weighted SCOM calculation (table frequency, endpoint frequency)
- Statistical threshold (percentile-based, Z-score, or fixed)
- Service ranking and suspicious flagging
- Dashboard with visualization

### MVP simplifications (marked clearly)

**1. Table extraction (simplified)**
- Current: Uses simple regex on SQL statements
- Paper likely: More sophisticated parsing or ORM instrumentation
- Impact: May miss tables in complex queries
- Status: MVP simplification

**2. Coupling analysis (missing)**
- Current: No coupling analysis
- Paper likely: Includes service-to-service coupling metrics
- Impact: Cannot detect wrong cuts caused by high coupling
- Status: Not implemented

---

## SCOM Calculation (Ultimate Version)

### Weighted SCOM formula
The ultimate version uses weighted Jaccard similarity instead of simple Jaccard.

**Table frequency weighting:**
```
weight(table) = count(table) / sum(count(all tables))
```
Tables used more often across all endpoint-table mappings get higher weight.

**Endpoint frequency weighting:**
```
weight(endpoint) = sum(count(endpoint-table pairs)) / sum(all counts)
```
Endpoints called more often get higher weight.

**Weighted Jaccard similarity:**
```
weighted_intersection = sum(weight(t) for t in tables_a ∩ tables_b)
weighted_union = sum(weight(t) for t in tables_a ∪ tables_b)
similarity = weighted_intersection / weighted_union
```

**Weighted average SCOM:**
```
weight(pair) = weight(endpoint_a) * weight(endpoint_b)
SCOM = sum(similarity * weight(pair)) / sum(weight(pair))
```

### Configuration
- `scom_method`: "weighted" or "simple"
- `table_weighting`: true/false
- `endpoint_weighting`: true/false

### Why weighting matters
- Frequently used tables are more important for cohesion
- Frequently called endpoints are more representative of service behavior
- Weighted SCOM is more faithful to the paper's likely method

---

## Threshold Method (Ultimate Version)

### Statistical threshold
The ultimate version uses data-driven thresholds instead of a fixed value.

**Percentile-based threshold (default):**
```
threshold = percentile(SCOM_scores, 25)
```
Services below the 25th percentile are flagged as suspicious.
- Simple to explain: "Bottom 25% are suspicious"
- Robust to outliers
- Adapts to any distribution
- Recommended method

**Z-score threshold:**
```
threshold = mean(SCOM) + (zscore_cutoff * std(SCOM))
```
Services with Z-score below -1.5 are flagged as suspicious.
- Standard statistical method
- Measures deviation from mean
- Useful for normal distributions

**Fixed threshold (fallback):**
```
threshold = 0.5
```
Services below 0.5 are flagged as suspicious.
- Arbitrary but simple
- Kept for comparison
- Not recommended for production

### Configuration
- `threshold_method`: "percentile", "zscore", or "fixed"
- `threshold_percentile`: 25.0 (default)
- `threshold_zscore`: -1.5 (default)
- `scom_threshold`: 0.5 (fallback for fixed method)

### Why statistical threshold matters
- Adapts to the actual SCOM distribution
- Not arbitrary
- Explainable
- More faithful to the paper's likely method

---

## Limitations

### Non-database-driven systems
- This tool requires database operations to compute cohesion
- For services without database operations (e.g., pure computation, external API calls), cohesion cannot be computed
- For such services, alternative methods would be needed (e.g., API call patterns, shared libraries)

### Trace coverage
- The tool depends on complete trace coverage
- If some services are not instrumented, the analysis is incomplete
- If traces are sampled, the analysis may miss rare operations

### Table name extraction
- SQL parsing is regex-based and may fail on complex queries
- ORM-generated queries may not expose table names clearly
- Custom database operations may not be detected

### Threshold selection
- Percentile threshold requires choosing a cutoff (default 25%)
- Different cutoffs may be appropriate for different contexts
- The choice should be documented and justified

---

## Future Improvements

### 1. Coupling analysis
- Analyze service-to-service calls from traces
- Detect shared tables across services
- Compute coupling metrics
- Combine cohesion and coupling to detect wrong cuts

### 2. Better table extraction
- Use SQL parser library for complex queries
- Handle ORM-specific patterns
- Support more database types

### 3. Threshold optimization
- Validate threshold cutoff with real data
- Compare percentile vs Z-score results
- Add adaptive threshold if needed
