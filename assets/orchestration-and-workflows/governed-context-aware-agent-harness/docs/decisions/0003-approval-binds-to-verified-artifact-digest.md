# ADR 0003: Approval binds to the verified artifact digest

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture section 8.5](../architecture.md#85-approval-request-and-decision), [Authentication and governance](../authentication-and-governance.md), [README](../../README.md), [ADR 0001](0001-harness-enforces-boundaries-not-transcripts.md), [verify-before-approve](../../../verify-before-approve/README.md)

## Context

Human approval is the last control before an agent's output is used. It is also easy to
weaken without noticing:

- **Approval of a different artifact.** The agent re-renders after verification, a UI shows
  a cached copy, or someone edits the stored artifact. The reviewer approves one thing and
  another thing ships.
- **Approval of the run instead of the output.** "Approve episode ep-0001" says nothing
  about which bytes were approved.
- **Narrated approval.** The model writes "the reviewer approved this". With no binding,
  nothing in the system can tell that this text is false.
- **Self-approval and rubber-stamping.** The agent's own identity, or an automated job in
  production, records the approval.
- **Approval as verification.** Reviewers are asked to catch numeric errors that a
  deterministic check could catch more reliably.

## Decision

1. **Verification comes first.** Only a verified artifact can be submitted for approval
   (strict gate mode). The approver confirms judgment and intent. It does not replace the
   verifier.
2. **The request carries digests.** An approval request names the artifact's SHA-256
   digest, the passing verification report's digest, and the verifier version.
3. **The artifact freezes.** After the request, render and analysis tools are refused
   ("artifact is frozen after an approval request"). Each episode can request approval
   once.
4. **The decision must echo the binding.** The harness accepts a decision only when all of
   these hold:
   - `request_id` and `artifact_sha256` match the request
   - the stored artifact still hashes to that digest
   - the approver is not the agent identity
   - live runs use a human approver
   - the decision is `approved`
5. **Pending is a stop, not a default.** A pending decision ends the episode as
   `awaiting_approval`. The harness never auto-approves, and no configuration setting can
   make it.
6. **The receipt binds everything.** An accepted decision yields a receipt derived from the
   trace ID, approver, and artifact digest. Memory is retained only with a receipt. The
   certificate records the receipt next to the artifact digest, and the completion ledger
   entry pins both.

## Consequences

### Positive

- What was verified, what was approved, and what was certified are provably the same
  bytes.
- A narrated approval cannot complete an episode, because completion needs a receipt the
  harness issued.
- Separation of duties is enforced in code, not only in process documents.
- Memory cannot learn from unapproved output.

### Trade-offs

- Any change after the request, however small, needs a new episode and a new approval.
- The approval UI must show the exact artifact by digest and send the digest back. A
  "simple approve button" is not enough.
- The reference treats the approver as a string. Production must take the approver from
  an authenticated identity, ideally with a signed decision
  ([authentication and governance](../authentication-and-governance.md)).
- Parking episodes at `awaiting_approval` needs resume handling and stale-request expiry.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Approve the episode or run ID | Does not say which artifact was approved. Edits after approval go undetected. |
| Approve before verification and let the human check the numbers | Puts deterministic checks on a human. Reviewers miss consistent but wrong figures. |
| Auto-approve when the verifier passes | Removes judgment and separation of duties. The verifier only covers what it was written to check. |
| Approve inside the agent conversation ("reply yes to approve") | The approver's identity and the artifact binding live in model-visible text that the model or an injected source can imitate. |
| Allow edits after approval, with a diff review | Feasible as a separate workflow, but every edit then needs re-verification. Simpler and safer to start a new episode. |
