# Authentication and Governance

This document defines who and what authenticates when an agent runs generated code in Azure Container Apps dynamic sessions, which roles each identity holds and at what scope, why no secret may enter a session, how session identifiers are keyed and rotated, and which governance controls keep the pattern honest over time. It assumes the architecture in [Architecture](architecture.md) and the landing-zone identity baseline in [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md); it does not repeat platform deployment identities.

Role names, token audiences, pool settings and the identities that Foundry creates for hosted agents are preview details that have changed over time. Treat every role name, audience and setting below as **verify against current Microsoft Learn documentation** before you assign or rely on it. Role definition IDs are deliberately not listed; pass them as parameters to your infrastructure code after looking them up.

## Contents

1. [Principles](#1-principles)
2. [Identities](#2-identities)
3. [Role assignments](#3-role-assignments)
4. [No secrets into sessions](#4-no-secrets-into-sessions)
5. [Caller identity and user context](#5-caller-identity-and-user-context)
6. [Tenant isolation: per-tenant pools or a shared pool](#6-tenant-isolation-per-tenant-pools-or-a-shared-pool)
7. [Derivation key management](#7-derivation-key-management)
8. [Data residency and retention](#8-data-residency-and-retention)
9. [Audit](#9-audit)
10. [Approval for side-effecting code](#10-approval-for-side-effecting-code)
11. [Policy as code](#11-policy-as-code)
12. [Threats and controls](#12-threats-and-controls)
13. [Related](#13-related)

## 1. Principles

1. **The session has no identity worth stealing.** Generated code runs where there is no managed identity endpoint, no agent token and no secret. The caller authenticates to the pool; the code inside the session does not authenticate to anything.
2. **One principal executes.** Only the sandbox broker's managed identity holds the session executor role on a pool. Agents, people and pipelines go through the broker so that identifier derivation, egress policy, output handling and audit cannot be bypassed.
3. **Identifiers are derived, never supplied.** The broker computes the session identifier from the authenticated tenant, user and conversation with a keyed hash. Neither the caller nor the model can choose or guess it.
4. **Context is not authorization.** Tenant, user and conversation values feed the identifier only after the broker has authenticated the request. A tool argument that names a user is data, not identity.
5. **Separate identities for separate duties.** Executing code, pulling images, deploying pools, rotating keys and reading audit each use a different principal.
6. **Approval sits outside the sandbox.** Code may propose a change to a system of record; only the approval workflow can apply it, with its own identity.

## 2. Identities

| Identity | Kind | Lives where | Authenticates to | Must never |
| --- | --- | --- | --- | --- |
| **Broker identity** | User-assigned managed identity | The broker's Container App, Functions app or hosted agent module | Session pool data plane (sessions audience), Key Vault (derivation key and any MCP connection key), audit sink | Be readable from inside a session; hold control-plane roles on the pool; hold data-plane roles on systems of record |
| **Pool identity** | User-assigned managed identity attached to the pool | The session pool resource | Container registry (pull custom images); optionally a reviewed egress target if the platform supports identity there (verify) | Be exposed to code in sessions; push images; reach agent data stores |
| **Agent identity** | Foundry project identity, hosted agent instance identity or the app identity of another runtime | The agent | The broker (as a tool or module), model deployments | Hold the session executor role directly; pass its tokens into code |
| **End user** | Microsoft Entra user or external identity | Client, web tier, gateway | Your application and gateway | Supply a session identifier; reach the pool |
| **Pool deployer** | Pipeline managed identity or workload identity federation | CI/CD environment | Azure Resource Manager (create and update pools, read them back) | Execute code; hold the session executor role |
| **Key custodian** | Entra group or pipeline identity with just-in-time elevation | Key Vault | Key Vault (create key versions) | Read audit content; execute code |
| **Operators** | Entra group, read-mostly | Operator network | Log Analytics and audit store (read), broker admin endpoints (drain, delete sessions) | Execute arbitrary code in user sessions; read raw identifiers |

The broker identity is the only identity that both sees end-user context and can reach the pool. Treat it as a high-value principal: dedicated to this purpose, no other roles, alerts on any new role assignment.

## 3. Role assignments

All names are built-in role names as published at the time of writing. Verify the current name, scope and permissions before assignment. Some documentation pages show broader roles (for example Contributor) in quick starts; do not use those in production.

| Identity | Scope | Role (verify current name) | Why |
| --- | --- | --- | --- |
| Broker identity | The specific session pool | Azure ContainerApps Session Executor | Execute code, upload and download files, delete sessions on that pool only |
| Broker identity | The Key Vault holding the derivation key | Key Vault Crypto User (key-based HMAC) or Key Vault Secrets User (secret-based key material) | Read or use the derivation key; choose one model and assign only that role |
| Broker identity | The Key Vault secret holding an MCP connection key, if you use the built-in MCP endpoint | Key Vault Secrets User at secret scope | Configure the MCP tool connection without exposing the key to the model |
| Broker identity | Audit sink (Log Analytics data collection rule or storage) | Monitoring Metrics Publisher or a write-only data role | Append audit records; never read or delete them |
| Pool identity | The container registry for custom images | AcrPull | Pull the session image by digest |
| Pool deployer | Resource group containing the pool | A narrowly scoped deployment role, constrained to Container Apps resources | Create and update pools; read configuration back after deployment |
| Role-assignment identity | Resource group or the pool | Role Based Access Control Administrator, constrained by conditions to the roles in this table | Grant the broker its pool role without broad rights |
| Operators | Log Analytics, audit store | Log Analytics Reader, Monitoring Reader | Investigate incidents without execution rights |
| Agent identity | The broker's front (MCP or OpenAPI) | An app role defined by the broker | Call `run_code`, `upload`, `download`, `end_conversation` |

Nobody else holds a data-plane role on the pool. The simulator models this with `RoleAssignments`: a call from an identity without the role returns `Forbidden` (403), a token for the wrong audience returns `Unauthorized` (401), and an anonymous call is rejected before any session is touched (scenario 6). The token audience used for sessions in current documentation is a dedicated dynamic sessions audience requested with the `/.default` scope (verify against current Microsoft Learn documentation); the broker requests only that scope, never a broad management or storage scope.

## 4. No secrets into sessions

No credential of any kind enters a session: not in code, not in uploaded files, not in environment variables, not in the custom image and not in egress configuration. The reasons are structural, not stylistic:

- **The code is attacker-influenced.** Anything a session can read, a prompt injection can instruct the code to print, encode into a chart, write into a downloadable file or send to an allowed host.
- **Outputs leave the sandbox.** Stdout and files flow back to the model, which may quote them to the user or pass them to other tools. A secret in a session is one `print` away from the transcript.
- **Sessions outlive turns.** A secret uploaded in turn one is still on disk in turn five, and still there until cooldown or delete.
- **Session identifiers are bearer-like.** Anyone who obtains an identifier and the executor role can run code in that session; a secret stored there widens the blast radius of that compromise.

Controls, as modelled by the simulator:

| Control | Where | Simulator scenario |
| --- | --- | --- |
| Pre-screen blocks credential-shaped content (storage keys, signed URL signatures, private key blocks, bearer tokens, literal credential assignments) with `SecretInCode`; the code never reaches the pool | Broker | 27 |
| Audit records name the matched pattern, never the value; rejections are audited | Broker | 27 |
| Identity-related environment variables read as unset inside the session; the broker requests only the sessions scope | Pool and broker | 29 |
| Environment reads are logged as a risk indicator, not blocked | Broker | 29 |
| Output guard redacts the same patterns in stdout and stderr | Broker | 13 |

If code genuinely needs data behind a credential, the broker (or the agent's tool layer) fetches that data with its own identity and data controls, then uploads only the needed subset to the session. The session never receives the means to fetch it.

## 5. Caller identity and user context

The identifier's inputs must be authenticated facts:

- **Hosted agents.** The container receives the caller's identity and conversation from the platform or from the gateway in front of it. The broker module reads them from the authenticated request context, never from model-written tool arguments.
- **Prompt agents.** The broker's tool front needs the end user's identity propagated to it, for example through an MCP gateway that validates the user token and forwards trusted claims. See [Backend Identity Propagation](../../../ai-gateway/backend-identity-propagation/README.md) and [Multi-Tenant MCP Gateway](../../../ai-gateway/multi-tenant-mcp-gateway/README.md).
- **Fail closed.** If the tenant, user or conversation is missing, empty or malformed, the broker rejects the call with `PolicyRejected`. It never falls back to a shared or default identifier, because a shared identifier is a shared filesystem.
- **Reject supplied identifiers.** A request that carries any session identifier is rejected with `ClientSuppliedSessionId`, even if the value is valid.

Platform-provided headers and metadata that reach the broker are correlation hints until the broker has validated them against a signed token or a trusted gateway.

## 6. Tenant isolation: per-tenant pools or a shared pool

| Option | Isolation | Operations | Choose when |
| --- | --- | --- | --- |
| **Shared pool, per-tenant identifiers** | Each session is isolated by the platform; tenants never share a session because the tenant is part of the derivation material | One pool per environment and image; capacity is shared, so one tenant's burst can cause capacity rejections for others | Most multi-tenant SaaS agents with the same egress posture and package set for every tenant |
| **Per-tenant pools** | Separate pool resources, separate pool identities, optionally separate regions and networks | One pool per tenant per image; more deployments, more capacity planning, more images to patch | Contractual or regulatory isolation, per-tenant egress allowlists, per-tenant data residency, or noisy-neighbour concerns |
| **Per-tier pools** | Group tenants by sensitivity or region | A handful of pools | Mixed portfolios where a few tenants need a stricter posture |

In every option the identifier still includes the tenant. The simulator shows that two tenants using identical user and conversation IDs receive different sessions, that a tenant ID containing the separator character is rejected with `PolicyRejected`, and that rotating the derivation key changes every identifier (scenario 3).

Per-tenant pools do not remove the need for per-user identifiers inside a tenant: two users of the same customer must not share a filesystem either.

## 7. Derivation key management

The derivation key is the only thing standing between an attacker who knows a tenant, user and conversation ID and that user's session identifier. Manage it like a signing key.

- **Store it in Key Vault.** Either as a Key Vault key used for HMAC operations where supported, or as random key material in a secret that the broker loads into memory (verify which HMAC options your Key Vault tier supports). Generate it with a cryptographically secure random source; never derive it from a password or a configuration value.
- **One key per environment.** Development, test and production use different keys, so an identifier computed in one environment means nothing in another.
- **Version it.** Prefix the derivation material with a format version (`v1` in this pattern) and record the Key Vault key version alongside every identifier the broker tracks for cleanup. A new format or key version must be a deliberate change.
- **Rotate at conversation boundaries.** Rotation remaps every conversation to a new identifier, and a new identifier is a new, empty session. Either rotate when no conversations are in flight, or keep the previous key version available and continue to derive with the version recorded for each open conversation until it closes. New conversations use the new version.
- **Retire old versions on a schedule.** Once every conversation that used a version has closed or exceeded the maximum lifetime, disable that version and run the orphan sweep for its identifiers.
- **Rotate immediately on suspected exposure.** Accept the state loss. Surface it to users through the broker's `stateLost` notice, the same path used for cooldown expiry.
- **Reject separator smuggling.** Any input containing the separator character is rejected before hashing, so crafted IDs cannot collide with another user's material.
- **Never log the key or raw inputs.** Audit records carry a short hash of the identifier (`sessionRef`) and hashed tenant and user references, never the identifier, the key or the raw values.

The simulator's `StaticDerivationKeyProvider` holds one demo key behind the `DerivationKeyProvider` protocol; scenario 3 derives with an alternate key to show that rotation changes every identifier. A production provider reads the current version from Key Vault, caches it briefly and exposes earlier versions for in-flight conversations.

## 8. Data residency and retention

- **Region.** Create pools in the same region (or regulatory boundary) as the data users upload. A shared global pool can quietly move regulated data across a boundary.
- **Session storage is transient.** Files under the session data root live until cooldown, maximum lifetime or explicit delete. Treat that as a cache with a short, bounded life, not as storage with a retention policy.
- **Delete early.** Call the delete operation when a conversation ends and audit it. Use the maximum-lifetime lifecycle (where available; verify) for regulated workloads so a busy session cannot live indefinitely.
- **What you keep, you govern.** Files the user needs are downloaded through the output guard and stored by the application with its own classification, encryption and retention. The audit trail keeps hashes and metadata, not content.
- **Custom images.** Bake no data into images. Images are shared by every session in the pool.

## 9. Audit

Every upload, execution, download, delete and refused request produces an audit record from the broker. The platform's own logs are useful for diagnosis but are not the audit trail: they do not know which user or conversation a session belongs to.

| Field | Content | Why |
| --- | --- | --- |
| `correlationId` | Broker-issued, or the upstream correlation ID when one is supplied | Join with agent, gateway and approval traces |
| `event` | `upload`, `execute`, `download`, `delete`, `rejected` | Reconstruct the session's history |
| `tenantRef`, `userRef`, `sessionRef` | Short keyed or plain hashes | Investigate one user's activity without storing identifiers |
| `pool`, `poolType` | Logical pool name and type | Tie the event to configuration and image |
| `codeSha256`, `codeBytes` | Hash and size of submitted code | Prove what ran without storing code that may contain user data |
| `findings` | Pre-screen indicators and blocking pattern names | Detection and review |
| `outcome`, `errorCode` | Broker status and taxonomy code | Dashboards and alerts |
| `stateLost`, `stdoutTruncated`, `quarantined` | Booleans and counts | Quality and abuse signals |
| `platformTraceId` | The platform's trace ID from error envelopes, if any | Support cases |

The simulator asserts that every audit record has a unique correlation ID, that the code hash is present, that raw tenant, user, conversation and identifier values never appear, and that an upstream correlation ID is honoured (scenario 31). Write audit to an append-only destination that the broker can write but not modify or delete.

## 10. Approval for side-effecting code

The sandbox has no route to systems of record, by design. When analysis concludes that something should change (raise a credit limit, reorder stock, flag a claim), the code emits a structured proposal in its output rather than performing the change. The broker turns each proposal into a ticket for an approval gate:

1. The proposal is parsed as data, validated against a schema and tagged with its source (`sandbox-output`) and the correlation ID.
2. A deterministic check re-verifies the inputs from the system of record, not from sandbox output.
3. A human or a policy approves or rejects.
4. Only the approval workflow, with its own identity and its own role on the target system, applies the change.

The broker's gate interface has no apply method; scenario 30 asserts that a proposal creates a ticket and that nothing else happens. The full pattern is in [Verify Before Approve](../../../orchestration-and-workflows/verify-before-approve/README.md).

## 11. Policy as code

Governance controls that should be enforced by the platform rather than by convention (adapt to the policy aliases available for your resources; verify current Azure Policy support for Container Apps session pools):

| Control | Enforcement idea | Effect |
| --- | --- | --- |
| Egress disabled on session pools unless an exemption exists | Policy on the pool's network setting | Deny or audit |
| No broad roles on session pools | Policy or a scheduled query on role assignments at pool scope; alert on anything other than the session executor role held by the broker identity | Audit and alert |
| Allowed regions | Built-in allowed-locations policy at the resource group | Deny |
| Custom images only from the approved registry, by digest | Policy on the pool's container image reference; pipeline gate rejects tags | Deny |
| Pool identity limited to pull | Scheduled role-assignment review | Audit |
| MCP disabled on custom-container pools, and read back after deployment | Post-deployment assertion in the pipeline | Fail the release |
| Lifecycle configured (cooldown and, for regulated tiers, maximum lifetime) | Post-deployment assertion | Fail the release |
| Derivation key rotation | Key Vault rotation policy plus a broker readiness check that the configured version exists | Alert |

Post-deployment read-back matters because preview resource providers can accept a setting and not apply it **(observed in preview; verify against current Microsoft Learn documentation)**.

## 12. Threats and controls

| Threat | Primary control | Secondary control |
| --- | --- | --- |
| Prompt-injected code mints the agent's tokens | Out-of-process execution in an isolated session ([ADR 0001](decisions/0001-never-execute-generated-code-in-process.md)) | Broker requests only the sessions scope |
| Code exfiltrates data over the network | Egress disabled by default ([ADR 0002](decisions/0002-one-session-per-user-conversation-egress-off-by-default.md)) | Allowlist with exact hosts and a filtering resolver |
| Code reaches instance metadata or the wire server | Session isolation; no agent identity in the session | Broker denies metadata and local addresses in every mode |
| One user reads another user's files | Per tenant, user and conversation identifiers derived with HMAC | Supplied identifiers rejected; audit by `sessionRef` |
| Attacker guesses an identifier | HMAC with a Key Vault key | Executor role held only by the broker |
| Secrets leak through output | No secrets in sessions | Output redaction; audit of matches |
| Compromised broker identity | Single-purpose identity with one role at pool scope | Alerts on role changes; key in Key Vault with separate custodian |
| Malicious package in a custom image | Image pipeline with pinned dependencies, scanning and digest pinning | Egress disabled limits what a malicious package can reach |
| Code triggers real-world side effects | No write path from sandbox; proposals become approval tickets | Deterministic re-verification before apply |
| Resource exhaustion blocks other users | Per-session isolation and platform limits | Capacity alerts; broker-side code and output size limits |

## 13. Related

- [Architecture](architecture.md)
- [Evaluation and troubleshooting](evaluation-and-troubleshooting.md)
- [ADR 0001: Never execute generated code in-process](decisions/0001-never-execute-generated-code-in-process.md)
- [ADR 0002: One session per user conversation, egress off by default](decisions/0002-one-session-per-user-conversation-egress-off-by-default.md)
- [ADR 0003: Built-in pool for MCP, custom container for packages](decisions/0003-built-in-pool-for-mcp-custom-container-for-packages.md)
- [Production Hosted Agents on Foundry](../../production-hosted-agents-on-foundry/README.md)
- [Agent Security and Governance](../../../security-and-governance/agent-security-and-governance/README.md)
- [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md)
