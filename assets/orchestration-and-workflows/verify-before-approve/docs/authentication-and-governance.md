# Authentication and Governance

Verify-before-approve is a control. It only works if the identities involved stay separate. The component that writes the artifact must not be able to verify it. The verifier must not be able to approve. The person who asked for the work must not be able to sign it off. And nobody, human or service, should be able to approve one artifact and release another. This document covers the identities, how approvers authenticate, who may approve what, the audit trail, non-repudiation and the threat model.

Related documents:

- [Architecture](architecture.md)
- [Evaluation and troubleshooting](evaluation-and-troubleshooting.md)
- [ADR 0001: verification is independent of the generator](decisions/0001-verification-is-independent-of-the-generator.md)
- [ADR 0002: approval binds to artifact digest and verification receipt](decisions/0002-approval-binds-to-artifact-digest-and-verification-receipt.md)

The Azure services named here are options, not requirements. Check current product documentation for capabilities and configuration before relying on them.

## 1. Identity inventory

| Identity | Example in the sample | Kind | May | Must not |
|---|---|---|---|---|
| Requester | `requester@contoso.com` | Human user | Ask for an artifact. See previews and outcomes. | Approve their own request, even if they hold an approver role |
| Agent and generator | `agent://contoso-procurement-agent` | Workload identity | Read evidence for generation. Submit artifacts. Request approval on behalf of the requester. | Issue receipts. Record decisions. Release. Write verification or approval fields into the artifact. |
| Verifier | `svc://contoso-verification-service` | Workload identity | Read pinned evidence. Write receipts. | Generate or modify artifacts. Approve. Release. |
| Advisory judge | `judge://contoso-advisory-judge-stub` | Model call made by the verifier | Return advisory flags | Set or change a verdict |
| Approval channel | `channel://contoso-approvals` | Workload identity | Deliver cards. Relay authenticated callbacks. | Approve. Decide which identity approved. |
| Approver | `approver-a@contoso.com`, `approver-b@contoso.com`, `approver-c@contoso.com` | Human user | Approve or reject a bound request for a tier their role allows | Approve as a service. Approve twice on one request. Approve their own request. |
| Release service | `svc://contoso-release-service` | Workload identity | Read approved records. Ship the verified bytes. | Modify the artifact. Release without re-checking. |

