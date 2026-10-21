# Evaluation and Troubleshooting

The verifier is a control, so it needs its own tests. A verifier that passes everything looks the same as a perfect artifact stream, and the only way to tell them apart is to feed it errors on purpose. This document covers how to test the verifier, how to reason about false accepts and false rejects, how to evaluate the correction loop and the approval experience, and how to diagnose each refusal code the gate can return.

Related documents:

- [Architecture](architecture.md)
- [Authentication and governance](authentication-and-governance.md)
- [Samples guide](../samples/README.md)
- [ADR 0003: unverified preview is a first-class state](decisions/0003-unverified-preview-is-a-first-class-state.md)

## 1. What to evaluate

| Component | Question | Primary method |
|---|---|---|
| Verifier | Does it catch the errors it claims to catch, without blocking correct artifacts? | Mutation testing plus a golden set of known-good artifacts |
| Gate | Does it refuse every invalid transition, receipt and decision? | Scenario tests that expect specific refusal codes |
| Correction loop | Does structured feedback lead to verified artifacts, within budget, without new errors? | Replay of failed attempts and their outcomes |
| Generator | How often does the first attempt verify? Which checks fail most? | Receipt analytics by check code |
| Approval experience | Do approvers spend their time on judgement? Are decisions reasoned? | Card review, rationale sampling, decision outcomes |

Evaluate these separately. A change that improves the generator's first-attempt pass rate is good news. A change that improves the pass rate because a check was loosened is a regression, and it only shows up if the verifier is tested on its own.

## 2. Testing the verifier itself

### 2.1 Mutation testing

Take a known-good artifact with its evidence. Apply one deliberate, realistic error. Run the verifier. Confirm that the receipt fails and that the failing check is the one you expected. Repeat for every class of error the verifier is meant to catch.

The sample ships this as `--mutations`, and the `mutation-suite-detects-every-injected-error` scenario runs it as part of `--self-test`:

| Mutation | What it changes | Expected failing check |
|---|---|---|
| `wrong-line-amount` | A line amount no longer equals quantity times unit price | `recompute.line_amounts` |
| `wrong-total` | The grand total is off | `recompute.totals` |
| `stale-tax-rate-applied` | Tax computed at an old rate | `recompute.totals` |
| `price-not-in-quote` | A unit price differs from the cited quote | `grounding.citations` |
| `missing-citation` | A line loses its citation | `grounding.citations` |
| `dangling-citation` | A citation points to evidence that does not exist | `grounding.citations` |
| `misquoted-fact` | A stated fact differs from the evidence it cites | `grounding.citations` |
| `vendor-not-approved` | The vendor is not on the approved list | `invariant.business_rules` |
| `over-budget` | The total exceeds the remaining budget | `invariant.business_rules` |
| `expired-quote` | The quote's validity date is in the past | `freshness.evidence_dates` |
| `restricted-value` | A free-text field contains an account-number-like value | `policy.restricted_content` |
| `restricted-key` | The artifact carries a restricted field such as `bank_account` | `policy.restricted_content` and `schema.contract` |
| `chart-value-mismatch` | A chart value differs from the table | `render.chart_matches_table` |
| `chart-series-dropped` | A chart series is missing | `render.chart_matches_table` |
| `schema-missing-total` | A required field is removed | `schema.contract` |
| `self-attested-verdict` | The artifact claims its own verdict | `schema.contract` |

Guidance for a real verifier:

- **One mutation per error class you care about.** Start from incident reviews and approver rejections. Every rejection reason an approver writes that a machine could have caught is a candidate mutation and a candidate check.
- **Assert the specific check, not only the verdict.** A mutation caught by the wrong check usually means two checks overlap, or one check is broken and another happens to cover for it.
- **Keep mutations realistic.** Off-by-one totals, swapped citations, a stale rate and a dropped series are the errors generators actually make. Random byte flips mostly test the schema check.
- **Run mutations in CI** against every verifier change. Treat a newly missed mutation as a failed build.
- **Keep a golden set** of known-good artifacts that must verify. This is the false-reject side of the same test.

### 2.2 A partial suite falsely accepts

The `--mutations` output also runs the same mutations against a suite that is missing `freshness.evidence_dates`. That suite reports `verified` for `expired-quote`. This is the failure mode to fear most: the receipt looks clean because the check that would have failed was never run.

The gate defends against it by requiring every code in `REQUIRED_CHECK_CODES` and refusing receipts that omit any of them (`incomplete_check_suite`). A verdict alone is never enough. Receipts also record `check_suite_sha256`, a digest of each check's code, category, determinism flag and statement, together with the verifier version. Together they tell you exactly which suite produced each receipt.

### 2.3 Testing the advisory judge separately

If you add an LLM-as-judge for qualities that code cannot check, such as tone or clarity, evaluate it as a model with its own dataset and rubric. Keep two properties fixed regardless of its quality:

