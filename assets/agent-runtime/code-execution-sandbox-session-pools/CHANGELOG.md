# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-20

### Added

- `README.md` with the core thesis, an architecture and implementation map, Domain Packs, when to use and when not to use, an architecture diagram, a session lifecycle diagram, a pattern selection matrix, key design decisions, getting started steps for the simulator, a file map and related assets.
- `manifest.yaml` with the asset metadata, pillars, Azure services, tags and related assets.
- `docs/architecture.md` covering context, forces, a threat model for model-generated code, the broker and session pool solution, components, the request and response flow, a decision matrix across five execution options, wire contracts (session identifier derivation, execute, files and an error taxonomy), session lifecycle and cleanup, pool selection and the MCP gap on custom-container pools, egress policy and allowlist variants, output handling, prompt and hosted agent integration, verify-before-approve integration, Domain Packs, anti-patterns, a production checklist, observability and preview caveats.
- `docs/authentication-and-governance.md` with the identity inventory, a hedged RBAC table, least privilege, why secrets never enter a session, per-tenant pools versus a shared pool with per-tenant identifiers, derivation key management, data residency, audit, approval for side effects and policy-as-code ideas.
- `docs/evaluation-and-troubleshooting.md` with a red-team test catalogue, functional evaluations that require execution evidence, a troubleshooting table and a map from simulator scenarios to tests.
- `docs/decisions/0001-never-execute-generated-code-in-process.md`, `docs/decisions/0002-one-session-per-user-conversation-egress-off-by-default.md` and `docs/decisions/0003-built-in-pool-for-mcp-custom-container-for-packages.md`.
- `samples/sandbox_session_sim.py`, a standard-library-only simulator with `--self-test` (34 scenarios) and `--demo` modes covering identifier derivation, tenant and user isolation, caller authorization, egress default and allowlist, metadata address blocking, file path validation, output truncation, redaction and quarantine, step, process and memory budgets, cooldown and lifetime expiry, capacity, pool selection and the MCP gap, warm-setting drift, in-process refusal, secret pre-screening, approval routing, audit and a gated live adapter stub, with `samples/README.md`.
