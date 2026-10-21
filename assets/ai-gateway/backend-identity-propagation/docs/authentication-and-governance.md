# Authentication and Governance

This document lists the identities, role assignments, key and secret layouts, and governance processes that [Backend Identity Propagation](../README.md) depends on. The design rationale is in [architecture.md](architecture.md).

> Role names, Key Vault operations and Entra features are described qualitatively. Verify them against current documentation before you assign roles or automate rotation.

## 1. Identity inventory

| Identity | Type | Used for | Must never |
|---|---|---|---|
| **MCP client or agent** | Public or confidential client in the customer tenant | Obtains the inbound token for the gateway audience | Be trusted for user identity beyond the validated token |
| **Gateway resource app** | Entra app registration (multi-tenant) | Audience of inbound tokens; confidential client for on-behalf-of | Hold a client secret in configuration |
| **Gateway managed identity** | User-assigned managed identity on API Management or the MCP adapter | Federated credential for the gateway app; reads Key Vault secrets; signs assertions; app-only backend calls | Be granted backend roles broader than the app-only tools need |
| **Backend app registrations** | Entra apps for Entra-protected backends (`api://contoso-erp-reporting`, `api://contoso-erp-orders`) | Audience of on-behalf-of and app-only tokens | Accept tokens for the gateway's audience |
| **Assertion issuer** | Logical issuer `https://mcp.contoso.example/assertions` with a Key Vault or Managed HSM key | Signs Option B assertions | Share its private key with any backend |
| **Gateway client certificate** | Certificate in Key Vault | mTLS from gateway to backend | Be reused for any other client |
| **Link service identity** | Managed identity of the identity link service | Reads and writes the link store; emits link audit events | Be callable from MCP clients except through the link page |
| **Agent identities** | Managed identity or Microsoft Entra Agent ID (verify current availability) | Autonomous agents acting on their own behalf | Use a cached user credential |

## 2. Role assignments

Grant the smallest scope that works. Prefer per-vault separation over fine-grained per-secret assignments when the number of tenants is large.

| Principal | Scope | Role or permission | Purpose |
|---|---|---|---|
| Gateway managed identity | Tenant-keys vault | Key Vault Secrets User (built-in role) | Read per-tenant API keys (Option C) |
| Gateway managed identity | Signing vault or Managed HSM key | Key Vault Crypto User, or a custom role limited to the sign operation | Sign assertions (Option B) |
| Gateway managed identity | Certificates vault | Read access to the client certificate secret | mTLS to backends |
| Gateway managed identity | Gateway app registration | Federated identity credential (subject = the managed identity) | Client assertion for on-behalf-of |
| Gateway app registration | Backend APIs | Delegated permissions such as `Reports.Read` (admin consent per customer tenant) | On-behalf-of |
| Gateway managed identity | Backend APIs | Application roles such as `Reference.Read` | App-only calls |
| Link service identity | Link store | Data reader and writer | Link lifecycle |
| Operators | Vaults | No standing data-plane access; just-in-time elevation | Break-glass rotation |

Rules:

- No human has standing **read** access to tenant API keys or the signing key.
- Separate vaults for (1) tenant API keys, (2) the signing key and (3) certificates. A role assignment mistake on one does not expose the others.
- Enable purge protection and soft delete on all three vaults. Disable public network access and reach them through private endpoints.
- Review role assignments on a schedule and alert on new assignments to these vaults.

## 3. Key Vault layout and naming

| Vault | Object | Name pattern | Example |
|---|---|---|---|
| Tenant keys | Secret | `tenant-{tenantId}-{api}-api-key` | `tenant-fabrikam-orders-api-key` |
| Signing | Key (EC P-256 or RSA, non-exportable) | `gw-assert-{yyyy}-{mm}` | `gw-assert-2026-10` |
| Certificates | Certificate | `gw-mtls-{backend}` | `gw-mtls-erp` |

- Tag tenant secrets with `tenant`, `api`, `owner` and `sunset` so inventory and expiry reports can be generated.
- Set an expiry date on every tenant secret and certificate; alert before expiry.
- The key name doubles as the JWS `kid`, so backends can tell keys apart during rotation.
- Never put a tenant key into an API Management named value or a policy file; resolve it by name at request time.

## 4. Rotation runbooks

### 4.1 Assertion signing key (Option B)

1. Create the next key (`gw-assert-2026-11`) in the signing vault.
2. Publish its public key in the JWKS alongside the current key.
3. Wait until every backend has refreshed its cached JWKS (confirm with backend telemetry, not a timer alone).
4. Switch the gateway's signing configuration to the next key.
5. After the maximum assertion lifetime plus the JWKS cache lifetime, remove the old key from the JWKS and disable it.

Emergency rotation: remove the compromised `kid` from the JWKS first, then switch signing, then notify backend owners to refresh. Expect a short burst of rejected calls.

### 4.2 Per-tenant API key (Option C)

