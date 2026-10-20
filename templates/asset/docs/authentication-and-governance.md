# Authentication, Identity, and Governance

This document separates the identity layers involved in this pattern, specifies how caller identity and entitlements flow to downstream systems of record, classifies authentication failure codes, and defines the two-principal isolation verification protocol.

Related documents:

- [Architecture](architecture.md)
- [Evaluation, release gates, and troubleshooting](evaluation-and-troubleshooting.md)

## 1. Identity layer inventory

| # | Identity Layer | Principal Type | Used For | Common Misconception |
|---|---|---|---|---|
| 1 | **Distribution / Package Identity** | App manifest ID / catalog entry | Admin approval, distribution scope, version pinning | Does not grant API or data permissions |
| 2 | **Agent / Runtime Identity** | Microsoft Entra Agent ID or Managed Identity | Channel authentication, telemetry emission, platform control plane | Must not stand in for the signed-in user on user-scoped data reads/writes |
| 3 | **Signed-In End User** | Microsoft Entra user principal (`oid`, `tid`) | Subject of delegated access and per-user daily caps | Prompt text cannot override `oid` or `tid` claims |
| 4 | **Downstream OBO / Tool Connection** | OAuth 2.0 On-Behalf-Of (OBO) or scoped App Role | Calling MCP servers, databases, or APIs with audience-specific tokens | Never forward an inbound token to a downstream API without an OBO exchange |
| 5 | **Accountable Human Sponsor** | Business owner, Entra Agent ID sponsor, maintainers | Access reviews, credential/sponsor rotation, incident response | Every agent identity requires a named human sponsor and offboarding runbook |

## 2. Token validation and error classification matrix

| Failure Category | Representative Codes / Conditions | Root Cause | Runtime Remediation |
|---|---|---|---|
| **Missing or malformed token** | `HTTP 401`, missing `Authorization: Bearer` | Unauthenticated caller | Return `401` with `WWW-Authenticate` RFC 9728 resource metadata challenge |
| **Audience / issuer / tenant mismatch** | `bad_aud`, `bad_iss`, `bad_tid` | Token minted for another service or foreign tenant | Reject immediately (`401`/`403`); emit structured auth audit record |
| **Conditional Access / CAE step-up** | `AADSTS50076`, `AADSTS53003`, `insufficient_claims` (`cp1`) | MFA or device compliance required mid-session | Purge assertion-bound token cache; return interactive step-up card or abort unattended run |
| **Consent required** | `AADSTS65001` | User or admin has not consented to downstream scope | Surface incremental consent card on interactive turns |
| **Insufficient entitlement** | `HTTP 403`, missing `roles` / `scp` or tenant scope | Caller authenticated but lacks `operate` role or row entitlement | Fail closed (`403`); do not reroute or retry |

## 3. Two-principal isolation verification protocol

Before release, automated tests must verify isolation using two synthetic principals (`Principal A` in `Scope-A`, `Principal B` in `Scope-B`):

1. **Positive scope check:** `Principal A` lists only `Scope-A` records; `Principal B` lists only `Scope-B` records.
2. **Cross-scope ID probe:** `Principal A` requests `Principal B`'s record ID -> rejected (`403`/`404`) with zero data leakage.
3. **Argument-injection override:** `Principal A` passes `{"tenant_id": "Scope-B"}` in tool arguments -> server ignores payload identity fields and enforces `Scope-A` from validated JWT claims.
4. **Telemetry redaction check:** Zero raw JWTs or secrets appear in logs or OpenTelemetry spans.
