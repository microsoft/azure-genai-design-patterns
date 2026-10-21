# ADR 0001: Budgets are enforced by the harness, not by the prompt

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [ADR 0002](0002-fail-closed-on-policy-unavailable.md), [ADR 0003](0003-profiles-selected-by-server-policy.md)

## Context

An agent that plans, calls tools, checks results and retries can loop. It retries a failing tool, re-plans after every verifier finding, reads ever more context, or keeps "improving" an answer that was already acceptable. Each iteration spends tokens, tool calls, wall-clock time and sometimes money, and each tool call may have side effects.

The common first response is to put limits in the system prompt: "Do not call more than ten tools", "Stop after three attempts", "Be careful with cost". This fails in predictable ways:

- **The model cannot count reliably.** It does not see a running meter. It estimates from its own transcript, which may be truncated, summarized or reordered by the orchestrator.
- **Instructions are negotiable.** A persuasive tool result, a retrieved document or a user message ("this is urgent, keep trying") can outweigh a soft instruction. Limits that the model may reinterpret are not limits.
- **Limits are not observable.** When the model decides to stop, nothing records which limit was reached, how much was left, or whether the stop was correct. Operators cannot alert on, tune or audit a sentence in a prompt.
- **Limits are not consistent.** The same prompt produces different stopping points on different runs, so evaluation results for "how many retries" do not transfer to production.

Budgets are a resource-control problem, and resource control belongs in the component that actually spends the resource.

## Decision

1. **Budgets are policy parameters, not prompt text.** The policy document defines, per autonomy profile, an attempt budget (plan submissions), a tool-call budget, a token budget, an active wall-clock budget and an optional cost ceiling. Values in the sample are illustrative only.
2. **The harness meters every resource it spends.** The harness, not the model, counts tool calls when it authorizes them, charges tokens and cost from model-call usage reported by the platform, and measures active wall-clock time. Time spent waiting for a human (escalated) or for a dependency to recover (suspended) does not count against the run.
3. **Every action passes the governor's gate.** Before executing a step, the harness calls `authorize`. The gate checks budgets after the plan and envelope checks and before allowing the action. When any budget is exhausted, the outcome is `escalate` with `BUDGET_EXHAUSTED`, and the step does not run.
4. **Attempts are charged before evaluation.** A plan submission consumes an attempt whether it is accepted, denied or escalated, so a model that keeps proposing rejected plans exhausts its budget instead of looping.
5. **Budgets are pinned per run.** The run copies its budgets from the policy version in force when it started. A policy change applies to new runs only, so a run cannot gain or lose budget mid-flight.
6. **More budget means a new, linked run.** An approver can choose `continue-in-new-run`. The governor starts a child run with fresh budgets from the current policy, a checkpoint of completed and remaining steps, and a continuation index. A per-profile `max_continuations` bounds the chain.
7. **The prompt may describe the limits, but never enforces them.** Telling the model its remaining budget can improve planning. The gate does not depend on the model having read or obeyed that text.
8. **A gateway token limit is a backstop.** An Azure API Management token-limit policy can cap model traffic per caller or per subscription. It protects the platform from a defective harness. It is not the primary control, because the gateway does not see tool calls, attempts, plans or run boundaries.

## Consequences

### Positive

- Stops are deterministic and testable. The self-test proves each budget type escalates at the same point on every run.
- Every stop has a reason code and a `budget_remaining` snapshot in the decision record, so operators can alert on, tune and audit budget behavior.
- Prompt injection cannot extend a budget. The model has no write access to the meter.
- Evaluation results transfer to production, because the same governor and policy run in both.

### Trade-offs

- **Budgets are checked at gates.** A single model call between gates can overshoot the token or cost budget. Bound each call with a per-call maximum output token setting, and treat the run budget as the aggregate control.
- **Someone has to size budgets.** Values that are too tight cause unnecessary escalations; values that are too loose let waste through. Size them from evaluation runs and review them as part of policy change control.
- **The harness must report usage honestly.** If a code path calls a model or tool without going through the governor, the budget is blind to it. Route every model and tool call through the harness loop and test that no bypass exists.
- **Escalations cost human attention.** Continuations make the cost visible rather than hiding it, but approvers need enough context in the escalation packet to decide quickly.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Limits written into the system prompt | Not enforced, not observable and not consistent between runs. Can be overridden by injected content. |
| Let the model call a "check budget" tool | Better than nothing, but the model still decides whether to call it and whether to obey the answer. |
| Gateway token limits only | Sees model tokens, not tool calls, attempts, plans or run boundaries. Useful as a backstop. |
| Fixed loop counter in orchestration code with no policy | Enforced, but not versioned, not per profile, not auditable and not changeable without a code release. |
| Hard stop with no escalation | Safe, but discards progress. Escalation with a checkpoint lets a person decide whether more budget is worth spending. |
