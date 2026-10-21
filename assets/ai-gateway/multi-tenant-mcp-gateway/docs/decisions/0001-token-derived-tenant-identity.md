# ADR 0001: Derive tenant identity only from a validated token claim

- **Status:** Accepted
- **Date:** 2026-10-20
- **Scope:** Every MCP server exposed through the multi-tenant gateway, every policy that makes a tenant-dependent decision (entitlement, limits, credential, routing, audit), and every backend that receives tenant context from the gateway
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md)

## Context

Contoso ERP's gateway serves many tenants through one hostname. Almost every decision the gateway makes depends on knowing which tenant a request belongs to: whether the tenant is active, which products and tools it may use, which rate-limit and quota counters to charge, which backend credential to use, which cell holds its data, and which audit partition to write to. If the tenant is wrong, every one of those controls is wrong at the same time.

Several signals look like they could identify the tenant:

- **Source IP address.** Traffic crosses an optional firewall or third-party NVA, Application Gateway, and API Management. Each hop can translate source addresses. Hosted MCP clients such as Copilot Studio and Microsoft Foundry share egress address ranges across many customers. Tenants' own users connect from changing networks. By the time a request reaches the policy, the address identifies a hop, not a tenant.
- **A client-supplied header** such as `X-Tenant-Id`. Any client can set any header. A header is only as trustworthy as the party that set it.
- **A path segment or hostname per tenant** such as `/t/fabrikam/mcp`. The client chooses the URL, so this is a routing hint, not proof. It also multiplies OAuth resource identifiers and protected resource metadata documents.
- **An API Management subscription key.** Keys identify an integration, are long-lived shared secrets, and are not supported by every MCP client. A key does not say which user is calling.
- **A tool argument or a value the model fills in.** The model can be prompted to put any tenant in an argument.
- **A validated access token claim.** The authorization server issues a signed token for the gateway's audience after authenticating the user, and it can include a tenant claim (a custom claim such as `tenant_id`, or Microsoft Entra `tid` mapped through the tenant directory).

Only the last signal is both bound to an authenticated principal and verifiable by the gateway.

## Decision

1. The gateway derives the tenant key **only** from a claim in an access token that has passed `validate-jwt` (or `validate-azure-ad-token`): signature, issuer, exactly one audience per MCP server, lifetime, and required scopes.
2. If the token has no tenant claim, or the claim does not map to a known, active tenant in the tenant directory, the request is denied (`403`). If the directory cannot be read, the request fails closed (`503`).
3. The gateway **deletes** client-supplied tenant hint headers (`X-Tenant-Id`, the gateway-to-backend tenant header, and similar) in the global policy before any API policy runs. Application Gateway also strips them at the edge. The gateway may record that a hint was present and whether it mismatched the token, as a probing signal, but never uses its value.
4. After validation, the gateway sets its own tenant header for the backend (`X-Contoso-Gateway-Tenant` in this asset). Backends accept that header only on requests authenticated as the gateway's identity over the private network.
5. No security control, rate limit, quota, or routing decision is keyed on source IP. IP allow lists remain valid only as **network** controls (for example, API Management accepts traffic only from the Application Gateway subnet).

## Consequences

- **Positive:** One trustworthy tenant key drives entitlement, limits, credentials, routing, and audit consistently. A forged header, a shared egress address, or a NAT change cannot move a request into another tenant.
- **Positive:** Hosted MCP clients that share egress ranges work without per-customer network exceptions.
- **Positive:** Spoofing attempts become observable in audit without affecting decisions.
- **Negative:** The authorization server must issue a reliable tenant claim. That means claim mapping, tenant membership checks at sign-in, and a plan for users who belong to more than one tenant.
- **Negative:** The gateway depends on the tenant directory at run time. Lookups must be cached, monitored, and fail closed.
- **Negative:** Backends must be changed to trust the gateway's tenant header only from the gateway identity, and to reject mismatches against their own data partition.

## Alternatives

| Alternative | Why rejected |
|---|---|
| **Source IP or IP ranges per tenant** | Rewritten by firewall, NAT, and WAF hops; shared by hosted MCP clients; changes when tenants change networks. It identifies the last hop, not the tenant. |
| **Client-supplied tenant header** | Forgeable by any caller. Accepting it turns every tenant-dependent control into a client option. |
| **Subscription key per tenant** | Long-lived shared secret that identifies an integration, not a user; not supported by every MCP client; leaks easily into configuration and prompts. Usable as a secondary integration identifier if it is checked against the token's tenant. |
| **Path segment or hostname per tenant** | Client-chosen, so it is still only a hint. Multiplies OAuth resources and metadata. Acceptable for routing convenience only if the gateway verifies it equals the token's tenant. |

## Follow-ups

- Agree the tenant claim name and issuance rules with the identity team, including how a user who belongs to more than one tenant selects one at sign-in. See [MCP OAuth Front Door](../../../mcp-oauth-front-door/README.md).
- Add the spoofed-header and missing-claim negative tests from [evaluation-and-troubleshooting.md](../evaluation-and-troubleshooting.md) to every release.
- Alert on spikes in mismatched tenant hint headers per client application.
- Review backends for any remaining tenant inference from client-controlled input.
