# Agentic AI Design Patterns

An open catalog of agentic AI design patterns, reference architectures and solution accelerators for building production-grade agentic systems on Azure and Microsoft Foundry.

Each asset is generalized from real implementations into a domain-agnostic pattern, explains its trade-offs and when not to use it, ships runnable samples with an offline self-test, and carries a machine-readable `manifest.yaml`, so you can browse it here or discover it through search tools. A maintainer reviews every asset before merge.

## Catalog

<!-- catalog:start -->
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
| [Agent State and Memory: Private Cosmos DB vs Foundry Memory](assets/agent-runtime/agent-state-cosmos-vs-foundry-memory/README.md) | `design-pattern` | preview | Separate the three kinds of agent state that teams conflate: resumable thread and checkpoint state, long-term user memory, and system-of-record data. Put them behind a pluggable StateStore, MemoryStore and ContextProvider interface, derive every key from the verified user and tenant, gate memory writes with a consent and content policy, and pick private Cosmos DB, Foundry Memory, a vector store or SQLite per kind of state, with an auditable erasure path. |
| [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md) | `design-pattern` | preview | A decision pattern for choosing between Foundry prompt agents (declarative model, instructions, and platform-managed tools), hosted agents (your own containerized Agent Framework, LangGraph, or custom code run by Foundry), or a mixed design, per scenario. Covers control, tools and protocols, state, identity, network isolation, observability, operations, and migration, with a runnable rubric-based runtime selector. |
| [Code Execution Sandboxes with Session Pools](assets/agent-runtime/code-execution-sandbox-session-pools/README.md) | `design-pattern` | preview | Design pattern for agents that write and run code. Never execute model-generated code in the agent's process. Run it in Azure Container Apps dynamic sessions (session pools): one isolated session per user and conversation, egress off by default, explicit lifecycle, file I/O through the session API and a least-privilege executor role. Includes a decision matrix, threat model, ADRs and an offline simulator. |
| [Custom OpenTelemetry export from hosted agents with a lightweight trace receiver](assets/agent-runtime/hosted-agent-otel-trace-receiver/README.md) | `sample` | preview | Sample for exporting a second, team-owned copy of Foundry hosted agent OpenTelemetry GenAI traces over OTLP/HTTP, alongside built-in Application Insights tracing rather than instead of it. A lightweight receiver authenticates exporters with Entra tokens, bounds payloads, redacts by allowlist with content off by default, hashes identifiers and returns OTLP partial success. Collector-first guidance, ADRs and an offline self-test. |
| [Hosted agent behind a streaming model gateway (bring your own model)](assets/agent-runtime/hosted-agent-streaming-model-gateway/README.md) | `solution-accelerator` | preview | Solution accelerator for a Foundry hosted agent that calls its model through a team-owned, OpenAI-compatible streaming gateway on Azure Container Apps. The gateway validates Entra tokens, routes to Foundry deployments or a bring-your-own model, keeps server-sent events unbuffered, retries only before the first byte, enforces per-caller policy, attributes usage and fails closed. Includes Bicep, a pipeline, gateway code, ADRs and an offline simulator. |
| [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md) | `reference-architecture` | preview | Reference architecture for running containerized hosted agents on Microsoft Foundry Agent Service in production, on top of the secured Foundry landing zone. Covers private networking, a managed identity with least-privilege data-plane roles, CI/CD from a runner inside the VNet, an explicit session lifecycle (pin, heartbeat, cleanup), per-audience token caching, OpenTelemetry observability and a governed rollout with version pinning and rollback, plus ADRs and an offline simulator. |
| [Request Context Is Not Authorization](assets/agent-runtime/request-context-is-not-authorization/README.md) | `design-pattern` | preview | Treat everything in an agent's request context (forwarded headers, payload tenant or user ids, metadata, tool arguments, retrieved content, memory, model output) as tainted input. Derive authorization only from a verified identity, decide it in a deny-by-default policy decision point, and re-check it at every tool and resource boundary at call time. The model is never the policy decision point. |

### [AI Gateway](assets/ai-gateway/README.md) (`ai-gateway`)

