# Orchestration and Workflows (`orchestration-and-workflows`)

Design patterns, reference architectures, and samples focused on multi-agent coordination, planner-and-specialist routing, durable state and caching, context compaction, and deterministic vs. dynamic workflow execution.

## Assets in this category

<!-- category-catalog:start -->
_No standalone assets have been published in this category yet._
<!-- category-catalog:end -->

## Related implementations already available in sibling categories

While standalone orchestration patterns are added here, production-ready orchestration implementations are already available inside the end-to-end reference architectures:

- **Planner + Domain Specialist Router (`RoutingDecision`, `SpecialistCard`, `HandoffEnvelope`, loop/hop guardrails):** [`m365-and-copilot/m365-azure-custom-agent/samples/planner_specialist_router.py`](../m365-and-copilot/m365-azure-custom-agent/samples/planner_specialist_router.py) and [`docs/architecture.md §3`](../m365-and-copilot/m365-azure-custom-agent/docs/architecture.md#3-planner-and-specialist-orchestration)
- **Dual-Container Cosmos DB State, ETag Concurrency & Pre-Compute Cache:** [`m365-and-copilot/m365-azure-custom-agent/samples/cosmos_state_and_cache.py`](../m365-and-copilot/m365-azure-custom-agent/samples/cosmos_state_and_cache.py) and [`docs/state-caching-and-proactive-ops.md`](../m365-and-copilot/m365-azure-custom-agent/docs/state-caching-and-proactive-ops.md)
- **Declarative `worker_agents` and Progressive `agent_skills` Orchestration:** [`m365-and-copilot/m365-mcp-plugin-integration/docs/architecture.md`](../m365-and-copilot/m365-mcp-plugin-integration/docs/architecture.md)
