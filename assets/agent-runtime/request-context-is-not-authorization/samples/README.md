# Samples - Request Context Is Not Authorization

`request_context_guard.py` is a self-contained, standard-library-only reference (Python 3.10+). It shows the full pattern at one tool boundary:

- an identity verifier;
- a context bag that marks everything else as tainted;
- a deny-by-default policy decision point (PDP) with reason codes;
- a tool policy enforcement point (PEP) that re-checks a live entitlement store on every call;
- a JSON-lines audit log.

It has no network access and no Azure dependencies. The token format is an HMAC-signed **test** token, not a JWT. Do not reuse `HmacTestVerifier` outside tests.

## Run it

```powershell
# Adversarial self-test (exit code 0 = all scenarios passed, 1 = at least one failed)
python samples\request_context_guard.py --self-test

# Same scenarios against a specific domain pack, or all of them
python samples\request_context_guard.py --self-test --domain fabrikam-health
python samples\request_context_guard.py --self-test --domain all

# Narrated demo that prints the audit log as JSON lines (default when no flag is given)
python samples\request_context_guard.py --demo --domain northwind-retail
```

Run the commands from the asset folder. If you run from elsewhere, use the full path to the script. Set `PYTHONDONTWRITEBYTECODE=1` if you do not want a `__pycache__` folder.

## What each component demonstrates

| Component | Role in the pattern | Production equivalent |
|---|---|---|
| `IdentityVerifier` (Protocol) | The only way to get a `VerifiedPrincipal` | Microsoft Entra token validation in your API or tool server |
| `HmacTestVerifier` | In-memory fake. It pins the algorithm and checks signature, issuer-per-tenant, audience, `nbf`/`exp`, the tenant allow-list and subject presence. | Test double only |
| `EntraJwksVerifier` | Production seam. It raises `NotImplementedError`; its docstring lists the validation steps. | A maintained OIDC/JWT library configured for Entra |
| `VerifiedPrincipal` | Sealed result of verification; application code cannot construct one | The claims principal your framework produces after validation |
| `ContextBag` / `Tainted` | Holds headers, payload, tool args, retrieved content, memory and model output with provenance and a taint class | Request-scoped context object in the agent or tool host |
| `PolicyDecisionPoint` | Deny by default. Refuses anything that is not a `VerifiedPrincipal` (`TAINTED_ATTRIBUTE`) and returns a `Decision` with a reason code. | Policy-as-code engine or an authorization service |
| `InMemoryEntitlementStore` | Live source of truth with `grant` / `revoke`, a `version` counter, and a `reads` counter used to prove live checks | Entitlement or access-review store, or a resource-side ACL |
| `ToolPEP` | Verify -> resolve resource server-side -> reject tenant-like args that disagree with the token -> PDP -> handler only on allow -> audit | Middleware in the tool server, MCP server, or API |
| `AuditLog` | One JSON line per decision. Logs keys and provenance only, never tainted values. | Azure Monitor / Log Analytics, or an append-only audit store |
| `HeaderTrustingAuthorizer`, `SessionSnapshotAuthorizer` | **Anti-pattern baselines**, used only to show contrast in the self-test | None. Do not copy them. |

## Scenarios

