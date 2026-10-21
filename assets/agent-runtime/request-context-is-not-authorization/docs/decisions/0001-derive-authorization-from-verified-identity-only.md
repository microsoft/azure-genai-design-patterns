# ADR 0001: Derive authorization from verified identity only

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** Every agent, tool server, MCP server, API and data store that makes an authorization decision on behalf of an agent workload, including memory, thread and retrieval stores
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md) · [ADR 0002](0002-reauthorize-at-the-resource-boundary.md)

## Context

An agent request carries far more context than a classic API call, and almost all of it looks like identity:

- **HTTP headers** forwarded by the agent host or a proxy, such as `x-user-id`, `x-tenant-id`, `x-roles` or `x-client-*`. Hosted agents on Foundry forward `x-client-*` headers and request metadata verbatim, and they can inject their own caller-id header (observed in preview; verify against current docs).
- **Payload fields and request metadata**, such as `{"tenant": "..."}` in a Responses API call.
- **Tool arguments** that the model fills in, such as `tenant_id`, `owner` or `as_role`.
- **Retrieved documents, tool results and memory**, which can state "this user is an administrator" or "the CFO pre-approved this".
- **Model output**, which can repeat any of the above as if it were fact.
- **A verified credential**: an access token whose signature, issuer, audience, lifetime and tenant the current service validated, or the runtime's own managed identity or agent identity.

Every source except the last can be set by a caller, an intermediary or an attacker who can influence the model through a prompt or a document. Prompt injection cannot be fully prevented, so any control that depends on the model behaving is a control the attacker can switch off.

Teams reach for headers and payload fields because the platform does not deliver the end user's token to a hosted agent. That pressure is real, and the asset addresses it with brokers, connection-based auth and a front-door-written context store ([architecture section 8.4](../architecture.md#84-getting-a-verifiable-user-to-the-tool)). The decision here is about what is allowed to **decide** access once a request reaches a boundary.

## Decision

1. **Subject attributes come only from verified identity at the current hop.** Tenant (`tid`), user (`oid`), roles, scopes, presenting app (`azp`) and identity type (`idtyp`) are read from a token validated by the service making the decision, or from the runtime's own attested identity. The validation rules are in [authentication and governance](../authentication-and-governance.md#4-token-validation-rules).
2. **Everything else is tainted.** Headers, payload fields, metadata, tool arguments, retrieved content, memory and model output are held in a context bag with provenance and a taint class. They may be used for display, routing hints, correlation and as **resource selectors** that the server resolves. They are never subject attributes.
3. **The PDP refuses tainted subjects.** The policy decision point accepts only a sealed verified principal. Anything else returns `TAINTED_ATTRIBUTE`. Application code cannot construct a verified principal without going through a verifier.
4. **Deny by default with reason codes.** No credential returns `NO_VERIFIED_IDENTITY`. A credential that fails validation returns `TOKEN_INVALID`. An unknown resource returns `RESOURCE_UNKNOWN`. A missing rule returns `NO_MATCHING_RULE`. There is no anonymous fallback.
5. **Resource tenant and owner come from the server-side registry.** A tenant-like argument that disagrees with the verified `tid` is denied as `TENANT_MISMATCH` and flagged `injection_suspected`.
6. **The model is never the policy decision point.** System-prompt instructions about who may see what are usability aids. They are not counted as controls in threat models or reviews.
7. **Asserted context is recorded, not trusted.** When a header or argument conflicts with verified identity, the audit record lists the key in `asserted_context_conflicts`. The value is not copied, and the conflict does not change the decision.
8. **Platform-attested values stay at their hop.** A value the agent host injects, such as a caller object id, may be used at that hop as a lookup key into a store written by an authenticated front door. Once the agent forwards it to another service, that service treats it as agent-asserted.

## Consequences

- **Positive:** Spoofed headers, injected tenant ids and model-echoed roles cannot change a decision. The reference suite shows the header-trusting baseline allowing the same attack the PEP denies.
- **Positive:** Every denial has a stable reason code, so operators can tell wiring regressions (`NO_VERIFIED_IDENTITY`), configuration problems (`TOKEN_INVALID`) and attacks (`TENANT_MISMATCH` with `injection_suspected`) apart.
- **Positive:** Security reviews become tractable. Reviewers check one verifier and one PDP per boundary instead of every place that reads a header.
- **Negative:** Every tool that holds user-scoped data must receive a verifiable user credential. For hosted agents this needs a broker, connection-based auth or a context store, which is extra work compared with forwarding a header.
- **Negative:** Some existing clients break when header-based identity stops working. The migration path in [architecture section 14](../architecture.md#14-migration-path-from-header-trusting-agents) uses shadow mode to find them first.
- **Negative:** Features that personalized behavior from headers must separate "display" uses (allowed) from "decide" uses (not allowed). That takes code review effort.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **Trust identity headers set by the agent host** | Only some headers are platform-attested, only at that hop, and the platform may identify the calling service rather than the end user. Once forwarded, the value is just another header. |
| **Trust headers if the request came from inside the network** | Network location identifies a hop, not a principal. Any compromised or misconfigured internal caller, or an injected tool call from the agent itself, can set them. |
| **Sign the context bag in the agent and trust it at tools** | The agent signs what the model and the caller gave it, so the signature proves only that the agent relayed it. It does not prove who the user is. |
| **Ask the model to enforce tenant and role rules** | Instructions are steerable by injected content. The model has no access to verified identity and no way to read live entitlements. |
| **Use the agent's own identity for all calls and filter by a user id header** | Confused deputy. The tool authorizes the agent, which can usually see every user's data, and the filter is caller-asserted. |
| **Allow anonymous access when identity is missing, for read-only tools** | Read-only tools still return tenant data. A dropped token in one hop then becomes a cross-tenant read. |

## Follow-ups

- Inventory every code path that reads identity from context, following phase 0 of the [migration path](../architecture.md#14-migration-path-from-header-trusting-agents).
- Agree with the platform team which headers, if any, are platform-attested at each hop, and record the list in the PEP configuration (`platform_attested_headers`). At a tool server reached through an agent, the expected list is empty.
- Choose the user-to-tool mechanism for each user-scoped tool: on-behalf-of through a broker, connection-based auth, or a front-door context store. See [Backend Identity Propagation](../../../../ai-gateway/backend-identity-propagation/README.md) and [MCP OAuth Front Door](../../../../ai-gateway/mcp-oauth-front-door/README.md).
- Add the spoofed-header, injected-tenant, model-echoed-role and missing-identity cases from [evaluation and troubleshooting](../evaluation-and-troubleshooting.md#3-adversarial-test-matrix) to every release gate.
- Alert on rising `asserted_context_conflicts` by caller app.
