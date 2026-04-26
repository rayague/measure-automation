# auto_setup – OpenTelemetry Instrumentation Setup

Automatically instruments any microservice project with OpenTelemetry
so the **SCOM boundary analyzer** can collect and analyze traces.

---

## Supported Frameworks

| Language       | Framework              | Auto-detected? |
|----------------|------------------------|----------------|
| Python         | Flask                  | ✅ Yes         |
| Python         | FastAPI                | ✅ Yes         |
| Python         | Django                 | ✅ Yes         |
| Python         | Django REST Framework  | ✅ Yes         |
| Python         | Starlette              | ✅ Yes         |
| Python         | Tornado                | ✅ Yes         |
| PHP            | Laravel                | ✅ Yes         |
| JavaScript/TS  | Express.js             | ✅ Yes         |
| JavaScript/TS  | Next.js                | ✅ Yes         |
| JavaScript/TS  | Nest.js                | ✅ Yes         |

---

## Quick Start

```bash
# Scan a project (auto-detect framework)
python setup_instrumentation.py --project-path ./my-service

# Force a specific framework
python setup_instrumentation.py --project-path ./my-service --framework fastapi

# Skip starting Jaeger (already running)
python setup_instrumentation.py --project-path ./my-service --no-jaeger

# Use a remote Jaeger host
python setup_instrumentation.py --project-path ./my-service --jaeger-host 192.168.1.10
```

---

## What Happens Automatically

```
Step 1  Detect framework    Reads files to find Flask / Express / Laravel etc.
Step 2  Install packages    pip install / npm install / composer require
Step 3  Generate file       Writes otel_instrumentation.py/.js/.php into your project
Step 4  Start Jaeger        docker run jaegertracing/all-in-one
Step 5  Show instructions   Tells you exactly which 2 lines to add in your app
Step 6  Collect traces      Pulls traces from Jaeger HTTP API
Step 7  Run SCOM analysis   Calculates cohesion score and flags Wrong Cuts
```

---

## What You Do Manually (2–3 lines only)

The script tells you exactly what to add. Examples:

**Flask / FastAPI / Django / Starlette / Tornado:**
```python
from otel_instrumentation import init_tracing
init_tracing()
```

**Express.js (first line of app.js):**
```js
require('./otel_instrumentation');
```

**Nest.js (first line of main.ts):**
```ts
import './otel_instrumentation';
```

**Next.js (inside instrumentation.ts):**
```ts
export async function register() {
  if (process.env.NEXT_RUNTIME === 'nodejs') {
    await import('./otel_instrumentation');
  }
}
```

**Laravel (inside bootstrap/app.php):**
```php
require __DIR__.'/../otel_instrumentation.php';
```

---

## Folder Structure

```
auto_setup/
├── __init__.py                    Python package marker
├── setup_instrumentation.py       Main script (run this)
├── requirements_auto.txt          Python dependencies
└── templates/
    ├── flask_wrapper.py           Flask template
    ├── fastapi_wrapper.py         FastAPI template
    ├── django_wrapper.py          Django template
    ├── djangorest_wrapper.py      Django REST Framework template
    ├── starlette_wrapper.py       Starlette template
    ├── tornado_wrapper.py         Tornado template
    ├── express_wrapper.js         Express.js template
    ├── nextjs_wrapper.js          Next.js template
    ├── nestjs_wrapper.js          Nest.js template
    └── laravel_wrapper.php        Laravel template
```

---

## Adding a New Framework

1. Create a new template in `templates/` named `<framework>_wrapper.<ext>`.
2. Use `{{SERVICE_NAME}}`, `{{JAEGER_HOST}}`, `{{JAEGER_GRPC_PORT}}` as placeholders.
3. Add the framework to `SUPPORTED_FRAMEWORKS` in `setup_instrumentation.py`.
4. Add detection logic in `detect_framework()`.
5. Add install logic in `FRAMEWORK_PACKAGES` or the install functions.
6. Add integration instructions in `INTEGRATION_INSTRUCTIONS`.

---

## Requirements

- **Docker** – to run Jaeger automatically
- **Python 3.8+** – to run the setup script itself
- **pip / npm / composer** – depending on the target project language
