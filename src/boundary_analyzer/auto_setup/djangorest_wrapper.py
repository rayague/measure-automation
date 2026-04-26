"""
otel_instrumentation.py – Django REST Framework
=================================================
Same setup as Django — DRF sits on top of Django,
so we instrument at the Django level.

Add these 2 lines BEFORE django.setup():

    from otel_instrumentation import init_tracing
    init_tracing()
"""

# Django REST Framework uses the same Django instrumentation
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor


def init_tracing():
    """
    Initialize tracing for Django REST Framework.
    DRF is built on top of Django, so we instrument Django directly.
    """
    resource = Resource.create({"service.name": "{{SERVICE_NAME}}"})

    exporter = OTLPSpanExporter(
        endpoint="http://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}"
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Instrument every DRF/Django view automatically
    DjangoInstrumentor().instrument()

    # Instrument every database query automatically
    SQLAlchemyInstrumentor().instrument()

    print(f"[OTel] Tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}")
