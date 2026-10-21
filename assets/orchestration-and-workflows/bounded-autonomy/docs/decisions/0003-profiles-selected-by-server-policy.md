# ADR 0003: The autonomy profile is selected by server-side policy, never by the prompt or the model

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture](../architecture.md), [Authentication and governance](../authentication-and-governance.md), [README](../../README.md), [ADR 0001](0001-enforce-budgets-in-harness-not-prompt.md), [ADR 0002](0002-fail-closed-on-policy-unavailable.md)

## Context

An autonomy profile decides how far an agent may go on its own: only observe, only propose, act with approval, or act. The profile has to come from somewhere. Several sources look convenient and are unsafe:

- **The prompt or request body.** A field such as `"mode": "act"` or a sentence such as "you have full permission to make changes" is caller-controlled text. Anyone who can reach the agent can claim any profile.
- **The model's own judgement.** Asking the model to decide whether a task is "safe enough" to act on lets the content of the task, including injected content, choose the level of control applied to it.
- **A client-side setting.** A toggle in the user interface is a claim made by the client, not a fact the server has verified.

The [request context is not authorization](../../../../agent-runtime/request-context-is-not-authorization/README.md) pattern makes the same point for identity: claims that arrive in the request are inputs to verify, not facts to trust. An autonomy profile is an authorization decision, so it must follow the same rule.

## Decision

1. **The profile is resolved on the server from verified inputs only.** The inputs are the caller's verified identity (the roles in a validated Microsoft Entra ID token) and the workload the run belongs to (from server-side configuration). The prompt, the plan and the model output are not inputs.
2. **The profile is the lower of the role grant and the workload cap.** Each role maps to a profile (`Autonomy.Observe` to `observe-only`, `Autonomy.Propose` to `propose`, `Autonomy.ActWithApproval` to `act-with-approval`, `Autonomy.Act` to `act`). The caller's highest grant is then capped by the workload. A restricted workload can lower autonomy for everyone; a role grant can never raise it above the cap.
3. **No grant, no run.** A caller with no autonomy role, or a workload with no cap, is refused with `NO_PROFILE_ASSIGNED`.
4. **A requested profile is recorded and ignored.** If a plan carries a requested profile, the governor writes it to the decision record and sets `requested_profile_ignored`. This makes attempts to escalate privilege through the prompt visible in telemetry without letting them take effect.
5. **The profile is fixed for the run.** It is resolved once in `start_run`, pinned with the policy version, and included in every decision record. Changing profile means starting a new run. Revoking a profile stops the run (see [ADR 0002](0002-fail-closed-on-policy-unavailable.md)).
6. **Approvers are authorized the same way.** Approver eligibility comes from a verified role (`Autonomy.Approver`) in the same tenant, and an approver cannot approve a run they started.

## Consequences

### Positive

- Prompt injection cannot raise autonomy. The worst a malicious instruction can do is generate a `requested_profile_ignored` record.
- Profiles are reviewable. Who holds which role, and which workloads are capped, are facts in Microsoft Entra ID and in the policy store, with their own change control.
- The same agent code can serve different audiences at different autonomy levels by configuration alone.

### Trade-offs

- **Role design is needed up front.** App roles have to be defined, assigned to groups and reviewed. Without that, every caller is refused.
- **On-behalf-of chains need care.** When a front-end calls the agent on behalf of a user, the profile must come from the user's verified token, not from the front-end's own identity, unless the workload is explicitly an unattended service workload with its own cap.
- **Some flexibility is lost.** A user cannot "just this once" ask for more autonomy. They ask for a role, or an approver approves individual steps through escalation.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Profile chosen by a request field or prompt text | Caller-controlled. Anyone can claim any profile. |
| Model classifies the task risk and picks a profile | The content being governed chooses its own governance. Steerable by injected content. |
| One profile for the whole deployment | Simple, but forces the most restrictive profile on every caller, or the least restrictive on all of them. |
| Profile from role grant only, no workload cap | A user with a broad role gets the same autonomy in every workload, including sensitive ones. |
| Model may request a higher profile mid-run | Turns the profile into a negotiation. Escalation of individual steps to a person covers the legitimate need. |
