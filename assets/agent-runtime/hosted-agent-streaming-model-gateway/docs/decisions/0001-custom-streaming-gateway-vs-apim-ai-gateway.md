# ADR 0001: Custom streaming gateway versus APIM AI gateway

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture](../architecture.md#components-and-responsibilities), [Asset README](../../README.md#decision-matrix), [Deployment and operations](../deployment-and-operations.md), [Bring your own model with an AI gateway in Foundry Agent Service](https://learn.microsoft.com/azure/foundry/agents/how-to/ai-gateway), [AI gateway capabilities in Azure API Management](https://learn.microsoft.com/azure/api-management/genai-gateway-capabilities)

## Context

Hosted agents in Foundry Agent Service run your own container, so the agent's code decides which model endpoint it calls. By default that is a Foundry model deployment, called with the agent's identity. Several needs push teams to put something in between:

- **Bring your own model.** A model outside the Foundry catalog, or one hosted by another provider, behind the same client code.
- **Central policy.** Which agent may use which model, token budgets per agent, and input checks applied in one place instead of in every agent.
- **Model abstraction.** Agents ask for an alias; the platform team changes the deployment or provider behind it without an agent release.
- **Failover across backends.** A second deployment or region when the first fails before answering.
- **Cost attribution.** Token usage per agent, per model, per backend.

There are three ways to meet these needs without writing a gateway, and they cover most teams:

1. **Call Foundry deployments directly** and accept per-agent configuration, with Foundry's own content filters and metrics.
2. **Azure API Management as an AI gateway**, with token limits, token metrics, load balancing across backends, content safety and Entra token validation as policies, in a managed service.
3. **A Foundry model gateway connection** (APIM or another OpenAI-compatible gateway) that prompt agents and hosted agents can use. Observed in preview; verify against current docs.

A custom gateway is only justified by needs those options do not meet. In practice, the cases that came up were streaming behaviour the team must control end to end (hold-headers versus early-commit, keepalive comments matched to their ingress timeouts, a strict "no retry after the first byte" rule), per-agent policy expressed in code next to the agent platform, and backends or protocols that need custom adaptation.

## Decision

1. **Default to the managed options.** Start from the [decision matrix](../../README.md#decision-matrix). Use a direct Foundry deployment when one agent uses one model. Use APIM as an AI gateway when you need central policy and its policies cover your rules. Use a Foundry connection for prompt agents.
2. **Build a custom streaming gateway only when a written requirement cannot be met** by APIM policies or a Foundry connection, for example a streaming behaviour, a policy rule or a backend adaptation. Record that requirement in your own ADR.
3. **Keep the custom gateway narrow.** It does authentication, policy, routing, relay and telemetry, and nothing else: no prompt rewriting, no caching of answers, no conversation state.
4. **Keep it OpenAI-compatible.** Agents use a standard client pointed at a different base URL, so moving to APIM or back to direct calls later is a configuration change in the agent, not a rewrite.
5. **Treat it as a production service.** It is in the hot path of every model call, so it gets an owner, an on-call rotation, release gates, a rollback plan and alerts before any agent depends on it.

## Consequences

### Positive

- Full control over streaming behaviour, failover rules and policy evaluation order, tested by the offline simulator.
- Bring-your-own backends are adapted in one place; agents see one contract.
- Per-agent usage events with exactly the fields your cost and audit reporting needs.
- Agents stay portable because the contract is the OpenAI format, not the gateway.

### Trade-offs

- You build, secure, patch, scale and operate a service that every agent depends on. An outage stops every agent behind it.
- An extra network hop on every model call. Measure it in your environment.
- Features APIM provides as configuration (semantic caching, built-in token metrics, a developer portal, policy fragments) must be built or done without.
- Budgets and idempotency need a shared store before you scale out, which is more infrastructure to run.
- The gateway identity becomes a high-value principal: it can call every model behind it.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Direct calls from each agent to Foundry | No central policy, no bring-your-own model behind the same contract, and cost attribution depends on each agent's own logging. The right answer when those needs are absent |
| APIM as an AI gateway | The preferred choice whenever its policies meet the requirements. Not chosen here only for the requirements it could not meet in this scenario; re-evaluate as APIM capabilities change |
| Foundry model gateway connection only | Covers prompt agents and model abstraction. Observed in preview; verify against current docs. It still needs a gateway behind it, and the connection does not by itself add per-agent policy |
| Policy and routing inside each agent's code | Every agent re-implements the rules, and a bug in one agent bypasses them. No single place to rotate a backend key |
| A general-purpose reverse proxy with no model awareness | Cannot enforce model allowlists or token budgets, cannot account usage, and often buffers server-sent events by default |
