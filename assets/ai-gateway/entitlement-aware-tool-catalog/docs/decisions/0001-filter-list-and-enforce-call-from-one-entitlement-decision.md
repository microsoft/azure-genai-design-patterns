# ADR 0001: Filter the list and enforce the call from one entitlement decision

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** Every MCP or tool gateway that serves `tools/list` and `tools/call` to callers with different tenants, licensed modules, feature flags or app roles, and every tool published to its catalog
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md) · [ADR 0002](0002-cache-views-by-entitlement-set-not-user.md)

## Context

A shared tool gateway serves callers who are allowed to use different tools. One tenant licenses three modules and another licenses one. One user holds the `Planner` app role and another does not. One tenant has a feature flag that unlocks an extra input property. The gateway has to answer two questions for each caller:

1. Which tools does the model see? That is the `tools/list` result.
2. Which tools may actually run? That is the `tools/call` decision.

When these questions are answered in different places, they drift apart, and each common shortcut fails in a predictable way:

- **Filter only in the agent prompt.** The system prompt says "use only the scheduling tools". The model still receives the full catalog, so every tool name, description and schema costs tokens, and an injected instruction in retrieved content can steer the model to any of them. The server enforces nothing.
- **Enforce only at call time.** Calls are safe, but the model reads, picks and fails on tools it can never use, every session. The list also reveals which modules exist for other tenants.
- **Filter only at list time.** The model does not see the hidden tool, but MCP does not require a client to list before it calls. A guessed, remembered or injected name runs.
- **Return different errors for "hidden" and "does not exist".** A caller can enumerate the catalog by probing names and comparing error shapes.

The inputs to the decision also need one trusted source. [Request Context Is Not Authorization](../../../../agent-runtime/request-context-is-not-authorization/README.md) covers why headers, arguments, session state and model output cannot carry the tenant or the caller's rights.

## Decision

1. **One decision function serves both paths.** `denial_reason(tool, entitlements, roles)` returns `not_entitled`, `role_required` or `None`, in the order described in [Architecture §5.2](../architecture.md#52-decision-order). `build_view` calls it to drop tools from `tools/list`. `_tools_call` calls it again on every `tools/call`. The call path never reads the cached view, the session or the client's earlier list. It looks the tool up in the governed catalog and evaluates the decision from scratch.
2. **Inputs come only from the validated token and the registry.** The tenant comes from the `tid` claim, resolved through the entitlement registry to an internal tenant with its modules, flags and version. Roles come from the `roles` claim. Tenant hint headers (`X-Tenant-Id` and similar) are ignored and their names are audited as `client_tenant_hint_ignored`. A `tenant_id` argument is rejected because no minimized schema contains it. Sessions are bound to the tenant and subject that created them.
3. **Deny by default, and treat an unknown tool like a hidden one.** A name that is not in the catalog gets reason `unknown` before the decision function runs. In the default uniform mode, hidden and unknown tools both return `-32602` with `Unknown tool: <name>`, or `Unknown tool` when the name is not a valid tool name. The backend is not called.
4. **Uniform errors are the default, and explicit disclosure is opt-in.** With `disclosure="explicit"`, an unentitled tool marked `discoverable` returns `-32001` `Tool not available for this caller` with `{"reason": ...}` in `data`. Tools not marked `discoverable` still get the uniform error. The gateway refuses to start in explicit mode when the Domain Pack policy sets `allow_explicit_disclosure` to false, as the healthcare pack does. See [Authentication and governance §9](../authentication-and-governance.md#9-disclosure-policy).
5. **Arguments are validated against the caller's minimized schema.** A property removed from the caller's view because the tenant lacks its feature flag is also refused at call time. The mismatch is audited as `call_rejected` with reason `invalid_arguments`, the response is a tool result with `isError: true`, and the backend is not called.
6. **The client sees a uniform answer, and the audit keeps the real reason.** `call_denied` records `unknown`, `not_entitled`, `role_required` or `stale_entitlements`, with the tenant, principal class, tool, entitlement and catalog versions, freshness and subject. `tools/list` is audited as `list_filtered` with the view version and the visible and hidden counts. Argument values are never logged, and a tool value that is not a valid name is recorded as a hash. The full response table is in [Architecture §14.6](../architecture.md#146-responses-by-outcome).

## Consequences

- **Positive:** List and call cannot disagree, because one function decides both. A cache or session bug can show the wrong list, but it cannot run a hidden tool. Scenario 5 shows this directly: with the insecure view key the list leaks a hidden tool, and the call is still denied.
- **Positive:** The model sees only tools it can run. Prompts are smaller, fewer turns are wasted on denials, and an injected instruction can reach only tools the caller was already entitled to use.
- **Positive:** Guessing names gains nothing. Hidden, unknown and malformed names return the same shape, so the catalog cannot be enumerated from outside (scenario 2).
- **Positive:** The design is testable. Removing either half fails the suite: the `call-skips-entitlement` mutant is caught by scenarios 2, 3, 5, 9 and 13, and the `list-unfiltered` mutant by scenarios 1, 3, 4, 5, 7 and 8. See [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md).
- **Negative:** Every call resolves entitlements. The entitlement cache in [ADR 0002](0002-cache-views-by-entitlement-set-not-user.md) keeps most of these lookups in memory.
- **Negative:** Uniform errors make "why can't I see this tool?" harder to answer for a legitimate user. Support works from the audit, which has the real reason. Domains where module names are public can opt into explicit disclosure per tool.
- **Negative:** Every tool needs governed metadata (module, optional feature flag, optional roles, read-only flag). Catalog lint rejects tools that break the naming and module rules (scenario 12), and publishing becomes a governed step.
- **Negative:** The decision lives in an SDK-based service, not in gateway policy. API Management still validates tokens and applies rate limits in front of it.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **Filter only in the agent prompt** | A prompt is a request to the model, not a control. The full catalog still reaches the model, costs tokens, and stays callable by any injected instruction. |
| **Enforce only at call time** | Safe, but the model wastes turns on tools it can never use, the prompt carries the whole catalog, and the list reveals modules licensed by other tenants. |
| **Filter only at list time** | MCP does not require listing before calling. A guessed or replayed name runs, and a view cache bug becomes an authorization bug. |
| **Require a tool to be listed in the session before it can be called** | Adds session state without adding safety. The session is request context, a list taken before a revocation would keep working, and clients that cache lists legitimately would fail. |
| **Rewrite `tools/list` bodies in gateway policy** | Duplicates the decision in a second language that the call path does not share, cannot minimize schemas cleanly, and breaks streaming responses. |
| **Expose the REST API as one generated MCP server** | The generated tool list is the same for every caller, so it cannot express per-tenant, per-role or per-flag catalogs. It fits single-audience tools only. |
| **One MCP server per tenant** | Isolates tenants but not roles or flags inside a tenant, multiplies deployments, and still needs a call-time check for roles. |

## Follow-ups

- Review the `discoverable` marker as part of catalog governance whenever explicit disclosure is enabled for a domain.
- Track `call_denied` counts by reason per tenant. A rise in `unknown` or `not_entitled` without a matching catalog change is a probing or prompt-injection signal.
- Map `-32001` and `-32003` to standard codes if the MCP specification defines authorization errors.
- When [Progressive Tool Loading](../../../../orchestration-and-workflows/progressive-tool-loading/README.md) is adopted, load from the caller's filtered view, never from the full catalog. Progressive loading decides when a tool reaches the model, and this decision decides which tools the caller may ever see.
