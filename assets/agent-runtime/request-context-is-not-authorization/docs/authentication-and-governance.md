# Authentication and Governance

This document covers which identities exist in a context-safe agent system, which credential each tool should receive, how tools validate tokens, how roles and policy are governed, and how decisions are audited. It assumes the architecture in [architecture.md](architecture.md). It does not repeat the role assignments for running a hosted agent, which are in [Production Hosted Agents on Microsoft Foundry](../../production-hosted-agents-on-foundry/docs/authentication-and-governance.md), or the token exchange mechanics, which are in [Backend Identity Propagation](../../../ai-gateway/backend-identity-propagation/README.md).

Role names, claim names and Foundry forwarding behavior change between releases. Statements marked **(observed in preview; verify against current docs)** come from testing, not a documented contract.

## 1. Principles

1. **Authentication produces the only authorization facts.** Tenant, user, roles and client application come from a validated credential at the current hop. Everything else is context ([ADR 0001](decisions/0001-derive-authorization-from-verified-identity-only.md)).
2. **Every tool is its own relying party.** A tool validates a token issued for its own audience. It does not trust that someone upstream checked.
3. **Decide at the resource, on every call.** Entitlements are read when the call is made, not when the session started ([ADR 0002](decisions/0002-reauthorize-at-the-resource-boundary.md)).
4. **Deny by default, with a reason.** Every outcome carries a reason code from a fixed, versioned catalog.
5. **The model holds no credentials and makes no decisions.** Tokens never enter prompts, arguments, memory or traces. Instructions are not controls.
6. **Least privilege per identity.** User-scoped work runs as the user. The agent's own identity is for agent-owned work only.

## 2. Identity inventory

| Identity | Kind | Proves | Used to call | Must never |
|---|---|---|---|---|
| **End user** | Entra user, signed in at the client | Who the person is, their tenant, their app roles | The front door | Have their token forwarded into the agent or the prompt |
| **Client application** | Entra app registration (public or confidential client) | Which app requested the token (`azp`) | The front door | Be the only factor in an authorization decision |
| **Front door** | Managed identity of the gateway or web tier | That an authenticated hop called the agent | Foundry project data plane (invoke); the context store (write bindings) | Pass user identity to the agent in headers that tools will trust |
| **Agent** | Entra agent identity or the hosted agent's managed identity | Which agent is calling | Models, its own state stores, app-only tools, the broker | Read user-scoped data with its own token |
| **Broker** | Managed identity of a confidential client | That a governed component exchanged or minted a credential | Entra (OBO), downstream tools | Accept calls from anyone except allow-listed agents |
| **Tool or MCP server** | App registration that defines the audience and app roles; managed identity for outbound calls | Its own identity to downstream data stores | Databases, indexes, APIs | Accept a tenant, user or role from headers or arguments |
| **Operators and policy authors** | Entra groups with just-in-time elevation | Who changed policy, entitlements or configuration | Policy repository, entitlement admin, audit store | Hold the right to change policy and to read or delete audit records at the same time |

