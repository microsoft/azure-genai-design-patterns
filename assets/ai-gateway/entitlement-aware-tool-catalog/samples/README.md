# Entitlement-Aware Tool Catalog sample

[`entitled_tool_catalog.py`](entitled_tool_catalog.py) is a runnable, standard-library-only reference implementation of the pattern in [`../docs/architecture.md`](../docs/architecture.md). It models an MCP tool gateway that computes, per caller, the tools that caller is entitled to. It returns only that subset from `tools/list` and re-checks the same decision on every `tools/call`.

The sample is a teaching artifact. It runs offline, needs no credentials, and makes no network calls. The token validator, entitlement registry and tool backend are in-memory test doubles. The production seams raise `NotImplementedError`.

## Prerequisites

- Python 3.10 or later.
- No packages to install. The sample imports only the standard library.

## Run it

From the repository root:

```powershell
python assets\ai-gateway\entitlement-aware-tool-catalog\samples\entitled_tool_catalog.py --self-test
```

Or from this `samples` folder:

```powershell
python entitled_tool_catalog.py --self-test                               # all three Domain Packs (default)
python entitled_tool_catalog.py --self-test --domain fabrikam-health      # one Domain Pack
python entitled_tool_catalog.py --demo --domain northwind-logistics       # short walkthrough, no assertions
python entitled_tool_catalog.py --demo --self-test --domain all           # walkthrough, then the suite
```

| Flag | Values | Default | Effect |
|---|---|---|---|
| `--self-test` | flag | on when `--demo` is absent | Runs the 14 scenarios for each selected Domain Pack. |
| `--demo` | flag | off | Prints a walkthrough for each selected pack. Combine it with `--self-test` to run both. |
| `--domain` | `contoso-manufacturing`, `fabrikam-health`, `northwind-logistics`, `all` | `all` | Selects the Domain Pack. |

### Expected output

The self-test prints one line per scenario and pack, then a total. It exits `0` when every scenario passes and `1` when any scenario fails. A failure line names the pack, the scenario, the exception type and the message.

```text
PASS [contoso-manufacturing] list-filtered-per-tenant-and-role
PASS [contoso-manufacturing] hidden-tool-call-indistinguishable
...
PASS [northwind-logistics] audit-records-decisions-not-content
42/42 scenarios passed
```

The demo prints what each tenant sees, the uniform error for a hidden tool and for a nonexistent tool, the `notifications/tools/list_changed` message after an entitlement change, the registry wire shape and the last audit events:

```text
== contoso-manufacturing: Manufacturing, operator Contoso (multi-plant production software) ==
Adventure Works [t-adventure-works] sees: maintenance.schedule_inspection, production.get_work_order, production.update_work_order_status, quality.list_nonconformances
Tailspin Toys [t-tailspin-toys] sees: production.get_work_order, production.update_work_order_status
limited caller calls quality.list_nonconformances: HTTP 200 {"code": -32602, "message": "Unknown tool: quality.list_nonconformances"}
limited caller calls quality.nonexistent_tool: HTTP 200 {"code": -32602, "message": "Unknown tool: quality.nonexistent_tool"}
entitlement change to v2; session outbox: [{"jsonrpc": "2.0", "method": "notifications/tools/list_changed"}]
registry wire response: {"tenantId": "t-tailspin-toys", "entitlementVersion": 2, "modules": ["production", "quality"], "featureFlags": [], "descriptionVariants": {}}
last audit events:
  ...
```

## What the sample implements

