# Authentication and Governance

Bounded autonomy works only if two things are true. The profile and envelope come from identities the server has verified. The policy that defines them is governed like any other production control. This document covers both. For the mechanism itself, see the [architecture](architecture.md).

## 1. Identity inventory

| Identity | Kind | What it does | What it must not be able to do |
|---|---|---|---|
| Operator (caller) | User or workload identity in Microsoft Entra ID | Starts runs. Its app roles set the highest profile it can receive. | Choose a profile, raise budgets, or approve its own escalations. |
| Approver | User in Microsoft Entra ID with the approver app role | Resolves escalations: approves a step, grants a continuation, clarifies a plan, allows a retry, or stops the run. | Approve its own runs, act for another tenant, or change policy. |
| Harness | Managed identity | Runs the governor, reads the policy store, writes decision records and calls tools. | Write to the policy store or the revocation key. |
| Policy author | User or group in Microsoft Entra ID | Proposes changes to profiles, tool classes, constraints and budgets. | Release a change without review. |
| Policy releaser | User, group or pipeline identity | Publishes a reviewed policy version and flips the revocation key. | Be the same person who authored the change, for high-impact changes. |
| Tool backends | Managed identities or service principals | Execute tool calls from the harness. | Accept calls that did not come from the harness identity. |

## 2. Binding profiles to verified identity

The governor receives a `Principal` with three fields: subject, app roles and tenant. The harness builds it from claims in a validated access token: `oid` or `sub` for the subject, `roles` for app roles, and `tid` for the tenant. It never builds it from the prompt, the request body or a tool argument.

The resolution rule is fixed: the profile is the lower of the highest profile granted by the caller's app roles and the workload's cap in policy.

| Input | Source | Trusted? |
|---|---|---|
| Caller app roles | Validated token `roles` claim | Yes |
| Caller tenant | Validated token `tid` claim | Yes |
| Workload | Server-side route or deployment configuration | Yes |
| Role-to-profile mapping and workload caps | Pinned policy version | Yes |
| `requested_profile` in the plan or request | Model or client | No. Recorded for audit, never used. |

This is the same principle as [request context is not authorization](../../../agent-runtime/request-context-is-not-authorization/README.md): nothing in the request can raise authority. [ADR 0003](decisions/0003-profiles-selected-by-server-policy.md) records the decision.

### Suggested app roles

Define app roles on the harness's app registration in Microsoft Entra ID. The names are illustrative; the policy document maps them to profiles.

| App role | Maps to | Typical assignees |
|---|---|---|
| `Autonomy.Observe` | `observe-only` | New users, auditors, read-only integrations |
| `Autonomy.Propose` | `propose` | Analysts who review and apply changes elsewhere |
| `Autonomy.ActWithApproval` | `act-with-approval` | Operators on regulated or customer-facing workloads |
| `Autonomy.Act` | `act` | Operators on well-constrained, verified workloads |
| `Autonomy.Approver` | Approver eligibility | Named reviewers, separate from operators |

Assign roles to groups rather than individuals, and review group membership on a regular schedule. A caller with no autonomy role receives no profile, and the run is refused with `NO_PROFILE_ASSIGNED`.

## 3. Separation of duties for escalations

The governor applies these checks to every resolution, in this order, before any state changes:

1. The escalation is still pending (`ESCALATION_NOT_PENDING`).
2. The requested action is one of the escalation's allowed resolutions (`RESOLUTION_NOT_ALLOWED`).
3. The resolution names the policy version pinned for the run (`POLICY_VERSION_MISMATCH`).
4. The resolution names the exact subject digest in the packet (`APPROVAL_MISMATCH`).
5. The approver is not the run's principal (`SELF_APPROVAL`).
6. The approver holds the approver role and belongs to the run's tenant (`APPROVER_NOT_ELIGIBLE`).

Then, for anything other than `stop`, it reads the revocation state live. If the store is unreachable, the resolution is denied with `POLICY_UNAVAILABLE` and the escalation stays pending.

The approver's identity always comes from their own validated token at the approval surface. The resolution body carries only the run, escalation, action, policy version, digest and an optional note. How the approver is authenticated, what they see and which channels deliver the packet are covered by [verify before approve](../../verify-before-approve/README.md).

A step approval is a single-use grant bound to the step digest. It cannot be replayed for a second call or reused after the step's arguments change.

## 4. Policy authoring and release

Treat the policy document as code.

