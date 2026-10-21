# AgentOps and Production Readiness (`agentops-and-readiness`)

Production-readiness guides, evidence-based readiness gates, evaluation methods, and operational practices for taking agentic workloads from a prototype or pilot to governed, reliable production.

## When to browse this category

- You need to run a **production-readiness review** of an agent workload before or after a pilot.
- You need **evidence-based readiness gates** (go/no-go criteria, readiness records, a six-pillar architectural review) before promoting an agent to production.
- You are designing an **AgentOps** lifecycle: evaluation datasets, regression gates, telemetry correlation, and incident response.

## Assets in this category

<!-- category-catalog:start -->
| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Agent Production Readiness Guide](agent-production-readiness/README.md) | `playbook` | experimental | An evidence-led production-readiness guide for taking an agentic application from early design or a pilot to an operable production workload, with six readiness pillars, three technical plays, and release gates. |
<!-- category-catalog:end -->

## How this category connects to technical patterns

The [Agent Production Readiness Guide](agent-production-readiness/README.md) organizes a readiness review around **six production-readiness pillars**. Each pillar points to reference architectures and solution accelerators in sibling categories.

For the pillar definitions, the asset-to-pillar map, and the planned categories, see the [production-readiness map](../../docs/production-readiness-map.md).

| Production-readiness pillar | Primary reference assets |
|---|---|
| **Pillar 1: AI CoE & Governance** | [`security-and-governance/agent-security-and-governance`](../security-and-governance/agent-security-and-governance/README.md) |
| **Pillar 2: AI Landing Zones** | [`landing-zones-and-infra/secured-foundry-landing-zone`](../landing-zones-and-infra/secured-foundry-landing-zone/README.md) |
| **Pillar 3: Agent Ops** | [`m365-and-copilot/m365-azure-custom-agent/docs/evaluation-and-routing-quality.md`](../m365-and-copilot/m365-azure-custom-agent/docs/evaluation-and-routing-quality.md) · [`security-and-governance/agent-security-and-governance/docs/evaluation-and-release-gates.md`](../security-and-governance/agent-security-and-governance/docs/evaluation-and-release-gates.md) |
| **Pillar 4: AI Security & Observability** | [`security-and-governance/agent-security-and-governance`](../security-and-governance/agent-security-and-governance/README.md) |
| **Pillar 5: Solution Optimization** | [`m365-and-copilot/m365-azure-custom-agent/docs/state-caching-and-proactive-ops.md`](../m365-and-copilot/m365-azure-custom-agent/docs/state-caching-and-proactive-ops.md) |
| **Pillar 6: Proven Patterns for Agent Architectures** | [`m365-and-copilot/m365-azure-custom-agent`](../m365-and-copilot/m365-azure-custom-agent/README.md) · [`m365-and-copilot/m365-mcp-plugin-integration`](../m365-and-copilot/m365-mcp-plugin-integration/README.md) |
