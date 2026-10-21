# Sample: autonomy governor

`autonomy_governor.py` is a single-file, standard-library-only implementation of the bounded autonomy governor described in [the architecture](../docs/architecture.md). It shows the mechanism, not a product. Every Azure dependency sits behind a `typing.Protocol` seam with an in-memory fake for the self-test and a production adapter stub that raises `NotImplementedError`.

The sample does not call a model, a tool or any network endpoint. "Tool calls" are step descriptors that the governor authorizes or refuses. Time comes from a fake clock, so every run is deterministic.

## Requirements

- Python 3.10 or later.
- No third-party packages, no Azure subscription and no credentials.

## Run it

From the repository root:

```powershell
# Deterministic self-test: 23 scenarios against each of the 4 Domain Packs
python assets\orchestration-and-workflows\bounded-autonomy\samples\autonomy_governor.py --self-test

# Readable demo run (default Domain Pack: it-operations)
python assets\orchestration-and-workflows\bounded-autonomy\samples\autonomy_governor.py

# Same demo for another industry
python assets\orchestration-and-workflows\bounded-autonomy\samples\autonomy_governor.py --domain healthcare-scheduling

# Emit the decision records, escalation packet and final runtime state as JSON
python assets\orchestration-and-workflows\bounded-autonomy\samples\autonomy_governor.py --json

# Print the policy document that the governor enforces for a Domain Pack
python assets\orchestration-and-workflows\bounded-autonomy\samples\autonomy_governor.py --policy --domain supply-chain

# List scenarios or Domain Packs, or run selected scenarios only
python assets\orchestration-and-workflows\bounded-autonomy\samples\autonomy_governor.py --list
python assets\orchestration-and-workflows\bounded-autonomy\samples\autonomy_governor.py --list-domains
python assets\orchestration-and-workflows\bounded-autonomy\samples\autonomy_governor.py --self-test --scenario budget-exhausted-escalates --scenario resume-after-approval
```

The self-test prints one `PASS <scenario> [<domain>]` line per scenario and Domain Pack, then a summary line. It exits with status `0` when every scenario passes and `1` otherwise.

## What the self-test covers

| Scenario | What it proves |
|---|---|
| `allow-in-envelope-read` | A planned read-only step inside the profile is allowed and consumes one tool call. |
| `server-policy-selects-profile` | The profile is the lower of the caller's role grant and the workload cap. A profile requested in the prompt is recorded and ignored. |
| `observe-only-denies-write` | `observe-only` exposes the read tool only. A write is refused with `TOOL_NOT_IN_PROFILE`. |
| `propose-records-proposal` | Under `propose`, a reversible write is not executed. It is recorded as a proposal with `PROPOSE_ONLY`. |
| `propose-denies-irreversible` | Under `propose`, an irreversible or external action is refused with `SIDE_EFFECT_EXCEEDS_PROFILE`. |
| `tool-not-in-profile-denied` | A tool that is not in the allowlist, or has no side-effect class, is refused. |
| `data-boundary-denied` | Regulated data cannot flow to an external destination (`DATA_BOUNDARY_VIOLATED`). |
| `plan-required-before-act` | No step is authorized before a plan is accepted (`PLAN_REQUIRED`). |
| `unplanned-step-denied` | A step whose digest is not in the accepted plan is refused (`STEP_NOT_IN_PLAN`). |
| `arg-constraint-escalates` | An argument outside its constraint (scope or amount) escalates (`ARG_CONSTRAINT_VIOLATED`). |
| `irreversible-escalates-under-act` | Even under `act`, an irreversible or external action escalates for approval (`APPROVAL_REQUIRED`). |
| `budget-exhausted-escalates` | When the tool-call budget is spent, the next step escalates (`BUDGET_EXHAUSTED`). |
| `token-wall-clock-and-cost-budgets` | Token, active wall-clock and optional cost budgets are metered by the harness and enforced at the next gate. |
| `attempts-budget-escalates` | Plan attempts are charged before evaluation, so repeated rejected plans exhaust the attempt budget. |
| `verifier-failures-escalate` | Consecutive verifier failures past the threshold escalate (`VERIFIER_FAILURES_EXCEEDED`). |
| `low-confidence-escalates` | A low-confidence or ambiguous plan escalates for clarification (`LOW_CONFIDENCE`). |
| `policy-unavailable-fails-closed` | If the policy store cannot be read, the run refuses to start or suspends (`POLICY_UNAVAILABLE`). |
| `kill-switch-stops-run` | Revoking a profile stops in-flight runs at the next gate (`PROFILE_REVOKED`). |
| `resume-after-approval` | An eligible approver approves the exact escalated step. The run resumes and the single-use grant is consumed. |
| `approval-must-match-escalation` | Stale policy versions, mismatched step digests, self-approval and ineligible approvers are refused. |
| `continue-after-budget-in-new-run` | A budget escalation can be continued only as a child run with fresh budgets, a checkpoint and a continuation limit. |
| `audit-unavailable-fails-closed` | If the decision record cannot be written, the action is refused and the run suspends (`AUDIT_UNAVAILABLE`). |
| `budget-pinned-for-run` | A policy published mid-run does not change budgets for that run; it applies to new runs. |

