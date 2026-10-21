# Evaluation and Troubleshooting

This document covers how to test a bounded autonomy governor, how to size budgets from evidence instead of guesses, what to watch in production and how to diagnose each reason code. For the mechanism, see the [architecture](architecture.md).

## What to evaluate

| Property | Question | How to test |
|---|---|---|
| Profile resolution | Does every caller and workload get the profile policy says, and nothing higher? | Table-driven tests over role and workload combinations; adversarial prompts that request a higher profile. |
| Envelope correctness | Is every tool, side-effect class, argument constraint and data boundary enforced? | One allow case and one deny or escalate case per rule, per tool. |
| Plan binding | Can the agent execute anything it did not plan? | Change one argument after plan acceptance; call an unplanned tool; repeat a completed step. |
| Budget enforcement | Does each budget stop the run at the next gate? | Exhaust each budget in turn: attempts, tool calls, tokens, active wall clock and cost. |
| Escalation and resolution | Are escalations raised for the right reasons, and can only valid resolutions unlock them? | Resolve with wrong digests, wrong policy versions, the operator's own identity, a non-approver and an approver from another tenant. |
| Fail-closed behavior | Do policy store, revocation and sink failures stop execution? | Fault injection on each dependency at each gate. |
| Resumability | Does a run resume exactly where it stopped after approval, retry or restart? | Persist state, restart the harness, resolve, and compare completed steps. |
| Agent quality under bounds | Does the agent still finish real tasks inside its envelope? | Task success rate and escalation rate on a representative evaluation set. |

The first seven properties are deterministic and belong in unit tests. The last one depends on the model and the task mix, and belongs in your evaluation pipeline.

## Scenarios as evaluation cases

The sample's `--self-test` runs 23 scenarios against four Domain Packs. Treat it as a template for your own suite:

