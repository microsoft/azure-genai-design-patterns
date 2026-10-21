# ADR 0003: Share one model deployment and enforce budgets at the gateway, instead of a deployment per team

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** The model deployment created by [foundry-account.bicep](../../infra/modules/foundry-account.bicep), the gateway backend, and capacity planning for all teams
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md)

## Context

Every team needs a model, and capacity is limited by quota per subscription, region and model. There are two ways to give each team a predictable share:

- **A deployment per team.** Each team gets its own deployment with its own capacity, either through its own key or through the gateway with routing by team.
- **A shared deployment with gateway budgets.** All teams use one deployment, and the gateway limits how many tokens per minute each team may use ([ADR 0001](0001-apim-product-per-team-as-quota-counter-key.md)).

The forces are:

- Quota for a model in a region is finite. Splitting it into many small deployments leaves capacity unused when a team is idle.
- Each extra deployment is another object to upgrade when the model version changes, and another place for content filter and responsible AI settings to drift.
- Some teams need a guarantee that another team cannot affect them, for example production workloads with contractual commitments.
- Chargeback needs per-team usage either way.

## Decision

1. Teams share one model deployment (default name `chat`) behind one gateway backend.
2. Each team's budget is a gateway limit, not a slice of deployment capacity.
3. The platform team keeps the sum of team budgets at or below the deployment's capacity, or accepts that model-side 429s can occur at busy times and alerts on them.
4. A team that needs reserved capacity gets its own deployment, added to `allowedModelDeployments` and kept out of the shared budget pool. This is an exception, recorded with a reason.

## Consequences

### Positive

- Capacity not used by one team is available to the others, up to their budgets.
- One deployment to upgrade, configure and review.
- Budgets change by redeploying a parameter, without moving capacity between deployments.
- Usage per team comes from the gateway metric, regardless of how capacity is arranged.

### Trade-offs and operational obligations

- Budgets cap use; they do not reserve capacity. If the sum of budgets exceeds capacity, a team within budget can still receive a model-side 429.
- The platform team must watch model-side 429s (see [evaluation-and-troubleshooting.md](../evaluation-and-troubleshooting.md#4-triage-a-429)) and add capacity or lower budgets when they appear.
- All teams share the same model version and content filter. A team that needs a different configuration needs its own deployment.
- The gateway is on the path for every call. Its availability and region placement matter to every team.

## Alternatives considered

| Alternative | Why it was rejected | When to reconsider |
|---|---|---|
| **Deployment per team, team holds a key** | Teams would hold model keys; no central budget or chargeback; key auth must stay enabled | Never in this design |
| **Deployment per team behind the gateway** | Fragments quota and multiplies upgrades; still needs gateway routing and keys | For teams that need reserved capacity or a different model configuration |
| **Provisioned throughput shared by all teams** | A purchasing choice, not an isolation mechanism; still needs per-team budgets on top | When the combined workload justifies it; budgets still apply |
| **Backend pool across regions** | Adds capacity and resilience, not per-team isolation | Add it as the next step when model-side 429s are frequent; budgets still apply |

## Follow-ups

- Review the sum of team budgets against deployment capacity whenever a team is onboarded or a budget changes.
- Record each dedicated-deployment exception with its owner and review date.
- See [Hosted Agent Streaming Model Gateway](../../../../agent-runtime/hosted-agent-streaming-model-gateway/README.md) for an agent runtime that calls models through a gateway like this one.
