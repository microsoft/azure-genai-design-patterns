# Assets & Category Taxonomy

Assets in this repository are organized by architectural category under `assets/<category>/<asset-id>/`. Seven categories are published today and one more is planned. Each published category folder has its own `README.md` with a decision guide comparing the patterns in that domain, while each asset folder (`<asset-id>/`) carries a machine-readable `manifest.yaml` validated by [`scripts/assets.py`](../scripts/assets.py).

Categories describe *what kind of architecture* an asset covers. Pillars describe *which path-to-production capability* it supports. See [Path to production](../docs/path-to-production.md) for the pillar map.

## Category Directory

| Category Folder | Scope & Typical Assets | Decision Guide |
|---|---|---|
| [`delivery-and-ops/`](delivery-and-ops/README.md) | End-to-end delivery playbooks, readiness gates, AgentOps lifecycles, evaluation harnesses, and operating models | [Browse `delivery-and-ops`](delivery-and-ops/README.md) |
| [`m365-and-copilot/`](m365-and-copilot/README.md) | Microsoft 365 Copilot, Microsoft Teams, Declarative Agents, Custom Engine Agents, MCP plug-ins, MCP Apps (`ui://`), and Cowork surfaces | [Browse `m365-and-copilot`](m365-and-copilot/README.md) |
| [`security-and-governance/`](security-and-governance/README.md) | Defense-in-depth agent security, Entra Agent ID, Foundry Toolbox allow-lists, runtime guardrails, Purview DSPM for AI, and Sentinel audit | [Browse `security-and-governance`](security-and-governance/README.md) |
| [`landing-zones-and-infra/`](landing-zones-and-infra/README.md) | Network-isolated Microsoft Foundry landing zones, Private Link/DNS topologies, Bicep/Terraform IaC, and multi-stage CI/CD pipelines | [Browse `landing-zones-and-infra`](landing-zones-and-infra/README.md) |
| [`agent-runtime/`](agent-runtime/README.md) | Where and how the agent process runs: prompt vs hosted agents, production hosted agents on Foundry, private runtime networking, sessions, and sandboxed code execution | [Browse `agent-runtime`](agent-runtime/README.md) |
| [`ai-gateway/`](ai-gateway/README.md) | API Management as the control plane for model, MCP tool, and agent traffic: multi-tenant MCP gateways, OAuth front doors, backend identity propagation, metering, and quotas | [Browse `ai-gateway`](ai-gateway/README.md) |
| [`orchestration-and-workflows/`](orchestration-and-workflows/README.md) | Governed agent harnesses, verify-before-approve gates, multi-agent planner/specialist routers, structured handoffs, durable state, and deterministic vs. dynamic workflows | [Browse `orchestration-and-workflows`](orchestration-and-workflows/README.md) |
| `data-and-tools/` *(planned)* | Governed Model Context Protocol (MCP) server architectures, On-Behalf-Of (OBO) data connectors, Foundry IQ / RAG grounding, and action safety gates | Planned — created with the first asset; see [where to look today](../docs/path-to-production.md#planned-categories-where-to-look-today) |

## How to choose a category for a new contribution

```mermaid
flowchart TD
    A["New reusable asset"] --> B{"Primary architectural layer?"}
    B -- "Delivery lifecycle, readiness gates, or AgentOps methodology" --> C1["assets/delivery-and-ops/"]
    B -- "M365 Copilot, Teams, Declarative Agent, or Custom Engine surface" --> C2["assets/m365-and-copilot/"]
    B -- "Identity, tool governance, guardrails, threat model, or SIEM audit" --> C3["assets/security-and-governance/"]
    B -- "Private networking, Foundry landing zone, Bicep/Terraform, or CI/CD" --> C4["assets/landing-zones-and-infra/"]
    B -- "Where/how the agent process runs: prompt vs hosted, sessions, sandboxes" --> C7["assets/agent-runtime/"]
    B -- "APIM control plane for model, MCP tool, or agent traffic" --> C8["assets/ai-gateway/"]
    B -- "Agent harness, verification gates, multi-agent routing, or orchestration control flow" --> C5["assets/orchestration-and-workflows/"]
    B -- "Standalone MCP server, RAG grounding, or data-layer OBO connector" --> C6["assets/data-and-tools/ (planned)"]
```

To scaffold a new asset in the right category, run:

```bash
python scripts/assets.py new --category <category> --id <asset-id> --kind <kind> --title "<Title>" [--with-infra] [--with-src]
```

If the category is still planned, `new` creates the category folder and its `README.md` from [`templates/category/`](../templates/category/README.md). See [`scripts/README.md`](../scripts/README.md) for every command.

See [CONTRIBUTING.md](../CONTRIBUTING.md) and the [Contribution & Standardization Guide](../docs/contribution-guide.md) for the full authoring and review bar.