API Management as the control plane for model, MCP tool, and agent traffic: multi-tenant MCP gateways, OAuth front doors, identity propagation, metering, and quotas.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Backend Identity Propagation for MCP and Agent Gateways (OBO, Signed Assertions, Tenant Credentials)](assets/ai-gateway/backend-identity-propagation/README.md) | `design-pattern` | preview | How an MCP server or agent gateway carries the end user's identity to a backend system of record without forwarding the client's token. Compares on-behalf-of token exchange (RFC 8693, Entra OBO), a gateway-signed user assertion with an identity link over mTLS, and a transitional per-tenant credential bridge, plus app-only calls and agent identity. Includes a decision matrix, wire contracts, caching and revocation rules, illustrative API Management policies, and an offline simulator. |
| [Entitlement-Aware Tool Catalog](assets/ai-gateway/entitlement-aware-tool-catalog/README.md) | `design-pattern` | preview | An MCP gateway that returns from tools/list only the tools each caller's tenant licenses and roles allow, with schemas minimized per entitlement, and re-checks the same decision on every tools/call so hidden or guessed tool names never run. Entitlements come from the validated token and a registry, views are cached by entitlement set rather than by user, and catalog changes reach affected sessions as tools/list_changed. |
| [MCP OAuth Front Door (Protected Resource Metadata, Discovery and Client Registration Shims)](assets/ai-gateway/mcp-oauth-front-door/README.md) | `design-pattern` | preview | An OAuth front door on Azure API Management that lets MCP clients discover Microsoft Entra ID or Entra External ID and obtain correctly scoped tokens for remote MCP servers. Covers the 401 challenge, Protected Resource Metadata (RFC 9728), an RFC 8414 metadata shim, an optional governed registration shim, authorize and token facades that map resource to scope, token validation, security trade-offs, and an offline simulator. |
| [Multi-Team Model Gateway with Per-Team Token Quotas](assets/ai-gateway/multi-team-model-gateway/README.md) | `solution-accelerator` | preview | A solution accelerator that lets several product teams share one Microsoft Foundry model deployment pool behind Azure API Management as an AI gateway. Each team gets its own token-per-minute budget, usage attribution for chargeback, and a gateway key delivered only through its own Foundry project connection, so no team ever holds a model key or can spend another team's quota. Includes Bicep, gateway policies, Python client code, and an offline simulator. |
| [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md) | `reference-architecture` | preview | A reference architecture for a SaaS provider (ISV) or platform team that exposes many backend APIs as MCP tools to many tenants' agents through one governed Azure API Management front door. Covers the WAF network path, token-derived tenant identity, a per-call policy pipeline (entitlement, per-tenant limits, backend credentials, cell routing, audit), tool checks on every tools/call, API Center as the catalog, and a runnable simulator. |

### [Orchestration and Workflows](assets/orchestration-and-workflows/README.md) (`orchestration-and-workflows`)

Governed agent harnesses, verification gates, multi-agent routers, planner-and-specialist handoffs, durable state/caching, and workflow engines.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Bounded Autonomy: Budgets, Action Envelopes and Escalation](assets/orchestration-and-workflows/bounded-autonomy/README.md) | `design-pattern` | preview | Server-enforced limits that let an agent act on its own inside a known envelope and stop or escalate outside it. Server-side policy picks an autonomy profile from verified identity, never the prompt. The harness enforces attempt, tool-call, token, time and cost budgets, checks each planned step against tool, side-effect, argument and data-boundary rules, fails closed without policy or audit, and records every decision. |
| [Evidence Ledger and Run Certificate](assets/orchestration-and-workflows/evidence-ledger-and-run-certificate/README.md) | `design-pattern` | preview | An append-only, hash-chained evidence ledger that records each agent step (policy decisions, tool calls, source digests, checks, approvals) as digests and references rather than raw content, sealed by a signed run certificate that binds the ledger head, artifact digest, required checks and approval. A run is done only with a valid, non-withheld certificate that anyone with a trusted key can verify offline. |
| [Governed Context-Aware Agent Harness](assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md) | `reference-architecture` | preview | A reference architecture for the runtime harness around an LLM agent: mandatory plan-before-act policy, progressive skill disclosure, scoped memory, an independent deterministic verifier, an evidence ledger, separate human approval of the exact verified artifact, and a completion certificate - so a convincing answer is never mistaken for a correctly completed task. |
| [Progressive Tool and Skill Loading](assets/orchestration-and-workflows/progressive-tool-loading/README.md) | `design-pattern` | preview | Start each run with a small skill index, then disclose tool schemas and skill instructions in tiers by phase or on model request. A context budget, deterministic ordering and eviction keep context focused. Calls to unloaded tools are rejected and every load is recorded. Loading decides when a tool is shown; entitlement and the autonomy profile decide which tools are eligible, and the server still checks entitlement on every call. |
| [Verify Before Approve: Deterministic Verification Ahead of Human Approval](assets/orchestration-and-workflows/verify-before-approve/README.md) | `design-pattern` | preview | Separates machine-checkable verification from human approval. A verifier independent of the generating model runs deterministic checks; only verified artifacts reach approvers, and each approval binds to the exact artifact sha256 and verification receipt. Failures drive bounded self-correction, then a labelled unverified preview, never silent approval. Approvers spend their attention on judgement, not arithmetic, and rubber-stamp approvals drop. |

