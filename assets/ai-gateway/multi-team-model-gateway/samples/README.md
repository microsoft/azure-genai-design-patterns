# Samples: Team Quota Gateway Simulator

`team_quota_gateway.py` is a single-file, standard-library-only simulation of the
gateway described in [architecture.md](../docs/architecture.md). It models several
teams sharing one model deployment behind Azure API Management, with one product,
one subscription, one Foundry project and one project connection per team, exactly
as [infra/](../infra/README.md) deploys them.

Use it to read the logic, to walk your platform and security teams through the edge
cases, and as an executable specification for the tests you run against your own
gateway. Each scenario is a short function that asserts one rule from the docs or an
ADR.

> **Test only.** Time is a logical clock that the scenarios advance by hand. Budgets
> and token counts are illustrative configuration: they say nothing about real
> capacity, latency, throughput or cost. There is no network call anywhere in the
> file, and every key is a deterministic fake generated in memory. The token bucket
> follows the documented behaviour of the `azure-openai-token-limit` policy on the v2
> tiers; the exact refill and estimation rules inside API Management are not
> published at this level of detail, so verify behaviour in your tenant with
> [src/tpm_burst_test.py](../src/tpm_burst_test.py).

## Prerequisites

- Python 3.10 or later. No packages to install.

## Run it

From the repository root:

```powershell
python assets/ai-gateway/multi-team-model-gateway/samples/team_quota_gateway.py --self-test
python assets/ai-gateway/multi-team-model-gateway/samples/team_quota_gateway.py --demo
```

- `--self-test` runs every scenario. It prints `PASS <scenario>` or
  `FAIL <scenario>: <reason>` for each one, then `N/35 scenarios passed`. It exits 0
  when all pass and 1 otherwise. The deployment pipeline in
  [infra/pipelines](../infra/pipelines/multi-team-gateway-deploy.yml) runs it as a gate.
- `--demo` prints a short narrated walkthrough:
  1. team alpha calls through its project connection and gets the budget headers
  2. team alpha bursts until the gateway answers 429 with `retry-after`
  3. team beta calls while alpha is throttled and is served from its own budget
  4. team alpha waits for `retry-after` and is served again
  5. team beta's key is rotated, the next deployment refreshes the connection, and
     the client re-reads it after a 401
  6. a chargeback split from the per-team token metric, as shares only

The demo never prints a key or prompt text.

## What the scenarios cover

| Area | Scenarios |
| --- | --- |
| Request path | `happy_path_team_call_with_budget_headers`, `backend_gets_managed_identity_token_only`, `caller_bearer_token_and_query_key_are_stripped` |
| Gateway authentication | `missing_key_rejected_401`, `unknown_key_rejected_401`, `suspended_subscription_rejected`, `non_product_subscriptions_blocked_403` |
| Deployment allow-list | `disallowed_deployment_404_spends_no_budget` |
| Per-team token limit | `oversized_prompt_blocked_before_backend`, `burst_team_gets_429_with_retry_after`, `gateway_throttle_never_reaches_backend`, `control_team_unaffected_by_burst`, `retry_after_is_honoured`, `budget_refills_gradually_not_all_at_once`, `actual_usage_reconciled_after_response`, `underestimate_overdraws_then_blocks`, `token_quota_returns_403_when_period_used`, `model_capacity_429_is_distinguishable` |
| Counter key | `spoofed_x_team_header_does_not_move_usage`, `anti_pattern_header_counter_key_is_spoofable`, `renaming_product_starts_a_fresh_counter` |
| Foundry projects and connections | `connection_names_unique_across_projects`, `connection_is_scoped_to_its_project`, `member_cannot_read_other_team_connection`, `listing_connections_never_returns_keys`, `connection_carries_gateway_not_model_endpoint` |
| Key handling | `model_account_rejects_key_auth`, `leaked_key_is_billed_to_its_owner_team`, `key_rotation_revokes_old_key_and_client_recovers`, `responses_and_logs_never_contain_keys_or_prompts` |
| Chargeback and telemetry | `chargeback_ledger_matches_consumed_headers`, `metric_dimensions_low_cardinality` |
| Lifecycle | `onboarding_a_team_leaves_existing_budgets_alone` |
| Safety of the code itself | `config_fails_closed`, `production_stubs_raise` |

Two scenarios are deliberate anti-patterns. `anti_pattern_header_counter_key_is_spoofable`
keys the budget on the caller-supplied `x-team` header and shows that every new header
value gets a fresh budget. `renaming_product_starts_a_fresh_counter` shows why the
product name must be treated as stable once traffic flows (see
[ADR 0001](../docs/decisions/0001-apim-product-per-team-as-quota-counter-key.md)).

## How the pieces map to the real deployment

| Simulation | Real component |
| --- | --- |
| `ModelGateway.handle` | The API-scope policy in [api-gateway-policy.xml](../infra/policies/api-gateway-policy.xml): product check, deployment allow-list, `<base />`, header stripping, managed identity, backend routing |
| `TokenBucket`, `product_name_counter_key` | `azure-openai-token-limit` with `counter-key="@(context.Product.Name)"` in [team-token-limit.xml](../infra/policies/team-token-limit.xml) (`ApimTokenLimitPolicy` stub) |
| `CharCountEstimator` | `estimate-prompt-tokens="true"`: the gateway counts prompt tokens before the call |
| `Product`, `Subscription`, `deploy_team` | The product, product-API link, subscription and project resources in [team.bicep](../infra/modules/team.bicep) |
| `FoundryAccount`, `Connection` | Foundry projects and `ApiManagement` project connections (`FoundryProjectConnections` stub) |
| `TeamClient` | `TeamGatewayClient` in [src/team_client.py](../src/team_client.py) |
| `FakeManagedIdentity` | The gateway's system-assigned identity with Cognitive Services User on the account (`GatewayManagedIdentity` stub) |
| `FakeModelBackend` | The shared model deployment with local (key) authentication disabled (`FoundryModelBackend` stub) |
| `MemoryMetricSink`, `chargeback` | `azure-openai-emit-token-metric` with a `Team` dimension in [token-metric-fragment.xml](../infra/policies/token-metric-fragment.xml), queried in Application Insights (`AppInsightsTokenMetricSink` stub) |
| `GatewayLogEntry` | API Management gateway logs in Log Analytics; a gateway-side 429 has no backend call |
| `classify_429` | The same rule in [src/tpm_burst_test.py](../src/tpm_burst_test.py): a top-level `statusCode` is the gateway's throttle, an `error` envelope came from the model deployment |

## The fictional world

All names are placeholders. `apim-contoso-aigw.example.com` stands in for the
gateway URL that the Bicep reads from `properties.gatewayUrl`, and
`aif-contoso.example.com` stands in for the model account endpoint, which teams never
see. The principals `principal-alice` (team alpha), `principal-bob` (team beta) and
`principal-platform-ops` (both teams, used for the isolation test) stand in for Entra
object IDs.

## Production stubs

The classes at the bottom of the file (`ApimTokenLimitPolicy`,
`FoundryProjectConnections`, `GatewayManagedIdentity`, `FoundryModelBackend`,
`AppInsightsTokenMetricSink`) raise `NotImplementedError`. They mark where the
simulation stops and the deployable pieces in [infra/](../infra/README.md) and
[src/](../src/README.md) begin. Do not ship the simulation.
