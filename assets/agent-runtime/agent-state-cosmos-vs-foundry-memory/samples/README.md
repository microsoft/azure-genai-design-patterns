# Samples - Agent State and Memory

`agent_state_store.py` is a self-contained reference that uses only the Python standard library (Python 3.10+). It implements the pattern end to end:

- two small interfaces (`StateStore` for threads and checkpoints, `MemoryStore` for long-term memory) and a `ContextProvider` hook that wraps both;
- a sealed, verified `Principal`, which is the only source of partition keys and memory scopes;
- in-memory and SQLite thread-state backends with etag concurrency, idempotency keys and TTL driven by an injectable clock;
- an in-memory long-term memory backend with per-user scopes, simple relevance scoring, supersession, TTL and erasure;
- a memory write policy that refuses credentials, sensitive categories, system-of-record data and instruction-like text, and records why;
- an erasure coordinator that clears both stores and returns an auditable receipt;
- documented production stubs for Cosmos DB, Foundry Memory and a self-hosted vector index.

It makes no network calls, imports no Azure SDK and needs no secrets. The SQLite backend uses an in-memory database, so the sample writes no files.

## Run it

```powershell
# Core suite (exit code 0 = all scenarios passed, 1 = at least one failed). This is also the default.
python samples\agent_state_store.py --self-test

# Domain packs, one at a time or all together
python samples\agent_state_store.py --domain contoso-finance
python samples\agent_state_store.py --domain fabrikam-health
python samples\agent_state_store.py --domain northwind-retail
python samples\agent_state_store.py --domain all

# Narrated six-step walkthrough of one user's turns, a blocked write, recall in a new thread, a stale write and erasure
python samples\agent_state_store.py --demo
```

Run the commands from the asset folder. If you run from elsewhere, use the full path to the script. The three modes are mutually exclusive. Set `PYTHONDONTWRITEBYTECODE=1` if you do not want a `__pycache__` folder.

## What each component demonstrates

| Component | Role in the pattern | Production equivalent |
|---|---|---|
| `Principal`, `FakeTokenVerifier` | Sealed result of token verification. Application code cannot construct one. It derives `state_partition_key()`, `memory_scope` and a pseudonymous `subject_ref`. | Claims principal from Microsoft Entra token validation |
| `EntraTokenVerifier` | Production seam. It raises `NotImplementedError`. | A maintained OIDC/JWT library configured for Entra |
| `RequestContext`, `ignored_hints()` | Headers and payload fields such as `x-tenant-id` or `memory_scope` are recorded as ignored hints and never used as keys | Request object in the agent host |
| `StateStore` (Protocol) | `create_thread`, `get_thread`, `append_message`, `recent_messages`, `save_checkpoint` (expected etag plus idempotency key), `load_latest_checkpoint`, `delete_thread`, `erase_user` | `CosmosStateStore` |
| `InMemoryStateStore`, `SqliteStateStore` | Two interchangeable fakes with the same contract. SQLite uses a conditional `UPDATE ... WHERE etag = ?` in place of an if-match replace. | Private Cosmos DB for NoSQL; SQLite for local development |
| `MemoryStore` (Protocol) | `add`, `search(principal, query, k)`, `erase_user` | `FoundryMemoryStore` or `VectorMemoryStore` |
| `InMemoryMemoryStore` | Scope per verified tenant and user, token-overlap scoring, topic supersession, TTL and an erasure watermark | Foundry Memory, or a private vector index |
| `ConsentRegistry`, `MemoryWritePolicy` | Opt-in check, then ordered rules. The first match is the recorded reason. | Consent service plus a classifier and human review of blocked items |
| `MemoryExtractor`, `Reflector` | Rule-based extraction of candidates, and an optional summary of a thread. Both go through the write policy. | LLM extraction (Foundry Memory runs its own) and a scheduled reflection job |
| `MemoryWriter` | Asynchronous queue. `drain()` returns `written`, `blocked` and `dropped`. | Queue or background worker |
| `MemoryContextProvider` | `before_turn` loads the thread, checkpoint, bounded history and top-k memories. `after_turn` checkpoints first, then queues memory candidates. A memory outage degrades the turn instead of failing it. | Agent Framework context provider or middleware |
| `ErasureCoordinator` | Authorizes self or a same-tenant `privacy-officer`, drops queued candidates, erases both stores, re-counts to verify, and replays the same receipt for a repeated request id | Privacy request workflow |
| `AuditLog` | Allow-listed fields only: ids, reasons and counts. Any other field raises `ValueError`. | Azure Monitor / Log Analytics, or an append-only audit store |
| `CosmosStateStore`, `FoundryMemoryStore`, `VectorMemoryStore` | Documented stubs. Each method raises `NotImplementedError`. Endpoints are `example.com` placeholders. | Your wired backends |

## Core scenarios (`--self-test`)

