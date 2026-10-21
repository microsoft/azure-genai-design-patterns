# ADR 0001: Hash chain plus a signed run certificate

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [ADR 0002](0002-digests-not-content-in-ledger.md), [ADR 0003](0003-withheld-certificate-means-not-done.md)

## Context

An agent run produces an artifact that someone will rely on: a report, a prior-authorization summary, a deviation record, a case decision. Later, an auditor, a regulator or a downstream system needs to answer a narrow question: *which steps produced this artifact, were the required checks run on these exact bytes, who approved it, and has any of that been changed since?*

Ordinary logs and traces do not answer it. They are designed for observability: sampled, mutable, retained for a short time, and written by many components. Anyone with write access to the log store can change history without leaving a trace.

The common first response is to hash-chain the log: each entry includes the hash of the previous one. That detects an edit, a removal or a reorder in the middle of the chain. It does not detect two attacks that matter:

- **Full rewrite.** A writer who can change one entry can also recompute every hash after it. The rewritten chain is internally consistent.
- **Truncation.** Any prefix of a valid chain is itself a valid chain. Dropping the last steps, such as a failed check or a denied tool call, leaves nothing to find.

A chain is only evidence relative to a head that someone outside the writer has pinned. The pattern needs that pin, and it needs it in a form that can be checked later without access to the live system.

## Decision

1. **Every step is an append-only ledger entry, hash-chained.** Each entry's header includes its `seq`, `run_id`, `type`, `actor`, `at`, `trace_id`, payload and body digests, and the previous entry's `entry_hash`. The `entry_hash` is the SHA-256 digest of the canonical header.
2. **Appends are conditional on the head.** The writer appends with the head it read. The store rejects a stale head, a wrong `run_id`, `seq` or `prev_hash`, and any append after the run is sealed. The writer refuses entry types that have no redaction allowlist.
3. **A signed run certificate pins the head.** At the end of the run, an issuer separate from the writer signs a certificate that binds the ledger head hash and entry count, the artifact digest, the verification receipt digest and required check results, the approval decision digest, the policy profile and version, and the agent and model versions.
4. **The algorithm and key ID are signed too.** The signing input is the canonical certificate without `signature.value`, so swapping the algorithm or the key breaks the signature. Verifiers pin the algorithm per trusted key ID.
5. **The run is sealed after issuance.** Nothing can be appended after the certified head, and the certificate, the header-only ledger and the artifact are written to a write-once archive.
6. **Verification is offline.** `verify_offline` needs only the certificate, a copy of the ledger, the trusted public keys and optionally the artifact, receipt and approval. It recomputes the chain and compares it with the pinned head and count.
7. **Production signatures are asymmetric.** The sample uses an in-memory HMAC signer so it runs without Azure. Production issuers sign with an asymmetric key in Azure Key Vault or Managed HSM, so verifiers hold only the public key.

## Consequences

### Positive

- Edits, removals, reorders, full rewrites and truncation are all detected. The self-test pins each attack to its finding.
- An auditor can verify a run years later with no access to the agent platform, the store or the issuer.
- The certificate is a compact, signed summary of the run's evidence, so downstream systems can gate on it without reading the ledger.
- Separating the writer and the issuer means compromising one identity is not enough to produce false evidence that verifies.

### Trade-offs

- **Before issuance, the run is not pinned.** A writer can still rewrite an open run. Keep runs short, issue promptly, and consider anchoring intermediate heads externally for long runs.
- **The issuer is trusted.** A compromised issuer can sign a false `issued`. The sample's offline verifier checks the issuer's bindings but does not re-derive its decisions. Re-deriving the withheld reasons offline is an extension point.
- **Keys must be managed.** Key rotation, trusted key lists and algorithm pinning are operational work. See [key management and rotation](../authentication-and-governance.md#4-key-management-and-rotation).
- **The canonical form is a contract.** Every writer, issuer and verifier must produce identical bytes. Changing it requires a schema version bump and shared test vectors.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Logs and traces only | Mutable, sampled and short-lived. Good for operations; not evidence. |
| Hash chain without a signed head | Detects edits in the middle, but not a consistent rewrite or a truncated tail. |
| Sign every entry | Proves who wrote each entry, but a truncated tail still verifies, and every append needs a signing call. A signed head gives the same tamper evidence for the run at one signature per run. |
| Immutable storage only | Protects stored bytes against overwrite, but does not bind the artifact, checks and approval together, and cannot be verified away from the storage account. Used as a complement for the archive. |
| An external transparency log or distributed ledger | Stronger against a compromised operator, but adds a dependency and operating cost most runs do not need. Supported as an extension for anchoring heads. |
