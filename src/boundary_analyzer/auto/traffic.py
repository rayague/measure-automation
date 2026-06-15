from __future__ import annotations

import ast
import json
import logging
import random
import string
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import requests

from boundary_analyzer.auto.errors import AnalysisError, ErrorCode
from boundary_analyzer.auto.models import Endpoint, ServiceInfo, TrafficResult

logger = logging.getLogger(__name__)


_OPENAPI_PATHS = [
    "/openapi.json",
    "/swagger.json",
    "/swagger/v1/swagger.json",
    "/api/openapi.json",
]

_HEALTH_KEYWORDS = frozenset(
    {
        "health",
        "healthz",
        "readyz",
        "livez",
        "ready",
        "metrics",
        "favicon.ico",
    }
)

_GRAPHQL_PATHS = [
    "/graphql",
    "/api/graphql",
    "/graphql/v1",
    "/query",
    "/api/query",
]

_LLM_PATHS = [
    "/v1/chat/completions",
    "/v1/completions",
    "/chat",
    "/api/chat",
    "/generate",
    "/api/generate",
    "/v1/generate",
]

_OAUTH2_CONFIG_PATHS = [
    "/.well-known/openid-configuration",
    "/.well-known/oauth-authorization-server",
]

_LLM_MODELS = [
    "gpt-4",
    "gpt-3.5-turbo",
    "claude-3",
    "llama-3",
    "mistral",
    "gemini-pro",
    "deepseek-chat",
]

_LLM_PROMPTS = [
    "Explain quantum computing in simple terms",
    "Write a Python function to sort a list",
    "Summarize the history of the Roman Empire",
    "Write a haiku about artificial intelligence",
    "What is the capital of France?",
    "Translate 'hello' to Spanish",
    "Write a short story about a robot learning to paint",
]

_GRAPHQL_INTROSPECTION_QUERY = """
query IntrospectionQuery {
  __schema {
    queryType { name }
    mutationType { name }
    types {
      kind name description
      fields { name description args { name type { name kind } } }
    }
  }
}
"""


@dataclass
class TrafficConfig:
    duration: int = 60
    workers: int = 5
    interval_min: float = 0.05
    interval_max: float = 0.2
    timeout: int = 10
    auth_token: str | None = None
    base_url: str = "http://127.0.0.1"


def discover_endpoints_openapi(host: str, port: int, config: TrafficConfig) -> list[Endpoint]:
    base = f"{config.base_url}:{port}"
    endpoints: list[Endpoint] = []

    for path in _OPENAPI_PATHS:
        url = urljoin(base, path)
        try:
            resp = requests.get(url, timeout=config.timeout)
            if resp.status_code == 200:
                spec = resp.json()
                parsed = _parse_openapi(spec, base)
                if parsed:
                    endpoints = parsed
                    break
        except (requests.RequestException, json.JSONDecodeError):
            continue

    return endpoints


def _parse_openapi(spec: dict[str, Any], base_url: str) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    paths = spec.get("paths", {})

    for path, methods in paths.items():
        if not isinstance(methods, dict):
            continue

        for method, details in methods.items():
            method = method.upper()
            if method not in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                continue

            if _is_health_path(path):
                continue

            params: list[dict[str, Any]] = []
            parameters = details.get("parameters", []) if isinstance(details, dict) else []
            for param in parameters:
                if isinstance(param, dict):
                    params.append(
                        {
                            "name": param.get("name", ""),
                            "in": param.get("in", "query"),
                            "required": param.get("required", False),
                            "type": _resolve_type(param.get("schema", {})),
                        }
                    )

            request_body: dict[str, Any] | None = None
            if isinstance(details, dict) and "requestBody" in details:
                rb = details["requestBody"]
                content = rb.get("content", {})
                for content_type, content_spec in content.items():
                    if "application/json" in content_type or content_type == "*/*":
                        schema = content_spec.get("schema", {})
                        request_body = schema
                        break

            endpoints.append(
                Endpoint(
                    method=method,
                    path=path,
                    params=params,
                    request_body=request_body,
                    auth_required=_is_auth_endpoint(path),
                )
            )

    return endpoints


