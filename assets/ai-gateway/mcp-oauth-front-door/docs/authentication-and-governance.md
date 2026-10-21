# MCP OAuth Front Door: Authentication and Governance

This document covers the identities, app registrations, configuration, consent and audit controls behind the front door described in [architecture.md](architecture.md). It assumes Microsoft Entra ID or Entra External ID as the identity provider (IdP) and Azure API Management (APIM) as the gateway. All IDs and names are placeholders, and IdP behavior should be **verified against current Microsoft Learn documentation**.

## 1. Identity inventory

| Identity | Type | Lives in | Purpose | Owner |
|---|---|---|---|---|
| `mcp-orders-api` (`11111111-1111-1111-1111-111111111111`) | App registration (API) | IdP tenant | Audience and scopes for the `orders` MCP server | MCP server owner |
| `mcp-knowledge-api` (`33333333-3333-3333-3333-333333333333`) | App registration (API) | IdP tenant | Audience and scopes for the `knowledge` MCP server | MCP server owner |
| `mcp-public-client` (`22222222-2222-2222-2222-222222222222`) | App registration (public client) | IdP tenant | The pre-registered client ID that native MCP clients use, and that the registration shim returns | Identity team |
| Additional public clients (optional) | App registration (public client) | IdP tenant | One per vetted client family when you want per-client consent and sign-in logs | Identity team |
| Browser client (optional) | App registration (single-page application platform) | IdP tenant | Browser-based MCP clients that redeem codes cross-origin | Identity team |
| Gateway managed identity | System- or user-assigned managed identity | Azure | Reads named values from Key Vault; reaches backends | Platform team |
| Broker app (Variant D only) | Confidential client app registration, with a certificate or federated credential | IdP tenant | Exchanges codes with the IdP on behalf of MCP clients | Platform team |

The front door in Variants A to C holds **no client secrets**. Public clients authenticate with PKCE and redirect URI checks only.

## 2. App registration baseline

### 2.1 API app per MCP server

- **Application ID URI:** `api://{app-id}` by default. If you want spec-current clients' `resource` to line up with the scope without a façade, consider an App ID URI equal to the MCP resource URI on a verified domain, and test how the IdP treats `resource` before relying on it.
- **Exposed scope:** `mcp.tools` (delegated), with an admin and user consent description that names the MCP server and what its tools can do.
- **App roles (optional):** for app-only callers such as admin-configured connectors using client credentials. Validate `roles` for those routes, `scp` for delegated routes.
- **Token version:** set the API's accepted access token version to 2, so the audience is always the app ID GUID. If you must accept v1 tokens, validate both the GUID and the App ID URI.
- **Assignment required:** turn on user assignment for workforce APIs that only certain groups may use. Without it, any user in the tenant can obtain a token after consent.
- **Optional claims:** add only what the tenant pipeline needs (for example `tid`, `oid`, group or role claims). Avoid large group claims; use app roles.

### 2.2 Public client app

- **Platform:** mobile and desktop applications, with loopback redirect URIs such as `http://127.0.0.1/callback` and `http://localhost/callback`. Register the exact paths your clients use. The IdP treats the port on loopback redirect URIs flexibly for native clients; check the redirect URI documentation for the current rules.
- **HTTPS redirect URIs:** only exact URIs for hosted clients you have vetted. No wildcards.
- **Single-page application platform:** only for browser-based clients that redeem codes cross-origin. Register those redirect URIs under that platform, not under mobile and desktop.
- **API permissions:** delegated `mcp.tools` on each API app the client may call, plus `offline_access`.
- **Public client flows setting:** leave the setting that enables device code and other public-client grants off unless a client needs them. The authorization code flow with PKCE doesn't require it.
- **Pre-authorized applications:** listing the public client as pre-authorized on the API app suppresses the consent prompt. That's convenient for first-party clients, but with a shared client ID it also hides which real client the user is authorizing. Use it only for clients you control.
- **Branding and publisher verification:** give the shared client a clear name that says it's a shared MCP client, and complete publisher verification where available. Users see this name on the consent screen.

