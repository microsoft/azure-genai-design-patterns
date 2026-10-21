# Illustrative APIM policies for backend identity propagation

These four Azure API Management (APIM) policy documents show how the propagation options in [architecture.md](../architecture.md) could map onto APIM. They are **illustrative**: written for this pattern, not exported from a deployment, and not tested against a live gateway. Verify every policy element and expression against the current [APIM policy reference](https://learn.microsoft.com/azure/api-management/api-management-policies) and [policy expressions](https://learn.microsoft.com/azure/api-management/api-management-policy-expressions) documentation before you use them.

The offline equivalent of the same logic, with tests, is the [simulator](../../samples/identity_propagation_sim.py). The recommended place for Option A is the MCP server or a small adapter that uses MSAL, not gateway policy; see [architecture.md §11](../architecture.md#11-implementing-in-api-management-or-in-the-mcp-server).

## Files

| File | Applies to | Option | What it does |
|---|---|---|---|
| [`obo-exchange.xml`](obo-exchange.xml) | `reporting` API | A | Keys a cache on a hash of `tid`, `oid`, audience and sorted scopes; on a miss gets a managed-identity federated assertion, calls the Entra token endpoint with the OBO grant, maps `65001`, `53003` and claims challenges to `consent_required`, `access_blocked` and `interaction_required`; caches only successes, for `expires_in` minus a skew; sets the downstream `Authorization` header |
| [`identity-link-lookup.xml`](identity-link-lookup.xml) | Fragment used by B and C | B, C | Looks up the identity link for `iss`, `tid`, `oid` and the backend tenant; caches active links and (briefly) "no link"; never caches an outage, and fails closed with `503` when the link service is unavailable; sets `linkState` and `backendUserId` |
| [`signed-assertion.xml`](signed-assertion.xml) | `field-service` API | B | Denies `identity_not_linked` with a link URL; builds the JWS header and claims (`sub`, `aud`, `tenant_id`, `act`, `scope`, `jti`, `iat`, `nbf`, `exp`, `req_id`, `cnf`); signs the digest with a Key Vault key over managed identity; sets `X-User-Assertion` and presents the gateway's mTLS client certificate |
| [`tenant-credential-bridge.xml`](tenant-credential-bridge.xml) | `orders` API | C | Enforces a tool allow-list and tenant slug format; resolves the acting user from the link (or `entra:<oid>`); reads `tenant-{tenant}-orders-api-key` from Key Vault with a short cache; sets `X-Api-Key` and `X-Acting-User`; evicts the cached key when the backend returns `401`; warns after the sunset date |

App-only calls (Option D in the architecture) need no policy of their own beyond `authentication-managed-identity` on the reference-data API and deleting any user-identifying arguments; they aren't shown.

## Assumed base pipeline

Each file starts with `<base />` and relies on variables that the API-level or product-level policy sets first, as in the [Multi-Tenant MCP Gateway](../../../multi-tenant-mcp-gateway/README.md) pipeline:

| Variable | Set by | Meaning |
|---|---|---|
| `jwt` | `validate-jwt` with `output-token-variable-name="jwt"` | The validated inbound token (audience is the MCP resource) |
| `tenantKey` | Tenant resolution | Lower-case backend tenant slug, for example `fabrikam` |
| `toolName` | JSON-RPC parsing | The MCP tool name for `tools/call` |
| `inboundToken` | Base policy, Option A only | The raw inbound bearer string, needed as the OBO `assertion`. Never trace or log it |

The base policy validates the inbound token **before** any of these files run, and deletes the client's `Authorization` header after copying the raw token into `inboundToken`. Each file also deletes the identity and credential headers a client could send (`X-User-Assertion`, `X-Acting-User`, `X-Api-Key`, `X-Contoso-User`, `X-Contoso-Tenant`) before it sets its own.

## Named values

All examples are placeholders. Durations are deliberately left as "your value": choose them from your own revocation and load requirements, not from this document.

| Named value | Example | Used by |
|---|---|---|
| `gateway-identity-client-id` | `44444444-4444-4444-4444-444444444444` (user-assigned managed identity) | all |
| `gateway-app-client-id` | `11111111-1111-1111-1111-111111111111` (app registration with a federated identity credential) | OBO |
| `reporting-obo-audience` | `api://contoso-erp-reporting` | OBO |
| `reporting-obo-scopes` | `api://contoso-erp-reporting/Reports.Read` (space-separated) | OBO |
| `obo-timeout-seconds` | Your value | OBO |
| `obo-expiry-skew-seconds` | Your value | OBO |
| `identity-link-service-url` | `https://links.contoso.example` | link lookup |
| `identity-link-service-audience` | `api://contoso-identity-links` | link lookup |
| `identity-link-url` | `https://mcp.contoso.example/link` | assertion |
| `link-timeout-seconds` | Your value | link lookup |
| `link-cache-seconds` | Your value (bounds revocation latency for a removed link) | link lookup |
| `link-negative-cache-seconds` | Your value, shorter than `link-cache-seconds` | link lookup |
| `assertion-issuer` | `https://mcp.contoso.example/assertions` | assertion |
| `assertion-signing-kid` | `gw-assert-2026-10` | assertion |
| `assertion-signing-vault-host` | `<assertion-vault>.vault.azure.net` | assertion |
| `assertion-signing-key-name` | `gw-assert` | assertion |
| `assertion-signing-key-version` | The pinned key version that matches `assertion-signing-kid` | assertion |
| `assertion-lifetime-seconds` | Your value, short and below the backend's maximum | assertion |
| `signing-timeout-seconds` | Your value | assertion |
| `kv-api-version` | A current Key Vault data-plane API version (verify) | assertion, bridge |
| `field-service-assertion-audience` | `urn:contoso:erp:field-service` | assertion |
| `field-service-assertion-scope` | `work-orders.read` | assertion |
| `gateway-mtls-cert-x5t-s256` | Base64url SHA-256 thumbprint of the gateway client certificate | assertion |
| `tenant-keys-vault-host` | `<tenant-keys-vault>.vault.azure.net` | bridge |
| `key-vault-timeout-seconds` | Your value | bridge |
| `tenant-key-cache-seconds` | Your value (bounds how long a rotated key keeps being sent) | bridge |
| `orders-bridge-allowed-tools` | `get_order_status` (comma-separated) | bridge |
| `orders-bridge-sunset` | `2027-06-30` | bridge |

`gateway-mtls-client` in `signed-assertion.xml` is an APIM **certificate** entity (backed by a Key Vault certificate), not a named value. Rotate it and `gateway-mtls-cert-x5t-s256` together, and have the backend accept both the old and new thumbprints during the overlap.

## Notes and caveats

- **No built-in OBO or asymmetric JWT signing.** APIM has no policy that performs the OBO exchange or mints an ES256/RS256 JWT. These files compose `send-request`, `authentication-managed-identity`, `cache-lookup-value` and `cache-store-value` to do it. That works but puts token-endpoint error handling and signing in policy expressions. Doing Option A (and often B) in the MCP server or a small adapter with MSAL and a Key Vault SDK is usually easier to test and maintain. [ADR 0001](../decisions/0001-never-pass-through-client-tokens.md) is satisfied either way.
- **Federated credential for OBO.** `obo-exchange.xml` uses a managed-identity token for `api://AzureADTokenExchange` as the client assertion, so the app registration has no secret. Check the current Entra guidance on federated identity credentials with managed identities and on which token audience to request.
- **Key Vault `sign` output.** The policy assumes the `sign` operation over an EC P-256 key with `ES256` returns `value` as base64url-encoded raw `R||S`, which is the JWS format (RFC 7515, RFC 7518). Verify this, and verify signature acceptance in the backend's JWS library, before relying on it. Key Vault `sign` calls are rate-limited; mint per call only if your volume allows it, or move signing into code with a cached key handle.
- **Expression types.** The files use `SHA256`, `Encoding`, `Convert`, `Uri`, `Regex`, LINQ, `JObject`, `IResponse` and `DateTime` in expressions. Check that each is in the allowed list for policy expressions in your APIM tier.
- **Expression syntax.** The files use C# generics (for example `As<JObject>`) and double quotes inside attribute values, as the APIM documentation does. APIM accepts this, but the files aren't strictly well-formed XML, so a generic XML linter may complain. Ampersands are produced with `"\u0026"` so no raw `&` appears.
- **Named values are textual.** `{{...}}` is substituted before the expression is compiled; `assertion-lifetime-seconds` and `obo-expiry-skew-seconds` must be plain integers.
- **No wildcard header delete.** APIM can't delete `X-Contoso-*` by pattern. List every identity header explicitly and add new ones to every policy when a backend introduces them.
- **JSON-RPC errors.** Denials return a JSON-RPC error body with `data.reason`. When tools are generated from a REST API exposed as an MCP server, check what error shape the client sees, and match tools on `context.Operation.Id` instead of `toolName` if the JSON-RPC body isn't available to policy.
- **Bridge key rotation.** On a backend `401`, `tenant-credential-bridge.xml` evicts the cached key and returns `502 backend_auth_failed`; it does **not** retry. The simulator retries once with a fresh key. Add a retry in code or with the `retry` policy if you need it, and never retry more than once.
- **Client identifier claim.** The assertion's `act.sub` uses the `azp` claim, which is in v2.0 access tokens. If your MCP resource accepts v1.0 tokens, use `appid` instead.
- **Diagnostics.** Exclude `Authorization`, `X-User-Assertion`, `X-Api-Key` and `Cookie` from logged headers and don't log request or response bodies on MCP routes. The `trace` elements record identifiers and outcomes only: never a token, assertion or key.
