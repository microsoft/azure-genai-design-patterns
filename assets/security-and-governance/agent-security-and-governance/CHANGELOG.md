# Changelog

## 0.2.0 - 2026-10-21

- Add Gate 5, the execution boundary, for agents that run generated code.
  `docs/code-execution-governance.md` covers ten principles, author-based trust,
  execution tiers 0 to 4, trust boundaries TB7 to TB9, the code-execution threat
  catalog `CX-01` to `CX-15`, identity, network, file, output and supply-chain
  controls, approval rules, audit evidence, red-team cases `CX-T1` to `CX-T12`,
  a production checklist and residual risks.
- Add ADR `docs/decisions/0002-execution-boundary-for-generated-code.md`.
- Add `samples/execution_isolation_probe.py`, a dependency-free probe that runs
  inside a sandbox session and checks for secrets in the environment, identity
  and metadata endpoint reach, public internet egress and package allowlists.
  It has an offline `--self-test` mode.
- Add KQL queries 9 to 11 to `samples/audit-queries.kql` for sandbox egress and
  probe failures, runtime image drift and session-pool saturation.
- Add TB7 to TB9 and cross-references to the code-execution threats and red-team
  cases in `docs/threat-model-and-control-matrix.md`.
- Update the README, `docs/architecture.md` and `samples/README.md` for Gate 5,
  and link the code-execution sandbox asset as the runtime implementation.

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
