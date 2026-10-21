# Authentication and Governance

The evidence ledger is only as trustworthy as the identities that can write it, sign it and purge it. This document covers who holds which identity, which roles they need, how keys are managed, and how retention and erasure work without breaking the evidence. Read it with the [architecture](architecture.md), especially the [threat model](architecture.md#9-threat-model).

Role names and API shapes below reflect Azure documentation at the time of writing. Confirm them against current docs before you assign anything.

## 1. Identity inventory

| Identity | Kind | What it does | What it must not be able to do |
|---|---|---|---|
| Harness writer | Managed identity of the harness host | Appends entries to the live ledger store for the runs it executes. | Sign certificates, change immutability policies, purge bodies, or write to the archive. |
| Certificate issuer | Separate managed identity (or a separate process with its own identity) | Reads the ledger, calls the signing operation, seals the run and writes to the archive. | Append ordinary entries, manage keys, or delete archived evidence. |
| Signing key | Key Vault or Managed HSM key, versioned | Signs the certificate's signing input. | Leave the vault. Be used by any identity other than the issuer. |
| Offline verifier and release gate | Pipeline or service identity, or an auditor workstation | Reads archived evidence and public keys; runs verification and `is_done`. | Write anywhere in the evidence path. Sign anything. |
| Approver | Person, signed in with Microsoft Entra ID | Approves or rejects one artifact digest with one receipt ID. | Approve a run they initiated. Edit the ledger. |
| Auditor | Person or audit tool | Reads archived certificates and header-only ledgers. | Write, purge or sign. |
| Data protection operator | Person, with an app role and just-in-time elevation | Purges redacted bodies and referenced content after issuance, within policy. | Edit headers, change certificates, or override a legal hold. |
| Key administrator | Person, with just-in-time elevation | Creates, rotates and disables signing key versions. | Use the key to sign. |
| Model and tools | None in the evidence path | Nothing. They never receive a ledger, store, archive or signer handle. | Anything listed above. |

The rule that matters most: the model has no identity in the evidence path, and the harness writer cannot sign. If one identity can both write the ledger and sign certificates, a compromise of that identity can produce a consistent, signed, false record.

## 2. App roles and data-plane RBAC

| Resource | Identity | Role (confirm against current docs) | Scope |
|---|---|---|---|
| Azure Cosmos DB for NoSQL (live ledger) | Harness writer | Cosmos DB Built-in Data Contributor | The ledger container only. |
| Azure Cosmos DB for NoSQL | Certificate issuer | A custom data-plane role that can read items and replace the head item to set `sealed` | The ledger container only. |
| Azure Cosmos DB for NoSQL | Investigations | Cosmos DB Built-in Data Reader | The ledger container, time-bound. |
| Azure Key Vault (signing key) | Certificate issuer | Key Vault Crypto User, or a narrower custom role with only the sign operation | The single key, not the vault. |
| Azure Key Vault | Key administrator | Key Vault Crypto Officer | The vault, through just-in-time elevation. |
| Azure Blob Storage (archive) | Certificate issuer | Storage Blob Data Contributor | The archive container only. Immutability blocks overwrite and delete. |
| Azure Blob Storage | Verifier, auditor | Storage Blob Data Reader | The archive container only. |
| Azure Blob Storage | Storage administrator | A control-plane role that can manage immutability policies and legal holds | The storage account, through just-in-time elevation. |
| Microsoft Foundry project | Harness host | Foundry User (formerly Azure AI User) | The project. Grants model and agent access, not evidence access. |

Disable key-based (shared key) authentication on the Cosmos DB account and the storage account so that the roles above are the only way in. The built-in Cosmos DB data roles are data-plane role definitions assigned with the Cosmos DB tooling, not standard Azure role assignments; check the current procedure.

Illustrative Microsoft Entra app roles on the approval and evidence applications:

| App role | Who | Allows |
|---|---|---|
| `Evidence.Approver` | Reviewers per profile | Submitting an approval decision for runs they did not initiate. |
| `Evidence.Auditor` | Audit and compliance | Reading certificates and header-only ledgers through the evidence API. |
| `Evidence.Purge` | Data protection operators | Requesting a purge for a run, subject to legal hold checks. |

Take the approver and initiator identities from validated tokens on the server, never from prompt content or request bodies. See [request context is not authorization](../../../agent-runtime/request-context-is-not-authorization/README.md).

## 3. Separation of duties

- **Writer and issuer are different identities.** The writer records steps; the issuer evaluates and signs.
- **Initiator and approver are different people.** The issuer withholds with `approval.self_approval` when the approver is the initiator or the agent. Enforce it at the approval surface too, so the case is rejected before it is recorded.
- **Key administrators do not sign.** Key Vault Crypto Officer manages keys; only the issuer holds the sign permission.
- **Purge operators cannot touch headers or certificates.** Purge sets bodies to `null` and deletes referenced content. Headers stay, and the chain stays valid.
- **Policy authors are not approvers for the runs governed by their change.** A profile change that relaxes a required check is reviewed by someone else.

## 4. Key management and rotation

- **Use an asymmetric key in production.** An EC key in Key Vault or Managed HSM, signed with ES256, keeps the private key in the vault and lets you publish the public key. `KeyVaultKeySigner` in the sample marks this seam.
- **Treat HMAC as internal only.** `KeyVaultSecretHmacSigner` reads a secret out of the vault, and every verifier needs the same secret, so anyone who can verify can forge. The sample's `InMemoryHmacSigner` key is a sample-only constant.
- **Always use versioned key IDs.** The certificate's `signature.key_id` names the exact key version. Rotation creates a new version for new certificates; old certificates still name the old version.
- **Publish the public keys.** Keep a list of trusted key IDs and their public keys, with validity dates, wherever verifiers can read it. Keep retired versions published for as long as certificates signed with them are retained.
- **Pin the algorithm per key.** Verifiers map each trusted key ID to one algorithm and reject anything else, which is how the sample raises `certificate.alg_mismatch` for `none`.
- **Rotate on a schedule and on suspicion.** Use Key Vault rotation policies where available, and alert on sign operations from any identity other than the issuer.
- **On compromise:** disable the key version, remove it from the trusted list, find every certificate signed with it in the archive, re-verify each one against its archived ledger, and re-issue where the run still qualifies. Record the incident and the re-issued certificate IDs.

## 5. Retention, legal hold and erasure

| Data | Where | Retention | Erasure |
|---|---|---|---|
| Headers and `entry_hash` | Live store and archive | The audit retention period for the use case. | Not erasable while retained. Headers hold digests, references and timestamps, not content. |
| Redacted bodies | Live store | Shorter: as long as operators need readable details. | Set to `null` with `strip_bodies` semantics. The chain and certificate stay valid. |
| Certificate | Archive | The audit retention period. | Not erasable while retained. |
| Artifact | Archive and the system of record | Business retention rules for the artifact. | Through the system of record. Offline verification then runs without `artifact_bytes`. |
| Source content, tool results, model text | Their own stores, referenced by `payload_ref` or not at all | Their own rules. | Their own process. The ledger holds only digests. |

- **Issue before purging.** The issuer needs the bodies. A reissue from purged entries returns `withheld` with `run.not_completed`.
- **Lock immutability only after testing.** A locked time-based retention policy on Blob Storage can be extended but not shortened or removed. Test with an unlocked policy.
- **Use legal holds for investigations.** A legal hold on the archive blocks deletion regardless of retention. Check for holds on referenced content stores before deleting content.
- **Review the allowlist with privacy owners.** Every allowlisted field is readable until purge. In the sample, the requester's identity is hashed only and the approver's identity is readable, because separation of duties must be auditable.
- **Mind low-entropy digests.** A digest of a value from a small set can be reversed by guessing. Use keyed digests for those fields (see [extension points](architecture.md#21-extension-points)).

## 6. Releasing ledger and certificate changes

Treat changes to the evidence path like changes to a payments system.

- Version the schemas (`ledger-entry/v1`, `run-certificate/v1`) and bump them for any change to the canonical form, header fields or certificate fields. Verifiers reject schemas they do not know.
- Version the profile (`profile_version`) and the verifier (`verifier_version`). Both are recorded in every certificate.
- Run the tamper drills, the equivalent of `python samples/evidence_ledger.py --self-test`, in CI on every change to the writer, issuer, verifier, redaction allowlist or Domain Packs.
- Keep old verifiers working: an archived certificate must still verify with the code and keys that applied when it was issued.
- See the [release gates](evaluation-and-troubleshooting.md#release-gates).

## 7. Kill switch and incident response

| Situation | Immediate action |
|---|---|
| Ledger store unavailable or rejecting appends | Stop new runs. Runs that cannot record evidence cannot be certified. |
| Unexpected `policy.unauthorized_tool_call` | Treat as a security incident: an enforcement point was bypassed. Suspend the agent version and investigate the tool's own authorization. |
| `certificate.head_mismatch` or `entry_count_mismatch` on an archived run | Treat as tampering with the live store or archive. Preserve both copies and investigate store access. |
| Suspected signing key compromise | Disable the key version and follow the procedure in [section 4](#4-key-management-and-rotation). |
| Allowlist leak (content in a readable body) | Purge the affected bodies, fix the harness code, and add a self-test case for the leak. |

## 8. Audit

- Store certificates and header-only ledgers in the archive, and keep the trusted key list with its history.
- Log every sign, seal, purge, legal hold and immutability policy change to Azure Monitor, with the run ID and the acting identity.
- Sample archived runs regularly and re-verify them offline with an independent verifier build.
- Report counts of withheld certificates by reason code, by agent version and by profile version.

## 9. Threat model

The full threat table is in [architecture section 9](architecture.md#9-threat-model). The identity-specific points:

- A compromised harness writer can record false steps for runs in progress, but cannot sign. Detect it through policy and verifier mismatches, and limit it by running each harness with the narrowest scope.
- A compromised issuer can sign a false `issued` certificate. This is the main residual risk. Re-derive withheld reasons offline and anchor certificate digests externally (see [extension points](architecture.md#21-extension-points)).
- A compromised approver account can approve a bad artifact, but not one that failed verification and not one the approver initiated. Use strong authentication and conditional access for approvers.
- A prompt-injected model has no identity in the evidence path. It can make the run fail its checks, which withholds the certificate; it cannot forge evidence.

## 10. Operating model

| Role | Owns |
|---|---|
| Platform team | Writer, issuer, store, archive and the trusted key list. |
| Security | Signing keys, rotation, compromise response, and access reviews. |
| Data protection | Redaction allowlists, purge schedules, legal holds and erasure requests. |
| Use-case owner | The profile, required checks, approvers and the Domain Pack. |
| Audit and compliance | Offline verification, sampling and reporting. |

Review role assignments for the writer, issuer, approver and purge roles at least as often as your access review policy requires, and review every allowlist change with data protection.