[Entra Agent ID](https://learn.microsoft.com/entra/agent-id/identity-platform/what-is-agent-id) gives agents a first-class identity with sponsors and lifecycle governance ([agent identity governance](https://learn.microsoft.com/entra/id-governance/agent-id-governance-overview)). How Foundry assigns identities to agents is described in [agent identity in Foundry](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity). For workloads outside Foundry, use [workload identities](https://learn.microsoft.com/entra/workload-id/workload-identities-overview) (managed identities or federated credentials).

## 3. Choosing the credential for each tool

Classify every tool before choosing how it authenticates. The classification goes into policy as `requires_delegated_user`.

| Question | If yes | If no |
|---|---|---|
| Does the tool return or change data that belongs to a user or to a customer tenant? | User-scoped: the subject must be the user | Possibly app-only; continue |
| Is the data the same for every caller (reference data, public catalog, the agent's own configuration)? | App-only with agent identity | User-scoped |
| Does the action need human accountability (approval, export, refund, deletion)? | User-scoped plus step-up or human approval | Follow the rows above |
| Will the tool run with no user present (scheduled job)? | Agent identity, limited to agent-owned data or to a stored, auditable delegation | Follow the rows above |

Then choose how a user-scoped tool receives a verifiable user. Architecture [section 8.4](architecture.md#84-getting-a-verifiable-user-to-the-tool) lists the options in preference order. In summary:

| Mechanism | What the tool validates | Strength | Cost |
|---|---|---|---|
| **Toolbox or MCP connection with user authentication** | A user token for its audience, presented by the platform | No custom token code in the agent | Only where the connection type supports per-user auth; consent and scope management |
| **[On-Behalf-Of](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow) through a broker** | A delegated token for its audience, with the user as subject and the agent or broker as client | Standard Entra semantics; user and client both visible | The broker needs an inbound user token or a binding it trusts; it is a high-value component |
| **Signed assertion from a broker** | A short-lived, audience-bound assertion signed by the broker | Works where OBO is not available, including non-Entra tools | You own the key, the format and the validation library |
| **Agent identity (autonomous)** | An app-only token for the agent | Simple, no user plumbing | Only for app-only tools; any user-scoped action returns `DELEGATION_REQUIRED` |

Foundry hosted agents do not receive the caller's `Authorization` header **(observed in preview; verify against current docs)**, so a hosted agent cannot perform OBO on its own. The broker resolves the user from a binding that the authenticated front door wrote, keyed by the platform-attested caller id and the session. Design notes:

- Prefer managed identity over OBO, and OBO over keys, for every call that does not need the user.
- The broker allow-lists which agents may call it, which audiences it will issue for, and which scopes per audience.
- Bindings expire, are written only by the front door, and are bound to both the caller id and the session.

## 4. Token validation rules

Every tool, broker and front door validates tokens with a maintained library, not hand-written parsing. The sample's `EntraJwksVerifier` documents the same steps. Apply them in this order and deny with `TOKEN_INVALID` on the first failure. A missing token is `NO_VERIFIED_IDENTITY`.

| # | Check | Rule | Common mistake |
|---|---|---|---|
| 1 | **Presence and format** | Exactly one `Authorization: Bearer` credential; a three-part JWS | Falling back to a cookie, a query string or a header like `x-access-token` |
| 2 | **Algorithm** | Allow-list asymmetric algorithms (Entra access tokens use `RS256`). Reject `none` and any symmetric algorithm. | Trusting the `alg` the token declares |
| 3 | **Signing key** | Look up `kid` in the JWKS from the OpenID configuration for the expected authority. Cache keys; refresh once on an unknown `kid`; deny if still unknown. | Fetching keys from a URL in the token, or caching keys forever |
| 4 | **Signature** | Verify over header and payload with the selected key | Decoding without verifying "because the gateway already did" |
| 5 | **Issuer** | Exact match against an allow-list. For multi-tenant apps, build the expected issuer from the `tid` claim **after** checking `tid` against the tenant allow-list. Pin the token version (`ver`) you expect. | Prefix or substring matching; accepting both v1 and v2 issuers by accident |
| 6 | **Tenant** | `tid` must be in the allow-list of tenants this tool serves | Treating "signed by Entra" as "from a customer we serve" |
| 7 | **Audience** | `aud` equals this tool's application ID URI or client id. One audience per tool. | Accepting Microsoft Graph tokens or another tool's tokens |
| 8 | **Lifetime** | `nbf` and `exp` checked against a synchronized clock with a small, documented skew | Ignoring `nbf`; large skew windows |
| 9 | **Client application** | `azp` (v2) or `appid` (v1) in the allow-list of clients that may call this tool, such as the broker or a specific agent identity | Letting any app in the tenant call the tool |
| 10 | **Token type** | Distinguish delegated from app-only: app-only tokens carry `roles` and no `scp`. The optional `idtyp` claim makes this explicit; configure it if you rely on it. | Treating an app-only token as a user |
| 11 | **Permissions** | Delegated: required scope in `scp`, and required app role in `roles` if the API uses user roles. App-only: required application role in `roles`. | Reading roles from a header or from the model |
| 12 | **Subject** | Use `tid` plus `oid` as the user key. Never authorize on `name`, `preferred_username`, `email` or `upn`. | Keying data by email address |

After step 12 the verifier constructs the sealed principal. Nothing else can construct one, and the PDP refuses any subject that is not a sealed principal (`TAINTED_ATTRIBUTE`).

Agent identity tokens carry additional claims that identify the agent and, for delegated tokens, the acting agent alongside the user. Verify the claim names against current [Entra Agent ID](https://learn.microsoft.com/entra/agent-id/identity-platform/what-is-agent-id) documentation before relying on them in policy.

**Where to validate**

- **Front door:** [validate-azure-ad-token](https://learn.microsoft.com/azure/api-management/validate-azure-ad-token-policy) or [validate-jwt](https://learn.microsoft.com/azure/api-management/validate-jwt-policy) in API Management, or middleware in the web tier. This is a coarse gate.
- **Tool:** full validation for its own audience, in-process, before the PDP. This is the authoritative check.
- **Broker:** validates the agent's token (caller) and the front door's binding before issuing anything.

Reference: [Microsoft identity platform access tokens](https://learn.microsoft.com/entra/identity-platform/access-tokens).

## 5. Roles, scopes and RBAC

**App roles and scopes on each tool API**

| Design rule | Why |
|---|---|
| Define one app role per privileged action (`Payments.Approve`, `Records.Export`, `Refunds.Issue`) and one per read family (`Ledger.Read`, `Clinical.Read`, `Orders.Read`) | Roles map one-to-one to policy rules and reason codes; reviews are readable |
| Assign app roles to **groups** under access reviews, not to individual users | Revocation flows from group membership and is reviewable |
| Expose a small number of delegated scopes per tool API | The scope says what the client may do on the user's behalf; the role says what the user may do |
| Keep fine-grained entitlements (cost center, care team, store) out of tokens | Tokens cannot be revoked mid-lifetime; entitlements are read live at the PEP |
| Require user assignment on tool APIs that should not be callable by every user in the tenant | Prevents "any signed-in user" tokens from reaching the PDP at all |

**Foundry RBAC**

Callers need a data-plane role on the Foundry project to invoke an agent, such as Foundry User (formerly Azure AI User). Verify the current role name before assignment. Treat that assignment as an authorization surface:

- Whoever can invoke the agent determines whose caller id the platform attests. If a second application gains invoke rights, it can create sessions whose platform-attested caller id is itself. That is why bindings are keyed by caller id **and** written only by the front door.
- Grant invoke rights to the front door identity only. End users reach the agent through the front door, not directly.
- The agent's own identity holds data-plane roles for models and its state stores, and nothing on customer data stores that tools own. Role details for hosted agents are in [Production Hosted Agents on Microsoft Foundry](../../production-hosted-agents-on-foundry/docs/authentication-and-governance.md).

For tools attached to Foundry agents, see [tool authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/tool-authentication), [MCP authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication) and [tool governance](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/governance).

## 6. Secrets and credentials

| Credential | Handling |
|---|---|
| User access tokens | Held only by the front door, the broker and the tool that validates them. Never logged, never placed in prompts, arguments, memory, thread stores or traces. |
| Agent and tool identities | Managed identities or federated credentials. No client secrets in code or configuration. |
| Broker signing key (signed-assertion option) | Hardware-backed key in Key Vault, sign operation only; rotation with overlapping validity; tools validate against published public keys |
| Connection credentials for Toolbox or MCP connections | Stored by the platform connection; access to edit connections is a privileged role |
| Test signing secrets | Test-only, generated per run, never shared with production configuration. The sample's HMAC verifier exists only for tests. |

Scan prompts, traces and audit output for token-shaped strings in CI and in production sampling. A token in a trace is an incident.

## 7. Policy as code

- **Repository.** Rules (action, required role, delegation requirement, owner-only, step-up) live in version control with an owner per tool family.
- **Versioning.** Each release has a `policy_version` that appears in every decision and audit record, so any decision can be replayed against the rules that made it.
- **Tests.** Every rule ships with allow and deny tests. The adversarial suite in [evaluation and troubleshooting](evaluation-and-troubleshooting.md) runs against every policy change.
- **Default.** A tool without a rule denies (`NO_MATCHING_RULE`). Adding a tool means adding a rule in the same change.
- **Reason-code catalog.** The catalog in [architecture section 10.3](architecture.md#103-pdp-request-and-response) is part of the policy contract. Adding a code is a minor version. Changing the meaning of a code is a major version, because dashboards, alerts and client messages depend on it.
- **Model-facing messages.** Map each reason code to a fixed, user-safe message. The model never sees policy detail, role names or resource attributes it could use to plan a bypass.

## 8. Entitlement governance and revocation

| Concern | Control |
|---|---|
| Source of truth | One governed system per entitlement family (finance access reviews, care-team roster, merchant admin grants) |
| Revocation | Revocation events increment the entitlement version and invalidate caches; the next call sees the change |
| Cache risk | Any cache lifetime is a documented risk acceptance signed by the data owner ([AV-10](../../../security-and-governance/agent-security-and-governance/docs/threat-model-and-control-matrix.md#av-10-stale-entitlement-cache)) |
| Access reviews | Group-based app role assignments and fine-grained grants are reviewed on a schedule |
| Agent lifecycle | Agent identities have sponsors and are retired with the agent; orphaned identities are disabled |
| Drills | A revocation drill removes a grant from a test user mid-session and confirms the next call denies with `ENTITLEMENT_REVOKED` |

## 9. Audit

| Requirement | Detail |
|---|---|
| Coverage | One record per PEP decision, allow or deny, including denials before the PDP (`NO_VERIFIED_IDENTITY`, `TOKEN_INVALID`, `TENANT_MISMATCH`) |
| Content | Subject (`tid`, `oid`, `idtyp`, `azp`, roles), action, resource id, decision, reason code, policy version, entitlement version, conflicts, injection flag, correlation id marked untrusted |
| Exclusions | No token values, and no tainted values: record key, provenance and class only |
| Integrity | Append-only store with a per-PEP sequence number so gaps are detectable |
| Access | Audit readers are separate from policy authors and tool operators; deletion requires a separate, logged approval |
| Retention | Set by the data owner and the applicable regulation for each Domain Pack; healthcare and public sector usually need longer retention and stricter read access |
| Use | Dashboards and alerts by reason code, tool and caller application; investigations replay decisions using the recorded policy and entitlement versions |

## 10. Separation of duties

| Duty | Performed by | Cannot also |
|---|---|---|
| Author and approve policy rules | Policy owners, through reviewed pull requests | Grant entitlements; read or delete audit records |
| Grant and revoke entitlements | Entitlement administrators in the system of record | Change policy or tool code |
| Assign app roles and Foundry invoke rights | Identity administrators, with access reviews | Change policy; operate the broker |
| Operate the broker and its keys | Platform security | Change which audiences the broker may issue for without review |
| Build and release tools and agents | App teams through the pipeline | Change policy in the same change without a policy owner's approval |
| Read audit and investigate | Security operations | Change policy, entitlements or audit retention |

## 11. Threat-to-control mapping

| Threat | Primary control | Supporting controls | Reason code | Sibling mapping |
|---|---|---|---|---|
| T1 Spoofed forwarded headers | Subject only from validated token (section 4) | Front door strips identity headers; conflicts recorded | No effect on decision; conflict logged | [AV-03](../../../security-and-governance/agent-security-and-governance/docs/threat-model-and-control-matrix.md#av-03-confused-deputy-and-cross-tenant-id-tampering) |
| T2 Injected tenant id in arguments | Server-side resource resolution; tenant cross-check at the PEP | Injection flag in audit; alert on spikes | `TENANT_MISMATCH` | AV-03 |
| T3 Model-echoed role claims | Roles only from token claims | Fixed user-safe messages; prompt shields | `ROLE_NOT_GRANTED` | AV-01, AV-02 |
| T4 Stale entitlements | Live entitlement read per call | Versioned caches; revocation drills | `ENTITLEMENT_REVOKED` | [AV-10](../../../security-and-governance/agent-security-and-governance/docs/threat-model-and-control-matrix.md#av-10-stale-entitlement-cache) |
| T5 Confused deputy | `requires_delegated_user` in policy; delegated tokens for user data | Broker allow-lists; `azp` allow-list at the tool | `DELEGATION_REQUIRED`, `NOT_ENTITLED` | AV-03 |
| T6 Missing identity as anonymous | Fail-closed verifier and PEP | Contract tests for token forwarding; reason-code dashboards | `NO_VERIFIED_IDENTITY`, `TOKEN_INVALID` | AV-03 |
| T7 Cross-tenant memory or thread bleed | Threads and memory are resources with owner-only rules | Partition keys from the verified subject | `TENANT_MISMATCH`, `NOT_ENTITLED` | AV-03 |
| Credentials in model-visible content | Tokens held only by front door, broker and tool | Token-shape scanning of traces and audit | Not applicable | [AV-07](../../../security-and-governance/agent-security-and-governance/docs/threat-model-and-control-matrix.md#av-07-credential-leakage-in-logs-and-traces) |

## 12. Governance checklist

- [ ] Every tool is classified as user-scoped or app-only, and the classification is in policy.
- [ ] Every tool validates tokens per section 4 for its own audience, with tenant and client allow-lists.
- [ ] Invoke rights on the Foundry project are held by the front door identity only.
- [ ] The broker (if used) allow-lists callers, audiences and scopes, and audits every issuance.
- [ ] App roles are assigned to reviewed groups; fine-grained entitlements stay out of tokens.
- [ ] Policy is versioned, tested and owned; the reason-code catalog is versioned.
- [ ] Audit records exclude tokens and tainted values, are append-only, and are readable only by a separate group.
- [ ] Separation of duties in section 10 is enforced through role assignments, not convention.
- [ ] Revocation drills and the adversarial suite run on a schedule and before every release.

## 13. References

- [Microsoft identity platform access tokens](https://learn.microsoft.com/entra/identity-platform/access-tokens)
- [On-Behalf-Of flow](https://learn.microsoft.com/entra/identity-platform/v2-oauth2-on-behalf-of-flow)
- [What is Microsoft Entra Agent ID](https://learn.microsoft.com/entra/agent-id/identity-platform/what-is-agent-id) and [agent identity governance](https://learn.microsoft.com/entra/id-governance/agent-id-governance-overview)
- [Agent identity in Foundry](https://learn.microsoft.com/azure/foundry/agents/concepts/agent-identity) and [hosted agents](https://learn.microsoft.com/azure/foundry/agents/concepts/hosted-agents)
- [Tool authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/tool-authentication), [MCP authentication](https://learn.microsoft.com/azure/foundry/agents/how-to/mcp-authentication) and [tool governance](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/governance)
- [API Management validate-azure-ad-token policy](https://learn.microsoft.com/azure/api-management/validate-azure-ad-token-policy)
- Sibling assets: [Backend Identity Propagation](../../../ai-gateway/backend-identity-propagation/README.md), [MCP OAuth Front Door](../../../ai-gateway/mcp-oauth-front-door/README.md), [Multi-Tenant MCP Gateway](../../../ai-gateway/multi-tenant-mcp-gateway/README.md), [Enterprise Agent Security and Tool Governance](../../../security-and-governance/agent-security-and-governance/README.md)
