# Changelog

All notable changes to this asset are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this asset uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-21

### Added

- Initial preview release of the **Request Context Is Not Authorization** design pattern.
- `README.md` with the core thesis, implementation map, Domain Packs, a where-to-enforce table, key design decisions, getting started, and related assets.
- `manifest.yaml` with the asset metadata, pillars, Azure services, tags and related assets.
- `docs/architecture.md`: the threat model with seven threats, taint classes and a source classification table, components, Mermaid component and sequence diagrams, context and identity for Foundry hosted and prompt agents, a decision matrix, wire contracts (token claims, header contract, PDP request and response, audit event), Domain Packs, failure modes, anti-patterns, a migration path from header-trusting agents, and a production checklist.
- `docs/authentication-and-governance.md`: identity inventory, the choice between delegated user, agent identity and broker, token validation rules, roles, policy as code with a reason-code catalog, audit, separation of duties, and threat-to-control mapping.
- `docs/evaluation-and-troubleshooting.md`: the adversarial test matrix, decision-correctness metrics, red-team prompts, CI gating, and a troubleshooting table.
- ADR 0001 (derive authorization from verified identity only) and ADR 0002 (re-authorize at the resource boundary).
- `samples/request_context_guard.py`: a standard-library reference with an identity verifier seam, a tainted context bag, a deny-by-default PDP with reason codes, a call-time tool PEP over a live entitlement store, a JSON-lines audit log, 11 adversarial self-test scenarios and three domain packs.
