# Backend Identity Propagation: Evaluation and Troubleshooting

How to prove that every backend call carries the right user, and only that user; how to gate releases; and how to diagnose propagation failures. Wire contracts and component behavior are in [architecture.md](architecture.md). All hosts, IDs and values are placeholders.

## 1. Scenario catalog

S1 to S27 each have an offline version in the [simulator](../samples/identity_propagation_sim.py), named in the Simulator column, and a live version in [section 3](#3-live-probe-checklist). S28 to S32 need real infrastructure and are live only.

| # | Scenario | Expected result | Simulator | Live |
|---|---|---|---|---|
| S1 | Option A: two calls by one user | One exchange; second call is a cache hit; downstream `aud` is the backend; the client's token never reaches the backend | `obo-cache-hit` | Probe 3 |
| S2 | Option A: two users in one tenant, interleaved | Each sees only their rows; one cache entry per user | `obo-no-cross-user-leak` | Probe 3, section 2 |
| S3 | Option A: cached token near expiry | Refreshed before expiry minus skew, never served after | `obo-expiry-skew` | Probe 3 |
| S4 | Option A: consent missing, MFA required, access blocked, IdP outage | `403 consent_required`, `401 interaction_required` with a claims challenge, `403 access_blocked`, `503`; none cached; no IdP error text in the response | `obo-errors-not-cached` | Probe 3 |
| S5 | Option A: exchange with a secret, an unfederated identity, or an app-only token | Exchange refused; backend not called | `obo-requires-federated-credential` | Probe 3 |
| S6 | Option B: valid assertion | Backend returns the linked user's rows; `act`, `cnf`, `jti` and `req_id` present; no `Authorization` header at the backend | `assertion-valid` | Probe 4 |
| S7 | Option B: expired, too long-lived, or not-yet-valid assertion | Backend rejects | `assertion-expired` | Probe 5 |
| S8 | Option B: wrong audience or issuer | Backend rejects | `assertion-wrong-aud` | Probe 5 |
| S9 | Option B: replayed `jti` | Backend rejects, including after expiry | `assertion-replayed-jti` | Probe 5 |
| S10 | Option B: tampered claims, empty signature, malformed, impostor key with a valid `kid` | Backend rejects | `assertion-tampered` | Probe 5 |
| S11 | Option B: `alg=none`, `HS256`, an IdP token, or the wrong `typ` | Backend rejects | `assertion-alg-none-rejected` | Probe 5 |
| S12 | Option B: assertion for another tenant or an unknown backend user; cross-tenant link | Backend rejects; link write refused | `assertion-wrong-tenant` | Probe 5, section 2 |
| S13 | Option B: assertion presented from outside the gateway connection, or with duplicate headers | Backend rejects and alerts | `assertion-bound-to-gateway-connection` | Probe 6 |
| S14 | Option B: signing key rotation | New `kid` accepted after publication; old key rejected after withdrawal | `signing-key-rotation` | S28 |
| S15 | Option B: unlinked user | `403 identity_not_linked` with a link URL; backend not called | `unlinked-user-denied` | Probe 4 |
| S16 | Link revoked; second identity claims a linked account | Denied; conflict logged | `link-revocation-and-conflict` | Probe 9 |
| S17 | Link store outage | Cached links served until expiry, then `503`; no fallback credential | `link-store-outage-fails-closed` | Probe 9 |
| S18 | Option C: wrong tenant key, cross-tenant order, rotated key | Denied; rotated key recovered with at most one retry | `bridge-wrong-tenant-key-denied` | Probe 7 |
| S19 | Option C: acting-user header from outside the gateway, or set by the client | Backend rejects direct calls; gateway overwrites the header | `bridge-spoofed-acting-user-non-gateway-denied` | Probe 6, Probe 7 |
| S20 | Option C: tool not on the allow-list, path traversal argument | Denied before any secret or link read | `bridge-tool-allow-list` | Probe 7 |
| S21 | Client sends identity, credential or correlation headers | Stripped on every API, in any letter case | `client-identity-headers-stripped` | Probe 2 |
| S22 | Token passthrough | Backend rejects the client's token by audience or algorithm | `passthrough-token-rejected-by-audience` | Probe 2 |
| S23 | App-only call with a `user` argument | Argument dropped; backend sees no user; missing app role denied | `app-only-ignores-user-arg` | Probe 8 |
| S24 | Agent acting as itself; agent acting for a user | Agent's own backend account without `act`; user call records the agent as actor | `agent-identity-acts-as-itself` | Probe 8 |
| S25 | Bad inbound token | `401` or `403` at the gateway; no exchange, link, vault or backend work | `inbound-token-validated-first` | Probe 1 |
| S26 | Production adapters | Refuse to run without explicit opt-in | `production-adapter-is-gated` | Not applicable |
| S27 | Logs | No token, assertion or key in any log; every record has a request ID | `no-secrets-in-audit` | Probe 10 |
| S28 | Signing key rotation in Key Vault | Next key published to the JWKS before use; overlap; old key withdrawn | Partly (S14) | Rotation drill |
| S29 | Gateway client certificate rotation | Backend accepts old and new thumbprints during overlap; no failed calls | No | Rotation drill |
| S30 | Offboarding | User disabled in Entra and link revoked: all options stop within the configured cache bounds | Partly (S16) | Probe 9 |
| S31 | Several gateway instances or regions | No cross-user cache hits; backend replay cache shared across backend instances | No | Load test with section 2 users |
| S32 | Real MCP clients and Conditional Access | Each supported client handles `interaction_required` and the claims challenge | No | Probe 3 |

## 2. Two-user, two-tenant isolation matrix

Cross-user and cross-tenant leakage is the failure that matters most, and it can hide behind caches. Run this matrix in every environment, with cold and warm caches, and interleave the users rather than running them in sequence.

Create test identities: A1 and A2 in customer tenant A, B1 in customer tenant B, and an agent app registered in tenant A. Seed each backend so every identity owns at least one distinguishable row.

| Call | As A1 | As A2 | As B1 | As agent |
|---|---|---|---|---|
| Option A tool (`list_my_reports`) | A1's rows only | A2's rows only | B1's rows, or `consent_required` if B hasn't consented | Denied (no OBO for app-only tokens) |
| Option B tool (`search_work_orders`) | A1's linked user's rows | A2's rows, or `identity_not_linked` | B1's rows from B's instance only | The agent's own backend account |
| Option C tool (`get_order_status`) on A's order | Allowed | Allowed if the backend lets A2 see it | `backend_denied` | Depends on the agent's link and allow-list |
| Option C tool on B's order | `backend_denied` | `backend_denied` | Allowed | `backend_denied` |
| App-only tool (`list_order_types`) | Reference data | Same | Same | Same, if the agent has the role |

Then repeat each row in order A1, A2, A1, B1, A1 within one cache lifetime and confirm the results don't change. A different result on the second A1 call means a cache key is missing a user or tenant component.

## 3. Live probe checklist

Run against a test environment with test identities. Replace placeholders with your values. On Windows PowerShell, call `curl.exe` explicitly. Get tokens with your test client, keep them in environment variables, decode them locally only, and never paste them into tickets or online decoders.

The probes call `tools/call` directly. If your MCP server requires `initialize` and a session first, run it and add the `Mcp-Session-Id` header.

```bash
TOOL='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"search_work_orders","arguments":{"status":"open"}}}'
```

**Probe 1: inbound validation (S25).**

```bash
curl -si -X POST https://mcp.contoso.example/field-service/mcp -H "Authorization: Bearer $OTHER_AUDIENCE_TOKEN" -H "Content-Type: application/json" -d "$TOOL"
curl -si -X POST https://mcp.contoso.example/field-service/mcp -H "Authorization: Bearer $EXPIRED_TOKEN" -H "Content-Type: application/json" -d "$TOOL"
curl -si -X POST https://mcp.contoso.example/field-service/mcp -H "Authorization: Bearer $NO_SCOPE_TOKEN" -H "Content-Type: application/json" -d "$TOOL"
```

Expect `401 invalid_token`, `401 invalid_token` and `403 insufficient_scope`. Confirm in the gateway logs that none of these caused a token exchange, link lookup, Key Vault call or backend request.

**Probe 2: header stripping and passthrough (S21, S22).** Point a test route at a backend that echoes request headers. Call each API as A1 and add spoofed headers:

```bash
curl -si -X POST https://mcp.contoso.example/field-service/mcp -H "Authorization: Bearer $A1_TOKEN" \
  -H "X-User-Assertion: forged" -H "x-acting-user: fs-1002" -H "X-Api-Key: guess" \
  -H "X-Contoso-Tenant: northwind" -H "X-Request-Id: client-chosen" -H "Content-Type: application/json" -d "$TOOL"
```

The echo must show only gateway-set values, and an `X-Request-Id` the gateway generated. Then, from a test host allowed to reach the backend, send A1's gateway token straight to each backend: it must be rejected by audience (Option A backend) or by algorithm and type (Option B backend).

**Probe 3: Option A (S1 to S5, S32).**

- Call `list_my_reports` twice as A1, then as A2, then as A1. Check the audit records: `cacheHit` false, true, false, true; different `tokenHashPrefix` per user.
- Call as B1 before tenant B grants admin consent: `403 consent_required`. Grant consent and retry: success with no restart.
- Apply a test Conditional Access policy that requires MFA for the downstream API. Call as A1 with a single-factor session: `401 interaction_required` with a claims challenge. Confirm each supported MCP client re-authenticates and the next call succeeds (S32).
- Call with the agent's app-only token: denied, backend not called.
- Remove the federated identity credential in a test copy of the app registration: exchanges fail and the gateway returns `502` without calling the backend.

**Probe 4: Option B happy path and linking (S6, S15).**

- Call `search_work_orders` as A1. In the backend's test log, confirm `iss`, `aud`, `sub` (the backend user), `tenant_id`, `act.sub` (the MCP client), `scope`, a fresh `jti`, a short `exp`, `req_id` equal to the gateway request ID, and `cnf` equal to the gateway certificate thumbprint.
- Call as a user with no link: `403 identity_not_linked` with the link URL; the backend log shows no request. Complete linking, wait for the negative cache entry to expire, and retry.

**Probe 5: Option B negative vectors (S7 to S12).** Live gateways mint only valid assertions, so test the backend verifier directly. In the test environment only, register a separate test signing key in the backend's trusted JWKS, and generate a vector set: expired, `nbf` in the future, lifetime above the maximum, wrong `aud`, wrong `iss`, modified payload, empty signature, `alg=none`, `HS256`, wrong `typ`, unknown `kid`, other tenant, unknown `sub`, and a valid assertion sent twice. Send each from the gateway connection. Every vector except the first use of the valid one must be rejected with a distinct reason in the backend log. Remove the test key from the JWKS afterwards and confirm the valid vector now fails.

**Probe 6: connection binding (S13, S19).** From a host on the same network that is not the gateway, call the Option B and Option C backends directly with a captured-in-test assertion or the test tenant key. Expect a TLS handshake failure or a rejection before business logic, and an alert. Repeat with a second, valid client certificate that isn't the gateway's: Option B must reject on `cnf`.

**Probe 7: Option C (S18 to S20).**

- Call `get_order_status` as A1 for an A order (allowed) and a B order (`backend_denied`).
- Call `cancel_order`: denied by the allow-list; Key Vault and link store logs show no reads.
- Call with `order_id` set to `../../admin`: `invalid_params`.
- Rotate tenant A's key in Key Vault and the backend together: the next call may fail once, then succeeds; there is never more than one retry.
- Confirm the backend sees `X-Acting-User` equal to A1's linked backend user, even when the client sent a different value.

**Probe 8: app-only and agent (S23, S24).**

- Call `list_order_types` with an argument `{"user": "fs-1002"}`: reference data returned; the backend saw no user claim or header.
- Call `search_work_orders` with the agent's app-only token: the backend sees the agent's own account and no `act`.
- Call as A1 through the agent's client: the backend sees A1 as `sub` and the agent client as `act.sub`.

**Probe 9: revocation and outage (S16, S17, S30).**

- Disable A2 in Entra and revoke sessions. Record when each option stops working: Option A when the cached downstream token expires and the next exchange fails; Options B and C when the inbound token expires or the link is revoked, bounded by the link cache. Compare with the bounds you documented; the probe proves the bound, not a number.
- Revoke A1's link: Options B and C deny within the link cache lifetime.
- Try to link a second test identity to A1's backend account: refused and logged as a conflict.
- Block the link store from the gateway: cached users continue until expiry, uncached users get `503`, and no call falls back to a tenant key or app-only token.

**Probe 10: logging hygiene (S27).** Export a sample of gateway diagnostics, gateway traces, MCP server logs and backend logs from the probes above. Search for `eyJ`, `Bearer `, the test tenant keys and `client_assertion`. The expected count is zero.

## 4. Release gates

| Gate | Requirement | Blocking |
|---|---|---|
| Simulator | `python samples/identity_propagation_sim.py --self-test` exits `0` | Yes |
| ADR 0001 | No route forwards the client's token; the backend audience check is verified by Probe 2 | Yes |
| Isolation matrix | Section 2 passes with cold and warm caches, in every region | Yes |
| Negative vectors | Probe 5 passes against every Option B backend | Yes |
| Header stripping | Probe 2 passes for every API, including any newly added identity headers | Yes |
| Logging hygiene | Probe 10 finds no secrets | Yes |
| Option C exception | Each bridged API has an owner, a tool allow-list, network restriction and a sunset date ([ADR 0002](decisions/0002-signed-assertion-over-shared-api-keys.md)) | Yes |
| Rotation drills | S28 and S29 run in the last rotation period | No, but tracked |
| Offboarding drill | Probe 9 results match the documented revocation bounds | No, but tracked |
| Alerts | Signature, replay, `identity_not_linked` spikes, link conflicts and direct-backend alerts configured | No, but tracked |

## 5. Troubleshooting

IdP error codes and messages change. The causes below are generic; match them to the current Microsoft Entra error code reference.

| Symptom | Likely cause | Check | Fix |
|---|---|---|---|
| `401 invalid_token` at the gateway with a fresh token | Audience, issuer or token version mismatch | Decode `aud`, `iss` and `ver` locally | See the [MCP OAuth front door](../../mcp-oauth-front-door/README.md) |
| `403 consent_required` for every user in one tenant | Downstream API not consented in that tenant | Enterprise applications in the customer tenant | Tenant administrator grants admin consent; add consent to onboarding |
| `401 interaction_required` repeating | Client doesn't handle the claims challenge, or the gateway retries | Response headers; client version | Return the challenge unchanged; don't retry exchanges in a loop; record client support in S32 |
| `502 backend_auth_failed` on Option A for everyone | Federated identity credential wrong (issuer, subject or audience), or the wrong managed identity | Federated credential on the app registration versus the managed identity's object ID | Fix the federated credential; never fall back to a client secret |
| Option A works for users, fails for agents | App-only tokens can't be exchanged on behalf of a user | Token `idtyp` or the absence of `scp` | Route agents through the agent identity path ([architecture §8](architecture.md#8-agent-identity)) |
| A user sees another user's rows | Cache key missing `oid` or `tid`; shared HTTP client caching responses | Cache key construction; response cache headers | Key by `tid`, `oid`, audience and scope hash; disable response caching on user routes; rerun section 2 |
| `identity_not_linked` for a user who linked | Negative cache not yet expired; link written under a different `tid` and `oid`, such as a guest account in another tenant | Link record versus the token's `tid` and `oid` | Wait or evict; link guest accounts per tenant; never key links on email |
| All Option B calls fail after a key change with `unknown_kid` | Gateway switched to a key the backend hasn't fetched | Backend JWKS cache; publication time of the next key | Publish the next key, wait for backend caches, then switch ([architecture §5.7](architecture.md#57-signing-keys-and-rotation)) |
| Intermittent `expired` or `not_yet_valid` | Clock skew | Time sync on gateway and backend hosts | Fix time sync; keep a small configured skew |
| `replayed` (jti already seen) on legitimate calls | Gateway or HTTP client retries reuse the same assertion | Retry policy on the backend call | Mint a fresh assertion per attempt |
| `cnf_mismatch` after a certificate renewal | Thumbprint named value or backend allow-list not updated | Gateway certificate versus `cnf` versus backend allow-list | Rotate certificate, thumbprint and allow-list together with overlap |
| `wrong_instance` from the backend | Tenant map points to the wrong backend tenant, or tampering | Tenant directory entry for `tid` | Fix the map; investigate as a security event if unexplained |
| Option C `502` after a key rotation | Stale cached tenant key | Key Vault secret version versus cache age | Evict and retry once; shorten the cache if rotations are frequent |
| Option C backend accepts calls from ops tooling | Network restriction or mTLS missing on the backend | Backend access logs by source | Restrict to the gateway path; alert on any other source; prioritize migration to Option B |
| Key Vault `429` on signing | Signing per call exceeds vault limits | Vault metrics | Move signing to code with a cached key handle, add vaults, or use Managed HSM |
| `503` with link store errors | Link store unavailable; fail closed is working | Link store health | Restore the store; don't add a fallback credential |
| `backend_denied` with `insufficient_role` on app-only calls | App role not assigned to the gateway's managed identity | Enterprise application role assignments | Assign the role; keep it read-only |

## 6. Diagnostics

The queries assume the gateway writes the audit record in [architecture §14](architecture.md#14-audit) to a custom table `McpPropagation_CL` and the backend writes its verification log to `ErpBackendAudit_CL`. Column names depend on how you ingest them; tables created through the classic custom log API add type suffixes such as `_s`. Adjust to what your workspace shows.

Outcomes by propagation option and reason:

```kusto
McpPropagation_CL
| where TimeGenerated > ago(1h)
| summarize calls = count() by propagationOption, outcome, reason, bin(TimeGenerated, 5m)
| order by TimeGenerated desc
```

Unlinked-user denials by tenant, to spot onboarding gaps or a broken link store:

```kusto
McpPropagation_CL
| where TimeGenerated > ago(24h)
| where reason == "identity_not_linked"
| summarize denials = count(), users = dcount(sub) by tenant, bin(TimeGenerated, 1h)
```

A downstream token served to more than one user. The expected result is empty:

```kusto
McpPropagation_CL
| where TimeGenerated > ago(24h)
| where propagationOption == "obo" and isnotempty(tokenHashPrefix)
| summarize users = dcount(sub) by tokenHashPrefix, tenant
| where users > 1
```

Hash prefixes are short, so a rare collision is possible; check any hit against full request records before treating it as an incident.

Backend calls whose assertion the gateway didn't mint, or whose user differs from what the gateway sent. The expected result is empty:

```kusto
ErpBackendAudit_CL
| where TimeGenerated > ago(1h)
| where isnotempty(jti)
| join kind=leftouter (
    McpPropagation_CL
    | where TimeGenerated > ago(1h)
    | project assertionJti, gatewayUser = backendUserId, requestId
  ) on $left.jti == $right.assertionJti
| where isempty(requestId) or sub != gatewayUser
| project TimeGenerated, jti, sub, gatewayUser, sourceAddress, outcome
```

Verification rejections at the backend by reason, for signature, replay and connection-binding alerts:

```kusto
ErpBackendAudit_CL
| where TimeGenerated > ago(1h)
| where outcome == "rejected"
| summarize rejects = count() by reason, sourceAddress, bin(TimeGenerated, 5m)
```

Set alert thresholds from your own baseline. Correlate exchange failures with the Entra sign-in logs for the gateway's app registration over the same window to see Conditional Access results and error codes. Check the current documentation for which sign-in log category records on-behalf-of exchanges.
