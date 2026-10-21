# Authentication and Governance

This document defines who and what authenticates in a production hosted agent deployment, which roles each identity holds and at what scope, how duties are separated, how image provenance is controlled, and which threats the design addresses. It builds on the identity guidance in the [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/docs/deployment-and-cicd.md) and does not repeat the landing zone's platform deployment identities.

Built-in role names and the identities Foundry creates for hosted agents are preview details that have changed over time. Treat every role name below as **verify against current Microsoft Learn documentation** before you assign it.

## Principles

1. **Microsoft Entra ID everywhere, keys nowhere.** Every dependency that supports Entra authentication has local (key) authentication disabled. No key-based fallback exists in code or configuration.
2. **Separate identities for separate duties.** The pipeline, the agent runtime, the image pull path, operators and callers each use a different principal.
3. **Data-plane roles at the narrowest scope.** The runtime identity never holds control-plane roles. Scopes are the project, the specific registry, the specific Cosmos DB account (or database), and the specific Application Insights resource.
4. **Context is not authorization.** Headers, metadata and platform-injected caller identifiers that reach the container are correlation hints. Authorization happens at the gateway and the tools.
5. **Provenance before deployment.** Only images built by the approved pipeline, from approved base images, scanned and pushed by digest to the private registry, can become agent versions.

## Identities

