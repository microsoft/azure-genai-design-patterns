# Evaluation, Release Gates, and Troubleshooting

This document covers how to prove that a multi-tenant MCP gateway keeps tenants apart: what to evaluate, which scenarios to run offline and against a deployed gateway, which gates to apply before release, and how to diagnose the problems teams hit most often. It complements the failure modes in [architecture.md section 15](architecture.md#15-failure-modes) and the negative tests in [authentication and governance](authentication-and-governance.md#13-negative-tests).

> MCP server support in Azure API Management and several diagnostics surfaces are new or in preview. Check current Microsoft Learn documentation for the log tables, tracing options, and metrics available in your region and tier.

## 1. What to evaluate

| Area | Question | Evidence |
|---|---|---|
| Tenant isolation | Can any request run as, or see data from, a tenant other than the one in its token? | Negative tests, backend tenant checks, audit records |
| Entitlement | Does each tenant see and call only the tools it is entitled to, even when it skips `tools/list`? | Tenant-by-method matrix in section 3 |
| Authentication | Are tokens for the wrong audience, issuer, or scope rejected, and can clients discover the authorization server? | `401` responses with `resource_metadata` |
| Fair use | Does one tenant's traffic throttle only that tenant? | Noisy-neighbor test in section 4 |
| Routing | Does each tenant reach its own cell with its own credential? | Backend logs, audit `cell` field |
| Protocol | Do `initialize`, `tools/list`, `tools/call`, and streamed responses work through every hop? | End-to-end tests from a real MCP client |
| Observability | Can you answer "who called which tool for which tenant, and what happened" for any request? | Metrics by tenant, audit events joined by request ID |

## 2. Evaluation scenarios

Run each scenario twice: offline with the [pipeline simulator](../samples/README.md), and against a deployed non-production gateway with test tenants and test client registrations.

| Scenario | Simulator check | Live gateway test | Pass criteria |
|---|---|---|---|
| Entitled call | `entitled-tools-call` | Call an entitled tool as each test tenant | `200`, result returned, audit `allowed`, correct cell |
| List filtering | `tools-list-filtered-by-entitlement` | Compare `tools/list` for tenants with different products | Each tenant sees only its tools |
| Call without list | `non-entitled-tool-blocked-on-tools-call-without-list` | Send `tools/call` for a non-entitled tool without calling `tools/list` | JSON-RPC `-32602`, same as an unknown tool, no backend call, audit `denied_tool_not_entitled` (`denied_tool` in the simulator) |
| Write scope | `write-tool-requires-user-scope` | Call a write tool with and without the write scope | Denied without the scope (gateway or backend), allowed with it |
| Per-tenant throttling | `rate-limit-exceeded-returns-429` | Drive one tenant past its limit while another tenant runs normally | `429` with `Retry-After` for the busy tenant only; recovery after the window |
| Unknown tenant | `unknown-tenant-returns-403` | Use a token whose tenant claim has no directory record | `403`, `-32001`, reason `unknown_tenant` |
| Suspended tenant | `suspended-tenant-returns-403` | Suspend a test tenant and evict its cache entry | `403`, reason `tenant_not_active` |
| Spoofed tenant header | `spoofed-tenant-header-ignored` | Send `X-Tenant-Id` naming another tenant | Request runs as the token's tenant; audit flags the header |
| No token passthrough | `client-token-not-forwarded` | Inspect backend request logs | No client `Authorization` header at the backend |
| Discovery | `missing-token-returns-401-with-resource-metadata` | Call without a token, then follow the metadata URL | `401` with `resource_metadata`; metadata document returns the authorization server |
| Wrong audience | `wrong-audience-returns-401` | Use a token minted for another API | `401` with `error="invalid_token"` |
| Handshake | `initialize-handshake` | Connect from each supported client (Copilot Studio, Foundry agent, an MCP inspector) | `initialize` succeeds and tools appear |
| Directory outage | Not simulated | Block the gateway's access to the tenant directory in a test environment | `503`, no backend call, audit `directory_unavailable` |
| Streaming | Not simulated | Call a tool that streams a long response | Events arrive progressively through every hop |

## 3. Tenant-by-method matrix

Build a matrix from your test tenants and run it on every release. The example uses the simulator fixtures.

| Tenant (state, products) | `initialize` | `tools/list` | `tools/call` entitled tool | `tools/call` non-entitled tool |
|---|---|---|---|---|
| Contoso (active, all) | `200` | All tools | `200` | Not applicable |
| Fabrikam (active, `orders`) | `200` | `orders` tools only | `200` | `-32602`, no backend call |
| Northwind (active, `orders` and `reporting`, small test limit) | `200` | `orders` and `reporting` tools | `200` until throttled, then `429` | `-32602` |
| Tailspin (suspended) | `403` | `403` | `403` | `403` |
| Unknown tenant | `403` | `403` | `403` | `403` |
| No token | `401` | `401` | `401` | `401` |

Add rows for each topology choice you run, such as a dedicated-cell tenant and a tenant in another region.

## 4. Load and noisy-neighbor checks

- Run a sustained load from one tenant while a second tenant sends a steady trickle. The second tenant's success rate and response time should not change in a way you would notice.
- Repeat with the busy tenant on a different MCP server to confirm whether counters are shared across servers, as you intended.
- Check that throttled requests do not reach the backend and do not consume backend credentials.
- Check that Event Hubs keeps up with audit volume during the test and that the audit count matches the gateway request count.
- Remember that distributed rate-limit counters are approximate. Treat small overshoots as expected and use quotas and backend metering for billing.

Agree on pass thresholds with the product owner for your own workload. This document does not give capacity or performance numbers.

## 5. Release gates

| Gate | Blocks release when |
|---|---|
| Simulator self-test | Any scenario fails |
| Bicep and policy lint | Bicep build or linter reports an error; policy XML is not well formed |
| Negative tests on non-production gateway | Any row in [authentication and governance section 13](authentication-and-governance.md#13-negative-tests) behaves differently |
| Tenant-by-method matrix | Any cell differs from the expected result |
| Client compatibility | A supported MCP client cannot complete `initialize` and one `tools/call` |
| Audit completeness | A test request has no matching audit event, or an audit event contains a body or token |
| Drift check | Deployed policy differs from source control |

### Illustrative CI smoke step

This step calls a non-production gateway as a test tenant and fails the build if a non-entitled tool is not blocked. Tokens come from your pipeline's secret store. The host name is a placeholder.

```bash
set -euo pipefail
GATEWAY="https://mcp.contoso.com/mcp"

body='{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"reporting_run_report","arguments":{}}}'

resp=$(curl -sS -X POST "$GATEWAY" \
  -H "Authorization: Bearer ${MCP_TEST_TOKEN_FABRIKAM}" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -H "MCP-Protocol-Version: 2025-06-18" \
  -H "X-Tenant-Id: contoso" \
  -d "$body")

echo "$resp" | grep -Eq '"code": ?-32602' || { echo "FAIL: non-entitled tool was not blocked"; exit 1; }
echo "PASS non-entitled tool blocked for fabrikam, spoofed header ignored"
```

Add similar steps for the `401`, `403`, and `429` paths. If your server requires a session, call `initialize` first and send the returned `Mcp-Session-Id` header.

## 6. Observability checks

Before go-live, confirm that you can answer these questions from telemetry alone:

- **Requests by tenant and outcome.** The `mcp-requests` custom metric in Application Insights, split by the `tenant` and `outcome` dimensions. Custom metric dimensions must be enabled on the Application Insights resource, or the dimensions are dropped.
- **One request end to end.** Join the Application Gateway access log (by its trace ID), the API Management gateway log (by request ID), the audit event in Event Hubs or Microsoft Sentinel, and backend logs (by `traceparent`).
- **Probing and spoofing.** Count `denied_tool_not_entitled` and spoofed tenant headers per tenant and client application.
- **Directory health.** Count `directory_unavailable` separately from `denied_unknown_tenant`.

An illustrative Application Insights query for outcomes by tenant:

```kusto
customMetrics
| where name == "mcp-requests"
| extend tenant = tostring(customDimensions.tenant), outcome = tostring(customDimensions.outcome)
| summarize requests = sum(valueCount) by tenant, outcome
| order by tenant asc, requests desc
```

Table and column names differ between classic and workspace-based Application Insights. Adjust the query to the schema you use.

## 7. Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| Client loops on sign-in or reports no authorization server | `401` lacks `WWW-Authenticate` with `resource_metadata`, or the metadata document requires a token | Return the challenge on every `401` path, including `on-error`; serve the metadata document anonymously. See [MCP OAuth Front Door](../../mcp-oauth-front-door/README.md). |
| `401 invalid_token` for valid users | Audience is not the MCP server's resource URI; issuer mismatch; required tenant claim missing; gateway cannot reach the signing keys | Decode a test token and compare `aud`, `iss`, `scp`, and the tenant claim with the policy; check outbound access to the OpenID metadata |
| `403` from Application Gateway, not API Management | WAF rule false positive on JSON-RPC bodies | Check WAF logs for the rule ID; add a narrow exclusion on the MCP route. See [architecture section 5.5](architecture.md#55-waf-and-mcp-streamable-http). |
| Streamed responses arrive all at once | Response buffering at a hop, or a policy that reads the response body | Turn off response buffering where needed, keep `buffer-response="false"` on `forward-request`, never read response bodies in MCP policies |
| Long tool calls cut off | Idle or request timeouts differ between hops | Align timeouts across the firewall, Application Gateway, API Management, and backend; prefer progress notifications for long work |
| One tenant throttles another | Counter key does not include the tenant, or includes something shared | Key `rate-limit-by-key` and `quota-by-key` on the normalized tenant claim |
| Tenant throttled on one server by traffic on another | Same counter key used on several MCP servers | Add the MCP server or product to the counter key if budgets should be separate |
| Tenant routed to the wrong cell | Stale cache or wrong directory record | Evict the cache entry, fix the record, and confirm the backend rejects tenant headers outside its partition |
| Tool appears in `tools/list` but `tools/call` is denied | Tool list and allowed tools come from different sources, or the client cached an old list | Derive both from the same directory record; send `list_changed` on entitlement changes |
| Non-entitled tool succeeds | `tools/call` re-check missing or bypassed by a different method name | Restore the re-check; reject unknown methods; add the case to release gates |
| Metrics have no tenant split | Custom metric dimensions disabled on Application Insights | Enable custom metrics with dimensions and redeploy the logger if needed |
| Denied requests have no audit event | A `return-response` path does not include the audit fragment | Include `mcp-audit` before every `return-response` and in `on-error` |
| Offboarded tenant still works | Cached tenant record has not expired | Evict with `cache-remove-value` from an operator-only operation; revoke at the authorization server |
| New tenant gets `unknown_tenant` | Claim value not normalized the same way as the directory key, or claim mapping wrong | Compare the normalized claim with the directory key; check the authorization server's claim mapping |
| JSON-RPC `-32700` parse errors | Client sent a non-JSON body, a batch, or the wrong content type | Check the request body and `Content-Type`; confirm the client speaks the protocol version you support |
| `503` for all tenants | Tenant directory unreachable or throttled | Check the directory's health and the gateway's private endpoint route; alert on `directory_unavailable` |

## 8. Where to look

| Source | What you find there |
|---|---|
| API Management request tracing | Step-by-step policy execution for a single test request. Enable it only in non-production or for a short time (check current docs for how tracing is authorized). |
| `X-Request-Id` response header | The gateway request ID, which matches the API Management gateway log and the audit event |
| Application Gateway access and firewall logs | Edge trace ID, WAF rule matches, backend status |
| Application Insights | `mcp-requests` metric by tenant, method, tool, and outcome; dependency calls to the directory and backends |
| Log Analytics | API Management gateway logs, Application Gateway logs, and diagnostic logs for Key Vault and Event Hubs |
| Event Hubs and Microsoft Sentinel | Audit events with outcome values such as `allowed`, `denied_unknown_tenant`, `denied_tenant_not_active`, `denied_not_entitled`, `denied_tool_not_entitled`, `rate_limited`, `quota_exceeded`, `invalid_token`, `unauthenticated`, `directory_unavailable`, `parse_error`, `backend_error`, and `gateway_error` |
| Backend logs | The gateway tenant header and `traceparent` for each call |

## 9. References

- [Architecture](architecture.md)
- [Authentication and governance](authentication-and-governance.md)
- [Pipeline simulator](../samples/README.md)
- [Infrastructure guide](../infra/README.md)
- [ADR 0001: Token-derived tenant identity](decisions/0001-token-derived-tenant-identity.md)
- [ADR 0002: Re-check entitlement on tools/call](decisions/0002-recheck-entitlement-on-tools-call.md)
- [Agent Production Readiness Guide](../../../agentops-and-readiness/agent-production-readiness/README.md)
- [Debug API Management policies](https://learn.microsoft.com/azure/api-management/api-management-howto-api-inspector)
- [Emit custom metrics from API Management](https://learn.microsoft.com/azure/api-management/emit-metric-policy)
- [Log events to Azure Event Hubs from API Management](https://learn.microsoft.com/azure/api-management/api-management-howto-log-event-hubs)
- [Application Gateway WAF exclusion lists](https://learn.microsoft.com/azure/web-application-firewall/ag/application-gateway-waf-configuration)
- [Model Context Protocol specification](https://modelcontextprotocol.io/specification)
