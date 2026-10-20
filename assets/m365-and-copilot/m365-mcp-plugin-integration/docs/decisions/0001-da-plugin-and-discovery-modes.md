# ADR 0001: Declarative-agent MCP plug-ins and tool discovery modes

- **Status:** Accepted
- **Date:** 2026-10-20
- **Scope:** `m365-mcp-plugin-integration` asset

## Context

A team has several remote MCP servers that it wants to expose to Microsoft 365 Copilot users:

- **Service A / CRM MCP**: authenticated with a SaaS provider's OAuth.
- **Service B / Knowledge MCP**: validates Microsoft Entra ID tokens directly.
- **Service C / Public Catalog MCP**: anonymous, read-only.

Microsoft 365 offers more than one way to register an MCP server:

1. **Declarative-agent MCP plug-in.** The app package defines `declarativeAgent.json` (v1.8). Its `actions[]` point to `ai-plugin.json` (v2.4) files whose runtime type is `RemoteMCPServer`. Tools are available only inside that agent.
2. **Tenant-wide agent connector.** The root app manifest (v1.27+) declares `agentConnectors[]`. The MCP server is exposed to Cowork and other Copilot surfaces, not just one agent.
3. **Federated Copilot connector.** An administrator adds the MCP server under Microsoft 365 admin center > Copilot > Connectors. It applies tenant-wide and needs no app package.

The team also has to choose how Copilot learns each server's tools:

- **Dynamic discovery:** `functions: []`, `run_for_functions: ["*"]`, and no `mcp_tool_description`. Copilot calls `tools/list` at runtime.
- **Pinned definitions:** `functions[]` plus `spec.mcp_tool_description.file`, which ships a snapshot of the tool catalog in the package.

Constraints that shape the decision:

- A declarative agent supports up to **10 plug-in actions** and **8,000 characters** of instructions.
- With dynamic discovery, every connected server is listed again on each turn, so latency and token usage grow with the size of the catalog.
- `isNonConsequential` is ignored for `RemoteMCPServer` runtimes. To skip the per-tool confirmation, the server must set `readOnlyHint` in the tool's annotations. The per-connection consent prompt always appears.
- In validation testing, a federated connector showed as connected but chat never invoked its tools. Declarative-agent plug-ins, both pinned and dynamic, invoked tools reliably.

## Decision

1. **Use declarative-agent MCP plug-ins (Path 1) as the default registration path.** Create one `ai-plugin.json` per MCP server with a unique `namespace`, and reference them all from a single declarative agent.
2. **Add tenant-wide agent connectors (Path 2) only when needed.** Use them when the tools must be available in Cowork or across Copilot without opening a specific agent. Treat this as an additional channel for the same servers, not a replacement for the agent.
3. **Do not use federated Copilot connectors for action-oriented MCP tools** until tool invocation is verified in the target tenant. Revisit this decision when the platform behavior changes.
4. **Default to dynamic discovery.** Use it during development and for servers whose owner also owns the agent.
5. **Switch to pinned definitions per server** in any of these cases:
   - Governance requires a reviewed tool surface.
   - You need custom Adaptive Card confirmation text or `security_info` metadata on write tools.
   - You need to expose only a subset of the server's tools.
   - You can't change the server's annotations.
6. **Keep the instructions under the cap with progressive disclosure.** Instructions route the user to the right workflow. The workflows themselves go in `agent_skills` folders (`skills/<name>/SKILL.md`), and tool descriptions stay short.

## Considered options

| Option | Pros | Cons | Outcome |
|---|---|---|---|
| Declarative-agent plug-ins, dynamic discovery | No catalog drift; fastest iteration; server owns annotations | Tools re-listed each turn; confirmation text comes from tool descriptions; whole server surface exposed | **Default** |
| Declarative-agent plug-ins, pinned definitions | Reviewed surface; per-function confirmation and `security_info`; subset exposure | Catalog drift if the server changes; package redeploy for every tool change | **Per server, when required** |
| Tenant-wide agent connectors | Reaches Cowork and broader Copilot surfaces; no agent switch for users | Broader blast radius; no dedicated instructions or skills; needs app manifest v1.27+ | **Additive, opt-in** |
| Federated Copilot connectors | Admin-only setup; no package | Tool invocation not reliable in validation; tenant-wide only; no per-agent instructions | **Rejected for now** |
| One agent per MCP server | Simple isolation | Users switch agents; no cross-server workflows | Rejected |

## Consequences

**Positive**

- One agent experience can orchestrate several servers in a single conversation.
- Each server keeps its own authentication registration, so you can mix OAuth, Entra SSO, and anonymous servers in one agent.
- Skills keep the instructions small and let workflows evolve without rewriting the agent prompt.

**Negative / risks**

- The 10-action cap limits how many servers fit in one agent. Group related tools on fewer servers, or split agents by domain.
- With dynamic discovery, a server deployment can change agent behavior without a package change. Pin tools, or gate server releases, for production-critical agents.
- With multiple authenticated servers, users see a separate sign-in or consent prompt for each server the first time they use it.
- Pinned catalogs must be regenerated whenever server tools change. Automate this from `tools/list` in CI.

**Follow-ups**

- Re-test federated connector invocation every quarter, and supersede this ADR if it becomes reliable.
- Track the declarative agent schema for changes to the action cap or instruction cap.
