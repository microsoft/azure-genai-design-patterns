# ADR 0002: Prefer signed user assertions over shared per-tenant API keys

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md), [Authentication and governance](../authentication-and-governance.md), [ADR 0001](0001-never-pass-through-client-tokens.md)

## Context

Some Contoso ERP modules (for example `field-service` and `orders`) don't trust the customer's identity provider. They have their own user table per customer tenant, and today they authenticate integrations with a per-tenant API key. On-behalf-of token exchange (Option A) isn't possible for them without a backend identity rewrite.

Two ways remain to carry the user to those modules:

- **Option C, credential bridge.** The gateway reads the tenant's API key from Key Vault and adds an `X-Acting-User` header. The backend trusts the header because the request arrived with the tenant key over the gateway's network path.
- **Option B, signed assertion.** The gateway resolves an identity link (`iss`, `tid`, `oid` to backend tenant and user) and mints a short-lived JWS (RFC 7515, RFC 7519) signed with a non-exportable Key Vault key. The backend verifies it against the gateway's JWKS (RFC 7517) and enforces its own RBAC for `sub`. The call travels over mTLS (RFC 8705), and the assertion is bound to the gateway's client certificate with `cnf`.

## Decision

1. **Option B is the target for every backend with its own identity store.** New modules and modules that already have user-level authorization implement assertion verification: `alg` allow-list, `kid` from the JWKS, `iss`, `aud`, `typ`, short `exp` and maximum lifetime, `jti` replay cache, tenant-to-instance check, local user check, and `cnf` match against the mTLS connection.
2. **Option C is allowed only as a transitional step**, with an owner, a sunset date recorded in configuration, and these compensating controls:
   - Per-tenant keys in a dedicated vault, resolved by name at request time and never stored in policy or named values.
   - The backend accepts the tenant key only over the gateway's private path, and ideally only with the gateway's client certificate.
   - The gateway strips any client-supplied acting-user header and sets it only from a resolved identity link.
   - A tool allow-list limits bridged modules to low-risk operations; writes and destructive tools wait for Option B.
   - Alerts on any use of a tenant key from outside the gateway.
3. **Both options share the identity link store.** Moving a module from C to B changes only the credential, not how users are linked.
4. **Keys and links are governed data.** Signing key rotation, link provisioning and offboarding follow [authentication-and-governance.md](../authentication-and-governance.md).

## Consequences

### Positive

- The backend can verify, per call, which user the gateway acted for and which client or agent was the actor (`act`), so non-repudiation doesn't rely on gateway logs alone.
- A stolen assertion is short-lived, single-use, audience-bound and useless without the gateway's certificate.
- The signing key never leaves Key Vault or the HSM; there is no shared secret for backends to leak.
- One assertion format works for every module with its own user store.

### Trade-offs

- Backends must implement JWS verification, JWKS caching, a replay cache and mTLS. That is real engineering work for the ISV.
- Signing adds a Key Vault (or HSM) operation per backend call. Plan signing capacity and failure handling; the gateway fails closed when signing is unavailable.
- Clock synchronization between gateway and backend matters, because assertion lifetimes are short.
- Identity links need lifecycle tooling (SCIM, admin mapping, just-in-time linking, recertification) before Option B can go live for a tenant.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Option C permanently | The backend can't verify the user, a leaked tenant key plus network access lets anyone impersonate any user in the tenant, and audit depends on gateway logs alone |
| Symmetric (HMAC) assertions with a secret shared per backend | Every backend that verifies can also mint; secret distribution and rotation multiply with the number of backends |
| Make every backend trust Entra and use on-behalf-of | Right long-term for some modules, but requires each backend to adopt a new identity model and every customer tenant to consent; Option B works with the backend's existing user table now |
| Store each user's backend password or personal API key in the gateway | Creates a credential store of every user, can't be scoped or rotated centrally, and breaks when users change passwords |
