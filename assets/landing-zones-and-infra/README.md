# Landing Zones and Infrastructure (`landing-zones-and-infra`)

Solution accelerators, Bicep/Terraform infrastructure-as-code templates, private DNS/VNet topologies, and multi-stage CI/CD pipelines for hosting enterprise AI agents on Microsoft Foundry and Azure.

## When to browse this category

- You need to deploy a **private-by-default Microsoft Foundry landing zone** (`publicNetworkAccess: Disabled`) with Private Endpoints, split-horizon Private DNS, capability-host subnet delegation, and private Container Apps MCP servers.
- You want a **two-stage progression** that validates functional agent, identity, and Toolbox contracts in Stage 1 before locking down network paths in Stage 2.
- You need **least-privilege separation of duties** across platform bootstrap, foundation infrastructure, and application team agent release pipelines.

## Assets in this category

<!-- category-catalog:start -->
| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Secured Microsoft Foundry Landing Zone for Enterprise Agents](secured-foundry-landing-zone/README.md) | `solution-accelerator` | preview | A two-stage Microsoft Foundry landing zone and Bicep reference setup that starts with an authenticated functional validation profile and progresses to a network-isolated, private-by-default enterprise foundation for Prompt and Hosted Agents that call private MCP tools. |
<!-- category-catalog:end -->

## Complementary assets in other categories

- **Runtime tool governance & Entra Agent ID controls on top of the landing zone:** [`security-and-governance/agent-security-and-governance`](../security-and-governance/agent-security-and-governance/README.md)
- **Publishing Hosted Agents to Microsoft Teams & M365 Copilot:** [`m365-and-copilot/m365-azure-custom-agent`](../m365-and-copilot/m365-azure-custom-agent/README.md)
- **AI Landing Zone readiness checklist (Pillar 2):** [`agentops-and-readiness/agent-production-readiness/docs/pillars/02-ai-landing-zones.md`](../agentops-and-readiness/agent-production-readiness/docs/pillars/02-ai-landing-zones.md)