| Component | Class or function | What it does |
|---|---|---|
| Verified identity | `VerifiedPrincipal`, `TokenValidator`, `FakeTokenValidator` | Only a validator can create a principal. The fake issues opaque `test-token.<random>` handles with an expiry. They are not JWTs. |
| Entitlement registry | `Entitlements`, `EntitlementRegistry`, `FakeEntitlementRegistry` | Maps a directory tenant ID to an internal tenant, entitlement version, modules, feature flags and description variants. It can simulate outages, lagging replicas and missed change events. |
| Catalog | `ToolDefinition`, `ToolCatalog` | Holds every tool with its module, any-of roles, optional feature flag, read-only flag and discoverability. Each publish increments the catalog version. Publishing runs a lint check. |
| Filtering | `principal_class`, `denial_reason`, `minimize_schema`, `build_view` | Normalizes roles to the roles that matter to the catalog, computes one allow/deny decision per tool, removes feature-gated schema properties and builds a versioned, digested view. |
| Entitlement cache | `CachePolicy`, `EntitlementCache` | Caches registry answers with a TTL, a negative cache for unknown tenants, a per-tenant version floor and a bounded stale window. |
| View cache | `ViewCache`, `secure_view_key` | Caches filtered views by `(tenant, principal class, entitlement version, catalog version)`. It never keys by user. |
| Cursors | `CursorCodec` | Issues opaque, HMAC-protected pagination cursors bound to the tenant, principal class and view digest. |
| Gateway | `EntitledToolGateway` | Handles `initialize`, `ping`, notifications, `tools/list` and `tools/call` over a JSON-RPC-over-HTTP shape, owns sessions and sends `notifications/tools/list_changed`. |
| Audit | `AuditLog` | Records decisions and context, never argument values, results or tokens. |
| Backend | `ToolBackend`, `FakeToolBackend` | Records which tool ran for which resolved tenant. |

## Scenarios

Each scenario runs once per Domain Pack, so the suite runs 42 checks. Every scenario builds a fresh gateway from the pack's seed data.

| # | Scenario | What it proves |
|---|---|---|
| 1 | `list-filtered-per-tenant-and-role` | The full and limited tenants, each with all roles and with no relevant role, see exactly the list computed by an independent oracle from the seed data. The pack's hidden tool appears only for the full tenant. The limited tenant's list is a strict subset. Each list writes a `list_filtered` audit event. |
| 2 | `hidden-tool-call-indistinguishable` | Calling a hidden tool, a tool that does not exist, and a hidden tool with junk arguments returns the same error shape with no `data`. The backend is never reached. The audit still records the true reasons: `not_entitled`, `unknown`, `not_entitled`. |
| 3 | `role-gated-call-enforced` | A caller without the required app role does not see the write tool, and a call by name returns `Unknown tool` with audit reason `role_required`. A caller with the role sees the tool and the call runs against the correct tenant. |
| 4 | `spoofed-context-ignored` | `X-Tenant-Id` and `X-Org-Id` headers do not change the list. The audit records the header names, not their values. A smuggled `tenant_id` argument is rejected as a schema mismatch before the backend. Reusing a session ID from another tenant or another user returns `404`, and the owner can still use it. |
| 5 | `cache-keyed-by-entitlement-set` | A second user with the same principal class (plus an irrelevant `Reader` role) gets a view cache hit. Cache keys contain no user IDs. A deliberately insecure key that omits the tenant leaks the full tenant's tool list to the limited tenant. The call is still denied because enforcement does not trust the view. The secure key does not leak. |
| 6 | `schema-minimized-per-entitlement` | A feature-gated input property is present for the entitled tenant and absent for the other. The internal flag annotation never reaches the client. The full tenant gets its description variant, the limited tenant gets the shared description. A limited caller who sends the hidden property gets a tool error and the backend is not called. |
| 7 | `cursor-bound-to-filtered-view` | With a page size of 2, pagination returns exactly the filtered view. Another tenant's cursor, a cursor with a tampered MAC and a malformed cursor are rejected with `-32602`. A valid cursor works. After an entitlement change, the old cursor is rejected because its view binding changed. |
| 8 | `change-notifies-only-affected-sessions` | An entitlement upgrade for the limited tenant notifies only that tenant's sessions that have listed tools. An idle session that never listed is not notified. Publishing a new tool notifies only sessions whose view changed. A session with a pending notification is not notified twice, and listing again clears the pending flag. |
| 9 | `revocation-bites-next-call` | With change events, a revocation denies the next call immediately and a restore re-enables it. With a missed event, exposure lasts only until the entitlement TTL ends. |
| 10 | `registry-outage-bounded-staleness` | A cold registry outage returns `503` with `-32003`. Where the pack allows staleness, lists are served from a stale snapshot and audited as `stale`. Read-only calls follow the pack's stale-call scope, and writes are refused. Past the window the gateway returns `503`. A lagging replica that returns an older entitlement version than the gateway has seen is refused until it catches up. |
| 11 | `identity-and-onboarding-fail-closed` | A missing, forged or expired token returns `401` with a `WWW-Authenticate` header that carries `resource_metadata`. A token from an unregistered tenant returns `403` with `-32001`, and the negative cache means a second attempt does not call the registry again. |
| 12 | `catalog-lint-blocks-tenant-data` | Publishing is rejected for a description that names a tenant, a schema `const` that holds a directory tenant ID, an invalid tool name, and a tool that moves to another module. The catalog version does not change and no session is notified. |
| 13 | `explicit-disclosure-is-opt-in` | The default is uniform `Unknown tool`. The health pack's policy rejects explicit disclosure at construction time. Elsewhere, explicit mode returns `-32001` with `{"reason": "not_entitled"}` only for a tool marked discoverable. Undiscoverable and nonexistent tools stay uniform. |
| 14 | `audit-records-decisions-not-content` | The audit contains no argument values, tokens or tenant-hint header values. An invalid tool name such as `../../admin tool` is stored as a hash. Every call decision records tenant, principal class, tool, entitlement version, catalog version and freshness. |

