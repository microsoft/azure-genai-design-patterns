# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-21

### Added

- `README.md` with the problem, when to use and when not to use, an at-a-glance architecture diagram, a decision matrix (built-in tracing only, OpenTelemetry Collector, lightweight receiver), key design decisions, getting started steps (offline sample first, then wiring a hosted agent with runtime environment variables or a second span processor), Domain Packs, a file map, related assets and preview caveats.
- `manifest.yaml` with the asset metadata, pillars, Azure services, tags and related assets.
- `docs/architecture.md` with the topology and sequence diagrams (export happy path with partial success, back-pressure with `429` and `503` and `Retry-After`), the request lifecycle, OTLP/JSON wire contracts (export request subset with `gen_ai.*` attributes, success and partial-success responses, error body), the receiver policy and redaction report shapes, configurable payload bounds, the redaction design, correlation with the gateway's `traceparent`, the sink contract, observability of the receiver itself, anti-patterns and a failure modes table.
- `docs/authentication-and-governance.md` covering exporter authentication options (Entra token from the agent's managed identity, a shared header for local development only, mTLS), the Entra registration and the claims to check (issuer, audience, tenant, client allowlist, app role), why attributes are not authorization, telemetry data classification, content capture governance, identifier hashing and key management, retention and residency, who can read the second copy, network and egress, a threat model and a review checklist.
- `docs/evaluation-and-troubleshooting.md` with test layers, a test matrix, how to prove spans arrive, how to prove dual export and that content stays out, negative authentication tests, common failures and a runbook with triage steps, queries and a release gate.
- `docs/decisions/0001-dual-export-not-replace-platform-tracing.md`, `docs/decisions/0002-redact-at-receiver-allowlist-attributes.md` and `docs/decisions/0003-collector-first-receiver-as-reference.md`.
- `samples/otlp_trace_receiver.py`, a standard-library-only OTLP/HTTP JSON trace receiver reference with a fake exporter, fake token validator, payload bounds, allowlist redaction, keyed identifier hashing, partial success, back-pressure responses, an in-memory sink, production stubs, a loopback-only `--serve` mode and `--self-test`, `--domain` and `--demo` modes, with `samples/README.md`.
