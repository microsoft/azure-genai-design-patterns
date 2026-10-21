# ADR 0003: Unverified preview is a first-class state

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md), [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md), [ADR 0001](0001-verification-is-independent-of-the-generator.md), [ADR 0002](0002-approval-binds-to-artifact-digest-and-verification-receipt.md)

## Context

Verification sometimes fails, and correction does not always fix it. The vendor is not on the approved list, the budget is exhausted, the quote has expired, or the generator cannot produce a value that re-derives from the evidence. The system has to do something with the result.

Common responses all hide the failure:

- **Silent downgrade.** Send the artifact for approval anyway, with a warning in small print or in a log. The approver sees a normal card and approves.
- **"Approved with exceptions".** Let the approver override a failed check. Over time, overrides become routine and the checks stop mattering.
- **Infinite retry.** Keep asking the generator to fix it. Cost grows without limit, outputs oscillate, and a pass may be luck rather than correctness.
- **Discard.** Throw the work away. The requester gets nothing useful and does not learn why.

The work is often still useful: a requester may want to see the draft to decide what to change upstream, such as choosing an approved vendor or asking finance for budget. What must not happen is that it looks like, or can become, an approvable artifact.

## Decision

1. **Correction is bounded.** The generator gets a fixed number of attempts. The sample uses `DEFAULT_MAX_CORRECTIONS = 2`, so three attempts in total. Each correction receives structured feedback: the failed check codes, paths, expected and observed values, suggested values where safe, and remaining attempts. See the `correction-feedback/v1` contract in [architecture section 10.4](../architecture.md#104-correction-feedback).
2. **When the budget is exhausted, the item enters `UNVERIFIED_PREVIEW`.** This is a terminal state. The gate writes an `unverified-preview/v1` record with a fixed banner, the failed check codes, the attempt count and the receipt id of the last failed verification.
3. **There is no transition from `UNVERIFIED_PREVIEW` to approval or release.** Requesting approval is refused (`not_verified`). Release is refused (`not_releasable`). The state machine raises `illegal_transition` for any path out of it.
4. **No override of a failed gating check.** Approvers cannot approve past a failure. To move forward, someone changes the inputs or the artifact, and a new item goes through verification from the start.
5. **Previews are visible, not hidden.** The preview is a record the requester can see. It is clearly labelled and shows exactly which checks failed. The gate emits a `vba.preview` span, so preview rates are measurable.

## Consequences

### Positive

- A failure is never presented as a normal approval. Approvers only see artifacts that passed every gating check.
- Cost and latency of correction are bounded and predictable in count, though not in time.
- Requesters get a useful draft and a precise reason, which shortens the path to fixing the real cause.
- Preview rates by check code are a direct signal of prompt, evidence or policy problems.

### Trade-offs

- Some legitimate exceptions now need a process outside this flow, such as a policy exception that updates the approved-vendor list or budget. That is deliberate, but it must exist.
- Teams will push to raise the correction budget. Raising it should be a measured decision, informed by how often extra attempts succeed, not a default.
- A check that rejects too often (false rejects) produces many previews and erodes trust. The fix is to correct the check, which requires monitoring and mutation tests. See [testing the verifier itself](../evaluation-and-troubleshooting.md#2-testing-the-verifier-itself).

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Send failed artifacts for approval with a warning | Silent downgrade. Approvers habituate to warnings, and the audit trail shows an approval of a failed artifact. |
| Allow approvers to override failed checks | Turns checks into suggestions. Overrides become routine and the checks stop being controls. |
| Retry until verification passes | Unbounded cost and oscillation. A pass after many tries may be chance. |
| Discard failed artifacts | Loses useful work and the explanation of why it failed |
| Let the generator decide when to stop | The generator is the component that failed. Stopping must be the gate's policy. |
