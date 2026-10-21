# Production-readiness map: navigating this repository

Many agentic applications stall between a convincing demonstration and an operable production workload. This page maps the catalog to six **production-readiness pillars** so you can find the asset that addresses a specific blocker: an unclear architecture, missing evaluation evidence, an unsafe tool boundary, or an environment that is not ready for production.

The pillars are a diagnostic map, not six mandatory projects or a maturity score. Start with the pillar that holds the blocker, and involve the others only where the evidence shows a dependency.

If you want a structured review method, the [Agent Production Readiness Guide](../assets/agentops-and-readiness/agent-production-readiness/README.md) provides an optional [readiness review](../assets/agentops-and-readiness/agent-production-readiness/docs/readiness-review.md), [pillar chapters](../assets/agentops-and-readiness/agent-production-readiness/docs/pillars/README.md), and a [readiness record](../assets/agentops-and-readiness/agent-production-readiness/docs/readiness-record.md) template. You can also use the assets below directly without it.

## Pillars and where to look

The [root README](../README.md#browse-by-production-readiness-pillar) has a generated pillar map built from each asset's `pillars:` field. The table below adds hand-picked deep links into specific documents and samples.

| Pillar | Questions it answers | Start with |
|--------|----------------------|------------|
| [1. AI CoE & Governance](../assets/agentops-and-readiness/agent-production-readiness/docs/pillars/01-ai-coe-and-governance.md) | Who owns the agent, how it is admitted and graduated, how it is inventoried and paid for. | [Agent security and governance](../assets/security-and-governance/agent-security-and-governance/README.md), [Agent 365 governance mapping](../assets/security-and-governance/agent-security-and-governance/docs/agent365-governance-mapping.md) |
| [2. AI Landing Zones](../assets/agentops-and-readiness/agent-production-readiness/docs/pillars/02-ai-landing-zones.md) | Where it runs, how it is isolated, and how identity, DNS and policy are applied per environment. | [Secured Microsoft Foundry landing zone](../assets/landing-zones-and-infra/secured-foundry-landing-zone/README.md) and its [Bicep templates](../assets/landing-zones-and-infra/secured-foundry-landing-zone/infra/foundation.bicep); [production hosted agents on Foundry](../assets/agent-runtime/production-hosted-agents-on-foundry/README.md) for private runtime networking and in-network CI/CD |
| [3. Agent Ops](../assets/agentops-and-readiness/agent-production-readiness/docs/pillars/03-agent-ops.md) | How changes are evaluated, versioned, traced, released and rolled back. | [Evaluation and release gates](../assets/security-and-governance/agent-security-and-governance/docs/evaluation-and-release-gates.md), [evaluation and routing quality](../assets/m365-and-copilot/m365-azure-custom-agent/docs/evaluation-and-routing-quality.md), [kill switch and smoke test](../assets/security-and-governance/agent-security-and-governance/samples/kill_switch_and_smoke_test.py) |
| [4. AI Security & Observability](../assets/agentops-and-readiness/agent-production-readiness/docs/pillars/04-ai-security-and-observability.md) | Which tools the agent may call, as whom, with what data, and how every call is audited. | [Threat model and control matrix](../assets/security-and-governance/agent-security-and-governance/docs/threat-model-and-control-matrix.md), [governed MCP server](../assets/security-and-governance/agent-security-and-governance/samples/governed_mcp_server.py), [authentication and identity](../assets/m365-and-copilot/m365-azure-custom-agent/docs/authentication-and-identity.md), [multi-tenant MCP gateway](../assets/ai-gateway/multi-tenant-mcp-gateway/README.md), [backend identity propagation](../assets/ai-gateway/backend-identity-propagation/README.md), [sandboxed code execution](../assets/agent-runtime/code-execution-sandbox-session-pools/README.md) |
| [5. Solution Optimization](../assets/agentops-and-readiness/agent-production-readiness/docs/pillars/05-solution-optimization.md) | Whether latency, cost and retrieval quality hold at production volume. | [State, caching and proactive operations](../assets/m365-and-copilot/m365-azure-custom-agent/docs/state-caching-and-proactive-ops.md), [Cosmos DB state and cache sample](../assets/m365-and-copilot/m365-azure-custom-agent/samples/cosmos_state_and_cache.py) |
| [6. Proven Patterns for Agent Architectures](../assets/agentops-and-readiness/agent-production-readiness/docs/pillars/06-proven-patterns-for-agent-architectures.md) | Which orchestration, front door and interoperability shape fits the workload. | [Microsoft 365 integration with an Azure custom engine agent](../assets/m365-and-copilot/m365-azure-custom-agent/README.md), [MCP via Microsoft 365 Copilot plug-ins](../assets/m365-and-copilot/m365-mcp-plugin-integration/README.md), [choosing prompt vs hosted agents](../assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md), [governed context-aware agent harness](../assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md), [verify-before-approve](../assets/orchestration-and-workflows/verify-before-approve/README.md) |

## Planned categories: where to look today

One category is planned but has no standalone asset yet. Its folder is created automatically with the first asset (`python scripts/assets.py new --category <category> ...`). Until then, the relevant material lives inside existing assets. Orchestration content that predates the [`orchestration-and-workflows`](../assets/orchestration-and-workflows/README.md) category is cross-linked from that category's README.

### Data and tools (`data-and-tools`)

| Topic | Current location |
|-------|------------------|
| Reference MCP server | [Reference MCP server sample](../assets/m365-and-copilot/m365-mcp-plugin-integration/samples/reference_mcp_server.py) |
| Governed MCP server with entitlement checks | [Governed MCP server sample](../assets/security-and-governance/agent-security-and-governance/samples/governed_mcp_server.py) |
| On-Behalf-Of (OBO) tool calls | [OBO MCP client sample](../assets/m365-and-copilot/m365-azure-custom-agent/samples/obo_mcp_client.py) and [authentication paths](../assets/m365-and-copilot/m365-mcp-plugin-integration/docs/authentication-paths.md) |
| Action safety gates for side-effecting tools | [Side-effect gate sample](../assets/m365-and-copilot/m365-azure-custom-agent/samples/side_effect_gate.py) and [architecture, section 6](../assets/m365-and-copilot/m365-azure-custom-agent/docs/architecture.md#6-side-effect-classification-and-action-safety-gates) |

## Contributing back

If you generalize a reusable pattern, reference architecture, or accelerator, tag it with the pillars it serves and contribute it by following the [contribution guide](contribution-guide.md).
