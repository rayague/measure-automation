<?php
/**
 * otel_instrumentation.php – Laravel
 * =====================================
 * OpenTelemetry tracing for Laravel applications.
 *
 * HOW TO USE:
 *   Add this line in bootstrap/app.php, AFTER $app = new Application(...):
 *
 *     require __DIR__.'/../otel_instrumentation.php';
 *
 * HOW IT WORKS:
 *   - Every HTTP request Laravel handles becomes a span.
 *   - Every database query (via Eloquent) also becomes a span.
 *   - All spans are sent to Jaeger.
 *
 * REQUIREMENTS (already installed by the setup script):
 *   composer require open-telemetry/sdk \
 *                    open-telemetry/exporter-otlp-grpc \
 *                    open-telemetry/opentelemetry-auto-laravel
 */

use OpenTelemetry\API\Globals;
use OpenTelemetry\Contrib\Otlp\OtlpHttpTransportFactory;
use OpenTelemetry\Contrib\Otlp\SpanExporter;
use OpenTelemetry\SDK\Common\Attribute\Attributes;
use OpenTelemetry\SDK\Resource\ResourceInfo;
use OpenTelemetry\SDK\Trace\Sampler\AlwaysOnSampler;
use OpenTelemetry\SDK\Trace\SpanProcessor\BatchSpanProcessor;
use OpenTelemetry\SDK\Trace\TracerProvider;
use OpenTelemetry\SemConv\ResourceAttributes;

// ── Build the resource (tells Jaeger our service name) ──────────────────────
$resource = ResourceInfo::create(
    Attributes::create([
        ResourceAttributes::SERVICE_NAME => '{{SERVICE_NAME}}',
    ])
);

// ── Build the exporter (sends traces to Jaeger over HTTP) ───────────────────
// Note: PHP SDK uses HTTP/JSON by default (gRPC requires extra extensions)
$transport = (new OtlpHttpTransportFactory())->create(
    'http://{{JAEGER_HOST}}:4318/v1/traces',   // Jaeger OTLP HTTP port
    'application/x-protobuf'
);

$exporter = new SpanExporter($transport);

// ── Build the tracer provider ────────────────────────────────────────────────
$tracerProvider = new TracerProvider(
    new BatchSpanProcessor($exporter),
    new AlwaysOnSampler(),
    $resource
);

// Register the provider globally so Laravel's auto-instrumentation uses it
Globals::registerInitializer(function (Configurator $configurator) use ($tracerProvider) {
    $propagator = TraceContextPropagator::getInstance();
    return $configurator
        ->withTracerProvider($tracerProvider)
        ->withPropagator($propagator);
});

// Flush traces when PHP finishes (important for CLI commands)
register_shutdown_function(function () use ($tracerProvider) {
    $tracerProvider->shutdown();
});

error_log('[OTel] Laravel tracing enabled → Jaeger at {{JAEGER_HOST}}:4318');
