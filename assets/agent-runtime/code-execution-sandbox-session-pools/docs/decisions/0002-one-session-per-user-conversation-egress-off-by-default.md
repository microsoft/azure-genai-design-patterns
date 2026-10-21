# ADR 0002: One session per user conversation, egress off by default

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md#81-session-identifier-derivation), [Egress policy](../architecture.md#11-egress-policy), [Asset README](../../README.md), [Authentication and governance](../authentication-and-governance.md#6-tenant-isolation-per-tenant-pools-or-a-shared-pool)

## Context

A dynamic session pool routes each request to a session by an identifier the caller supplies. The platform does not know who the user is: whoever presents the same identifier reaches the same sandbox, its files and its interpreter state. The identifier is therefore the isolation key between users and tenants, and choosing it is a security decision, not a convenience.

Two failure modes appeared repeatedly in preview testing. When the identifier was generated per request (or included a timestamp), every call created a new session: variables and uploaded files vanished between turns, and capacity filled with idle sessions until requests were rejected **(observed in preview; verify current behaviour)**. When the identifier was a predictable value such as a user name or a conversation ID taken from the client, anyone who could guess or replay it could read another user's working data.

Network egress is the other half of the boundary. Code that can reach the internet can send whatever it reads to an attacker, fetch a second-stage payload or install unreviewed packages. Per current documentation, session pools start with egress disabled; verify against current Microsoft Learn documentation.

## Decision

1. The broker derives the identifier server-side as an HMAC over tenant, user and conversation, keyed with a secret held in Key Vault, and renders it as an opaque value within the platform's documented length and character limits. The simulator uses a prefix plus a hex digest.
2. Tenant and user come from the validated token, never from the request body. The conversation ID comes from the agent runtime's conversation record. Clients cannot supply or override an identifier (scenario 4).
3. One conversation maps to exactly one session for its lifetime. The same user in the same conversation reuses the session (scenario 1); different users (scenario 2) and different tenants with identical user and conversation IDs (scenario 3) never share one.
4. The identifier is pinned per conversation and never includes time, nonces or request IDs. The broker records `sessionIsNew` and `stateLost` so unpinned identifiers are detectable (scenario 20).
5. Pools are created with egress disabled (scenario 7). An allowlist is an explicit, reviewed exception that lists exact host names only, rejects wildcards, and never permits metadata or local addresses (scenarios 8 and 9). Allowlist mode requires a custom virtual network and an egress firewall.
6. The broker deletes the session when the conversation closes; cooldown and maximum lifetime are the backstop.

## Consequences

### Positive

- Cross-user and cross-tenant session bleed requires breaking the HMAC key, not guessing a value.
- Conversations keep state across turns, so multi-step analysis works and capacity is not wasted.
- A compromised session cannot send data anywhere by default.

### Trade-offs

- The derivation key is a critical secret: rotation causes state loss for in-flight conversations unless the broker records a key version per conversation.
- Users cannot share a sandbox across conversations; continuing work in a new conversation means re-uploading data.
- Workloads that need external data must fetch it in the broker and upload it, or go through an allowlist review and the network work that goes with it.
- No packages can be installed at run time, which pushes package needs to a custom image ([ADR 0003](0003-built-in-pool-for-mcp-custom-container-for-packages.md)).

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| New identifier per request | Loses state every turn; fills capacity with idle sessions |
| One identifier per user across conversations | Mixes unrelated work; data from one conversation leaks into another; long-lived sessions hit maximum lifetime |
| One shared session for the whole pool or tenant | No isolation between users |
| Client-supplied identifier | Guessable and replayable; trusts the least trusted party |
| Plain hash of tenant, user and conversation | Anyone who knows the inputs can compute the identifier; HMAC adds a secret |
| Egress open by default, block known-bad hosts | Unbounded exfiltration surface; blocklists are always incomplete |
| Allowlist on by default with common package indexes | Package indexes are an exfiltration and supply-chain channel; bake packages into an image instead |
