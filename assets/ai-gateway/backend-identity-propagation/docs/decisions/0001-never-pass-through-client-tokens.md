# ADR 0001: Never pass client tokens through to backends

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [ADR 0002](0002-signed-assertion-over-shared-api-keys.md)

## Context

MCP clients and agents call the gateway with an access token whose audience is the gateway (or the MCP server behind it). The tools behind the gateway call backend systems of record, such as the Contoso ERP `orders`, `field-service` and `reporting` APIs. Each backend needs to know which user is calling so that its own row-level authorization still applies.

The simplest implementation is to forward the client's `Authorization` header to the backend. The [MCP security best practices](https://modelcontextprotocol.io/specification/latest/basic/security_best_practices) explicitly forbid this ("token passthrough"), for good reasons:

- The token's audience is the gateway. A backend that accepts it accepts tokens that were never meant for it, which also means any other service the user's token reaches could replay it against the backend.
- The gateway's controls (tenant resolution, tool entitlement, rate limits, audit) can be bypassed by anyone holding the token who can reach the backend.
- The backend can't tell whether the caller was the gateway acting for the user or something else holding the user's token (confused deputy).
- Several of our backends don't trust the client's identity provider at all; they have their own user tables or only per-tenant API keys.

## Decision

1. **The gateway deletes the client's `Authorization` header** (and every other identity or credential header a client could send: `X-User-Assertion`, `X-Acting-User`, `X-Api-Key`, `X-Contoso-*`) right after it validates the inbound token.
2. **Every backend call carries a credential the gateway obtained for that backend**, chosen per backend from the options in [architecture.md section 9](../architecture.md#9-decision-matrix):
   - Option A, on-behalf-of token exchange, when the backend trusts the same identity provider. The downstream token's audience is the backend.
   - Option B, a short-lived signed user assertion plus identity link, when the backend has its own identity store ([ADR 0002](0002-signed-assertion-over-shared-api-keys.md)).
   - Option C, a per-tenant credential bridge, only as a time-boxed transitional step for backends that support nothing else.
   - App-only tokens for tools that read data with no per-user rules.
3. **Backends enforce audience.** Each backend that accepts Entra tokens accepts only tokens whose `aud` is its own application. A token for the gateway's audience is rejected.
4. **No fallback to a broader credential.** If the exchange fails, consent is missing or the user is unlinked, the call fails. It never falls back to app-only, a tenant key or passthrough.
5. **User identity comes only from the validated token**, never from tool arguments or client headers.

## Consequences

### Positive

- Tokens can't be replayed across services, because every hop has its own audience.
- Backends keep enforcing their own authorization, now with a verifiable user identity.
- The gateway is the only path that can produce backend credentials, so its audit trail is complete.
- Revocation and least privilege are per backend: each downstream token or assertion carries only that backend's scopes.

### Trade-offs

- Every backend needs an explicit propagation option, configuration and tests; there is no "just forward it" default.
- On-behalf-of adds an identity provider dependency, consent per customer tenant, and Conditional Access errors to handle.
- Signed assertions add a signing key, a JWKS, an identity link store and backend verification code.
- Errors are more varied (`consent_required`, `interaction_required`, `identity_not_linked`), and clients need to handle them.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Forward the client token to the backend (passthrough) | Forbidden by the MCP specification; enables replay and confused deputy attacks; bypasses gateway controls |
| Request a token with several audiences, so one token works at the gateway and the backend | Same replay problem; most identity providers issue one audience per token, and widening the audience widens the blast radius |
| One shared service account for all backend calls | Backend loses user context; every user effectively has the service account's rights ([anti-patterns](../architecture.md#17-anti-patterns)) |
| Let the client call the backend directly with its own token for that backend | Moves credentials and backend topology to every client, and loses gateway entitlement and audit |
