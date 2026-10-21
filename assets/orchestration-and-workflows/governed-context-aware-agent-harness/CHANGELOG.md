# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-20

### Added

- `README.md` with the core thesis, when to use and when not to use, architecture at a glance, key design decisions, Domain Packs, quickstart and related assets.
- `docs/architecture.md` covering the problem, invariants, forces, components, the episode lifecycle, module contracts, run profiles and the immutable-versus-configurable control matrix, wire contracts, context engineering (progressive skill disclosure, scoped memory, budgets, compaction status), the Microsoft Agent Framework mapping, the production path on Microsoft Foundry, Domain Packs, known gaps, failure modes, anti-patterns and a production checklist.
- `docs/authentication-and-governance.md` with keyless model access, the identity inventory, separation of duties for approval, immutable controls, the audit trail, memory data handling, a threat model and governance operations.
- `docs/evaluation-and-troubleshooting.md` with the scenario catalog mapped to the simulator, qualitative evaluation metrics, release gates, a troubleshooting table and diagnostics guidance.
- `docs/decisions/0001-harness-enforces-boundaries-not-transcripts.md`, `docs/decisions/0002-progressive-skill-disclosure-over-all-loaded.md` and `docs/decisions/0003-approval-binds-to-verified-artifact-digest.md`.
- `src/README.md` with the reference module layout, skill packaging convention and Agent Framework integration notes.
- `samples/harness_sim.py`, a standard-library-only simulator of the governed harness with 17 self-test scenarios, a readable episode trace, a near-miss mode, profile selection and gated production stubs.
