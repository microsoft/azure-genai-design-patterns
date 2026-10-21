# Authentication and Governance

This document covers where entitlements come from, which identities take part, how tokens and roles become an entitlement decision, which Azure permissions each component requires, and how the catalog, policy and audit are governed. It assumes the architecture in [architecture.md](architecture.md).

It does not repeat the full token-validation checklist, which is in [Request Context Is Not Authorization](../../../agent-runtime/request-context-is-not-authorization/docs/authentication-and-governance.md), how MCP clients obtain tokens, which is in [MCP OAuth Front Door](../../mcp-oauth-front-door/README.md), or how the service calls domain APIs with the right credential, which is in [Backend Identity Propagation](../../backend-identity-propagation/README.md).

Role names, claim behavior and portal options change between releases. Check them against current documentation before you rely on them.

## 1. Principles

1. **Entitlements come from verified identity.** The tenant comes from the validated `tid` claim, the roles from the validated `roles` claim, and licensed modules and flags from the registry keyed by that tenant. Headers, arguments, session state and model output are never inputs to the decision.
2. **One decision, applied twice.** The same function filters `tools/list` and authorizes `tools/call` ([ADR 0001](decisions/0001-filter-list-and-enforce-call-from-one-entitlement-decision.md)).
3. **Deny by default.** Unknown tools, unregistered tenants and missing snapshots are denied. A stale snapshot is used only inside a window the risk owner approved.
4. **Shared metadata is public to every tenant.** Tool names, descriptions and schemas in the shared catalog must not contain tenant data.
5. **Audit decisions, not content.** Record who, which tool, which versions and why. Never record arguments, results, tokens or the values of untrusted headers.

## 2. Entitlement sources

Most products combine two sources: app roles for what a person may do, and a registry for what the tenant has bought.

| Source | What it expresses | Freshness | Strengths | Risks |
|---|---|---|---|---|
| **App roles** on the MCP server's app registration (`roles` claim) | Job function inside a tenant: Planner, Scheduler, Dispatcher | Fixed at token issuance; changes apply at the next token | Signed by Entra, no extra lookup, assignable to users, groups and client applications | Role changes take up to a token lifetime to apply. Customer admins must assign roles in their own directory. |
| **Group claims** (`groups` claim) | Membership in directory groups | Fixed at token issuance | Reuses existing groups | Group IDs differ per tenant, and users in many groups hit the overage limit, after which the claim is replaced by a reference. Prefer app roles. |
| **Entitlement registry or licensing service** | Licensed modules, feature flags, description variants, plan | As fresh as the cache TTL and change events allow | Changes apply mid-session, can be revoked quickly, and stay under the operator's control | An extra dependency with its own availability. Requires a version stamp and change events. |
| **Per-tenant configuration** (for example a configuration store) | Small, slow-changing settings | Configuration refresh interval | Simple | No version stamp or events unless you add them. Treat it as a registry and give it a version. |

Guidance:

- Put **what the tenant licensed** in the registry. Licensing changes must apply without waiting for every user to sign in again.
- Put **what the person does** in app roles. Define them on the MCP server's app registration, and assign them to groups in each customer directory.
- Do not put module or license names in app roles. Customer admins could assign themselves a module they have not bought.
- Keep the set of role names that tools reference small and stable. The principal class, and so the cache key, is built from them.

