# ADR 0002: Re-check tool entitlement on every tools/call

- **Status:** Accepted
- **Date:** 2026-10-20
- **Scope:** Every MCP server behind the multi-tenant gateway whose tools are not available to every tenant, and every MCP adapter or backend that serves those tools
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [ADR 0001](0001-token-derived-tenant-identity.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md)

## Context

Contoso ERP sells products (`orders`, `field-service`, `reporting`), and tenants are entitled to different tools. Some write tools also require a user-level scope. MCP gives the gateway two natural points to apply entitlement:

- **`tools/list`**, where the server tells the client which tools exist. Filtering this response is good for usability: agents only plan with tools they can use, and tenants do not see tools they have not bought.
- **`tools/call`**, where the client invokes a tool by name.

Filtering the list is tempting as the only control, because it is visible and easy to demonstrate. It is not authorization:

- A client can call `tools/call` with any tool name without ever calling `tools/list`.
- Clients cache tool lists. After a downgrade or suspension, a cached list still contains tools the tenant no longer has.
- A tool name can leak through documentation, another tenant, a prompt, or simple guessing.
- The model, not the developer, chooses which tool to call and can be prompted to call a tool that is not in the list.
- In API Management, MCP policies apply per MCP server, not per tool, and the gateway should not rewrite streaming response bodies. Filtering `tools/list` at the gateway is therefore often impractical, while checking the tool name in a `tools/call` request body is straightforward.

## Decision

1. The gateway checks entitlement on **every** `tools/call` request: it reads `params.name` from the JSON-RPC request, confirms that the tool belongs to a product the tenant is entitled to, and confirms any additional scope the tool requires.
2. A tool outside the tenant's entitlement gets the **same response as an unknown tool**: HTTP `200` with JSON-RPC error `-32602` and the message `Unknown tool: <name>`. The response does not reveal whether the tool exists for other tenants.
3. `tools/list` is filtered to the tenant's entitlement where the platform allows it, preferably at the source (the MCP adapter or server, using the gateway's trusted tenant header) rather than by rewriting the response in gateway policy.
4. Backends still enforce tenant scope on the data a tool returns. The gateway check prevents calling the wrong tool; the backend check prevents returning the wrong data.
5. Every denial is written to the audit stream with the tenant, client application, tool, and reason, so support and security teams can diagnose what the client cannot see.

## Consequences

- **Positive:** Entitlement holds regardless of client behavior, caching, or prompt injection.
- **Positive:** The check runs on the request body, which API Management can read without affecting response streaming.
- **Positive:** Denials do not disclose the catalog of tools a tenant has not bought.
- **Negative:** The entitlement map must be available on every call, so the tenant directory must be cached and fail closed.
- **Negative:** The tool-to-product map must stay in sync with the MCP servers. A new tool that is not in the map is denied for everyone until it is added, which is the safe default but needs a release step.
- **Negative:** Support cannot rely on error text to explain a denial; they use the audit record.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **List-only filtering** | Not a control. Clients can skip or cache the list, and the model can be prompted to call unlisted tools. |
| **Backend-only checks** | Each backend team re-implements entitlement differently; denials arrive late, after limits are charged and connections are opened; no central audit of denials. Kept as defense in depth for data scope, not as the entitlement control. |
| **One MCP server per product, entitlement by product only** | Works for coarse product entitlement, and is worth combining with this decision. It does not cover per-tool scopes such as write tools, and it multiplies servers if entitlement is finer than a product. |
| **One MCP server per tenant** | Physically separates tool sets but multiplies servers, OAuth resources, policies, and catalog entries per tenant, and drifts quickly. |

## Follow-ups

- Keep the tool-to-product and tool-to-scope maps in source control alongside the policies, and validate them against each MCP server's tool list in the release pipeline.
- Implement `tools/list` filtering in the MCP adapter, keyed on the gateway's tenant header, and send `notifications/tools/list_changed` when entitlement changes.
- Keep the "non-entitled tool blocked on `tools/call` without a prior `tools/list`" negative test in every release; see [evaluation-and-troubleshooting.md](../evaluation-and-troubleshooting.md).
