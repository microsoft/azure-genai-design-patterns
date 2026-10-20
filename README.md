# Agentic AI Design Patterns

Field-proven design patterns, reference architectures and solution accelerators for building agentic AI systems on Azure and Microsoft Foundry.

Every asset in this repository is reviewed by a curator before it is merged, follows the same folder layout, and carries a machine-readable `manifest.yaml`, so you can browse it here or discover it through search tools.

## Catalog

<!-- catalog:start -->
### [Delivery and Operations](assets/delivery-and-ops/README.md) (`delivery-and-ops`)

Playbooks, readiness gates, evaluation frameworks, and AgentOps operating models.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Agent Path to Production](assets/delivery-and-ops/agent-path-to-production/README.md) | `playbook` | experimental | An evidence-led delivery playbook for taking an agentic application from early design or a pilot to an operable production workload, with specialist routing, readiness gates, and reusable learning. |

### [Microsoft 365 and Copilot Integration](assets/m365-and-copilot/README.md) (`m365-and-copilot`)

Declarative agents, custom engine agents, MCP plug-ins, MCP Apps (`ui://`), Teams, and Cowork surfaces.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Microsoft 365 Integration with Azure Custom Engine Agent](assets/m365-and-copilot/m365-azure-custom-agent/README.md) | `reference-architecture` | preview | Reference architecture for publishing an Azure-hosted custom engine agent (Microsoft Foundry Hosted Agent or Azure Container Apps) to Microsoft Teams and Microsoft 365 Copilot, composed with declarative agents and Cowork over one governed MCP tool layer with OBO identity, durable state, and gated actions. |
| [Microsoft 365 Copilot Integration with MCP via Plug-ins](assets/m365-and-copilot/m365-mcp-plugin-integration/README.md) | `design-pattern` | preview | Integrate one or more remote Model Context Protocol (MCP) servers into Microsoft 365 Copilot using declarative-agent plug-ins, progressive agent skills, and OAuth or Microsoft Entra SSO, with clear guidance on tool discovery modes, action confirmation, and MCP Apps. |

### [Security and Governance](assets/security-and-governance/README.md) (`security-and-governance`)

Defense-in-depth tool governance, Entra Agent ID, Foundry Toolbox, guardrails, Purview, and Sentinel audit.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Enterprise Agent Security and Tool Governance](assets/security-and-governance/agent-security-and-governance/README.md) | `reference-architecture` | preview | A defense-in-depth reference architecture for securing and governing enterprise AI agents at the tool call, using Microsoft Foundry, Entra Agent ID, Foundry Toolbox, and Microsoft Agent 365 so identity, tool reach, approval, and data entitlements are enforced by the platform and the source, not the prompt. |

### [Landing Zones and Infrastructure](assets/landing-zones-and-infra/README.md) (`landing-zones-and-infra`)

Private Microsoft Foundry landing zones, network isolation, Bicep/Terraform IaC, and multi-stage CI/CD.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Secured Microsoft Foundry Landing Zone for Enterprise Agents](assets/landing-zones-and-infra/secured-foundry-landing-zone/README.md) | `solution-accelerator` | preview | A two-stage Microsoft Foundry landing zone and Bicep reference setup that starts with an authenticated functional validation profile and progresses to a network-isolated, private-by-default enterprise foundation for Prompt and Hosted Agents that call private MCP tools. |
<!-- catalog:end -->

The catalog above is generated from each asset's `manifest.yaml` by `python tools/assets.py catalog`. Do not edit it by hand.

## Asset kinds

| Kind | What it is |
|------|------------|
| `design-pattern` | A reusable approach to a recurring agentic design problem, with trade-offs and when (not) to use it. |
| `reference-architecture` | An end-to-end architecture for a scenario, with components, data flows, identity, security and operations. |
| `solution-accelerator` | Deployable code and infrastructure that implements a pattern or architecture. |
| `playbook` | A step-by-step delivery or adoption guide. |
| `sample` | A small, focused code sample that demonstrates one technique. |

## Repository layout

```text
assets/
  README.md                       category directory and decision flowchart
  <category>/                     architectural category folder (6 standard categories)
    README.md                     category decision guide and auto-generated category catalog
    <asset-id>/                   one folder per asset; the folder name is the stable asset id
      manifest.yaml               metadata: category, kind, summary, tags, owners, version, maturity
      README.md                   overview, Domain Pack map, when to use, architecture, getting started
      CHANGELOG.md                what changed in each version
      docs/                       architecture deep dive, identity/auth, evaluation/troubleshooting, ADRs
      samples/                    runnable reference implementations with offline --self-test (optional)
      infra/                      infrastructure as code + reference CI/CD pipelines (optional)
      src/                        source code (optional)
docs/
  contribution-guide.md           field-to-pattern generalization standard, depth bar, and sanitization rules
templates/
  asset/                          standardized starting point for `python tools/assets.py new`
  category/                       starting point for a category README decision guide
schema/
  manifest.schema.json            JSON schema for manifest.yaml
tools/
  assets.py                       validation, catalog generation, and new-asset scaffolding
```

Assets are grouped into six stable architectural categories under [`assets/`](assets/README.md):
- [`delivery-and-ops`](assets/delivery-and-ops/README.md) — Delivery playbooks, readiness gates, and AgentOps lifecycles
- [`m365-and-copilot`](assets/m365-and-copilot/README.md) — Microsoft 365 Copilot, Teams, Declarative Agents, Custom Engine Agents, and Cowork
- [`security-and-governance`](assets/security-and-governance/README.md) — Agent identity, four-gate tool governance, guardrails, Purview, and Sentinel
- [`landing-zones-and-infra`](assets/landing-zones-and-infra/README.md) — Private Microsoft Foundry landing zones, Bicep/Terraform IaC, and CI/CD
- [`orchestration-and-workflows`](assets/orchestration-and-workflows/README.md) — Multi-agent routers, handoff contracts, and durable state/caching
- [`data-and-tools`](assets/data-and-tools/README.md) — Governed MCP servers, OBO data connectors, and action safety gates

## Using an asset

Start with the category's `README.md` to compare sibling patterns, then open the asset's `README.md`. Link to a specific version with a tag or commit permalink rather than `main`, because assets evolve.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [Contribution & Pattern Standardization Guide](docs/contribution-guide.md). To scaffold a new asset:

```bash
python tools/assets.py new --category <category> --id <asset-id> --kind <kind> --title "<Title>"
python tools/assets.py validate
python tools/assets.py catalog
```

A curator reviews every contribution. Everything in this repository is public: do not commit customer names, customer data, internal links, unreleased product information, or secrets.

## Earlier content

The 2023–2024 content of this repository (foundational concepts, the original design patterns, reference architecture and accelerators) is preserved at commit [`a4c1ee8`](https://github.com/microsoft/agenticai-design-patterns/tree/a4c1ee891c93a2fb2995dbd10f9ced73795d6f64).

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is subject to those third parties' policies.