References: [app roles](https://learn.microsoft.com/entra/identity-platform/howto-add-app-roles-in-apps), [group claims](https://learn.microsoft.com/entra/identity/hybrid/connect/how-to-connect-fed-group-claims), [access token claims](https://learn.microsoft.com/entra/identity-platform/access-tokens).

## 3. Identity inventory

| Identity | Kind | Proves | Used to call | Must never |
|---|---|---|---|---|
| **End user** | Entra user in the customer directory | Tenant (`tid`), subject (`oid`), app roles | The MCP endpoint, through the client | Have a token forwarded to domain APIs or placed in the prompt |
| **Client application or agent** | App registration or agent identity | Which application requested the token (`azp` or `appid`) | The MCP endpoint | Be the only factor in an entitlement decision |
| **MCP server app registration** | Multi-tenant app registration | Defines the audience and the app roles | Nothing; it is the resource | Accept tokens issued for another audience |
| **Gateway edge** | Managed identity of the gateway | That an authenticated hop forwarded the request | The entitled tool service, if it requires a hop credential | Rewrite the tenant, roles or `tools/list` body |
| **Entitled tool service** | Managed identity of the container app | Its own identity to platform services | Registry, event subscription, Key Vault, domain APIs | Forward the caller's MCP token to a domain API |
| **Entitlement registry** | API with its own app registration | The authoritative entitlement record for a tenant | Nothing in this flow | Accept a tenant ID from anyone but allow-listed callers |
| **Catalog publishers** | Entra group with just-in-time elevation | Who published or changed a tool | The catalog pipeline | Also be able to delete audit records |
| **Registry administrators** | Entra group with just-in-time elevation | Who changed a tenant's entitlements | The registry admin surface | Change entitlements without an audit record |

## 4. Token validation and claims

The gateway edge validates the token before the request reaches the service. The service validates again, or alone if there is no edge. Use a maintained library in both places.

| Step | Check | On failure |
|---|---|---|
| 1 | One `Authorization: Bearer` value is present | `401` |
| 2 | Signature against the issuer's published keys; allowed algorithms only | `401` |
| 3 | Issuer from an allow-list, built from `tid` only after `tid` is accepted | `401` |
| 4 | Audience equals the MCP server's application ID URI or client ID | `401` |
| 5 | `nbf` and `exp` with small, documented clock skew | `401` |
| 6 | `azp` or `appid` in the allow-list of client applications | `401` |
| 7 | Required delegated scope in `scp`, or application role in `roles` for app-only callers | `401` or `403`, per your API contract |
| 8 | Build the sealed principal from `tid`, `oid`, `roles` and `azp` | n/a |

Every `401` carries `WWW-Authenticate: Bearer resource_metadata="..."` so MCP clients can discover the authorization server, and produces one `auth_failed` audit event. The sample's `FakeTokenValidator` is a lookup table, not a JWT parser; it only distinguishes `missing credential`, `invalid credential` and `expired credential`. Its `EntraTokenValidator` seam documents the real steps. In API Management, the [validate-azure-ad-token](https://learn.microsoft.com/azure/api-management/validate-azure-ad-token-policy) or [validate-jwt](https://learn.microsoft.com/azure/api-management/validate-jwt-policy) policy can apply steps 2 to 6 at the edge.

After validation:

- `tid` is mapped to an internal tenant by the registry. An unregistered `tid` returns `403` and is negative-cached for `negative_ttl`.
- `roles` are normalized to the roles the catalog references (the principal class).
- `oid` becomes the audit subject and the session owner. Never use `name`, `preferred_username`, `email` or `upn` for authorization.
- Tenant hint headers (`X-Tenant-Id`, `X-Org-Id`, `X-Customer-Id`, `X-Account-Id`) are ignored. Their names are audited.

App-only callers (a scheduled agent with no user) receive application roles in `roles` and no `scp`. Decide explicitly which tools app-only callers may see. A common choice is to give app-only clients a dedicated role and reference it only on read-only tools.

## 5. Azure role assignments

Assign roles to managed identities and groups, not to individuals. Scope each assignment to the single resource.

| Principal | Resource | Role or permission | Why |
|---|---|---|---|
| Entitled tool service managed identity | Entitlement registry API | An application role defined by the registry (for example `Entitlements.Read`) | Read entitlements. No write access. |
| Entitled tool service managed identity | Message broker or event subscription for entitlement events | A receive-only data role, such as Azure Service Bus Data Receiver | Receive change events |
| Entitled tool service managed identity | Key Vault holding the cursor key | Key Vault Secrets User | Read the cursor key. No list or write access. |
| Entitled tool service managed identity | Domain APIs | An application role per API, or OBO when the API requires the user | Invoke allowed tools |
| Gateway edge managed identity | Entitled tool service | Ingress restricted to the gateway, plus a hop credential if used | Make the service unreachable except through the edge |
| Agents that call models | Microsoft Foundry project | Foundry User (formerly Azure AI User) | Run agents and call models. Unrelated to tool entitlements. |
| Catalog publishers group | Catalog repository and pipeline | Contributor on the repository, with required review | Publish tools |
| Security and audit readers | Log analytics workspace | Log Analytics Reader | Investigate decisions |

See [managed identities](https://learn.microsoft.com/entra/identity/managed-identities-azure-resources/overview) and [Key Vault RBAC](https://learn.microsoft.com/azure/key-vault/general/rbac-guide).

## 6. Secrets and keys

| Item | Where | Rotation |
|---|---|---|
| Cursor MAC key | Key Vault secret, read by every replica at start and on rotation | Rotate on a schedule. Accept the current and previous key during an overlap at least as long as a client might hold a cursor. The sample uses one ephemeral key per process. |
| Registry access | Managed identity token | Handled by the platform; no stored secret |
| Domain API access | Managed identity, or OBO through a confidential client with a certificate or a workload identity credential | Certificates in Key Vault with auto-renewal |
| Client application secrets | Not used by the service | n/a |

A leaked cursor key lets an attacker forge cursors, but a forged cursor still binds to the caller's own tenant, principal class and view digest. The impact is limited to paging oddities in the attacker's own view. Rotate anyway.

## 7. Catalog governance

Every tool definition goes through a reviewed publish pipeline. The sample's `ToolCatalog` lint rejects three classes of mistake automatically:

| Lint rule | Why |
|---|---|
| Name matches `^[A-Za-z0-9_.-]{1,128}$` | Names appear in logs and errors. A strict pattern keeps injected text out of both. |
| A published name cannot move to another module | Moving a tool silently changes who is entitled to it. Version the name instead (`production.get_work_order_v2`). |
| No tenant-identifying term in title, description, variants or schema | Shared metadata is shown to every entitled tenant. The forbidden list is built from tenant display names and IDs. |

Add these review checks, which a script cannot judge reliably:

- The module, roles, feature flag and read-only flag match what the tool really does. A write tool marked read-only can run during a registry outage.
- Description variants use neutral IDs (`multi-site`, not a customer name) and say nothing a limited tenant must not learn.
- Each `x-entitlement-flag` property is optional. A required gated property breaks callers without the flag.
- `discoverable` is set only where naming the module in an error is acceptable.
- The description is written for the model: what the tool does, when to use it, what it returns. Descriptions are also an injection surface, so they never contain instructions to the model about other tools.

Separation of duties: the people who publish tools do not administer tenant entitlements, and neither group can change or delete audit records.

## 8. Policy per domain

`CachePolicy` holds the settings a risk owner signs off on. The Domain Packs show three postures.

| Setting | Manufacturing | Healthcare | Logistics | How to choose |
|---|---|---|---|---|
| `entitlement_ttl` | 30 s | 10 s | 30 s | The longest revocation delay you accept when an event is lost |
| `negative_ttl` | 10 s | 10 s | 30 s | Short enough that a newly onboarded tenant is not locked out for long |
| `max_staleness` | 60 s | 0 | 120 s | How long tools stay usable during a registry outage. Zero means fail closed. |
| `stale_call_scope` | `read-only` | `none` | `read-only` | Which calls may run on a stale snapshot |
| `allow_explicit_disclosure` | yes | no | yes | Whether any tool may return a "not available for this caller" error |

The sample values are illustrative and run against a simulated clock. Choose production values with the risk owner, and record the decision with the policy.

## 9. Disclosure policy

The default is **uniform**: a hidden tool and a nonexistent tool return the same `-32602 Unknown tool: <name>` with no `data`. This hides which modules exist and what other tenants license.

**Explicit** mode returns `-32001 Tool not available for this caller` with `{"reason": "not_entitled"}` or `role_required`, but only for tools marked `discoverable`, and only when the domain policy allows it. The service refuses to start in explicit mode under a policy that forbids it. Use explicit mode where module names are public and a clear message helps a user request access. Never use it for tools whose existence is itself sensitive.

## 10. Audit

Each decision produces one structured event. The full event list and fields are in [architecture.md section 14.7](architecture.md#147-audit-events).

| Always record | Never record |
|---|---|
| Event, time, internal tenant ID, subject (`oid`) | Tool arguments or results |
| Principal class, tool name (or a hash for invalid names) | Access tokens or any part of them |
| Entitlement version, catalog version, freshness | Values of tenant hint headers |
| Denial reason (`unknown`, `not_entitled`, `role_required`, `stale_entitlements`, `invalid_arguments`) | Free text from the model |
| Cursor rejection reason, session rejection reason | Description variant text |

Operational guidance:

- Send events to a log analytics workspace with a correlation ID shared with the gateway edge logs.
- Treat `oid` as personal data. Set retention to what your compliance policy requires, and restrict read access to the security and audit readers group.
- Alert on bursts of `call_denied` with reason `not_entitled` or `unknown` from one subject. It often means a model is probing for tools, or a client is using an outdated list.
- Alert on `entitlements_unavailable` and on a rising share of `stale` freshness. Both indicate registry trouble.

## 11. Prompt injection and responsible AI

Catalog filtering reduces what an injected instruction can reach: the model cannot be steered toward a tool that is not in its context, and call-time enforcement blocks the tool even if the name is guessed. It does not make an allowed tool safe. An injection can still misuse a tool the caller is entitled to.

Keep the controls that address that risk: human approval for high-impact writes, record-level authorization in domain APIs, output filtering, and the guidance in [Agent Security and Governance](../../../security-and-governance/agent-security-and-governance/README.md).
