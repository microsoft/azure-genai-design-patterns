# Samples: Backend Identity Propagation Simulator

`identity_propagation_sim.py` is a single-file, standard-library-only simulator of the
pattern. The parties are a fake Entra-like identity provider, the MCP gateway, an
identity link store, a fake Key Vault, and four fictional Contoso ERP backends
(`reporting`, `field-service`, `orders`, and an app-only `orders-reference` endpoint).
It runs all three propagation options from
[architecture.md](../docs/architecture.md), plus app-only calls, an agent acting as
itself, and the token passthrough anti-pattern, and it shows the backend rejecting
each forged, replayed, or misrouted credential.

The simulator models the decisions and the wire contracts. It does not model API
Management, Microsoft Entra ID, Azure Key Vault, or TLS. Use it to read the logic, to
argue about edge cases, and as an executable specification for your own gateway or
MCP server tests.

> **Test only.** Tokens are HS256 and signed with in-memory secrets. Assertions carry
> an `ES256` header but are HMAC-signed by `SimulatedSigningKey`. The backend gets a
> verify-only view that stands in for a JWKS endpoint. Certificates are labels hashed
> into thumbprints, and there is no network. Production uses asymmetric keys held in
> Key Vault or an HSM, real JWKS discovery, and real mTLS
> ([RFC 8705](https://www.rfc-editor.org/rfc/rfc8705)). Do not copy the crypto.

## Prerequisites

- Python 3.10 or later. No packages to install.

## Run it

From the repository root:

```powershell
python assets/ai-gateway/backend-identity-propagation/samples/identity_propagation_sim.py --self-test
python assets/ai-gateway/backend-identity-propagation/samples/identity_propagation_sim.py --demo
```

- `--self-test` runs every scenario, prints `PASS <scenario>` or `FAIL <scenario>`,
  then `N/27 scenarios passed`. It exits 0 when all pass and 1 otherwise.
- `--demo` narrates Option A, Option B, Option C, an app-only call, an agent acting as
  itself, and a token passthrough attempt, then prints the audit trail. It never
  prints a token and redacts `X-Api-Key`.
- With no argument it prints help and exits 2.

## The fictional world

All IDs are placeholders. Each GUID repeats one hex digit so it cannot be mistaken for
a real tenant, app, or object ID.

| Party | Identifier | Notes |
| --- | --- | --- |
| Gateway app (inbound audience) | `11111111-...` | Accepts tokens with scope `mcp.tools` or app role `Mcp.Agent` |
| MCP client | `33333333-...` | Recorded as the actor (`act`) in assertions |
| Gateway managed identity | `44444444-...` | The only credential allowed to exchange tokens (federated credential) |
| Rogue managed identity | `55555555-...` | Not federated; every exchange it attempts fails |
| Dispatch agent app | `bbbbbbbb-...` | App-only token with role `Mcp.Agent`; object ID `aaaaaaaa-...` |
| Gateway home tenant | `dddddddd-...` | Issues the managed identity tokens |

Customer tenants:

| Entra tenant | Backend tenant | Cell |
| --- | --- | --- |
| `00000000-...` | `fabrikam` | `cell-eu-1` |
| `66666666-...` | `northwind` | `cell-us-1` |
| `cccccccc-...` | none | Unknown tenant, refused at the gateway |

Users and identity links:

| User | Tenant | Backend user | How linked |
| --- | --- | --- | --- |
| Alice `22222222-...` | fabrikam | `fs-1001` | SCIM |
| Bob `77777777-...` | fabrikam | `fs-1002` | Just-in-time at first use |
| Dave `88888888-...` | fabrikam | `fs-1003` exists, no link | Denied with a link URL until he links |
| Carol `99999999-...` | northwind | `nw-2001` | Admin mapping |
| Erin `eeeeeeee-...` | northwind | none | Attempts a cross-tenant link; refused |
| Dispatch agent | fabrikam | `svc-dispatch-agent` | Admin mapping for the agent's own identity |

Backends and how the gateway reaches them (`BACKEND_CONFIG`):

| API | Option | Credential the backend verifies | Tools |
| --- | --- | --- | --- |
| `reporting` | A, on-behalf-of | Downstream token, `aud=api://contoso-erp-reporting`, scope `Reports.Read` | `list_my_reports` |
| `field-service` | B, signed assertion over mTLS | `X-User-Assertion` JWS, `aud=urn:contoso:erp:field-service`, `typ=user-assertion+jwt` | `search_work_orders` |
| `orders` | C, per-tenant bridge (transitional, has a sunset date) | `X-Api-Key` for the tenant plus `X-Acting-User`, only from the gateway connection | `get_order_status` (allow-listed); `cancel_order` is denied |
| `orders-reference` | App-only | Managed identity token with app role `Reference.Read` plus `X-Contoso-Gateway-Tenant` | `list_order_types` |

The assertion issuer is `https://mcp.contoso.example/assertions`. The signing key ID
is `gw-assert-2026-10`, and the rotation scenario introduces `gw-assert-2027-01`. The
per-tenant secret name follows `tenant-{tenantId}-orders-api-key`. Unlinked users are
sent to `https://mcp.contoso.example/link`.

The time-to-live values in the file (token cache skew, assertion lifetime, link cache,
negative link cache, tenant key cache, JWKS cache) exist only to drive the tests. They
are not sizing guidance.

## What the self-test proves

The "Covers" column links to the scenario catalog in
[evaluation-and-troubleshooting.md](../docs/evaluation-and-troubleshooting.md#1-scenario-catalog).

| Scenario | Covers | What it proves |
| --- | --- | --- |
| `obo-cache-hit` | S1 | Two calls cause one exchange. The gateway authenticates with a client assertion, not a secret. The downstream token has the backend audience, the user's `oid`, and the gateway as `azp`. The inbound token is never forwarded. |
| `obo-no-cross-user-leak` | S2 | Alice, Bob, then Alice again each see only their own rows. There are two exchanges and two cache keys, and the downstream tokens differ per user. |
| `obo-expiry-skew` | S3 | A call just before the skewed expiry hits the cache; a call at it refreshes. A skew of 0 is rejected. |
| `obo-errors-not-cached` | S4 | Consent, MFA, Conditional Access, and outage errors are never cached. MFA returns 401 with an `insufficient_claims` claims challenge and a step-up token succeeds. No IdP error text reaches the client. |
| `obo-requires-federated-credential` | S5 | A non-federated managed identity and a client secret both fail, and an app-only token cannot use the OBO route. The backend is not called. |
| `assertion-valid` | S6 | The header is exactly `ES256`, `typ`, and `kid`. The full claim set is present, with `act` naming the MCP client and `cnf` binding to the gateway certificate. `jti` and the request ID correlate between backend log and gateway audit. No `Authorization` header reaches the backend. |
| `assertion-expired` | S7 | Expired, too-long-lived, and not-yet-valid assertions are rejected; a control assertion passes. |
| `assertion-wrong-aud` | S8 | A wrong audience or issuer is rejected. |
| `assertion-replayed-jti` | S9 | A replayed `jti` is rejected, even after the assertion expires. Every gateway call uses a fresh `jti`. |
| `assertion-tampered` | S10 | Modified claims, an empty signature, malformed input, and an impostor key reusing a valid `kid` are rejected. |
| `assertion-alg-none-rejected` | S11 | `alg=none`, `HS256`, and an IdP token presented as an assertion fail the algorithm allow-list; the wrong `typ` fails too. |
| `assertion-wrong-tenant` | S12 | An assertion for another tenant or an unknown backend user is rejected. A cross-tenant link is refused at write time, and a legacy one is still denied at read time. |
| `assertion-bound-to-gateway-connection` | S13 | Calls from the ops subnet or the internet are rejected and raise alerts. A second certificate on the same network fails the `cnf` check. Duplicate assertion headers are rejected. |
| `signing-key-rotation` | S14 | A new `kid` is fetched on demand, old and new keys overlap, and a withdrawn key stops verifying after the JWKS cache expires. Forced refreshes are rate limited. The JWKS exposes public metadata only. |
| `unlinked-user-denied` | S15 | An unlinked user gets 403 with a link URL, and the backend is not called. No backend IDs leak. The negative result is cached, and a just-in-time link then works. |
| `link-revocation-and-conflict` | S16 | A revoked link is denied. A second identity cannot link to an account that is already linked, and the conflict is logged. |
| `link-store-outage-fails-closed` | S17 | During a link store outage, a cached link is still used, an uncached or stale one fails with 503, and the bridge reads no secrets. |
| `bridge-wrong-tenant-key-denied` | S18 | The acting user comes from the link, not the client. A key for another tenant and a cross-tenant order are both denied. A rotated key is absorbed with exactly one retry. |
| `bridge-spoofed-acting-user-non-gateway-denied` | S19 | Direct calls from outside the gateway path are rejected before business logic runs and raise alerts. A client-supplied `X-Acting-User` is replaced. |
| `bridge-tool-allow-list` | S20 | A tool that is not allow-listed, an unknown tool, and a path traversal argument all fail before any secret or link read. |
| `client-identity-headers-stripped` | S21 | On all four APIs, client-supplied identity, credential, and correlation headers are stripped in any letter case. Benign headers pass through. |
| `passthrough-token-rejected-by-audience` | S22 | Forwarding the client's token fails the audience check at `reporting` and the algorithm check at `field-service`. A downstream token presented to the gateway is rejected. |
| `app-only-ignores-user-arg` | S23 | A `user` argument is dropped and the backend sees no user claims. A missing app role is denied. |
| `agent-identity-acts-as-itself` | S24 | An agent token maps to the agent's own backend account with no `act` claim. The agent cannot use the OBO route. A user token via the agent client records the agent as actor. |
| `inbound-token-validated-first` | S25 | Bad audience, malformed, expired, unknown-tenant, and missing-scope tokens are rejected at the gateway, with zero backend, IdP, vault, or link work. |
| `production-adapter-is-gated` | S26 | The production adapters refuse to run without an explicit opt-in. |
| `no-secrets-in-audit` | S27 | No token, secret, or assertion appears in the audit log, link events, or backend logs. Hash prefixes are short, and every record has a request ID. |

Each check was mutation-tested by breaking the simulator on purpose and confirming
that at least one scenario failed. The mutations were: removing header stripping,
trusting any network path, allowing bridge writes, accepting `HS256` or `none`, and
setting the expiry skew to 0.

## How the simulator maps to API Management

| Simulator handler | Illustrative policy | APIM building blocks |
| --- | --- | --- |
| `Gateway._authenticate` | The assumed base API policy, described in the [policies README](../docs/policies/README.md#assumed-base-pipeline) | `validate-jwt` or `validate-azure-ad-token`, tenant allow-list, `return-response` with `WWW-Authenticate` |
| Header stripping | The base policy plus the first lines of each option's policy file | `set-header` with `exists-action="delete"`, one per header name |
| `Gateway._obo` | [obo-exchange.xml](../docs/policies/obo-exchange.xml) | `authentication-managed-identity`, `send-request` to the token endpoint, `cache-lookup-value` and `cache-store-value` |
| `Gateway._assertion` | [identity-link-lookup.xml](../docs/policies/identity-link-lookup.xml) and [signed-assertion.xml](../docs/policies/signed-assertion.xml) | `send-request` to the link store, `send-request` to Key Vault sign, client certificate on the backend |
| `Gateway._bridge` | [identity-link-lookup.xml](../docs/policies/identity-link-lookup.xml) and [tenant-credential-bridge.xml](../docs/policies/tenant-credential-bridge.xml) | Tool allow-list, `send-request` to Key Vault secrets, `cache-lookup-value` |
| `Gateway._app_only` | None | `authentication-managed-identity` with the reference API resource |

API Management has no built-in on-behalf-of or JWS-signing policy. The policy files are
illustrative and untested. See
[section 11 of architecture.md](../docs/architecture.md#11-implementing-in-api-management-or-in-the-mcp-server)
for when to do this work in the MCP server instead.

## Adapter seams

| Seam | In-memory fake | Gated production stub | Real implementation |
| --- | --- | --- | --- |
| `TokenExchanger.get_token` | `OnBehalfOfExchanger` over `FakeIdentityProvider` | `EntraOnBehalfOfExchanger` | MSAL confidential client using a managed identity federated credential |
| `IdentityLinkResolver.resolve` | `CachedLinkResolver` over `InMemoryIdentityLinkStore` | None | Your link store, for example a table keyed by `tid` and `oid` |
| `SecretStore.get_secret` | `FakeKeyVault` | None | Key Vault secrets client using managed identity |
| `AssertionSigner` (`kid`, `alg`, `sign`) | `SimulatedSigningKey` plus `JwksPublisher` | `KeyVaultAssertionSigner` | Key Vault or HSM sign over a SHA-256 digest |

Both stubs raise `RuntimeError` unless constructed with `enable_network=True`, and
raise `NotImplementedError` after that. Before you wire `KeyVaultAssertionSigner`,
verify that the signature format Key Vault returns for ES256 is the raw `R||S`
encoding that JWS ([RFC 7515](https://www.rfc-editor.org/rfc/rfc7515)) expects.

## Extend it

- Add a backend: add an entry to `BACKEND_CONFIG`, `TOOL_PARAMETERS`, and
  `BACKEND_ROUTES`, build a fake backend in `build_world`, and add scenarios for its
  failure modes.
- Add a scenario: write a `scenario_*` function that builds a fresh world, asserts
  with `check(...)`, and register it in `SCENARIOS`.
- Try a policy change: change the gateway logic, run `--self-test`, and confirm which
  scenarios fail. If none fail, the change is untested; add a scenario.

## Limitations

- No real cryptography, network, TLS, or token formats beyond what the checks need.
- One process and one clock. There is no distributed cache, so cache consistency
  across gateway instances is not modeled.
- The JSON-RPC error shape is simplified. Map it to your MCP server's error contract.
- On a 401 from a cached tenant key, the simulator evicts the key and retries once.
  The illustrative `tenant-credential-bridge.xml` evicts without retrying, so the next
  call picks up the new key.
- Entra Agent ID is not modeled. The agent is a plain app registration with an app
  role.

## Related

- [Architecture](../docs/architecture.md)
- [Authentication and governance](../docs/authentication-and-governance.md)
- [Evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md)
- [Multi-tenant MCP gateway](../../multi-tenant-mcp-gateway/README.md)
- [MCP OAuth front door](../../mcp-oauth-front-door/README.md)
