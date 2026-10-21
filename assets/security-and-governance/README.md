# Security and Governance (`security-and-governance`)

Reference architectures, threat models, entitlement enforcement patterns, and observability queries for governing enterprise AI agents and their tool calls across Microsoft Foundry, Microsoft Entra Agent ID, Microsoft Agent 365, Microsoft Purview, and Microsoft Sentinel.

## Core design principle

> **Never use a prompt instruction as a security boundary.** Enforce tool reach in the catalog (Gate 1), caller identity in Entra ID (Gate 2), human approval and tenant/role entitlements in the MCP server (Gate 3), and indirect prompt-injection shields plus immutable SIEM audit at the platform boundary (Gate 4).

## Assets in this category

<!-- category-catalog:start -->
| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Enterprise Agent Security and Tool Governance](agent-security-and-governance/README.md) | `reference-architecture` | preview | A defense-in-depth reference architecture for securing and governing enterprise AI agents at the tool call, using Microsoft Foundry, Entra Agent ID, Foundry Toolbox, and Microsoft Agent 365 so identity, tool reach, approval, and data entitlements are enforced by the platform and the source, not the prompt. |
<!-- category-catalog:end -->

## Complementary assets in other categories

- **Network isolation & private endpoints:** [`landing-zones-and-infra/secured-foundry-landing-zone`](../landing-zones-and-infra/secured-foundry-landing-zone/README.md)
- **Server-side `R`/`W1`–`W4` action gate & OBO row-level isolation:** [`m365-and-copilot/m365-azure-custom-agent`](../m365-and-copilot/m365-azure-custom-agent/README.md)
- **Production-readiness gates (Pillar 1 & Pillar 4):** [`agentops-and-readiness/agent-production-readiness`](../agentops-and-readiness/agent-production-readiness/README.md)