def discover_endpoints_graphql(host: str, port: int, config: TrafficConfig) -> list[Endpoint]:
    base = f"{config.base_url}:{port}"
    endpoints: list[Endpoint] = []

    for path in _GRAPHQL_PATHS:
        url = urljoin(base, path)
        try:
            resp = requests.post(url, json={"query": _GRAPHQL_INTROSPECTION_QUERY}, timeout=config.timeout)
            if resp.status_code != 200:
                resp_get = requests.get(url, timeout=config.timeout)
                if resp_get.status_code != 200:
                    continue
                data = resp_get.json()
            else:
                data = resp.json()

            schema = data.get("data", {}).get("__schema", {})
            if not schema:
                continue

            query_type = schema.get("queryType", {}).get("name", "Query")
            mutation_type = schema.get("mutationType", {}).get("name", "Mutation")

            for type_def in schema.get("types", []):
                type_name = type_def.get("name", "")
                if type_name.startswith("__"):
                    continue
                if type_name not in (query_type, mutation_type):
                    continue
                fields = type_def.get("fields", [])
                for field in fields:
                    field_name = field.get("name", "")
                    if field_name.startswith("__"):
                        continue
                    args = field.get("args", [])
                    ep = Endpoint(
                        method="POST",
                        path=path,
                        params=[],
                        request_body=None,
                        graphql_field=field_name,
                        graphql_args=[{"name": a.get("name"), "type": a.get("type", {}).get("name", "String")} for a in args],
                        is_graphql=True,
                    )
                    endpoints.append(ep)
            if endpoints:
                break
        except (requests.RequestException, json.JSONDecodeError, KeyError):
            continue

    return endpoints


def _is_graphql_path(path: str) -> bool:
    return any(p in path.lower() for p in ["/graphql", "/query"])


def _is_llm_path(path: str) -> bool:
    lower = path.lower()
    return any(kw in lower for kw in ["/chat", "/completions", "/generate"])


def _generate_llm_payload() -> dict[str, Any]:
    return {
        "model": random.choice(_LLM_MODELS),
        "messages": [
            {"role": "system", "content": "You are a helpful assistant."},
            {"role": "user", "content": random.choice(_LLM_PROMPTS)},
        ],
        "temperature": 0.7,
        "max_tokens": 100,
    }


def _generate_graphql_payload(field: str, args: list[dict[str, Any]]) -> dict[str, Any]:
    arg_str = ""
    if args:
        arg_parts = []
        for a in args:
            arg_parts.append(f'{a["name"]}: "{_random_string(8)}"')
        arg_str = "(" + ", ".join(arg_parts) + ")"

    query = f"""
query {{
  {field}{arg_str} {{
    {_random_string(6)}
  }}
}}
"""
    return {"query": query.strip()}


def _resolve_type(schema: dict[str, Any]) -> str:
    if "$ref" in schema:
        return "object"
    return schema.get("type", "string")


def _is_health_path(path: str) -> bool:
    parts = path.strip("/").split("/")
    return any(p in _HEALTH_KEYWORDS for p in parts)


def _is_auth_endpoint(path: str) -> bool:
    lower = path.lower()
    return any(kw in lower for kw in ["login", "auth", "token", "signin", "oauth"])


def discover_endpoints_ast(service: ServiceInfo, root_dir: Path) -> list[Endpoint]:
    if service.language != "python":
        return []

    endpoints: list[Endpoint] = []
    py_files = list(root_dir.rglob("*.py"))

    for py_file in py_files:
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue

        endpoints.extend(_extract_fastapi_endpoints(tree))
        endpoints.extend(_extract_flask_endpoints(tree))
        endpoints.extend(_extract_django_urls(tree, py_file))

    return endpoints


def _extract_fastapi_endpoints(tree: ast.AST) -> list[Endpoint]:
    endpoints: list[Endpoint] = []
    route_methods = ("get", "post", "put", "delete", "patch")

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if not isinstance(func.value, ast.Name):
            continue
        method = func.attr.lower()
        if method not in route_methods:
            continue
        if not func.value.id.endswith(("app", "router", "api")):
            continue

        args = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not args:
            continue

        path_val = args[0].value
        assert isinstance(path_val, str)
        if _is_health_path(path_val):
            continue

        endpoints.append(
            Endpoint(
                method=method.upper(),
                path=path_val,
            )
        )

    return endpoints


def _extract_flask_endpoints(tree: ast.AST) -> list[Endpoint]:
    endpoints: list[Endpoint] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Attribute):
            continue
        if func.attr != "route":
            continue
        if not isinstance(func.value, ast.Name):
            continue
        if not func.value.id.endswith(("app", "blueprint", "bp")):
            continue

        args = [a for a in node.args if isinstance(a, ast.Constant) and isinstance(a.value, str)]
        if not args:
            continue

        path_val = args[0].value
        assert isinstance(path_val, str)
        if _is_health_path(path_val):
            continue

        methods = ["GET"]
        for kw in node.keywords:
            if kw.arg == "methods" and isinstance(kw.value, (ast.List, ast.Tuple)):
                methods = [e.value.upper() for e in kw.value.elts if isinstance(e, ast.Constant) and isinstance(e.value, str)]

        for method in methods:
            if method in ("GET", "POST", "PUT", "DELETE", "PATCH"):
                endpoints.append(Endpoint(method=method, path=path_val))

    return endpoints