### [AgentOps and Production Readiness](assets/agentops-and-readiness/README.md) (`agentops-and-readiness`)

Production-readiness guides, evidence-based readiness gates, evaluation frameworks, and AgentOps operating models.

| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Agent Production Readiness Guide](assets/agentops-and-readiness/agent-production-readiness/README.md) | `playbook` | experimental | An evidence-led production-readiness guide for taking an agentic application from early design or a pilot to an operable production workload, with six readiness pillars, three technical plays, and release gates. |

**Planned categories** (folder is created with the first asset): Data and Tools (`data-and-tools`). See [docs/production-readiness-map.md](docs/production-readiness-map.md#planned-categories-where-to-look-today) for related content today.
<!-- catalog:end -->

## Browse by production-readiness pillar

Every asset is also tagged with the production-readiness pillars it supports, so you can start from a specific blocker (governance, environment, AgentOps, security, optimization or architecture) and find the patterns that address it. The [production-readiness map](docs/production-readiness-map.md) adds hand-picked deep links, and the [Agent Production Readiness Guide](assets/agentops-and-readiness/agent-production-readiness/README.md) offers an optional review method that uses the same pillars.

<!-- pillars:start -->
| Pillar | Focus | Assets |
|--------|-------|--------|
| [1. AI CoE & Governance](assets/agentops-and-readiness/agent-production-readiness/docs/pillars/01-ai-coe-and-governance.md) | Operating model, intake and graduation, responsible AI policy, agent inventory, cost accountability. | [Enterprise Agent Security and Tool Governance](assets/security-and-governance/agent-security-and-governance/README.md), [Agent State and Memory: Private Cosmos DB vs Foundry Memory](assets/agent-runtime/agent-state-cosmos-vs-foundry-memory/README.md), [Request Context Is Not Authorization](assets/agent-runtime/request-context-is-not-authorization/README.md), [Entitlement-Aware Tool Catalog](assets/ai-gateway/entitlement-aware-tool-catalog/README.md), [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md), [Verify Before Approve: Deterministic Verification Ahead of Human Approval](assets/orchestration-and-workflows/verify-before-approve/README.md), [Agent Production Readiness Guide](assets/agentops-and-readiness/agent-production-readiness/README.md) |
| [2. AI Landing Zones](assets/agentops-and-readiness/agent-production-readiness/docs/pillars/02-ai-landing-zones.md) | Network topology, private connectivity, identity and RBAC, policy, environments, regional design. | [Secured Microsoft Foundry Landing Zone for Enterprise Agents](assets/landing-zones-and-infra/secured-foundry-landing-zone/README.md), [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md), [Backend Identity Propagation for MCP and Agent Gateways (OBO, Signed Assertions, Tenant Credentials)](assets/ai-gateway/backend-identity-propagation/README.md), [MCP OAuth Front Door (Protected Resource Metadata, Discovery and Client Registration Shims)](assets/ai-gateway/mcp-oauth-front-door/README.md), [Multi-Team Model Gateway with Per-Team Token Quotas](assets/ai-gateway/multi-team-model-gateway/README.md), [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md), [Agent Production Readiness Guide](assets/agentops-and-readiness/agent-production-readiness/README.md) |
| [3. Agent Ops](assets/agentops-and-readiness/agent-production-readiness/docs/pillars/03-agent-ops.md) | CI/CD, versioning, tracing, evaluation gates, monitoring, incident runbooks. | [Microsoft 365 Integration with Azure Custom Engine Agent](assets/m365-and-copilot/m365-azure-custom-agent/README.md), [Enterprise Agent Security and Tool Governance](assets/security-and-governance/agent-security-and-governance/README.md), [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md), [Code Execution Sandboxes with Session Pools](assets/agent-runtime/code-execution-sandbox-session-pools/README.md), [Custom OpenTelemetry export from hosted agents with a lightweight trace receiver](assets/agent-runtime/hosted-agent-otel-trace-receiver/README.md), [Hosted agent behind a streaming model gateway (bring your own model)](assets/agent-runtime/hosted-agent-streaming-model-gateway/README.md), [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md), [Bounded Autonomy: Budgets, Action Envelopes and Escalation](assets/orchestration-and-workflows/bounded-autonomy/README.md), [Evidence Ledger and Run Certificate](assets/orchestration-and-workflows/evidence-ledger-and-run-certificate/README.md), [Governed Context-Aware Agent Harness](assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md), [Agent Production Readiness Guide](assets/agentops-and-readiness/agent-production-readiness/README.md) |
| [4. AI Security & Observability](assets/agentops-and-readiness/agent-production-readiness/docs/pillars/04-ai-security-and-observability.md) | Agent and tool authorization, prompt-injection defenses, sensitive data, red teaming, audit trails. | [Enterprise Agent Security and Tool Governance](assets/security-and-governance/agent-security-and-governance/README.md), [Agent State and Memory: Private Cosmos DB vs Foundry Memory](assets/agent-runtime/agent-state-cosmos-vs-foundry-memory/README.md), [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md), [Code Execution Sandboxes with Session Pools](assets/agent-runtime/code-execution-sandbox-session-pools/README.md), [Custom OpenTelemetry export from hosted agents with a lightweight trace receiver](assets/agent-runtime/hosted-agent-otel-trace-receiver/README.md), [Hosted agent behind a streaming model gateway (bring your own model)](assets/agent-runtime/hosted-agent-streaming-model-gateway/README.md), [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md), [Request Context Is Not Authorization](assets/agent-runtime/request-context-is-not-authorization/README.md), [Backend Identity Propagation for MCP and Agent Gateways (OBO, Signed Assertions, Tenant Credentials)](assets/ai-gateway/backend-identity-propagation/README.md), [Entitlement-Aware Tool Catalog](assets/ai-gateway/entitlement-aware-tool-catalog/README.md), [MCP OAuth Front Door (Protected Resource Metadata, Discovery and Client Registration Shims)](assets/ai-gateway/mcp-oauth-front-door/README.md), [Multi-Team Model Gateway with Per-Team Token Quotas](assets/ai-gateway/multi-team-model-gateway/README.md), [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md), [Bounded Autonomy: Budgets, Action Envelopes and Escalation](assets/orchestration-and-workflows/bounded-autonomy/README.md), [Evidence Ledger and Run Certificate](assets/orchestration-and-workflows/evidence-ledger-and-run-certificate/README.md), [Governed Context-Aware Agent Harness](assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md), [Progressive Tool and Skill Loading](assets/orchestration-and-workflows/progressive-tool-loading/README.md), [Verify Before Approve: Deterministic Verification Ahead of Human Approval](assets/orchestration-and-workflows/verify-before-approve/README.md), [Agent Production Readiness Guide](assets/agentops-and-readiness/agent-production-readiness/README.md) |
| [5. Solution Optimization](assets/agentops-and-readiness/agent-production-readiness/docs/pillars/05-solution-optimization.md) | Performance, latency, cost, retrieval quality, semantic layers, scaling. | [Microsoft 365 Integration with Azure Custom Engine Agent](assets/m365-and-copilot/m365-azure-custom-agent/README.md), [Entitlement-Aware Tool Catalog](assets/ai-gateway/entitlement-aware-tool-catalog/README.md), [Multi-Team Model Gateway with Per-Team Token Quotas](assets/ai-gateway/multi-team-model-gateway/README.md), [Progressive Tool and Skill Loading](assets/orchestration-and-workflows/progressive-tool-loading/README.md), [Agent Production Readiness Guide](assets/agentops-and-readiness/agent-production-readiness/README.md) |
| [6. Proven Patterns for Agent Architectures](assets/agentops-and-readiness/agent-production-readiness/docs/pillars/06-proven-patterns-for-agent-architectures.md) | Orchestration, quality controls, interoperability, state, front doors, scale. | [Microsoft 365 Integration with Azure Custom Engine Agent](assets/m365-and-copilot/m365-azure-custom-agent/README.md), [Microsoft 365 Copilot Integration with MCP via Plug-ins](assets/m365-and-copilot/m365-mcp-plugin-integration/README.md), [Agent State and Memory: Private Cosmos DB vs Foundry Memory](assets/agent-runtime/agent-state-cosmos-vs-foundry-memory/README.md), [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](assets/agent-runtime/choosing-prompt-vs-hosted-agents/README.md), [Code Execution Sandboxes with Session Pools](assets/agent-runtime/code-execution-sandbox-session-pools/README.md), [Hosted agent behind a streaming model gateway (bring your own model)](assets/agent-runtime/hosted-agent-streaming-model-gateway/README.md), [Production Hosted Agents on Microsoft Foundry](assets/agent-runtime/production-hosted-agents-on-foundry/README.md), [Request Context Is Not Authorization](assets/agent-runtime/request-context-is-not-authorization/README.md), [Backend Identity Propagation for MCP and Agent Gateways (OBO, Signed Assertions, Tenant Credentials)](assets/ai-gateway/backend-identity-propagation/README.md), [Entitlement-Aware Tool Catalog](assets/ai-gateway/entitlement-aware-tool-catalog/README.md), [MCP OAuth Front Door (Protected Resource Metadata, Discovery and Client Registration Shims)](assets/ai-gateway/mcp-oauth-front-door/README.md), [Multi-Tenant MCP Gateway on Azure API Management](assets/ai-gateway/multi-tenant-mcp-gateway/README.md), [Bounded Autonomy: Budgets, Action Envelopes and Escalation](assets/orchestration-and-workflows/bounded-autonomy/README.md), [Evidence Ledger and Run Certificate](assets/orchestration-and-workflows/evidence-ledger-and-run-certificate/README.md), [Governed Context-Aware Agent Harness](assets/orchestration-and-workflows/governed-context-aware-agent-harness/README.md), [Progressive Tool and Skill Loading](assets/orchestration-and-workflows/progressive-tool-loading/README.md), [Verify Before Approve: Deterministic Verification Ahead of Human Approval](assets/orchestration-and-workflows/verify-before-approve/README.md), [Agent Production Readiness Guide](assets/agentops-and-readiness/agent-production-readiness/README.md) |
<!-- pillars:end -->

The catalog and pillar map above are generated from each asset's `manifest.yaml` by `python scripts/assets.py catalog`. Do not edit them by hand.

## How this repository is used

This repository is a public technical reference. Architects and engineers use it directly, and teams and partners running path-to-production programs for agentic workloads use it as their technical reference. Program process such as intake, engagement and scheduling lives outside this repository; the assets here stay focused on reusable architecture, code and evidence.

## Asset kinds

| Kind | What it is |
|------|------------|
| `design-pattern` | A reusable approach to a recurring agentic design problem, with trade-offs and when (not) to use it. |
| `reference-architecture` | An end-to-end architecture for a scenario, with components, data flows, identity, security and operations. |
| `solution-accelerator` | Deployable code and infrastructure that implements a pattern or architecture. |
| `playbook` | A step-by-step adoption or readiness guide. |
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
  production-readiness-map.md     pillar-by-pillar navigation into the catalog
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
| [`m365-and-copilot`](assets/m365-and-copilot/README.md) | Microsoft 365 Copilot, Teams, declarative and custom engine agents, MCP plug-ins | Published |
| [`security-and-governance`](assets/security-and-governance/README.md) | Agent identity, tool governance, guardrails, audit | Published |
| [`landing-zones-and-infra`](assets/landing-zones-and-infra/README.md) | Private Microsoft Foundry landing zones and infrastructure as code | Published |
| [`agent-runtime`](assets/agent-runtime/README.md) | Prompt vs hosted agents, production hosted agents, sessions, sandboxed code execution | Published |
| [`ai-gateway`](assets/ai-gateway/README.md) | API Management as the control plane for model, MCP tool, and agent traffic | Published |
| [`orchestration-and-workflows`](assets/orchestration-and-workflows/README.md) | Governed agent harnesses, verification gates, multi-agent routing, durable workflows | Published |
| [`agentops-and-readiness`](assets/agentops-and-readiness/README.md) | Production-readiness guides, readiness gates, evaluation and AgentOps | Published |
| `data-and-tools` | Governed MCP servers, data connectors, grounding, action safety | Planned |

Planned categories get a folder with their first asset. Until then, the [production-readiness map](docs/production-readiness-map.md#planned-categories-where-to-look-today) shows where those topics are covered today.

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

A maintainer reviews every contribution. Everything in this repository is public: do not commit customer names, customer data, internal links, unreleased product information, or secrets.

## Earlier content

The 2023–2024 content of this repository (foundational concepts, the original design patterns, reference architecture and accelerators) is preserved at commit [`a4c1ee8`](https://github.com/microsoft/agenticai-design-patterns/tree/a4c1ee891c93a2fb2995dbd10f9ced73795d6f64).

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is subject to those third parties' policies.
