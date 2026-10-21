# ADR 0002: Re-authorize at the resource boundary on every call

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** Every tool, MCP server, API and data store reached by an agent, including thread, memory and retrieval stores, and every cache that sits between a policy enforcement point and an entitlement source
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md) · [ADR 0001](0001-derive-authorization-from-verified-identity-only.md)

## Context

[ADR 0001](0001-derive-authorization-from-verified-identity-only.md) settles **what** may decide access: verified identity, never request context. This ADR settles **where** and **when** the decision is made.

Agent sessions are long and multi-hop. A single conversation can span many turns. Each turn can call several tools, and each tool can touch a different resource. Between two turns, things change that a session-start check cannot see:

- An administrator revokes a user's entitlement, offboards a merchant, or ends a care-team assignment. The user's access token stays valid until it expires.
- The model is steered, by a user or by injected content, toward a resource that was never part of the original request.
- A tool resolves a selector to a resource whose tenant or owner differs from what the agent assumed.

Earlier hops cannot make the authoritative decision. The front door sees the user's token, but not the final resource. The agent sees the resource selector, but only as model-influenced content, and it holds no verifiable user credential for the tool. Only the tool or resource boundary sees the verified subject, the resolved resource and the live entitlement together ([architecture section 9.1](../architecture.md#91-where-to-enforce)).

Teams are tempted to authorize once, at session start or at the front door, and carry the result forward in session state, a header or the system prompt. That is cheaper, and it fails silently. Access survives revocation, and the carried result becomes one more piece of tainted context.

## Decision

1. **Every tool or resource boundary is a policy enforcement point (PEP).** On every call, it verifies the credential presented for its own audience, resolves the resource from a server-side registry, cross-checks tenant-like arguments and asks the policy decision point (PDP). The handler runs only on `ALLOW`.
2. **No session-start snapshot.** Decisions are not carried across turns or calls in session state, headers, memory or prompts. A prior allow is never an input to a later decision.
3. **Entitlements are read live, or from a cache that revocation invalidates.** If a cache is used, revocation events invalidate entries, entries carry the store version, and the residual window is a documented risk acceptance. A cache that relies on a time-to-live alone is not acceptable for user-scoped or privileged actions ([architecture section 9.2](../architecture.md#92-what-may-be-cached)).
4. **Every decision records its versions.** Each decision and audit record carries `policy_version` and `entitlement_version`, so an allow based on stale state can be found after the fact.
5. **Fail closed.** If the entitlement store, the policy or the signing keys are unavailable, the PEP denies with a reason code. It never falls back to an allow, a snapshot or an earlier decision. Add a dedicated operational reason code for store outages to your catalog. The reference sample does not simulate an outage.
6. **Upstream checks are coarse and advisory.** The front door can reject unauthenticated callers and apply coarse role gates. The agent can pre-filter tools for usability ([Progressive Tool Loading](../../../../orchestration-and-workflows/progressive-tool-loading/README.md)). Neither replaces the PEP decision.
7. **Data-plane scoping follows the verified subject.** When the tool queries a store that supports row-level security or security trimming, the scope is set from the verified subject inside the PEP, never from a filter the model wrote.

## Consequences

- **Positive:** A revocation takes effect on the next tool call, not at the next session. The reference suite's `revocation-between-turns` scenario shows turn 2 denied with `ENTITLEMENT_REVOKED`, two live reads and a higher `entitlement_version`. The `SessionSnapshotAuthorizer` baseline still allows.
- **Positive:** A model steered to a new resource mid-session is evaluated against that resource. It does not inherit an allow granted for a different one.
- **Positive:** Audit records tie each action to the exact policy and entitlement state it was decided under, which supports incident review and access recertification.
- **Negative:** Every call pays for token validation, a registry lookup and an entitlement check. Measure that cost in your environment, and reduce it with key caching, validation-result caching within the token's own lifetime and versioned entitlement caches. Do not reduce it by skipping the check.
- **Negative:** The entitlement store becomes a runtime dependency of every tool. Its availability and its revocation event feed need owners, monitoring and alerts.
- **Negative:** Each tool team owns PEP wiring and policy. A shared library or a central authorization service keeps rules and reason codes consistent ([architecture section 9.3](../architecture.md#93-choosing-a-pdp-shape)).

## Alternatives

| Alternative | Why rejected |
|---|---|
| **Authorize once at session start and cache the result** | Revocations do not apply until the session ends. The cached result travels as session state that the model and caller can influence. |
| **Authorize only at the front door or in the agent** | Neither hop sees the resolved resource and the live entitlement together. The agent's view of the resource is model-influenced. |
| **Cache entitlements with a time-to-live only** | Access persists for the whole time-to-live after revocation, and nothing records which version was used. Acceptable only for low-risk reads, as a documented risk acceptance with versioned entries. |
| **Rely on token lifetime as the revocation mechanism** | Tokens carry roles, not fine-grained entitlements, and they stay valid for their lifetime after an entitlement changes. Continuous access evaluation helps for sign-in risk events. It does not cover application-level entitlements. |
| **Hide tools from the model instead of checking calls** | Tool pre-filtering reduces confusion and token cost, but a hidden tool can still be reached through another path, and a visible tool can still be pointed at the wrong resource. |
| **Let the data store enforce everything** | Row-level security is necessary but not sufficient. Something must set the scope from a verified subject, and not every store or API supports it. |

## Follow-ups

- Run a revocation drill for each user-scoped tool. Confirm that the first call after revocation returns `ENTITLEMENT_REVOKED` ([evaluation and troubleshooting section 6](../evaluation-and-troubleshooting.md#6-metrics-and-targets)).
- Publish revocation events from the entitlement system of record, and subscribe every entitlement cache to them.
- Add the store-outage reason code to the catalog, and alert on it separately from security denials.
- For MCP fleets behind a gateway, split coarse catalog checks at the gateway from resource-level checks at the tool. See [Entitlement-Aware Tool Catalog](../../../../ai-gateway/entitlement-aware-tool-catalog/README.md) and [Multi-Tenant MCP Gateway](../../../../ai-gateway/multi-tenant-mcp-gateway/README.md).
- Review thread, memory and retrieval stores as resources with owner-only rules, as in the `cross-tenant-memory-read` scenario ([architecture section 6.4](../architecture.md#64-memory-threads-and-retrieval-are-resources-too)).
