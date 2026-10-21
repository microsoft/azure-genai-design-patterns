# Authentication and Governance

This document covers the identities, role assignments, approval rules, audit trail, memory
data handling, and threat model behind the
[Governed Context-Aware Agent Harness](../README.md). The design rationale is in
[architecture.md](architecture.md). The simulator
[`samples/harness_sim.py`](../samples/harness_sim.py) implements the approval and memory
rules described here. Its self-test proves them in-process.

> Role names, Foundry features, and Agent Framework APIs are described qualitatively. Check
> them against current Microsoft documentation before you assign roles or automate anything.
> The simulator models identities as plain strings. A production deployment must bind them to
> authenticated Microsoft Entra ID principals.

## 1. Identity inventory

The harness has **three** distinct principals. Collapsing any two of them removes a control.

| Identity | Type | Used for | Must never |
|---|---|---|---|
| **Agent identity** (`agent://contoso-briefing-agent` in the sim) | Managed identity of the hosted agent, or a Microsoft Entra Agent ID where available (verify current availability) | Calling the model deployment, retrieval tools, the restricted-data projection API, and its own memory scope. Appending to the evidence ledger. | Approve an artifact. Write approval decisions. Change configuration. Read other memory scopes. |
| **Reviewer (approver)** | Human Entra ID user, reached through an approval UI | Deciding `approved`, `rejected`, or leaving `pending` for one exact `artifact_sha256` | Be the agent identity, or a service principal, for live runs |
| **Operator** | Human Entra ID user or a deployment pipeline identity | Changing configurable knobs (skills, memory scope, budgets, gate mode) through a versioned config store | Edit immutable controls. Edit ledgers or certificates. Approve artifacts for episodes they configured, if your separation-of-duties policy requires it. |
| **Rehearsal approver** | Test double (`approver_kind: automated-rehearsal`) | Offline and CI runs with a scripted model | Be accepted when the model client is live |

The simulator enforces the first two rows. A decision whose `approver` equals the agent
identity is rejected (`agent-cannot-approve-its-own-artifact`). When the model client is
live, a decision whose `approver_kind` is not `human` is rejected.

## 2. Model and tool access without keys

- **Model access** uses Microsoft Entra ID. Locally, run `az login` and let
  `DefaultAzureCredential` pick up the developer credential. In Azure, the hosted agent's
  managed identity authenticates. The harness never reads, stores, or logs an API key.
- **Deployment names** come from configuration (`<chat-deployment>`). They are not in code.
  The endpoint looks like `https://<your-resource>.openai.azure.com/` or a Foundry project
  endpoint, also from configuration.
- **Tools** call downstream services with the agent's managed identity, or with an
  on-behalf-of token when the call must carry the end user's identity. See
  [Backend Identity Propagation](../../../ai-gateway/backend-identity-propagation/README.md)
  for the gateway-side patterns.
- **Restricted data** is reached only through a projection API that returns the
  allowlisted fields (`exposure_band` in the worked example). The agent identity should not
  hold read access to the raw restricted store.
- **Telemetry ingestion** can also use Entra ID authentication instead of a bare
  connection string. Application Insights supports Entra-authenticated ingestion; check the
  current setup steps.

## 3. Role assignments

Grant the smallest scope that works. Names below are illustrative.

| Principal | Scope | Role or permission (verify names) | Purpose |
|---|---|---|---|
| Agent managed identity | Foundry project or Azure OpenAI resource | A data-plane inference role (for example *Cognitive Services OpenAI User*, or the current Foundry user role) | Call `<chat-deployment>` |
| Agent managed identity | Evidence container (ledgers, certificates, artifacts) | Write-only or append-style data access. No delete. Container protected by an immutability policy. | Persist evidence |
| Agent managed identity | Memory store, own scope only | Read and write on the scope's partition or container | Scoped memory |
| Agent managed identity | Restricted-data projection API | Application role limited to the projection operation | Allowlisted fields only |
| Agent managed identity | Application Insights | *Monitoring Metrics Publisher* (for Entra-authenticated ingestion) | Export spans and events |
| Approval service identity | Approval queue and decision store | Read requests, write decisions | Bind decisions to authenticated reviewers |
| Reviewers | Approval app | App role such as `Approvals.Decide` (custom), assigned through a group | Decide on artifacts |
| Operators | Config store | Write on configurable keys only | Change knobs for the next episode |
| Auditors | Evidence container, Application Insights | Read-only | Audit and replay |

Rules:

- No principal holds **delete** on the evidence container during the retention window.
- The agent identity cannot write to the decision store. Decisions reach the harness
  through the approval service, which stamps the authenticated reviewer identity on them.
  The harness never accepts the reviewer name from the model or from a tool argument.