## How to read the code

| Section | What to look for |
|---|---|
| `R` | Stable reason codes. Dashboards, alerts and runbooks key on these, not on message text. |
| `build_policy` | The versioned policy document: tool classes, role grants, workload caps and the four profiles with their side-effect rules, argument constraints, data boundary and illustrative budgets. |
| `resolve_profile` | Profile selection from verified roles and workload. The prompt is not a parameter. |
| `envelope_check` | Evaluation precedence: allowlist, then data boundary, then side-effect rule, then argument constraints. |
| `PolicyStore`, `InMemoryPolicyStore`, `AppConfigurationPolicyStore` | The policy-store seam, the fake and the production stub. |
| `DecisionSink`, `InMemoryDecisionSink`, `EvidenceLedgerSink` | The decision-record seam, the fake and the production stub. |
| `AutonomyGovernor` | The gate the harness loop calls: `start_run`, `submit_plan`, `authorize`, `record_verification`, `charge`, `complete_run`, `resolve_escalation` and `retry_after_suspension`. |
| `DOMAIN_PACKS` | Four industry configurations that reuse the same governor code. |

## Production seams

| Seam | Fake used by the self-test | Production stub | What a real adapter does |
|---|---|---|---|
| `PolicyStore` | `InMemoryPolicyStore` | `AppConfigurationPolicyStore` | Reads the policy document from Azure App Configuration by key and label, or from an immutable snapshot, using a managed identity with the App Configuration Data Reader role. Reads the revocation (kill-switch) key on every gate. Raises `PolicyStoreUnavailable` on any read failure and never falls back to defaults. |
| `DecisionSink` | `InMemoryDecisionSink` | `EvidenceLedgerSink` | Appends each decision record to an append-only evidence ledger. See [Evidence ledger and run certificate](../../evidence-ledger-and-run-certificate/README.md). Raises `DecisionSinkUnavailable` if the write is not acknowledged. |

Model calls, tool execution and the verifier are outside the governor. The harness loop calls the governor before each step, executes the step only on `allow`, reports verification results with `record_verification` and reports metered usage with `charge`.

## Budgets are illustrative

The budgets in `build_policy` (attempts, tool calls, tokens, active wall-clock seconds and an optional cost ceiling) exist so the scenarios have something to exhaust. They are not recommendations. Size real budgets from evaluation runs of your own workload, as described in [Evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md).

## Extending the sample

- **Add a Domain Pack.** Add a `DomainPack` entry to `DOMAIN_PACKS` with an organization, a workload, read, write and irreversible tools with example arguments, the scope and amount constraints for the write tool, the destination of the irreversible tool, a tool that no profile may use, and an example of regulated data. The self-test runs every scenario against it automatically.
- **Add a reason code.** Add it to `R`, emit it from the governor, add a scenario that asserts it, and add a row to the troubleshooting table.
- **Swap a seam.** Implement `PolicyStore` or `DecisionSink` against your store, keep the fail-closed contract, and run the self-test against the fake to confirm the governor behavior is unchanged.
