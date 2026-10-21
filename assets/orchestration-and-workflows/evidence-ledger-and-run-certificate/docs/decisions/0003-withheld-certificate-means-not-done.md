# ADR 0003: A withheld certificate means the run is not done

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [ADR 0001](0001-hash-chain-plus-signed-certificate.md), [ADR 0002](0002-digests-not-content-in-ledger.md)

## Context

Agent platforms usually report a run as finished when the loop ends: the model stops calling tools, the orchestrator records `completed`, and the output is handed on. That status says execution ended. It says nothing about whether the output is fit to use.

In practice, runs end with a confident answer in many states that are not acceptable:

- A required check failed, or was never run, while the model's text says "all checks look fine".
- The verifier passed an earlier version of the artifact, not the one being shipped.
- Nobody approved the artifact, the approver rejected it, or the approver was the person who started the run.
- A tool call ran that policy had denied, even though the final artifact looks clean.

If "done" is a flag that any component can set, or is inferred from the model's own summary, these runs slip through. If the issuer refuses to produce anything when a condition fails, the absence of a certificate is ambiguous: it could mean the run failed, the issuer crashed, or the certificate was lost.

## Decision

1. **The issuer always produces a signed certificate.** Its `status` is `issued` only when every rule passes, and `withheld` otherwise.
2. **The issuer evaluates every rule and records every reason.** A withheld certificate lists all of its `withheld_reasons`, de-duplicated and in a stable order, so an operator sees the full picture in one pass.
3. **The issuer does not trust claims; it recomputes them.** Required checks come from the run context, not the receipt. A check passes only when `passed` is true and `status` is `passed`, regardless of the receipt's own verdict. Approvals must be in the ledger, bound to the current artifact digest and receipt, and not from the initiator or the agent. Every tool call must follow an earlier `allow` decision for the same action.
4. **Done has one definition.** `is_done(cert, findings)` is true only when the certificate is `issued`, `withheld_reasons` is empty, and offline verification returns no findings. Nothing else, including `run_closed` with status `completed`, a confident model output or a `verified` receipt, means done.
5. **A clean artifact does not redeem a bad run.** A policy violation during the run withholds the certificate even when the artifact passes every check and is approved.
6. **A withheld run is fixed by a new run.** A sealed run is never edited or re-issued to make it pass. The cause is fixed and a new run, with a new run ID, produces a new certificate.

## Consequences

### Positive

- Downstream systems gate on one signed field and one verifier call, not on a mix of statuses and log searches.
- A withheld certificate is evidence too. It proves that the run happened, that it was evaluated, and exactly why it is not done.
- "Missing certificate" means only one thing: issuance did not happen. That is an operational alert, distinct from a run that was evaluated and withheld.
- Promoting a withheld certificate to `issued` breaks its signature. The scenario `withheld-certificate-is-not-done` pins this.

### Trade-offs

- **Every closed run costs a signing call,** including runs that are withheld. This is deliberate: withheld evidence is as important as issued evidence.
- **Stricter than "the output looks right".** Users may see a good-looking artifact that is not done because, for example, an approval is missing. The withheld reasons must be visible in the user experience, so the next step is clear.
- **Reasons are decided at issuance.** The sample's offline verifier checks the signature and bindings but does not re-derive the reasons from the ledger. A verifier that re-derives them is an extension point that reduces trust in the issuer.
- **New rules change what done means.** Adding a reason code is a policy change. Version it, test it, and review it like any other gate.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Treat `run_closed` with status `completed` as done | Says that execution ended, not that the output is verified, approved and policy-compliant. |
| Trust the verifier's own verdict | A receipt can omit a required check and still say `verified`. Required checks must come from the run context. |
| Refuse to issue anything on failure | The absence of a certificate becomes ambiguous, and the reasons are not recorded as signed evidence. |
| Stop at the first failing rule | Faster, but operators fix one reason, re-run and hit the next. Listing every reason is cheaper overall. |
| Let an operator override a withheld certificate | Turns evidence into an editable flag. Overrides belong in a new run with its own approval, recorded in its own ledger. |