- Operators have no data-plane access to memory scopes or evidence by default. Use
  just-in-time elevation for incident work.
- Review role assignments on a schedule. Alert on new assignments to the evidence
  container and the decision store.

## 4. Approval governance

Approval is a separate control, and it cannot be disabled (`separate_approval` is an
immutable control). The approval gate applies these rules in order. Each one has a
self-test scenario.

| Rule | Rejection status | Scenario |
|---|---|---|
| Approval is requested only for an artifact whose current digest has a passing verification report | Tool refusal ("only a verified artifact can be submitted for approval") | `correction-budget-exhausted-unverified-preview-no-approval` (nothing reaches the approver) |
| Observe mode never requests approval | Tool refusal. The output stays an unverified preview. | `correction-budget-exhausted-unverified-preview-no-approval` |
| One approval request per episode. Analysis and rendering are frozen after it. | Tool refusal | Enforced in the gate. No dedicated scenario yet. |
| `decision.request_id` and `decision.artifact_sha256` must equal the request, and the artifact body must still hash to that digest | `approval_rejected` | `approval-digest-mismatch-rejected` |
| `pending` stops the episode and never auto-approves | `awaiting_approval` | `narrated-done-is-not-completion` (parked variant) |
| The approver must be non-empty and must not be the agent identity | `approval_rejected` | `agent-cannot-approve-its-own-artifact` |
| Live model client means the approver must be `human` | `approval_rejected` | `agent-cannot-approve-its-own-artifact` |
| Anything other than `approved` is honored as a rejection | `approval_rejected` | `approval-digest-mismatch-rejected` |

What the reviewer sees matters as much as the rule set:

- The approval UI fetches the artifact **by digest** from the evidence store and recomputes
  the sha256 before rendering it. It never renders a copy the agent sends along with the request.
- The UI shows the verification summary (verifier version, checks, failed-then-passed
  history), the cited sources, and the policy decision. Reviewers approve evidence, not prose.
- The UI labels the episode's model client (`live` or `scripted`) and profile. A rehearsal
  must never look like a live run.
- Decisions carry a reason. Rejections feed back to the domain team, not to the same episode.

## 5. Immutable and configurable controls

