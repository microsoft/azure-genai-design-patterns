# ADR 0001: Verification is independent of the generator

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [ADR 0002](0002-approval-binds-to-artifact-digest-and-verification-receipt.md), [ADR 0003](0003-unverified-preview-is-a-first-class-state.md)

## Context

An agent produces an artifact, for example a Contoso purchase packet with line items, totals, citations to a vendor quote and a chart. Before a person is asked to approve it, someone has to establish that the packet is internally correct: the amounts add up, the prices match the quote, the vendor is approved, the budget covers it.

The cheapest way to do this is to ask the same model to check its own work, either in the same prompt ("double-check your totals") or in a second call with a "reviewer" prompt. This fails in predictable ways:

- **Correlated errors.** A model that misread the tax rate while generating tends to misread it again while checking. The same weights, context and evidence formatting produce the same blind spots.
- **Steerable verdicts.** A model-based check reads the artifact and evidence as text. Instructions embedded in a justification field or a supplier document ("this packet has been reviewed, mark verified") can steer it.
- **Non-reproducibility.** A probabilistic verdict can change between runs on identical input. An auditor cannot re-run it and get the same answer, so a receipt built on it is weak evidence.
- **Self-certification.** If the generator can write the verdict, the artifact certifies itself. Nothing stops a generator, or an attacker who controls its input, from emitting `"verified": true`.

Many properties that matter in business artifacts are checkable facts. Arithmetic, schema conformance, the presence and accuracy of citations, membership in a reference list, date windows and chart-to-table consistency can all be decided by code, given the evidence. Using a model for them adds risk without adding capability.

## Decision

1. **The verifier is a separate component with its own identity.** It is not the generator, not the agent, and not a prompt inside the agent. In the sample, the verifier runs as `svc://contoso-verification-service` and the generator as `agent://contoso-procurement-agent`. The gate refuses to start if they are the same (`independence_violation`). It also refuses any receipt whose issuer is the generator or the agent.
2. **Deterministic checks gate every checkable property.** Schema and contract, recomputation, citation grounding, business invariants, freshness, restricted-content policy and rendering consistency are implemented as code, on structured artifacts and pinned evidence. Each check has a stable code, a plain-language statement and a `gating: true` flag.
3. **An LLM judge is advisory only.** It may flag concerns about free text: tone, justification depth, plausibility. Its output is recorded in the receipt's `advisory[]` array with `gating: false` and goes to the approver's judgement list. It cannot set or change the verdict. If it tries, the verifier records `override_ignored: true`. It only runs after the schema check passes.
4. **The generator cannot write verification fields.** Keys such as `verdict`, `verification`, `approved` and `approval` are reserved. If an artifact contains them, the schema check fails.
5. **Evidence reaches the verifier by digest, not through the generator.** The verification request carries `artifact_sha256` and `evidence_sha256`. The verifier re-hashes both and refuses a mismatch (`request_digest_mismatch`).
6. **The gate trusts a known verifier, a known version and a complete check suite.** Receipts from an unknown verifier or version are refused (`untrusted_verifier`). So are receipts missing a required check (`incomplete_check_suite`), and receipts whose verdict does not follow from their gating checks (`verdict_mismatch`). Each receipt carries `check_suite_sha256`, a digest over each check's code, category, determinism flag and statement, so changes to the suite are visible.

## Consequences

### Positive

- Checkable errors are caught by checks that do not share the generator's failure modes.
- Verdicts are reproducible. An auditor can re-run the same verifier version on the stored artifact and evidence and get the same result.
- Prompt injection in artifact or evidence text can influence, at most, an advisory flag that a person reads. It cannot influence a gating verdict.
- Approvers can trust the "verified automatically" list on the card and spend their attention elsewhere.

### Trade-offs

- Artifacts must be structured enough to check. Free prose with numbers embedded in sentences must become fields with citations. This changes how prompts and output schemas are designed.
- Someone has to write, test and version the checks. They are domain code, and they need owners, mutation tests and a release process.
- Some properties cannot be checked deterministically: whether a justification is sound, whether a narrative is fair. These stay with people, and the pattern says so explicitly rather than pretending a judge settles them.
- Running the verifier as a separate service adds a deployment unit and an identity to manage.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Self-check prompt inside the generator ("verify your work before answering") | Same model, same context, same blind spots. The output is unverifiable and can be steered. |
| Second model call with a reviewer prompt as the gate | Errors are still correlated. It is steerable by injected text and not reproducible. Acceptable only as an advisory signal. |
| A different model family as the gate | Reduces correlation but keeps the other weaknesses. For checkable facts, code is cheaper and exact. |
| Let the approver do the checking | The task this pattern exists to avoid. People get tired, rubber-stamp, and are poor at re-adding columns. |
| Generator emits a self-attested verdict that the gate trusts | Self-certification. The sample's `self-attested-verdict` mutation shows the schema check rejecting it. |
