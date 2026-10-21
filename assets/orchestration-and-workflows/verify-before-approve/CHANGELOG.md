# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-20

### Added

- Initial preview release of the verify-before-approve design pattern: deterministic verification that is independent of the generator, a bounded correction loop, an explicit unverified-preview state, and human approval bound to the artifact digest and verification receipt.
- `README.md` with the core thesis, a pipeline diagram, a risk-tier gate summary, five Domain Packs, guidance on when to use and when not to use the pattern, a quickstart for the simulator, and related assets.
- `docs/architecture.md` covering the problem and forces, terminology (verification, validation and approval), the six responsibilities, the state machine with its invariants, runtime sequence diagrams, a taxonomy of checks, the bounded correction loop, the decision matrix and risk tiers, illustrative wire contracts, the approval card specification, gate rules, implementation notes for Azure, the relationship to the governed agent harness, Domain Packs, failure modes, anti-patterns and a production checklist.
- `docs/authentication-and-governance.md` covering the identity inventory, separation of duties, approver authentication, authorization by risk tier, the audit trail, non-repudiation, a threat model, data handling on the approval surface and the governance operating model.
- `docs/evaluation-and-troubleshooting.md` covering mutation testing of the verifier, false accepts versus false rejects, evaluating the correction loop and the approval experience, a refusal-code and symptom troubleshooting guide, and release gates for verifier changes.
- ADR 0001: verification is independent of the generator.
- ADR 0002: approval binds to the artifact digest and the verification receipt.
- ADR 0003: unverified preview is a first-class, terminal state.
- `samples/verify_approve_sim.py`, a single-file, stdlib-only simulator with `typing.Protocol` seams, eight deterministic checks, an advisory judge stub, a bounded correction loop, an enforced state machine, digest-bound receipts and decisions, single-use approval nonces, risk-tier gates, a gated approval-channel adapter, a 16-mutation verifier test table, a readable trace and a 21-scenario `--self-test`.
- `samples/README.md` describing how to run, read and extend the simulator.
