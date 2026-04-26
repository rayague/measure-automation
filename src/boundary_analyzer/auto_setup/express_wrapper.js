/**
 * otel_instrumentation.js – Express.js
 * ======================================
 * OpenTelemetry tracing for Express.js applications.
 *
 * HOW IT WORKS:
 *   - Every HTTP request Express handles becomes a span.
 *   - Spans are sent to Jaeger so we can analyze them.
 *
 * ADD THIS AS THE VERY FIRST LINE of your app.js or index.js:
 *   require('./otel_instrumentation');
 *
 * IMPORTANT: It must be the first line — before Express is imported.
 */

'use strict';

const { NodeSDK }          = require('@opentelemetry/sdk-node');
const { OTLPTraceExporter }= require('@opentelemetry/exporter-trace-otlp-grpc');
const { Resource }         = require('@opentelemetry/resources');
const { SemanticResourceAttributes } = require('@opentelemetry/semantic-conventions');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');

// The exporter sends all traces to Jaeger
const exporter = new OTLPTraceExporter({
  url: 'grpc://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}',
});

// The SDK glues everything together
const sdk = new NodeSDK({
  // Tell Jaeger the name of this service
  resource: new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]: '{{SERVICE_NAME}}',
  }),
  traceExporter: exporter,
  // Automatically trace HTTP, Express, database, etc.
  instrumentations: [getNodeAutoInstrumentations()],
});

// Start the SDK — this must happen before anything else
sdk.start();

// When the process stops, flush all remaining traces to Jaeger
process.on('SIGTERM', () => {
  sdk.shutdown()
    .then(() => console.log('[OTel] SDK shut down successfully.'))
    .catch((err) => console.error('[OTel] Error shutting down SDK:', err))
    .finally(() => process.exit(0));
});

console.log(`[OTel] Tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}`);
