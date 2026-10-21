# ADR 0003: Entra managed identity tokens only for callers

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Authentication and governance](../authentication-and-governance.md), [Token validation checklist](../authentication-and-governance.md#token-validation-checklist), [Asset README](../../README.md), [Request Context Is Not Authorization](../../../request-context-is-not-authorization/README.md)

## Context

Every hosted agent that uses the gateway must prove who it is, because the gateway's policy (allowed models, budgets, input rules) is keyed by caller. The common shortcuts are:

- a shared API key for the gateway, stored in each agent's configuration;
- a per-agent API key, issued and rotated by the platform team;
- an identifier in a request header, set by the agent or forwarded by the platform, trusted as the caller.

Hosted agents already have a Microsoft Entra identity, managed by the platform, that can request tokens without any stored secret. The gateway already runs with a managed identity for its own backend calls. The tenant's identity team already governs app registrations and role assignments.

Headers such as an agent ID or a request ID may arrive with requests from Foundry. They are useful for correlation, but they are not signed by anything the gateway trusts, so they cannot decide what a caller may do. See [Request Context Is Not Authorization](../../../request-context-is-not-authorization/README.md).

## Decision

1. **Callers authenticate only with Microsoft Entra access tokens** issued to their managed identity for the gateway's app ID URI. The gateway does not accept API keys, client certificates or shared secrets from callers.
2. **The gateway validates every token fully** before reading the request body: signature against the tenant's signing keys (RS256 only), issuer (v1 or v2 form for the configured tenant only), audience (app ID URI or client ID), tenant ID, expiry and not-before, and the `Gateway.Invoke` app role.
3. **The caller identity is the signed `azp` or `appid` claim.** The policy is keyed by that client ID. A caller not in the policy is denied with `403 caller_not_allowed`, even with a valid token and the app role.
4. **Assignment required is on.** Only identities assigned the app role can obtain a token for the gateway at all.
5. **Fail closed.** If the gateway cannot fetch signing keys or its tenant and audience settings are missing, it rejects requests or refuses to start; it never falls back to accepting unvalidated tokens.
6. **Request headers are correlation only.** Forwarded identifiers are logged as attributes and never used to choose a caller, a policy or a backend.

## Consequences

### Positive

- No secret exists in any agent container, so there is nothing to leak, copy or rotate on the caller side.
- Caller identity comes from a signed claim, so usage attribution and budgets cannot be spoofed by changing a header.
- Granting and revoking access uses the tenant's normal tooling: app role assignments and the policy file, both reviewable.
- The same pattern serves prompt agents through a Foundry connection that uses its project identity. Observed in preview; verify against current docs.

### Trade-offs

- Every new agent needs an app role assignment and a policy entry before its first call. Role changes take effect only when the agent gets a new token.
- Local development needs a developer identity with the role in a non-production tenant or app registration, or the offline simulator.
- Callers outside Entra (another cloud, a partner) cannot use the gateway directly; they need federation or a different front door.
- Token validation depends on fetching the tenant's signing keys. The gateway caches them, but a prolonged failure to fetch them means rejecting requests.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Shared API key for all agents | No per-agent attribution or policy; one leak exposes every model; rotation touches every agent |
| Per-agent API keys | Attribution works, but every agent stores a secret and the platform team runs key issuance and rotation |
| Trust a caller identifier in a header | Not signed by anything the gateway trusts; any caller can claim to be any agent |
| Mutual TLS with client certificates | Certificates in agent containers are secrets to issue, store and rotate; hosted agent runtimes may not support presenting them |
| Accept both keys and tokens | Two authentication paths double the attack surface, and the weaker one becomes the one everyone uses |
