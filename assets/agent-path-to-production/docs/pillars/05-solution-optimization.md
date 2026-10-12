# Solution Optimization

Pillar 05 of the [pillar chapters](README.md) for [Agent Path to
Production](../../README.md). Use it when latency, cost, scaling, or retrieval
quality is the reason a working agent cannot ship or cannot stay in production.

## Pillar definition

This pillar covers improving a working agent's performance, cost, and answer
quality without weakening its safety or authorization controls. Optimization
starts from a measured baseline and changes one thing at a time; it is an
engineering loop, not a model swap.

Sub-topics:

- **Performance and latency.** End-to-end response time, time to first token,
  streaming, parallel tool calls, and the latency each model call, retrieval,
  and tool call contributes.
- **Cost management.** Consumption by model, tool, and retrieval; token budgets;
  provisioned versus pay-as-you-go capacity; and cost attribution to workloads.
- **Retrieval quality.** Chunking, indexing, hybrid and semantic ranking,
  filters, freshness, and evaluation of retrieval separately from generation.
- **Semantic layers and business tools.** Exposing governed business definitions
  and parameterized tools so the agent does not have to infer business logic
  from raw schemas.
- **Scaling.** Concurrency, quotas and rate limits, payload and pagination
  limits, cold starts, and state size and expiry under realistic load.
- **Model and tool selection.** Choosing the smallest model that meets the
  quality bar for each step, routing between models, and deciding when a
  deterministic tool is a better fit than a model call.

## Field signals

These signals are generalized from delivery reviews; they describe recurring
situations, not statistics.

- Teams commonly reach first for a larger or faster model when latency is too
  high, but the effective levers are usually elsewhere: pre-computing
  predictable results, caching, keeping capacity warm, paging large results, and
  right-sizing quotas.
- Teams commonly split workloads into a scheduled pre-computation path for
  predictable, expensive analysis and a live path for interactive questions, so
  users are not waiting on work that could have been done in advance.
- Teams commonly trade cold-start latency against idle cost in serverless or
  scale-to-zero hosting, and discover late that session state needs explicit
  size limits and expiry.
- Teams commonly find that gateway quotas, payload limits, and pagination behave
  differently under realistic load than in a demo. Measuring both warm and cold
  paths at expected concurrency exposes these limits before users do.
- Teams commonly improve accuracy on business questions by giving the agent
  governed business tools or a semantic layer instead of free-form generated
  queries against raw data, and then revisit model choice using evaluation
  results rather than intuition.

## Entry questions

1. Which metric is failing (latency, cost, throughput, or quality), and what is
   the agreed target?
2. Is there a baseline from the [Agent Ops](03-agent-ops.md) evaluation set,
   with component versions recorded?
3. Where does time or cost go in a typical request: model calls, retrieval, tool
   calls, orchestration overhead, or waiting on downstream systems?
4. Which results are predictable enough to pre-compute or cache, and how are
   those results scoped to the users allowed to see them?
5. What concurrency, payload sizes, and result volumes are expected at peak, and
   have quotas, rate limits, and pagination been tested at that load?
6. How does hosting behave on a cold start, and what is the cost of keeping
   capacity warm?
7. How is session or task state sized and expired?
8. Is retrieval evaluated separately from generation, so that retrieval and
   model problems can be told apart?
9. Does the agent generate free-form queries where a governed business tool or
   semantic layer would be more reliable?

## Approach and play

