# Changelog

## 0.1.0 - 2026-10-20

- Add the Enterprise Agent Security and Tool Governance reference architecture.
- Frame agent security around four questions per tool call and four serial
  runtime gates: Foundry Toolbox allow-list, connection identity (Entra Agent ID
  or OAuth passthrough), human approval, and source-side entitlement enforcement.
- Add `docs/architecture.md` covering the four gates, defense-in-depth
  guardrails (network egress, content safety and Prompt Shields, evaluation as a
  release gate), and audit and containment (unified audit schema, traces, Entra
  agent sign-in logs, kill switch).
- Add `docs/agent365-governance-mapping.md` with the eight-stage agent governance
  lifecycle, the three Foundry agent identity archetypes, a five-pillar
  Foundry-versus-Agent 365 comparison, a capability maturity table, and data
  residency considerations.
- Add ADR `docs/decisions/0001-four-gate-tool-governance.md`.
- Add sanitized samples: a governed hosted agent (`governed_agent_example.py`)
  and KQL audit queries (`audit-queries.kql`).
