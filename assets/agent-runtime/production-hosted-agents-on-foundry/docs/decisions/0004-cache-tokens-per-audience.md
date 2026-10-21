# ADR 0004: Cache tokens per audience

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md#73-token-acquisition-in-the-container), [Authentication and governance](../authentication-and-governance.md), [Asset README](../../README.md)

## Context

The agent container calls several Entra-protected dependencies, each with its own audience: model deployments, Cosmos DB, the MCP gateway, telemetry ingestion and possibly the Foundry project data plane. A token issued for one audience is rejected by the others.

Two failure patterns appear in prototypes:

- **Fetching a token on every call.** Each turn pays for token acquisition, the identity endpoint sees avoidable load, and a slow identity endpoint makes every request slow. Client-side credential creation, token acquisition and connection setup were a noticeable share of first-turn cost in testing.
- **One shared token variable.** A token cached for one audience is sent to another dependency, which fails with 401. Because the identity has the right roles, the error is misread as a role problem.

## Decision

1. Create one credential object per process (Azure Identity `ManagedIdentityCredential` or `DefaultAzureCredential` configured for managed identity) and reuse it.
2. Cache access tokens keyed by **audience** (scope). Each entry stores the token and its expiry.
3. Refresh a token when the remaining lifetime falls below a refresh skew, not when it has already expired. Requests near the boundary therefore never carry a token that expires in flight.
4. Serialize refresh per audience so concurrent requests do not stampede the identity endpoint.
5. Prefer SDK clients that accept a credential and cache internally; apply this rule explicitly for hand-written HTTP clients (gateway, custom tools).
6. Reuse HTTP clients and connection pools per dependency.
7. Never log tokens. Log the audience, expiry and cache hit or miss.
8. Never put a token in an environment variable, a file in the image, or the agent definition.

## Consequences

### Positive

- One acquisition per audience per token lifetime instead of per call.
- Audience mismatches are structurally prevented and easy to diagnose (the cache key is the audience).
- Steady-state turns do not depend on identity endpoint latency.

### Trade-offs

- A small amount of code for hand-written clients; the simulator shows the pattern.
- A revoked role is not noticed until the cached token expires or the dependency re-checks authorization. Acceptable for data-plane access; use short token lifetimes where policy requires faster revocation.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Acquire a token per request | Avoidable cost and dependency on the identity endpoint for every call |
| Single cached token for all calls | Audience mismatch; misleading 401s |
| Refresh only after a 401 | Every token expiry becomes a failed request and a retry |
| Long-lived secrets instead of tokens | Violates the no-keys invariant |
