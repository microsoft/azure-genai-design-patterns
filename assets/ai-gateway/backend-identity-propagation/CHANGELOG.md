# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-20

### Added

- `README.md` with the core thesis, when to use and when not to use, architecture, option selection flowchart and matrix, key design decisions, Domain Packs and getting started steps.
- `docs/architecture.md` covering invariants, forces, components, Option A (on-behalf-of token exchange), Option B (signed user assertion with an identity link over mTLS), Option C (transitional per-tenant credential bridge), app-only calls, agent identity, the decision matrix, wire contracts, API Management implementation notes, caching rules, revocation and offboarding, the audit event, Domain Packs, failure modes, anti-patterns and a production checklist.
- `docs/authentication-and-governance.md` with the identity inventory, role assignments, Key Vault layout, key and secret rotation, identity link governance, consent and Conditional Access, error classification, audit fields and two-user isolation tests.
- `docs/evaluation-and-troubleshooting.md` with the scenario catalog mapped to the simulator, negative tests, release gates, a troubleshooting table and diagnostics queries.
- `docs/decisions/0001-never-pass-through-client-tokens.md` and `docs/decisions/0002-signed-assertion-over-shared-api-keys.md`.
- `docs/policies/` with illustrative API Management policies for on-behalf-of exchange with per-user caching, signed assertion minting, and the per-tenant credential bridge.
- `samples/identity_propagation_sim.py`, a standard-library-only simulator with `--self-test` and `--demo` modes, in-memory identity provider, identity link, secret store and backend fakes, and a gated production adapter stub.