1. Generate a new key in the backend for the tenant (the backend should allow two active keys per tenant).
2. Write it to `tenant-{tenantId}-{api}-api-key` as a new secret version.
3. Let the gateway cache expire, or invalidate it; confirm calls succeed with the new key.
4. Revoke the old key in the backend.

### 4.3 Gateway client certificate (mTLS)

1. Issue the new certificate in Key Vault; add its thumbprint to the backend allow-list.
2. Switch the gateway to the new certificate.
3. Remove the old thumbprint after traffic has moved. If assertions carry `cnf.x5t#S256`, the change is automatic, because each assertion names the certificate it was sent with.

### 4.4 Federated identity credential (Option A)

There is no secret to rotate. Govern the federated credential itself: who can add or change federated credentials on the gateway app registration, and alert on changes.

## 5. Identity link governance

- **Ownership.** Each customer tenant designates link administrators. Contoso ERP operations owns the link service but does not create links for customers except through audited support requests.
- **Provisioning.** Prefer SCIM. Admin mapping requires a second approver for bulk changes. Just-in-time linking requires the user to prove control of both identities and never stores the backend credential.
- **Keys.** Links are keyed on issuer, `tid` and `oid`. Email and UPN may be displayed, never matched.
- **Conflicts.** A second identity claiming an already-linked backend user returns `409 conflict` and raises an alert to the tenant's link administrators.
- **Recertification.** Link administrators review active links on a schedule; links unused for a long period are suspended automatically.
- **Offboarding.** SCIM deprovisioning or the tenant offboarding runbook revokes links and publishes unlink events to invalidate gateway caches.
- **Audit.** Every create, verify, suspend, revoke and conflict event is recorded with the actor, method and reason.

## 6. Consent and Conditional Access (Option A)

- The gateway app's delegated permissions to each Entra-protected backend need **admin consent in each customer tenant**. Provide an onboarding page with the admin consent link and a check that reports which tenants are missing consent.
- Customer tenants may apply Conditional Access to the backend API. The gateway can't satisfy interactive requirements; it returns `interaction_required` with the claims challenge so the client can re-authenticate.
- Classify errors for operators and clients:

| Class | Example | Client reason | Retry |
|---|---|---|---|
| Consent | Missing delegated permission consent | `consent_required` | After admin consent |
| Interaction | MFA or device compliance required for the downstream API | `interaction_required` | After re-sign-in with claims |
| Block | Conditional Access block | `access_blocked` | No |
| Configuration | Invalid scope, wrong audience, federated credential mismatch | `backend_auth_failed` | No; operator fix |
| Transient | Token endpoint unavailable or throttled | `temporarily_unavailable` | Yes, with backoff |

Error codes vary by identity provider and change over time; map them in one place and verify against the current error reference.

## 7. Audit fields and queries

The audit record ([architecture.md, section 14](architecture.md#14-audit)) carries `requestId`, `tenant`, `sub`, `azp`, `tool`, `propagationOption`, `backendAudience`, `backendUserId`, `assertionJti`, `cacheHit` and `outcome`, plus the coarse client-facing `reason` on denials. A Log Analytics sketch, assuming a custom table `McpPropagation_CL`:

```kusto
McpPropagation_CL
| where TimeGenerated > ago(1d)
| summarize calls = count(),
            denied = countif(outcome != "allowed"),
            unlinked = countif(reason == "identity_not_linked")
          by tenant, propagationOption, bin(TimeGenerated, 1h)
| order by TimeGenerated desc
```

Join with backend logs on `requestId` and `assertionJti` to prove that every backend call was made by the gateway for a specific user:

```kusto
McpPropagation_CL
| where propagationOption == "signed-assertion"
| join kind=leftanti (ErpBackendAudit_CL | project assertionJti = jti) on assertionJti
| project TimeGenerated, requestId, tenant, tool, assertionJti
```

Rows from the second query are assertions the gateway minted that the backend never logged; investigate them alongside backend calls with no matching gateway record.

## 8. Diagnostics hygiene

- Exclude `Authorization`, `X-User-Assertion`, `X-Api-Key` and `Cookie` from API Management diagnostic header logging; log no request or response bodies on MCP routes.
- Log hashes or short hash prefixes, never raw tokens or keys.
- Mask link tickets and one-time codes in link page URLs and logs.
- Restrict who can read audit tables; they contain user identifiers.

## 9. Isolation tests (required before go-live)

For each user-scoped tool and each option, with two users in the same tenant and one user in another tenant:

1. User 1 and user 2 call the same tool; each sees only their own rows.
2. User 2 calls immediately after user 1 (warm cache); still only their own rows.
3. User 1 sends a forged `X-Acting-User`, `X-User-Assertion` or `user_id` argument naming user 2; the gateway strips or ignores it.
4. A user from the other tenant calls with a valid token; the backend returns no rows from the first tenant (`wrong_instance` or empty).
5. The inbound token is replayed directly at the backend; the backend rejects it on audience.

The [simulator](../samples/README.md) covers these in memory; repeat them against staging with real identities.
