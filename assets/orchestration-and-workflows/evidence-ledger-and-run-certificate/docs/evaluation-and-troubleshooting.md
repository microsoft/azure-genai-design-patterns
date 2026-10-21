# Evaluation and Troubleshooting

This document covers how to test an evidence ledger and certificate issuer, how to run tamper drills against real storage, what to watch in production and how to diagnose each finding and withheld reason. For the mechanism, see the [architecture](architecture.md).

## What to evaluate

| Property | Question | How to test |
|---|---|---|
| Chain integrity | Is every edit, reorder, removal and re-hash of an entry detected, and located at the right `seq`? | One mutation per field and per position, against `verify_chain`. |
| Head binding | Are a truncated tail and a consistent full rewrite detected? | Truncate and rewrite copies of a certified ledger, then run offline verification against the certificate. |
| Fail-closed issuance | Does every missing or failed condition produce a signed `withheld` certificate with the right reason? | One case per reason code, including combinations. |
| Signature and key handling | Do edits, promotions, a wrong key, an untrusted key and an algorithm downgrade all fail verification? | Mutate copies of certificates; verify with a separate verifier build and key list. |
| Redaction | Does any raw content, model text or requester identity reach the ledger? | Plant unique markers in prompts, sources, tool results and identities, then search the serialized ledger for them. |
| Purge | Does the chain and the certificate still verify after bodies are purged? | Strip bodies from a certified ledger and verify offline. |
| Concurrency and sealing | Are stale-head appends, appends after sealing and archive overwrites rejected without side effects? | Race two writers on one run; append after issuance; overwrite an archived object. |
| Canonicalization | Do two implementations produce the same bytes and digests for the same object? | Cross-language test vectors; key order; Unicode; rejected floats. |
| Coverage | Does the harness record every step it takes? | Harness integration tests that compare executed tool calls with `tool_call_request` entries. |

The first eight properties are deterministic and belong in unit tests. Coverage depends on the harness and belongs in its integration tests.

## Scenarios as evaluation cases

The sample's `--self-test` runs 20 scenarios. Several of them run against all four Domain Packs.

| Scenario | What it proves | Expected codes |
|---|---|---|
| `build-chain-and-verify` | A clean run chains, issues, verifies offline and is done; the archived header-only ledger also verifies. | None |
| `detect-tampered-entry` | A body edit and a header edit are each located. | `ledger.redacted_digest`, `ledger.entry_hash` |
| `detect-tampered-entry-with-rehash` | Re-hashing an edited entry breaks the next link. | `ledger.prev_hash` on the next `seq` |
| `detect-full-chain-rewrite-via-certificate` | A consistent rewrite passes the chain check and fails against the certificate. | `certificate.head_mismatch` |
| `detect-reordered-entry` | Swapped entries are detected. | `ledger.sequence`, `ledger.prev_hash` |
| `detect-removed-entry` | A removed middle entry is detected by the chain and by the certified count. | `ledger.sequence`, `ledger.prev_hash`, `certificate.entry_count_mismatch` |
| `detect-truncated-tail-via-certificate` | A truncated prefix still chains, and fails against the certificate. | `certificate.head_mismatch`, `certificate.entry_count_mismatch` |
| `withhold-when-required-check-fails` | Each required check, when failed, withholds with exactly that check, while the model text still sounds confident. | `verification.required_check_failed:<code>`, `approval.missing` |
| `withhold-when-required-check-missing` | A receipt whose own verdict is `verified` but which lacks a required check is not enough. Unknown check codes are rejected. | `verification.required_check_missing:<code>` |
| `withhold-when-approval-missing` | No approval decision means withheld, with no approval block. | `approval.missing` |
| `withhold-when-approval-is-invalid` | A rejection, an approval of another artifact, a self-approval and a forged decision are all refused. | `approval.rejected`, `approval.artifact_mismatch`, `approval.self_approval`, `approval.not_in_ledger` |
| `withhold-on-unauthorized-tool-call` | A tool call that policy denied but that still ran withholds the certificate, even with a clean artifact. | `policy.unauthorized_tool_call:<seq>` |
| `verify-certificate-signature-offline` | A separate verifier accepts the certificate and archived copies, and rejects a wrong key, an untrusted key, an edit and an algorithm downgrade. | `certificate.signature_invalid`, `certificate.untrusted_key`, `certificate.alg_mismatch` |
| `detect-artifact-digest-mismatch` | Different artifact bytes fail offline, and a reissue with them is withheld. | `certificate.artifact_mismatch`, `artifact.digest_mismatch`, `verification.stale_artifact`, `approval.artifact_mismatch` |
| `withheld-certificate-is-not-done` | A withheld certificate is valid evidence of an unfinished run; promoting it to `issued` breaks the signature. | `certificate.signature_invalid` |
| `raw-content-stays-out-of-ledger` | Markers, model text, the requester identity and source content are absent from the ledger; a smuggled field is caught on read. | `ledger.redaction_policy` |
| `purge-redacted-bodies-keeps-chain-valid` | Purged bodies keep the chain and the certificate valid; issuing after purge is withheld. | `run.not_completed` on reissue |
| `reject-append-on-stale-head-and-sealed` | Stale-head and unknown-type appends change nothing; appends after sealing and archive overwrites are refused. | `StaleHeadError`, `LedgerError`, `LedgerSealedError`, `ImmutableWriteError` |
| `canonical-json-is-order-independent-and-rejects-floats` | Canonical bytes are sorted, compact and UTF-8; floats and NaN are rejected. | `TypeError` for floats |
| `azure-adapters-are-stubs` | The Azure adapters satisfy the seams and raise `NotImplementedError` without network calls. | `NotImplementedError` |

