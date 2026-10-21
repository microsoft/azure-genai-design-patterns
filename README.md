# Agentic AI Design Patterns

Field-proven design patterns, reference architectures, solution accelerators and delivery playbooks for taking agentic AI systems on Azure and Microsoft Foundry from pilot to production.

Every asset is generalized from repeated field work into a domain-agnostic pattern, reviewed by a curator before merge, follows the same folder layout, and carries a machine-readable `manifest.yaml`, so you can browse it here or discover it through search tools.

## Start here: path to production

Most agentic projects stall between a working pilot and an operable production workload. The assets here are organized around six path-to-production pillars, so you can go from "what is blocking us" to the right pattern. Use the [path-to-production guide](docs/path-to-production.md) to frame a review, and the [Agent Path to Production playbook](assets/delivery-and-ops/agent-path-to-production/README.md) to run one.

<!-- pillars:start -->
| Pillar | Focus | Assets |
|--------|-------|--------|
| [1. AI CoE & Governance](assets/delivery-and-ops/agent-path-to-production/docs/pillars/01-ai-coe-and-governance.md) | Operating model, intake and graduation, responsible AI policy, agent inventory, cost accountability. | [Agent Path to Production](assets/delivery-and-ops/agent-path-to-production/README.md), [Enterprise Agent Security and Tool Governance](assets/security-and-governance/agent-security-and-governance/README.md), [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md), [Verify Before Approve: Deterministic Verification Ahead of Human Approval](assets/orchestration-and-workflows/verify-before-approve/README.md) |
| [2. AI Landing Zones](assets/delivery-and-ops/agent-path-to-production/docs/pillars/02-ai-landing-zones.md) | Network topology, private connectivity, identity and RBAC, policy, environments, regional design. | [Agent Path to Production](assets/delivery-and-ops/agent-path-to-production/README.md), [Secured Microsoft Foundry Landing Zone for Enterprise Agents](assets/landing-zones-and-infra/secured-foundry-landing-zone/README.md), [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md), [Backend Identity Propagation for MCP and Agent Gateways (OBO, Signed Assertions, Tenant Credentials)](assets/ai-gateway/backend-identity-propagation/README.md), [MCP OAuth Front Door (Protected Resource Metadata, Discovery and Client Registration Shims)](assets/ai-gateway/mcp-oauth-front-door/README.md), [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md) |
| [3. Agent Ops](assets/delivery-and-ops/agent-path-to-production/docs/pillars/03-agent-ops.md) | CI/CD, versioning, tracing, evaluation gates, monitoring, incident runbooks. | [Agent Path to Production](assets/delivery-and-ops/agent-path-to-production/README.md), [Microsoft 365 Integration with Azure Custom Engine Agent](assets/m365-and-copilot/m365-azure-custom-agent/README.md), [Enterprise Agent Security and Tool Governance](assets/security-and-governance/agent-security-and-governance/README.md), [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md), [Code Execution Sandboxes with Session Pools](assets/agent-runtime/code-execution-sandbox-session-pools/README.md), [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md), [Governed Context-Aware Agent Harness](assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md) |
| [4. AI Security & Observability](assets/delivery-and-ops/agent-path-to-production/docs/pillars/04-ai-security-and-observability.md) | Agent and tool authorization, prompt-injection defenses, sensitive data, red teaming, audit trails. | [Agent Path to Production](assets/delivery-and-ops/agent-path-to-production/README.md), [Enterprise Agent Security and Tool Governance](assets/security-and-governance/agent-security-and-governance/README.md), [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md), [Code Execution Sandboxes with Session Pools](assets/agent-runtime/code-execution-sandbox-session-pools/README.md), [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md), [Backend Identity Propagation for MCP and Agent Gateways (OBO, Signed Assertions, Tenant Credentials)](assets/ai-gateway/backend-identity-propagation/README.md), [MCP OAuth Front Door (Protected Resource Metadata, Discovery and Client Registration Shims)](assets/ai-gateway/mcp-oauth-front-door/README.md), [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md), [Governed Context-Aware Agent Harness](assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md), [Verify Before Approve: Deterministic Verification Ahead of Human Approval](assets/orchestration-and-workflows/verify-before-approve/README.md) |
| [5. Solution Optimization](assets/delivery-and-ops/agent-path-to-production/docs/pillars/05-solution-optimization.md) | Performance, latency, cost, retrieval quality, semantic layers, scaling. | [Agent Path to Production](assets/delivery-and-ops/agent-path-to-production/README.md), [Microsoft 365 Integration with Azure Custom Engine Agent](assets/m365-and-copilot/m365-azure-custom-agent/README.md) |
| [6. Proven Patterns for Agent Architectures](assets/delivery-and-ops/agent-path-to-production/docs/pillars/06-proven-patterns-for-agent-architectures.md) | Orchestration, quality controls, interoperability, state, front doors, scale. | [Agent Path to Production](assets/delivery-and-ops/agent-path-to-production/README.md), [Microsoft 365 Integration with Azure Custom Engine Agent](assets/m365-and-copilot/m365-azure-custom-agent/README.md), [Microsoft 365 Copilot Integration with MCP via Plug-ins](assets/m365-and-copilot/m365-mcp-plugin-integration/README.md), [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md), [Code Execution Sandboxes with Session Pools](assets/agent-runtime/code-execution-sandbox-session-pools/README.md), [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md), [Backend Identity Propagation for MCP and Agent Gateways (OBO, Signed Assertions, Tenant Credentials)](assets/ai-gateway/backend-identity-propagation/README.md), [MCP OAuth Front Door (Protected Resource Metadata, Discovery and Client Registration Shims)](assets/ai-gateway/mcp-oauth-front-door/README.md), [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md), [Governed Context-Aware Agent Harness](assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md), [Verify Before Approve: Deterministic Verification Ahead of Human Approval](assets/orchestration-and-workflows/verify-before-approve/README.md) |
<!-- pillars:end -->

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