## Domain Packs

| Pack | Industry and operator | Full tenant | Limited tenant | Hidden from limited | Late-published tool | Policy differences |
|---|---|---|---|---|---|---|
| `contoso-manufacturing` | Manufacturing. Contoso runs multi-plant production software. | Adventure Works: production, quality and maintenance modules, `cost-analytics` and `predictive-maintenance` flags. | Tailspin Toys: production only. | `quality.list_nonconformances` (discoverable) | `maintenance.get_asset_health` | Bounded staleness, stale reads allowed, explicit disclosure allowed. |
| `fabrikam-health` | Healthcare. Fabrikam runs a clinic operations platform. | Lamna Healthcare: scheduling, clinical-summary and billing modules, `telehealth` flag. | Relecloud Clinics: scheduling only. | `clinical.get_visit_summary` | `billing.get_remittance_status` | No stale window, no stale calls, explicit disclosure forbidden. |
| `northwind-logistics` | Logistics. Northwind Traders runs a freight platform. | Wide World Importers: shipments, customs and fleet modules, `carbon-report` and `hazmat` flags. | Wingtip Toys: shipments only. | `customs.get_declaration_status` (discoverable) | `fleet.list_vehicles` | Longer stale window, stale reads allowed, explicit disclosure allowed. |

TTL and staleness values in the packs are abstract test ticks, not recommendations.

## Production seams

The sample marks where real services plug in. None of them is called by the self-test.

| Seam | Production responsibility |
|---|---|
| `EntraTokenValidator` | Validate the access token signature against the issuer's signing keys, the issuer, the audience, `nbf` and `exp`, and the allowed client application (`azp` or `appid`). Read `tid`, `oid`, `roles` and `scp` from the validated token only. |
| `HttpEntitlementRegistryClient` | Call the tenant registry or licensing service with a managed identity. Map `tid` to the internal tenant ID. Subscribe to entitlement-change events through a message broker or event-routing service. |
| `ForwardingToolBackend` | Call the domain API with a credential minted for that API (managed identity or on-behalf-of), pass the resolved tenant, and let the domain API do record-level authorization. Never forward the caller's MCP token. |
| `CursorCodec` key | The sample generates an ephemeral key per process. In production, use a shared key from a managed secret store and rotate it. |
| `_arguments_match` | The sample checks only top-level types, required fields and `additionalProperties: false`. Use a full JSON Schema validator in production. |

## Limitations

- JSON-RPC over HTTP is modeled as method calls on the gateway object. There is no HTTP server, streaming transport or SSE.
- The view cache is an in-process FIFO. It needs no TTL because its key already carries the entitlement and catalog versions. Each replica can keep its own cache, or share one with the same key.
- Error codes `-32001` and `-32003` are this design's implementation-defined codes inside the JSON-RPC server-error range. Document your own and keep them stable.
- The sample proves behavior, not performance. It makes no latency, throughput or capacity claims.

Delete any generated `__pycache__` directory before running `python scripts/assets.py validate`.
