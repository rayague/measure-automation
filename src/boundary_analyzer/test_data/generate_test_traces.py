from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any
from uuid import uuid4


def _generate_trace_id() -> str:
    """Generate a random trace ID (hex string)."""
    return uuid4().hex


def _generate_span_id() -> str:
    """Generate a random span ID (hex string)."""
    return uuid4().hex[:16]


def _create_span(
    span_id: str,
    parent_span_id: str | None,
    operation_name: str,
    service_name: str,
    start_time: int,
    duration: int,
    tags: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a span in Jaeger format."""
    return {
        "traceID": "",
        "spanID": span_id,
        "parentSpanID": parent_span_id,
        "operationName": operation_name,
        "startTime": start_time,
        "duration": duration,
        "tags": tags or [],
        "process": {
            "serviceName": service_name,
            "tags": [],
        },
        "logs": [],
        "references": [
            {"refType": "CHILD_OF", "spanID": parent_span_id}
        ] if parent_span_id else [],
    }


def _create_http_endpoint_span(
    service_name: str,
    method: str,
    path: str,
    trace_id: str,
    start_time: int,
) -> dict[str, Any]:
    """Create an HTTP endpoint span."""
    span_id = _generate_span_id()
    operation_name = f"{method} {path}"
    
    tags = [
        {"key": "http.method", "value": method},
        {"key": "http.route", "value": path},
        {"key": "http.target", "value": path},
    ]
    
    span = _create_span(
        span_id=span_id,
        parent_span_id=None,
        operation_name=operation_name,
        service_name=service_name,
        start_time=start_time,
        duration=random.randint(1000, 5000),
        tags=tags,
    )
    span["traceID"] = trace_id
    
    return span


def _create_db_span(
    service_name: str,
    table: str,
    operation: str,
    parent_span_id: str,
    trace_id: str,
    start_time: int,
) -> dict[str, Any]:
    """Create a database operation span."""
    span_id = _generate_span_id()
    operation_name = f"{operation} {table}"
    
    sql_statements = {
        "SELECT": f"SELECT * FROM {table} WHERE id = ?",
        "INSERT": f"INSERT INTO {table} (col1, col2) VALUES (?, ?)",
        "UPDATE": f"UPDATE {table} SET col1 = ? WHERE id = ?",
        "DELETE": f"DELETE FROM {table} WHERE id = ?",
    }
    
    tags = [
        {"key": "db.system", "value": "mysql"},
        {"key": "db.name", "value": "testdb"},
        {"key": "db.statement", "value": sql_statements.get(operation, f"{operation} {table}")},
        {"key": "db.operation", "value": operation},
    ]
    
    span = _create_span(
        span_id=span_id,
        parent_span_id=parent_span_id,
        operation_name=operation_name,
        service_name=service_name,
        start_time=start_time + 500,
        duration=random.randint(1000, 3000),
        tags=tags,
    )
    span["traceID"] = trace_id
    
    return span


def _generate_service_trace(
    service_name: str,
    endpoints: list[tuple[str, str]],  # (method, path)
    tables: list[str],
    num_traces: int,
    base_start_time: int,
) -> list[dict[str, Any]]:
    """Generate traces for a single service.
    
    Args:
        service_name: Name of the service
        endpoints: List of (method, path) tuples
        tables: List of tables used by this service
        num_traces: Number of traces to generate
        base_start_time: Base start time in microseconds
    
    Returns:
        List of trace objects in Jaeger format
    """
    traces = []
    
    for i in range(num_traces):
        trace_id = _generate_trace_id()
        start_time = base_start_time + (i * 10000000)
        
        # Random endpoint
        method, path = random.choice(endpoints)
        
        # Create root span (HTTP endpoint)
        root_span = _create_http_endpoint_span(
            service_name=service_name,
            method=method,
            path=path,
            trace_id=trace_id,
            start_time=start_time,
        )
        
        spans = [root_span]
        
        # Add DB spans (1-3 DB operations per trace)
        num_db_ops = random.randint(1, 3)
        for _ in range(num_db_ops):
            table = random.choice(tables)
            operation = random.choice(["SELECT", "INSERT", "UPDATE"])
            
            db_span = _create_db_span(
                service_name=service_name,
                table=table,
                operation=operation,
                parent_span_id=root_span["spanID"],
                trace_id=trace_id,
                start_time=start_time,
            )
            spans.append(db_span)
        
        traces.append({
            "traceID": trace_id,
            "spans": spans,
        })
    
    return traces


def generate_test_scenarios() -> dict[str, list[dict[str, Any]]]:
    """Generate test scenarios with different cohesion patterns.
    
    Scenarios:
    1. High cohesion service: endpoints use same tables
    2. Low cohesion service: endpoints use different tables
    3. Mixed service: some overlap, some differences
    """
    base_start_time = 1700000000000000  # Microseconds since epoch
    
    # Scenario 1: High cohesion service (user-service)
    # Endpoints: GET /users, POST /users, GET /users/{id}
    # Tables: users, profiles (shared across all endpoints)
    user_service_traces = _generate_service_trace(
        service_name="user-service",
        endpoints=[("GET", "/users"), ("POST", "/users"), ("GET", "/users/{id}")],
        tables=["users", "profiles"],
        num_traces=20,
        base_start_time=base_start_time,
    )
    
    # Scenario 2: Low cohesion service (order-service)
    # Endpoints: GET /orders, POST /orders, GET /products, POST /payments
    # Tables: orders, products, payments (each endpoint uses different tables)
    order_service_traces = _generate_service_trace(
        service_name="order-service",
        endpoints=[
            ("GET", "/orders"),
            ("POST", "/orders"),
            ("GET", "/products"),
            ("POST", "/payments"),
        ],
        tables=["orders", "products", "payments"],
        num_traces=20,
        base_start_time=base_start_time + 200000000,
    )
    
    # Scenario 3: Mixed cohesion service (inventory-service)
    # Endpoints: GET /inventory, POST /inventory, GET /stock
    # Tables: inventory, stock, warehouse (partial overlap)
    inventory_service_traces = _generate_service_trace(
        service_name="inventory-service",
        endpoints=[
            ("GET", "/inventory"),
            ("POST", "/inventory"),
            ("GET", "/stock"),
        ],
        tables=["inventory", "stock", "warehouse"],
        num_traces=15,
        base_start_time=base_start_time + 400000000,
    )
    
    # Scenario 4: Another high cohesion service (notification-service)
    # Endpoints: POST /notify, POST /email, POST /sms
    # Tables: notifications, logs (shared)
    notification_service_traces = _generate_service_trace(
        service_name="notification-service",
        endpoints=[
            ("POST", "/notify"),
            ("POST", "/email"),
            ("POST", "/sms"),
        ],
        tables=["notifications", "logs"],
        num_traces=15,
        base_start_time=base_start_time + 600000000,
    )
    
    return {
        "user-service": user_service_traces,
        "order-service": order_service_traces,
        "inventory-service": inventory_service_traces,
        "notification-service": notification_service_traces,
    }


def save_test_traces(traces_by_service: dict[str, list[dict[str, Any]]], output_dir: Path) -> None:
    """Save test traces to JSON files in Jaeger export format."""
    output_dir.mkdir(parents=True, exist_ok=True)
    
    for service_name, traces in traces_by_service.items():
        # Jaeger export format
        jaeger_response = {
            "data": traces,
            "total": len(traces),
            "limit": len(traces),
            "offset": 0,
            "errors": None,
        }
        
        output_file = output_dir / f"{service_name}_traces.json"
        with output_file.open("w", encoding="utf-8") as f:
            json.dump({"jaeger_response": jaeger_response}, f, indent=2)
        
        print(f"Saved {len(traces)} traces for {service_name} to {output_file}")


def main() -> int:
    """Generate synthetic test traces."""
    output_dir = Path("data/raw/traces")
    
    print("Generating synthetic test traces...")
    print("=" * 60)
    
    traces_by_service = generate_test_scenarios()
    
    save_test_traces(traces_by_service, output_dir)
    
    print("\n" + "=" * 60)
    print("Test traces generated successfully!")
    print(f"Output directory: {output_dir}")
    print("\nScenarios:")
    print("  - user-service: High cohesion (shared tables)")
    print("  - order-service: Low cohesion (different tables)")
    print("  - inventory-service: Mixed cohesion (partial overlap)")
    print("  - notification-service: High cohesion (shared tables)")
    print("\nNext steps:")
    print("  1. Run: python .\\src\\boundary_analyzer\\pipeline\\step_02_read_traces.py")
    print("  2. Continue with steps 3-8")
    
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