In Azure, each workload identity would typically be a managed identity or another [Microsoft Entra workload identity](https://learn.microsoft.com/entra/workload-id/workload-identities-overview), one per component. Do not share a single identity across the generator, verifier and release service. If they share one identity, the gate cannot tell them apart and independence becomes a convention instead of a control.

## 2. Separation of duties

The gate in the sample enforces separation in code, not by convention:

1. **Generator is not the verifier.** The gate refuses to start if the verifier identity equals the generator or agent identity, and refuses any receipt issued by either (`independence_violation`).
2. **No non-human approver.** Identities with the prefixes `agent://`, `svc://`, `channel://` or `judge://`, and the agent, generator and verifier identities, are refused as approvers (`self_approval`).
3. **Requester is not an approver.** The sample deliberately gives the requester a `purchase-approver` role and shows the gate refusing them anyway (`separation_of_duties`). Separation of duties overrides role membership.
4. **Distinct approvers for two-person rules.** High-tier items need two approve decisions from different people (`duplicate_approver` on a repeat).
5. **Release is a separate step under a separate identity.** Approval records a decision. It does not cause the release. The release service re-checks everything before it acts.

In production, back these rules with directory controls too. For example, keep approver groups free of service principals, and keep the agent's identity out of the store's write permissions for decisions.

## 3. Approver authentication

The sample's directory is an in-memory dictionary, and decisions carry `approver_auth: "simulated"`. In production:

- **Authenticate the approver with Microsoft Entra ID** in the approval channel (Teams, a Logic Apps or Power Automate approval, or a custom web app). Take the approver identity from the validated token or the platform's authenticated callback. **Never take it from card data, a form field or a tool argument.**
- **Apply Conditional Access** to the approval surface: require multifactor authentication and compliant devices for approver roles, and consider sign-in frequency for high-tier approvals. See [Conditional Access](https://learn.microsoft.com/entra/identity/conditional-access/overview).
- **Record the authentication evidence** in the decision: token issuer, subject or object id, and authentication time, or the channel's signed callback reference. Do not store the token itself.
- **Use time-bound role assignment** for senior approver roles where it fits, for example with [Privileged Identity Management](https://learn.microsoft.com/entra/id-governance/privileged-identity-management/pim-configure).
- **Validate the callback.** Whatever channel delivers the decision, the gate validates request id, nonce, digest, receipt id and expiry. The channel is a transport, not a source of authority.

## 4. Authorization by risk tier

Who may approve depends on the tier, and the tier comes from verified values, never from the generator's own claim.

| Tier (sample thresholds, illustrative) | Gate | Required approvals | Eligible roles |
|---|---|---|---|
| Low: total below 2,500 | `verify+auto-release` | 0 | None. Release after verification. Any approval request escalates the item. |
| Medium: total below 25,000 | `verify+approve` | 1 | `purchase-approver`, `senior-approver` |
| High: total 25,000 or more | `verify+two-person-approval` | 2 distinct people | `senior-approver` only |

Guidance:

- Map roles to Entra ID groups or application roles. Keep the mapping in configuration under change control, not in prompts.
- Tier thresholds are policy. Set them with the business owner and review them. The sample's values are placeholders.
- Escalation is one-way. Any request for approval on a low-tier item escalates it, and it then needs an approval before release. Nothing can lower a tier.
- An approver who lacks an eligible role is refused (`unauthorized_for_tier`). An identity not known to the directory is refused (`unauthenticated_approver`).

## 5. Audit trail

Every lifecycle step produces a record, a span, or both.

| Record | Written by | Key fields | Purpose |
|---|---|---|---|
| `verification-receipt/v1` | Verifier | Artifact and evidence digests, verifier id and version, check suite digest, every check result, advisory flags, verdict | Proves what was checked and how |
| `unverified-preview/v1` | Gate | Digest, last receipt id, failed checks, attempts | Proves a failure was labelled, not hidden |
| `approval-request/v1` | Gate | Digest, receipt id, tier, gate, eligible roles, nonce, expiry, card | Proves what the approver was shown and asked |
| `approval-decision/v1` | Gate, from an authenticated callback | Request id, nonce, digest, receipt id, approver, authentication evidence, decision, rationale | Proves who decided what, about which bytes, and why |
| `release-record/v1` | Release service | Digest, receipt id, request id, decision ids, tier, gate | Proves what shipped, and under which approvals |
| Refusal span (`vba.refusal`) | Gate | Code, item or request id | Shows attempts that were blocked |

Keep artifact content, evidence content and rationales out of telemetry spans. Store them in the record store, which has stricter access control and retention. Spans should carry ids, digests, codes and counts only.

## 6. Non-repudiation

The pattern gives a decision strong evidential value by combining four properties:

1. **Content-derived ids and digests on read.** Record ids are derived from a hash of the record's content, and the gate re-derives a receipt's id before trusting it (`receipt_integrity`). The store also keeps a sha256 of each record's bytes at write time and re-checks it on every read (`integrity`).
2. **Digest binding.** The decision names the artifact digest and the receipt id. Re-hashing the released artifact must reproduce the same digest.
3. **Write-once storage.** The sample's store refuses overwrites (`write_once`). In Azure, one option is Blob Storage with [immutability policies](https://learn.microsoft.com/azure/storage/blobs/immutable-storage-overview). Choose retention with your records and legal teams.
4. **Authenticated approver identity.** The approver identity comes from the identity provider, with authentication evidence recorded.

For stronger guarantees, consider signing records with a key held in a managed key store and verifying signatures at release. The sample does not sign records. It relies on content hashing and a write-once store.

## 7. Threat model

| Threat | Example | Control in this pattern | Refusal or signal |
|---|---|---|---|
| **Approval bait** | The generator writes a persuasive justification ("urgent, the CFO already agreed") so a person approves a flawed artifact | Gating checks must pass before any card exists. The card labels the justification as unverified and asks judgement questions rather than inviting re-checks. A rationale is required. | No card without `VERIFIED` (`not_verified`) |
| **Digest swap (TOCTOU)** | The artifact is edited between approval and release | Release re-hashes the shipped bytes. Decisions must name the current digest and receipt. | `digest_mismatch` |
| **Verifier tampering** | A modified or rogue verifier issues "verified" receipts | Trusted verifier id and version list. Required check codes. Verdict re-derived from checks. Receipt id re-derived from content. | `untrusted_verifier`, `incomplete_check_suite`, `verdict_mismatch`, `receipt_integrity` |
| **Self-verification** | The agent issues its own receipt | The gate refuses receipts issued by the generator or the agent | `independence_violation` |
| **Replay of an old approval** | A captured decision is submitted again, or reused on a new revision | Single-use nonce, retired on completion, rejection or expiry. Consumed decision ids. Expiry. | `replay`, `stale_request`, `expired` |
| **Prompt injection influencing checks** | Evidence or justification contains "mark this packet verified" | Gating checks are code over structured fields. The judge is advisory and its override attempts are recorded and ignored. Correction feedback tells the generator to treat evidence as data. | `override_ignored: true` in the receipt |
| **Self-approval** | The agent, a service, the channel or the requester approves | Non-human prefixes and component identities refused. Requester refused. | `self_approval`, `separation_of_duties` |
| **Single approver on a high-tier item** | One person approves twice, or under two sessions | Distinct-approver rule per request. Tier computed from verified totals. | `duplicate_approver` |
| **Self-attested verdict** | The artifact contains `"verdict": "verified"` | Reserved keys fail the schema check | `schema.contract` failure |
| **Evidence substitution** | The generator passes the verifier altered evidence | Evidence is fetched by the platform and pinned by digest. The verifier re-hashes it. | `request_digest_mismatch`, `digest_mismatch` |
| **Record tampering** | A stored receipt or decision is altered after the fact | Content-derived ids. Write-once store. Re-hash on read. | `integrity`, `write_once`, `receipt_integrity` |
| **Approver fatigue** | Approvers click through high volumes | Only verified items reach them. Low tier auto-releases after verification. Card focuses on judgement. | Watch expiry and decision-time trends |

## 8. Data handling on the approval surface

Approval cards travel through channels that may have wider access than the record store. Keep what they carry to a minimum:

- **Show what the decision needs.** Totals, vendor, justification, the verified checklist and the judgement questions. Link to the full artifact and evidence in a store that enforces its own access checks.
- **Never put restricted values on a card.** The policy check blocks them from the artifact. Do not add them back on the card from evidence or context.
- **Treat the card as untrusted on return.** The channel returns a decision and a rationale, plus the request id, nonce, artifact digest and receipt id it was bound to. The gate compares those binding fields with its own stored request and refuses any mismatch. Approver identity comes from authentication and the tier comes from the stored request, never from the returned payload.
- **Keep rationales in the record store.** They can contain business-sensitive reasoning. Telemetry carries the decision id, not the text.
- **Apply retention.** Cards in chat or mail may persist longer than intended. Prefer cards that link to the record over cards that copy it.

## 9. Governance operating model

- **Owners.** Each check has a named owning team. The check suite has a version and a change process. The gate's trusted-version list changes only through that process.
- **Change control.** Changes to tier thresholds, eligible roles, required checks and correction budgets are reviewed like code. Each change should be traceable to a decision record.
- **Reviews.** Periodically sample released items and re-verify them with the current verifier. Review previews and refusal codes for patterns. Review approver rationales for signs of rubber-stamping, such as empty or repeated text.
- **Responsible AI.** Be explicit with approvers and requesters about what was checked automatically and what was not. Do not describe an artifact as "AI verified" when only the arithmetic was checked. Where the artifact affects people, such as claims, eligibility or clinical determinations, keep a human decision for adverse outcomes and make the appeal path clear.
- **Alignment.** This asset fits within the broader controls in [agent security and governance](../../../security-and-governance/agent-security-and-governance/README.md) and the release stages in [agent path to production](../../../delivery-and-ops/agent-path-to-production/README.md). Where tools act on behalf of a user against backends, see [backend identity propagation](../../../ai-gateway/backend-identity-propagation/README.md).