### [Agent Runtime and Hosting](assets/agent-runtime/README.md) (`agent-runtime`)

Prompt vs hosted agents, production hosted agents on Foundry, private runtime networking, sessions, and sandboxed code execution.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md) | `design-pattern` | preview | A decision pattern for choosing between Foundry prompt agents (declarative model, instructions, and platform-managed tools), hosted agents (your own containerized Agent Framework, LangGraph, or custom code run by Foundry), or a mixed design, per scenario. Covers control, tools and protocols, state, identity, network isolation, observability, operations, and migration, with a runnable rubric-based runtime selector. |
| [Code Execution Sandboxes with Session Pools](assets/agent-runtime/code-execution-sandbox-session-pools/README.md) | `design-pattern` | preview | Design pattern for agents that write and run code. Never execute model-generated code in the agent's process. Run it in Azure Container Apps dynamic sessions (session pools): one isolated session per user and conversation, egress off by default, explicit lifecycle, file I/O through the session API and a least-privilege executor role. Includes a decision matrix, threat model, ADRs and an offline simulator. |
| [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md) | `reference-architecture` | preview | Reference architecture for running containerized hosted agents on Microsoft Foundry Agent Service in production, on top of the secured Foundry landing zone. Covers private networking, a managed identity with least-privilege data-plane roles, CI/CD from a runner inside the VNet, an explicit session lifecycle (pin, heartbeat, cleanup), per-audience token caching, OpenTelemetry observability and a governed rollout with version pinning and rollback, plus ADRs and an offline simulator. |

### [AI Gateway](assets/ai-gateway/README.md) (`ai-gateway`)

API Management as the control plane for model, MCP tool, and agent traffic: multi-tenant MCP gateways, OAuth front doors, identity propagation, metering, and quotas.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Backend Identity Propagation for MCP and Agent Gateways (OBO, Signed Assertions, Tenant Credentials)](assets/ai-gateway/backend-identity-propagation/README.md) | `design-pattern` | preview | How an MCP server or agent gateway carries the end user's identity to a backend system of record without forwarding the client's token. Compares on-behalf-of token exchange (RFC 8693, Entra OBO), a gateway-signed user assertion with an identity link over mTLS, and a transitional per-tenant credential bridge, plus app-only calls and agent identity. Includes a decision matrix, wire contracts, caching and revocation rules, illustrative API Management policies, and an offline simulator. |
| [MCP OAuth Front Door (Protected Resource Metadata, Discovery and Client Registration Shims)](assets/ai-gateway/mcp-oauth-front-door/README.md) | `design-pattern` | preview | An OAuth front door on Azure API Management that lets MCP clients discover Microsoft Entra ID or Entra External ID and obtain correctly scoped tokens for remote MCP servers. Covers the 401 challenge, Protected Resource Metadata (RFC 9728), an RFC 8414 metadata shim, an optional governed registration shim, authorize and token facades that map resource to scope, token validation, security trade-offs, and an offline simulator. |
| [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md) | `reference-architecture` | preview | A reference architecture for a SaaS provider (ISV) or platform team that exposes many backend APIs as MCP tools to many tenants' agents through one governed Azure API Management front door. Covers the WAF network path, token-derived tenant identity, a per-call policy pipeline (entitlement, per-tenant limits, backend credentials, cell routing, audit), tool checks on every tools/call, API Center as the catalog, and a runnable simulator. |

### [Orchestration and Workflows](assets/orchestration-and-workflows/README.md) (`orchestration-and-workflows`)

