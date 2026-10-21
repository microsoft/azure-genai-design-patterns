# Authentication and Governance

> Part of [Agent State and Memory: Private Cosmos DB vs Foundry Memory](../README.md). Companion documents: [Architecture](architecture.md) · [Evaluation and troubleshooting](evaluation-and-troubleshooting.md) · [ADR 0001](decisions/0001-separate-conversation-state-from-long-term-memory.md) · [ADR 0002](decisions/0002-scope-memory-to-verified-user-and-tenant.md)

This document covers how the agent authenticates to the state store and the memory store, how keys and scopes are derived, and how consent, retention, erasure and audit are governed. It assumes the design in [architecture.md](architecture.md). It does not repeat the role assignments needed to run a hosted agent, which are in [Production Hosted Agents on Microsoft Foundry](../../production-hosted-agents-on-foundry/docs/authentication-and-governance.md). It also does not repeat the token-validation rules, which are in [Request Context Is Not Authorization](../../request-context-is-not-authorization/docs/authentication-and-governance.md).

Role names, data actions and Foundry Memory behavior change between releases. Statements marked **(observed in preview; verify against current docs)** come from testing, not from a documented contract.

## Contents

1. [Principles](#1-principles)
2. [Identity inventory](#2-identity-inventory)
3. [Cosmos DB: managed identity and data-plane RBAC](#3-cosmos-db-managed-identity-and-data-plane-rbac)
4. [Private networking and data residency](#4-private-networking-and-data-residency)
5. [Foundry project identity and memory roles](#5-foundry-project-identity-and-memory-roles)
6. [Scope from verified identity only](#6-scope-from-verified-identity-only)
7. [Consent and data categories](#7-consent-and-data-categories)
8. [Retention, time to live and the erasure workflow](#8-retention-time-to-live-and-the-erasure-workflow)
9. [Logging without leaking memory](#9-logging-without-leaking-memory)
10. [Encryption and customer-managed keys](#10-encryption-and-customer-managed-keys)
11. [Separation of duties](#11-separation-of-duties)
12. [Threat-to-control mapping](#12-threat-to-control-mapping)
13. [Governance checklist](#13-governance-checklist)
14. [References](#14-references)

## 1. Principles

1. **No keys.** Every store is reached with a managed identity or an agent identity and data-plane role-based access control (RBAC). Account keys, connection strings and resource tokens are disabled, not just unused.
2. **Keys and scopes come from the verified principal.** The state partition key and the memory scope are computed from the validated token's tenant id and object id, and from nothing else ([ADR 0002](decisions/0002-scope-memory-to-verified-user-and-tenant.md)).
3. **RBAC is not a tenant boundary.** Cosmos DB and Foundry roles grant access to an account, database, container or project. They do not restrict access to one partition key or one memory scope. Tenant and user isolation inside a container or store is enforced by the adapter, so the adapter's key derivation is a security control and is tested like one.
4. **Memory is opt-in and governed by policy.** No consent means no memory writes. Every write passes a policy that refuses credentials, sensitive categories, system-of-record data and instruction-like text.
5. **Retention is configured; erasure is executed.** Time to live is a retention backstop. A right-to-be-forgotten request is an explicit, verified, audited delete across both stores.
6. **Logs carry identifiers, reasons and counts, never content.**

## 2. Identity inventory

| Identity | Kind | Used to call | Needs | Must never |
|---|---|---|---|---|
| **End user** | Entra user signed in at the client | The front door | Consent to memory, if memory is used | Supply their own tenant or user id in a header or body that the agent trusts |
| **Front door** | Managed identity of the gateway or web tier | The agent endpoint | Invoke rights on the agent | Forward identity hints that downstream code treats as facts |
| **Agent runtime** | Hosted agent's managed identity or Entra agent identity | Cosmos DB (thread state), memory store, models | Data-plane read and write on the state container; memory read and write on its own store | Delete across tenants; manage accounts, containers or memory stores |
| **Memory writer** | The agent runtime identity, or a separate worker identity if writes run out of process | The memory store | Memory write on the agent's store | Read thread state it does not need |
| **Memory service extraction** | The identity the memory service uses to call the chat and embedding deployments | Model deployments | Model data-plane access (see section 5) | Be shared with unrelated workloads |
| **Erasure coordinator** | A separate managed identity, invoked by the privacy workflow | Cosmos DB, memory store, consent registry, audit store | Read, delete and query on the state container; scope deletion on the memory store | Serve end-user turns |
| **Platform operators** | Entra groups with just-in-time elevation | Azure Resource Manager | Control-plane roles to create accounts, containers and memory stores | Hold data-plane read rights on conversation content by default |

Use [workload identities](https://learn.microsoft.com/entra/workload-id/workload-identities-overview) (managed identities or federated credentials) for every non-human identity. How Foundry assigns identities to agents is described in [agent identity in Foundry](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity).

## 3. Cosmos DB: managed identity and data-plane RBAC

**Disable key-based access.** Set `disableLocalAuth: true` on the account so that primary and secondary keys and resource tokens are rejected for data operations. Set `disableKeyBasedMetadataWriteAccess: true` so that container and database changes go through Azure Resource Manager, where they are governed and logged. Deploy both settings from infrastructure as code and audit them with Azure Policy.

**Separate the control plane from the data plane.**

| Plane | Role system | Example role | Grant to | Scope |
|---|---|---|---|---|
| Control plane | Azure RBAC | Cosmos DB Operator (`230815da-be43-4aae-9cb4-875f7bd000aa`) | Platform pipeline and operators | Account |
| Data plane, read only | Cosmos DB native role definitions | Cosmos DB Built-in Data Reader (`00000000-0000-0000-0000-000000000001`) | Support tooling that reads metadata and items for diagnostics, if approved | Container (`/dbs/<db>/colls/agent_state`) |
| Data plane, read and write | Cosmos DB native role definitions | Cosmos DB Built-in Data Contributor (`00000000-0000-0000-0000-000000000002`) or a custom role | Agent runtime; erasure coordinator | Container (`/dbs/<db>/colls/agent_state`) |

Data-plane roles are assigned with Cosmos DB SQL role assignments (`Microsoft.DocumentDB/databaseAccounts/sqlRoleAssignments`, or `az cosmosdb sql role assignment create`), not with an Azure role assignment on the account. The scope can be the account (`/`), a database (`/dbs/<db>`) or a container (`/dbs/<db>/colls/<container>`). Always scope to the container.

**Prefer a custom role for the agent runtime.** The built-in contributor role includes item deletion and other actions the turn path does not need. A custom role definition can grant only the data actions the turn path uses:

| Data action | Turn path needs it for |
|---|---|
| `Microsoft.DocumentDB/databaseAccounts/readMetadata` | SDK initialization |
| `.../sqlDatabases/containers/items/create` | Messages, checkpoints, idempotency records, new thread heads |
| `.../sqlDatabases/containers/items/read` | Point reads of the head and the idempotency record |
| `.../sqlDatabases/containers/items/replace` | Replacing the thread head with if-match |
| `.../sqlDatabases/containers/executeQuery` | Reading recent messages and the latest checkpoint |

Items expire through time to live, which the service performs without the caller needing delete rights. Give `items/delete` only to the erasure coordinator, and to the agent runtime only if the agent offers an in-conversation "delete this thread" command. Transactional batch operations require the permission for each operation in the batch. Verify the data action names against current docs before you deploy.

**What RBAC cannot do.** A role assignment on `agent_state` lets the identity read every tenant's items in that container. The hierarchical partition key keeps queries efficient and makes the tenant explicit, but the adapter enforces it. Two consequences:

- The adapter computes `[tenantId, threadId]` only from `Principal.state_partition_key(thread_id)` and checks `ownerOid` on every thread read. Scenarios `01`, `02` and `03` are security tests, not functional tests.
- If a tenant needs isolation enforced by the platform (separate keys, separate regions, separate access), add a container or an account per tenant tier and scope the role assignment to it. The item model does not change.

## 4. Private networking and data residency

| Concern | Thread state in Cosmos DB | Memory in Foundry Memory | Memory in a self-hosted store |
|---|---|---|---|
| Public access | Disabled (`publicNetworkAccess: Disabled`) | Reached through the Foundry project endpoint; follow the project's network configuration | Disabled on Azure AI Search or PostgreSQL |
| Private endpoint | Sub-resource `Sql`, private DNS zone `privatelink.documents.azure.com` | Through the Foundry resource's private endpoint for the project endpoint | Search: `privatelink.search.windows.net`; PostgreSQL flexible server: its private endpoint or virtual network integration |
| Where the data lives | Your account, in the regions you configure | A service-managed store in the project. Memory stores do not support virtual network integration **(observed in preview; verify against current docs)** | Your resource, in the region you choose |
| Fit for "must stay on our private network" | Yes | Check before choosing; if in doubt, use a self-hosted store | Yes |

Each Cosmos DB region in the account needs its own private DNS records, and the agent's virtual network must resolve all of them. The landing-zone wiring for private endpoints, DNS zones, bring-your-own thread storage and hosted-agent egress is in [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md). Its [network and DNS troubleshooting guide](../../../landing-zones-and-infra/secured-foundry-landing-zone/docs/network-and-dns-troubleshooting.md) covers split-horizon resolution checks.

Data residency is decided per kind of state ([ADR 0001](decisions/0001-separate-conversation-state-from-long-term-memory.md)). Transcripts can carry anything a user types, including regulated content, so thread state usually has the strictest residency. Memory holds only policy-approved preferences, which can make a managed store acceptable where a transcript store would not be. Record that decision per Domain Pack (architecture [section 12](architecture.md#12-domain-packs)).

## 5. Foundry project identity and memory roles

| Identity | Role | Scope | Why |
|---|---|---|---|
| Agent runtime (calls memory search and update) | Foundry User (`53ca6127-db72-4b80-b1b0-d745d6d5456d`, formerly Azure AI User) | The Foundry project or resource | Data-plane access to the project, including memory operations |
| Identity the memory service uses for extraction and embedding | Cognitive Services OpenAI User, or an equivalent model data-plane role | The resource that hosts the chat and embedding deployments | Without it, update calls do not produce memories and the store stays empty **(observed in preview; verify against current docs)** |
| Erasure coordinator | Foundry User, or a custom role that includes scope deletion | The Foundry project | Deletes a user's scope and runs the verification search |
| Platform pipeline | A project management role | The Foundry project | Creates and configures memory stores, models and profile guidance |

Check whether the role you grant the agent runtime also allows it to create, reconfigure or delete memory stores. If it does and that is not acceptable, use a custom role with only the data actions the agent needs. Keep store creation, model selection and profile guidance in the platform pipeline, where changes are reviewed.

When a prompt agent uses a memory search tool, or a hosted agent uses a Foundry memory context provider, the service resolves the scope per request. Prove which value it used and where that value came from before you rely on it (architecture [section 7.3](architecture.md#73-binding-memory-to-agents)).

## 6. Scope from verified identity only

| Value | Derived from | Never from |
|---|---|---|
| Tenant level of the partition key | `tid` of a validated token, through `Principal` | `x-tenant-id`, `x-ms-client-tenant-id`, a body field, a URL segment, the model |
| Thread owner | `oid` of a validated token | A user id in the request |
| Thread id | Server-issued at `create_thread`; the client may echo it back | Client-chosen ids accepted as new threads |
| Memory scope | A hash of `tid` and `oid` (`mem-...`) | `x-memory-user-id`, `x-agent-user-id`, `x-user-id`, a `scope` or `memory_scope` field |
| Audit subject | A separate hash of `tid` and `oid` (`sub-...`) | Display names, email addresses |
| Erasure target | A directory object id in the requester's verified tenant | Email addresses, display names, another tenant |

The sample enforces this with a sealed `Principal` that only a token verifier can construct. Request hints are recorded by name in a `hint_ignored` audit event and otherwise ignored. The rule and its reasoning are in [Request Context Is Not Authorization](../../request-context-is-not-authorization/README.md), including why [memory, threads and retrieval are resources too](../../request-context-is-not-authorization/docs/architecture.md#64-memory-threads-and-retrieval-are-resources-too).

**Use a keyed hash in production.** The sample computes scopes with a plain SHA-256 for readability. Anyone who knows a tenant id and object id can recompute that value. In production, use an HMAC whose key is held in Key Vault and read by managed identity, so that a scope string reveals nothing on its own. The key must stay stable: rotating it changes every scope and orphans existing memories, so plan rotation as a migration with a re-keying job. A scope string is still personal data (it is pseudonymous, not anonymous) and is covered by erasure.

**No user, no memory.** App-only tokens (an autonomous job running as the agent identity) have no user, so they get no memory scope and no user thread. A scheduled job that must act for a user needs a stored, auditable delegation, as described in [Request Context Is Not Authorization](../../request-context-is-not-authorization/docs/authentication-and-governance.md).

## 7. Consent and data categories

**Consent.** Long-term memory is a feature the user turns on. Record consent per verified user and tenant (the sample's `ConsentRegistry` keys it by memory scope), show the user what is remembered, and let them delete individual memories or all of them. Revoking consent stops new writes immediately; existing memories are erased if the user asks, or kept under the retention policy if they only paused the feature. Keep memory consent separate from marketing or analytics consent.

**Data categories.** Classify every category before deciding where it may live.

| Category | Thread state | Long-term memory | System of record |
|---|---|---|---|
| Preferences (channel, language, format, size, pickup location) | Yes, as typed | Yes, with consent | Optional mirror, if the business needs it |
| Profile facts the user chooses to share (role, team, time zone) | Yes | Yes, with consent | The directory or HR system is authoritative |
| Credentials, one-time codes, PINs, keys | Avoid; redact before persisting where possible | **Never** (`credential`) | Never in agent stores |
| Payment card data | Avoid; redact before persisting if your compliance scope requires | **Never** (`sensitive_category:payment_card`) | The payment system |
| Government identifiers | Avoid; redact where possible | **Never** (`sensitive_category:government_id`) | The identity or records system |
| Health information | Only inside the regulated boundary, under the records policy | **Never** (`sensitive_category:health`) | The clinical record |
| Balances, orders, invoices, transactions | Mentioned in conversation only | **Never** (`system_of_record_data`) | The ledger, commerce or billing system |
| Instructions aimed at the agent | Part of the transcript | **Never** (`instruction_like_content`) | Not applicable |

The reason codes match the sample's write policy (architecture [section 8.2](architecture.md#82-memory-write-policy)). For Foundry Memory, also state the exclusions in the store's profile guidance, but treat that as a second layer: the write policy runs first, in your code, before anything reaches the extraction model.

## 8. Retention, time to live and the erasure workflow

**Retention by item type.** The values below are the sample's illustrative settings, shown to make the structure concrete. Set real values from your records-retention policy and each Domain Pack's regulation.

| Item | Store | Retention mechanism | Sample setting | Refreshed by |
|---|---|---|---|---|
| Thread head | Cosmos DB | Item `ttl` | 14 days | Each checkpoint |
| Message | Cosmos DB | Item `ttl` | 14 days from creation | Not refreshed |
| Checkpoint | Cosmos DB | Item `ttl` | 14 days from creation | Not refreshed |
| Idempotency record | Cosmos DB | Item `ttl` | 2 days | Not refreshed |
| Memory | Memory store | Store or record time to live | 180 days | Supersession creates a new record |
| Consent record | Your consent store | Life of the account, plus your legal hold policy | Not modeled | Grant or revoke |
| Audit event | Append-only audit store | Your audit retention policy | Not modeled | Never modified |
| Erasure receipt | Append-only audit store | As long as you must prove erasure | Not modeled | Never modified |

**Erasure workflow.**

1. A data-subject request arrives through a verified channel: the user themselves, or a privacy officer acting in the same tenant.
2. The privacy workflow calls the erasure coordinator with a `requestId`, the requester's token and, for officer requests, the target's directory object id.
3. The coordinator authorizes the request (architecture [section 11.1](architecture.md#111-authorization)), then drops queued candidates, erases the memory scope and sets the watermark, erases thread state, revokes consent and verifies zero remaining items (architecture [section 11.2](architecture.md#112-order-of-operations)).
4. It writes an `erasure_completed` audit event and stores the receipt: pseudonymous subject and requester references, counts per store, `verified`, and completion time. The receipt contains no content.
5. Downstream owners (analytics copies, change-feed consumers, search indexes) receive an erasure event keyed by the subject reference and confirm their own deletes.
6. Any receipt with `verified: false` opens an incident. It is not reported to the user as complete.

A replay of the same `requestId` returns the stored receipt and records `erasure_replayed`. A denied request records `erasure_denied` with the reason and without the target's identifier.

## 9. Logging without leaking memory

Logs, traces and audit events are copies of data. If they contain memory text or messages, they become an unmanaged memory store with a different retention policy, different readers and no erasure path.

| Rule | How |
|---|---|
| Allow-list fields | The sample's `AuditLog` accepts only the fields in `AUDIT_FIELDS` and raises on anything else. Apply the same allow-list in your logging wrapper, not a deny-list |
| Identifiers, not content | Log thread ids, turn ids, memory ids, categories, topics, reason codes, counts and outcomes. Never message text, memory text, checkpoint state or tool results |
| Pseudonymous subjects | Log `subject_ref`, not object ids, user principal names or email addresses |
| Hints by name only | `hint_ignored` records which header or field was present, never its value |
| Blocked writes without text | `memory_write_blocked` records category, topic and reason. The blocked text is exactly the sensitive content you must not keep |
| Tracing | Disable prompt and completion capture in production tracing, or route it to a store with the same retention, access and erasure as thread state. Check the default for each SDK and exporter you use |
| Recalled memory in prompts | Prompts may contain recalled memory. Treat prompt logs as thread-state data, or do not keep them |
| Tests that enforce it | Scenario `13 audit_has_no_content` fails if any audit event contains message or memory text |

## 10. Encryption and customer-managed keys

- **At rest:** Cosmos DB, Azure AI Search, PostgreSQL and Foundry encrypt data at rest with service-managed keys by default.
- **Customer-managed keys:** where policy requires them, configure customer-managed keys in Key Vault or Managed HSM for the Cosmos DB account and for any self-hosted memory store. Plan this at account creation and verify whether your configuration supports enabling it later. The account's identity needs key wrap and unwrap rights on the key.
- **Foundry Memory:** confirm whether memory store content is covered by the Foundry resource's customer-managed key configuration before relying on it **(observed in preview; verify against current docs)**. If it is not, and your policy requires customer-managed keys for memory text, use a self-hosted store.
- **Key revocation is not erasure.** Revoking a key makes the whole account unreadable. Per-user erasure still needs the workflow in section 8.
- **In transit:** TLS to every store. Private endpoints keep traffic off the public internet but do not replace TLS.

## 11. Separation of duties

| Duty | Performed by | Cannot also |
|---|---|---|
| Create and configure accounts, containers, memory stores and profile guidance | Platform pipeline and operators | Read conversation content by default |
| Serve user turns | Agent runtime identity | Delete across users; manage stores; read the audit store |
| Approve and execute erasure | Privacy workflow and erasure coordinator identity | Serve user turns; modify audit records |
| Change the memory write policy | Policy owners through reviewed pull requests, with evaluation results | Grant themselves data-plane read access |
| Read audit and investigate | Security operations | Change retention, policy or role assignments |
| Diagnose a specific conversation | Support, through just-in-time elevation with a recorded reason | Hold standing read access to thread state |

## 12. Threat-to-control mapping

| Threat | Primary control | Supporting controls | Evidence |
|---|---|---|---|
| Forged tenant or user hint selects another tenant's thread | Keys from `Principal` only | Owner check on read; `hint_ignored` audit | Scenarios `01`, `02` |
| Missing or invalid token falls back to a default user | Fail closed with `unverified_principal` | Sealed principal type | Scenario `03` |
| One user's memory recalled for another user | Per-user, per-tenant scope; server-side scope filter | Scope tests per backend | Scenario `04`, Northwind `same_user_id_separate_across_tenants` |
| Credentials, card data or clinical details written to memory | Write policy before every write | Profile guidance; evaluation of blocked items | Scenario `09`, Fabrikam and Northwind packs |
| Memory poisoning with instructions | `instruction_like_content` rule; extract from user turns only | Label recalled memory as data in the prompt | Scenario `09` |
| Erased memory reappears from an in-flight write | Drop queue first; erasure watermark | Verification step | Scenario `07` |
| Leaked keys give full account access | Local authentication disabled | Azure Policy audit; no keys in configuration | Deployment checks |
| Logs become a shadow memory store | Audit allow-list | Tracing content capture off | Scenario `13` |

Broader agent threats (prompt injection, tool misuse, credential leakage) are mapped in [Enterprise Agent Security and Tool Governance](../../../security-and-governance/agent-security-and-governance/README.md).

## 13. Governance checklist

- [ ] Local (key) authentication is disabled on every store and audited by policy.
- [ ] Data-plane roles are scoped to the container or project, and the agent runtime has no delete or management rights it does not need.
- [ ] Public network access is disabled on the state store; the memory store's network posture is recorded and accepted for each Domain Pack.
- [ ] The memory extraction identity can use the chat and embedding deployments, and an update-then-search check runs after every deployment.
- [ ] Partition keys and memory scopes come only from the verified principal; the scope uses a keyed hash with a stable key.
- [ ] Consent is recorded per user and checked before every memory write; users can see and delete what is remembered.
- [ ] The write policy, its reason codes and its evaluation set are versioned and owned.
- [ ] Retention is set per item type from the records policy; erasure is executed, verified and receipted across both stores and downstream copies.
- [ ] Logs, traces and audit events use an allow-list and contain no message or memory text.
- [ ] Customer-managed keys are configured where policy requires them, including a decision for the memory store.
- [ ] The separation of duties in section 11 is enforced by role assignments.

## 14. References

- [Role-based access control with Microsoft Entra ID for Azure Cosmos DB for NoSQL](https://learn.microsoft.com/azure/cosmos-db/nosql/security/how-to-grant-data-plane-role-based-access)
- [Configure Azure Private Link for an Azure Cosmos DB account](https://learn.microsoft.com/azure/cosmos-db/how-to-configure-private-endpoints)
- [Configure customer-managed keys for your Azure Cosmos DB account](https://learn.microsoft.com/azure/cosmos-db/how-to-setup-customer-managed-keys)
- [Time to live in Azure Cosmos DB](https://learn.microsoft.com/azure/cosmos-db/time-to-live)
- [Memory in Foundry Agent Service](https://learn.microsoft.com/azure/foundry/agents/concepts/what-is-memory)
- [Agent identity in Foundry](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity)
- Sibling assets: [Request Context Is Not Authorization](../../request-context-is-not-authorization/README.md), [Production Hosted Agents on Microsoft Foundry](../../production-hosted-agents-on-foundry/README.md), [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md), [Enterprise Agent Security and Tool Governance](../../../security-and-governance/agent-security-and-governance/README.md)
