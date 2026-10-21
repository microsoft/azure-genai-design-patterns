# Authentication and governance

This document covers every identity in the pattern, how each hop is authenticated, where the bring-your-own backend credential lives, and the governance gap that remains open even when every hop is configured correctly. The token checks described here are implemented in [`src/gateway/app.py`](../src/gateway/app.py) and exercised offline by the `auth_rejects_bad_tokens_fail_closed` scenario in [`samples/streaming_gateway_sim.py`](../samples/streaming_gateway_sim.py).

> **Preview.** Hosted agent identities, the way the hosted-agent runtime issues tokens, and Foundry model gateway connections were observed in preview; verify against current docs before you rely on any statement marked that way.

## Identity map

| Hop | Caller | Credential | Validated by | Grants |
|---|---|---|---|---|
| Agent to gateway | Hosted agent identity | Entra access token for the gateway's app ID URI, requested from the agent's own identity | Gateway (signature, issuer, audience, tenant, lifetime, app role, caller allowlist) | Calling the gateway, nothing else |
| Gateway to Foundry deployment | Gateway user-assigned managed identity | Entra access token for the Cognitive Services scope | Foundry account (RBAC data action) | Model inference on that account |
| Gateway to Key Vault | Gateway user-assigned managed identity | Entra access token, used by the Container Apps secret reference | Key Vault (RBAC mode) | Reading secrets in that vault |
| Gateway to bring-your-own backend | Gateway | The backend's own credential (API key, or a token if the provider supports Entra or OAuth2) | The backend provider | Inference on the provider's model |
| Pipeline to Azure | Deployment workload identity | OIDC federated credential, no stored secret | Microsoft Entra ID and Azure RBAC | Deploying the resources in [`infra/`](../infra/README.md) |
| Prompt agent through a Foundry connection (optional) | Foundry, on behalf of the connection | OAuth2 client credentials token, or an API key (observed in preview; verify against current docs) | Gateway | Calling the gateway as one more caller |

The agent container holds **no** model key, **no** backend key and **no** gateway key. Its only credential is the identity the hosted-agent runtime gives it.

## Agent identity to gateway

### Register the gateway in Entra

Create one app registration per environment for the gateway. It represents the gateway as a resource (an API); the gateway never signs in with it.

