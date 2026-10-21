# Sample: evidence ledger and run certificate

`evidence_ledger.py` is a single-file, standard-library-only implementation of the evidence ledger and run certificate described in [the architecture](../docs/architecture.md). It shows the mechanism, not a product. Every Azure dependency sits behind a `typing.Protocol` seam with an in-memory fake for the self-test and a production adapter stub that raises `NotImplementedError`.

The sample does not call a model, a tool or any network endpoint. A scripted harness run appends the same entries a real harness would: run start, policy decisions, tool calls, retrieved sources, model output, the artifact, the verifier receipt, the approval decision and run close. The certificate is then issued, the run is sealed and the evidence is archived. Time comes from a fake clock, so every run is deterministic.

## Requirements

- Python 3.10 or later.
- No third-party packages, no Azure subscription and no credentials.

## Run it

From the repository root:

```powershell
# Deterministic self-test: 20 scenarios, several of which run against all 4 Domain Packs
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --self-test

# Readable demo run (default Domain Pack: financial-report). Prints the ledger, the certificate and the result
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py

# Same demo for another industry
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --domain prior-auth

# List the Domain Packs and their required checks
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --list-domains

# See a certificate withheld: a failing check, a missing check, no approval, or a denied tool call that ran anyway
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --fail-check recompute.totals
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --domain case-decision --skip-check notice.appeal_deadline
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --domain quality-deviation --no-approval
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --unauthorized-tool-call

# Emit the certificate, the header-only ledger, the offline findings and the done flag as JSON
python assets\orchestration-and-workflows\evidence-ledger-and-run-certificate\samples\evidence_ledger.py --domain prior-auth --json
```

The self-test prints one `PASS <scenario>` line per scenario, then a summary line. It exits with status `0` when every scenario passes and `1` otherwise.

The demo exits with status `0` when the run is done (an issued certificate with no offline findings) and `2` when it is not. `--fail-check` and `--skip-check` accept only a check code from the selected Domain Pack. Any other code is rejected by the argument parser with a usage message.

## Domain Packs

| Key | Organization | Required checks |
|---|---|---|
| `financial-report` | Contoso Bank | `recompute.totals`, `grounding.citations`, `disclosure.distribution_list` |
| `prior-auth` | Fabrikam Health | `eligibility.service_date`, `grounding.criteria_cited`, `phi.minimum_necessary` |
| `quality-deviation` | Northwind Manufacturing | `spec.limits_recomputed`, `traceability.lot_genealogy`, `capa.owner_assigned` |
| `case-decision` | Northwind County | `eligibility.recompute`, `statute.references_valid`, `notice.appeal_deadline` |

The same ledger, issuer and verifier code runs for every pack. Only the sources, the artifact, the checks, the approver and the action that policy denies change.

## What the self-test covers

| Scenario | What it proves |
|---|---|
| `build-chain-and-verify` | For every Domain Pack, a clean run chains, is issued with all required checks passed, verifies offline and is done. The archived header-only ledger also verifies. |
| `detect-tampered-entry` | Editing a readable body gives `ledger.redacted_digest`. Editing a header field gives `ledger.entry_hash`. |
| `detect-tampered-entry-with-rehash` | Recomputing the edited entry's own digests moves the break to the next entry (`ledger.prev_hash`). |
| `detect-full-chain-rewrite-via-certificate` | A full rewrite is internally consistent and passes `verify_chain`. Only the certified head catches it (`certificate.head_mismatch`). |
| `detect-reordered-entry` | Swapping two entries gives `ledger.sequence` and `ledger.prev_hash`. |
| `detect-removed-entry` | Removing an entry breaks the chain, and the certified count catches it (`certificate.entry_count_mismatch`). |
| `detect-truncated-tail-via-certificate` | A truncated prefix still chains. The certificate catches it with `certificate.head_mismatch` and `certificate.entry_count_mismatch`. |
| `withhold-when-required-check-fails` | For every check in every pack, a failure withholds the certificate with exactly that `verification.required_check_failed:<code>`, even though the model said the checks looked fine. The withheld certificate is valid evidence, and the run is not done. |
| `withhold-when-required-check-missing` | A receipt that omits a required check still says `verified`, but the issuer withholds with `verification.required_check_missing:<code>`. Unknown check codes are rejected. |
| `withhold-when-approval-missing` | For every pack, no approval gives exactly `approval.missing` and no approval block. |
| `withhold-when-approval-is-invalid` | A rejection, an approval of another artifact, a self-approval and an approval that is not in the ledger give `approval.rejected`, `approval.artifact_mismatch`, `approval.self_approval` and `approval.not_in_ledger`. |
| `withhold-on-unauthorized-tool-call` | For every pack, a tool call that ran after a `deny` decision gives `policy.unauthorized_tool_call:<seq>`, even though the artifact passes verification. |
| `verify-certificate-signature-offline` | An independent verifier accepts a valid certificate and its archived copies. A wrong key, an untrusted key, an edited field and a downgraded algorithm give `certificate.signature_invalid`, `certificate.untrusted_key` and `certificate.alg_mismatch`. |
| `detect-artifact-digest-mismatch` | Swapped artifact bytes give `certificate.artifact_mismatch` offline. Issuing against them gives `artifact.digest_mismatch`, `verification.stale_artifact` and `approval.artifact_mismatch`. |
| `withheld-certificate-is-not-done` | A withheld certificate verifies but is not done. Promoting it to `issued` breaks the signature. |
| `raw-content-stays-out-of-ledger` | For every pack, the requester's identity, source content, model text and planted markers never appear in the serialized ledger. A body field outside the allowlist gives `ledger.redaction_policy`. |
| `purge-redacted-bodies-keeps-chain-valid` | After `strip_bodies`, the chain and the certificate still verify. Issuing from stripped entries is withheld with `run.not_completed`, so issue before purging. |
| `reject-append-on-stale-head-and-sealed` | A racing append raises `StaleHeadError`, an unknown entry type raises `LedgerError`, and the head does not move. After certification, appends raise `LedgerSealedError` and archive overwrites raise `ImmutableWriteError`. |
| `canonical-json-is-order-independent-and-rejects-floats` | Canonical JSON is sorted, compact and UTF-8. Key order does not change the digest. Floats and NaN are rejected. |
| `azure-adapters-are-stubs` | The Azure adapters satisfy the seams and raise `NotImplementedError`. |