Governed agent harnesses, verification gates, multi-agent routers, planner-and-specialist handoffs, durable state/caching, and workflow engines.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Governed Context-Aware Agent Harness](assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md) | `reference-architecture` | preview | A reference architecture for the runtime harness around an LLM agent: mandatory plan-before-act policy, progressive skill disclosure, scoped memory, an independent deterministic verifier, an evidence ledger, separate human approval of the exact verified artifact, and a completion certificate - so a convincing answer is never mistaken for a correctly completed task. |
| [Verify Before Approve: Deterministic Verification Ahead of Human Approval](assets/orchestration-and-workflows/verify-before-approve/README.md) | `design-pattern` | preview | Separates machine-checkable verification from human approval. A verifier independent of the generating model runs deterministic checks; only verified artifacts reach approvers, and each approval binds to the exact artifact sha256 and verification receipt. Failures drive bounded self-correction, then a labelled unverified preview, never silent approval. Approvers spend their attention on judgement, not arithmetic, and rubber-stamp approvals drop. |

**Planned categories** (folder is created with the first asset): Data and Tools (`data-and-tools`). See [docs/path-to-production.md](docs/path-to-production.md) for related content today.
<!-- catalog:end -->

The pillar map and catalog above are generated from each asset's `manifest.yaml` by `python scripts/assets.py catalog`. Do not edit them by hand.

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
assets/                           the catalog: one folder per category, one folder per asset
  README.md                       category directory and decision flowchart
  <category>/                     architectural category
    README.md                     category decision guide and generated category catalog
    <asset-id>/                   one folder per asset; the folder name is the stable asset id
      manifest.yaml               metadata: category, kind, pillars, tags, owners, version, maturity
      README.md                   overview, domain map, when to use, architecture, getting started
      CHANGELOG.md                what changed in each version
      docs/                       architecture deep dives, identity, evaluation, troubleshooting, ADRs
      samples/                    runnable reference code with an offline --self-test
      infra/                      infrastructure as code (optional)
      src/                        source code (optional)
docs/
  path-to-production.md           pillar-by-pillar navigation into the catalog
  contribution-guide.md           generalization method, depth bar and public-safety rules
scripts/                          maintenance tooling run locally and in CI
  assets.py                       scaffold, validate, catalog and scan assets
  manifest.schema.json            JSON Schema for manifest.yaml
templates/
  asset/                          starting point copied by `scripts/assets.py new`
  optional/                       infra/ and src/ folders added with --with-infra / --with-src
  category/                       starting point for a new category README
.github/                          CI workflow, CODEOWNERS, issue and pull request templates
```

Assets are grouped into architectural categories under [`assets/`](assets/README.md):

| Category | Scope | Status |
|----------|-------|--------|
| [`delivery-and-ops`](assets/delivery-and-ops/README.md) | Delivery playbooks, readiness gates, evaluation and AgentOps | Published |
| [`m365-and-copilot`](assets/m365-and-copilot/README.md) | Microsoft 365 Copilot, Teams, declarative and custom engine agents, MCP plug-ins | Published |
| [`security-and-governance`](assets/security-and-governance/README.md) | Agent identity, tool governance, guardrails, audit | Published |
| [`landing-zones-and-infra`](assets/landing-zones-and-infra/README.md) | Private Microsoft Foundry landing zones and infrastructure as code | Published |
| [`agent-runtime`](assets/agent-runtime/README.md) | Prompt vs hosted agents, production hosted agents, sessions, sandboxed code execution | Published |
| [`ai-gateway`](assets/ai-gateway/README.md) | API Management as the control plane for model, MCP tool, and agent traffic | Published |
| [`orchestration-and-workflows`](assets/orchestration-and-workflows/README.md) | Governed agent harnesses, verification gates, multi-agent routing, durable workflows | Published |
| `data-and-tools` | Governed MCP servers, data connectors, grounding, action safety | Planned |

Planned categories get a folder with their first asset. Until then, [docs/path-to-production.md](docs/path-to-production.md#planned-categories-where-to-look-today) shows where those topics are covered today.

## Using an asset

Start with the category's `README.md` to compare sibling patterns, then open the asset's `README.md`. Link to a specific version with a tag or commit permalink rather than `main`, because assets evolve.

## Contributing

Propose a pattern with the **Pattern proposal** issue form, then follow [CONTRIBUTING.md](CONTRIBUTING.md) and the [contribution guide](docs/contribution-guide.md). To scaffold and check a new asset:

```bash
pip install -r scripts/requirements.txt
python scripts/assets.py new --category <category> --id <asset-id> --kind <kind> --title "<Title>"
python scripts/assets.py catalog
python scripts/assets.py validate
python scripts/assets.py scan
```

A curator reviews every contribution. Everything in this repository is public: do not commit customer names, customer data, internal links, unreleased product information, or secrets.

## Earlier content

The 2023–2024 content of this repository (foundational concepts, the original design patterns, reference architecture and accelerators) is preserved at commit [`a4c1ee8`](https://github.com/microsoft/agenticai-design-patterns/tree/a4c1ee891c93a2fb2995dbd10f9ced73795d6f64).

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is subject to those third parties' policies.
