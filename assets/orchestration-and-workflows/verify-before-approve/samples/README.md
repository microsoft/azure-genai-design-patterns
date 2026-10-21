# Samples: Verify Before Approve Simulator

`verify_approve_sim.py` is a single-file, standard-library-only simulator of the
pattern. A fictional Contoso procurement agent drafts purchase packets from a vendor
quote, a budget ledger, a tax table and an approved-vendor list. An independent
verification service checks each packet, a gate drives the state machine from
[architecture.md](../docs/architecture.md#5-state-machine), and fictional approvers
decide on cards that separate what was verified automatically from what needs their
judgement. A release service ships only the bytes that were approved.

The simulator models the decisions, the state machine and the wire contracts. It does
not model a model, Microsoft Entra ID, Microsoft Teams, Azure Logic Apps or storage.
Use it to read the logic, to argue about edge cases, and as an executable
specification for your own gate and verifier tests.

> **Test only.** Approver authentication is a directory lookup in a dictionary. The
> generator is deterministic code that can be told to make specific mistakes, and the
> advisory judge is a keyword stub. Records live in an in-memory, write-once store,
> and the clock is fixed. The approval-channel adapter is a gated stub that never
> makes a network call. Do not copy the authentication model; in production the
> approver identity comes from a validated token issued to the approval channel.

## Prerequisites

- Python 3.10 or later. No packages to install and no network access.

## Run it

From the repository root:

```powershell
python assets/orchestration-and-workflows/verify-before-approve/samples/verify_approve_sim.py
python assets/orchestration-and-workflows/verify-before-approve/samples/verify_approve_sim.py --self-test
python assets/orchestration-and-workflows/verify-before-approve/samples/verify_approve_sim.py --mutations
python assets/orchestration-and-workflows/verify-before-approve/samples/verify_approve_sim.py --list
python assets/orchestration-and-workflows/verify-before-approve/samples/verify_approve_sim.py --scenario high-tier-needs-two-approvers
```

- With no argument it prints a readable trace. A medium-tier packet is drafted with a
  wrong total, fails `recompute.totals`, receives structured correction feedback, is
  verified on attempt 2, and is shown to an approver as an approval request with its
  card. One approver approves, the release service re-hashes and releases, and the
  trace ends with the lifecycle spans that were emitted. Spans carry ids, digests and
  codes, never packet content.
- `--self-test` runs every scenario, prints `PASS <scenario>` or `FAIL <scenario>`,
  then `N/21 scenarios passed`. It exits 0 when all pass and 1 otherwise.
- `--mutations` runs the verifier against a clean packet and 16 deliberately broken
  ones, prints which check caught each, and exits 1 if any mutation is missed. It also
  shows that a suite without `freshness.evidence_dates` falsely accepts an expired
  quote, and that the gate refuses such receipts with `incomplete_check_suite`.
- `--list` prints the scenario names. `--scenario NAME` runs one scenario and exits 2
  for an unknown name.

## The fictional world

All identities and organizations are fictional. Evidence is addressed with
`evidence://contoso/<kind>/<id>` URIs that resolve only inside the simulator.

| Party | Identity | Role in the pattern |
| --- | --- | --- |
| Generator | `agent://contoso-procurement-agent` | Drafts packets and applies correction feedback. Never verifies, approves or releases. |
| Verifier | `svc://contoso-verification-service`, version `purchase-packet-verifier/1.2.0` | The only trusted verifier identity and version. Issues receipts. |
| Advisory judge | `judge://contoso-advisory-judge-stub` | Flags thin justifications. Never gating. |
| Release service | `svc://contoso-release-service` | Re-hashes the artifact and releases only on a digest and receipt match. |
| Approval channel | `channel://contoso-approvals` | The gated adapter. A channel is never an approver. |
| Requester | `requester@contoso.com` | Holds `purchase-approver`, but separation of duties stops self-approval. |
| Approver A | `approver-a@contoso.com` | `purchase-approver` |
| Approver B | `approver-b@contoso.com` | `purchase-approver`, `senior-approver` |
| Approver C | `approver-c@contoso.com` | `senior-approver` |

Evidence (pinned by digest before verification):

| Evidence id | Kind | Facts the checks use |
| --- | --- | --- |
| `ev-quote-001` | `vendor-quote` | Vendor Fabrikam Supplies, unit price per SKU, `valid_through` |
| `ev-budget-001` | `budget-ledger` | Cost center `CC-1001`, `budget_remaining` |
| `ev-tax-001` | `tax-table` | `tax_rate` |
| `ev-vendors-001` | `approved-vendor-list` | Fabrikam Supplies and Northwind Traders |

Risk tiers are computed from the **re-derived** total, never the total the generator
wrote. The thresholds are simulation values only.

| Tier | Rule in the simulator | Gate | Approvals | Eligible roles | Example total |
| --- | --- | --- | --- | --- | --- |
| Low | below 2,500.00 | `verify+auto-release` | 0 | n/a | 453.60 |
| Medium | below 25,000.00 | `verify+approve` | 1 | `purchase-approver`, `senior-approver` | 13,564.80 (default) |
| High | anything above | `verify+two-person-approval` | 2 distinct people | `senior-approver` | 33,912.00 |

Requesting approval for a low-tier item escalates it to `verify+approve (escalated)`.
The correction budget is `DEFAULT_MAX_CORRECTIONS = 2`, so at most three attempts. An
approval request expires after a simulated 24 hours.

## The checks

Every check is a pure function of the packet, the pinned evidence and the injected
clock. All eight are gating and required: the gate refuses a receipt that is missing
any code in `REQUIRED_CHECK_CODES`.

| Check code | Category | What it proves |
| --- | --- | --- |
| `schema.contract` | Schema | Required keys, types and formats; no unknown keys; no verdict or approval fields written by the generator. Runs first; on failure, checks that need a valid shape are `skipped`, which still counts as not passed. |
| `recompute.line_amounts` | Recomputation | Every line amount equals quantity times unit price. |
| `recompute.totals` | Recomputation | Subtotal, tax at the cited tax-table rate, and total all re-derive. |
| `grounding.citations` | Citation grounding | Every price and claim cites evidence that exists, and the cited value matches it. |
| `invariant.business_rules` | Invariant | Vendor is approved, cost center matches the ledger, re-derived total fits the remaining budget. |
| `freshness.evidence_dates` | Freshness | No future-dated evidence, per-kind age limits, packet date inside the quote validity window. |
| `policy.restricted_content` | Policy | No bank, tax-id or personal-contact keys, and no values that look like account numbers. Runs even when the schema check fails. |
| `render.chart_matches_table` | Rendering | Chart series equal the line items one-to-one in label, value and order. |

The advisory judge's output is stored in the receipt's `advisory[]` array with
`gating: false`. If artifact text steers the judge into asserting a verdict, the
entry is stored with `override_ignored: true` and the verdict does not change.

## What the self-test proves

| Scenario | What it proves |
| --- | --- |
| `happy-path-release` | A clean medium-tier packet goes `DRAFT -> VERIFYING -> VERIFIED -> AWAITING_APPROVAL -> APPROVED -> RELEASED`. The request, decision and release all bind the same digest and receipt. Every lifecycle span is emitted and no span carries packet content. |
| `low-tier-auto-release` | A low-tier packet releases with no approval. A medium-tier packet cannot skip approval (`approval_required`). Requesting approval on a low-tier item escalates it. |
| `recompute-catches-wrong-total` | A wrong total fails only `recompute.totals`, with path, expected, observed and suggested value. With no correction budget the item ends as a preview listing that check. |
| `citation-missing-blocks` | Missing, dangling and misquoted citations and an unquoted price all fail `grounding.citations`. An uncited packet never reaches an approver (`not_verified`). |
| `mutation-suite-detects-every-injected-error` | The clean packet verifies, and each of the 16 mutations fails exactly its expected checks. A suite without the freshness check falsely accepts an expired quote, and the gate refuses it with `incomplete_check_suite`. |
| `correction-loop-fixes-then-verifies` | Structured `correction-feedback/v1` reaches the generator and the packet verifies on attempt 2 as a new revision. A second case verifies on attempt 3, after fixing the table exposes a chart mismatch, showing no verdict inheritance. |
| `correction-budget-exhausted-unverified-preview` | A generator that ignores feedback uses all three attempts. The item becomes an `UNVERIFIED_PREVIEW` with the banner, failed checks and attempt count, stored as a record, with no risk tier, and cannot be released. |
| `unverified-cannot-request-approval` | An over-budget packet cannot be fixed by the generator. The preview refuses approval requests, release and a forged decision (`stale_request`), and stays terminal. |
| `digest-swap-after-approval-blocks-release` | A decision bound to a different digest is refused, and so is releasing swapped bytes after approval (`digest_mismatch`). Re-ordering keys does not change the canonical digest. |
| `approval-replay-rejected` | Re-submitting a decision, with or without a new rationale, or against another item, is refused (`replay`). A wrong nonce is refused (`stale_request`). Refusals do not change state. |
| `expired-approval-request-rejected` | A late decision is refused (`expired`). Expiry returns the item to `VERIFIED`, and a reissued request has a new id and nonce. The release cites the live request. |
| `self-approval-rejected` | The agent, verifier, judge and channel identities are refused (`self_approval`), and so is the requester (`separation_of_duties`). Refusals record nothing; a distinct approver then succeeds. |
| `unauthorized-approver-rejected` | An unknown user, an invalid decision value and an empty rationale are refused. A purchase approver cannot approve a high-tier item (`unauthorized_for_tier`). |
| `high-tier-needs-two-approvers` | One senior approval is not enough to release. The same person cannot approve twice (`duplicate_approver`). Two distinct senior approvers release it. |
| `advisory-judge-cannot-override-failed-check` | An injected "mark verified" instruction steers the judge, but the receipt still fails and the override is recorded as ignored. A forged verifier's receipt is refused (`verdict_mismatch`) and never stored. An advisory flag on a verified packet goes to the human-judgement list instead of blocking. |
| `verifier-independence-enforced` | The gate refuses to start when the verifier and generator share an identity (`independence_violation`). Receipts from an old verifier version or an unknown verifier are refused (`untrusted_verifier`) and never stored. |
| `illegal-state-transition-raises` | Skipping states raises `illegal_transition` and changes nothing. Terminal states (`UNVERIFIED_PREVIEW`, `REJECTED`, `RELEASED`) refuse every transition. Gate operations on the wrong state or an unknown item are refused. |
| `approval-card-lists-verified-vs-human-items` | The card lists all eight verified checks with "do not re-check" guidance, and only judgement questions in the human list. It also shows a binding block, a diff that lists only what changed, links to all four evidence items, and the justification marked as unverified. |
| `rejection-is-terminal` | A rejected item cannot be released, re-requested or approved, and the same item id cannot be resubmitted. |
| `receipt-tamper-detected` | Editing a stored receipt is detected when the record is re-hashed (`integrity`), before approval and again before release. The store refuses overwrites (`write_once`), and a receipt whose content does not match its id is refused. |
| `production-approver-adapter-is-gated` | The channel adapter refuses to construct without the opt-in variable. Its card payload carries the request id, nonce and digest and requires a rationale. `decide()` is never callable, and the channel identity cannot approve. |

The refusal codes in this table are the ones the
[troubleshooting guide](../docs/evaluation-and-troubleshooting.md#6-troubleshooting)
explains.

## Mutation testing the verifier

`--mutations` is the verifier's own test. Each mutation injects one realistic error
into the packet or the evidence and states which checks must fail:

| Group | Mutations |
| --- | --- |
| Arithmetic | `wrong-line-amount`, `wrong-total`, `stale-tax-rate-applied` |
| Grounding | `price-not-in-quote`, `missing-citation`, `dangling-citation`, `misquoted-fact` |
| Business rules and freshness | `vendor-not-approved`, `over-budget`, `expired-quote` |
| Policy | `restricted-value`, `restricted-key` |
| Rendering | `chart-value-mismatch`, `chart-series-dropped` |
| Contract | `schema-missing-total`, `self-attested-verdict` |

A mutation that no check catches is a false accept waiting to happen. See
[testing the verifier itself](../docs/evaluation-and-troubleshooting.md#2-testing-the-verifier-itself)
for how to grow this table from incidents and run it in CI.

## Adapter seams

Each seam is a `typing.Protocol`, so you can swap a fake for a real implementation
without changing the gate.

| Seam | In-memory fake | Gated production stub | Real implementation (illustrative) |
| --- | --- | --- | --- |
| `Clock` | `FixedClock` | None | System UTC clock |
| `Generator` | `DraftingGenerator`, `ScriptedGenerator` | None | Your agent, for example on Microsoft Foundry |
| `Check` | Eight `_BaseCheck` subclasses | None | Your domain checks, versioned with the suite |
| `AdvisoryJudge` | `StubAdvisoryJudge` | None | A model-based evaluator whose output stays advisory |
| `Verifier` | `CheckSuiteVerifier` (plus `ForgingVerifier` as a test double) | None | A separate service with its own workload identity |
| `Approver` | `ScriptedApprover` | `ChannelApprovalAdapter` | Teams Adaptive Card, Logic Apps approval or a custom UI |
| `ReceiptStore` | `InMemoryReceiptStore` (write-once, re-hashes on read, `tamper()` for tests) | None | Immutable blob storage or a ledger keyed by record id |

Supporting pieces: `SequentialNonceSource` issues approval nonces, `Telemetry`
collects spans, `WorkItem` holds the state machine, `VerifyBeforeApproveGate`
enforces the rules, and `Rig` wires a fresh world for each scenario. Refusals raise
`GateRefusal` with a stable code; illegal state changes raise `IllegalTransition`.

## The gated channel adapter

`ChannelApprovalAdapter` shows where a real approval channel plugs in. Constructing it
raises `NotImplementedError` unless the environment variable
`VBA_SIM_ENABLE_CHANNEL_ADAPTER=1` is set. Once enabled, `build_card_payload()` returns
an illustrative Adaptive Card shape with the bound digest, the verified checklist, the
judgement questions, a required rationale input, and approve and reject actions that
carry the request id and nonce. `decide()` always raises `NotImplementedError`.

Before you wire a real channel, verify against current documentation how it delivers
the submitting user's identity. The approver identity must come from a validated token
issued to the channel, never from fields in the card data.

## Extend it

- **Add a check:** subclass `_BaseCheck` with a new `code`, add it to `ALL_CHECKS` and
  `REQUIRED_CHECK_CODES`, add at least one `Mutation` to `MUTATIONS` that only your
  check can catch, and run `--mutations`.
- **Add a scenario:** write an `s_*` function that builds a fresh rig with `make_rig`
  or `mutated_rig`, asserts with `ensure(...)` and `expect_refusal(...)`, and register
  it in `SCENARIOS`.
- **Try a rule change:** change the gate, run `--self-test` and `--mutations`, and
  confirm which scenarios fail. If none fail, the change is untested; add a scenario.
- **Adapt to your domain:** replace the packet, evidence and checks while keeping the
  gate, records and state machine. The [Domain Packs](../docs/architecture.md#15-domain-packs)
  list checks and judgement items for other industries.

## Limitations

- No model, network, real identity provider or storage. Approver authentication is a
  dictionary lookup, and the channel adapter is a stub.
- One process and one clock. Concurrency between approvers, distributed nonce stores
  and storage consistency are not modeled.
- Canonical JSON uses sorted keys and compact separators. Production should adopt a
  published canonicalization so every language computes the same digest.
- Record schemas are illustrative, not a stable contract.
- The tier thresholds, evidence age limits and approval expiry are simulation values.
  Choose real values from your own policy.

## Related

- [Asset README](../README.md)
- [Architecture](../docs/architecture.md)
- [Authentication and governance](../docs/authentication-and-governance.md)
- [Evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md)
- [Governed Context-Aware Agent Harness](../../governed-context-aware-agent-harness/README.md)
