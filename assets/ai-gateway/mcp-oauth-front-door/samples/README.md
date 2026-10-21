# Samples: OAuth front door simulator

[`oauth_front_door_sim.py`](oauth_front_door_sim.py) simulates the MCP OAuth front door described in [the architecture](../docs/architecture.md#6-solution-overview). It runs offline, uses only the Python standard library, and needs no Azure resources, IdP tenant, or MCP client.

It puts four parties on an in-memory network so you can test the whole discovery and sign-in path without deploying anything:

- **The front door (`OAuthFrontDoor`):** the challenge, protected resource metadata (RFC 9728), the authorization server metadata shim (RFC 8414), the registration shim (RFC 7591 request shape), the authorize and token façades, and token validation at the MCP route. Each handler mirrors one illustrative [policy file](../docs/policies/README.md).
- **A fake identity provider (`FakeIdentityProvider`):** an authorization server *modeled* on a scope-based enterprise IdP. It publishes only OpenID Connect discovery, doesn't advertise `code_challenge_methods_supported` or a `registration_endpoint`, binds the audience from an `api://{app-id}/{scope}` scope, rejects a `resource` that names a different API, requires PKCE with `S256` for public clients, and issues single-use codes.
- **Simulated MCP clients (`McpClientSim`):** generic client types that follow the challenge, read PRM, walk the discovery URLs, optionally register, run PKCE, and call the MCP server.
- **A fake backend (`FakeBackend`):** records what the gateway forwards after validation, so tests can prove the client token never leaves the gateway.

The simulator models the **logic** of the pattern. It is not API Management, not Microsoft Entra ID, and not an MCP server you can connect a real client to. The IdP behaviors are a model for testing the front door; verify the real behavior of your IdP against its current documentation.

> **Test only.** Access tokens are HS256 JWTs signed with a random key generated in memory on every run. Real deployments validate asymmetric signatures against the keys the IdP publishes, with `validate-jwt` or `validate-azure-ad-token`. Never reuse `sign_jwt`, `decode_jwt_unverified`, or `FakeIdentityProvider` outside a test harness.

## Prerequisites

- Python 3.10 or later.
- No packages, network access, or credentials.

## Run it

From the repository root:

```powershell
# Offline scenarios. Prints PASS <scenario> per scenario; exits 0 when all pass, 1 otherwise.
python assets/ai-gateway/mcp-oauth-front-door/samples/oauth_front_door_sim.py --self-test

# Print the wire contracts from architecture section 13, produced by the simulator:
# the 401 challenge, PRM for two variants, the metadata shim, a registration, and the authorize redirect.
python assets/ai-gateway/mcp-oauth-front-door/samples/oauth_front_door_sim.py --emit

# A narrated sign-in through Variant C+R (request log, token claims, gateway trace, refresh,
# cross-server replay, what the backend received), followed by a variant-by-client-type table.
python assets/ai-gateway/mcp-oauth-front-door/samples/oauth_front_door_sim.py --demo
```

Running it with no arguments prints help and exits with code 2. No mode prints an access token, refresh token, or authorization code; the demo prints decoded claims and logs request lines without query strings.

## The fictional world

Every host, tenant and ID is a placeholder.

| Fixture | Value | Notes |
|---|---|---|
| Gateway | `https://mcp.contoso.example` | Serves the MCP routes and every front door endpoint |
| Façade issuer | `https://mcp.contoso.example/auth` | Issuer of the metadata shim in Variants B, C and C+R |
| IdP authority | `https://login.example.com/00000000-0000-0000-0000-000000000000` | Issuer is the authority plus `/v2.0` |
| Shared public client | `22222222-2222-2222-2222-222222222222` | Pre-registered; loopback redirect URIs `http://127.0.0.1/callback` and `http://localhost/callback`, any port |
| User | `44444444-4444-4444-4444-444444444444` | The `oid` in every minted token |

| MCP server | Resource (`resource`) | API app ID (`aud`) | IdP scope after mapping |
|---|---|---|---|
| Contoso Orders | `https://mcp.contoso.example/orders/mcp` | `11111111-1111-1111-1111-111111111111` | `api://11111111-1111-1111-1111-111111111111/mcp.tools` |
| Contoso Knowledge | `https://mcp.contoso.example/knowledge/mcp` | `33333333-3333-3333-3333-333333333333` | `api://33333333-3333-3333-3333-333333333333/mcp.tools` |

### Variants

`build_world(variant)` wires the front door for one variant from [architecture section 9](../docs/architecture.md#9-variants-and-decision-matrix). Variant D, a dedicated broker app, is a different application and isn't simulated.

| Variant | Metadata shim | Authorize and token façades | Registration shim | PRM `authorization_servers` |
|---|---|---|---|---|
| `A` | No | No | No | IdP issuer |
| `B` | Yes, endpoints at the IdP | No | No | Façade issuer |
| `C` | Yes, endpoints at the façades | Yes | No | Façade issuer |
| `C+R` | Yes, plus `registration_endpoint` | Yes | Yes | Façade issuer |

### Client types

These are generic behaviors from [architecture section 12](../docs/architecture.md#12-client-compatibility), not models of any named product.

| Profile | Sends `resource` | Requires `S256` advertised | Discovery | Client ID |
|---|---|---|---|---|
| `SPEC_CURRENT_CLIENT` (type A) | Yes | Yes | Every URL in order | Pre-registered |
| `REGISTRATION_DEPENDENT_CLIENT` (type B) | Yes | Yes | Every URL in order | Must register first |
| `OLDER_DISCOVERY_CLIENT` (type C) | No | No | Only the first URL | Pre-registered |
| `LENIENT_CLIENT` | No | No | Every URL in order | Pre-registered |

`--demo` prints which client type signs in under which variant. In the simulation, only Variant C+R works for all four, Variant C works for every type except the registration-dependent one, and Variant A works only for the lenient client. Use the table to reason about the pattern, then build the real matrix from live probes ([evaluation section 2](../docs/evaluation-and-troubleshooting.md#2-client-and-variant-matrix)).

The rate-limit, lifetime and cache values in the fixtures are small test values that keep the scenarios fast. They aren't sizing guidance.

## What the self-test proves

The `S` numbers refer to the [scenario catalog](../docs/evaluation-and-troubleshooting.md#1-scenario-catalog).

| Scenario | Covers | What it proves |
|---|---|---|
| `s1-challenge-carries-resource-metadata` | S1 | A request without a token, or with a non-`Bearer` scheme, gets `401` with `resource_metadata` for that server, `no-store`, and the header exposed to browsers. Nothing reaches the backend |
| `s2-prm-per-server` | S2 | Each server's PRM matches section 13.2 and passes RFC 9728 checks; Variant A names the IdP and IdP scopes; Variant B names the shim with IdP scopes |
| `s3-prm-unknown-path-404` | S3 | Unknown or partial PRM paths and non-`GET` methods return `404` |
| `s4-discovery-finds-shim-with-s256` | S4 | The first discovery URL a client tries finds the shim, which matches section 13.3, advertises only `S256`, and has no `jwks_uri`. The modeled IdP document fails the same checks |
| `s4b-metadata-shim-per-variant` | S4 | Variant A serves no shim, B points at the IdP endpoints, C at the façades, and only C+R advertises registration. A mismatched upstream issuer is refused |
| `s5-registration-returns-shared-client` | S5 | Registration returns the shared public client ID (section 13.4), is `no-store`, and is recorded for audit |
| `s6-registration-rejects-disallowed-metadata` | S6 | Non-loopback, look-alike, or query-carrying redirect URIs, confidential auth methods, other grant or response types, and malformed bodies get `400`; nothing is recorded |
| `s6b-registration-disabled-and-rate-limited` | S6 | Variant C has no registration endpoint; C+R limits registrations per caller, counts malformed requests, and resets after the period |
| `s7-authorize-maps-resource-to-scope` | S7 | The façade redirects to the IdP with the mapped scope plus `offline_access`, unchanged `state` and PKCE values, and no `resource` (section 13.5); a default scope applies when `resource` is absent, and only allow-listed parameters pass through |
| `s8-authorize-requires-pkce-s256` | S8 | Missing, `plain`, or malformed PKCE gets a `400` page with no redirect; the modeled IdP also refuses `plain` |
| `s9-authorize-rejects-unknown-client-or-redirect` | S9 | Unknown clients, disallowed redirect URIs, and other response types get a `400` page that doesn't echo the redirect URI, and never reach the IdP. The IdP's own redirect URI check is a second line of defense |
| `s10-unknown-resource-invalid-target` | S10 | A resource that isn't in the map gets `invalid_target` at both façades and never reaches the IdP |
| `variant-b-idp-rejects-foreign-resource` | Variant B | With endpoints at the modeled IdP, a client that sends the MCP URL as `resource` fails with `invalid_target`; one that doesn't send it succeeds |
| `variant-a-direct-idp-discovery-gaps` | Variant A | Pointing PRM straight at the modeled IdP fails for clients that need `S256` advertised or RFC 8414 discovery; the gateway serves only PRM and the MCP route |
| `s11-end-to-end-token-audience` | S11 | A full sign-in yields a token whose `aud`, `scp`, `iss`, `tid` and `azp` match the server, and the MCP call succeeds |
| `s12-wrong-verifier-and-code-reuse` | S12 | A wrong verifier gets `invalid_grant` from the IdP, codes are single-use, and the redirect URI must match the authorization request |
| `token-facade-forwarding-contract` | S11 | The token façade forwards the form in section 13.7 to the IdP, drops gateway cookies, keeps `Origin`, returns a `no-store` response, and rejects other grant types and unknown clients before calling the IdP |
| `token-facade-idp-unreachable-502` | Resilience | If the IdP is unreachable, the token façade returns `502 temporarily_unavailable`; the challenge and PRM keep working |
| `s13-wrong-audience-or-expired-rejected` | S13 | Tokens for the other server, expired, not-yet-valid, or malformed tokens get `401 invalid_token` naming the right PRM; configured clock skew is honored and bounded |
| `s14-wrong-issuer-key-or-tenant-rejected` | S14 | Tokens from another issuer, signed with another key or an unknown `kid`, carrying another `tid`, minted by another tenant's IdP, tampered, or using `alg: none` get `401 invalid_token`; nothing reaches the backend |
| `s15-missing-scope-403` | S15 | A valid token without the server's scope gets `403 insufficient_scope` |
| `s16-no-token-passthrough` | S16 | The backend never receives an `Authorization` header or any token value; identity travels as validated context |
| `s17-refresh-keeps-audience` | S17 | Refresh keeps the audience, rotates the refresh token, maps `resource` to scope, and rejects unknown clients, unknown tokens, and unknown resources |
| `pkce-rfc7636-test-vector` | PKCE | The `S256` transform matches the RFC 7636 appendix B test vector, and the façades pass a fresh verifier and challenge through unchanged |
| `production-adapter-is-gated` | Safety | `EntraIdentityProvider` refuses to construct without `enable_network=True`, its methods raise `NotImplementedError`, and unknown variants are refused |
| `emitted-documents-valid-and-trace-clean` | Logging | Every `--emit` document passes the client-side validators, and no token, code, or verifier appears in the gateway trace or client logs |

Each check covered by a scenario was verified by mutation: disabling the audience, issuer, tenant, lifetime, signature, scope, PKCE, redirect URI, or resource-mapping check makes at least one scenario fail.

## How the simulator maps to API Management

| Simulator handler (`OAuthFrontDoor`) | Policy file | API Management building blocks |
|---|---|---|
| `handle_mcp` (no token or invalid token) | [`mcp-challenge-and-validate.xml`](../docs/policies/mcp-challenge-and-validate.xml) | `validate-jwt` or `validate-azure-ad-token` with issuer, audience, tenant and scope checks; `on-error` returns `401` or `403` with `WWW-Authenticate` |
| `handle_mcp` (valid token) | [`mcp-challenge-and-validate.xml`](../docs/policies/mcp-challenge-and-validate.xml) | Remove the client `Authorization` header, then hand off to the [multi-tenant MCP gateway](../../multi-tenant-mcp-gateway/README.md) pipeline |
| `prm` | [`prm.xml`](../docs/policies/prm.xml) | `return-response` with JSON built from named values, one operation per MCP server path |
| `as_metadata` | [`as-metadata-shim.xml`](../docs/policies/as-metadata-shim.xml) | `send-request` to the IdP's OpenID Connect document, cached; rewrite issuer and endpoints; add `S256` |
| `register` | [`register-shim.xml`](../docs/policies/register-shim.xml) | `rate-limit-by-key` on caller IP; validate redirect URIs and metadata; return the shared client ID |
| `authorize` | [`authorize-facade.xml`](../docs/policies/authorize-facade.xml) | Check client ID, redirect URI and PKCE; map `resource` to scope; `302` to the IdP |
| `token` | [`token-facade.xml`](../docs/policies/token-facade.xml) | Check grant type and client ID; map `resource` to scope; forward the form with `send-request`; return the IdP's response with `no-store` |

The policy files are illustrative and untested; the simulator is the tested reference for the logic. Small differences remain (for example, JSON formatting and which optional PRM fields are emitted). Validate any policy in your own API Management instance before you rely on it. See the [policies guide](../docs/policies/README.md).

## Adapter seams

| Seam | In-memory fake used by tests | Gated production stub | What a real implementation does |
|---|---|---|---|
| `IdentityProvider` (`Protocol`: `issuer`, `openid_configuration()`, `signing_keys()`) | `FakeIdentityProvider` | `EntraIdentityProvider` | Fetches the tenant's OpenID Connect document and signing keys over HTTPS, caches them, and refreshes on key rotation. In API Management, `validate-jwt` with an OpenID configuration URL does this for token validation, and the metadata shim's `send-request` does it for discovery |
| `ClientRegistry` (`Protocol`: `allowed_client_ids()`, `shared_public_client_id()`, `record_registration(...)`) | `InMemoryClientRegistry` | (none) | Reads the governed allow-list of pre-registered public clients from configuration (named values or a config store) and writes registration events to your audit log without tokens |
| Backend | `FakeBackend` | (none) | The gateway pipeline and MCP server behind the front door |
| Network | `Network` | (none) | Real HTTPS; injected so tests can make the IdP unreachable |
| Clock | `FakeClock` | (none) | Wall-clock time; injected so lifetime and rate-limit tests are deterministic |

`EntraIdentityProvider` raises `RuntimeError` unless constructed with `enable_network=True`, and its methods raise `NotImplementedError`. The `production-adapter-is-gated` scenario checks both.

## Extend it

- **Add an MCP server.** Add an `McpServer` to `DEFAULT_SERVERS` and a matching `ApiApp` to `DEFAULT_APIS` (the fake IdP's list is separate). On the front door side, PRM, the shim's `scopes_supported`, and the resource-to-scope map are all derived from `config.servers`, as they should be in production. Add a negative test for cross-server replay.
- **Add a client type.** Add a `ClientProfile` with the behavior you saw in a live probe, and add it to the `--demo` table. Keep the label generic.
- **Model another IdP behavior.** Change `FakeIdentityProvider`, for example to accept `resource` when the App ID URI matches the resource URI, then check which variants still need the façades.
- **Model External ID or several customer tenants.** Construct `FakeIdentityProvider(clock, tenant_id=...)` with another tenant, give each resource path its own issuer, and assert that each route rejects the other tenant's tokens.
- **Wire a real IdP.** Implement `IdentityProvider` against your tenant in your own code. Keep the in-memory fake for the self-test.

## Limitations

- HS256 test tokens; no asymmetric signatures, key rotation, or real discovery.
- No real Entra ID or External ID behavior; the fake IdP is a model to verify against current documentation.
- API Management policy expressions aren't compiled or executed; the handlers reimplement their logic in Python.
- No CORS preflight (`OPTIONS`) handling, and browser-specific IdP behavior such as SPA redirect URIs and `Origin` rules isn't modeled.
- No consent screens, Conditional Access, or user interaction; the fake IdP signs in the same user every time.
- One process: no distributed rate-limit counters, cache expiry races, or multiple gateway regions.
- Client ID Metadata Documents and Variant D aren't simulated.

## Related

- [Asset README](../README.md)
- [Architecture: wire contracts](../docs/architecture.md#13-wire-contracts)
- [Authentication and governance](../docs/authentication-and-governance.md)
- [Evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md)
- [Policy files](../docs/policies/README.md)
