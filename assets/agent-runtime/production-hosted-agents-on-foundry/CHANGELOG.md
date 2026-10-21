# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-20

### Added

- `README.md` with the overview, when to use and when not to use (linking the hosted versus prompt agent decision), an at-a-glance architecture diagram, a pattern selection matrix, key design decisions, Domain Packs, getting started steps for the simulator, a file map and related assets.
- `manifest.yaml` with the asset metadata, pillars, Azure services, tags and related assets.
- `docs/architecture.md` covering context and scope, the logical architecture, component responsibilities, the request and session lifecycle (pinning, readiness, heartbeat, expiry, cleanup, retries and idempotency), the identity model, networking on top of the secured Foundry landing zone, the build and release pipeline, configuration and secrets, observability with OpenTelemetry, qualitative scaling and cost behaviour, failure modes, a production readiness checklist, Domain Packs and preview caveats.
- `docs/authentication-and-governance.md` with the identity inventory (deployer, agent runtime, caller and operator), hedged role assignments, separation of duties, caller identity flow to tools, image provenance and supply chain, governance controls and a threat model.
- `docs/evaluation-and-troubleshooting.md` with pipeline smoke tests and evaluation gates, session diagnostics, a troubleshooting runbook and an operational runbook for rotation, rollback, draining sessions and orphan cleanup.
- `docs/deployment-and-operations.md`, a step-by-step deployment guide that uses placeholder values only.
- `docs/decisions/0001-deploy-and-invoke-from-inside-the-vnet.md`, `docs/decisions/0002-agent-identity-gets-least-privilege-data-plane-roles.md`, `docs/decisions/0003-explicit-session-lifecycle-no-keep-warm.md` and `docs/decisions/0004-cache-tokens-per-audience.md`.
- `infra/main.bicep`, an illustrative and undeployed template for the release identity and its federated credential, the session state containers in Azure Cosmos DB and the least-privilege role assignments for the release, image pull, runtime and caller identities, with `infra/README.md` describing the full set of additions on top of the landing zone.
- `src/Dockerfile` and `src/app_skeleton.py`, an illustrative non-root container layout with configuration loading, a per-audience token cache, OpenTelemetry setup and graceful shutdown, with `src/README.md`.
- `samples/hosted_agent_ops_sim.py`, a standard-library-only simulator with `--self-test` and `--demo` modes covering network gating, data-plane authorization, token caching, the session state machine, idempotent retries, the release pipeline with rollback, correlated spans, orphan sweeps, keep-warm detection and a gated production adapter stub, with `samples/README.md`.
