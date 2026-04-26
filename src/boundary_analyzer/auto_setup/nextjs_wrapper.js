/**
 * otel_instrumentation.js – Next.js
 * ====================================
 * OpenTelemetry tracing for Next.js applications.
 *
 * HOW TO USE:
 *   1. Create a file called instrumentation.ts (or .js) at the ROOT of your project.
 *   2. Paste this inside it:
 *
 *        export async function register() {
 *          if (process.env.NEXT_RUNTIME === 'nodejs') {
 *            await import('./otel_instrumentation');
 *          }
 *        }
 *
 *   3. In next.config.js add:
 *        experimental: { instrumentationHook: true }
 *
 *   4. Restart Next.js.
 */

'use strict';

const { NodeSDK }          = require('@opentelemetry/sdk-node');
const { OTLPTraceExporter }= require('@opentelemetry/exporter-trace-otlp-grpc');
const { Resource }         = require('@opentelemetry/resources');
const { SemanticResourceAttributes } = require('@opentelemetry/semantic-conventions');
const { getNodeAutoInstrumentations } = require('@opentelemetry/auto-instrumentations-node');

const exporter = new OTLPTraceExporter({
  url: 'grpc://{{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}',
});

const sdk = new NodeSDK({
  resource: new Resource({
    [SemanticResourceAttributes.SERVICE_NAME]: '{{SERVICE_NAME}}',
  }),
  traceExporter: exporter,
  // Auto-instrumentations cover HTTP, fetch, and database calls
  instrumentations: [
    getNodeAutoInstrumentations({
      // We disable fs instrumentation — it creates too much noise in Next.js
      '@opentelemetry/instrumentation-fs': { enabled: false },
    }),
  ],
});

sdk.start();

process.on('SIGTERM', () => {
  sdk.shutdown().finally(() => process.exit(0));
});

console.log(`[OTel] Next.js tracing enabled → Jaeger at {{JAEGER_HOST}}:{{JAEGER_GRPC_PORT}}`);