- Its output is recorded with `gating: false` and never changes the verdict. The `advisory-judge-cannot-override-failed-check` scenario shows a judge trying to set the verdict and being ignored (`override_ignored: true`).
- It never replaces a deterministic check for a fact that code can check. If the judge is catching arithmetic or citation errors, add the deterministic check instead.

### 2.4 Testing independence and integrity

Beyond correctness, test that the verifier cannot be bypassed:

- A receipt issued by the generator's identity is refused (`independence_violation`).
- A receipt from an unlisted verifier version is refused (`untrusted_verifier`).
- A receipt whose verdict disagrees with its checks is refused (`verdict_mismatch`).
- A receipt modified after issue is refused (`receipt_integrity`).

The `verifier-independence-enforced` and `receipt-tamper-detected` scenarios cover these.

## 3. False accepts and false rejects

Two kinds of error matter, and they have different costs.

| | False accept | False reject |
|---|---|---|
| What happens | A wrong artifact is marked verified | A correct artifact is marked failed |
| Who notices | Possibly nobody, until after release | The generator, the requester and support |
| Typical cause | Missing check, loose tolerance, check reading the wrong field, partial suite | Over-strict tolerance, stale reference data, evidence fetched from the wrong source, rounding differences |
| Effect on approvers | They trust a receipt that is wrong | Items pile up as previews, or people learn to work around the system |
| How to detect | Mutation testing, post-release sampling, approver rejections that cite a checkable fact | Golden set, review of preview and failure reasons, requester feedback |

How to reason about them without inventing metrics:

- **A false accept is a verifier bug.** Fix the check, then add the mutation that would have caught it.
- **A false reject is also a bug, but a safe one.** The item still gets a labelled preview. Fix it quickly, because repeated false rejects train people to distrust the verifier.
- **Track counts by check code over time**, not a single pass rate. A sudden change in one check's failure count after a release usually points to that check or its reference data.
- **Sample released items** and re-verify them with the current verifier. Disagreement means either the verifier changed or something was released that should not have been.
- **Read approver rejections.** If approvers reject verified items for reasons a machine could check, the verifier has a gap. If they reject for judgement reasons, the pattern is doing its job.

## 4. Evaluating the correction loop

The loop gives the generator structured failures (`correction-feedback/v1`) and a fixed budget. In the sample, `DEFAULT_MAX_CORRECTIONS` is 2, so there are three attempts in total.

What to look at:

- **Recovery by check code.** Which failures does feedback usually fix? Recompute and citation failures often recover. Freshness failures often cannot, because the evidence itself is stale and the generator cannot make it fresh.
- **New failures introduced by a correction.** If a second attempt fixes the total but breaks a citation, the feedback may be too narrow, or the generator is regenerating the whole artifact instead of correcting it.
- **Failures that never recover.** Route them to a preview early. Spending attempts on a failure that needs new evidence wastes time and cost.
- **Budget.** Raise it only with evidence that later attempts recover. A larger budget mostly delays the preview.
- **Feedback content.** Feedback carries codes, details and findings, and the instruction treats evidence as data. Check that it never asks the generator to "make the check pass", and that it never echoes restricted values back into the prompt. The sample's policy findings report `[redacted]` instead of the matched value.

The `correction-loop-fixes-then-verifies` and `correction-budget-exhausted-unverified-preview` scenarios show both outcomes.

## 5. Approval experience and latency

This pattern exists to save approver attention, so evaluate that directly. The sample makes no timing claims, and you should measure in your own environment.

- **Time to decision** from request to decision. A falling value with steady rejection reasons is healthy. A falling value with empty or repeated rationales may be rubber-stamping.
- **Expired requests** (`vba.approval.expired`). A rising number usually means the card goes to the wrong people, at the wrong time, or asks too much.
- **Re-requests after verification failures.** Each new revision needs a new request. Frequent re-requests on one item point to unstable evidence or a weak generator.
- **Rationale quality.** Sample rationales. Good ones refer to the judgement items on the card: vendor fit, timing, risk. Rationales that restate totals suggest the card is not making clear what was already verified.
- **Card review.** Show the card to approvers and ask what they think they are being asked to decide. If the answer is "check the numbers", the card needs work.
- **Tier mix.** If most items land in the high tier, the thresholds may be wrong, and senior approvers become the bottleneck.

## 6. Troubleshooting

Every refusal in the sample raises with a code and emits a `vba.refusal` span. Start from the code.

