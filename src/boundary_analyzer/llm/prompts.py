"""All LLM prompt templates for the Boundary Analyzer.

Each prompt is a function that takes context data and returns a complete prompt string.
"""

import logging
from typing import Any

logger = logging.getLogger(__name__)

INSTRUMENTATION_SYSTEM = """\
You are the instrumentation engine of MBA (Microservice Boundary Analyzer).
Your ONLY task is to add OpenTelemetry (OTel) distributed tracing to a
microservice so that:
  - HTTP request spans are sent to Jaeger
  - Database query spans are sent to Jaeger
  - Metrics and logs exporters are DISABLED (traces only)
  - The service.name attribute equals the provided SERVICE_NAME

RULES:
  1. Never remove, rename, or change existing code logic
  2. Never change database models, route handlers, or business logic
  3. Use ONLY official OTel packages for the target language
  4. Always use BatchSpanProcessor, never SimpleSpanProcessor
  5. The service.name MUST equal the provided SERVICE_NAME
  6. Return ONLY the COMPLETE modified entry point file content — no explanations, no markdown
  7. If you CANNOT safely instrument, respond with exactly "ERROR:" followed by the reason

OTel endpoint: %s
Transport: OTLP HTTP (port 4318)

## Python-specific Reference

For FastAPI:
  - Use FastAPIInstrumentor.instrument_app(app) AFTER app creation
  - Import from opentelemetry.instrumentation.fastapi

For Flask:
  - Use FlaskInstrumentor().instrument_app(app) AFTER app = Flask(__name__)
  - Import from opentelemetry.instrumentation.flask

For Django:
  - Add bootstrap code to manage.py or wsgi.py BEFORE django.setup()
  - Import from opentelemetry.instrumentation.django

For SQLAlchemy:
  - Call SQLAlchemyInstrumentor().instrument() BEFORE creating any engine
  - Import from opentelemetry.instrumentation.sqlalchemy

Key packages (already available in the Docker image):
  - opentelemetry-distro, opentelemetry-sdk, opentelemetry-api
  - opentelemetry-exporter-otlp-proto-http
  - opentelemetry-instrumentation (provides the entrypoint wrapper)

THE CODE MUST BE VALID PYTHON. Every import must exist in the OpenTelemetry ecosystem."""


def build_instrumentation_prompt(
    context_text: str,
    jaeger_host: str = "localhost",
    jaeger_port: int = 4318,
    context: dict[str, Any] | None = None,
) -> str:
    """Build the prompt for generating OTel instrumentation code.

    Accepts either a pre-formatted ``context_text`` string (backward compat)
    or a structured ``context`` dict from ``build_project_context()``.
    """
    otel_endpoint = f"http://{jaeger_host}:{jaeger_port}"
    sys_prompt = INSTRUMENTATION_SYSTEM % otel_endpoint

    if context is not None:
        lines = [
            f"Service name: {context.get('service_name', 'unknown')}",
            f"Language: {context.get('framework', 'unknown')}",
            f"Framework: {context.get('framework', 'unknown')}",
            f"ORM: {context.get('orm', 'unknown')}",
            f"HTTP client: {context.get('http_client', 'unknown')}",
            f"Has Dockerfile: {context.get('has_dockerfile', False)}",
            f"Entry file: {context.get('main_file', 'not found')}",
            "",
            "--- File Tree ---",
        ]
        structure = context.get("structure", [])
        for entry in structure[:60]:
            lines.append(f"  {entry}")

        routes = context.get("api_routes", [])
        if routes:
            lines.append("")
            lines.append("--- API Routes Detected ---")
            for route in routes[:20]:
                lines.append(f"  {route['file']}:{route['line']}  {route['route']}")

        main_content = context.get("main_content", "")
        if main_content:
            lines.append("")
            lines.append(f"--- Entry Point: {context.get('main_file', 'main.py')} ---")
            lines.append(main_content)

        req_content = context.get("requirements_content", "")
        if req_content:
            lines.append("")
            lines.append(f"--- {context.get('requirements_file', 'requirements.txt')} ---")
            lines.append(req_content)

        context_text = "\n".join(lines)

    return f"""{sys_prompt}

Here is the complete project context:

{context_text}

---

Add OpenTelemetry instrumentation to the main application file.
Return ONLY the complete modified file content — no explanations, no markdown."""


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

### Endpoint to Table Access Matrix (with call counts)
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
