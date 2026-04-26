"""
otel_instrumentation.py – Tornado
====================================
OpenTelemetry tracing for Tornado applications.

Add at the top of your main file (before IOLoop starts):

    from otel_instrumentation import init_tracing
    init_tracing()
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.tornado import TornadoInstrumentor


def init_tracing():
    """
    Initialize OpenTelemetry for Tornado.
    Call this before tornado.ioloop.IOLoop.current().start().
    """
    resource = Resource.create({"service.name": "{{SERVICE_NAME}}"})

    exporter = OTLPSpanExporter(
        endpoint="http://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}"
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Instrument all Tornado RequestHandlers automatically
    TornadoInstrumentor().instrument()

    print(f"[OTel] Tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}")
