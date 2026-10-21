# ADR 0002: The ledger stores digests and references, not content

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture](../architecture.md), [README](../../README.md), [ADR 0001](0001-hash-chain-plus-signed-certificate.md), [ADR 0003](0003-withheld-certificate-means-not-done.md)

## Context

To prove what an agent did, it is tempting to record everything: the prompt, the retrieved documents, the tool arguments and results, the model's output. The ledger would then be a complete replay of the run.

In regulated work, that content is often the most sensitive data in the system: financial results before disclosure, patient records, deviation investigations, citizen case files. Copying it into an audit store creates problems:

- **The audit store becomes the most sensitive store.** It holds a copy of everything, from every run, with a long retention period.
- **Access control is lost.** Source systems enforce who can read what. A copy in the ledger is readable by anyone who can read the ledger, which includes auditors who should see the evidence but not the content.
- **Erasure and retention conflict with immutability.** Content must be deleted on schedule or on request. An append-only chain whose hashes cover the content cannot be edited without breaking it.
- **Prompt injection reaches the audit store.** Retrieved text and tool results may contain instructions or misleading claims, and they would sit in the evidence record unfiltered.

The audit question is not "what did the content say?" It is "can we prove which exact content was used, and that it has not changed?" A digest answers that.

## Decision

1. **Every payload is represented by its digest.** Each entry carries `payload_digest`, the SHA-256 of the full canonical payload, inside the hashed header. Anyone who holds the original payload can prove it matches.
2. **Content lives in its system of record.** An optional `payload_ref` points to where the content is kept, such as a document URI or an artifact path, under that system's own access control and retention.
3. **Only allowlisted fields are readable.** Each entry type has a short allowlist of fields that may be stored in readable form, such as `action`, `outcome`, `rule_id`, `status`, `source_id` or `verdict`. `redact` keeps only those fields, and `verify_chain` reports any extra field as `ledger.redaction_policy`.
4. **Identities are decided per field.** The requester's identity in `run_started` is hashed only. The approver's identity in `approval_decision` is readable, because separation of duties must be auditable.
5. **Readable bodies sit outside the entry hash.** The `redacted` body is bound by `redacted_digest` in the header, but is not itself part of the header. Setting it to `null` purges the readable details without breaking the chain or the certificate.
6. **The archive keeps headers only.** The long-lived archived ledger is written with bodies stripped.
7. **The harness passes digests, not content, for sources.** `source_retrieved` entries carry the source ID, URI, classification and content digest. The content itself never reaches the writer.

## Consequences

### Positive

- The ledger is far less sensitive than the content it describes, so auditors, operators and downstream systems can read it with broad access.
- Retention and erasure are handled where the content lives. The ledger stays valid after content is deleted and after bodies are purged.
- A leak of the ledger exposes structure and digests, not content.
- The scenario `raw-content-stays-out-of-ledger` proves that planted markers, model text, the requester's identity and source content never appear in the serialized ledger.

### Trade-offs

- **The ledger alone cannot replay the run.** To see what the content said, a reader fetches it through `payload_ref` with their own permissions, and checks its digest. If the content was deleted, only the fact that it existed remains.
- **Low-entropy values can be guessed.** A digest of a value chosen from a small set, such as a status or a short ID, can be reversed by hashing every candidate. Keep such values in the allowlist if they are not sensitive, or use a keyed digest (HMAC) where they are. Keyed digests are an extension point.
- **Allowlisted fields can still carry content.** A URI or a source ID can encode sensitive information. Validate the format of allowlisted fields and review allowlist changes with data protection owners.
- **Issue before purging.** The issuer reads bodies to evaluate rules such as the close status and policy decisions. A certificate issued from stripped entries is withheld with `run.not_completed`.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Store full content in the ledger | Makes the ledger the most sensitive store, defeats source access control and conflicts with erasure. |
| Store content encrypted in the ledger | Keeps the content in scope for retention and erasure, and key management becomes part of the audit path. Crypto-shredding is possible but adds complexity for little gain over references. |
| Hash everything, keep nothing readable | Safe, but the ledger becomes unreadable for ordinary audit questions such as which rule allowed a tool call. A short allowlist balances the two. |
| Readable fields inside the entry hash | Purging them would break the chain. Binding the body through `redacted_digest` keeps purge possible. |
| Rely on traces for content | Traces are sampled, mutable and short-lived, and often carry content that should not be retained. They complement the ledger; they do not replace it. |
