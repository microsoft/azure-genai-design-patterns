# Samples: gateway pipeline simulator

[`gateway_pipeline_sim.py`](gateway_pipeline_sim.py) simulates the per-call pipeline of the multi-tenant MCP gateway described in [the architecture](../docs/architecture.md#6-per-call-policy-pipeline). It runs offline, uses only the Python standard library, and needs no Azure resources.

It collapses two runtime pieces into one process so you can test the isolation rules without deploying anything:

- **The API Management policy pipeline:** token check, tenant from a token claim, tenant directory lookup, product entitlement, per-tenant rate limit, backend credential and cell selection, and an audit record.
- **The MCP adapter behind API Management:** `initialize`, `tools/list` filtered by the tenant's entitlements, and the same entitlement re-checked on every `tools/call`.

The simulator models the **logic** of the pipeline. It is not API Management, and it is not an MCP server you can connect a client to.

> **Test only.** The simulator builds and reads **unsigned** JWTs (`alg: none`) so that it can run offline. The real gateway validates signature, issuer, audience, lifetime, and scopes with `validate-jwt` or `validate-azure-ad-token`. Never reuse `decode_unsigned_jwt` or `make_test_token` outside a test harness.

## Prerequisites

- Python 3.10 or later.
- No packages, network access, or credentials.

## Run it

From the repository root:

```powershell
# Offline isolation scenarios. Prints PASS <scenario> per check; exits 0 on success, 1 on failure.
python assets/ai-gateway/multi-tenant-mcp-gateway/samples/gateway_pipeline_sim.py --self-test

# A short narrated run: list, call, list-skipping call, spoofed header, unknown tenant, no token,
# followed by the audit records the gateway wrote (which never contain bodies or tokens).
python assets/ai-gateway/multi-tenant-mcp-gateway/samples/gateway_pipeline_sim.py --demo
```

Running it with no arguments prints help and exits with code 2.

## The fictional tenants and tools

| Tenant key | Status | Products | Cell | Notes |
|---|---|---|---|---|
| `contoso` | active | `orders`, `field-service`, `reporting` | Cell A | Sees every tool; write tools still need the `orders.write` scope |
| `fabrikam` | active | `orders` | Cell B | Used to show that `reporting` and `field-service` tools are blocked |
| `northwind` | active | `orders`, `reporting` | Cell A | Has a deliberately tiny rate-limit bucket so the self-test can trigger `429` quickly |
| `tailspin` | suspended | `orders` | Cell B | Used to show that suspended tenants fail closed |

| Tool | Product | Extra scope |
|---|---|---|
| `orders_get_order` | `orders` | none |
| `orders_search_orders` | `orders` | none |
| `orders_create_order` | `orders` | `orders.write` |
| `field_service_list_work_orders` | `field-service` | none |
| `reporting_run_report` | `reporting` | none |

The rate-limit values in the fixtures are small test values chosen so the scenarios run instantly. They are not sizing guidance.

## What the self-test proves

| Check | Expected | What it proves |
|---|---|---|
| `entitled-tools-call` | `200` with a result; one backend call to Cell A with Contoso's credential reference; audit `allowed` | The happy path routes by the tenant record and selects the tenant's credential |
| `tools-list-filtered-by-entitlement` | Fabrikam sees only `orders` read tools; Contoso with `orders.write` sees all | `tools/list` reflects products and scopes |
| `non-entitled-tool-blocked-on-tools-call-without-list` | Fabrikam calling `reporting_run_report` directly gets `-32602`, identical to an unknown tool; no backend call; audit `denied_tool` | Entitlement is re-checked on `tools/call`, and denials do not reveal the catalog ([ADR 0002](../docs/decisions/0002-recheck-entitlement-on-tools-call.md)) |
| `write-tool-requires-user-scope` | `orders_create_order` fails without `orders.write` and succeeds with it | Per-tool scopes are enforced on top of product entitlement |
| `rate-limit-exceeded-returns-429` | Northwind is throttled with `429` and `Retry-After`; Contoso is unaffected; Northwind recovers after waiting | Limits are keyed per tenant, so one tenant cannot starve another |
| `unknown-tenant-returns-403` | `403` with reason `unknown_tenant`; no backend call | Unknown tenants fail closed |
| `suspended-tenant-returns-403` | `403` with reason `tenant_not_active` | Suspension takes effect at the gateway |
| `spoofed-tenant-header-ignored` | Fabrikam sending `X-Tenant-Id: contoso` still sees Fabrikam's tools, routes to Cell B, and the backend receives the gateway's tenant header with `fabrikam`; the spoof is flagged in audit | The tenant comes from the token, never from a header ([ADR 0001](../docs/decisions/0001-token-derived-tenant-identity.md)) |
| `client-token-not-forwarded` | The backend call carries no `Authorization` header from the client | The client token is never passed through |
| `missing-token-returns-401-with-resource-metadata` | `401` with `WWW-Authenticate` pointing to the protected resource metadata | Clients can discover the authorization server ([MCP OAuth Front Door](../../mcp-oauth-front-door/README.md)) |
| `wrong-audience-returns-401` | `401` with `error="invalid_token"` | Tokens minted for other APIs are rejected |
| `initialize-handshake` | `200` with an `Mcp-Session-Id` header and `tools.listChanged` advertised | The MCP handshake works through the pipeline |
| `production-adapters-are-gated` | Both production stubs raise `NotImplementedError` | Stubs cannot be used by accident |

## How the pipeline maps to API Management

| Simulator stage (`Gateway.handle`) | API Management equivalent ([policy files](../infra/policies/mcp-tenant-pipeline.xml)) |
|---|---|
| `_authenticate` | `validate-jwt` with issuer, audience, required claims, and a `401` that carries `resource_metadata` |
| Tenant from `tenant_id` claim; `X-Tenant-Id` only compared and flagged | Global policy deletes tenant hint headers; pipeline reads the claim from the validated token |
| `TenantDirectory.get` | `tenant-directory` fragment: `cache-lookup-value`, then `send-request` with managed identity, then `cache-store-value`; fail closed |
| Product entitlement and suspended-tenant checks | `choose` on the tenant record, returning `403` with a JSON-RPC error |
| `TokenBucket` per tenant | `rate-limit-by-key` and `quota-by-key` keyed on the tenant key |
| `tools/call` re-check | `choose` on `params.name` in the request body against the tenant's tool list |
| `tools/list` filtering | The MCP adapter, using the gateway's trusted tenant header |
| `BackendCall` with credential reference, cell, and trusted tenant header | `authentication-managed-identity` (or a Key Vault named value), `set-header`, and `set-backend-service` to the tenant's cell backend |
| `AuditSink.emit` | `mcp-audit` fragment: `emit-metric` with tenant dimensions and `log-to-eventhub` partitioned by tenant |

## Adapter seams

| Seam | In-memory fake used by tests | Gated production stub | What a real implementation does |
|---|---|---|---|
| `TenantDirectory` (`Protocol`, `get(tenant_key)`) | `InMemoryTenantDirectory` | `ProductionTenantDirectory` | Calls your entitlement API or configuration store with a managed identity, caches by tenant key with a bounded lifetime, and fails closed. Never trusts a client-supplied tenant value. |
| `AuditSink` (`Protocol`, `emit(record)`) | `InMemoryAuditSink` | `EventHubAuditSink` | Sends one record per request to Event Hubs, partitioned by tenant. In API Management this is the `log-to-eventhub` policy rather than application code. Never includes bodies or tokens. |
| Backend | `FakeBackend` records what the gateway forwarded | (none) | Your MCP adapter or cell backend, which must also enforce tenant scope on data |
| Clock | `FakeClock` | (none) | Wall-clock time; injected so rate-limit tests are deterministic |

Both production stubs raise `NotImplementedError`, and the `production-adapters-are-gated` scenario checks that they do.

## Extend it

- **Add a tenant or product.** Edit `default_tenants()` and `DEFAULT_CATALOG`, then add a scenario that proves the new boundary.
- **Map Entra `tid` instead of a custom claim.** Change `TENANT_CLAIM` and add a mapping step in front of `TenantDirectory.get`. Add a scenario for one Entra tenant that owns more than one account.
- **Model quotas.** Add a second counter per tenant with a longer window next to the token bucket.
- **Model cache staleness.** Wrap `InMemoryTenantDirectory` with a cache and a clock, suspend a tenant, and assert when calls start failing.
- **Wire a real directory.** Implement `TenantDirectory` against your entitlement API in your own code. Keep the in-memory fake for the self-test.

## Limitations

- Unsigned test tokens; no signature, key rotation, or issuer discovery.
- A single process: no distributed counters, cache races, multi-region behavior, or streaming.
- `tools/list` filtering is shown in the simulator; in API Management it usually lives in the MCP adapter, because gateway policies should not rewrite streaming responses.
- Tool argument schemas are placeholders.

## Related

- [Asset README](../README.md)
- [Architecture: per-call policy pipeline](../docs/architecture.md#6-per-call-policy-pipeline)
- [Evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md)
- [Infrastructure guide](../infra/README.md)
