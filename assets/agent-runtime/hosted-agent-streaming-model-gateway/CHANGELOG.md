# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-21

### Added

- `README.md` with the problem, when to use and when not to use, an at-a-glance architecture diagram, a decision matrix (direct Foundry deployment, Azure API Management AI gateway, Foundry model gateway connection, custom streaming gateway), key design decisions, Domain Packs, getting started steps (simulator first, then deployment), a file map and related assets.
- `manifest.yaml` with the asset metadata, pillars, Azure services, tags and related assets.
- `docs/architecture.md` with the topology and sequence diagrams (streaming happy path, client disconnect, failover before the first byte versus a failure mid-stream, policy deny), the wire contracts as JSON shapes, streaming correctness rules, routing and failover, policy enforcement points, observability and usage attribution, anti-patterns and a failure modes table.
- `docs/authentication-and-governance.md` covering the hosted agent identity to gateway hop (issuer, audience, app role, caller allowlist and tenant checks), the gateway identity to Foundry hop, bring-your-own backend credentials in Azure Key Vault, what the request context does not authorize, governance gaps and a threat model.
- `docs/evaluation-and-troubleshooting.md` with a test matrix, how to prove a stream is not buffered, common failures and a troubleshooting runbook.
- `docs/deployment-and-operations.md` with the phased provisioning order, teardown order, rollout and rollback, qualitative scaling notes and cost attribution.
- `docs/decisions/0001-custom-streaming-gateway-vs-apim-ai-gateway.md`, `docs/decisions/0002-retry-only-before-first-byte.md` and `docs/decisions/0003-gateway-auth-entra-mi-only.md`.
- `infra/main.bicep`, an illustrative and undeployed template for the Container Apps environment, the gateway container app, its user-assigned managed identity, Log Analytics, Application Insights, an RBAC-mode Key Vault and the role assignments on an existing Foundry account and container registry, with `infra/parameters.example.json`, `infra/pipelines/gateway-deploy.yml` and `infra/README.md`.
- `src/gateway/app.py`, an illustrative OpenAI-compatible streaming proxy (Starlette and httpx) with Entra token validation, per-caller policy, retry before the first byte, keepalive comments, cancellation on disconnect and usage telemetry, with `src/gateway/requirements.txt` and `src/gateway/Dockerfile`.
- `src/agent/main.py`, a hosted agent snippet that points its model client at the gateway with a managed identity token, with `src/agent/requirements.txt` and `src/README.md`.
- `samples/streaming_gateway_sim.py`, a standard-library-only simulator with `--self-test`, `--domain` and `--demo` modes covering token validation, policy, failover, keepalives, cancellation, buffering detection, idempotency, trace propagation and usage attribution, with `samples/README.md`.
