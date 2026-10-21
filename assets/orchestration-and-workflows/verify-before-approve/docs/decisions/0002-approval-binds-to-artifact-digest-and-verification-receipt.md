# ADR 0002: Approval binds to the artifact digest and the verification receipt

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md), [Authentication and governance](../authentication-and-governance.md), [ADR 0001](0001-verification-is-independent-of-the-generator.md), [Governed agent harness ADR 0003](../../../governed-context-aware-agent-harness/docs/decisions/0003-approval-binds-to-verified-artifact-digest.md)

## Context

An approval is only meaningful if it applies to a specific thing. In many workflows it applies to something vague: a ticket, a conversation, "the latest version", a draft in a shared folder. That leaves several gaps:

- **Time of check to time of use (TOCTOU).** A person approves version A. Before release, the agent regenerates, a collaborator edits a cell, or a retry produces version B. Version B ships with version A's approval.
- **Approving the wrong evidence.** The artifact is unchanged but was verified against older evidence, or against a different check suite. The approval implicitly endorses checks that never ran.
- **Replay.** A decision captured for one request is submitted again for another item, or for a later revision of the same item.
- **Ambiguous audit.** Months later, nobody can prove which bytes the approver saw and which checks had passed when they clicked.

The [governed context-aware agent harness](../../../governed-context-aware-agent-harness/README.md) binds approval to the verified artifact digest. This ADR makes that binding concrete for the standalone pattern and adds the receipt, nonce and expiry.

## Decision

1. **The artifact digest is the identity of what is approved.** The digest is sha256 over canonical JSON (sorted keys, compact separators, ASCII escaping). For production, consider a standard canonicalization such as [RFC 8785](https://www.rfc-editor.org/rfc/rfc8785).
2. **Every approval request carries four binding values:** `artifact_sha256`, `receipt_id`, a single-use `nonce` and `expires_at`. The gate builds the request only for an item in `VERIFIED` state with a receipt it has just re-validated.
3. **Every decision must echo the binding.** A decision whose `artifact_sha256` or `receipt_id` differs from the current request is refused (`digest_mismatch`). A decision for an unknown request, or one whose request id or nonce is not the item's current one, is refused (`stale_request`). A decision that repeats a recorded decision or carries a retired nonce is refused (`replay`). A decision after expiry is refused (`expired`). Nonces are retired when a request is completed, rejected or expired.
4. **Any change invalidates approval.** Every change to the artifact, including one made by the generator, a person or a correction, creates a new revision with a new digest. That revision must be verified again and go through a new approval request. Nothing carries over.
5. **Release re-checks everything at the last moment.** Release re-hashes the bytes being shipped and compares them with the verified digest (`digest_mismatch`). It re-reads and re-validates the receipt, and it counts only approve decisions bound to the same digest, receipt and request (`approval_required` if too few).
6. **Records are write-once and self-identifying.** Receipt, request, decision and release ids are derived from a hash of their content. The store refuses overwrites (`write_once`) and re-hashes on read (`integrity`). The gate also re-derives each receipt id before trusting it (`receipt_integrity`).
7. **The card shows the binding.** The approver sees the digest and receipt id they are approving, so the binding is visible to people and not only to machines.

## Consequences

### Positive

- Approving A and shipping B is refused at release, not discovered in an audit.
- A decision cannot be replayed onto another item, revision or request.
- An auditor can go from a release record to the exact bytes, the exact checks and the exact decisions, and confirm each link by re-hashing.
- Expiry stops stale approvals from authorizing releases long after the context has changed.

### Trade-offs

- Any edit, even a cosmetic one, requires re-verification and re-approval. Teams must design artifacts so that cosmetic layers, such as rendering, are derived after release, or accept the extra round.
- Canonicalization must be stable across languages and library versions. A change in serialization changes digests and breaks bindings, so it needs a version and a migration plan.
- Approval channels must carry the digest, receipt id and nonce through to the callback. Some low-code approval actions may need extra fields or a wrapper to do this.
- Expiry policy is a trade-off between stale approvals and approver workload. The sample's window is a placeholder.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Approve the item id or ticket, not the bytes | Classic TOCTOU. The content can change under the approval. |
| Approve the prompt or plan that produced the artifact | Prompts do not determine outputs. Approves intent, not effect. |
| Lock the artifact after approval, with no digest | Locks can be bypassed or released by mistake. A digest makes any change detectable, even outside the system. |
| Bind to the artifact digest only, not the receipt | An approval would survive a change of evidence or check suite. The approver endorsed specific checks. |
| No nonce, rely on digest uniqueness | The same artifact can legitimately be re-submitted, for example after expiry. A nonce makes each request single-use. |
