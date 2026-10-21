# ADR 0001: Deploy and invoke from inside the VNet

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md#8-networking-on-top-of-the-landing-zone), [Asset README](../../README.md), [Landing zone deployment and CI/CD](../../../../landing-zones-and-infra/secured-foundry-landing-zone/docs/deployment-and-cicd.md)

## Context

The Foundry account and project run with public network access disabled, as the [Secured Foundry Landing Zone](../../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md) requires. Hosted agent management (create and inspect versions, switch the active version, list and end sessions, stream session logs) and invocation are data-plane operations. Azure Resource Manager does not expose hosted agent versions as resources **(observed in preview; verify against current docs)**, so there is no control-plane path that avoids the private endpoint.

In testing, every data-plane call from outside the private network, including from public hosted CI runners and developer laptops, returned 403 with a "public access is disabled" style message. Teams under release pressure then tend to enable public access "temporarily", or to move release steps into a public pipeline with an exception.

## Decision

1. Every data-plane step runs inside the network: image push, version creation, smoke turns, evaluation gates, promotion, rollback, session diagnostics and the orphan sweeper.
2. The runner is either the landing zone's self-hosted runner in the operator VNet or a Container Apps job in a delegated subnet with private DNS resolution. Both use managed identity or workload identity federation; neither stores secrets.
3. Public hosted runners may run steps that do not touch the data plane (lint, unit tests, static analysis), and hand off to the in-network runner by artifact.
4. A negative test that invokes the agent from outside the network and expects 403 runs in every environment.
5. Enabling public network access to unblock a deployment is not an approved procedure. Policy denies it, and drift raises an alert.

## Consequences

### Positive

- The private posture holds in every environment, including development.
- Smoke tests prove the real network path, DNS and identity, not a shortcut.
- A 403 from the pipeline has one meaning per message, which makes failures fast to classify.

### Trade-offs

- The team operates and patches runner infrastructure.
- Runner capacity is a release bottleneck; plan for concurrent pipelines.
- Developers cannot invoke private agents from laptops without a VPN or jump host into the network; development projects that mirror production inherit this.
- Job-based runners need care: environment variables were observed to persist between executions, so every execution passes its full environment.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Temporarily enable public network access during deploys | Opens the data plane; easy to leave on; defeats the landing zone |
| Public hosted runner with an IP allowlist | The private project does not accept public traffic regardless of source IP; allowlists drift |
| Deploy through Azure Resource Manager only | Hosted agent versions are not exposed as ARM resources in preview |
| Registry-side build and a platform-side deploy trigger | Registry build tasks could not reach the private registry in testing; still needs an in-network smoke turn |
| Skip smoke tests in private environments | A version can be accepted and still fail at runtime; only a real turn proves it |
