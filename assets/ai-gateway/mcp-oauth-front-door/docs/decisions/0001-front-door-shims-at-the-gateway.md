# ADR 0001: Put the OAuth front door shims at the gateway

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [ADR 0002](0002-prefer-preregistered-clients-over-dcr-shim.md)

## Context

Remote MCP servers protected by OAuth must let MCP clients discover the authorization server and obtain tokens on their own. MCP clients expect a `401` challenge with `resource_metadata`, Protected Resource Metadata (RFC 9728), Authorization Server Metadata (RFC 8414) or OpenID Connect discovery with `S256` advertised, a way to get a client ID, and resource indicators (RFC 8707).

The enterprise identity provider (Microsoft Entra ID workforce or External ID) remains the authorization server of record, but differs from those expectations in a few places: where its discovery document lives relative to its issuer, whether PKCE support is advertised, no open dynamic client registration, and audience selection through scopes rather than `resource`. Verify these against current documentation; they may change.

We run several MCP servers behind one API Management gateway that already performs tenant resolution, tool entitlement and audit (see the [multi-tenant MCP gateway](../../../multi-tenant-mcp-gateway/README.md)). We need one place to close these gaps without changing every MCP server or the IdP.

## Decision

1. **The gateway owns the front door.** The challenge, PRM, the optional metadata shim, the optional registration shim and the optional authorize and token façades are API Management policies on the same gateway that fronts the MCP servers. MCP servers don't implement OAuth discovery themselves.
2. **The gateway never mints tokens.** Every access token is issued by the IdP. Façades only validate, translate (`resource` to `scope`) and forward. Token validation at the MCP route checks issuer, audience, scope or role, and tenant.
3. **Variants are chosen by probes.** Start with Variant A (PRM pointing at the IdP) and add the metadata shim (B), façades (C) or the registration shim (C+R) only when a probe shows a required client fails without it.
4. **One configuration source.** PRM, the metadata shim, the allow-lists and the resource-to-scope map are generated from one reviewed configuration file and deployed as named values and policies.
5. **No token passthrough.** After validation, the gateway removes the client's `Authorization` header. Backends receive their own credentials, as in [Backend Identity Propagation](../../../backend-identity-propagation/README.md).
6. **A dedicated broker (Variant D) is the escalation path**, used only when gateway policies can't meet client or consent requirements (for example strict issuer checks on the authorization response, or per-client consent screens).

## Consequences

### Positive

- MCP servers stay simple and identity-agnostic; adding a server is configuration, not code.
- One place to apply logging hygiene, rate limits, allow-lists and alerts for every OAuth interaction.
- The IdP stays the only token issuer, so Conditional Access, consent, sign-in logs and revocation keep working.
- The front door shares the gateway's existing deployment, monitoring and change control.

### Trade-offs

- The gateway becomes part of the sign-in path. A misconfigured façade blocks sign-in for every client using it.
- The token façade sees codes, verifiers and tokens in transit. Diagnostics must exclude bodies on that route.
- The metadata shim's issuer differs from the token issuer. Clients that compare an authorization response `iss` (RFC 9207) with the shim's issuer will fail; Variant D is the remedy.
- Policy expressions are harder to unit-test than application code. The [simulator](../../samples/oauth_front_door_sim.py) and the live probes in the evaluation doc compensate.
- Shims track client behavior that changes over time; they need periodic re-probing and removal when no longer needed.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Implement OAuth discovery and registration in every MCP server | Duplicated security-sensitive code in each server, inconsistent behavior, and each server becomes an OAuth expert |
| Dedicated auth broker for all traffic from day one (Variant D) | Becomes a full authorization server with its own keys, consent store and attack surface; justified only when policies can't meet requirements |
| Wait for the IdP to support every client expectation | Blocks adoption now; we will remove shims as native support arrives |
| Disable authentication on MCP routes and rely on network controls | Every tool becomes anonymous to anyone on the network; fails security review |
| Use only admin-configured connectors with confidential clients | Works for some hosts, but excludes clients that discover and register on their own |
