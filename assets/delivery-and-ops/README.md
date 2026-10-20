# Delivery and Operations (`delivery-and-ops`)

Playbooks, readiness frameworks, evaluation methodologies, and operational runbooks for taking agentic AI workloads from early prototype or pilot to governed, reliable production.

## When to browse this category

- You are establishing an **AI Path to Production** program across engineering, platform, and security teams.
- You need **evidence-based readiness gates** (go/no-go criteria, readiness records, six-pillar architectural reviews) before promoting an agent to production.
- You are designing an **AgentOps** lifecycle (evaluation datasets, regression gates, telemetry correlation, and incident response).

## Assets in this category

<!-- category-catalog:start -->
| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Agent Path to Production](agent-path-to-production/README.md) | `playbook` | experimental | An evidence-led delivery playbook for taking an agentic application from early design or a pilot to an operable production workload, with specialist routing, readiness gates, and reusable learning. |
<!-- category-catalog:end -->

## How this category connects to technical patterns

The `agent-path-to-production` playbook organizes delivery around **six pillars**, each backed by deep technical reference architectures and solution accelerators in sibling categories:

| Path-to-Production Pillar | Primary Category & Reference Assets |
|---|---|
| **Pillar 1 — AI CoE & Governance** | [`security-and-governance/agent-security-and-governance`](../security-and-governance/agent-security-and-governance/README.md) |
| **Pillar 2 — AI Landing Zones** | [`landing-zones-and-infra/secured-foundry-landing-zone`](../landing-zones-and-infra/secured-foundry-landing-zone/README.md) |
| **Pillar 3 — Agent Ops** | [`m365-and-copilot/m365-azure-custom-agent/docs/evaluation-and-routing-quality.md`](../m365-and-copilot/m365-azure-custom-agent/docs/evaluation-and-routing-quality.md) · [`security-and-governance/agent-security-and-governance/docs/evaluation-and-release-gates.md`](../security-and-governance/agent-security-and-governance/docs/evaluation-and-release-gates.md) |
| **Pillar 4 — AI Security & Observability** | [`security-and-governance/agent-security-and-governance`](../security-and-governance/agent-security-and-governance/README.md) |
| **Pillar 5 — Solution Optimization** | [`m365-and-copilot/m365-azure-custom-agent/docs/state-caching-and-proactive-ops.md`](../m365-and-copilot/m365-azure-custom-agent/docs/state-caching-and-proactive-ops.md) |
| **Pillar 6 — Proven Patterns for Agent Architectures** | [`m365-and-copilot/m365-azure-custom-agent`](../m365-and-copilot/m365-azure-custom-agent/README.md) · [`m365-and-copilot/m365-mcp-plugin-integration`](../m365-and-copilot/m365-mcp-plugin-integration/README.md) |
