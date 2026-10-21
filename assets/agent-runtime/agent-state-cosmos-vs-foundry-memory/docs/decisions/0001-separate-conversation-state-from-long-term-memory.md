# ADR 0001: Separate conversation state from long-term memory

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** Every agent that keeps conversation threads, run checkpoints or anything it "remembers" about a user across sessions, whatever the hosting model (prompt agent, hosted agent, container, function)
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md) · [ADR 0002](0002-scope-memory-to-verified-user-and-tenant.md)

## Context

Teams describe everything an agent keeps as "memory". In practice an agent holds three different kinds of state ([architecture section 1](../architecture.md#1-three-kinds-of-state)):

- **Thread state and run checkpoints.** The messages of one conversation, the current workflow step, pending approvals and the tool-call ledger. It must be read exactly, resumed after a replica restart, written once per turn and kept inside the network and data boundary the workload promised.
- **Long-term user memory.** Preferences, profile facts and summaries the user stated or agreed to, extracted across sessions and retrieved by relevance. It is consent-bound, may be outdated and must be erasable on request.
- **System-of-record data.** Balances, orders, prescriptions and case decisions. It is owned by a business system with its own access controls and records policy, and a stale copy produces a wrong business outcome.

When these share one store, one key or one prompt-assembly path, the failures in [architecture section 2](../architecture.md#2-why-conflating-them-fails) follow: prompts grow without limit, a summary quietly becomes a second ledger, a thread delete leaves extracted facts behind, and a managed memory service ends up holding conversation text that was meant to stay on a private network. Managed memory and thread storage also have different maturity and networking properties: Foundry Memory does not offer virtual network integration for memory stores (observed in preview; verify against current docs), while Cosmos DB supports private endpoints and can be used as bring-your-own thread storage for a Foundry project.

The choice of backend for each kind of state is also likely to change over the life of a workload: a prototype starts on SQLite, production thread state moves to private Cosmos DB, and long-term memory may move between Foundry Memory and a self-hosted vector store as networking requirements and preview status change.

## Decision

1. **Classify every value before storing it.** Apply the classification test in [architecture section 1](../architecture.md#1-three-kinds-of-state). Thread state goes to a `StateStore`, long-term memory to a `MemoryStore`, and system-of-record data stays in its own system, read through an authorized tool on every turn.
2. **Two narrow interfaces, one hook.** The agent never calls a store directly. It calls a `ContextProvider` whose `before_turn` loads the thread, the latest checkpoint, the last N messages and the top-k memories, and whose `after_turn` saves the checkpoint and queues memory candidates ([architecture section 4](../architecture.md#4-the-pluggable-interface)).
3. **Choose a backend per kind of state.** Use the [decision matrix](../architecture.md#5-decision-matrix). The default for regulated or private-network workloads is private Cosmos DB for thread state and either a self-hosted memory service with a private endpoint, Foundry Memory where its networking model is acceptable, or no long-term memory at all.
4. **Thread state is authoritative for the conversation; memory never is.** Checkpoints are written with an ETag and an idempotency key. Memory is rendered in a labelled block as user-stated, possibly outdated and never authoritative.
5. **Memory writes are asynchronous and gated.** Candidates are extracted after the checkpoint, pass consent and the write policy, and are written by a background writer. System-of-record and sensitive-category content is refused with a reason code.
6. **Memory is optional context.** A memory outage marks the turn degraded and never fails the turn or blocks the checkpoint (invariant I9).
7. **One erasure path covers every store.** An `ErasureCoordinator` drops queued candidates, erases memory, erases thread state, revokes consent and verifies a zero count ([architecture section 11](../architecture.md#11-erasure-across-both-stores)).

## Consequences

- **Positive:** Each kind of state gets the retention, networking, consistency and residency decision it needs, instead of the strictest or loosest one applying to everything.
- **Positive:** Backends are swappable. Scenario `12 backend_swap_sqlite` runs the same provider, write policy and erasure coordinator over a different `StateStore`, and the production stubs show where Cosmos DB and Foundry Memory calls go.
- **Positive:** Prompts are bounded by construction: last N messages plus top-k memories within a character budget.
- **Positive:** Answers about money, care or entitlement come from the system of record, so a stale memory cannot produce a wrong business outcome.
- **Negative:** Two stores to operate, secure, monitor and erase, plus a background writer and its queue.
- **Negative:** A fact the user mentions in a thread is not remembered until the asynchronous writer has run, and is never remembered if the write policy refuses it. Users need to be told what the agent remembers.
- **Negative:** Every backend adapter must map its native errors and item shapes to the contract, and must pass the same contract tests.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **One store for everything (threads, checkpoints and memory in one container or index)** | Mixes exact-read, ETag-guarded state with relevance-ranked, consent-bound memory. Retention, erasure and access rules collide, and one key design cannot serve both read patterns. |
| **Foundry Memory for transcripts and checkpoints** | Memory is extracted and consolidated asynchronously by the service, so it cannot be read back exactly or guarded with an ETag, and memory stores lack virtual network integration (observed in preview; verify against current docs). |
| **Keep memory in the container's RAM or sandbox filesystem** | A hosted-agent memory setting sizes the container, and a sandbox filesystem is scoped to a session. Both are lost on restart or expiry and neither is scoped per user ([hosted agents section 6.6](../../../production-hosted-agents-on-foundry/docs/architecture.md#66-container-restart-and-session-expiry)). |
| **Treat the full transcript as memory and replay it every turn** | Unbounded prompt growth, old instructions resurfacing, no relevance ranking, and sensitive data the user typed once is replayed forever. |
| **Cache system-of-record values in memory to save tool calls** | The copy goes stale and escapes the system's access controls. A wrong balance or prescription is worse than an extra tool call. |

## Follow-ups

- Inventory every value the agent currently persists and classify it with the section 1 test before choosing backends.
- Decide per workload whether long-term memory is needed at all. If not, do not implement `MemoryStore`.
- Write contract tests for every backend adapter by porting the reference scenarios ([evaluation section 8](../evaluation-and-troubleshooting.md#8-running-and-extending-the-reference-suite)).
- Re-check Foundry Memory networking, API names and retrieval behavior at each release while it is in preview ([architecture section 17](../architecture.md#17-preview-caveats)).
- Align thread storage with the private Cosmos DB and private endpoint design in [Secured Foundry Landing Zone](../../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md), and episode-level memory hygiene with [Governed Context-Aware Agent Harness](../../../../orchestration-and-workflows/governed-context-aware-agent-harness/docs/architecture.md#93-scoped-memory-across-episodes).
