# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-20

### Added

- `README.md` with the core thesis, when to use and when not to use, architecture, decision flowchart, variant selection matrix, key design decisions, Domain Packs and getting started steps.
- `docs/architecture.md` covering what MCP clients expect, where enterprise identity providers differ, the challenge, protected resource metadata (RFC 9728), the authorization server metadata shim (RFC 8414), the client registration shim (RFC 7591), the authorize and token façades with resource-to-scope mapping (RFC 8707), token validation, variants A to D, identity populations, client compatibility types, wire contracts, failure modes, anti-patterns and a production checklist.
- `docs/authentication-and-governance.md` with the identity inventory, app registration baseline, gateway RBAC, configuration change control, Conditional Access, consent governance, audit fields and lifecycle.
- `docs/evaluation-and-troubleshooting.md` with the scenario catalog, client and variant matrix, live probe checklist, release gates, troubleshooting table and diagnostics queries.
- `docs/decisions/0001-front-door-shims-at-the-gateway.md` and `docs/decisions/0002-prefer-preregistered-clients-over-dcr-shim.md`.
- `docs/policies/` with illustrative API Management policies for the challenge and validation, protected resource metadata, metadata shim, registration shim, and authorize and token façades.
- `samples/oauth_front_door_sim.py`, a standard-library-only simulator with `--self-test`, `--emit` and `--demo` modes, in-memory identity provider and client fakes, and a gated production adapter stub.