### 2.3 Multi-tenant and External ID specifics

- **Customers' own tenants:** make the API and public client apps multi-tenant, and have each customer administrator grant consent. Each customer gets its own resource path, and its PRM points at that customer's tenant-specific issuer. The gateway validates against an issuer allow-list and a `tid` allow-list.
- **External ID:** create the API and public client apps in the External ID tenant, attach them to a sign-up and sign-in user flow, and use the External ID tenant's authority host in PRM, the façades and `validate-jwt`.
- **B2B guests:** no extra registrations. Govern guests with access reviews, Conditional Access and assignment on the API apps.

## 3. Gateway RBAC and secrets

| Principal | Role | Scope | Why |
|---|---|---|---|
| Platform engineers | API Management Service Contributor | APIM instance | Deploy policies and named values through the pipeline |
| Release pipeline identity | API Management Service Contributor | APIM instance | The only writer in production |
| Gateway managed identity | Key Vault Secrets User | Key Vault holding named values | Resolve Key Vault-backed named values |
| Identity team | Application Administrator (or a custom role) | IdP tenant | Manage app registrations and redirect URIs |
| Security operations | Log Analytics Reader | Workspace | Investigate front door and validation events |

Keep the allow-lists and the resource-to-scope map in named values (Key Vault-backed if you treat them as sensitive), deployed from source control. No person should edit them in the portal in production.

## 4. Configuration as governed data

The front door's security depends on a few pieces of configuration. Generate all of them from one source file so PRM, the metadata shim and the policies can't drift.

| Named value | Example | Used by | Change control |
|---|---|---|---|
| `mcp-gateway-base-url` | `https://mcp.contoso.example` | Challenge, PRM, shim | Platform |
| `mcp-facade-issuer` | `https://mcp.contoso.example/auth` | PRM, shim | Platform |
| `mcp-idp-authority` | `https://login.example.com/00000000-0000-0000-0000-000000000000` | Façades, validation | Identity |
| `mcp-idp-issuer` | `{authority}/v2.0` | Validation | Identity |
| `mcp-allowed-client-ids` | `22222222-2222-2222-2222-222222222222` (semicolon-separated) | Façades | Identity and security review |
| `mcp-loopback-redirect-regex` | Loopback hosts with the registered paths | Shim, authorize façade | Identity and security review |
| `mcp-resource-scope-map` | `resource|scope` pairs | Façades | MCP server owner and security review |
| `mcp-orders-audience` | `11111111-1111-1111-1111-111111111111` | Validation | MCP server owner |
| `mcp-registration-enabled` | `false` | Registration shim | Security approval (ADR) |
| `mcp-registration-rate-calls` and `mcp-registration-rate-period` | Your values | Registration shim | Platform |