def _extract_django_urls(tree: ast.AST, source_file: Path) -> list[Endpoint]:
    endpoints: list[Endpoint] = []

    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if not isinstance(func, ast.Name):
            continue
        if func.id not in ("path", "re_path"):
            continue

        args = node.args
        if len(args) < 2:
            continue

        route = ""
        if isinstance(args[0], ast.Constant) and isinstance(args[0].value, str):
            route = args[0].value

        if not route or _is_health_path(route):
            continue

        endpoints.append(Endpoint(method="GET", path=route))

        if _has_post_keyword(node):
            endpoints.append(Endpoint(method="POST", path=route))

    return endpoints


def _has_post_keyword(node: ast.Call) -> bool:
    for kw in node.keywords:
        if kw.arg == "name" and isinstance(kw.value, ast.Constant):
            name = str(kw.value.value).lower()
            if any(kw in name for kw in ("create", "add", "new", "post", "submit")):
                return True
    return False


def _generate_path_params(path: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for match in __import__("re").finditer(r"\{(\w+)\}", path):
        param_name = match.group(1)
        if param_name.lower() in ("id", "uid", "pk", "user_id", "item_id"):
            params[param_name] = str(uuid.uuid4())[:8]
        else:
            params[param_name] = _random_string(6)
    return params


def _generate_query_params(params: list[dict[str, Any]]) -> dict[str, str]:
    result: dict[str, str] = {}
    for param in params:
        if param.get("in") == "query":
            name = param["name"]
            if param.get("type") in ("integer", "number"):
                result[name] = str(random.randint(1, 100))
            elif param.get("type") == "boolean":
                result[name] = random.choice(["true", "false"])
            else:
                result[name] = _random_string(8)
    return result


def _generate_request_body(schema: dict[str, Any] | None) -> dict[str, Any] | list[Any] | None:
    if schema is None:
        return None

    if "$ref" in schema:
        return {"dummy": True}

    if schema.get("type") == "object":
        properties = schema.get("properties", {})
        body: dict[str, Any] = {}
        for prop_name, prop_schema in properties.items():
            body[prop_name] = _generate_value(prop_schema, prop_name)
        return body

    if schema.get("type") == "array":
        items = schema.get("items", {})
        return [_generate_value(items)]

    return {"value": _random_string(10)}


def _generate_value(schema: dict[str, Any], name: str = "") -> Any:
    schema_type = schema.get("type", "string")

    if schema_type == "string":
        if name.lower() in ("email", "mail"):
            return f"test.{uuid.uuid4().hex[:6]}@example.com"
        if name.lower() in ("url", "website", "link"):
            return f"https://example.com/{uuid.uuid4().hex[:6]}"
        if name.lower() in ("phone", "tel", "telephone"):
            return f"+1-555-{random.randint(100, 999):03d}-{random.randint(1000, 9999):04d}"
        if name.lower() in ("uuid", "id", "uid"):
            return uuid.uuid4().hex[:12]
        return _random_string(10)

    if schema_type == "integer":
        return random.randint(1, 1000)

    if schema_type == "number":
        return round(random.uniform(0.0, 1000.0), 2)

    if schema_type == "boolean":
        return random.choice([True, False])

    if schema_type == "array":
        return [_generate_value(schema.get("items", {}))]

    if schema_type == "object":
        result: dict[str, Any] = {}
        for k, v in schema.get("properties", {}).items():
            result[k] = _generate_value(v, k)
        return result

    return None


def _random_string(length: int) -> str:
    return "".join(random.choices(string.ascii_lowercase, k=length))


def _send_request(
    method: str,
    base_url: str,
    path: str,
    params: list[dict[str, Any]],
    request_body: dict[str, Any] | None,
    config: TrafficConfig,
    endpoint: Endpoint | None = None,
) -> tuple[bool, int, str]:
    path_params = _generate_path_params(path)
    resolved_path = path
    for key, val in path_params.items():
        resolved_path = resolved_path.replace(f"{{{key}}}", val)

    url = urljoin(base_url, resolved_path)
    query_params = _generate_query_params(params)

    headers = {"Content-Type": "application/json"}
    if config.auth_token:
        headers["Authorization"] = f"Bearer {config.auth_token}"

    body: dict[str, Any] | list[Any] | None = None
    if method in ("POST", "PUT", "PATCH"):
        if (endpoint and endpoint.is_graphql) or _is_graphql_path(path):
            ep = endpoint
            body = (
                _generate_graphql_payload(
                    ep.graphql_field if ep else "",
                    ep.graphql_args if ep else [],
                )
                if ep
                else None
            )
            if body:
                method = "POST"
        elif _is_llm_path(path):
            body = _generate_llm_payload()
        else:
            body = _generate_request_body(request_body)

    try:
        resp = requests.request(
            method=method,
            url=url,
            params=query_params if query_params else None,
            json=body if body else None,
            headers=headers,
            timeout=config.timeout,
        )
        return resp.status_code < 500, resp.status_code, url
    except requests.RequestException as e:
        return False, 0, f"{url} ({e})"


def _try_oauth2_client_credentials(base_url: str, config: TrafficConfig) -> str | None:
    token_paths = [
        "/auth/token",
        "/api/auth/token",
        "/oauth/token",
        "/api/oauth/token",
        "/token",
        "/api/token",
        "/connect/token",
    ]

    for path in token_paths:
        url = urljoin(base_url, path)
        try:
            for client_id, client_secret in [
                ("client", "secret"),
                ("api-client", "api-secret"),
                ("service-account", "sa-secret"),
            ]:
                resp = requests.post(
                    url,
                    data={
                        "grant_type": "client_credentials",
                        "client_id": client_id,
                        "client_secret": client_secret,
                    },
                    timeout=config.timeout,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    token = data.get("access_token") or data.get("token") or data.get("id_token")
                    if token:
                        return str(token)
        except requests.RequestException:
            continue

    return None


def _try_oauth2_password_grant(base_url: str, config: TrafficConfig) -> str | None:
    token_paths = ["/auth/token", "/api/auth/token", "/oauth/token", "/api/oauth/token", "/token", "/api/token"]

    for path in token_paths:
        url = urljoin(base_url, path)
        try:
            for username, password in [
                ("admin", "admin"),
                ("user", "password"),
                ("test", "test"),
                ("admin@test.com", "admin123"),
            ]:
                resp = requests.post(
                    url,
                    data={
                        "grant_type": "password",
                        "username": username,
                        "password": password,
                    },
                    timeout=config.timeout,
                )
                if resp.status_code in (200, 201):
                    data = resp.json()
                    token = data.get("access_token") or data.get("token") or data.get("id_token")
                    if token:
                        return str(token)
        except requests.RequestException:
            continue

    return None


def _try_oauth2_refresh(base_url: str, existing_token: str, config: TrafficConfig) -> str | None:
    token_paths = ["/auth/token", "/api/auth/token", "/oauth/token", "/token", "/api/token", "/connect/token"]

    for path in token_paths:
        url = urljoin(base_url, path)
        try:
            resp = requests.post(
                url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": existing_token,
                },
                timeout=config.timeout,
            )
            if resp.status_code in (200, 201):
                data = resp.json()
                token = data.get("access_token") or data.get("token")
                if token:
                    return str(token)
        except requests.RequestException:
            continue

    return None


def _try_auth(base_url: str, config: TrafficConfig) -> str | None:
    auth_paths = ["/auth/login", "/api/auth/login", "/login", "/api/login", "/auth/token"]

    for path in auth_paths:
        url = urljoin(base_url, path)
        try:
            payloads = [
                {"username": "admin", "password": "admin"},
                {"username": "test", "password": "test"},
                {"username": "user", "password": "password"},
                {"username": "admin@test.com", "password": "admin123"},
            ]
            for payload in payloads:
                resp = requests.post(url, json=payload, timeout=5)
                if resp.status_code in (200, 201):
                    data = resp.json()
                    token = data.get("access_token") or data.get("token") or data.get("id_token") or data.get("accessToken")
                    if token:
                        return str(token)
        except requests.RequestException:
            continue

    token = _try_oauth2_client_credentials(base_url, config)
    if token:
        return token

    token = _try_oauth2_password_grant(base_url, config)
    if token:
        return token

    return None


def generate_traffic(
    service: ServiceInfo,
    config: TrafficConfig,
    endpoints: list[Endpoint] | None = None,
) -> TrafficResult:
    base_url = f"{config.base_url}:{service.port}" if service.port else config.base_url
    result = TrafficResult()

    if endpoints is None:
        endpoints = discover_endpoints_openapi("127.0.0.1", service.port or 8000, config)
        if not endpoints:
            endpoints = discover_endpoints_graphql("127.0.0.1", service.port or 8000, config)
        if not endpoints:
            endpoints = discover_endpoints_ast(service, service.entry_points[0].path.parent)
        if not endpoints:
            result.errors.append("No endpoints discovered")
            return result

    has_llm = any(_is_llm_path(ep.path) for ep in endpoints)
    result.llm_endpoints = has_llm
    has_graphql = any(ep.is_graphql or _is_graphql_path(ep.path) for ep in endpoints)
    result.graphql_endpoints = has_graphql

    result.endpoints_discovered = len(endpoints)

    if config.auth_token is None:
        config.auth_token = _try_auth(base_url, config)
        result.auth_used = config.auth_token is not None

    endpoints_tested: list[Endpoint] = []
    auth_eps: list[Endpoint] = []
    noauth_eps: list[Endpoint] = []

    for ep in endpoints:
        if ep.auth_required:
            auth_eps.append(ep)
        else:
            noauth_eps.append(ep)

    if config.auth_token:
        endpoints_tested = endpoints
    else:
        endpoints_tested = noauth_eps
        if auth_eps:
            result.errors.append(f"Skipped {len(auth_eps)} auth-required endpoints")

    result.endpoints_tested = len(endpoints_tested)

    request_patterns: list[tuple[str, str, Endpoint]] = []
    for ep in endpoints_tested:
        request_patterns.append((ep.method, ep.path, ep))
        if ep.method == "GET":
            request_patterns.append(("GET", ep.path, ep))

    get_endpoints = [ep for ep in endpoints_tested if ep.method == "GET"]
    post_endpoints = [ep for ep in endpoints_tested if ep.method == "POST"]
    put_endpoints = [ep for ep in endpoints_tested if ep.method == "PUT"]
    delete_endpoints = [ep for ep in endpoints_tested if ep.method == "DELETE"]

    def worker_task() -> tuple[bool, str]:
        if request_patterns and random.random() < 0.6 and get_endpoints:
            ep = random.choice(get_endpoints)
        elif request_patterns and random.random() < 0.3 and post_endpoints:
            ep = random.choice(post_endpoints)
        elif put_endpoints and random.random() < 0.5:
            ep = random.choice(put_endpoints)
        elif delete_endpoints:
            ep = random.choice(delete_endpoints)
        elif endpoints_tested:
            ep = random.choice(endpoints_tested)
        else:
            return True, "no endpoints to test"

        success, status, url = _send_request(ep.method, base_url, ep.path, ep.params, ep.request_body, config, endpoint=ep)
        if status == 429:
            return False, "rate_limited"
        return success, url

    deadline = time.time() + config.duration
    ok_count = 0
    fail_count = 0
    rate_limited = False
    tested_endpoints: set[str] = set()
    _traffic_lock = threading.Lock()

    with ThreadPoolExecutor(max_workers=config.workers) as executor:
        futures = []
        while time.time() < deadline:
            if rate_limited:
                time.sleep(2)
                rate_limited = False

            f = executor.submit(worker_task)
            futures.append(f)

            if len(futures) >= 10:
                done = futures[:]
                futures = []
                for future in as_completed(done):
                    try:
                        success, info = future.result(timeout=config.timeout)
                        with _traffic_lock:
                            if success:
                                ok_count += 1
                                if isinstance(info, str):
                                    tested_endpoints.add(info)
                            elif info == "rate_limited":
                                rate_limited = True
                                fail_count += 1
                            else:
                                fail_count += 1
                    except Exception as e:
                        logger.warning("Traffic request failed: %s", e)
                        with _traffic_lock:
                            fail_count += 1

            time.sleep(random.uniform(config.interval_min, config.interval_max))

        for future in as_completed(futures):
            try:
                success, info = future.result(timeout=config.timeout)
                with _traffic_lock:
                    if success:
                        ok_count += 1
                    else:
                        fail_count += 1
            except Exception as e:
                logger.warning("Future result failed: %s", e)
                with _traffic_lock:
                    fail_count += 1

    result.total_requests = ok_count + fail_count
    result.successful_requests = ok_count
    result.failed_requests = fail_count
    result.endpoints_ok = len(tested_endpoints)
    result.duration_seconds = time.time() - (deadline - config.duration)

    if result.none_succeeded and endpoints_tested:
        raise AnalysisError(
            code=ErrorCode.ALL_ENDPOINTS_FAILED,
            scope=service.name,
            _override_detail=f"All {len(endpoints_tested)} endpoints returned errors. Last URL attempted: see --verbose.",
            recoverable=False,
        )

    return result