1. **App ID URI.** Set `api://<gateway-app-client-id>` or a verified-domain URI. Agents request tokens for `<app-id-uri>/.default`.
2. **App role.** Add an application app role with the value `Gateway.Invoke`, allowed member type *Applications*. Managed identities and agent identities receive app-only tokens, and app roles are how those tokens carry an authorization claim. See [Add app roles to your application](https://learn.microsoft.com/entra/identity-platform/howto-add-app-roles-in-apps).
3. **Assignment required.** On the gateway's enterprise application (service principal), set *Assignment required* to yes. Entra then refuses to issue a token to any identity without a role assignment, so unassigned identities fail before they reach your network.
4. **Assign the role** to each hosted agent identity that may call the gateway, and to nothing else. Assigning app roles to hosted agent identities was observed in preview; verify against current docs. If your tenant cannot assign roles to that identity type, keep the role check off and rely on the caller allowlist, and record that as an accepted risk.
5. **Token version.** Decide whether the API accepts v1 or v2 access tokens (`requestedAccessTokenVersion` in the app manifest) and configure the gateway for what you chose. The two versions differ in issuer, audience format and caller claim (see the table below).

### Token validation checklist

The gateway validates every request, including retries and repeated calls on the same connection. It never caches a "this connection is authenticated" decision.

| Check | v1 access token | v2 access token | Failure |
|---|---|---|---|
| Signature | Signing keys from the tenant's JWKS endpoint, refreshed when an unknown key ID appears | Same | `401 invalid_token` |
| Algorithm | Only the asymmetric algorithm Entra uses; never accept `none` or symmetric algorithms | Same | `401 invalid_token` |
| Issuer (`iss`) | `https://sts.windows.net/<tenant-id>/` | `https://login.microsoftonline.com/<tenant-id>/v2.0` | `401 invalid_issuer` |
| Audience (`aud`) | Usually the app ID URI | Usually the gateway app's client ID | `401 invalid_audience` |
| Tenant (`tid`) | Your tenant only | Same | `401 invalid_tenant` |
| Lifetime (`nbf`, `exp`) | Enforced with a small clock-skew allowance | Same | `401 token_expired` |
| App role (`roles`) | Contains `Gateway.Invoke` | Same | `403 missing_app_role` |
| Caller | `appid` claim is a key in the policy's `callers` | `azp` claim is a key in the policy's `callers` | `403 caller_not_allowed` |

Practical notes:

- **Configure both audience forms.** `GATEWAY_AUDIENCES` takes a comma-separated list. Put the app ID URI and the client ID in it, so a change of token version does not take the gateway down.
- **Allow exactly one tenant.** `GATEWAY_TENANT_ID` is required. Do not accept the `common` or `organizations` issuers; the gateway is not multi-tenant.
- **The caller is the claim, not the header.** `caller_id` in policy, budgets and usage events is the validated `azp` or `appid`. No header, query string or body field can change it.
- **Never log the token.** Log the claims you used (`tid`, `azp` or `appid`, `roles`, the token's unique ID) with the request ID. Error envelopes never echo the token.
- **Fail closed on key retrieval.** If the JWKS endpoint cannot be reached, the request fails with `401`. The gateway does not skip signature checks to stay available.

### How the agent gets the token

The agent code in [`src/agent/main.py`](../src/agent/main.py) wraps its HTTP client with an auth hook that asks the agent's identity for a token for `<app-id-uri>/.default` on each call. The identity library caches tokens and refreshes them before expiry, so the per-call cost is a cache lookup.

- In the hosted-agent runtime, the identity is the dedicated agent identity the runtime assigns. How the runtime exposes it to code (environment variables, a managed identity endpoint, or a platform credential) was observed in preview; verify against current docs and the hosting library version you pin.
- For local development, the same code falls back to a developer credential. The developer's account then needs the `Gateway.Invoke` role in a development tenant or a development app registration, never in production.
- The agent never sends an API key. The model client's required `api_key` argument gets a dummy value, and the real `Authorization` header comes from the auth hook.

## Gateway identity to Foundry

### One user-assigned identity

The gateway runs with one user-assigned managed identity, created by [`infra/main.bicep`](../infra/main.bicep). It is used for three things: pulling the image from the registry, reading the bring-your-own secret from Key Vault, and calling Foundry deployments.

Use a user-assigned identity rather than system-assigned so that role assignments exist before the first revision starts, and survive the container app being recreated.

### Model role on the Foundry account

The gateway identity needs a data-plane role that allows chat completions on the Foundry account:

- **Recommended:** *Cognitive Services OpenAI User* at the Foundry account scope. It grants model inference through the OpenAI-compatible endpoints and little else.
- **Alternative:** *Foundry User (formerly Azure AI User)* if your landing zone standardizes on it. It is broader: it also covers other project data-plane operations.

The Bicep template takes the role as the required parameter `modelInferenceRoleDefinitionId`, with no default, so the choice is explicit per environment. Built-in role IDs are the same in every tenant; resolve the ID with the Azure CLI rather than hand-copying it:

```bash
az role definition list --name "Cognitive Services OpenAI User" --query "[0].name" -o tsv
```

Scope the assignment to the account the gateway calls, not the resource group or subscription.

### Keys stay disabled

- The Foundry account must have `disableLocalAuth: true`. Then no API key can call it, and Entra plus RBAC is the only path. The template references an existing account; confirm its setting with `az cognitiveservices account show --name <account> --resource-group <rg> --query properties.disableLocalAuth`.
- The gateway asks for tokens with the `https://cognitiveservices.azure.com/.default` scope and caches them in the credential.

### The client ID gotcha in Container Apps

Container Apps has no instance metadata endpoint. Managed identity tokens come from the identity endpoint the platform injects. With a user-assigned identity:

- Pass the identity's client ID explicitly (`AZURE_CLIENT_ID`, read by `ManagedIdentityCredential(client_id=...)`).
- If you forget it, or a credential chain picks the wrong credential type, the token request fails quickly and the gateway returns `502 upstream_failed` before any backend sees traffic. The gateway logs show a credential error, not an upstream status.

See [Managed identities in Azure Container Apps](https://learn.microsoft.com/azure/container-apps/managed-identity).

## Bring-your-own backend credentials

A model outside Foundry usually authenticates with its own API key. That key is the one secret in this pattern. Handle it like this:

1. **Key Vault in RBAC mode.** The template creates a vault with RBAC authorization, purge protection and public network access disabled by default. Access policies are not used.
2. **Seed the secret out of band.** An operator or a seeding pipeline with *Key Vault Secrets Officer*, granted just in time and removed afterwards, writes the secret. The secret value never goes through a Bicep parameter, a pipeline variable or a source file. The template's `secretSeederPrincipalId` parameter is optional for exactly that reason.
3. **Gateway reads, nothing else.** The gateway identity has *Key Vault Secrets User* on the vault. It cannot write, list keys or manage access.
4. **Container Apps Key Vault reference.** The container app declares a secret with `keyVaultUrl` and `identity` set to the gateway identity, and the container gets it through `secretRef` as `BYO_BACKEND_API_KEY`. The key does not appear in the template, the revision definition or the image. See [Manage secrets in Azure Container Apps](https://learn.microsoft.com/azure/container-apps/manage-secrets).
5. **Rotation.** Use a versionless secret URL so a new version is picked up. Container Apps refreshes Key Vault references on its own cycle; to apply a rotation immediately, restart the active revision. Rotate with overlap: create the new key at the provider, write it to Key Vault, restart, verify, then revoke the old key.
6. **Prefer tokens when the provider supports them.** If the bring-your-own provider accepts Entra tokens or OAuth2 with workload identity federation, use that instead of a static key and drop the Key Vault secret.

What the agent sees of all this: nothing. The agent asks for the `byo-chat` alias. Whether that alias is served by a keyed provider is a gateway policy fact.

## What request context does not authorize

Requests to the gateway carry context that looks authoritative but is not:

- correlation headers forwarded by Foundry or the hosted-agent runtime, such as client request IDs and agent or project identifiers (observed in preview; verify against current docs);
- `traceparent` and other tracing headers;
- the `user` field in the request body, or any metadata field;
- `X-Forwarded-For` and other proxy headers;
- the `Idempotency-Key`.

The gateway uses these for correlation, logging and duplicate detection only. **None** of them selects a policy, raises a budget, unlocks a model or identifies the caller. A caller that sends another agent's ID in a header is still the caller in its token. See [Request Context Is Not Authorization](../../request-context-is-not-authorization/README.md) for the general rule and its test cases.

If you need per-user decisions (for example, only some end users may reach a model), the user's identity has to arrive as a validated token, not as a header the agent fills in. That is a different pattern: see [Backend Identity Propagation](../../../ai-gateway/backend-identity-propagation/README.md).

## Closing the bypass

A gateway only governs the traffic that goes through it. Nothing in Foundry forces a hosted agent's model calls through your gateway: the agent owns its model client and could point it anywhere it can reach and authenticate to. Close the bypass on both of those conditions.

**Remove the credential path.**

- Do not give hosted agent identities a model inference role on any Foundry account. Only the gateway identity has one.
- Check that the agent identity has no inherited role either: a role on the resource group or subscription that includes inference data actions opens the same door.
- If the agent identity needs a Foundry role on its own project for other features, check whether that role includes model inference on the project's account. If it does, host the models the gateway serves on a separate Foundry account where agent identities have no role. Which roles hosted agents need was observed in preview; verify against current docs.
- Keep `disableLocalAuth: true` on every Foundry account, so a key found in a repository or a log cannot be used.

**Remove the network path.**

- Restrict outbound traffic from the agent's network to the gateway and the services the agent legitimately needs. Network isolation for hosted agents was observed in preview; verify against current docs.
- Keep Foundry accounts private (private endpoints only) and allow the bring-your-own provider's host name only from the gateway's subnet.

**Detect what you cannot prevent.**

- Use Azure Policy to audit or deny role assignments of inference roles on Foundry accounts to principals other than the gateway identity.
- Alert on role assignment writes at Foundry account scope.
- Compare Foundry account diagnostic logs (caller object ID per request) with the gateway identity. Any other caller is either a bypass or an exception you must document.

The [Agent Security and Governance](../../../security-and-governance/agent-security-and-governance/README.md) asset covers the wider control set these checks belong to.

## Prompt agents through a Foundry connection

If prompt agents also use the gateway through a Foundry model gateway connection, Foundry calls the gateway with the connection's credential (observed in preview; verify against current docs):

- **OAuth2 client credentials (preferred).** Create a separate app registration for the connection, assign it `Gateway.Invoke`, and store its client credential in the connection. The token Foundry sends is an ordinary Entra token, so the gateway validates it like any other and the connection appears as one caller (`azp`) with its own policy and budget.
- **API key.** The reference gateway does not accept API keys ([ADR 0003](decisions/0003-gateway-auth-entra-mi-only.md)). Adding a key path means a second authentication mode, key storage and rotation in the gateway. Avoid it.
- **Attribution.** Every prompt agent behind one connection shares one caller identity. If you need per-agent budgets, use one connection per agent or team.

## Threat model

| Threat | Control | Residual risk |
|---|---|---|
| Stolen agent token replayed against the gateway | Short token lifetime, audience bound to the gateway, network restrictions on ingress | Replay within the token lifetime from an allowed network |
| Token for another API sent to the gateway | Audience and issuer checks | None if both audience forms are configured correctly |
| Identity from another tenant | Single-tenant issuer and `tid` check | None |
| Valid identity with no business calling the gateway | *Assignment required*, `Gateway.Invoke` role, caller allowlist | An admin assigning the role by mistake; review assignments |
| Caller impersonating another agent with headers | Caller comes from the signed claim only | None |
| Agent bypasses the gateway | No model role for agent identities, egress control, policy audit, log comparison | Roles the agent needs for other features; see [Closing the bypass](#closing-the-bypass) |
| Bring-your-own key leaked | Key only in Key Vault and the gateway's memory, RBAC read for one identity, rotation procedure | Exposure through a gateway memory dump or verbose logging; keep logs content-free |
| Gateway identity over-privileged | Role scoped to one Foundry account and one vault | The gateway is a high-value target; protect its revisions and pipeline |
| Policy tampering at runtime | Policy is a reviewed document deployed as a new revision; no admin endpoint | A pipeline compromise; protect the pipeline identity and require reviews |
| Prompt or completion text in telemetry | Usage event has no content; no request-body logging | Debug logging switched on in production; alert on the setting |

## Review checklist

Before an environment goes live:

- [ ] The gateway app registration has an app ID URI, the `Gateway.Invoke` app role and *Assignment required* set.
- [ ] Only approved hosted agent identities (and connection app registrations, if used) have the role.
- [ ] `GATEWAY_TENANT_ID`, `GATEWAY_AUDIENCES` and `GATEWAY_REQUIRED_ROLE` are set; the gateway refuses to start without them.
- [ ] Every caller in the policy document maps to a known identity and owner.
- [ ] The gateway identity has the model role on the Foundry account it calls, and nowhere else.
- [ ] No hosted agent identity has a model inference role, directly or inherited.
- [ ] The Foundry account has `disableLocalAuth: true` and is reachable only privately.
- [ ] The bring-your-own secret is in Key Vault (RBAC mode), referenced by the container app, and absent from templates, pipelines and images.
- [ ] The secret seeder role is removed after seeding.
- [ ] The rotation procedure has been run once in a non-production environment.
- [ ] A request with no token, an expired token, a wrong-audience token and an unassigned identity each fail with the expected code (see [Evaluation and troubleshooting](evaluation-and-troubleshooting.md)).
- [ ] Policy audit and the role assignment alert are in place.

## Related

- [Architecture](architecture.md)
- [ADR 0003: Entra managed identity tokens only for callers](decisions/0003-gateway-auth-entra-mi-only.md)
- [Production Hosted Agents on Foundry](../../production-hosted-agents-on-foundry/README.md)
- [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md)
- [Microsoft identity platform access tokens](https://learn.microsoft.com/entra/identity-platform/access-tokens)
- [Bring your own model with an AI gateway](https://learn.microsoft.com/azure/foundry/agents/how-to/ai-gateway)
