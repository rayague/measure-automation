"""All LLM prompt templates for the Boundary Analyzer.

Each prompt is a function that takes context data and returns a complete prompt string.
"""

INSTRUMENTATION_SYSTEM = """You are an expert in Python microservices and OpenTelemetry instrumentation.
Your task is to add complete OpenTelemetry tracing to a FastAPI/Flask microservice.

RULES:
- Use OTLP gRPC exporter pointing to %s
- Add ALL necessary imports at the top of the file
- Instrument the web framework, database (SQLAlchemy), and HTTP client (httpx/requests)
- Do NOT change any existing logic, routes, or behaviour
- Do NOT remove or alter existing imports, middleware, or configuration
- Add the instrumentation code in the correct location (lifespan for FastAPI, after app creation for Flask)
- Return ONLY the COMPLETE modified file content, no explanations
- If you cannot add instrumentation, start your response with "ERROR:"

THE CODE MUST BE VALID PYTHON. Every import must exist in the OpenTelemetry ecosystem."""


def build_instrumentation_prompt(
    context_text: str,
    jaeger_host: str = "localhost",
    jaeger_port: int = 4317,
) -> str:
    """Build the prompt for generating OTel instrumentation code."""
    otel_endpoint = f"http://{jaeger_host}:{jaeger_port}"
    sys_prompt = INSTRUMENTATION_SYSTEM % otel_endpoint
    return f"""{sys_prompt}

Here is the complete project context:

{context_text}

---

Add OpenTelemetry instrumentation to the main application file.
Return ONLY the complete modified file content."""


def build_analysis_prompt(
    rank_csv: str,
    mapping_csv: str,
    context_text: str,
    spans_count: int = 0,
    traces_count: int = 0,
) -> str:
    """Build the prompt for narrative analysis of SCOM results.

    Args:
        rank_csv: String representation of service_rank.csv.
        mapping_csv: String representation of endpoint_table_map.csv.
        context_text: Additional project context text.
        spans_count: Total number of spans analysed.
        traces_count: Total number of traces analysed.
    """
    data_src = []
    if traces_count:
        data_src.append(f"- **Traces analysed:** {traces_count}")
    if spans_count:
        data_src.append(f"- **Spans analysed:** {spans_count}")
    data_src_str = "\n".join(data_src) if data_src else ""

    return f"""You are an expert in microservices architecture, domain-driven design, and quantitative software analysis.
You are analysing cohesion metrics (SCOM — Service Cohesion Measure) computed from runtime OpenTelemetry traces.

Your task is to produce a **detailed, quantitative, data-driven** analysis. Be specific — reference actual endpoint names, table names, and call counts from the data. Every claim must be backed by numbers.

---

## DATA INPUTS

### Project Context
{context_text}

### SCOM Ranking (all services sorted by cohesion, lowest = worst)
```
{rank_csv}
```

### Endpoint → Table Access Matrix (with call counts)
```
{mapping_csv}
```

{data_src_str}

---

## OUTPUT FORMAT

Produce a structured Markdown analysis. Follow the exact section format below.

### Suspicious Service Analysis (one section per suspicious service)

**{{service_name}}** (SCOM: {{score}}, Rank: #{{rank}})

**Endpoint Overlap Matrix:**
For each endpoint in this service, list the tables it accesses and the call count. Then compute the **Jaccard similarity** for every endpoint pair:

J(A, B) = |tables(A) ∩ tables(B)| / |tables(A) ∪ tables(B)|

Present as:
| Endpoint Pair | Shared Tables | Union Tables | Jaccard | Verdict |
|---|---|---|---|---|
| `GET /a` ↔ `POST /b` | {{table_x, table_y}} | {{table_x, table_y, table_z}} | 0.67 | moderate overlap |

**Why — Root Cause:**
Identify the specific *wrong cut*: which endpoint(s) access tables that belong to a different domain. For example: "`GET /stock` touches `warehouse`, which has no overlap with `inventory` accessed by the other two endpoints — this indicates stock-management and inventory-management are conflated in one service."

**Impact — Architectural Consequence:**
Explain concretely how this low cohesion affects development:
- Change coupling (modifying table X requires understanding Y endpoints)
- Deployment risk (the service cannot be split independently)
- Team friction (multiple bounded contexts in one codebase)

**Quantified Suggestion — Refactor Plan:**
Propose a concrete split into bounded contexts. For each proposed service, list:
- New service name
- Endpoints it would own
- Tables it would own
- Expected SCOM improvement (qualitative estimate)

---

### Healthy Services

For each non-suspicious service, briefly note:
- What makes it cohesive (which tables are shared across which endpoints)
- **Jaccard similarity** between its endpoints (should be high)
- One sentence confirming the bounded context is well-defined

---

### Threshold Impact Analysis

Explain how the threshold affects the results:
- How many services would be flagged with a ±0.1 threshold shift
- Is the current threshold justified by the data distribution (e.g., natural gap in scores)

---

### Data Sources

{data_src_str}

**Important formatting rules:**
- Use `|` table syntax for structured data (matrices, comparisons)
- Use `**bold**` for service names and key metrics
- Every claim must reference actual endpoint names, table names, and counts from the input data
- Be quantitative — numbers are better than adjectives"""
