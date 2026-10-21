# Evaluation, release gates and troubleshooting

This document covers how to show that the gateway does what it claims (each team has its own budget, keys stay with their team, nobody bypasses the gateway, usage is attributed to a team), what to check before each release, and how to find the layer at fault when a call fails.

There are two kinds of evidence:

- **Offline:** the stdlib [simulator](../samples/README.md) checks the rules on every pipeline run with no Azure access.
- **Live:** the [client tools](../src/README.md) and the post-deploy checks run against a deployed environment.

Neither produces performance figures. Budgets in the examples are placeholders that you set per environment.

## 1. Offline self-test

```powershell
python samples/team_quota_gateway.py --self-test   # PASS <scenario> lines, a summary, exit 0
python samples/team_quota_gateway.py --demo        # narrated walk-through of the same rules
```

The self-test models each APIM product as a token bucket keyed by product name. It covers:

- prompt token estimation and post-response settlement
- 429 with `retry-after`
- the `x-remaining-tokens` and `x-tokens-consumed` headers
- removal of inbound `api-key`, `Authorization` and `x-team`
- the managed-identity call to the model
- the product check and the deployment allow-list
- connection-name uniqueness across projects
- project-scoped key reads
- the chargeback ledger
- classification of a gateway 429 versus a model 429

Any failure prints `FAIL <scenario>` and exits non-zero. The pipeline runs it in stage 0, so a policy or naming change that breaks a rule fails the build before anything is deployed. When you change a rule in the policy XML, change the simulator in the same pull request.

## 2. Live checks

### Single call per team

```powershell
cd src
python run_team.py --team alpha
python run_team.py --team alpha --list-connections
```

| Exit code | Meaning |
|---|---|
| 0 | Call succeeded; budget headers printed |
| 1 | Gateway or connection problem (see section 5) |
| 2 | Missing settings in `.env` |
| 3 | No access to the team's project or connection |
| 4 | Team is over its budget; `retry-after` printed |

### Isolation burst test

```powershell
python tpm_burst_test.py --burst-team alpha --control-team beta
```

Run this as an operator who belongs to both teams, in a sandbox where team alpha has a small `tokensPerMinute`. The test sends requests as alpha until the gateway answers 429. It checks that the 429 is the gateway's (top-level `statusCode` and `retry-after`) and not the model's. It then sends one request as beta.

| Exit code | Verdict | What it means |
|---|---|---|
| 0 | PASS | Alpha was throttled on its own budget; beta was served |
| 1 | INCONCLUSIVE | Alpha was never throttled, or the 429 came from the model deployment |
| 2 | FAIL | Beta was also refused; the budgets are not independent |
| 3 | Setup problem | Settings, sign-in or project access |

A result of 1 is common on a first run. Lower alpha's budget in the sandbox, or raise `--max-requests`. If the 429 came from the model, the shared deployment ran out of capacity before alpha's budget did. That is also worth knowing (see section 4).

### Two-principal protocol

