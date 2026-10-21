# ADR 0001: Loading is not authorization

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** Every agent harness that discloses tools or skills to a model in stages, and every tool server those tools run on
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md) · [ADR 0002](0002-phase-driven-loading-with-meta-tool-fallback.md)

## Context

Progressive loading hides most tools from the model most of the time. That makes it tempting to treat
"not loaded" as "not allowed": if the model never sees the refund-exception tool, surely it cannot
call it. That reasoning fails in several predictable ways:

- **Models emit names they were not shown.** A model can produce a tool name it remembers from
  training, from earlier in the transcript, from a skill body or reference, or from text that an
  attacker placed in retrieved content. Function-calling APIs return whatever name the model wrote.
- **MCP does not require listing before calling.** Any client, including a buggy or compromised
  harness, can send `tools/call` for a name it never received from `tools/list`.
- **The harness is not the boundary.** The loader runs in the agent process. Its state (the loaded
  set, the phase, the budget) is request context from the tool server's point of view.
  [Request Context Is Not Authorization](../../../../agent-runtime/request-context-is-not-authorization/README.md)
  explains why such state cannot carry a caller's rights.
- **Different "no" answers leak the catalog.** If a withheld skill returns "not permitted" and a
  made-up skill returns "not found", a model or an attacker can enumerate what exists by probing.
- **Skills can over-reach.** A skill's tool list is authored content. If loading a skill added its
  tools to the view without checking eligibility, publishing a skill would grant tools.

Three different questions are being answered, and each needs its own owner:

1. **Which** tools may this caller ever use? Entitlement, decided by the tool server.
2. **Which** of those may this run use? The autonomy profile, decided by the bounded-autonomy layer.
3. **When** is an eligible tool shown to the model? Progressive loading, decided by the harness.

## Decision

1. **The loader only narrows.** The loader computes `eligible = entitled ∩ profile` from the catalog
   source's answer for the verified caller and the run's autonomy profile. Every trigger (always-on,
   phase, meta-tool, router) selects from `eligible`. Nothing a model writes, and nothing a skill's
   metadata says, can add a tool to `eligible`. A skill is eligible when it brings no tools or at
   least one eligible tool, and loading it discloses only the intersection of its tool list with
   `eligible` (sample: `_skill_eligible` and `_visible`). A skill whose tools are all withheld is
   absent from the index.
   Entitlement filtering itself belongs to the
   [Entitlement-Aware Tool Catalog](../../../../ai-gateway/entitlement-aware-tool-catalog/README.md)
   and is not reimplemented here.
2. **The harness gate rejects anything not loaded.** Before forwarding a call, the harness checks
   the name against the disclosed set (sample: `ProgressiveLoader.call_tool`). An eligible tool whose
   skill is not loaded returns `-32602` `Tool not loaded.` with a `load_skill` hint naming the
   skill. A name that is not eligible, does not exist or is malformed returns the uniform `-32602`
   `Unknown tool.` response. The tool server is not called in either case.
3. **The tool server re-checks every call.** The server evaluates entitlement for every `tools/call`
   from the verified token, independent of what the harness disclosed. A call the harness allowed
   but the server refuses is recorded as `tool_call_denied_by_server`, the loader refreshes the
   catalog, the revoked tool leaves the view, and any skill left with no eligible tool is unloaded
   with trigger `catalog_changed`. See
   [Multi-Tenant MCP Gateway ADR 0002](../../../../ai-gateway/multi-tenant-mcp-gateway/docs/decisions/0002-recheck-entitlement-on-tools-call.md).
4. **Uniform answers outside, real reasons inside.** `load_skill` returns the same `UNKNOWN_SKILL`
   response for a withheld skill, a skill outside the profile, a non-existent skill and a non-string
   argument. `search_tools` post-filters every result to the eligible set, so a search index that
   contains withheld skills cannot leak them. The ledger records the real reason (`not_eligible`,
   `unknown`, `tool_not_loaded`) and records malformed names as `invalid:<hash>`.
5. **The `tool_not_loaded` hint is allowed because it reveals nothing new.** It names only skills
   that are already in the model's tier 0 index. Hints are never produced for ineligible tools.

## Consequences

- **Positive:** Prompt injection, hallucinated names and harness bugs cannot reach a tool the caller
  is not entitled to; the server refuses. The `entitlement-filtered-never-appears` scenario calls the
  catalog directly, bypassing the loader, and the backend still does not run.
- **Positive:** The harness gate catches hallucinated and premature calls cheaply, before any network
  call, and turns them into a useful hint or a uniform refusal.
- **Positive:** Misses (`tool_not_loaded`) and unknown calls become distinct, measurable signals for
  catalog tuning and for injection detection.
- **Positive:** Skill authors cannot grant tools. If a skill's tool list names a withheld tool, that
  tool is simply not disclosed, and a skill with no other eligible tool is absent from the index.
- **Negative:** Two checks run on every call: the harness gate and the server re-check. The harness
  gate is an in-memory lookup.
- **Negative:** Uniform responses make "why can't the agent use this tool?" harder to answer from the
  transcript. Support works from the ledger, which keeps the real reason.
- **Negative:** A skill whose tool list is only partly eligible loads with a subset of its tools,
  while its instructions may still describe the missing ones. Authors should split skills along
  entitlement and effect-class lines, and the registry review should flag skills that mix them.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **Hide tools in the prompt only** ("use only these tools") | A prompt is a request, not a control. The model can still emit any name, and nothing downstream refuses it. |
| **Use the loader as the authorization gate** | The loader runs in the agent process and its state is request context. A bypass, a bug or a second client reaches the server with no check. |
| **Load everything the caller is entitled to** | Safe if the server enforces, but loses the focus, cost and misuse-surface benefits that motivate the pattern. Kept as the baseline in the decision matrix. |
| **Let skills declare the tools they grant** | Turns skill publishing into an access-control change outside the entitlement system. |
| **Explain withheld tools to the model** ("you are not entitled to X") | Leaks the catalog and invites the model to argue or retry. Uniform unknown is safer; the ledger keeps the reason. |
| **Silently drop calls to not-loaded tools** | The model retries or hallucinates success. An explicit `tool_not_loaded` error with a hint lets it recover. |

## Follow-ups

- Alert on any `tool_call_denied_by_server` event. In a healthy system the harness should rarely
  allow a call the server then refuses; each one is revocation lag or a catalog mismatch.
- Add the server-direct bypass test (call the catalog without the loader) to every adoption's CI.
- Revisit the error codes if the MCP specification defines dedicated authorization or
  tool-availability errors.
- Align with the harness-level decision in
  [Governed Context-Aware Agent Harness ADR 0001](../../../../orchestration-and-workflows/governed-context-aware-agent-harness/docs/decisions/0001-harness-enforces-boundaries-not-transcripts.md).