| # | Scenario | Attack or condition | Expected PEP outcome | Baseline shown |
|---|---|---|---|---|
| 1 | `valid-allow` | Verified user with role and a live entitlement | `ALLOW`, exactly one live entitlement read | |
| 2 | `spoofed-header` | `x-user-id`, `x-tenant-id` and `x-roles` set by the caller | No token: `NO_VERIFIED_IDENTITY`. Real token plus spoofed role headers: `ROLE_NOT_GRANTED`. The conflict is recorded in the audit record. | Header-trusting authorizer **allows** |
| 3 | `injected-tenant-in-tool-args` | Model-generated `tenant_id` argument, or a resource in another tenant | `TENANT_MISMATCH`, with `injection_suspected=true` for the argument case | |
| 4 | `model-echoed-admin-role` | A role string from model output passed as the subject; a forged principal; an `as_role` argument | `TAINTED_ATTRIBUTE`; constructing the principal raises `TypeError`; `ROLE_NOT_GRANTED` | |
| 5 | `revocation-between-turns` | Entitlement revoked between turn 1 and turn 2 while the token is still valid | Turn 1 `ALLOW`, turn 2 `ENTITLEMENT_REVOKED`, two live reads | Session snapshot **still allows** |
| 6 | `missing-identity` | No bearer, empty bearer with a user-id header, blank `Bearer` | `NO_VERIFIED_IDENTITY` (never anonymous-allow), zero store reads | |
| 7 | `tampered-token` | Edited `roles` claim; `alg=none` | `TOKEN_INVALID` | |
| 8 | `expired-or-misaddressed-token` | Expired, aged past `exp`, wrong audience, issuer/tenant mismatch, tenant not on the allow-list | `TOKEN_INVALID` | |
| 9 | `cross-tenant-memory-read` | Reading your own thread, another tenant's thread, or a colleague's thread | `ALLOW`, `TENANT_MISMATCH`, `NOT_ENTITLED` | |
| 10 | `confused-deputy` | The agent's own app token plus an on-behalf-of header; a delegated token for an unentitled user | `DELEGATION_REQUIRED`; `NOT_ENTITLED`, even though the agent identity itself is entitled | |
| 11 | `audit-completeness` | Five mixed calls, a hostile correlation header, and injected note text in the args | Five audit events, each with a valid reason code. The correlation is marked untrusted and sanitized, and the injected text never appears. | |

Each scenario builds a fresh `World`, so scenarios never share state. A scenario that raises an exception counts as a failure.

## Reason codes

| Reason code | Meaning | Typical cause |
|---|---|---|
| `ALLOW` | Every check passed | |
| `NO_VERIFIED_IDENTITY` | No credential was presented | Caller relied on headers; a hop dropped the token |
| `TOKEN_INVALID` | A credential was presented but failed verification | Expired, tampered, wrong audience or issuer, tenant not allowed, wrong algorithm |
| `TAINTED_ATTRIBUTE` | The PDP was handed something other than a verified principal | Code path built a subject from context or model output |
| `TENANT_MISMATCH` | The resource tenant or a tenant-like argument differs from the verified `tid` | Cross-tenant access attempt or argument injection |
| `DELEGATION_REQUIRED` | The action needs a delegated user, but the caller is app-only | Agent using its own identity for user-scoped data |
| `RESOURCE_UNKNOWN` | The resource selector did not resolve in the server-side registry | Hallucinated or probing identifier |
| `NO_MATCHING_RULE` | No policy rule for the action or tool | New tool registered without policy (deny by default) |
| `ROLE_NOT_GRANTED` | The verified token lacks the required role | Role claimed in headers or arguments, not in the token |
| `NOT_ENTITLED` | No live entitlement, or not the owner of an owner-only resource | User never granted; colleague's thread |
| `ENTITLEMENT_REVOKED` | The entitlement existed but has been revoked | Access removed between turns |

## Production seams

- Replace `HmacTestVerifier` with an `EntraJwksVerifier` implementation built on a maintained library. Keep the `IdentityVerifier` Protocol so the PEP code does not change.
- Replace `InMemoryEntitlementStore` with your entitlement source of truth. If you add a cache, keep it short-lived, invalidate it on revocation events, and include `entitlement_version` in decisions so audits can show which state was used.
- Replace the in-code rule map with policy as code (versioned, reviewed, tested). Keep the reason-code catalog stable.
- Ship `AuditLog` records to a durable, access-controlled sink. Do not add tainted values to the record.
- Configure `platform_attested_headers` only for headers your hosting platform injects **at this hop**. At a tool server reached through an agent, the expected value is empty: anything the agent forwards is agent-asserted.

See [../docs/architecture.md](../docs/architecture.md) for the full design and [../docs/evaluation-and-troubleshooting.md](../docs/evaluation-and-troubleshooting.md) for how to extend this suite in CI.
