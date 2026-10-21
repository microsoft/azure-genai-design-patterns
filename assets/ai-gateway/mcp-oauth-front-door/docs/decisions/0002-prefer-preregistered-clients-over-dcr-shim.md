# ADR 0002: Prefer pre-registered clients over a dynamic client registration shim

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture: client registration shim](../architecture.md#84-client-registration-shim), [Authentication and governance](../authentication-and-governance.md), [ADR 0001](0001-front-door-shims-at-the-gateway.md)

## Context

The MCP authorization specification lets clients obtain a client ID in more than one way: a client ID pre-registered with the authorization server, Dynamic Client Registration (RFC 7591), and, in newer revisions, Client ID Metadata Documents, where the client ID is an HTTPS URL that points at a document describing the client. Some MCP clients only proceed if the authorization server metadata advertises a `registration_endpoint`.

Microsoft Entra ID doesn't offer open RFC 7591 registration for arbitrary clients (verify against current documentation). A gateway "registration shim" can accept RFC 7591 requests and return the client ID of a pre-registered public client, which makes registration-dependent clients work. But it means:

- every registrant shares one client identity, so consent screens and sign-in logs name the shared app, not the real client
- any local process can claim to be any client, which enables consent phishing
- you can't revoke one registrant without revoking all of them
- the gateway exposes an anonymous, internet-facing endpoint that must be rate-limited and monitored

## Decision

1. **Default: pre-registered clients.** For each client family we support, register a public client app (or reuse a vetted shared one) with exact redirect URIs, and configure clients with its client ID. Where clients support Client ID Metadata Documents and the authorization server path supports them (directly or through Variant D), prefer those over a shim.
2. **The registration shim is off by default** (`mcp-registration-enabled = false`) and enabled per environment only when a client the business must support can't work without it.
3. **When enabled, the shim must:**
   - return only the pre-registered shared public client ID, and never create directory objects
   - accept only loopback redirect URIs on the paths registered at the IdP, plus exact vetted HTTPS URIs
   - refuse confidential-client authentication methods
   - be rate-limited per caller and log every attempt (without bodies)
   - be paired with Conditional Access and user assignment on the API apps
4. **The consent-phishing trade-off is accepted explicitly** by the security owner, with a named owner and a review date in this ADR's change history, and reviewed when client support changes.
5. **Regulated workloads don't enable the shim.** They use pre-registered clients or Variant D.

## Consequences

### Positive

- Consent screens and sign-in logs identify real client families wherever possible.
- Revocation and Conditional Access can target individual client families.
- The anonymous attack surface is minimal by default.
- Registration-dependent clients can still be supported, under explicit governance.

### Trade-offs

- Onboarding a new client family is a manual step (app registration, redirect URIs, allow-list).
- Some clients won't work until the shim is enabled or they adopt pre-registration or Client ID Metadata Documents.
- With the shim on, monitoring has to make up for the lack of per-client identity.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Registration shim on by default for maximum compatibility | Shared identity and consent phishing for every user, even when no client needs it |
| Shim that creates a real app registration per request | Lets anonymous callers create identities in the directory; unacceptable |
| Shim that issues a new random client ID per request and maps it at the gateway | The gateway becomes a client registry and has to rewrite client IDs in every flow; the IdP still sees one app; complexity without real separation |
| Dedicated broker with real per-client registration (Variant D) | Correct, but heavy; it's the escalation path when per-client consent is required |
| Refuse registration-dependent clients entirely | Acceptable for regulated workloads, but blocks legitimate clients elsewhere |
