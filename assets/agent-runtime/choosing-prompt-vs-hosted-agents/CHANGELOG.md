# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-20

### Added

- Initial preview release of the decision pattern for choosing between Foundry prompt agents, hosted agents, and mixed designs.
- `README.md` with the core thesis ("default to prompt, escalate to hosted for named control, mix per scenario"), an architecture diagram, a decision flowchart, a pattern selection matrix, and five Domain Packs.
- `docs/architecture.md` covering forces, the responsibility split, reference architecture, decision matrix, runtime sequence diagrams (prompt, hosted, and both mixed shapes), illustrative wire contracts, hosted protocol selection, state and session model, identity, network placement, observability, versioning, warmth, cost and operations, failure modes, anti-patterns, migration paths, and a production checklist.
- `docs/authentication-and-governance.md` covering the identities each runtime uses, RBAC, secrets, network and egress control, guardrails, audit, and isolation negative tests.
- `docs/evaluation-and-troubleshooting.md` covering evaluation scenarios, a per-runtime test matrix, migration parity evaluations, release gates, and a symptom-to-fix troubleshooting table.
- ADR 0001: default to prompt agents and escalate to hosted agents only for a named, required control.
- `samples/runtime_selector.py`, a stdlib-only, rubric-based runtime selector with adapter seams for profile sources and result sinks, plus an offline `--self-test` that covers six scenarios and four input and adapter checks.
- `samples/example-profiles.json` with three illustrative workload profiles and `samples/README.md` describing how to run and extend the selector.