- **Keep one scenario per reason code.** Every reason code your governor can emit should have at least one test that asserts it, and the expected state change.
- **Run each scenario against each workload configuration.** The sample runs the same scenarios against every Domain Pack. Do the same for every workload in your policy, so a new tool or constraint is covered automatically.
- **Assert the decision record, not just the outcome.** Check reason codes, policy version, profile, subject digest and remaining budget. Dashboards and audits depend on those fields.
- **Add adversarial cases.** Include prompts and retrieved content that claim a higher profile, ask the agent to skip planning, or embed tool calls with out-of-scope arguments. The expected result is the same decision you get without the injected text.
- **Run the suite on every policy change.** A policy change is a code change. See [release gates](#release-gates-for-policy-changes).

## Sizing budgets from evaluation runs

Budget values in the sample are illustrative. Real values come from your workload. This asset gives no recommended numbers, because the right values depend on the task, the tools, the model and how much a stuck run costs you.

A practical process:

1. **Build a representative task set.** Include routine tasks, hard but legitimate tasks, and tasks that should fail or escalate. Label each with its expected outcome.
2. **Run it with generous evaluation budgets** in a non-production environment, under `observe-only` or `propose` or against sandboxed tools. The goal is to see how much a successful run actually uses.
3. **Collect usage per run** from the runtime state and decision records: attempts, tool calls, tokens, active seconds and cost, with the run's final outcome.
4. **Compare successful runs with runs that looped or failed.** Good budgets sit above nearly all successful runs and below the point where failing runs clearly stop making progress. Choose the percentile and headroom deliberately, and record why.
5. **Set budgets per profile and workload,** not globally. A read-heavy investigation and a single-change remediation have very different shapes.
6. **Check what each exhausted budget produces.** Every exhaustion escalates. If evaluation runs often exhaust a budget while making progress, the budget is too tight or the plans are too coarse. If looping runs rarely exhaust anything, the budgets are too loose.
7. **Revisit with production telemetry.** Track `BUDGET_EXHAUSTED` escalations and their resolutions. Many continuations granted for one workload means the budget is too tight for real work. Many stops means the budget is catching runs that should stop.

Notes on individual budgets:

| Budget | What drives it | Things to watch |
|---|---|---|
| Attempts | How often the agent needs to replan, including after verifier failures | Each plan submission costs one, including rejected plans. |
| Tool calls | The number of steps in typical plans | Proposals under `propose` do not consume tool calls; executed steps do. |
| Tokens | Context size, tool output size and number of model calls | Measured from platform usage data and checked at the next gate, so one call can overshoot. Set per-call output limits. |
| Active wall clock | Tool latency and model latency | Time spent escalated or suspended does not count. |
| Cost | Model pricing and any metered tools | Optional. Use it when cost matters more than tokens, for example with mixed models. |

The verifier failure threshold and the minimum plan confidence are tuned the same way: compare evaluation runs that succeeded after a retry with runs that never recovered, and compare plan confidence with task outcome.

## Measuring escalation quality

Escalations are the cost of bounded autonomy. Too few means the envelope is too wide; too many means approvers stop reading. Track these per workload and profile:

| Metric | What it tells you |
|---|---|
| Escalation rate by reason code | Which bound is doing the work. A rise in one code after a release points to the change. |
| Resolution mix (approve, continue, clarify, retry, stop) | Whether escalations are mostly legitimate work or mostly runs that should stop. |
| Approved steps later failing verification | Whether approvers have enough information in the packet. |
| Time from escalation to resolution | Whether the escalation channel is staffed. Do not set targets from this asset; agree them with the business. |
| Continuation chain length | Whether budgets are too tight, or runs are being extended instead of fixed. |
| `requested_profile_ignored` occurrences | How often prompts or clients try to request autonomy. Useful as a prompt-injection signal. |

## Telemetry and dashboards

Emit one telemetry event or span per decision, through OpenTelemetry to Azure Monitor and Application Insights. Suggested attributes:

| Attribute | Source field |
|---|---|
| `autonomy.run_id`, `autonomy.decision_id` | `run_id`, `decision_id` |
| `autonomy.outcome` | `outcome` |
| `autonomy.reason_codes` | `reason_codes`, as a JSON array string |
| `autonomy.profile` | `profile` |
| `autonomy.policy_id`, `autonomy.policy_version` | `policy` |
| `autonomy.phase` | `phase` |
| `autonomy.subject_type`, `autonomy.tool` | `subject` |
| `autonomy.budget_remaining.*` | `budget_remaining` |

Do not put argument values, tool output or model text in telemetry. The decision record in the evidence ledger is the audit copy.

An example query, assuming each decision is written as a custom event named `autonomy.decision`. Adjust table and column names to match how your exporter writes the data.

```kusto
customEvents
| where timestamp > ago(1d) and name == "autonomy.decision"
| extend outcome = tostring(customDimensions["autonomy.outcome"]),
         profile = tostring(customDimensions["autonomy.profile"]),
         policy_version = tostring(customDimensions["autonomy.policy_version"]),
         reasons = todynamic(tostring(customDimensions["autonomy.reason_codes"]))
| mv-expand reason = reasons to typeof(string)
| summarize decisions = count() by bin(timestamp, 1h), outcome, reason, profile, policy_version
| order by timestamp desc
```

Alerts worth having:

- Any `POLICY_UNAVAILABLE` or `AUDIT_UNAVAILABLE`. Runs are suspended until the dependency recovers.
- Any `PROFILE_REVOKED`. Someone used the kill switch, and in-flight runs are stopping.
- A rise in `BUDGET_EXHAUSTED`, `VERIFIER_FAILURES_EXCEEDED` or `LOW_CONFIDENCE` after a policy or model change.
- `SELF_APPROVAL` or `APPROVER_NOT_ELIGIBLE`. Someone tried to resolve an escalation they should not.
- Escalations pending longer than the time the business agreed.

## Troubleshooting by reason code

| Reason code | What it means | Likely cause | What to check or do |
|---|---|---|---|
| `NO_PROFILE_ASSIGNED` | Run refused at start. | Caller has no autonomy app role, or the workload has no cap. | Check the token's `roles` claim and the workload name against `workload_caps`. |
| `TOOL_NOT_IN_PROFILE` | Step denied. | Tool missing from the profile allowlist or from `tool_classes`. | Is the tool meant to be available at this profile? Add it through a policy release, not a hotfix. |
| `SIDE_EFFECT_EXCEEDS_PROFILE` | Step denied. | An irreversible or external action under a profile that does not permit it. | Expected under `observe-only` and `propose`. If the workload needs it, review the cap. |
| `PROPOSE_ONLY` | Step recorded as a proposal, not executed. | Reversible write under `propose`. | Expected. Proposals are in the runtime state for someone to apply. |
| `DATA_BOUNDARY_VIOLATED` | Step denied. | Input labels are too sensitive for the destination, or labels are missing. | Check `input_labels` on the step. Missing or unknown labels count as regulated. |
| `ARG_CONSTRAINT_VIOLATED` | Step escalated. | Argument outside its allowed values or range, or missing. | The `violations` list in the decision details names the argument and the rule. |
| `APPROVAL_REQUIRED` | Step escalated. | Irreversible or external action under `act`, or any write under `act-with-approval`. | Expected. Route to an approver. |
| `PLAN_REQUIRED` | Step denied. | The harness called `authorize` before a plan was accepted, or after the plan was voided by a verifier failure. | Submit a plan first. After a verifier failure, replan. |
| `STEP_NOT_IN_PLAN` | Step denied. | Tool, arguments, destination or labels differ from the planned step. | Compare step digests. Small argument changes produce a different digest. |
| `STEP_ALREADY_EXECUTED` | Step denied. | The harness retried a completed step. | Check harness retry logic. Replan if the step really must run again. |
| `BUDGET_EXHAUSTED` | Step or plan escalated. | A budget is spent. | `details` names the budget. See [sizing budgets](#sizing-budgets-from-evaluation-runs). |
| `VERIFIER_FAILED` | Verification failed; plan voided. | The step's result did not pass the verifier. | Read the findings, then replan. |
| `VERIFIER_FAILURES_EXCEEDED` | Run escalated. | Consecutive failures reached the profile's threshold. | Is the verifier right? Is the task feasible? Resolve with retry or stop. |
| `LOW_CONFIDENCE` | Plan escalated. | Plan confidence below the minimum, or ambiguities listed. | Resolve with `clarify-plan` or `stop`. Look for ambiguous requests upstream. |
| `POLICY_UNAVAILABLE` | Run refused or suspended. | Policy store or revocation key unreachable or invalid. | Store health, network path, managed identity role assignment, document schema. Retry with `retry_after_suspension`. |
| `AUDIT_UNAVAILABLE` | Run suspended. | Decision sink did not acknowledge the write. | Evidence ledger health. Retry with `retry_after_suspension`. |
| `PROFILE_REVOKED` | Run stopped or refused. | Kill switch set for this profile and version. | Expected during an incident. Start new runs after the revocation is lifted. |
| `RUN_NOT_RUNNING`, `RUN_NOT_SUSPENDED` | Call refused. | The harness called the wrong operation for the run's status. | Read the run status before calling. |
| `ESCALATION_NOT_PENDING`, `RESOLUTION_NOT_ALLOWED` | Resolution refused. | Escalation already resolved, or action not offered. | Use `allowed_resolutions` from the packet. |
| `POLICY_VERSION_MISMATCH`, `APPROVAL_MISMATCH` | Resolution refused. | The approval surface sent a stale version or digest. | Re-fetch the packet and resolve again. |
| `SELF_APPROVAL`, `APPROVER_NOT_ELIGIBLE` | Resolution refused. | Approver is the operator, lacks the role, or is in another tenant. | Route to an eligible approver. Review role assignments if this was unexpected. |

## Debugging a single run

1. Get all decision records for the `run_id`, and for its parent and child runs if it was continued, in order.
2. Read `PROFILE_ASSIGNED` first. Its details show the role profile and workload cap that produced the profile, and whether a profile was requested and ignored.
3. Follow `budget_remaining` across records to see which budget ran down and when.
4. For a denied step, compare its `step_digest` with the plan's digests in `PLAN_ACCEPTED`.
5. For an escalation, compare the packet's `subject_digest` with the resolution's. A mismatch means the approval was for different content or an older state.
6. Compare the runtime state snapshot with the last decision. `last_decision_id` should match.

## Release gates for policy changes

Before a new policy version is published:

- [ ] The scenario suite passes against the new version for every workload.
- [ ] Every new tool has a side-effect class reviewed by its owner, and at least one allow and one refusal test.
- [ ] Changes that widen an envelope, raise a workload cap, loosen a data boundary or change approver eligibility have a second reviewer from security or risk.
- [ ] Budget changes cite evaluation runs or production telemetry.
- [ ] The evaluation set's success and escalation rates are compared with the current version, and differences are explained.
- [ ] The `version` field changed, and the release label or snapshot reference is updated in a single step.
- [ ] Rollback to the previous version has been rehearsed.
- [ ] Dashboards and alerts are split by `policy_version` so the effect of the release is visible.

Runs already in flight keep their pinned version. To move them to the new policy, stop them or let them finish, and start new runs.