Use these as a template for your own suite:

- **Keep one test per finding and per reason code.** Every code your verifier or issuer can emit needs at least one test that asserts it.
- **Assert the exact list, not just membership, where it matters.** The missing-check and missing-approval scenarios assert that the reason list contains only the expected reason, which catches unrelated regressions.
- **Run each scenario against each Domain Pack or profile.** A new profile, check or entry type is then covered automatically.
- **Add injection cases.** Put text in sources and tool results that asks the agent to skip verification, approve itself or claim the run is done. The expected certificate is the same as without the injected text.
- **Run the suite on every change** to the writer, issuer, verifier, canonical form, allowlist or profiles. See [release gates](#release-gates).

## Tamper drills

Unit tests mutate in-memory lists. Tamper drills prove the same properties against your real store, archive and keys. Run them in a non-production environment, or against copies exported from production.

| Drill | How | Expected result |
|---|---|---|
| Edit a body | Change one allowlisted field in a copy of a ledger. | `ledger.redacted_digest` at that `seq`. |
| Edit and re-hash | Change a body, recompute `redacted_digest` and `entry_hash`. | `ledger.prev_hash` on the next entry. |
| Remove, reorder, truncate | Apply each to a copy of a certified ledger. | Chain findings, and `certificate.head_mismatch` or `entry_count_mismatch` offline. |
| Full rewrite | Change an early entry and re-chain every entry after it. | Chain passes; `certificate.head_mismatch` offline. |
| Overwrite archived evidence | Try to overwrite or delete a certificate in the immutable container with the issuer identity. | The storage service refuses. |
| Append after seal | Try to append to a sealed run with the writer identity. | The store refuses, and the head is unchanged. |
| Write as the wrong identity | Try to append with the issuer identity, and to sign with the writer identity. | Authorization failures. See [separation of duties](authentication-and-governance.md#3-separation-of-duties). |
| Promote a withheld certificate | Set `status` to `issued` and clear the reasons. | `certificate.signature_invalid`. |
| Downgrade or swap the key | Set the algorithm to `none`; replace the key ID with an untrusted one. | `certificate.alg_mismatch`; `certificate.untrusted_key`. |
| Leak content | Plant a marker in a tool result and search the ledger and archive for it. | Not found. |

Run drills on a schedule and after any change to storage configuration, role assignments or keys. Record the results as evidence for your own audits.

## Telemetry and dashboards

Emit content-free signals that share the run's `trace_id`. Telemetry is for dashboards and alerts. The ledger is the evidence.

| Signal | Why |
|---|---|
| Certificates by status, profile and agent version | The share of runs that are done. A drop after a release points to a regression. |
| Withheld reasons by code | Which conditions fail most. Use the code, never the check details or content. |
| Offline findings by code | Should be empty for every archived run. Any finding is an integrity incident, not a quality issue. |
| `policy.unauthorized_tool_call` count | Should be zero. Any occurrence means an enforcement point was bypassed. |
| Stale-head retries and sealed-append rejections | Contention or a second writer on one run. Repeated rejections for one run need investigation. |
| Archive write refusals | Something tried to overwrite sealed evidence. |
| Sign failures and sign calls by identity | Key Vault availability, and any sign call from an identity other than the issuer. |
| Runs closed without a certificate | Issuance backlog or failure. Every closed run should have a certificate, issued or withheld. |
| Purge backlog | Bodies past their retention period that have not been purged. |

Keep run IDs, reason codes and digests in telemetry. Keep prompts, source content, model text and check details out of it. Use [Azure Monitor and OpenTelemetry](https://learn.microsoft.com/azure/azure-monitor/app/opentelemetry-enable) for collection, and the [Hosted Agent OpenTelemetry Trace Receiver](../../../agent-runtime/hosted-agent-otel-trace-receiver/README.md) for hosted-agent traces.

## Troubleshooting by reason code

### Ledger findings

`verify_chain` returns these. Any of them on an archived ledger is an integrity incident.

| Code | Meaning | Likely causes | What to do |
|---|---|---|---|
| `ledger.empty` | No entries | Wrong run ID, wrong partition, or the run never started | Check the run ID and the store. |
| `ledger.schema` | Unknown entry schema | A writer on a newer or older schema | Upgrade the verifier, or fix the writer's schema constant. |
| `ledger.sequence` | `seq` is not one more than the previous entry | Removal, reorder, or a writer that does not use the store's head check | Compare with the archived copy; review store access. |
| `ledger.run_id` | An entry from another run | A store query across partitions, or an injected entry | Fix the query; investigate the injection. |
| `ledger.type` | Unknown entry type | A newer writer, or an injected entry | Add the type and its allowlist on purpose, with a schema bump. |
| `ledger.prev_hash` | Link to the previous entry is broken | Reorder, removal, or an edited and re-hashed entry before this one | Treat as tampering; compare with the archive. |
| `ledger.entry_hash` | Header does not match its hash | Header edited, or the canonical form differs between writer and verifier | Run the canonicalization test vectors on both; then treat as tampering. |
| `ledger.redacted_digest` | Body does not match its digest | Body edited after writing | Treat as tampering. Purge sets bodies to `null`, which never triggers this. |
| `ledger.redaction_policy` | Body has fields outside the allowlist | Writer allowlist out of date, or a field smuggled into a body | Fix the writer; purge the affected bodies. |
| `ledger.timestamp` | Time moves backwards | Clock skew across writer hosts, or reordering | Take time from one clock per run; investigate if the order is also wrong. |
| `ledger.first_entry` | First entry is not `run_started` | Truncated head or a partial export | Re-export the full ledger. |
| `ledger.closed_not_last` | Entries follow `run_closed` | An append after close that bypassed the seal | Check that the store enforces the seal. |

### Certificate findings

`verify_offline` returns these, in addition to the ledger findings above. The signature checks are evaluated in order, so only the first of `untrusted_key`, `alg_mismatch` and `signature_invalid` is reported.

| Code | Meaning | Likely causes | What to do |
|---|---|---|---|
| `certificate.schema` | Unknown certificate schema | Verifier older than the issuer | Upgrade the verifier. |
| `certificate.untrusted_key` | Key ID not in the trusted list | Key rotated and the list not updated, or a forged certificate | Update the published key list; if the key is unknown, treat as forgery. |
| `certificate.alg_mismatch` | Algorithm differs from the one pinned for the key | Downgrade attempt, or a misconfigured list | Treat as forgery unless the list is wrong. |
| `certificate.signature_invalid` | Signature does not match | Any edit to the certificate, including promotion from withheld, or the wrong public key | Verify with the right key version; otherwise treat as tampering. |
| `certificate.head_mismatch` | Ledger head differs from the certified head | Truncation, full rewrite, appends after issuance, or the wrong ledger copy | Compare live and archived copies. |
| `certificate.entry_count_mismatch` | Entry count differs | Removal, truncation or appends after issuance | As above. |
| `certificate.artifact_not_in_ledger` | Certified subject was never ledgered | Issuer bug or forgery | Investigate the issuer. |
| `certificate.artifact_mismatch` | Supplied artifact bytes differ from the subject, or there is no subject | A different version of the artifact was shipped | Find which bytes were released; do not release these. |
| `certificate.receipt_not_in_ledger` | Certified receipt was never ledgered | Issuer bug or forgery | Investigate the issuer. |
| `certificate.receipt_mismatch` | Supplied receipt differs from the certified digest | The wrong receipt was supplied, or it was edited | Fetch the receipt for this run ID. |
| `certificate.approval_not_in_ledger` | Certified approval was never ledgered | Issuer bug or forgery | Investigate the issuer. |
| `certificate.approval_mismatch` | Supplied approval differs from the certified digest | The wrong decision was supplied, or it was edited | Fetch the decision for this run ID. |

### Withheld reasons

`issue_certificate` produces these. A withheld certificate is valid evidence that the run is not done. It is not an integrity problem.

| Reason | Meaning | What to do |
|---|---|---|
| `ledger.chain_invalid` | The live ledger failed `verify_chain` at issuance | Run `verify_chain` to see which finding, then follow the table above. |
| `ledger.not_closed` | The last entry is not `run_closed` | The harness ended without closing the run, or issuance ran too early. |
| `run.not_completed` | `run_closed` does not have status `completed`, or its body was purged | Check the close status. Issue before purging bodies. |
| `artifact.missing` | No artifact bytes or metadata supplied to the issuer | Pass the artifact from its system of record. |
| `artifact.digest_mismatch` | Supplied artifact does not match the last `artifact_produced` entry | The artifact changed after it was recorded. Re-run, do not patch. |
| `verification.no_required_checks` | The profile lists no required checks | Fix the profile. An empty list never certifies. |
| `verification.missing` | No verifier receipt supplied | Check that the verifier ran and its receipt was passed through. |
| `verification.report_not_in_ledger` | The supplied receipt was never recorded | The harness skipped the `verifier_result` entry, or a different receipt was supplied. |
| `verification.stale_artifact` | The receipt names a different artifact digest | The artifact changed after verification. Verify again. |
| `verification.required_check_missing:<code>` | The receipt lacks a required check | Verifier version or profile out of step. Do not trust the receipt's own verdict. |
| `verification.required_check_failed:<code>` | A required check failed | Fix the artifact or its inputs and re-run. In the sample, no approval is requested after a failed verification, so `approval.missing` appears too. |
| `approval.missing` | No approval decision | Route to an approver, or check why none was requested. |
| `approval.not_in_ledger` | The supplied decision was never recorded | A decision from outside the run, or an edited one. |
| `approval.rejected` | The approver rejected | Expected outcome; follow the rejection. |
| `approval.artifact_mismatch` | The approval names a different artifact digest | The approver saw another version. Approve the current digest. |
| `approval.receipt_mismatch` | The approval names a different receipt | The approver saw an older verification. Approve again. |
| `approval.self_approval` | The approver is the initiator or the agent | Route to a different approver; enforce at the approval surface. |
| `policy.unauthorized_tool_call:<seq>` | A tool call without a prior matching `allow` decision | Security incident. See [kill switch and incident response](authentication-and-governance.md#7-kill-switch-and-incident-response). |
| `policy.profile_mismatch` | Entries record a profile version other than the run's | The profile changed mid-run, or the harness reported the wrong version. |

### Write-path errors

| Error | Meaning | What to do |
|---|---|---|
| `StaleHeadError` | Another append moved the head | Re-read the head and retry. Persistent conflicts mean two writers for one run. |
| `LedgerSealedError` | The run is sealed | Nothing more can be recorded. Start a new run. |
| `LedgerError` | Unknown entry type, or an entry whose run ID, `prev_hash` or `seq` does not match the store | Fix the caller. |
| `ImmutableWriteError` | The archive refused to overwrite | Expected for sealed evidence. Never retry with a delete. |

## Debugging a single run

1. **Start from the run ID or trace ID.** Find the certificate in the archive. If there is none, the run was never certified; check whether it closed.
2. **Verify offline first.** Run the verifier with the trusted key list, the header-only ledger, and the artifact, receipt and approval if you have them. Findings mean an integrity problem; go to the incident process.
3. **If there are no findings, read the status.** `issued` with no reasons means done. `withheld` means read `withheld_reasons` and follow the table above.
4. **Read the ledger headers in order.** `seq`, `type`, `actor` and `at` tell the story of the run without any content. Bodies, if not yet purged, add the allowlisted details.
5. **Fetch content only when you need it,** from its system of record through `payload_ref`, and compare its digest with `payload_digest`.
6. **Use traces for timing and errors.** Search traces and logs by `trace_id`. They explain what happened operationally; the ledger shows what is proven.
7. **Never fix a run by editing it.** A sealed run stays as it is. Fix the cause and start a new run, which gets a new run ID and a new certificate.

To see the shape of the data, run the sample with JSON output. It prints the certificate, the ledger with bodies stripped, the offline findings and the `done` flag:

```powershell
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --domain case-decision --no-approval --json
```

## Release gates

Changes to the evidence path need stricter gates than ordinary features, because a bug can make false evidence look valid.

| Gate | Passes when |
|---|---|
| Self-test and unit suite | Every scenario passes, with one test per finding and per withheld reason. |
| Canonicalization vectors | Writer, issuer and every verifier implementation produce identical bytes and digests for the shared vectors. |
| Tamper drills | Every drill in this document produces its expected result in a non-production environment. |
| Backward verification | Certificates and ledgers archived under earlier versions still verify with the new verifier and the published keys. |
| Redaction review | Data protection has reviewed any allowlist change, and the content-leak test passes with new markers. |
| Schema and version bumps | Any change to headers, certificate fields or the canonical form bumps the schema; profile and verifier versions are updated. |
| Separation of duties | Role assignments match the [identity inventory](authentication-and-governance.md#1-identity-inventory). |
| Rollback plan | The previous writer and issuer can be restored, and runs issued in between still verify. |

Then follow the [Agent Production Readiness Guide](../../../agentops-and-readiness/agent-production-readiness/README.md).
