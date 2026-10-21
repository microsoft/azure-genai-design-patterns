# Changelog

All notable changes to this asset are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this asset uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-21

### Added

- Initial preview release of the **Entitlement-Aware Tool Catalog** design pattern.
- `README.md` with the implementation map, Domain Packs, when to use and when not to use, the architecture overview, key design decisions, getting started, a file map, related assets, the production-readiness pillar mapping, security and responsible AI notes, and limitations.
- `manifest.yaml` with the asset metadata, pillars, Azure services, tags and related assets.
- `docs/architecture.md`: problem and forces, scope against sibling assets, components and a Mermaid component diagram, the entitlement decision and principal classes, filtered views and schema minimization, the entitlement and view caches, freshness, staleness and revocation, the versioned catalog and `tools/list_changed` notifications, bound pagination cursors, request sequences, hosting on Azure, a decision matrix, wire contracts (headers, token claims, registry response, `initialize` and `tools/list` results, responses by outcome, audit events), Domain Packs, failure modes, anti-patterns and a production checklist.
- `docs/authentication-and-governance.md`: principles, entitlement sources, identity inventory, token validation and claims, Azure role assignments, secrets and keys, catalog governance, policy per domain, disclosure policy, audit, and prompt injection and responsible AI.
- `docs/evaluation-and-troubleshooting.md`: what to evaluate, test layers, the scenario matrix, running and extending the reference suite, the Domain Pack matrix, a mutation check, metrics and observability, red-team prompts, CI gating, a troubleshooting table and where to look.
- ADR 0001 (filter the list and enforce the call from one entitlement decision) and ADR 0002 (cache views by entitlement set, not by user).
- `samples/entitled_tool_catalog.py`: a standard-library reference gateway with a token validator seam, an entitlement registry with change events and outage simulation, an entitlement cache with a version floor and bounded staleness, a view cache keyed by entitlement set, schema minimization, MAC-protected cursors bound to the view, catalog lint, `tools/list_changed` notifications, a decision audit, three Domain Packs and 14 scenarios (42 checks), runnable with `--self-test` and `--demo`.
- `samples/README.md` describing how to run the sample and what each scenario proves.
