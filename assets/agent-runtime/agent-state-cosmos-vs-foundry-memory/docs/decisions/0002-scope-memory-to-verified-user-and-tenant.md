# ADR 0002: Scope memory and thread state to the verified user and tenant

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** Every partition key, thread owner check, memory scope, audit subject and erasure target computed by a `StateStore`, `MemoryStore`, `ContextProvider` or erasure coordinator
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md) · [ADR 0001](0001-separate-conversation-state-from-long-term-memory.md)

## Context

Memory and thread stores are multi-tenant by default: one Cosmos DB container holds every tenant's threads, and one memory store holds every user's memories, separated only by a key. Whoever chooses that key chooses whose data the agent reads.

Agent requests carry many values that look like a key:

- Headers such as `x-tenant-id`, `x-ms-client-tenant-id`, `x-user-id`, `x-agent-user-id` and `x-memory-user-id`. In some Foundry topologies the per-request memory user id can be supplied by a header (observed in preview; verify against current docs).
- Payload fields such as `tenant_id`, `user_id`, `scope`, `partition_key` and `memory_scope`.
- Tool arguments the model fills in, and text in retrieved documents or recalled memories.

Any of these can be set by a caller, an intermediary or prompt-injected content. If a partition key or memory scope is built from one of them, a forged value reads another tenant's thread or another user's memories, and the store has no way to notice. The same user object id can also exist in more than one tenant (guest accounts, multi-tenant apps), so a scope built from the user id alone merges two people's data.

[Request Context Is Not Authorization](../../../request-context-is-not-authorization/README.md) establishes the general rule ([its ADR 0001](../../../request-context-is-not-authorization/docs/decisions/0001-derive-authorization-from-verified-identity-only.md)) and lists memory, threads and retrieval as resources that need the same protection ([section 6.4](../../../request-context-is-not-authorization/docs/architecture.md#64-memory-threads-and-retrieval-are-resources-too)). This ADR applies that rule to state and memory keys.

## Decision

1. **A sealed `Principal` is the only input to key computation.** It is constructed only by a token verifier after validating signature, issuer, audience, lifetime and tenant. It exposes the verified tenant (`tid`) and user (`oid`) and nothing else that can be set by the caller.
2. **Thread partition key is `[tenantId, threadId]` with an owner check.** The tenant level comes from the principal. The thread id is server-issued. Every read compares the stored owner with the principal's user, and missing, expired and not-yours all return the same `thread_not_found` error.
3. **One memory scope per verified user per tenant.** The scope is an opaque value derived from `tid` and `oid` together. The same object id in two tenants is two scopes. In production the value is an HMAC with a key held in Key Vault; the sample uses an unkeyed hash for readability ([authentication and governance section 6](../authentication-and-governance.md#6-scope-from-verified-identity-only)).
4. **Request hints are logged by name and ignored.** A `hint_ignored` audit event lists the header or field names, never their values, and the request continues with keys from the principal.
5. **Explicit scope for managed memory.** When calling Foundry Memory, pass the scope computed from the principal. Do not rely on a user-id template that the service resolves from a caller-settable header unless a gateway strips and re-sets that header from a validated token.
6. **No user, no memory.** App-only callers get no memory scope and no user thread. A job acting for a user needs a stored, auditable delegation.
7. **Erasure targets are verified too.** A user may erase themselves. A privacy role may erase another user only in its own verified tenant. Cross-tenant and malformed targets are denied with reason codes ([architecture section 11.1](../architecture.md#111-authorization)).

## Consequences

- **Positive:** A forged tenant header, a forged memory user id or an injected `memory_scope` argument cannot change which data is read. Scenarios `02 forged_tenant_hint_ignored`, `04 memory_scoped_per_user` and the Northwind `same_user_id_separate_across_tenants` scenario prove it.
- **Positive:** Isolation does not depend on the model, the prompt or the memory service's request-resolution behavior.
- **Positive:** Audit records carry a separate pseudonymous subject reference, so investigations can correlate events without storing names or email addresses.
- **Negative:** Every path that writes or reads memory needs a validated user token at the hop that computes the key. Hosted agents may need a broker or a front-door context store to provide one.
- **Negative:** The HMAC key becomes a long-lived dependency. Rotating it changes every scope and orphans existing memories, so rotation is a planned migration with a re-keying job.
- **Negative:** Shared memory across a team or a tenant is not possible with this scope. If a workload needs shared knowledge, it belongs in a security-trimmed search index, not in user memory.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **Trust `x-memory-user-id` or a similar header for the memory scope** | Any caller or intermediary that can set the header can select another user's memories. Even when the platform sets it at one hop, it becomes caller-asserted once forwarded. |
| **One scope per tenant or per agent** | Every user in the tenant would receive every other user's preferences and summaries. Erasure for one user would require rewriting a shared scope. |
| **Scope by user object id alone** | The same object id can appear in more than one tenant, so two tenants' data would share one scope. |
| **Scope by email address or user principal name** | Mutable, reusable after an account is deleted, and personal data in plain text in every key and log line. |
| **Let the client choose or send the scope** | Moves the authorization decision to the least trusted party. Equivalent to trusting the header. |
| **Rely on the model or system prompt to keep users apart** | Instructions are steerable by injected content, and the model has no access to verified identity. |

## Follow-ups

- Find every place a partition key, memory scope or erasure target is computed and confirm it reads only from the verified principal.
- Decide where the HMAC key lives, which identity reads it and how rotation would be run, before the first memory is written.
- For each Foundry Memory binding, prove which user id the service resolved and where it came from, following [architecture section 7.3](../architecture.md#73-binding-memory-to-agents).
- Add the forged-hint, cross-tenant and same-object-id cases from [evaluation section 5](../evaluation-and-troubleshooting.md#5-adversarial-and-red-team-inputs) to every release gate.
- Alert on rising `hint_ignored` and `thread_access_denied` counts by calling app.
