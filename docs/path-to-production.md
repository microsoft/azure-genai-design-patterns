# Path to production: navigating this repository

Most agentic applications stall between a convincing demonstration and an operable production workload. This repository is organized so that a team, or the specialists helping it, can find the blocker, open the pattern that removes it, and record the evidence that the blocker is closed.

The method is the [Agent Path to Production](../assets/delivery-and-ops/agent-path-to-production/README.md) playbook:

1. **Frame and triage** the workload with the [delivery runbook](../assets/delivery-and-ops/agent-path-to-production/docs/runbook.md).
2. **Locate the principal blocker** in one of the six [pillar chapters](../assets/delivery-and-ops/agent-path-to-production/docs/pillars/README.md).
3. **Apply the smallest pattern** that removes it, using the assets mapped below.
4. **Record the evidence and the decision** in the [readiness record](../assets/delivery-and-ops/agent-path-to-production/docs/readiness-record.md).
5. **Harvest what was reusable** back into this repository as a new or updated asset (see the [contribution guide](contribution-guide.md)).

The pillars are a diagnostic map, not six mandatory projects or a maturity score. Start with the pillar that holds the blocker and involve others only where the evidence shows a dependency.

## Pillars and where to look

The [root README](../README.md#start-here-path-to-production) has a generated pillar map built from each asset's `pillars:` field. The table below adds hand-picked deep links into specific documents and samples.

| Pillar | Questions it answers | Start with |
|--------|----------------------|------------|
| [1. AI CoE & Governance](../assets/delivery-and-ops/agent-path-to-production/docs/pillars/01-ai-coe-and-governance.md) | Who owns the agent, how it is admitted and graduated, how it is inventoried and paid for. | [Agent security and governance](../assets/security-and-governance/agent-security-and-governance/README.md), [Agent 365 governance mapping](../assets/security-and-governance/agent-security-and-governance/docs/agent365-governance-mapping.md) |
| [2. AI Landing Zones](../assets/delivery-and-ops/agent-path-to-production/docs/pillars/02-ai-landing-zones.md) | Where it runs, how it is isolated, and how identity, DNS and policy are applied per environment. | [Secured Microsoft Foundry landing zone](../assets/landing-zones-and-infra/secured-foundry-landing-zone/README.md) and its [Bicep templates](../assets/landing-zones-and-infra/secured-foundry-landing-zone/infra/foundation.bicep) |
| [3. Agent Ops](../assets/delivery-and-ops/agent-path-to-production/docs/pillars/03-agent-ops.md) | How changes are evaluated, versioned, traced, released and rolled back. | [Evaluation and release gates](../assets/security-and-governance/agent-security-and-governance/docs/evaluation-and-release-gates.md), [evaluation and routing quality](../assets/m365-and-copilot/m365-azure-custom-agent/docs/evaluation-and-routing-quality.md), [kill switch and smoke test](../assets/security-and-governance/agent-security-and-governance/samples/kill_switch_and_smoke_test.py) |
| [4. AI Security & Observability](../assets/delivery-and-ops/agent-path-to-production/docs/pillars/04-ai-security-and-observability.md) | Which tools the agent may call, as whom, with what data, and how every call is audited. | [Threat model and control matrix](../assets/security-and-governance/agent-security-and-governance/docs/threat-model-and-control-matrix.md), [governed MCP server](../assets/security-and-governance/agent-security-and-governance/samples/governed_mcp_server.py), [authentication and identity](../assets/m365-and-copilot/m365-azure-custom-agent/docs/authentication-and-identity.md) |
| [5. Solution Optimization](../assets/delivery-and-ops/agent-path-to-production/docs/pillars/05-solution-optimization.md) | Whether latency, cost and retrieval quality hold at production volume. | [State, caching and proactive operations](../assets/m365-and-copilot/m365-azure-custom-agent/docs/state-caching-and-proactive-ops.md), [Cosmos DB state and cache sample](../assets/m365-and-copilot/m365-azure-custom-agent/samples/cosmos_state_and_cache.py) |
| [6. Proven Patterns for Agent Architectures](../assets/delivery-and-ops/agent-path-to-production/docs/pillars/06-proven-patterns-for-agent-architectures.md) | Which orchestration, front door and interoperability shape fits the workload. | [Microsoft 365 integration with an Azure custom engine agent](../assets/m365-and-copilot/m365-azure-custom-agent/README.md), [MCP via Microsoft 365 Copilot plug-ins](../assets/m365-and-copilot/m365-mcp-plugin-integration/README.md) |

## Planned categories: where to look today

Two categories are planned but have no standalone asset yet. Their folders are created automatically with the first asset (`python scripts/assets.py new --category <category> ...`). Until then, the relevant material lives inside existing assets.

### Orchestration and workflows (`orchestration-and-workflows`)

| Topic | Current location |
|-------|------------------|
| Planner-and-specialist routing | [Architecture, section 3](../assets/m365-and-copilot/m365-azure-custom-agent/docs/architecture.md#3-planner-and-specialist-orchestration) and the [router sample](../assets/m365-and-copilot/m365-azure-custom-agent/samples/planner_specialist_router.py) |
| Durable state and caching | [State, caching and proactive operations](../assets/m365-and-copilot/m365-azure-custom-agent/docs/state-caching-and-proactive-ops.md) and the [Cosmos DB sample](../assets/m365-and-copilot/m365-azure-custom-agent/samples/cosmos_state_and_cache.py) |
| Surface and orchestration split | [ADR 0001](../assets/m365-and-copilot/m365-azure-custom-agent/docs/decisions/0001-surface-and-orchestration-split.md) |
| Multiple MCP servers behind one agent | [MCP plug-in architecture](../assets/m365-and-copilot/m365-mcp-plugin-integration/docs/architecture.md) |

### Data and tools (`data-and-tools`)

| Topic | Current location |
|-------|------------------|
| Reference MCP server | [Reference MCP server sample](../assets/m365-and-copilot/m365-mcp-plugin-integration/samples/reference_mcp_server.py) |
| Governed MCP server with entitlement checks | [Governed MCP server sample](../assets/security-and-governance/agent-security-and-governance/samples/governed_mcp_server.py) |
| On-Behalf-Of (OBO) tool calls | [OBO MCP client sample](../assets/m365-and-copilot/m365-azure-custom-agent/samples/obo_mcp_client.py) and [authentication paths](../assets/m365-and-copilot/m365-mcp-plugin-integration/docs/authentication-paths.md) |
| Action safety gates for side-effecting tools | [Side-effect gate sample](../assets/m365-and-copilot/m365-azure-custom-agent/samples/side_effect_gate.py) and [architecture, section 6](../assets/m365-and-copilot/m365-azure-custom-agent/docs/architecture.md#6-side-effect-classification-and-action-safety-gates) |

## Using this in a path-to-production review

- **Before the review:** ask the workload team to fill the "Outcome and routing" section of the [readiness record](../assets/delivery-and-ops/agent-path-to-production/docs/readiness-record.md).
- **During the review:** use the pillar chapter's diagnostic questions, then open the mapped asset to agree on the target design and the evidence that will close the gap.
- **After the review:** record the decision in the readiness record. If the work produced something reusable, generalize it and contribute it as an asset tagged with the pillars it serves.
