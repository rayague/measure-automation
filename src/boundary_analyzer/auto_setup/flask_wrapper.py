"""
otel_instrumentation.py – Flask
================================
This file sets up OpenTelemetry tracing for your Flask application.

HOW IT WORKS:
  - Every HTTP request your app receives becomes a "span" (a recorded event).
  - Every database query also becomes a span.
  - All spans are sent to Jaeger so we can see them.

YOU DO NOT NEED TO EDIT THIS FILE.
Just call init_tracing() at the top of your main app file.
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.flask import FlaskInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor


def init_tracing():
    """
    Call this function ONCE at the start of your app.
    It configures OpenTelemetry to send traces to Jaeger.
    """

    # 'resource' tells Jaeger which service these traces belong to
    resource = Resource.create({"service.name": "{{SERVICE_NAME}}"})

    # 'exporter' sends the traces to Jaeger over gRPC
    exporter = OTLPSpanExporter(
        endpoint="http://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}"
    )

    # 'provider' is the main tracing engine
    provider = TracerProvider(resource=resource)

    # 'processor' batches spans and sends them to the exporter
    provider.add_span_processor(BatchSpanProcessor(exporter))

    # Register the provider globally
    trace.set_tracer_provider(provider)

    # Automatically trace every Flask HTTP request
    FlaskInstrumentor().instrument()

    # Automatically trace every SQLAlchemy database query
    SQLAlchemyInstrumentor().instrument()

    print(f"[OTel] Tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}")
