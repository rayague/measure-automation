"""
otel_instrumentation.py – Django / Django REST Framework
==========================================================
This file sets up OpenTelemetry tracing for your Django application.

HOW IT WORKS:
  - Every HTTP request Django handles becomes a span.
  - Every ORM database query also becomes a span.
  - All spans are sent to Jaeger.

YOU DO NOT NEED TO EDIT THIS FILE.
Add these 2 lines BEFORE django.setup() in your manage.py or wsgi.py:

    from otel_instrumentation import init_tracing
    init_tracing()
"""

from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.instrumentation.django import DjangoInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor


def init_tracing():
    """
    Call this function ONCE, before django.setup() is called.
    """

    resource = Resource.create({"service.name": "{{SERVICE_NAME}}"})

    exporter = OTLPSpanExporter(
        endpoint="http://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}"
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Automatically trace every Django view / URL
    DjangoInstrumentor().instrument()

    # Automatically trace every ORM query
    SQLAlchemyInstrumentor().instrument()

    print(f"[OTel] Tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}")
