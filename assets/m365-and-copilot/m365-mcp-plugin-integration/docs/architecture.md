# Architecture: Remote MCP servers in Microsoft 365 Copilot

This document is the reference architecture for connecting remote Model Context Protocol (MCP) servers to Microsoft 365 Copilot. It applies to any enterprise backend: a SaaS product your organization buys, an internal line-of-business API, a set of servers behind an enterprise gateway, or an MCP App that renders an interactive dashboard inside Copilot.

It covers:

1. [Context and forces](#1-context-and-forces)
2. [Pattern taxonomy](#2-pattern-taxonomy)
3. [Reference architecture](#3-reference-architecture)
4. [Registration surfaces](#4-registration-surfaces)
5. [Package anatomy](#5-package-anatomy)
6. [Tool discovery at the protocol level](#6-tool-discovery-at-the-protocol-level)
7. [Latency, rate limits, and caching](#7-latency-rate-limits-and-caching)
8. [The 8,000-character instruction budget](#8-the-8000-character-instruction-budget)
9. [Progressive disclosure with `agent_skills`](#9-progressive-disclosure-with-agent_skills)
10. [Action safety and the confirmation model](#10-action-safety-and-the-confirmation-model)
11. [Hosting, identity, and observability](#11-hosting-identity-and-observability)
12. [Security model](#12-security-model)
13. [Anti-patterns](#13-anti-patterns)
14. [Platform limits reference](#14-platform-limits-reference)

Schema versions used throughout: app manifest **1.29** (1.27+ for `agentConnectors`), declarative agent **v1.8**, plug-in manifest **v2.4**, MCP Apps extension **stable v1 (2026-01-26)**. Copilot extensibility features move quickly. Re-verify every limit in this document against the schema and toolkit release you ship with.

Related documents:

- [authentication-paths.md](authentication-paths.md): Paths A1, A2, A3, B, and C, with end-to-end sequence diagrams.
- [mcp-apps-and-interactive-widgets.md](mcp-apps-and-interactive-widgets.md): `ui://` widgets, the renderer sandbox, CSP, CORS, and the widget bridge.
- [troubleshooting-and-diagnostics.md](troubleshooting-and-diagnostics.md): a stage-by-stage diagnostic playbook.
- [decisions/0001-da-plugin-and-discovery-modes.md](decisions/0001-da-plugin-and-discovery-modes.md): why declarative-agent plug-ins are the default.

---

## 1. Context and forces

An organization has capabilities behind HTTP APIs and wants Microsoft 365 Copilot users to reach them in natural language without leaving Copilot. MCP gives a standard contract (`initialize`, `tools/list`, `tools/call`, `resources/read`) that one server can implement once and many hosts can consume. Microsoft 365 Copilot consumes remote MCP servers over **Streamable HTTP**, through an app package that tells Copilot where the server is, how to authenticate, and how much of its surface to expose.

The design has to balance these forces:

| Force | Tension |
|---|---|
| **Reach versus control** | A tenant-wide connector reaches every Copilot surface. A declarative agent gives you instructions, skills, and a curated persona, but users must open that agent. |
| **Freshness versus governance** | Dynamic discovery always reflects the live server. Pinned discovery gives a reviewed, versioned tool surface that changes only when the package changes. |
| **Instruction budget versus behavior** | Instructions are capped at 8,000 characters. Everything the agent must know has to fit, be moved to skills, or be moved into tool descriptions. |
| **Identity fidelity versus setup cost** | Per-user delegated tokens preserve the backend's authorization model but require an OAuth or Entra registration per server. Anonymous access is trivial to set up but cannot authorize anything. |
| **Autonomy versus safety** | Read-only tools should run without friction. Write tools need human confirmation, server-side gating, and audit, and the platform's confirmation behavior must be understood precisely. |
| **Server count versus sign-in friction** | Each authenticated server adds a consent or sign-in step. Several servers in one agent can produce sign-in prompts for servers the user did not ask about. |
| **Rich UI versus portability** | MCP Apps render interactive widgets, but they run in a sandboxed, null-origin iframe with a strict CSP and a subset of the bridge API. |

---

## 2. Pattern taxonomy

Most enterprise integrations fall into one of four patterns. They share the same building blocks (app package, plug-in manifest, MCP server, authentication registration) but differ in scope, ownership, and governance.

### 2.1 Pattern 1: Single-Domain Departmental Agent

One declarative agent fronts one or two MCP servers that belong to a single business domain, for example an HR policy assistant over an internal HR API, or a field-service agent over a work-order system.

- **Owner:** the same team owns the agent and the server.
- **Discovery:** dynamic. The team can change server annotations and descriptions directly.
- **Authentication:** Path A3 (Microsoft Entra SSO with on-behalf-of) for internal APIs; Path A1 if the backend has its own OAuth server.
- **Instructions:** short. Skills hold the two or three domain workflows.
- **Strengths:** fastest to build, simplest to troubleshoot, one sign-in.
- **Watch for:** scope creep. When a second domain appears, decide whether it is a new agent or a move to Pattern 2.

### 2.2 Pattern 2: Multi-Server Enterprise Workbench

One declarative agent orchestrates several MCP servers (CRM, knowledge base, catalog, ticketing) so that a single conversation can span systems: "Review Fabrikam's account, find the renewal-pricing guidance, and shortlist add-ons."

- **Owner:** a platform or product team owns the agent. Server owners vary.
- **Discovery:** mixed. Dynamic for servers the agent team owns; pinned for servers owned by others or under change control.
- **Authentication:** mixed per server (A1, A2, A3, B, or C). Each `ai-plugin.json` carries its own `auth`.
- **Instructions:** a routing table (intent to server) plus a skill router. Cross-server workflows live in skills.
- **Limits:** at most 10 plug-in actions per agent. Group related tools behind fewer servers, or front several backends with one MCP gateway.
- **Watch for:** multi-server sign-in prompts (see [troubleshooting](troubleshooting-and-diagnostics.md#5-multi-mcp-sign-in-behavior)), and per-turn `tools/list` fan-out across every server.

### 2.3 Pattern 3: ISV Partner Plug-in

A software vendor ships an MCP server for its SaaS product, plus an app package that customer tenants install.

- **Owner:** the vendor owns the server and the package. The customer tenant owns installation, policy, and consent.
- **Discovery:** pinned is preferred for a published product, because customers review a fixed tool surface per version. Dynamic is acceptable for preview channels.
- **Authentication:** provider OAuth: Path A1 (static registration with PKCE, including a public client with no secret), or Path A2 when the SaaS authorization server supports dynamic client registration. Use Path A3 only when the product's API is registered in Microsoft Entra ID. The OAuth registration must be usable from customer tenants:
  - **Publisher-owned registration** with target audience set to any tenant (`AnyTenant`), so one `reference_id` works everywhere, or
  - **Customer-owned registration**: each customer registers the vendor's OAuth client in its own tenant and rebuilds the package with its own `reference_id`.
  A registration scoped to the home tenant only (`HomeTenant`) fails in every other tenant with a pre-connection 404.
- **Strengths:** one server serves every tenant and every MCP-capable host.
- **Watch for:** per-tenant data isolation on the server (`tid` claims, tenant-scoped storage), version skew between the pinned catalog and the server, and support boundaries.

### 2.4 Pattern 4: Tenant-Wide `agentConnectors` for Cowork

The app manifest (v1.27+) declares `agentConnectors[]` with `toolSource.remoteMcpServer`. The server becomes available to Cowork and other Copilot surfaces without a dedicated agent persona.

- **Owner:** a tenant administrator approves and deploys it.
- **Discovery:** set `mcpToolDescription.file` for a pinned catalog. In manifest 1.29 the `file` property is required whenever `mcpToolDescription` is present; omit the whole object for dynamic discovery where the schema allows it.
- **Authentication:** `None`, `OAuthPluginVault`, `ApiKeyPluginVault`, `DynamicClientRegistration` (RFC 7591), or `AzureKeyVault`. The last two are available only on this surface, not in plug-in manifests.
- **Strengths:** broadest reach; no agent switch for users.
- **Watch for:** no agent instructions or skills apply, so tool descriptions carry all of the guidance; the blast radius is the whole tenant; write tools need especially strong server-side gating.

### 2.5 Selection matrix

| Question | P1 Departmental | P2 Workbench | P3 ISV Plug-in | P4 `agentConnectors` |
|---|---|---|---|---|
| Users open a named agent? | Yes | Yes | Yes (optionally also P4) | No |
| Number of MCP servers | 1-2 | 3-10 | 1 per product | Any (one connector each) |
| Who owns the server? | Agent team | Mixed | Vendor | Server team |
| Agent instructions and skills | Yes | Yes (essential) | Yes | Not applicable |
| Preferred discovery | Dynamic | Mixed | Pinned | Pinned |
| Typical auth | A3 or A1 | Mixed | A1, A2, A3 | Any, including DCR |
| Main risk | Scope creep | Sign-in friction, fan-out | Cross-tenant registration | Tenant-wide blast radius |

The patterns compose. A common rollout is P1 for a pilot, P2 when adjacent systems join, and P4 for the subset of read-only tools that should be available everywhere.

---

## 3. Reference architecture

```mermaid
flowchart LR
    U[User in Microsoft 365 Copilot] --> DA[Declarative agent<br/>instructions + skills]
    DA --> PA[ai-plugin-oauth.json<br/>namespace servicecrm]
    DA --> PB[ai-plugin-entra-sso.json<br/>namespace serviceknowledge]
    DA --> PC[ai-plugin-anonymous.json<br/>namespace servicecatalog]
    AC[agentConnectors in manifest.json] -. tenant-wide .-> PC2[Catalog connector]
    PA -->|OAuthPluginVault| V[(Token vault)]
    PB -->|OAuthPluginVault, Entra SSO| V
    V --> SA[Server A: CRM MCP<br/>provider OAuth]
    V --> SB[Server B: Knowledge MCP<br/>Entra JWT validation]
    PC --> SC[Server C: Catalog MCP<br/>anonymous + MCP App]
    PC2 --> SC
    SC -. ui:// resource .-> W[Widget renderer<br/>sandboxed iframe]
```

Responsibilities by layer:

| Layer | Owns | Does not own |
|---|---|---|
| App package (`manifest.json`) | App identity, version, icons, valid domains, agent and connector declarations | Secrets, tokens, tool behavior |
| Declarative agent | Persona, routing instructions, starters, skills, the list of actions | Authorization decisions |
| Plug-in manifest | Server URL, auth type and `reference_id`, exposed functions, confirmation text | Token issuance |
| Token vault | OAuth flows, token storage and refresh, attaching bearer tokens | What the token allows |
| MCP server | Token validation, authorization, tool behavior, side-effect gating, audit | The user's Copilot session |
| Widget | Presentation and user gestures | Authority over writes (the server re-checks everything) |

---

## 4. Registration surfaces

| | Declarative-agent plug-in | Tenant-wide `agentConnectors` | Federated Copilot connector |
|---|---|---|---|
| Declared in | `declarativeAgent.json` `actions[]` → `ai-plugin.json` v2.4 `runtimes[].type: "RemoteMCPServer"` | `manifest.json` (v1.27+) `agentConnectors[].toolSource.remoteMcpServer` | Microsoft 365 admin center |
| Scope | One agent | Tenant: Cowork and other Copilot surfaces | Tenant |
| Instructions and skills | Yes | No | No |
| Auth types | `None`, `OAuthPluginVault`, `ApiKeyPluginVault` | Those plus `DynamicClientRegistration`, `AzureKeyVault` | Connector-specific |
| Pinned catalog | `spec.mcp_tool_description.file` + `functions[]` | `mcpToolDescription.file` | Not applicable |
| Tool invocation in validation testing | Reliable (dynamic and pinned) | Reliable for pinned connectors | Showed "connected" but no `tools/call` was ever sent |
| Recommendation | **Default** | Additive, for broad read-only reach | Do not use for action-oriented tools until verified in your tenant |

Treat `ApiKeyPluginVault` for MCP as unvalidated in this pattern. If a backend accepts only an API key or a custom header, put an OAuth gateway in front of it (see [authentication-paths.md](authentication-paths.md#8-the-client-id-no-secret-classification-matrix)).

---

## 5. Package anatomy

```text
appPackage/
├── manifest.json                 # app manifest 1.29: identity, icons, validDomains,
│                                 # copilotAgents.declarativeAgents[], optional agentConnectors[]
├── declarativeAgent.json         # DA v1.8: instructions, starters, actions[], agent_skills[]
├── ai-plugin-oauth.json          # plug-in v2.4, Server A, OAuthPluginVault (A1), pinned functions[]
├── ai-plugin-entra-sso.json      # plug-in v2.4, Server B, OAuthPluginVault (Entra SSO, A3), dynamic
├── ai-plugin-anonymous.json      # plug-in v2.4, Server C, None (C, dev only), dynamic
├── mcp-tools-catalog.json        # pinned tools/list snapshot for Server A ({"tools":[...]})
├── skills/
│   ├── account-review/SKILL.md
│   └── catalog-lookup/SKILL.md
├── color.png                     # 192 x 192
└── outline.png                   # 32 x 32, transparent background, white outline
```

Packaging rules:

- Zip the **contents** of `appPackage/`, not the folder. `manifest.json` must be at the zip root.
- Every `file` reference (`declarativeAgent.json`, plug-in files, `mcp-tools-catalog.json`, skill folders) is a path relative to the zip root.
- `validDomains` lists hostnames (`crm-mcp.contoso.example.com`), not URLs. It does not register OAuth callbacks or open firewalls.
- `copilotAgents.declarativeAgents[].id` must be unique in the tenant. Reusing an ID from another installed package makes the agent silently fail to appear.
- One declarative agent per package. Several agents means several packages.
- Before you zip, search for unresolved placeholders: `REPLACE_WITH_`, `${{`, and `example.com`.
- Validate with the Microsoft 365 Agents Toolkit CLI: `atk validate --package-file appPackage.zip --validate-method validation-rules`.

The [samples](../samples/README.md) folder contains a complete, sanitized package that follows this layout.

---

## 6. Tool discovery at the protocol level

Microsoft 365 Copilot speaks MCP over **Streamable HTTP**: every JSON-RPC message is an HTTP `POST` to the server URL (for example `https://catalog-mcp.contoso.example.com/mcp`). The server answers with `application/json` or with a `text/event-stream` that carries the JSON-RPC response. Authenticated servers receive `Authorization: Bearer <token>` on **every** request, including `initialize` and `tools/list`.

Common request headers:

```http
POST /mcp HTTP/1.1
Host: catalog-mcp.contoso.example.com
Content-Type: application/json
Accept: application/json, text/event-stream
Authorization: Bearer eyJ0eXAiOiJKV1QiLCJhbGciOiJSUzI1NiJ9...   (authenticated servers only)
Mcp-Session-Id: 6f1c...                                          (after initialize, stateful servers only)
MCP-Protocol-Version: 2025-06-18                                 (after initialize)
```

The `clientInfo`, `protocolVersion`, and capability values below are illustrative. The host sets them, and they change over time. Your server must negotiate versions instead of hard-coding one.

### 6.1 Dynamic discovery

Configuration: `"functions": []`, `"run_for_functions": ["*"]`, and no `mcp_tool_description`.

Observed behavior: on **every turn**, for **every configured server**, Copilot runs `initialize` → `notifications/initialized` → `tools/list`, even when the user's request concerns only one server. In one eight-turn test, a server that the user never asked about received eight `tools/list` calls and zero `tools/call` requests. Copilot can also enumerate again within a single turn. A full handshake-and-list cycle took under 1.2 s of machine time against a warm server.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Copilot as M365 Copilot orchestrator
    participant Vault as Token vault
    participant MCP as MCP server (Streamable HTTP)
    participant API as Backend API

    User->>Copilot: "Find data-protection add-ons"
    Copilot->>Vault: Get token for plug-in reference_id (authenticated servers)
    Vault-->>Copilot: Access token (or sign-in card if none)
    Copilot->>MCP: POST /mcp initialize {protocolVersion, capabilities, clientInfo}
    MCP-->>Copilot: 200 result {protocolVersion, capabilities.tools, serverInfo} + Mcp-Session-Id
    Copilot->>MCP: POST /mcp notifications/initialized
    MCP-->>Copilot: 202 Accepted
    Copilot->>MCP: POST /mcp tools/list
    MCP-->>Copilot: 200 result {tools: [name, description, inputSchema, annotations]}
    Note over Copilot: Model plans with the live tool list,<br/>instructions, skills, and the user prompt
    Copilot->>User: Confirmation card (unless readOnlyHint is true)
    User-->>Copilot: Confirm
    Copilot->>MCP: POST /mcp tools/call {name, arguments}
    MCP->>API: Authorized backend call (on behalf of the user)
    API-->>MCP: Data
    MCP-->>Copilot: 200 result {content, structuredContent, _meta, isError}
    Copilot->>User: Grounded answer (and widget if _meta.ui.resourceUri is set)
    Note over Copilot,MCP: Next turn: initialize + tools/list run again for every server
```

**`initialize` request and response**

```json
{
  "jsonrpc": "2.0",
  "id": 0,
  "method": "initialize",
  "params": {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": { "name": "copilot-host", "version": "x.y.z" }
  }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": 0,
  "result": {
    "protocolVersion": "2025-06-18",
    "capabilities": { "tools": { "listChanged": false }, "resources": {} },
    "serverInfo": { "name": "contoso-catalog", "version": "1.4.0" },
    "instructions": "Public product catalog. Read tools are safe; save_shortlist writes user state."
  }
}
```

**`notifications/initialized`** has no `id` and gets no JSON-RPC response. Answer it with HTTP `202 Accepted`:

```json
{ "jsonrpc": "2.0", "method": "notifications/initialized" }
```

**`tools/list` request and response**

```json
{ "jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {} }
```

```json
{
  "jsonrpc": "2.0",
  "id": 1,
  "result": {
    "tools": [
      {
        "name": "search_catalog",
        "title": "Search catalog",
        "description": "Search the public product catalog by keyword. Returns up to 25 products.",
        "inputSchema": {
          "type": "object",
          "properties": {
            "query": { "type": "string", "description": "Keywords to search for." },
            "top": { "type": "integer", "minimum": 1, "maximum": 25, "default": 10 }
          },
          "required": ["query"],
          "additionalProperties": false
        },
        "annotations": {
          "title": "Search catalog",
          "readOnlyHint": true,
          "openWorldHint": false
        },
        "_meta": { "ui": { "resourceUri": "ui://contoso/catalog-board" } }
      }
    ]
  }
}
```

**`tools/call` request and response**

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "method": "tools/call",
  "params": { "name": "search_catalog", "arguments": { "query": "data protection", "top": 5 } }
}
```

```json
{
  "jsonrpc": "2.0",
  "id": 2,
  "result": {
    "content": [
      { "type": "text", "text": "Found 3 products for 'data protection' (receipt 9b2e...)." }
    ],
    "structuredContent": {
      "view": "catalog_results",
      "query": "data protection",
      "items": [ { "sku": "DP-100", "name": "Backup Essentials", "price": 12.0 } ]
    },
    "_meta": { "ui": { "resourceUri": "ui://contoso/catalog-board", "title": "Catalog results" } },
    "isError": false
  }
}
```

Key points:

- `content` is the part the model reads. Never leave it empty, even when `structuredContent` carries the data.
- Return refusals and validation failures as `isError: true` with a readable `content` message, not as JSON-RPC protocol errors. Reserve protocol errors (`-32601` method not found, `-32602` invalid params) for malformed requests.
- In dynamic mode the **tool description becomes the confirmation text** for write tools, so write it for the person approving the action.
- Dynamic discovery is subject to platform validation. A newly deployed tool is not guaranteed to appear in the very next turn, and a schema the host rejects can make the tool, or the whole server, invisible. See [troubleshooting](troubleshooting-and-diagnostics.md#4-stage-3-toolslist-schema-rejection).

### 6.2 Pinned discovery

Configuration: `functions[]` in the plug-in manifest lists name, description, parameters, and optional `capabilities`. `spec.mcp_tool_description.file` points to a JSON file in the package that mirrors a `tools/list` result (`{"tools": [...]}`), including `annotations`. `run_for_functions` lists the function names explicitly.

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Copilot as M365 Copilot orchestrator
    participant Pkg as App package (installed)
    participant MCP as MCP server
    participant API as Backend API

    Note over Copilot,Pkg: At install or update time
    Pkg-->>Copilot: functions[] + mcp-tools-catalog.json (schemas, annotations, confirmation text)
    User->>Copilot: "Save DP-100 and DP-200 to my 'Q3 renewal' shortlist"
    Note over Copilot: Model plans with the packaged schema.<br/>No discovery round-trip is needed for planning.
    Copilot->>User: Adaptive Card from capabilities.confirmation ("Save shortlist?")
    User-->>Copilot: Confirm
    Copilot->>MCP: POST /mcp initialize
    MCP-->>Copilot: 200 result + Mcp-Session-Id
    Copilot->>MCP: POST /mcp notifications/initialized
    MCP-->>Copilot: 202 Accepted
    Copilot->>MCP: POST /mcp tools/call {name: "save_shortlist", arguments}
    MCP->>MCP: Re-validate arguments and side-effect gate
    MCP->>API: Write
    API-->>MCP: OK
    MCP-->>Copilot: 200 result {content, structuredContent}
    Copilot->>User: "Saved 2 items to 'Q3 renewal' (receipt ...)"
```

Whether the host also sends `tools/list` in pinned mode is an implementation detail. Do not depend on it either way: your server must answer `tools/list` correctly, and its live schema must stay compatible with the packaged one.

### 6.3 Dynamic versus pinned

| Concern | Dynamic | Pinned |
|---|---|---|
| Source of truth | Live `tools/list` | Package (`functions[]` + catalog file) |
| Per-turn discovery traffic | `initialize` + `tools/list` per server per turn | None needed for planning |
| Tool change rollout | Immediate on server deploy (subject to validation) | Package version bump and reinstall |
| Confirmation text | The tool description | `capabilities.confirmation` Adaptive Card |
| `security_info` metadata | Not available | `capabilities.security_info.data_handling` |
| Expose a subset of server tools | No; every tool is visible | Yes; list only what you want |
| Governance and review | Server release process | Package review, per version |
| Drift risk | None | Catalog and server can diverge; regenerate in CI |
| Best for | Development, single-owner servers | Production, third-party servers, ISV packages, write tools needing custom cards |

A hybrid is common: pin the governed write-capable server and keep read-only internal servers dynamic.

### 6.4 Generating and checking the pinned catalog

Generate the catalog from the live server in CI so that it never drifts:

1. Start the server (or point at staging) and send `initialize`, `notifications/initialized`, and `tools/list`.
2. Write `result` (the object with `tools`) to `mcp-tools-catalog.json`, sorted by tool name.
3. Regenerate `functions[]` from the same data: `name`, `description`, and `parameters` = `inputSchema`. Keep any hand-written `capabilities` blocks.
4. Fail the build when the diff against the committed file is non-empty and the package `version` was not bumped.
5. Check that every `run_for_functions` entry exists in `functions[]`, and every function exists in the catalog.

---

## 7. Latency, rate limits, and caching

Because dynamic discovery fans out to every server on every turn, a slow or throttled server degrades the whole agent, not just its own tools.

### 7.1 Budget per turn

| Step | Target (p95) | Why |
|---|---|---|
| TLS + `initialize` | < 150 ms | Paid once per server per turn |
| `notifications/initialized` | < 20 ms | Return 202 immediately; do no work |
| `tools/list` | < 100 ms | Paid for every server, including unused ones |
| `tools/call` (read) | < 2 s | User-perceived latency |
| `tools/call` (write) | < 5 s | Confirmation already added human latency |
| Widget resource (`resources/read`) | < 300 ms | Panel stays blank while loading |

### 7.2 Server-side rules

- **Make `tools/list` static.** Build the tool list once at startup. Do not call the backend, the database, or a feature-flag service inside `tools/list`. If tools vary per user, derive the filter from token claims already in memory.
- **Make `initialize` cheap.** No backend calls, no token exchange. Validate the bearer token against cached JWKS (refresh at most hourly).
- **Prefer stateless Streamable HTTP when scaling out.** With several replicas and no session affinity, a stateful server can receive a `tools/call` on a replica that never saw the session's `initialize`. Either run stateless (for example `FastMCP(..., stateless_http=True)` in the Python SDK) or configure sticky sessions or a shared session store.
- **Keep replicas warm.** Set minimum replicas to at least 1. A cold start inside `tools/list` makes every turn slow.
- **Separate rate-limit buckets.** Give discovery methods (`initialize`, `notifications/initialized`, `tools/list`) a generous bucket sized for `turns per minute × 2` per user, and give `tools/call` its own stricter, per-tool bucket. Throttling `tools/list` hides tools from the model, and the agent then claims it cannot help.
- **Return 429 with `Retry-After`** for `tools/call` throttling, and put a readable explanation in the response body. Never throttle discovery for a well-behaved client.
- **Cache backend reads, not tool lists per user.** Use short-TTL caches (30-120 s) for reference data such as catalogs, and include the cache age in `structuredContent` so the widget can show it.

### 7.3 Client-side (package) levers

- Fewer servers per agent means less fan-out. Front several backends with one MCP gateway when they share an auth model.
- Pin servers that rarely change. Pinning removes discovery from the planning path.
- Keep tool counts per server moderate (roughly 5-20). Large tool lists cost tokens on every turn and reduce selection accuracy.

---

## 8. The 8,000-character instruction budget

`instructions` in `declarativeAgent.json` is capped at **8,000 characters** (characters, not bytes or tokens). Other text caps that interact with it: `description` ≤ 1,000 characters, `conversation_starters` ≤ 12, `actions` ≤ 10.

### 8.1 What belongs where

| Content | Put it in | Why |
|---|---|---|
| Persona, scope, tone | Instructions | Always needed |
| Which server handles which intent | Instructions (routing table) | Needed before tool selection |
| Which skill handles which workflow | Instructions (skill router) + skill `description` | Activation depends on both |
| Universal safety rules (confirm writes, never invent IDs) | Instructions | Must apply everywhere |
| Multi-step workflows, templates, examples | `SKILL.md` | Loaded only when relevant |
| What a single tool does and when to use it | Tool `description` | Travels with the tool, including to other hosts |
| Parameter formats and constraints | `inputSchema` (`description`, `enum`, `pattern`, `minimum`) | Machine-enforced, not prose |
| Authorization rules | MCP server | Instructions guide the model; they do not enforce anything |

### 8.2 Allocation table (Pattern 2 workbench, three servers, two skills)

| Section | Allocation | Target | Contents |
|---|---|---|---|
| Identity and scope | 5% | 400 | Who the agent is, who it serves, what is out of scope |
| Server routing table | 15% | 1,200 | One line per server: domain, entity types, "use for", "not for" |
| Skill router | 12% | 950 | One line per skill: trigger phrases, skill name, and fallback |
| Safety and write rules | 12% | 950 | Look up before update, confirm writes, cite records, never invent IDs |
| Answer format and citations | 8% | 650 | Structure, tables, record links, receipts |
| Clarification and fallback | 8% | 650 | When to ask, what to do when no skill or tool fits |
| Cross-server orchestration rules | 10% | 800 | Order of operations, joining keys between systems |
| **Subtotal** | **70%** | **5,600** | |
| **Reserve** | **30%** | **2,400** | Headroom for fixes found in testing |

Ship at or below about 6,000 characters. The reserve is where fixes for real failures go; an agent that ships at 7,900 characters cannot be fixed without deleting something.

### 8.3 Allocation by pattern

| Section | P1 Departmental | P2 Workbench | P3 ISV Plug-in |
|---|---|---|---|
| Identity and scope | 600 | 400 | 500 |
| Server routing | 300 | 1,200 | 300 |
| Skill router | 800 | 950 | 700 |
| Safety and write rules | 900 | 950 | 1,200 |
| Format and citations | 600 | 650 | 600 |
| Clarification and fallback | 600 | 650 | 600 |
| Cross-server orchestration | 0 | 800 | 0 |
| Reserve | 4,200 | 2,400 | 4,100 |

### 8.4 Measuring

Measure the string Copilot will see, after any templating:

```powershell
python -c "import json;d=json.load(open('declarativeAgent.json',encoding='utf-8'));print(len(d['instructions']))"
```

The Microsoft 365 Agents Toolkit can load instructions from a separate file (`"instructions": "$[file('instructions.txt')]"`). Measure the expanded text, not the reference. Add the check to CI and fail above 6,500 characters so the reserve is enforced.

### 8.5 Writing compact instructions

- Use a routing table, not paragraphs: `CRM (servicecrm): accounts, contacts, opportunities. Not for: articles, prices.`
- State rules once. Do not repeat "always confirm" in every section.
- Reference skills by name: "For an account review, follow the `account-review` skill."
- Leave tool mechanics to tool descriptions and schemas.
- Avoid examples in instructions. Put examples in skills, where they cost nothing until activation.

---

## 9. Progressive disclosure with `agent_skills`

`declarativeAgent.json` v1.8 accepts `"agent_skills": [{ "folder": "skills/account-review" }, ...]`. Each folder contains a `SKILL.md` with YAML front matter (`name`, `description`) and a Markdown body. This combination was validated with app manifest 1.29, DA v1.8, and Agents Toolkit packaging.

### 9.1 Runtime model

Skills follow a three-level progressive-disclosure model:

| Level | What is loaded | When | Cost |
|---|---|---|---|
| 1. Metadata | `name` and `description` from each skill's front matter | Every turn, for every skill | A few hundred characters per skill |
| 2. Body | The full `SKILL.md` Markdown body | When the model decides the skill matches the request | Paid only on activation |
| 3. Referenced files | Additional files the body points to | When the body says to read them | Paid only when needed |

```mermaid
sequenceDiagram
    autonumber
    actor User
    participant Orchestrator as Copilot orchestrator
    participant Skills as Skill store (package)
    participant MCP as MCP servers

    Orchestrator->>Skills: Load metadata (name + description) for all skills
    User->>Orchestrator: "Prepare an account review for Fabrikam"
    Orchestrator->>Orchestrator: Match request against instructions' skill router + skill descriptions
    alt Match found
        Orchestrator->>Skills: Load skills/account-review/SKILL.md body
        Orchestrator->>MCP: Step 1: servicecrm get_account
        Orchestrator->>MCP: Step 2: servicecrm list_opportunities
        Orchestrator->>MCP: Step 3: serviceknowledge search_articles
        Orchestrator->>User: Review in the skill's output format
    else No match
        Orchestrator->>User: Answer from instructions and tools only
    end
```

The practical consequence: the `description` field is the **only** part of a skill that the model sees before activation. A skill with a weak description never loads, however good its body is.

### 9.2 Preventing silent non-activation

Skills fail silently. Nothing in the UI says "skill not loaded", and the agent still produces a plausible answer without it. Guard against that as follows.

| Cause | Symptom | Fix |
|---|---|---|
| Vague `description` ("Helps with accounts") | Skill never loads | Write "Use when the user asks to review, summarize, or prepare a QBR for a customer account" with the user's verbs and nouns |
| Instructions don't mention the skill | Loads inconsistently | Add a skill-router line in the instructions naming the skill |
| Two skills with overlapping descriptions | Wrong skill loads | Make triggers disjoint; add "Not for: ..." to each description |
| Invalid YAML front matter (tabs, unquoted colons) | Skill ignored | Lint front matter in CI; quote strings that contain colons |
| `folder` path wrong, or folder not in the zip | Skill ignored, no validation error | List the zip contents in CI; compare against `agent_skills[].folder` |
| Body references tools by a name the model does not see | Steps skipped | Use the exact tool names from `tools/list` |
| Body too long | Steps truncated or ignored | Keep the body under roughly 3,000 characters; move reference data to Level 3 files |

**Marker banner.** While testing, make the first line of each skill's output a visible marker, for example `[skill: account-review v3]`. Run a fixed prompt set and check that the marker appears exactly when expected. Remove or hide the marker before production, or keep it as an HTML comment in structured output if your test harness can read it.

### 9.3 Preventing over-narrowed scope refusals

Narrow instructions are the most common cause of an agent refusing reasonable requests.

| Over-narrowed instruction | What happens | Better phrasing |
|---|---|---|
| "Only answer questions about Contoso accounts." | Refuses "What's our renewal-pricing guidance?" because it isn't an account | "You help account teams with accounts, related knowledge articles, and catalog products." |
| "Always use the account-review skill." | Forces the skill on unrelated requests, or refuses when it doesn't fit | "For account reviews, follow the account-review skill. Otherwise answer directly with the right tools." |
| "Never answer without calling a tool." | Refuses greetings, clarifications, and general questions | "Ground factual answers about records in tool results." |
| "Use only the CRM tools." | Ignores the knowledge and catalog servers | Remove it. The routing table already says which server owns which data. |

Rules of thumb:

- Reserve "never" and "only" for safety rules: never invent IDs, never write without confirmation.
- Always give a fallback: "If no skill applies, answer with the available tools and say what you couldn't do."
- Test three prompt classes for every skill: a direct trigger, a paraphrase, and an adjacent request that should **not** trigger the skill but should still be answered.

Instructions and skills shape behavior. They never enforce authorization. A user who can call a tool can phrase a request that bypasses any instruction, so every limit that matters must be enforced on the MCP server.

---

## 10. Action safety and the confirmation model

### 10.1 Two different cards

| Card | Text | Controlled by | Can it be suppressed? |
|---|---|---|---|
| **Connection consent** | "I'll connect to ... for this and future requests" with Allow / Cancel | Platform, per server connection | No. It can recur per turn and per server. |
| **Per-tool confirmation** | Confirm / Cancel, or "Review 1 of N" for several calls | `annotations.readOnlyHint` | Yes. `readOnlyHint: true` suppresses it. |

Verified behavior:

- `readOnlyHint: true` suppresses the per-tool card. In an A/B test, three annotated read calls ran in about 80 ms with no card; the same tools without the annotation produced "Review 1 of 3".
- `isNonConsequential` in the plug-in manifest is **ignored** for `RemoteMCPServer` runtimes. Do not rely on it.
- `capabilities.confirmation` changes only the **wording** of the card, and only for pinned functions. In dynamic mode, the tool description is the confirmation text.
- Cards for a batch of calls are shown before the first call, so timing cannot tell you which card was shown. Record the cards visually.
- Annotation keys are camelCase (`readOnlyHint`). A snake_case key (`read_only_hint`) is silently dropped.

Testing this requires a human in a Copilot client; the Chat API cannot invoke declarative-agent MCP tools. The [troubleshooting playbook](troubleshooting-and-diagnostics.md#8-confirmation-card-test-protocol) has the full test protocol.

### 10.2 Side-effect classes

Classify every tool before you annotate it:

| Class | Meaning | Example | `readOnlyHint` | `destructiveHint` | `idempotentHint` | Server-side gate |
|---|---|---|---|---|---|---|
| **R** | Reads only | `search_catalog`, `get_product` | `true` | n/a | n/a | Authorization only |
| **W1** | Reversible, private to the user | `save_shortlist` | `false` | `false` | `true` | Daily cap, audit |
| **W2** | Draft that becomes visible to others only when sent | `draft_order` | `false` | `false` | `true` | Daily cap, audit, returns `draft_id` |
| **W3** | Irreversible, single target | `submit_order` | `false` | `true` | `false` | Requires a valid `draft_id`; cap; audit |
| **W4** | Fan-out to many targets | Bulk notify, mass update | `false` | `true` | `false` | Requires an approval token from a separate human step; strict cap |

Set `openWorldHint: true` when the tool reaches systems outside your control (public web, external email).

### 10.3 Server-side gate rules

1. **Draft before send.** A W3 tool accepts only a `draft_id` produced by a W2 tool in the same user's context. The model cannot jump straight to an irreversible action.
2. **Approval tokens for fan-out.** W4 tools require a token issued by a separate approval step, ideally a human gesture in a widget or an external workflow.
3. **Per-user daily caps** for each write class.
4. **Kill switch.** A configuration flag disables all writes, or a class of writes, without a deploy.
5. **Audit every write** with user, tenant, tool, arguments hash, result, receipt ID, and correlation ID.
6. **Refuse with a structured payload** and `isError: true`: `{"error": "draft_required", "detail": "...", "refused_by": "side_effect_gate"}`.
7. **Re-validate everything.** Confirmation cards, widget buttons, and instructions are UX. The gate is the control.

### 10.4 Designing write tools

- Split read tools from write tools. Never put a write behind a `mode` parameter on a read tool.
- Avoid chatty writes. One `save_shortlist(name, skus[])` beats five `add_to_shortlist(sku)` calls, each with its own card.
- Write descriptions for the approver: "Saves the listed SKUs to a named shortlist visible only to you. Overwrites a shortlist with the same name."
- Return a receipt ID in `content` so users and support can match an answer to a server log line.

---

## 11. Hosting, identity, and observability

### 11.1 Hosting

| Concern | Recommendation |
|---|---|
| Compute | Azure Container Apps with minimum replicas ≥ 1, HTTP scale rule, health probes on `/healthz` |
| Ingress | Public HTTPS with a certificate from a public CA. Copilot calls from Microsoft's cloud, so private-only endpoints are unreachable. |
| Secrets | Azure Key Vault, read through a managed identity. No secrets in images or app settings. |
| Backend identity | Managed identity or on-behalf-of flow for backend calls. Never forward the Copilot-issued token to a different audience. |
| Host allow-list | `ALLOWED_HOSTS` set to the public hostname(s); anything else gets HTTP 421 (DNS-rebinding protection) |
| CORS | Allow the widget renderer origin only if the server serves MCP App resources that need it (see the [MCP Apps guide](mcp-apps-and-interactive-widgets.md)) |

### 11.2 Observability

Log one structured line per JSON-RPC message, keyed by a correlation ID:

```json
{
  "ts": "2026-10-20T15:04:05.120Z",
  "event": "mcp_request",
  "correlation_id": "c0a8...",
  "mcp_session_id": "6f1c...",
  "method": "tools/call",
  "tool": "save_shortlist",
  "user_oid_hash": "sha256:...",
  "tid": "REPLACE_WITH_TENANT_ID",
  "status": "ok",
  "is_error": false,
  "latency_ms": 184,
  "receipt": "9b2e6a1c-..."
}
```

Diagnose by stage from the log alone:

| What the log shows | Meaning |
|---|---|
| No `mcp_request` at all | Nothing reached the server: vault lookup, DNS, TLS, or ingress problem |
| `initialize` but no `tools/list` | Handshake failed or was rejected (version, 401, 421) |
| `tools/list` only, every turn | Tools discovered but never selected: description, instructions, or refusal problem |
| `tool_called` with `is_error: true` | The tool ran and refused or failed; read `detail` |
| `tool_called` with `is_error: false` | The tool ran; any problem is in rendering or answer composition |

Return the receipt ID in `content` so a user's screenshot maps to a log line.

---

## 12. Security model

| Threat | Control |
|---|---|
| Token for another API replayed at your server | Validate `aud`, `iss`, `tid`, signature, and expiry on every request |
| Token without delegated permission | Require `scp` to contain the expected scope (for example `access_as_user`) or the calling app (`azp`/`appid`) to be allow-listed |
| Cross-tenant access (ISV) | Pin or allow-list tenant IDs; partition data by `tid` |
| DNS rebinding against a local or internal server | Check `Host` against `ALLOWED_HOSTS`; return 421 otherwise |
| Prompt injection from backend data | Treat tool output as data. Do not let returned text trigger writes; writes need confirmation and the server gate. |
| Model-initiated destructive actions | Side-effect classes, draft-before-send, approval tokens, caps, kill switch |
| Widget abuse | Widget is a view; the server re-checks every `callServerTool`. Strict CSP, no external scripts. |
| Over-broad reach | Prefer declarative-agent scope over tenant-wide connectors for write tools |
| Secret leakage | Key Vault and managed identity; no secrets in the package (`reference_id` only) |

---

## 13. Anti-patterns

| Anti-pattern | Why it fails | Instead |
|---|---|---|
| One plug-in file with several wildcard runtimes | Only one runtime per plug-in file can claim `*`, and a runtime cannot claim functions already claimed by another | One plug-in file per server, each with a unique `namespace` |
| Calling the backend inside `tools/list` | Runs for every server on every turn | Static tool list built at startup |
| Encoding authorization in instructions | Instructions are guidance, not enforcement | Enforce on the server |
| Write tools without annotations | Confirmation on every call, and no destructive hint | Classify (R, W1-W4) and annotate |
| Relying on `isNonConsequential` | Ignored for MCP runtimes | `readOnlyHint` on the server or in the pinned catalog |
| Packing workflows into instructions | Exhausts the 8,000-character budget | Skills |
| Reusing a declarative agent `id` across packages | Agent silently missing | Unique `id` per agent |
| Home-tenant-only OAuth registration for a multi-tenant product | Pre-connection 404 in customer tenants | Any-tenant registration or customer-owned registration |
| Replacing an upstream IdP callback with `oAuthRedirect` (Path A2) | Sign-in breaks | Keep both redirect URIs; see Path A2 |
| Federated connector for action tools | No `tools/call` observed | Declarative-agent plug-in |
| Combining every backend into the first pilot | Failures can't be attributed | Pilot each backend in its own build, then combine |

---

## 14. Platform limits reference

| Limit | Value | Where |
|---|---|---|
| Instructions | 8,000 characters | `declarativeAgent.json` |
| Agent description | 1,000 characters | `declarativeAgent.json` |
| Conversation starters | 12 | `declarativeAgent.json` |
| Actions (plug-ins) per agent | 10 | `declarativeAgent.json` |
| Declarative agents per package | 1 | `manifest.json` |
| Wildcard runtimes per plug-in file | 1 | `ai-plugin.json` |
| `agentConnectors[].id` | 64 characters | `manifest.json` |
| `agentConnectors[].description` | 4,000 characters | `manifest.json` |
| `authorization.referenceId` | 128 characters | `manifest.json` |
| Color icon | 192 x 192 PNG | Package |
| Outline icon | 32 x 32 PNG, transparent | Package |
| Org catalog propagation | Up to a few hours | Tenant |

Values were current for the schema versions listed at the top of this document. Treat them as planning numbers and confirm them against the current schema before release.
