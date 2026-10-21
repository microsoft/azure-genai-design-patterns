# Illustrative APIM policies for the MCP OAuth front door

These six Azure API Management (APIM) policy documents show how the front door in [architecture.md](../architecture.md) maps onto APIM. They are **illustrative**: written for this pattern, not exported from a deployment, and not tested against a live gateway. Verify every policy element and expression against the current [APIM policy reference](https://learn.microsoft.com/azure/api-management/api-management-policies) and [policy expressions](https://learn.microsoft.com/azure/api-management/api-management-policy-expressions) documentation before you use them.

The offline equivalent of the same logic, with tests, is the [simulator](../../samples/oauth_front_door_sim.py).

## Files

| File | Route or operation | Variant | What it does |
|---|---|---|---|
| [`mcp-challenge-and-validate.xml`](mcp-challenge-and-validate.xml) | Every MCP server API (`/orders/mcp` shown) | A, B, C | `401` + `WWW-Authenticate` with `resource_metadata` when no bearer token; `validate-jwt` (issuer, audience, `tid`); separate `scp` check returning `403 insufficient_scope`; stores claims, deletes `Authorization`, includes the tenant pipeline fragment |
| [`prm.xml`](prm.xml) | `GET /.well-known/oauth-protected-resource/{*path}` | A, B, C | RFC 9728 document per known resource path; `404` for anything else |
| [`as-metadata-shim.xml`](as-metadata-shim.xml) | `GET /.well-known/oauth-authorization-server/auth` | B, C | RFC 8414 document for the façade issuer; `S256` only; `registration_endpoint` only when registration is enabled |
| [`register-shim.xml`](register-shim.xml) | `POST /auth/register` | C (optional) | RFC 7591 request checks; returns the shared pre-registered public client ID; `404` when disabled; rate-limited |
| [`authorize-facade.xml`](authorize-facade.xml) | `GET /auth/authorize` | C | Validates client, redirect URI and PKCE **before** redirecting; maps `resource` to scope; `302` to the IdP |
| [`token-facade.xml`](token-facade.xml) | `POST /auth/token` | C | Validates grant and client; maps `resource` to scope; forwards to the IdP token endpoint; `no-store` |

In Variant A only `mcp-challenge-and-validate.xml` and `prm.xml` apply, with `authorization_servers` set to the IdP issuer instead of the façade issuer. Variant D replaces the shim and façade policies with a broker application.

## Named values

All configuration is in named values so that PRM, the metadata shim and the façades are generated from one source and can't drift. The security-relevant subset and its change control are in [authentication-and-governance.md §4](../authentication-and-governance.md#4-configuration-as-governed-data).

| Named value | Example | Used by |
|---|---|---|
| `mcp-gateway-base-url` | `https://mcp.contoso.example` | challenge, PRM |
| `mcp-facade-issuer` | `https://mcp.contoso.example/auth` | PRM, shim |
| `mcp-known-resource-paths` | `orders/mcp;knowledge/mcp` | PRM |
| `mcp-orders-resource-path` | `orders/mcp` | challenge |
| `mcp-docs-base-url` | `https://docs.contoso.example/mcp` | PRM, shim |
| `mcp-metadata-max-age` | Your cache lifetime in seconds | PRM, shim |
| `mcp-browser-client-origin` | `https://app.contoso.example` | challenge |
| `mcp-idp-authority` | `https://login.example.com/00000000-0000-0000-0000-000000000000` | challenge, shim, authorize, token |
| `mcp-idp-issuer` | `https://login.example.com/00000000-0000-0000-0000-000000000000/v2.0` | challenge |
| `mcp-idp-tenant-id` | `00000000-0000-0000-0000-000000000000` | challenge |
| `mcp-orders-audience` | `11111111-1111-1111-1111-111111111111` | challenge |
| `mcp-token-clock-skew-seconds` | Your tolerance in seconds | challenge |
| `mcp-facades-enabled` | `true` (Variant C) or `false` (Variant B) | shim |
| `mcp-registration-enabled` | `false` | shim, register |
| `mcp-registration-rate-calls` | Your value | register |
| `mcp-registration-rate-period` | Your value in seconds | register |
| `mcp-shared-public-client-id` | `22222222-2222-2222-2222-222222222222` | register |
| `mcp-allowed-client-ids` | `22222222-2222-2222-2222-222222222222` (semicolon-separated) | authorize, token |
| `mcp-loopback-redirect-regex` | `^http://(127\.0\.0\.1\|localhost)(:[0-9]+)?/callback$` | register, authorize, token |
| `mcp-resource-scope-map` | `https://mcp.contoso.example/orders/mcp\|api://11111111-1111-1111-1111-111111111111/mcp.tools;https://mcp.contoso.example/knowledge/mcp\|api://33333333-3333-3333-3333-333333333333/mcp.tools` | authorize, token |
| `mcp-default-scope` | `api://11111111-1111-1111-1111-111111111111/mcp.tools` | authorize, token |
| `mcp-idp-timeout-seconds` | Your value | token |

The regex and map values above are shown with escaped `|` for the table; the stored value uses a plain `|`.

## Notes and caveats

- **Expression syntax.** The files use C# expressions with generics (for example `As<JObject>`) and double quotes inside attribute values, as the APIM documentation does. APIM accepts this, but the files aren't strictly well-formed XML, so a generic XML linter may complain. Ampersands are produced with `"\u0026"` so no raw `&` appears.
- **Named values are textual.** `{{...}}` is substituted before the expression is compiled. Keep regex and map values free of `"` characters, and treat edits to them as code changes.
- **One API per MCP server.** The challenge policy is shown for `orders`. Each MCP server API gets its own copy with its own resource path and audience named values, or a shared fragment parameterized by API-level variables.
- **Root PRM path.** Depending on how you define the wildcard operation, `/.well-known/oauth-protected-resource` with no suffix may need its own operation. Return `404` from it unless you have a single default resource.
- **Tenant pipeline.** `include-fragment fragment-id="mcp-tenant-pipeline"` stands for the per-call pipeline in [Multi-Tenant MCP Gateway](../../../multi-tenant-mcp-gateway/README.md). It isn't defined here.
- **Diagnostics.** Turn off request and response body logging for the `/auth/*` operations and mask the `code`, `code_verifier`, `refresh_token` and `code_challenge` parameters. The `trace` elements in these policies record outcomes and reasons only.
- **Multi-tenant issuers.** For customers in their own tenants, replace the single issuer and `tid` claim with allow-lists, and select the PRM `authorization_servers` value per resource path.
- **Browser clients.** The token façade doesn't delete the `Origin` header. See [architecture.md §8.6](../architecture.md#86-token-facade) before changing that.