| Identity | Kind | Lives where | Authenticates to | Must never |
|---|---|---|---|---|
| **Deployer (pipeline identity)** | User-assigned managed identity federated to the CI environment, or the in-network runner's managed identity | Operator VNet runner or Container Apps job | Private registry (push), Foundry project data plane (create versions, smoke turns, switch active version) | Hold Owner or User Access Administrator; run from outside the network |
| **Role-assignment identity** | Separate managed identity or a privileged access workflow | Platform pipeline | Azure Resource Manager, to grant the runtime identity its data-plane roles | Deploy agent versions or invoke agents |
| **Agent runtime identity** | The hosted agent's instance identity created by Foundry, or a customer-managed identity where the platform supports it | Inside each session sandbox | Model deployments, Cosmos DB, MCP gateway, telemetry ingestion | Hold control-plane roles; hold roles on other agents' data |
| **Image pull identity** | The identity the platform uses to pull the image (the project's managed identity in current docs; verify) | Foundry project | Private registry (pull) | Push images |
| **Caller identity** | End user (through a gateway) or a calling application's managed identity | Client, web tier or gateway | Gateway and Foundry project data plane (invoke) | Reach the project from outside the network |
| **Operator** | Entra group, with just-in-time elevation for write actions | Operator VNet | Application Insights and Log Analytics (read), project data plane (inspect versions, list and end sessions) | Push images or create versions outside the pipeline |

### Agent identity in preview

When you inspect a hosted agent version, the platform reports an agent identity blueprint and an instance identity **(observed in preview; verify against current docs)**. The instance identity is the principal your code authenticates as. It starts with no data-plane roles. Before relying on it:

- Confirm whether the instance principal is stable across versions in your environment. If it changes, the role-assignment stage must run for every new version, before the smoke turn, and old assignments must be removed when the version is retired.
- Record the principal object id per version in the release record so auditors can map telemetry back to an identity.
- Where a customer-managed identity is supported for the runtime, prefer it: its lifecycle and role assignments are then independent of version churn.

## Role assignments

All names are built-in role names as published at the time of writing. Verify the current name, scope and permissions before assignment. The illustrative [`infra/main.bicep`](../infra/main.bicep) takes role definition ids as parameters instead of hardcoding them.

| Identity | Scope | Role (verify current name) | Why |
|---|---|---|---|
| Deployer | Private container registry | AcrPush | Push images by digest from the in-network runner |
| Deployer | Foundry project | Foundry User (formerly named Azure AI User) | Create agent versions, list versions, run smoke turns, switch the active version, manage sessions |
| Role-assignment identity | Resource group or specific resources | Role Based Access Control Administrator, constrained by conditions to the roles listed in this table | Grant and remove the runtime identity's data-plane roles without broad rights |
| Agent runtime | Foundry account (model deployments) | Cognitive Services OpenAI User | Call model deployments; also observed to be needed for memory writes in preview |
| Agent runtime | Foundry project | Foundry User, only if the agent calls other Foundry project services | Call project-level services such as other agents or evaluations |
| Agent runtime | Cosmos DB account or database | Cosmos DB Built-in Data Contributor (a Cosmos DB SQL data-plane role, assigned through Cosmos DB role assignments rather than Azure RBAC) | Read and write threads, checkpoints and idempotency records |
| Agent runtime | Application Insights | Monitoring Metrics Publisher | Only when telemetry ingestion uses Entra authentication |
| Agent runtime | MCP gateway application | App role defined by the gateway | Call tools; the gateway maps the caller |
| Image pull identity | Private container registry | AcrPull | Pull the agent image into session sandboxes |
| Caller application | Foundry project | Foundry User | Invoke the agent through the private endpoint |
| Operators | Application Insights, Log Analytics workspace | Monitoring Reader, Log Analytics Reader | Read traces, metrics and logs |
| Operators (elevated, just in time) | Foundry project | Foundry User | List and end sessions, inspect versions, stream session logs |

Notes:

- A missing role on the runtime identity appeared as an authentication error (401) from the model or Cosmos DB client, not as a 403, in preview testing. The smoke turn catches it; the pipeline should report it as "role missing", not "deploy failed".
- Role assignments take time to propagate. The pipeline waits and retries the smoke turn a bounded number of times after granting roles; it does not loop indefinitely.
- Never grant Contributor, Owner or Cognitive Services Contributor to the runtime identity. Those are control-plane roles and do not grant data access anyway.

## Separation of duties

| Duty | Performed by | Cannot also |
|---|---|---|
| Author agent code and prompts | App team, through pull requests | Approve their own production promotion |
| Build, scan, push, create version, smoke test | Pipeline identity | Grant roles; change network settings |
| Grant runtime roles | Role-assignment identity, through a reviewed definition | Deploy or invoke agents |
| Approve production promotion | Release approver (and model risk reviewer where required) | Change the pipeline definition in the same change |
| Operate sessions and diagnostics | Operators, with just-in-time elevation | Push images or create versions |
| Change network, DNS and private endpoints | Platform team, through the landing zone pipeline | Deploy agent versions |
| Change telemetry content-recording settings | Operations with data owner approval | Read content they enabled without an access review |

The landing zone already separates platform deployment from agent release; this asset adds the runtime-role grant as its own duty, because a pipeline that can both deploy code and grant that code new data access can escalate privilege in one step.

## Caller identity and tools

The container does not receive the caller's access token. The platform forwards a caller identifier that names the **calling principal**; when a web tier calls the agent with its own managed identity, that is the web tier, not the end user **(observed in preview; verify against current docs)**. Do not use it for authorization.

When tools must act as, or on behalf of, the end user:

1. A gateway in front of the agent authenticates the user.
2. The gateway passes a signed, audience-bound assertion or a reference the tools can validate.
3. The agent calls tools through the MCP gateway with its own runtime identity, carrying the user context.
4. The tools or the gateway authorize against the user context.

The patterns, trade-offs and simulator for this flow are in [Backend Identity Propagation](../../../ai-gateway/backend-identity-propagation/README.md) and [Multi-Tenant MCP Gateway](../../../ai-gateway/multi-tenant-mcp-gateway/README.md). This asset does not duplicate them.

## Image provenance and supply chain

| Control | Implementation | Verified by |
|---|---|---|
| Approved base images | A short list of minimal base images, mirrored into the private registry and refreshed on a schedule | Build fails if the base is not on the list |
| Reproducible build | Dependencies pinned with hashes; build on the in-network runner | Two builds of the same commit produce the same dependency set |
| Vulnerability and secret scan | Scanner in the pipeline; secret scanning on source and image layers | Gate on unaccepted critical findings and any secret |
| Software bill of materials | Generated per image and stored with the release record | Attached to the release |
| Signing (optional) | Sign the digest with a signing service and verify before creating the version. Whether the platform verifies signatures at pull time is not documented for hosted agents at the time of writing; verify in your pipeline instead | Pipeline verification step |
| Digest pinning | Agent versions reference `image@sha256:<digest>`, never a mutable tag | Release record and version inspection |
| Registry hardening | Public network access disabled, admin user disabled, anonymous pull disabled, retention that excludes digests referenced by active and rollback versions | Policy and pipeline checks |
| Code package path | Not used in production; storage and retention of uploaded code are not documented at the time of writing | Policy |

## Governance

### Agent version approval

- Every production version is created by the pipeline from a reviewed commit.
- The release record stores: commit, image digest, scan results, SBOM reference, evaluation scores, runtime principal id, approver and the rollback target.
- Production promotion requires an approval that is not the author. Regulated workloads add a model risk or compliance approval.
- Versions that are no longer active or a rollback target are retired on a schedule, and their role assignments are removed if the instance identity is per version.

### Azure Policy ideas

Built-in policy coverage for Foundry hosted agents is limited at the time of writing. The following are candidates; verify which built-in definitions exist and which need custom definitions.

| Policy intent | Target |
|---|---|
| Deny public network access | Foundry accounts, container registries, Cosmos DB accounts |
| Deny local authentication | Foundry accounts, Cosmos DB accounts |
| Deny registry admin user and anonymous pull | Container registries |
| Require private endpoints | Container registries, Cosmos DB accounts, Foundry accounts |
| Require diagnostic settings to Log Analytics | Foundry accounts, registries, Cosmos DB accounts |
| Audit role assignments of control-plane roles to agent runtime principals | Role assignments |
| Require approved regions | All resources in the landing zone |

### Telemetry governance

- Content recording (prompts, completions, tool payloads) is off in production by default. Turning it on is a change with a data owner's approval and a fixed end date.
- Telemetry retention and access follow the most sensitive data the workload can record.
- If a custom OpenTelemetry collector is used, it is private, allowlisted and in scope for the same reviews as the data stores.

## Threat model

| Threat | Vector | Impact | Mitigation |
|---|---|---|---|
| Data plane exposed publicly | Public network access re-enabled to unblock a deploy | Anyone with a token can invoke or change agents | In-network pipeline; policy denies public access; alerts on configuration drift |
| Leaked key | Key-based auth on a dependency | Data access without audit | Local auth disabled; no keys in code, config or images |
| Over-privileged runtime | Broad roles granted to fix a 401 | Prompt injection turns into wide data access | Minimal data-plane roles; role-assignment identity limited by conditions; audits |
| Privilege escalation through the pipeline | Pipeline grants its own code new roles | Unreviewed data access | Separate role-assignment identity and review |
| Malicious or vulnerable image | Compromised base image or dependency | Code execution with the runtime identity | Approved bases, pinned dependencies, scanning, optional signing, digest pinning |
| Tag substitution | Mutable tag repointed to a different image | Unreviewed code runs | Digest pinning in versions |
| Caller spoofing | Trusting forwarded headers or metadata as the user | Cross-user data access | Gateway authentication and identity propagation; headers are hints only |
| Data exfiltration through egress | Prompt-injected tool call to an attacker host | Data loss | Egress allowlist with logging; no TLS inspection exemption beyond identity endpoints |
| Content leakage through telemetry | Prompt and completion capture left on | Sensitive data in a broadly readable store | Content recording off by default; retention and access controls |
| Stale vulnerable version keeps running | Sessions not drained after a security fix | Exposure window remains | Drain sessions on rollout; verify version in spans |
| Session leakage and cost abuse | Unpinned retries or abandoned sessions | Cost and capacity exhaustion | Pinning, idempotency, explicit end, sweeper, alerts on session counts |
| Untrusted code in the agent process | Model-generated code executed in the container | Code runs with the agent identity | Run code in [session pools](../../code-execution-sandbox-session-pools/README.md) |

## Related

- [Architecture](architecture.md)
- [Evaluation and troubleshooting](evaluation-and-troubleshooting.md)
- [ADR 0002: Agent identity gets least-privilege data-plane roles](decisions/0002-agent-identity-gets-least-privilege-data-plane-roles.md)
- [ADR 0004: Cache tokens per audience](decisions/0004-cache-tokens-per-audience.md)
- [Agent Security and Governance](../../../security-and-governance/agent-security-and-governance/README.md)
- [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md)
- [Azure built-in roles](https://learn.microsoft.com/azure/role-based-access-control/built-in-roles)
- [Configure role-based access control for Azure Cosmos DB for NoSQL](https://learn.microsoft.com/azure/cosmos-db/nosql/security/how-to-grant-data-plane-role-based-access)
