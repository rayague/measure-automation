/**
 * otel_instrumentation.js – Nest.js
 * ====================================
 * OpenTelemetry tracing for Nest.js applications.
 *
 * HOW TO USE:
 *   Add this as the VERY FIRST LINE of main.ts (before everything else):
 *
 *     import './otel_instrumentation';
 *
 *   It MUST come before NestFactory.create(...) so all modules are traced.
 */

'use strict';

const { NodeSDK }          = require('@opentelemetry/sdk-node');
const { OTLPTraceExporter }= require('@opentelemetry/exporter-trace-otlp-grpc');
const { Resource }         = require('@opentelemetry/resources');
const { SemanticResourceAttributes } = require('@opentelemetry/semantic-conventions');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');
const { HttpInstrumentation }    = require('@opentelemetry/instrumentation-http');
const { ExpressInstrumentation } = require('@opentelemetry/instrumentation-express');

const exporter = new OTLPTraceExporter({
  url: 'grpc://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}',
});

const sdk = new NodeSDK({
  resource: new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]: '{{SERVICE_NAME}}',
  }),
  traceExporter: exporter,
  instrumentations: [
    // Auto-instrumentations cover HTTP, databases, etc.
    getNodeAutoInstrumentations(),
    // These two are important for Nest.js specifically
    new HttpInstrumentation(),
    new ExpressInstrumentation(),   // Nest.js uses Express under the hood
  ],
});

sdk.start();

process.on('SIGTERM', () => {
  sdk.shutdown().finally(() => process.exit(0));
});

console.log(`[OTel] Nest.js tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}`);
