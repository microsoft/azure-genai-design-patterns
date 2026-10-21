# ADR 0002: Fail closed when the policy store or the decision sink is unavailable

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [ADR 0001](0001-enforce-budgets-in-harness-not-prompt.md), [ADR 0003](0003-profiles-selected-by-server-policy.md)

## Context

The governor depends on two things outside the agent process:

- **The policy store** holds the versioned policy document (profiles, envelopes, budgets) and the revocation list that acts as a kill switch.
- **The decision sink** receives a decision record for every allow, deny and escalate, for audit and for the run certificate.

Either can be unreachable: a network fault, throttling, an expired credential, a misconfigured role assignment or a regional incident. The harness then has to choose between stopping and continuing on some fallback.

Continuing is tempting, because a stalled agent looks like an outage. The usual fallbacks are a cached policy of unknown age, built-in defaults compiled into the harness, or "log locally and upload later". Each has a failure mode that defeats the purpose of the pattern:

- **Built-in defaults** become a second, unreviewed policy. They tend to be permissive, because they were written to keep the agent working.
- **A stale cache** keeps enforcing a profile after it has been revoked. The kill switch stops working exactly when an operator may need it.
- **Deferred audit** means actions happen with no durable record. If the local buffer is lost, there is no evidence the action was authorized, which is worse for an irreversible or external action than not acting at all.

Bounded autonomy exists so that an agent acts only inside a known envelope. If the governor cannot establish the envelope or cannot record its decision, the envelope is not known.

## Decision

1. **No policy, no run.** If the policy store cannot be read when a run starts, the governor refuses the run with `deny` and `POLICY_UNAVAILABLE`. It never substitutes defaults.
2. **No policy, no action.** The governor reads the revocation key on every gate. If the store is unreachable during a run, the gate returns `deny` with `POLICY_UNAVAILABLE` and the run moves to `suspended`. No step runs while it is suspended.
3. **No audit record, no action.** Every decision is written to the decision sink before the harness acts on it. If the write fails, the governor returns `deny` with `AUDIT_UNAVAILABLE`, records the outcome it would have returned in the failed record's details, and suspends the run. An `allow` that cannot be recorded is never returned.
4. **Suspension is resumable and does not consume budget.** Suspended time is excluded from the active wall-clock budget. When the dependency recovers, the harness calls `retry_after_suspension`. The governor re-reads the policy (or re-tests the sink) and returns the run to its previous status with `POLICY_RESTORED` or `AUDIT_RESTORED`.
5. **Revocation stops runs.** If the revocation key names the run's profile, the gate returns `deny` with `PROFILE_REVOKED` and the run moves to `stopped`. This is the kill switch, and it only works if the revocation key is read on every gate (decision 2).
6. **Resolutions re-check policy.** Approving, clarifying, retrying or continuing an escalation performs a live policy read first. If the store is unavailable, the resolution is refused. Stopping a run does not need a read, so a person can always stop a run.
7. **A cache may reduce load, not extend authority.** An adapter may cache the policy document for a short, bounded interval within a single version. It must not serve a cached document after a read failure, and it must not cache the revocation key past the interval the operator accepts for kill-switch latency.

## Consequences

### Positive

- An outage cannot widen what the agent may do. The worst case is that the agent stops and waits.
- The kill switch is reliable. Revoking a profile affects in-flight runs at their next gate.
- Every executed action has a decision record, which the [evidence ledger](../../../evidence-ledger-and-run-certificate/README.md) can persist and a run certificate can cite.
- Suspended runs resume where they stopped, without re-planning and without losing budget to the outage.

### Trade-offs

- **Availability of the agent is bounded by the availability of the policy store and the sink.** Design both for the availability the workload needs: replicas, private networking, and alerts on `POLICY_UNAVAILABLE` and `AUDIT_UNAVAILABLE`.
- **Suspended runs need an owner.** Something has to call `retry_after_suspension` (a scheduler, a queue trigger or an operator), and suspended runs that never recover must be closed out.
- **Per-gate reads add load.** Read the small revocation key on every gate and the larger policy document only when its version changes.
- **Users see refusals during incidents.** Explain them plainly ("the agent is paused because its policy could not be checked") rather than retrying silently.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Fall back to built-in defaults | Creates an unreviewed second policy that is usually more permissive than the real one. |
| Serve the last cached policy indefinitely | Revocations stop taking effect during the outage, which disables the kill switch. |
| Allow read-only actions when policy is unavailable | Reads can still move regulated data across a boundary or leak it into a prompt. Some workloads may accept this, but it must be an explicit, reviewed policy decision, not a default. |
| Log decisions locally and upload later | Actions happen with no durable record. A lost buffer leaves irreversible actions unaccounted for. |
| Fail open with an alert | The alert arrives after the action. For irreversible or external actions that is too late. |
