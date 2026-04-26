"""
otel_instrumentation.py – Starlette
=====================================
OpenTelemetry tracing for Starlette applications.

Add at the top of your main file:

    from otel_instrumentation import init_tracing
    init_tracing()
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.starlette import StarletteInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor


def init_tracing(app=None):
    """
    Initialize OpenTelemetry for Starlette.
    Optionally pass your Starlette app to instrument it directly.
    """
    resource = Resource.create({"service.name": "{{SERVICE_NAME}}"})

    exporter = OTLPSpanExporter(
        endpoint="http://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}"
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    SQLAlchemyInstrumentor().instrument()

    if app is not None:
        StarletteInstrumentor.instrument_app(app)
    else:
        StarletteInstrumentor().instrument()

    print(f"[OTel] Tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}")