| # | Scenario | What it proves |
|---|---|---|
| 1 | `thread_isolation_across_tenants` | The same user id in another tenant, and another user in the same tenant, get indistinguishable `NotFoundOrDenied` errors. Partition keys differ by tenant. |
| 2 | `forged_tenant_hint_ignored` | Forged tenant, user and scope headers and payload fields select nothing. They are audited by name only, without values. |
| 3 | `unverified_principal_rejected` | Direct construction, a bad signature or audience, an untrusted tenant and a malformed id are all rejected. The memory scope is opaque. |
| 4 | `memory_scoped_per_user` | User A's preference is recalled for user A in a new thread and never returned to user B in the same tenant |
| 5 | `idempotent_checkpoint_and_resume` | A retry with the same key is a no-op. A stale etag raises `ConcurrencyConflict` and writes nothing. A restarted provider resumes from the latest checkpoint. |
| 6 | `ttl_expiry_of_thread_state` | Activity slides the thread expiry. An idle thread expires, while memory outlives it on its own TTL. |
| 7 | `erasure_removes_state_and_memory` | Both stores and the queued candidate are cleared for one user only. A replay returns the same receipt, and a late candidate is dropped by the watermark. |
| 8 | `erasure_authorization` | A peer is denied, and a privacy officer from another tenant can never reach this tenant. A same-tenant privacy officer succeeds. |
| 9 | `write_policy_blocks_sor_and_sensitive` | Credentials, card numbers, government ids, health details, balances, order numbers, instruction-like text, missing consent, unsupported categories and over-long text are all blocked with a reason |
| 10 | `context_provider_bounds` | Only relevant memories above the score floor are injected, within top-k and the character budget. History is capped. Memory is labelled non-authoritative. |
| 11 | `contradiction_supersedes_older_memory` | A newer preference on the same topic supersedes the older one. Unrelated memories survive. |
| 12 | `backend_swap_sqlite` | The SQLite backend passes the same isolation, idempotency, resume, erasure and TTL checks |
| 13 | `audit_has_no_content` | No message, memory or blocked text appears in audit events |
| 14 | `production_stubs_are_documented_not_wired` | The stubs satisfy the Protocols, are documented and raise `NotImplementedError`. No Azure module is imported. |
| 15 | `memory_outage_degrades_not_fails` | With memory down, the turn still checkpoints and is marked `memory_unavailable`. Recall returns after recovery. |
| 16 | `reflection_summary_passes_policy` | A shopping summary is stored and recalled. A clinical summary is blocked as `sensitive_category:health`. |

## Domain scenarios (`--domain`)

| Domain | Scenario | What it proves |
|---|---|---|
| `contoso-finance` | `dispute_resumes_after_replica_restart` | A dispute flow on SQLite resumes on a new provider, and the client retry is a duplicate |
| `contoso-finance` | `balance_comes_from_ledger_not_memory` | A balance is refused as memory and read through a tool, which returns the current value |
| `contoso-finance` | `statement_preference_recalled_in_new_thread` | A delivery preference follows the user into a new thread |
| `fabrikam-health` | `clinical_details_never_memorized` | Medication and allergy statements stay in thread state only |
| `fabrikam-health` | `reminder_preference_recalled` | A reminder-channel preference is recalled for the patient and not for another patient |
| `fabrikam-health` | `patient_self_erasure_verified` | Self-service erasure verifies, replays idempotently and leaves nothing to recall |
| `northwind-retail` | `order_and_card_details_blocked` | An order number and a test card number are refused |
| `northwind-retail` | `size_and_pickup_recalled` | Only the relevant size and pickup preferences are recalled |
| `northwind-retail` | `same_user_id_separate_across_tenants` | The same object id in two tenants gives different partitions and scopes |

Each scenario builds a fresh `Stack`, so scenarios never share state. A scenario that raises an unexpected exception counts as a failure, and the run continues.

## Policy reason codes

| Reason | Raised when |
|---|---|
| `no_consent` | The verified user has not opted in to long-term memory |
| `unsupported_category` | The candidate is not a preference, profile, procedural or summary memory |
| `too_long` | The candidate text exceeds the illustrative maximum length |
| `credential` | Passwords, PINs, keys, tokens or one-time codes |
| `sensitive_category:payment_card`, `:government_id`, `:health` | Card numbers, identity documents, clinical details |
| `system_of_record_data` | Balances, invoices, transactions, order numbers or currency amounts. Read these through a tool. |
| `instruction_like_content` | Text that tries to change the agent's instructions |

## Extending the suite

- Add a core scenario with `@scenario("name")` or a domain scenario with `@domain_scenario("contoso-finance", "name")`. Build a `Stack()` (or `Stack(state_backend="sqlite")`) and use `login`, `thread`, `turn`, `recall` and `new_provider`.
- Fail with `expect(condition, message)` or `expect_raises(ErrorType, fn, ..., detail="...")`. Both raise `CheckFailed`.
- Advance time with `s.clock.advance(seconds)` instead of sleeping.
- Keep audit assertions on reasons and counts. Never add content fields to `AUDIT_FIELDS`.

## Swapping in real backends

1. Implement the `StateStore` or `MemoryStore` Protocol in your own module. Keep the method signatures, so `MemoryContextProvider`, `MemoryWriter` and `ErasureCoordinator` do not change.
2. Follow the wiring in the stub docstrings and in [architecture section 6](../docs/architecture.md#6-cosmos-db-data-model-for-thread-state) (Cosmos DB) and [section 7](../docs/architecture.md#7-foundry-memory-model) (Foundry Memory). Treat Foundry Memory behavior as observed in preview; verify against current docs.
3. Map backend errors to the sample's types: precondition failure to `ConcurrencyConflict`; not found and owner mismatch both to `NotFoundOrDenied`; missing verification to `ScopeViolation`; refused erasure to `ErasureDenied`; writes after an erasure watermark to `ScopeErased`.
4. Derive every partition key and scope from `Principal` only. Use managed identity, data-plane RBAC and private endpoints, as described in [authentication and governance](../docs/authentication-and-governance.md).
5. Re-run the scenarios against your implementation as contract tests, as described in [evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md#8-running-and-extending-the-reference-suite).

## Safety notes

- All tenant and user ids are synthetic zero GUIDs. Placeholder endpoints use `example.com`.
- The test card number is a well-known test pattern assembled at runtime.
- Retention values (thread, memory and idempotency TTLs, history and memory budgets) are illustrative, not recommendations.
- The sample hashes scopes with unkeyed SHA-256 for readability. In production, use an HMAC with the key held in Key Vault.

See [../README.md](../README.md) for the asset overview and [../docs/architecture.md](../docs/architecture.md) for the full design.