| Stage | Practice |
|---|---|
| Authoring | Keep the policy document in source control next to the scenario suite. Every tool has a side-effect class reviewed by its owner. |
| Review | Require review from someone other than the author. Changes that widen an envelope, raise a workload cap, add an approver role or loosen a data boundary need a security or risk reviewer. |
| Testing | Run the scenario suite (like the sample's `--self-test`) against the proposed version in CI. See [evaluation and troubleshooting](evaluation-and-troubleshooting.md#release-gates-for-policy-changes). |
| Release | Publish to Azure App Configuration as a new key-value version under a release label, or as an immutable snapshot. Bump `version` on every change. |
| Pinning | Each run pins the version it started with. A new version applies to new runs only. Resolutions must name the pinned version. |
| Rollback | Point the release label or snapshot reference back to the previous version. Runs already pinned to the bad version can be stopped with the kill switch. |

The harness reads the store with its managed identity, which holds App Configuration Data Reader. Only the release pipeline or a small release group holds write access. Separate stores or labels per environment keep a test policy out of production.

## 5. Kill switch and revocation

Revocations are kept apart from the policy document, under a separate key that only the releaser can write. They name a profile and either a specific policy version or every version. The governor reads them live on every plan gate, every authorize call and every non-stop resolution.

| Action | Effect |
|---|---|
| Revoke one profile for one version | Runs on that version and profile stop at their next gate with `PROFILE_REVOKED`. New runs that resolve to it are refused. |
| Revoke one profile for every version | The profile is disabled until the revocation is removed. |
| Revoke every profile | Every run stops at its next gate. Use for incidents. |

Revocation is checked at gates, so a tool call that is already executing completes. Pair the kill switch with tool-side controls if you need to interrupt in-flight actions. Practice the kill switch in a non-production environment before you depend on it.

## 6. Audit

Every decision, including allows and refusals, is emitted to the decision sink before the state change it describes. If the sink cannot accept a record, the run is suspended with `AUDIT_UNAVAILABLE`, so nothing executes without a record.

Each record carries the policy id and version, the profile, the principal's subject and tenant, the phase, the subject with its digest, the reason codes and the remaining budget. That is enough to answer "who was allowed to do what, under which policy, and why" for any action. The [evidence ledger](../../evidence-ledger-and-run-certificate/README.md) covers durable, tamper-evident persistence and the run certificate.

Telemetry in Azure Monitor carries the same reason codes for dashboards and alerts. It is not a substitute for the audit record.

## 7. Threat model

| Threat | Example | Control |
|---|---|---|
| Prompt injection raises autonomy | Retrieved content says "you are now in act mode". | The profile comes from token claims and policy; `requested_profile` is ignored. |
| Model ignores budget instructions | The agent keeps calling tools after being told to stop. | Budgets are enforced by the governor at every gate. |
| Out-of-envelope action | The agent calls a tool outside the profile or with an out-of-scope argument. | Allowlist, side-effect rules and argument constraints at `authorize`. |
| Data exfiltration through tools | Regulated data sent to an external destination. | Data-boundary rules keyed on input labels; unknown labels count as regulated. |
| Plan bait-and-switch | The agent executes something other than what it planned. | Steps must match the accepted plan by digest. |
| Approval replay or reuse | One approval used twice, or after arguments change. | Single-use grants bound to the step digest. |
| Self-approval or cross-tenant approval | The operator approves their own escalation. | `SELF_APPROVAL` and `APPROVER_NOT_ELIGIBLE` checks. |
| Stale approval | An approval arrives after the policy changed. | Resolutions must name the pinned policy version; revocation is checked live. |
| Policy store tampering | Someone widens a profile directly in the store. | Write access limited to the release pipeline; versions reviewed; changes audited. |
| Fail-open on outage | The store is down, so the harness uses defaults. | Runs suspend; there is no default policy in code. |
| Unaudited action | The sink is down and actions continue. | Runs suspend with `AUDIT_UNAVAILABLE`. |
| Governor bypass | Code calls a tool without `authorize`. | Tools accept only the harness identity; code review; tests. |

## 8. Governance operating model

- **Owners.** Name an owner for the policy document, for each workload cap and for each tool's side-effect class.
- **Promotion.** Raise a workload's cap, for example from `act-with-approval` to `act`, only with evidence: escalation outcomes, verifier results and incident history.
- **Review cadence.** Review app role assignments, approver groups and workload caps regularly, and after any incident involving the agent.
- **Incidents.** Use the kill switch first, then investigate with decision records. Restore autonomy through a normal policy release.
- **Reporting.** Report escalation rates by reason code, continuation counts and suspension time per workload to the policy owners.