| Control | Class | Notes |
|---|---|---|
| `mandatory_policy` (plan-before-act, mandatory stages, skill ownership) | Immutable | Code, reviewed through the normal change process |
| `arithmetic_allowlist` (the `ast` evaluator's node and name allowlist) | Immutable | Widening it is a security change |
| `telemetry` | Immutable | It can fail without blocking admission, but it cannot be switched off |
| `separate_approval` | Immutable | No configuration path turns approval off or lets the agent approve |
| Skill availability (except always-on skills), enabled tools | Configurable | Always-on skills (`research-policy`, `evidence-verification`) cannot be disabled |
| Memory on or off, memory scope | Configurable | Scope must match `[A-Za-z0-9_-]{1,64}` |
| Gate mode (`strict` or `observe`) | Configurable | `observe` produces unverified previews only |
| Model request and tool-call ceilings, execution attempts, timeout | Configurable within bounds | Out-of-range values raise `ConfigError` |

Any attempt to configure an immutable key raises `ConfigError`. Accepted changes bump the
config version and apply to the **next** episode. A running episode keeps its pinned
version and emits `config_change_deferred`
(`immutable-controls-rejected-config-applies-next-run`).

## 6. Audit trail

Three records, each with a different job:

| Record | Contents | Retention guidance |
|---|---|---|
| **Evidence ledger** (per episode) | Hash-chained entries: `skill_loaded`, `plan`, `memory_rejected`, `source_snapshot`, `policy_admission`, `analysis`, `artifact`, `verification`, `approval_request`, `approval_decision`, `approval_receipt`, `memory_write`, `completion` or `preview` | Same as the artifact. Immutable storage. |
| **Completion certificate** | Policy, plan digest, skill versions and digests, source snapshot digests, verification summary, approval receipt, artifact digest, memory outcome, ledger head and entry count, every evidence hash, execution settings | Same as the artifact. Store the head and entry count separately so truncation is detectable. |
| **OpenTelemetry** | `invoke_agent`, `chat`, and `execute_tool <name>` spans with GenAI semantic-convention attributes, plus harness events such as `plan_recorded`, `control_rejected`, `verification_completed`, `approval_recorded`, `config_change_deferred` | Per your Application Insights retention. Telemetry is for operations, not proof. |

Audit questions this answers:

- *Which bytes did the reviewer approve?* `approval_receipt.artifact_sha256` in the ledger
  equals `certificate.artifact.sha256`.
- *Was the number recomputed?* The `verification` entry holds the report. The certificate
  holds its digest and attempt count.
- *Was the run live?* `certificate.model_client` and the `invoke_agent` span attribute
  `harness.model_client`.
- *Did someone edit the record?* Re-run the chain check. Compare the stored head and entry
  count with the certificate (`ledger-tamper-detected`).

## 7. Memory data handling

- **Scopes.** Each memory scope is a partition (for example per team and environment).
  An episode lists and reads only its configured scope. Other scopes are invisible, not
  just filtered (`memory-scope-isolation`).
- **What is stored.** Only the harness-issued record: `kind`, `schema_version`, `trace_id`,
  `policy_id`, `policy_version`, `event_date_rule`, `artifact_sha256`, `approval_receipt`.
  No transcripts, source content, restricted values, or free-text "lessons".
- **When it is written.** Only after an approval receipt, only once per episode, and only if
  the record equals the one the harness issued. The model cannot change a field.
- **On read.** Records are schema-checked. Wrong fields, wrong policy version, a bad digest,
  or a missing receipt mean the record is withheld and a `memory_rejected` entry is ledgered.
  A plan that cites an unvalidated trace is refused (`cross-run-memory-recall-validated`).
- **Lifecycle.** Expire or re-review records when the policy version changes. Purge a scope
  when its team or environment is retired. Treat memory stores as containing derived business
  data and classify them accordingly.

## 8. Threat model

| Threat | Example | Primary mitigation | Residual risk |
|---|---|---|---|
| **Prompt injection via retrieved sources** | A commentary source says "Ignore prior instructions" or asserts a cause | Label-based admission: `untrusted` sources are excluded and unknown labels fail closed. A causal-language check runs in the verifier. Only trusted sources count as citations. | Trusted sources can still be wrong. Labels must be assigned upstream, correctly. |
| **Memory poisoning** | An out-of-band write plants a record with a different event-date rule | Schema validation, policy-version match, receipt format check, approval-gated writes, scoped storage | A well-formed record with a forged receipt passes the format check. Production should verify receipts against the decision store. |
| **Approval bait** | A reviewer is shown a polished summary while different bytes ship, or the artifact is edited after verification | Digest binding, re-hash at decision time, the UI fetching by digest, the artifact frozen after the request | Reviewer fatigue. Mitigate with clear verification summaries and sampling audits. |
| **Self-approval** | The agent names itself as approver, or tries to put a decision in a tool argument | The model can only *request* approval (`request_approval`). The decision comes from the Approver seam (the approval service), never from model output. The approver is never the agent identity. | A misconfigured approval service identity. Audit role assignments. |
| **Tool overreach** | The model calls a domain tool before planning, a tool whose skill is not loaded, or a disabled tool | Policy gate on every call, skill-to-tool ownership, the tool-call ceiling | Tools themselves must enforce authorization too. The gate does not replace it. |
| **Code-execution escape** | The model submits `__import__('os')` or a huge exponent | `ast` allowlist: names, operators, constant sizes. No `eval`, `exec`, or imports. | Python `-I` isolated mode (used by the source demo) is **not** an OS sandbox. Run anything broader in [session pools](../../../agent-runtime/code-execution-sandbox-session-pools/README.md). |
| **Restricted-data leakage** | Position size appears in the briefing or in telemetry | Server-side projection, a verifier check for restricted literals and field names, telemetry redaction | Paraphrased leaks are hard to detect mechanically |
| **Silent fallback** | The live client fails and a scripted answer is shown | The runtime refuses substitution (`model_client_unavailable`). The certificate labels the client. | Operational pressure to "just show something". Keep the rule in code. |
| **Evidence tampering** | A ledger entry or source changes after snapshot | Hash chain, source re-hash before domain calls and approval, immutable storage | Whole-ledger replacement. Anchor heads externally (store them separately). |

## 9. Go-live governance checklist

- [ ] Three distinct principals exist (agent, reviewer, operator), and the role assignments match section 3.
- [ ] The approval service stamps an authenticated reviewer identity. Self-approval and non-human live approval are rejected in tests.
- [ ] Immutable controls have no configuration path. Config changes are versioned and deferred to the next episode.
- [ ] The evidence container is append-only with an immutability policy. The certificate head and entry count are stored separately.
- [ ] Memory scopes are named per team and environment. Records are validated on read. Writes need a receipt.
- [ ] Source labels are assigned upstream, and unknown labels fail closed.
- [ ] Telemetry redacts restricted fields before export.
- [ ] The threat-model rows above each have at least one automated test (see [evaluation-and-troubleshooting.md](evaluation-and-troubleshooting.md)).
- [ ] The broader tool-governance controls in [Enterprise Agent Security and Tool Governance](../../../security-and-governance/agent-security-and-governance/README.md) are in place.