Optimization has no separate play in the
[runbook](../runbook.md#3-unblock-with-a-bounded-play). Run it as a bounded
experiment on top of the [Play
B](../runbook.md#play-b-make-quality-and-changes-observable) baseline, so every
change is measured against the same evaluation set and released through the same
gate.

1. **Confirm the baseline.** Use the Play B baseline and add the metric being
   optimized, measured the same way it will be judged in production.
2. **Profile the request path.** Use traces to attribute latency and cost to
   model calls, retrieval, tool calls, and orchestration. Separate warm from
   cold paths.
3. **Form one hypothesis.** Choose a single change, such as caching a result,
   pre-computing an analysis, adding a business tool, adjusting retrieval,
   enabling streaming, or routing a step to a smaller model, and predict its
   effect.
4. **Test under realistic conditions.** Run at expected concurrency with
   realistic payloads and result sizes. Include quota, rate-limit, pagination,
   and cold-start behavior.
5. **Compare like for like.** Rerun the full evaluation set. Accept the change
   only if the target metric improves and quality, safety, and authorization
   results do not regress.
6. **Release through the gate.** Ship the change through the [Agent
   Ops](03-agent-ops.md) pipeline, update the baseline, and record the
   experiment in the [readiness
   record](../readiness-record.md#evidence-and-acceptance).
7. **Monitor in production.** Track the optimized metric alongside quality and
   cost, and alert on regression.

## Readiness evidence and gate

| Evidence item | What good looks like | Gate |
|---------------|----------------------|------|
| Performance and cost baseline | Latency, cost, and quality are measured on the evaluation set, with component versions recorded. | Required (Quality and safety) |
| Experiment record | Each change has a hypothesis, test conditions, and before-and-after results on the same evaluation set. | Required (Quality and safety) |
| No quality or safety regression | Quality, safety, and authorization results after the change meet the thresholds agreed in the baseline. | Required (Quality and safety) |
| Load and limits test | Expected concurrency, payload sizes, quotas, pagination, and cold starts are tested, with results recorded. | Recommended (Operations) |
| Cost model | Expected cost per task and at peak volume, with attribution by model, retrieval, and tool. | Recommended (Operations) |
| Retrieval evaluation | Retrieval relevance is measured separately from answer quality. | Recommended (Quality and safety) |
| Cache and state policy | Caches and state are scoped to authorized users, sized, and given an expiry. | Recommended (Authorization and data) |

## Common pitfalls

- **Optimizing without a baseline.** Without a before measurement, an
  improvement is an impression.
- **Swapping the model first.** A model change alters behavior across the whole
  workload; retrieval, caching, tools, and pre-computation are often cheaper and
  safer levers.
- **Measuring only warm paths.** A demo on warm capacity hides the cold starts
  and quota limits users encounter.
- **Caching across users.** A cache keyed without user or permission scope leaks
  data between users.
- **Unbounded state.** Session or task state without size limits and expiry
  grows until it becomes a performance or cost problem.
- **Free-form queries for business metrics.** Letting the model infer business
  definitions from raw schemas produces plausible but inconsistent answers.
- **Silent quality loss.** A faster or cheaper configuration that was not rerun
  against the full evaluation set may fail on tasks that matter.

## Collaboration boundaries

- **[AI CoE & Governance](01-ai-coe-and-governance.md):** sets cost
  accountability and any limits on model choice, and approves trade-offs between
  cost and quality.
- **[AI Landing Zones](02-ai-landing-zones.md):** provides quota, regional
  capacity, and hosting options such as provisioned throughput or warm
  instances.
- **[Agent Ops](03-agent-ops.md):** owns the baseline, the evaluation set, and
  the release gate that every optimization ships through.
- **[AI Security & Observability](04-ai-security-and-observability.md):**
  reviews caches, pre-computed results, and model routing for authorization and
  data-leakage risk.
- **[Proven Patterns for Agent
  Architectures](06-proven-patterns-for-agent-architectures.md):** owns
  structural changes, such as splitting pre-computed and live paths or
  introducing business tools.
- **Roles:** application engineering, data or analytics engineering for semantic
  layers and retrieval, platform engineering for capacity, the cost owner, and
  the workload owner, who accepts trade-offs.

## References

- [Cost optimization](https://learn.microsoft.com/en-us/azure/well-architected/cost-optimization/)
- [Performance efficiency](https://learn.microsoft.com/en-us/azure/well-architected/performance-efficiency/)
- [Design and develop a RAG solution](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/rag/rag-solution-design-and-evaluation-guide)
- [Access Foundry Models and other language models through a gateway](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/azure-openai-gateway-guide)
- [Azure OpenAI in Microsoft Foundry Models performance and latency](https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/latency)
