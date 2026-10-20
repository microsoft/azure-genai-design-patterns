# 1. Surface and orchestration split

- Status: accepted
- Date: 2026-10-20

## Context

The Contoso Revenue Intelligence Agent is a seller-facing assistant in Microsoft Teams and Microsoft 365 Copilot. It has the following characteristics:

- Its orchestration is custom: a planner with specialists.
- Its model versions are pinned for evaluation stability, and its throughput is provisioned.
- It pushes a proactive morning briefing.
- It reaches downstream data through OBO with territory-level permissions.

Users now also want three more things:

- Answers grounded in their own mail, meetings, and files.
- Supervised Microsoft 365 actions.
- Scheduled or bulk work, such as overnight territory scans and multi-account outreach, with per-item approval.

Four surface options are available:

1. A **custom engine agent (CEA)** hosted on a Microsoft Foundry Hosted Agent or Azure Container Apps and published to Teams and Microsoft 365 Copilot.
2. A **declarative agent (DA)** running on Microsoft 365 Copilot's orchestrator.
3. **Cowork**, for scheduled and event-driven execution.
4. **Copilot Studio / Power Automate Workflows.**

Each option is strong where the others are weak:

- **The CEA** offers control: orchestration, models, evaluations, and push.
- **The DA** offers work context and native supervised actions with no operations overhead. It has an instruction cap of about 8,000 characters and cannot push.
- **Cowork** can run while nobody is present, but it cannot push into Teams.
- **Copilot Studio / Workflows** suit notifications and low-code assistants.

Forcing every behavior onto one surface would either lose capabilities or duplicate business logic.

## Decision

1. **Keep the engine as a custom engine agent.** Host it on a Foundry Hosted Agent (the target state) or on Azure Container Apps until the preview gaps are closed. It owns planner-and-specialist orchestration, model pinning and PTU, evaluations and tracing, and **proactive push** through the Microsoft 365 Agents SDK and Azure Bot Service.
2. **Add a declarative agent** for work-context grounding, supervised Microsoft 365 actions, and MCP Apps dashboards.
3. **Use Cowork** for scheduled, event-triggered, and bulk work that has per-item approval.
4. **Put all business data access and all write actions behind one governed MCP server.** The server enforces scope from the validated OBO token and classifies side effects as `R` or `W1`–`W4` (draft before send, caps, approval tokens, audit, kill switch). It is registered in the Foundry tool catalog, and all three surfaces consume it.
5. **Specialists hand out-of-domain requests back to the planner** and never route to peer specialists. Deterministic work is implemented as tools. Only work that needs judgement or synthesis goes to reasoning specialists.
6. **Store state in Azure Cosmos DB**: conversation references, sessions, turns, and a territory- and time-zone-partitioned pre-compute cache with a live-refresh fallback.
7. **Use Copilot Studio / Power Automate Workflows** for notification-only and low-code scenarios that need none of the above. Do not build a CEA for them.

## Consequences

**Easier:**

- Each surface does what it is best at, and adding or switching a surface is additive. Because business logic and safety live in the MCP server, the surface choice is **reversible**.
- Safety is consistent. A Cowork bulk run, a DA action, and a CEA chat turn pass through the same gate.
- A second solution, such as renewals or partner management, can start from the shared tool catalog instead of copying the first one's infrastructure.
- The CEA can move from self-hosted Container Apps to a Foundry Hosted Agent without a rewrite.

**Harder:**

- There are more artifacts to package, govern, and support: the app package, DA manifest, connectors, and the engine.
- Several identity layers must be documented and handed over. See [authentication and identity](../authentication-and-identity.md).
- Calling the CEA from the DA is not a documented first-class pattern. Each surface must be useful on its own.
- Several dependencies are in preview and need validation spikes: Hosted Agents, Cowork offline OBO, Work IQ, and DA native action capabilities.

## Alternatives considered

- **CEA only.** Rejected. Work context and supervised Microsoft 365 actions would need hand-written Graph integration and a custom approval UX. Scheduled and bulk work would stay in custom infrastructure.
- **DA only.** Rejected. It loses custom orchestration, model pinning and PTU, evaluation control, and proactive push. The specialist prompts would also exceed the instruction cap.
- **Cowork as the primary surface.** Rejected. It has no real-time conversation and no push into Teams. It fits only part of the workload.
- **Copilot Studio / Workflows for everything.** Rejected for the core engine because of the orchestration, model control, and evaluation needs. Accepted for notification-only and low-code scenarios.
- **Separate tool implementations for each surface.** Rejected. Business logic would be duplicated, safety would be enforced inconsistently, and changing surfaces would be expensive.
- **Peer-to-peer specialist routing.** Rejected. It creates N×N prompt coupling, exposes internal agent names to users, and causes misrouting that is hard to test.