## How to read the code

| Section | What to look for |
|---|---|
| `canonical`, `digest_bytes`, `digest_obj` | Canonical JSON (sorted keys, compact separators, UTF-8, no floats) and `sha256:` digests. Every hash in the sample goes through these. |
| `REDACTION_ALLOWLIST`, `HEADER_FIELDS` | The per-type fields that may stay readable, and the header fields covered by `entry_hash`. |
| `build_entry`, `redact`, `strip_bodies` | How an entry is built: the full payload is hashed, only allowlisted fields are kept, and the readable body sits outside the entry hash. |
| `verify_chain` | Chain verification and its `ledger.*` finding codes. |
| `EvidenceLedger` | The writer the harness uses. It reads the head, builds the next entry and appends with the expected head. |
| `DOMAIN_PACKS`, `build_artifact`, `corrupt_artifact`, `run_check`, `verify_artifact`, `make_approval` | The four industry configurations and the sample verifier and approval that feed the certificate. |
| `_policy_reasons`, `issue_certificate`, `signing_input` | Issuance: every rule is evaluated, every reason is recorded, and the certificate is signed with `alg` and `key_id` inside the signing input. |
| `verify_offline`, `is_done` | Offline verification and its `certificate.*` finding codes, and the single definition of done. |
| `run_scenario` | One scripted harness run: append, issue, seal, archive. |
| `SCENARIOS`, `run_self_test`, `main` | The self-test and the command line. |

## Production seams

| Seam | Fake used by the self-test | Production stub | What a real adapter does |
|---|---|---|---|
| `LedgerStore` | `InMemoryLedgerStore` | `CosmosDbLedgerStore` | Stores entries in Azure Cosmos DB for NoSQL, partitioned by run. Appends the entry and moves the run's head item in one transactional batch with an ETag condition, and raises `StaleHeadError` when the condition fails. Uses Microsoft Entra ID authentication. |
| `EvidenceArchive` | `InMemoryEvidenceArchive` | `BlobEvidenceArchive` | Writes the artifact, the header-only ledger and the certificate to Azure Blob Storage with version-level immutability (WORM), overwrite disabled and a retention policy. Legal hold covers disputes. |
| `Signer` | `InMemoryHmacSigner` (HS256) | `KeyVaultKeySigner` (ES256), `KeyVaultSecretHmacSigner` (HS256) | Signs the certificate with an asymmetric key in Azure Key Vault, so the private key never leaves the vault and verifiers hold only the public key. The HMAC variant is shown for comparison; every verifier would need the secret. |

Each stub's docstring describes the intended shape. Confirm the SDK calls against current documentation before you implement one.

The sample's HMAC key is a fixed, sample-only constant. Never use it, or HMAC with a shared secret, for certificates that third parties must verify.

## Extending the sample

- **Add a Domain Pack.** Add a `DomainPack` entry to `DOMAIN_PACKS` with an industry, organization and title, agent and profile IDs, a requester and a different approver, sources, the artifact's claims, lines, total and fields, `CheckSpec` entries (each of kind `recompute`, `grounding`, `required_field` or `no_marker`), a marker string and the `blocked_action` that policy denies. The scenarios that loop over `DOMAIN_PACKS` pick it up automatically.
- **Add an entry type.** Add it to `REDACTION_ALLOWLIST` with the smallest set of readable fields, append it from the harness, and bump `LEDGER_ENTRY_SCHEMA` if existing verifiers must reject it. Add a scenario that proves no raw content leaks.
- **Add a withheld reason.** Emit it from `issue_certificate`, add a scenario that asserts it, and add a row to [troubleshooting by reason code](../docs/evaluation-and-troubleshooting.md#troubleshooting-by-reason-code).
- **Swap a seam.** Implement `LedgerStore`, `EvidenceArchive` or `Signer` against your service, keep the contract (conditional append, write-once, algorithm pinned per key), and confirm that the self-test still passes with the fakes.
- **Strengthen offline verification.** The sample's `verify_offline` checks the signature, the chain and every binding, but trusts the issuer's withheld reasons. A stricter verifier re-derives the reasons from the ledger and the run's policy. See [extension points](../docs/architecture.md#21-extension-points).