| Code | Where | Likely cause | What to do |
|---|---|---|---|
| `independence_violation` | Gate start-up, receipt validation | The verifier shares an identity with the generator or agent, or a receipt was issued by one of them | Give the verifier its own workload identity. Make sure the agent cannot call the receipt-writing path. |
| `receipt_integrity` | Receipt validation | The receipt was modified after issue, or serialized differently | Do not transform receipts in transit. Store and pass them as issued. |
| `digest_mismatch` | Receipt validation, decision, release | The artifact or evidence changed after verification or approval | Expected after any edit. Re-verify the new revision and request a new approval. If nobody edited it, look for a mutable store or a transform step. |
| `untrusted_verifier` | Receipt validation | The verifier version is not on the trusted list | Add the version through change control after its mutation tests pass, or roll back the verifier. |
| `incomplete_check_suite` | Receipt validation | A required check did not run | Restore the check or update `REQUIRED_CHECK_CODES` through change control. Never drop a check to get items through. |
| `verdict_mismatch` | Receipt validation | The verdict disagrees with the check results | Treat as a verifier defect or tampering. Investigate before trusting other receipts from the same version. |
| `request_digest_mismatch` | Verifier | The verification request's digests do not match the artifact or evidence handed over | Fix the request builder or the evidence fetch. Do not let callers supply their own digests. |
| `write_once` | Store | Something tried to overwrite a stored record | Find the duplicate write. Records are immutable. Write a new record instead. |
| `not_found` | Store | A referenced record does not exist | Check ids and storage location. A missing receipt blocks approval and release by design. |
| `integrity` | Store | A stored record's content no longer matches its id | Treat as tampering or storage corruption. Freeze releases that depend on it. |
| `duplicate_item` | Submit | The same item id was submitted twice | Use a new item id for new work. Use revisions for corrections. |
| `unknown_item` | Lookups | The item id is not known to the gate | Check the id and the gate instance. |
| `not_verified` | Approval request | Approval was requested for an item that is not `VERIFIED` | Expected for previews. Fix the failing checks or provide better evidence, then re-verify. |
| `replay` | Decision | A decision id was already used, or its nonce was retired | Expected for duplicate callbacks. Make the channel callback idempotent. |
| `stale_request` | Decision | The request is unknown, or is not the item's current request | The approver acted on an old card. Send the current card. |
| `not_awaiting_approval` | Decision, expiry | The item is not waiting for approval | It was already approved, rejected, expired or released. Check state before acting. |
| `expired` | Decision | The decision arrived after the request expired | Request approval again if the item is still wanted. |
| `not_expired` | Expiry | Expiry was attempted before the deadline | Check the clock source and the expiry job schedule. |
| `self_approval` | Decision | A service, agent, channel, judge or verifier identity tried to approve | Make sure the approver identity comes from the authenticated user, not the caller. |
| `separation_of_duties` | Decision | The requester tried to approve | Route to a different approver. |
| `unauthenticated_approver` | Decision | The approver is not known to the directory | Check the identity provider mapping and group membership. |
| `unauthorized_for_tier` | Decision | The approver lacks an eligible role for this tier | Route to an approver with the right role, or review the role mapping. |
| `duplicate_approver` | Decision | The same person approved twice on a two-person request | Route to a second distinct approver. |
| `invalid_decision` | Decision | The decision value is not `approve` or `reject` | Fix the channel payload mapping. |
| `rationale_required` | Decision | The rationale is empty | Require a rationale in the card. |
| `approval_required` | Release | Approval is needed and missing, or there are not enough bound approvals | Request approval, or collect the remaining approvals. |
| `not_releasable` | Release | The item is not in a releasable state | Check whether it is a preview, rejected, expired or already released. |
| `illegal_transition` | State machine | Code tried a transition the state machine forbids | A defect in the calling code. The state machine is the source of truth. |

Other symptoms:

| Symptom | Likely cause | What to do |
|---|---|---|
| Item stuck in `VERIFYING` | The gate refused the receipt, so the item did not advance | Look for a `vba.refusal` span with a receipt code for that item |
| Many previews with one failing check | Stale reference data, a strict tolerance or a generator regression | Compare against the golden set. Check the reference data the check reads. |
| Verified items rejected by approvers for checkable reasons | Verifier gap | Add a check and a mutation |
| Advisory flags on every item | Judge prompt or rubric drift | Re-evaluate the judge. It does not block, but noise hides real flags. |
| Approval cards show raw totals for re-checking | Card design | Move machine-verified items into the verified checklist and ask judgement questions |

## 7. Release gates for verifier changes

Before a new verifier version is added to the trusted list:

1. All mutations are caught by the expected checks.
2. All golden-set artifacts verify.
3. `check_suite_sha256` changes only when the set of checks or a check's definition changed, and that change is reviewed.
4. Receipts from the new version pass gate validation in a staging gate.
5. Sampled recent releases still verify with the new version, or any differences are explained.
6. The previous version stays trusted until in-flight approvals that reference it have completed or expired.

These map onto the evaluation stages in [agent path to production](../../../delivery-and-ops/agent-path-to-production/README.md).
