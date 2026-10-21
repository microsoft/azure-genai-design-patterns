# ADR 0001: Dual export; never replace the platform's tracing

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Asset README: Wiring a hosted agent](../../README.md#wiring-a-hosted-agent), [Architecture: Correlation](../architecture.md#correlation), [Evaluation and troubleshooting: Prove dual export](../evaluation-and-troubleshooting.md#prove-dual-export), [Production Hosted Agents on Foundry: observability](../../../production-hosted-agents-on-foundry/docs/architecture.md#11-observability)

## Context

A Foundry hosted agent sends its traces to the Application Insights resource connected to its Foundry project. Foundry's own tracing views, the agent monitoring experience and platform support all read from that path. The runtime reserves the Application Insights connection setting, so the agent's configuration cannot repoint it (observed in preview; verify against current docs).

Teams still ask for a second copy of the same spans:

- one tenant's traces must stay in a particular region;
- a retention period differs from the workspace default;
- an evaluation pipeline needs spans in its own store;
- finance wants token usage attributed per agent identity;
- an engineer needs to see exactly what an agent exports.

The runtime supports this. An OTLP endpoint set on the agent is exported to **in addition to** Application Insights (observed in preview; verify against current docs). Agent code can also add a second span processor with its own exporter.

Some properties of the second path shape the decision:

- **It can fail without anyone seeing it.** An OTLP exporter that times out or gets a non-retryable status logs a warning in the agent and drops the batch. If the second copy were the only copy, those failures would leave no trace anywhere.
- **It is redacted and bounded on purpose.** The receiver drops content, unknown attributes and over-limit spans ([ADR 0002](0002-redact-at-receiver-allowlist-attributes.md)). It is a deliberately incomplete copy.
- **It sits outside the platform's support boundary.** A support case or an incident review starts from the built-in traces, not from a team-owned store.

## Decision

1. **The built-in export stays on and unchanged.** Nobody removes, overrides or repoints the Application Insights connection, and nobody turns off Foundry tracing to avoid storing spans twice.
2. **The second copy is additive.** It is added with the runtime's OTLP environment variables or with a second `BatchSpanProcessor` in the agent's code. In both cases it has its own exporter and its own queue, so a slow or failing receiver cannot block the platform export.
3. **Both copies carry the same trace and span ids.** The receiver validates and normalizes ids but never replaces them, starts a new trace or re-parents spans. The trace id is the join key between the two copies and with the [streaming model gateway](../../../hosted-agent-streaming-model-gateway/docs/architecture.md#trace-propagation).
4. **The built-in copy is the record for operations.** Platform support, incident triage and the Foundry views use it. Nothing that needs every span relies on the second copy alone.
5. **The second copy is governed on its own terms.** Each route has its own retention, region and readers, set by its data owner and reviewed in the [governance checklist](../authentication-and-governance.md#review-checklist).
6. **Dual export is proved, not assumed.** Each agent release that changes telemetry wiring runs the [dual-export check](../evaluation-and-troubleshooting.md#prove-dual-export), and the scheduled gap query alerts when traces reach Application Insights but not the second copy.

## Consequences

### Positive

- Foundry's tracing views, agent monitoring and platform support keep working whatever happens to the receiver.
- A receiver outage, a wrong endpoint or a bad token loses only the second copy, and the gap query shows which traces are missing.
- Teams can add, change or remove the second copy without touching platform configuration.
- The same trace id works in Application Insights, the gateway's logs and the team's destination.

### Trade-offs

- Two copies of telemetry exist, and the second one needs its own classification, retention, residency and reader reviews.
- The agent does more export work and sends more egress traffic. Measure it for your workload; this asset contains no figures.
- With a second span processor, both exporters sit behind the same sampler. The second copy cannot hold spans that the agent did not sample; any further reduction happens at the Collector or receiver.
- If content capture is on in the agent, content reaches Application Insights even though the receiver drops it. Dual export does not make the built-in copy safer; agent-side settings do.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Replace the Application Insights export with OTLP to the team's store | Foundry's trace views go empty, the setting is reserved by the platform, and platform support loses its evidence |
| Send everything to a Collector and let it forward to Application Insights | The built-in path is not configurable that way (observed in preview), and it would make the team's Collector a dependency of the platform view |
| Copy data out of the Log Analytics workspace afterwards (data export rules or scheduled queries) | A good first choice when the need is only a longer archive in the same boundary. It cannot keep data out of the first store, cannot route by exporter identity before storage, and inherits the workspace's region. Verify which tables your workspace can export. |
| Send the second copy only from agents that "need" it, and switch built-in tracing off for them | Same harm as replacing the platform export, applied to the agents with the strictest requirements |
| No second copy; ask for platform features instead | The right answer when built-in tracing meets the need (see the asset README's *When not to use*). Not sufficient when residency, retention or per-identity routing rules differ from the platform's. |
