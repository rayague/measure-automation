"""
otel_instrumentation.py – FastAPI
===================================
This file sets up OpenTelemetry tracing for your FastAPI application.

HOW IT WORKS:
  - Every HTTP request becomes a span (recorded event).
  - Every database query (via SQLAlchemy) also becomes a span.
  - All spans are sent to Jaeger so we can see and analyze them.

YOU DO NOT NEED TO EDIT THIS FILE.
Just call init_tracing() at the top of your main.py (before app = FastAPI()).
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor


def init_tracing(app=None):
    """
    Call this function ONCE at the start of your app.

    If you pass your FastAPI app object, HTTP routes will be traced automatically.
    Example:
        app = FastAPI()
        init_tracing(app)

    If you do not pass the app, call FastAPIInstrumentor().instrument_app(app)
    after creating it.
    """

    # Tell Jaeger the name of this service
    resource = Resource.create({"service.name": "{{SERVICE_NAME}}"})

    # Send traces to Jaeger via gRPC
    exporter = OTLPSpanExporter(
        endpoint="http://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}"
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Instrument database queries automatically
    SQLAlchemyInstrumentor().instrument()

    # Instrument the FastAPI app if provided
    if app is not None:
        FastAPIInstrumentor.instrument_app(app)
    else:
        # Will instrument the first FastAPI app created after this call
        FastAPIInstrumentor().instrument()

    print(f"[OTel] Tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}")
