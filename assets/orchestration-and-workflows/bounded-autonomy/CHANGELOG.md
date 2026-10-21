# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-21

### Added

- Initial preview release of the bounded autonomy design pattern: autonomy profiles selected by server-side policy, harness-enforced budgets, allowed-action envelopes, plan-before-act, explicit stop and escalation conditions, fail-closed behavior and a structured policy decision record.
- `README.md` with the core thesis, an architecture and implementation map, four Domain Packs, guidance on when to use and when not to use the pattern, a component diagram, key decisions, a quickstart for the sample and related assets.
- `docs/architecture.md` covering the problem and forces, terminology, components, the four autonomy profiles, budgets and how they are metered, the allowed-action envelope and its evaluation precedence, plan-before-act, stop and escalation conditions with their resolutions, runtime state and resumability, sequence diagrams, the decision matrix, illustrative wire contracts, the mapping onto Azure services, the relationship to the governed agent harness, Domain Packs, failure modes, anti-patterns and a production checklist.
- `docs/authentication-and-governance.md` covering identity binding for profiles, approver eligibility, separation of duties, policy authoring and release, the kill switch, audit and a threat model.
- `docs/evaluation-and-troubleshooting.md` covering scenario testing of the governor, budget sizing from evaluation runs, the metrics to watch, a reason-code troubleshooting guide and release gates for policy changes.
- ADR 0001: budgets are enforced by the harness, not by the prompt.
- ADR 0002: fail closed when the policy store or the decision sink is unavailable.
- ADR 0003: the autonomy profile is selected by server-side policy, never by the prompt or the model.
- `samples/autonomy_governor.py`, a single-file, stdlib-only governor with `typing.Protocol` seams for the policy store and decision sink, production adapter stubs, four Domain Packs, a demo run, a JSON output mode and a 23-scenario `--self-test` that runs against every Domain Pack.
- `samples/README.md` describing how to run, read and extend the sample.
