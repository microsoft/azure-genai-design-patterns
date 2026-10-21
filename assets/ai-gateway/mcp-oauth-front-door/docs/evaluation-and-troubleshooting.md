# MCP OAuth Front Door: Evaluation and Troubleshooting

How to prove the front door works for the clients you support, how to gate releases, and how to diagnose sign-in failures. Wire contracts and component behavior are in [architecture.md](architecture.md). All hosts, IDs and values are placeholders.

## 1. Scenario catalog

Every scenario has an offline version in the [simulator](../samples/oauth_front_door_sim.py) and a live version in [section 3](#3-live-probe-checklist).

| # | Scenario | Expected result | Simulator | Live |
|---|---|---|---|---|
| S1 | MCP request without a token | `401`, `WWW-Authenticate` with `resource_metadata` for this server and `scope` | Yes | Probe 1 |
| S2 | PRM for each MCP server | `200`, `resource` equals the MCP URL, `authorization_servers` and `bearer_methods_supported` present | Yes | Probe 2 |
| S3 | PRM for an unknown path | `404` | Yes | Probe 2 |
| S4 | Discovery of the authorization server | Metadata found at a URL the client tries; `S256` advertised | Yes | Probe 3 |
| S5 | Registration with a loopback redirect URI | `201` with the shared public client ID | Yes | Probe 4 |
| S6 | Registration with a disallowed redirect URI | `400 invalid_redirect_uri` | Yes | Probe 4 |
| S7 | Authorize with `resource` and PKCE | `302` to the IdP with mapped `scope`, unchanged `state` and PKCE values, no `resource` | Yes | Probe 5 |
| S8 | Authorize without PKCE, or with `plain` | `400` error page, no redirect | Yes | Probe 5 |
| S9 | Authorize with an unknown client or redirect URI | `400` error page, no redirect | Yes | Probe 5 |
| S10 | Authorize with an unknown `resource` | `400 invalid_target` | Yes | Probe 5 |
| S11 | Token exchange with the correct verifier | `200` with an access token whose `aud` is this server's API | Yes | Probe 6 |
| S12 | Token exchange with the wrong verifier | `400 invalid_grant` from the IdP | Yes | Probe 6 |
| S13 | Token for another MCP server | `401 invalid_token` | Yes | Probe 7 |
| S14 | Token from another issuer | `401 invalid_token` | Yes | Probe 7 |
| S15 | Token without the required scope | `403 insufficient_scope` | Yes | Probe 7 |
| S16 | Backend request after validation | No client `Authorization` header reaches the backend | Yes | Backend echo |
| S17 | Refresh token grant | New access token with the same audience | Yes | Probe 6 |

## 2. Client and variant matrix

Fill this in from probes, not from documentation or memory. Client behavior changes between releases, so record the client version and the date.

| Client (type and version) | Reads `resource_metadata` | Discovery URLs tried | Needs `S256` advertised | Needs `registration_endpoint` | Sends `resource` | Browser-based | Minimum variant | Probe date |
|---|---|---|---|---|---|---|---|---|
| Client type A, version x | | | | | | | | |
| Client type B, version y | | | | | | | | |
| Client type C, version z | | | | | | | | |

How to fill it in:

- Point the client at a test MCP route behind the front door with request logging on (no bodies on `/auth/*`).
- Record which well-known URLs it requests, in what order, and where it stops.
- Record the parameters on the authorize request (`resource`, `scope`, `code_challenge_method`, `client_id`).
- Record whether it calls the registration endpoint and what it sends.
- Repeat with each variant until you find the lowest one that completes sign-in and a tool call.

## 3. Live probe checklist

Run against a test environment. Replace placeholders with your values. On Windows PowerShell, call `curl.exe` explicitly. Don't paste real tokens into shared terminals or tickets.

**Probe 1: challenge.**

```bash
curl -si -X POST https://mcp.contoso.example/orders/mcp \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"<version>","capabilities":{},"clientInfo":{"name":"probe","version":"1.0.0"}}}'
```

Expect `401`, a `WWW-Authenticate` header with `resource_metadata="https://mcp.contoso.example/.well-known/oauth-protected-resource/orders/mcp"`, and `Access-Control-Expose-Headers: WWW-Authenticate`.

**Probe 2: protected resource metadata.**

```bash
curl -s https://mcp.contoso.example/.well-known/oauth-protected-resource/orders/mcp
curl -si https://mcp.contoso.example/.well-known/oauth-protected-resource/unknown/mcp
```

Expect JSON whose `resource` is exactly `https://mcp.contoso.example/orders/mcp`, then `404` for the unknown path. Repeat with an `Origin` header and check the CORS response.

**Probe 3: discovery.** Try every URL a client might derive from the `authorization_servers` value, and record which ones answer:

```bash
curl -si https://mcp.contoso.example/.well-known/oauth-authorization-server/auth
curl -si https://mcp.contoso.example/.well-known/openid-configuration/auth
curl -si https://mcp.contoso.example/auth/.well-known/openid-configuration
```

For Variant A, run the same three derivations against the IdP issuer. Check that the document you get has an `issuer` equal to the value in PRM, and that `code_challenge_methods_supported` contains `S256`.

**Probe 4: registration shim (only if enabled).**

```bash
curl -si -X POST https://mcp.contoso.example/auth/register \
  -H "Content-Type: application/json" \
  -d '{"client_name":"probe","redirect_uris":["http://127.0.0.1:53682/callback"],"token_endpoint_auth_method":"none"}'

curl -si -X POST https://mcp.contoso.example/auth/register \
  -H "Content-Type: application/json" \
  -d '{"client_name":"probe","redirect_uris":["https://attacker.example/callback"]}'
```

Expect `201` with the shared client ID, then `400 invalid_redirect_uri`. Send a burst of requests and check that rate limiting answers `429`.

**Probe 5: authorize façade.** Don't follow redirects; inspect the `Location` header.

```bash
curl -si "https://mcp.contoso.example/auth/authorize?response_type=code&client_id=22222222-2222-2222-2222-222222222222&redirect_uri=http%3A%2F%2F127.0.0.1%3A53682%2Fcallback&scope=mcp.tools&state=probe1&code_challenge=E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM&code_challenge_method=S256&resource=https%3A%2F%2Fmcp.contoso.example%2Forders%2Fmcp"
```

Expect `302` to the IdP authorize endpoint with `scope=api://11111111-1111-1111-1111-111111111111/mcp.tools offline_access` (URL-encoded), `state=probe1`, the same challenge, and no `resource`. Then repeat with:

- no `code_challenge` → `400`, no `Location`
- `code_challenge_method=plain` → `400`, no `Location`
- `client_id=99999999-9999-9999-9999-999999999999` → `400`, no `Location`
- `redirect_uri=https%3A%2F%2Fattacker.example%2Fcb` → `400`, no `Location`
- `resource=https%3A%2F%2Fmcp.contoso.example%2Funknown%2Fmcp` → `400 invalid_target`

**Probe 6: full sign-in and token.** Use a test client or a small script that runs a loopback listener, opens the authorize URL in a browser, receives the code and posts to `/auth/token` with the verifier. Confirm:

- the token response has `Cache-Control: no-store`
- the access token's decoded `aud`, `iss`, `scp`, `azp` and `tid` match [architecture section 13.9](architecture.md#139-access-token-claims-decoded-payload)
- a second redemption of the same code fails
- a redemption with a different verifier fails
- a `refresh_token` grant returns a token with the same audience

Decode tokens locally only. Don't paste them into online decoders.

**Probe 7: validation.**

```bash
curl -si -X POST https://mcp.contoso.example/orders/mcp -H "Authorization: Bearer $ORDERS_TOKEN" ...
curl -si -X POST https://mcp.contoso.example/orders/mcp -H "Authorization: Bearer $KNOWLEDGE_TOKEN" ...
curl -si -X POST https://mcp.contoso.example/orders/mcp -H "Authorization: Bearer $OTHER_TENANT_TOKEN" ...
```

Expect success, `401 invalid_token`, and `401 invalid_token`. A token with the right audience but without `mcp.tools` should get `403 insufficient_scope`.

**Backend echo.** Point a test route at a backend that echoes request headers, and confirm the client's `Authorization` header doesn't arrive.

## 4. Release gates

| Gate | Requirement | Blocking |
|---|---|---|
| Simulator | `python samples/oauth_front_door_sim.py --self-test` exits `0` | Yes |
| Configuration consistency | PRM, metadata shim, allow-lists and resource-to-scope map generated from one source; diff reviewed | Yes |
| Negative probes | Probes 2, 4, 5 and 7 negative cases pass in the target environment | Yes |
| Client matrix | Every supported client completes sign-in and one tool call with the chosen variant | Yes |
| Logging hygiene | A sample of gateway logs for `/auth/*` contains no codes, verifiers or tokens | Yes |
| Registration shim | Disabled, or an approved ADR exists with an owner and review date | Yes |
| Alerts | Rejection, registration-burst and validation-failure alerts configured | No, but tracked |

## 5. Troubleshooting

IdP error messages and codes change. The descriptions below are generic; match them to the current error code reference in Microsoft Learn.

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| Client never opens a browser after the `401` | Client didn't find or didn't trust PRM | Gateway logs for PRM requests; `WWW-Authenticate` exposed to browsers | Fix the `resource_metadata` URL; expose the header; check CORS on PRM |
| Client fetches PRM, then stops | `resource` in PRM doesn't match the URL it called | Compare the strings exactly, including trailing slash and case | Generate PRM from the route definition |
| Client fetches PRM, then requests several well-known URLs and stops | No metadata at a URL the client derives, or `S256` not advertised | Probe 3 | Add the metadata shim (Variant B), or serve it at the URL the client tries |
| Client fails with "registration not supported" or similar | Client requires a `registration_endpoint` | Probe 4; client logs | Pre-register if the client allows it; otherwise C+R with the shim governance in ADR 0002 |
| IdP error: the resource parameter doesn't match the requested scopes | Client sent `resource` straight to the IdP | Authorize URL in the browser | Route through the authorize façade (Variant C), or align the App ID URI with the resource |
| IdP error: invalid resource or the application wasn't found in the tenant | Scope names an API app that doesn't exist in this tenant, or isn't consented | Mapped scope; enterprise applications in the tenant | Fix the map; grant admin consent in that tenant |
| IdP error: redirect URI doesn't match | The client's loopback path or HTTPS URI isn't registered on the client app | Redirect URI in the authorize URL versus the app registration | Register the exact path; keep the shim allow-list generated from the registration |
| IdP error: PKCE required or code challenge invalid | Challenge missing or not S256 | Authorize URL | Fix the client or the façade; never fall back to `plain` |
| IdP error on token: verifier doesn't match | Client used a different verifier, or something altered it | Token façade transformations | Pass `code_verifier` through unchanged |
| IdP error on token: cross-origin redemption only allowed for single-page applications | Browser client redeeming a code for a client registered as native | `Origin` on the forwarded request; platform of the redirect URI | Register the redirect URI under the single-page application platform |
| IdP error on token: code already redeemed or expired | Retry after success, or a slow user | Client retry logic | Start a new sign-in; codes are single-use |
| `401 invalid_token` at the MCP route with a fresh token | Audience mismatch (v1 versus v2, App ID URI versus GUID, or the wrong API) | Decode `aud` and `ver` locally | Set the API's accepted token version to 2, or accept both audience forms; fix the map |
| `401 invalid_token` for one customer only | Issuer or `tid` not in the allow-list | Decode `iss` and `tid` | Add the tenant through the onboarding process |
| `403 insufficient_scope` | Token lacks `mcp.tools`, or app-only token without the role | Decode `scp` or `roles` | Request the right scope; assign the app role |
| Users signed out frequently | No refresh token issued | Token response | Add `offline_access` in the façade mapping and grant it |
| Consent screen names the shared client, not the real client | Registration shim returns a shared client ID | Expected | Pre-register per client family, or move to Variant D |
| Registration endpoint returning `429` for legitimate users | Rate limit too tight for shared egress | Caller IP distribution | Tune the limit, or pre-register clients behind shared egress |
| Sign-in works, then the backend rejects calls | Backend expected the client token (passthrough) | Backend auth logs | Use backend identity propagation; never forward the client token |
| Signature validation fails for everyone after a period of working | Pinned signing keys after IdP key rollover | Validation failure reasons | Use `openid-config` discovery |

## 6. Diagnostics

Example queries for API Management resource logs in Log Analytics. Column names depend on your diagnostic settings and log schema; adjust to what your workspace shows.

Rejections on front door operations by status:

```kusto
ApiManagementGatewayLogs
| where TimeGenerated > ago(1h)
| where Url has "/auth/" or Url has "/.well-known/"
| summarize count() by OperationId, ResponseCode, bin(TimeGenerated, 5m)
| order by TimeGenerated desc
```

Challenges versus authenticated calls per MCP route:

```kusto
ApiManagementGatewayLogs
| where TimeGenerated > ago(1h)
| where Url has "/mcp"
| summarize challenges = countif(ResponseCode == 401), forbidden = countif(ResponseCode == 403), ok = countif(ResponseCode < 400) by ApiId, bin(TimeGenerated, 5m)
```

Registration bursts by caller:

```kusto
ApiManagementGatewayLogs
| where TimeGenerated > ago(1h)
| where Url has "/auth/register"
| summarize attempts = count(), rejected = countif(ResponseCode >= 400) by CallerIpAddress
| where attempts > 20
```

The threshold is an example; set it from your own baseline. Correlate these with IdP sign-in logs filtered to the public client ID to see Conditional Access results and IdP error codes for the same time window.