The access checks need two test principals, each in one team only. They are listed in [authentication-and-governance.md](authentication-and-governance.md#5-two-principal-isolation-protocol).

## 3. Release gates

| Gate | Stage | Check | Pass condition |
|---|---|---|---|
| G0 | Lint | `az bicep lint` and `az bicep build` on every template | No errors |
| G1 | Lint | Policy XML is well formed and placeholders are intact | Pipeline step passes |
| G2 | Lint | No output exposes `listSecrets`, keys or credentials; no `isSharedToAll: true` | No matches |
| G3 | Lint | Simulator `--self-test` | Every scenario passes, exit 0 |
| G4 | Lint | `py_compile` on the client source | No errors |
| G5 | What-if | `az deployment group what-if` reviewed by a person | No unexpected deletes; role assignments only on team projects and the gateway identity |
| G6 | Verify | Every team connection exists, is `ApiManagement`, and is not shared to all | Pipeline step passes |
| G7 | Verify | A call with no key | 401 from the gateway |
| G8 | Verify | Two-principal protocol | All steps match the expected result |
| G9 | Verify | `tpm_burst_test.py` in a sandbox | Exit 0 |
| G10 | Verify | Chargeback query (section 6) after G9 | Rows for both teams, with alpha larger |

G0 to G7 are automated in [the reference pipeline](../infra/pipelines/multi-team-gateway-deploy.yml). G8 to G10 need test principals and a sandbox budget. Run them at least when a policy, role or naming change ships, and record the results.

## 4. Triage a 429

Two different things return 429, and they need different fixes.

| Signal | Team budget (gateway) | Shared capacity (model) |
|---|---|---|
| Body | Top-level `statusCode: 429`, "Token limit is exceeded" | `{"error": {...}}` from the model |
| `retry-after` | Set by the token limit policy | May be set by the model |
| `x-remaining-tokens` | Present, at or near zero | Absent or not near zero |
| Gateway log | `ResponseCode` 429, no backend response | `BackendResponseCode` 429 |
| Teams affected | One team | Possibly several teams at once |
| Fix | Wait, or raise that team's `tokensPerMinute` | Raise deployment capacity, add a backend pool, or lower the sum of team budgets |

The same rule is in `classify_429` in both [tpm_burst_test.py](../src/tpm_burst_test.py) and the simulator.

If the sum of all team budgets is higher than the deployment's capacity, a busy period can produce model 429s for teams that are within budget. Keep the sum at or below capacity, or accept and alert on that case.

## 5. Symptom, cause and fix

| Symptom | Likely cause | Fix |
|---|---|---|
| `run_team.py` exit 3, "no access to the team project" | Caller is not Foundry User on that project, or the assignment has not propagated | Add the caller to the team's group; wait and retry |
| Exit 3, "connection was not found" | Wrong `TEAM_<X>_CONNECTION` or project endpoint | Copy both from the `teams` deployment output |
| Exit 1, "returned no API key" | Connection is not `ApiKey`, or the caller cannot read credentials | Check the connection auth type; see the verify item in the authentication document |
| Gateway 401 `{"statusCode": 401}` | Key regenerated, subscription suspended, or a wrong key | The client re-reads once; if it persists, redeploy to refresh the connection and check the subscription state |
| Gateway 403 `team_product_required` | All-access or API-scoped key used | Use the team connection; disable the all-access subscription |
| Gateway 404 `deployment_not_allowed` | Deployment not in `allowedModelDeployments` | Use an allowed deployment, or update the parameter and redeploy |
| Gateway 404 `{"statusCode": 404}` | Wrong path or operation; only chat completions and embeddings are defined | Use `<gatewayUrl>/aigw` as `azure_endpoint`; add operations if needed |
| Model 404 `DeploymentNotFound` | The deployment is allowed in the gateway but does not exist on the account | Create the deployment or fix the name |
| Model 401 or 403 through the gateway | Gateway identity has no model role, or a wrong role ID was passed | Check `apimModelInferenceRoleDefinitionId` and the role assignment on the account |
| 429 for one team | Team budget used | Section 4 |
| 429 for several teams | Shared deployment capacity | Section 4 |
| No `x-remaining-tokens` header | Request did not go through a team product, or an error was returned before the limit policy ran | Check the key and the product policy |
| No `Team` metric | Observability disabled, custom metrics not enabled on the API diagnostic, or metrics not yet ingested | Check `enableObservability`, the API diagnostic `metrics` setting and the logger |
| Deployment: connection already exists | Connection names must be unique across the account, not only within a project | Keep the `ai-gateway-team-<name>` pattern |
| Deployment: role assignment `PrincipalNotFound` | New principal not yet replicated | Re-run the deployment, or set `memberPrincipalType` |
| Deployment: model SKU or version not available | Region or quota | Pick another region, SKU or version |

## 6. Example queries

These queries assume workspace-based Application Insights and resource-specific APIM tables (`logAnalyticsDestinationType: Dedicated`). Column and metric names vary by release. **Verify in your tenant** before you build reports or alerts on them.

Token usage per team per day, from the token metric (Application Insights):

```kusto
customMetrics
| where timestamp > ago(30d)
| where name == "Total Tokens"
| extend team = tostring(customDimensions["Team"]),
         deployment = tostring(customDimensions["Deployment"])
| summarize tokens = sum(valueSum) by team, deployment, day = bin(timestamp, 1d)
| order by day asc, team asc
```

Gateway throttles versus model throttles, per team product (Log Analytics):

```kusto
ApiManagementGatewayLogs
| where TimeGenerated > ago(1d)
| where ApiId == "foundry-openai"
| summarize requests = count(),
            gatewayThrottled = countif(ResponseCode == 429 and BackendResponseCode != 429),
            modelThrottled = countif(BackendResponseCode == 429),
            unauthorized = countif(ResponseCode == 401),
            forbidden = countif(ResponseCode == 403)
  by ProductId, bin(TimeGenerated, 1h)
| order by TimeGenerated asc
```

Calls that were refused because they did not use a team product:

```kusto
ApiManagementGatewayLogs
| where TimeGenerated > ago(7d)
| where ApiId == "foundry-openai" and ResponseCode == 403 and isempty(ProductId)
| summarize calls = count() by ApimSubscriptionId, bin(TimeGenerated, 1h)
```

## 7. Alerts

| Alert | Signal | Why |
|---|---|---|
| Team throttled repeatedly | Gateway 429s for one product above your threshold | The team needs a bigger budget or is misbehaving |
| Shared capacity exhausted | Any backend 429 | The sum of budgets is above capacity, or capacity dropped |
| Unauthorized spike | 401s for one subscription | Possible leaked or stale key; rotate |
| Non-team key in use | Any 403 `team_product_required` | Someone is using an all-access or API-scoped key |
| Model auth failure | Backend 401 or 403 | The gateway identity lost its role |
| Missing telemetry | No `Total Tokens` for a team that made successful calls | Chargeback gap |

Set thresholds from your own baseline. This asset does not recommend values.

## 8. Verify in your tenant

- The policy names (`azure-openai-*` or the newer `llm-*` names) and their attributes in your APIM release.
- Token counts and `x-tokens-consumed` behaviour for streaming responses, and how much a budget can overrun under concurrent requests.
- Token counters are kept per gateway region; a multi-region APIM has separate budgets per region.
- The custom metric and table names used in section 6.
- That BasicV2 meets your network needs; use StandardV2 when the model account must be private.

## Related

- [Architecture](architecture.md)
- [Authentication and governance](authentication-and-governance.md)
- [ADR 0001: APIM product per team as the quota counter key](decisions/0001-apim-product-per-team-as-quota-counter-key.md)
- [Infrastructure guide](../infra/README.md)
- [Agent Production Readiness Guide](../../../agentops-and-readiness/agent-production-readiness/README.md)
