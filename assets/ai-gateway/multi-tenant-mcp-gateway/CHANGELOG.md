# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-20

### Added

- Initial preview release of the reference architecture for a multi-tenant MCP gateway on Azure API Management, illustrated with a fictional multi-tenant SaaS provider, Contoso ERP.
- `README.md` with the core thesis ("the tenant comes from the token, entitlement is enforced on every call, and the gateway chooses the backend credential and cell"), an architecture diagram, a decision flowchart, a pattern selection matrix, key design decisions, and five Domain Packs.
- `docs/architecture.md` covering the problem, forces, reference architecture, the network path through an optional firewall or NVA, Application Gateway WAF_v2, and internal API Management, why tenant identity must never be keyed on source IP, WAF considerations for Streamable HTTP and server-sent events, the ordered per-call policy pipeline with policy excerpts, tool-level authorization with the `tools/call` re-check, backend credential options, API Management MCP capabilities and limits, the API Center catalog, topology options with a decision matrix, observability, wire contracts, failure modes, anti-patterns, a production checklist, and qualitative cost considerations.
- `docs/authentication-and-governance.md` covering the identity inventory, RBAC baseline, secrets, tenant onboarding and offboarding, entitlement governance, policy as code, audit, and negative tests.
- `docs/evaluation-and-troubleshooting.md` covering evaluation scenarios, a tenant-by-method test matrix, release gates, and a symptom-to-fix troubleshooting table.
- ADR 0001: derive tenant identity only from a validated token claim.
- ADR 0002: re-check tool entitlement on every `tools/call`, not only when filtering `tools/list`.
- `samples/gateway_pipeline_sim.py`, a stdlib-only simulation of the per-call pipeline with adapter seams for the tenant directory and audit sink, gated production stubs, a `--demo` mode, and an offline `--self-test` covering thirteen scenarios.
- `samples/README.md` describing how to run and extend the simulator.
- `infra/main.bicep`, an illustrative, not production-hardened skeleton for API Management in a virtual network, Application Gateway WAF_v2, API Center, Key Vault, Event Hubs, Log Analytics, Application Insights, named values, backends, and policy fragments.
- `infra/policies/` with illustrative global, tenant pipeline, tenant directory, and audit policy XML, and `infra/README.md` describing parameters, execution order, deployment, and known gaps.
