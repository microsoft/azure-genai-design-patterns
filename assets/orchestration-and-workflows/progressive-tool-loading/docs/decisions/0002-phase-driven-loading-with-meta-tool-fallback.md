# ADR 0002: Phase-driven loading with a meta-tool fallback

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** The choice of loading triggers in any harness that applies progressive tool and skill loading, and the budget and ordering rules that go with them
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Decision matrix](../architecture.md#6-loading-triggers-and-decision-matrix) · [Context budgeting](../architecture.md#7-context-budgeting) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md) · [ADR 0001](0001-loading-is-not-authorization.md)

## Context

Once a harness stops loading everything up front, something has to decide what to show and when.
There are four common ways to decide, and each has a weakness when used alone:

- **Phase-driven.** The harness loads skills tagged for the current phase (plan, gather, act,
  verify). This is predictable, needs no extra model turns and is easy to audit. It misses needs the
  phase map did not anticipate.
- **Intent router.** A classifier picks skills from the request. It handles many distinct request
  types but a confident misroute narrows the view too far, and the router decision itself must be
  recorded to audit the run.
- **Meta-tool.** The model asks for a skill with `load_skill(name)`, after reading the tier 0 index
  or calling `search_tools(query)`. This handles the long tail but costs a turn per load, changes
  the prompt prefix often and can thrash under a tight budget.
- **Semantic search only.** The model searches a large index for every capability. It scales to
  large catalogs but returns distractors, and every action needs a search turn first.

The autonomy profile from [Bounded Autonomy](../../../../orchestration-and-workflows/bounded-autonomy/README.md)
is not one of these options. It decides eligibility before any trigger runs.

The [Governed Context-Aware Agent Harness](../../../../orchestration-and-workflows/governed-context-aware-agent-harness/docs/decisions/0002-progressive-skill-disclosure-over-all-loaded.md)
already decided that progressive disclosure beats loading everything. This ADR decides how the
disclosure is driven.

## Decision

1. **Phase is the primary trigger.** Each skill's metadata lists the phases it serves. Entering a
   phase unloads non-pinned skills not tagged for it and loads the eligible skills that are, in
   sorted order (sample: `enter_phase`). Each load and unload is recorded with trigger `phase` or
   `phase_end`.
2. **Policy skills are always on and pinned.** Skills marked `always_on` load at start, cannot be
   unloaded by the model (`pinned`) and are never evicted. If they do not fit the budget, the
   harness fails at start rather than running without them.
3. **`load_skill` is the fallback.** Every eligible skill appears in the tier 0 index with a
   one-line description, so the model can load what the phase map missed. A meta-tool load lasts
   until the phase ends or the model calls `unload_skill`. When the model calls an eligible tool
   whose skill is not loaded, the `tool_not_loaded` error names the skill to load.
4. **`search_tools` is for large catalogs.** It returns tier 0 entries, post-filtered to the eligible
   set, and never loads anything. Use it when the full index would take too much of the budget;
   otherwise the index alone is enough.
5. **The router is optional.** Add an intent router only where request types are clearly separable.
   When its confidence is low it widens (phase defaults, or leaves the choice to the model). It
   never narrows below the phase defaults.
6. **Budget with a declared overflow policy.** The view is measured against a budget. `evict`
   unloads the least recently used non-pinned skill; `refuse` returns `budget_exceeded` and changes
   nothing. Recency updates on load, on repeat load and on tool call. The policy is configuration,
   recorded per workload.
7. **Canonical view.** The view is rebuilt for every model call in a fixed order: meta-tools, sorted
   index, pinned skills, other loaded skills by name, tool definitions by id. Load order does not
   affect the view.
8. **Write skills follow the envelope.** The sample allows the model to load any eligible skill in
   any phase, because approval of write calls belongs to the bounded-autonomy layer. A stricter
   deployment may refuse meta-tool loads of skills with write tools until the plan is approved. In
   both cases the approval gate on the call stays.

## Consequences

- **Positive:** Most runs need no extra turns for loading. The phase map does the work and the
  ledger explains each load by phase.
- **Positive:** The unexpected is still reachable, by one explicit and recorded `load_skill` call.
- **Positive:** The view changes mostly at phase boundaries and is identical for the same loaded set,
  which keeps the prompt prefix stable for providers that reuse prefix work.
- **Positive:** Misses become a tuning signal. A skill that keeps being loaded by meta-tool in the
  same phase belongs in that phase's map.
- **Negative:** Someone has to maintain phase tags on every skill. The registry review checks them
  and the telemetry shows when they drift.
- **Negative:** Phases assume a workflow with recognizable stages. Open-ended conversational agents
  get less from phase loading and lean more on the index and the meta-tool.
- **Negative:** `evict` can remove a skill the model planned to use again. The `refuse` policy and
  per-phase load caps are the mitigations for runs where that matters.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **All loaded** | Simple and predictable, but brings distractors, cost and misuse surface back. Kept as the baseline in evaluation. |
| **Meta-tool only** | Every run spends turns discovering obvious skills, and the prefix changes on every load. |
| **Router only** | A misroute has no recovery path inside the run unless the model can still load skills itself. |
| **Semantic search only** | Every capability needs a search turn first, and distractors in search results become wrong-tool calls. |
| **Load by tool instead of by skill** | Tools arrive without the instructions that say when to use them, and instructions arrive without their tools. |
| **Append new tool definitions to the transcript** | The view grows without bound, ordering depends on history and unloading becomes impossible. |

## Follow-ups

- Track meta-tool loads per phase. Move skills that are repeatedly loaded by meta-tool into the
  phase where they are needed.
- Cap meta-tool loads per phase, and make the cap visible in configuration and in the ledger.
- Replace the character approximation with the tokenizer for the deployed `<model-deployment>` and
  set the budget from measured runs.
- Revisit the router guidance once there is labeled data on misroutes for the workload.
