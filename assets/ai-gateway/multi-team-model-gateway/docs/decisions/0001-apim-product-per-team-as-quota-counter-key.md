# ADR 0001: Use one APIM product per team as the token quota counter key

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** The token limit policy, the team products and subscriptions created by [team.bicep](../../infra/modules/team.bicep), the chargeback metric dimension, and every client that calls the gateway
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md)

## Context

Several teams share one model deployment through one API Management API. The gateway must give each team its own token budget, so that one busy team cannot use up capacity the others depend on. The same identifier must also attribute usage to a team for chargeback.

The `azure-openai-token-limit` policy (`llm-token-limit` in newer documentation) keeps one counter per value of its `counter-key` expression. Whatever that expression returns *is* the team as far as the budget is concerned. The candidates are:

- **A request header such as `x-team`.** The client sets it, so any team can claim to be another and spend that team's budget.
- **The source IP address.** Teams share build agents, egress addresses and hosted runtimes. An address identifies a network hop, not a team.
- **The subscription ID (`context.Subscription.Id`).** Bound to the key, so it cannot be forged. However, it changes when a subscription is recreated, and a team with more than one subscription would get more than one budget.
- **An Entra token claim (`appid`, `azp` or a group).** Bound to an authenticated identity. It needs `validate-jwt` in front of the limit, an app registration or app role per team, and a Foundry connection auth type other than `ApiKey`.
- **The product name (`context.Product.Name`).** Resolved by the gateway from the subscription key. The client cannot choose it, and it is fixed at deployment time.

Teams also need different budgets. A single API-scope policy with one `tokens-per-minute` value would give every team the same number.

## Decision

1. Each team gets exactly one APIM product, `team-<name>`, with `subscriptionRequired: true`, `approvalRequired: true` and `subscriptionsLimit: 1`. The product's display name equals its resource name, because `context.Product.Name` returns the display name.
2. Each team product carries its own policy with `azure-openai-token-limit`, `counter-key="@(context.Product.Name)"` and that team's `tokens-per-minute`, `estimate-prompt-tokens="true"`, and the `retry-after`, `x-remaining-tokens` and `x-tokens-consumed` headers.
3. The API policy refuses any call that did not arrive through a product (`context.Product == null`, which covers the built-in all-access subscription and API-scoped subscriptions) with 403 `team_product_required`. This closes the paths that would otherwise have no team budget. Only team products are linked to the gateway API, so every product that reaches the API carries a team token limit.
4. The token metric uses the same expression for its `Team` dimension, so the budget and the chargeback figure always name the same team.
5. The gateway ignores and removes the `x-team` header. Clients may send it for their own diagnostics; it has no effect on budget or attribution.

## Consequences

### Positive

- The team identity used for the budget comes from the key, which the gateway checks. It cannot be set by the caller.
- Budgets differ per team without extra policy logic: each product policy holds one number.
- Budget, chargeback and access are all tied to one resource per team, so onboarding and retiring a team is one entry in the `teams` parameter.
- The offline simulator can model the rule exactly, because the counter key is a plain string.

### Trade-offs and operational obligations

- The budget is tied to a shared secret. A leaked key spends that team's budget until it is rotated (see ADR 0002 for how keys reach teams).
- Product names are part of the contract. Renaming a product resets its counter and splits its chargeback history.
- Counters are per gateway region; a multi-region APIM gives each region its own budget per team.
- Several calls can pass the estimate at the same time and briefly overrun a budget. The team is then throttled until the budget refills.
- `subscriptionsLimit: 1` means a team cannot hold two independent keys. Zero-downtime rotation uses the subscription's primary and secondary keys instead.

## Alternatives considered

| Alternative | Why it was rejected | When to reconsider |
|---|---|---|
| **Header (`x-team`) as counter key** | Caller-controlled; any team can spend another team's budget | Never as the only key |
| **Subscription ID as counter key** | Changes on recreation; several subscriptions would mean several budgets | If teams need several keys with separate budgets on purpose |
| **Entra claim as counter key** | Needs `validate-jwt`, app registrations per team and Entra-based connections | When you move to Entra-only gateway auth; see [authentication-and-governance.md](../authentication-and-governance.md#7-entra-only-alternative) |
| **One API per team** | Duplicates the API, its operations and its policy for every team | If teams need different operations or backends |
| **API-scope limit with one value** | Every team would get the same budget | If all teams really do get the same budget |

## Follow-ups

- Keep the 403 `team_product_required` check and the simulator scenario that tests it in every release.
- Alert on any 403 `team_product_required`; it means someone has a non-team key.
- Disable the built-in all-access subscription in each APIM instance.