The full list of named values the illustrative policies use, including operational ones such as cache lifetimes and timeouts, is in the [policies README](policies/README.md#named-values).

Every change to the allow-lists, redirect rules or map needs review by someone other than the author, and a negative test that proves the change didn't widen access.

## 5. Conditional Access and user assignment

- Target Conditional Access policies at the **API app registrations** (the resources), not only at the public client. Then every client that obtains a token for an MCP server is subject to them.
- Require MFA and, where appropriate, compliant or managed devices for write-capable MCP servers.
- Use sign-in frequency and session controls for high-risk servers, rather than relying on short access token lifetimes alone.
- Use user assignment on the API apps to restrict who can obtain tokens at all.
- For External ID, apply the Conditional Access and MFA options available in the External ID tenant, and check current documentation for differences from workforce tenants.

## 6. Consent governance

- **User consent settings.** Restrict user consent to verified publishers and low-risk permissions, and send everything else through the admin consent workflow.
- **Shared client consent.** With one shared public client, a user's consent covers every MCP client that uses that client ID. Explain this on the consent description, and prefer per-client-family registrations for anything sensitive.
- **Consent phishing.** A malicious local app can use the shared client ID and a loopback redirect to get a token the user believes they gave to a different client. Mitigate with pre-registration of vetted client families, Conditional Access on devices, user assignment, and alerts on unusual sign-in patterns for the shared client.
- **Revocation.** Revoking consent or sessions for the shared client affects every client that uses it. Document how to revoke for one user (sign-in session revocation) and for everyone (remove consent, rotate to a new client ID).
- **Registration shim approval.** Enabling the shim is a security decision recorded in an ADR (see [ADR 0002](decisions/0002-prefer-preregistered-clients-over-dcr-shim.md)) with an owner and a review date.

## 7. Audit

| Event | Fields to record | Never record |
|---|---|---|
| Challenge issued | Route, resource, request ID | Request body |
| PRM or metadata fetched | Path, status | Nothing sensitive is present |
| Registration attempt | Outcome, reason, redirect URI class, client name length, caller IP | Full request body |
| Authorize request | Outcome, reason, client ID, redirect URI class, resource, mapped scope | `state`, `code_challenge`, full query string |
| Token request | Outcome, grant type, client ID, resource, IdP error code, IdP correlation ID | Codes, verifiers, tokens, request or response body |
| Token validation | Outcome, failure reason, issuer, audience, `tid`, `azp`, `oid` | The token |

Configure APIM diagnostics to exclude bodies on the `/auth/*` operations and to mask sensitive query parameters and headers. Send audit events to Log Analytics and your SIEM, and correlate them with IdP sign-in logs.

## 8. Negative tests

Run these in every environment before release. The [simulator](../samples/oauth_front_door_sim.py) covers the logic offline; the live versions are in [evaluation-and-troubleshooting.md](evaluation-and-troubleshooting.md).

| Test | Expected result |
|---|---|
| MCP call with no token | `401` with `resource_metadata` pointing at this server's PRM |
| PRM for an unknown server path | `404` |
| Registration with a non-loopback, non-allow-listed redirect URI | `400 invalid_redirect_uri`, nothing registered |
| Registration requesting a confidential-client auth method | `400 invalid_client_metadata` |
| Authorize with an unknown client ID | `400` error page, **no redirect** |
| Authorize with a disallowed redirect URI | `400` error page, **no redirect** |
| Authorize without `code_challenge` or with `plain` | `400` error page, **no redirect** |
| Authorize with an unknown `resource` | `400 invalid_target` |
| Token request with the wrong `code_verifier` | IdP error, passed through as `400 invalid_grant` |
| Token for the `knowledge` API presented to `orders` | `401 invalid_token` |
| Token from another tenant or issuer | `401 invalid_token` |
| Valid token missing `mcp.tools` | `403 insufficient_scope` |
| Request reaching the backend | No `Authorization` header from the client |

## 9. Lifecycle

**Adding an MCP server:** create its API app and scope, add the resource-to-scope map entry, add PRM, grant the public clients permission, update validation audiences, run the negative tests, then register the server in the catalog used by the [multi-tenant MCP gateway](../../multi-tenant-mcp-gateway/README.md).

**Adding a client family:** probe its behavior, decide whether it can use an existing pre-registered client or needs its own, register exact redirect URIs, add it to the allow-list, and test sign-in and refresh.

**Removing a client family:** remove it from the allow-list first (the façades stop accepting it), then remove its redirect URIs or registration, then revoke its consent grants.

**Spec and client changes:** review each new MCP specification revision and each major client release against the [client compatibility](architecture.md#12-client-compatibility) table, and re-run the probes. Remove shims you no longer need. The goal is to move down the variants, not up.

**Key and configuration rotation:** the front door relies on IdP key discovery and needs no key rotation itself. Variant D brokers rotate their certificates or rely on federated credentials.
