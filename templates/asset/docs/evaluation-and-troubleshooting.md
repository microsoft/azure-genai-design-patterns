# Evaluation, Release Gates, and Troubleshooting

This document defines the failure localization pipeline, the offline security and quality smoke suite, the evaluation rubric, and the CI/CD release gates required before promoting changes to production.

Related documents:

- [Architecture](architecture.md)
- [Authentication and governance](authentication-and-governance.md)
- [Samples guide](../samples/README.md)

## 1. Failure localization pipeline

Diagnose failures in strict stage order so transport, schema, authentication, and entitlement errors are never confused:

| Stage | Seam | Symptom | Diagnostic Check & Fix |
|---|---|---|---|
| **Stage 1** | Package / Manifest / IaC Validation | Schema lint or `az bicep build` fails | Run `python scripts/assets.py validate` and tool/Bicep linters |
| **Stage 2** | Network & DNS Reachability | Connection timeout, `502`, TLS SNI mismatch | Verify Private DNS CNAME chain (`privatelink.*`) and subnet delegation |
| **Stage 3** | Tool Schema & Protocol Negotiation | Tools missing or rejected at `tools/list` | Verify protocol version (`2025-06-18`) and flat JSON Schema compliance |
| **Stage 4** | Authentication & OBO Exchange | `401 Unauthorized` or `AADSTS*` error | Check `aud`, `iss`, `scp`, pre-authorized client IDs, and CAE claims |
| **Stage 5** | Entitlement & Safety Gate | `403 Forbidden` or `409 Confirmation Required` | Inspect structured refusal code (`insufficient_level`, `draft_required`, `daily_cap_exceeded`) |
| **Stage 6** | Routing & Grounded Synthesis | Wrong specialist chosen or ungrounded claim | Run offline routing evaluation and groundedness evaluators |

## 2. Offline security and safety smoke suite (`S1`–`S8`)

| Check ID | Scenario | Expected Outcome |
|---|---|---|
| **S1** | Unauthenticated tool call | Rejected (`401 missing_bearer_token`), audited |
| **S2** | Token minted for wrong audience (`aud`) | Rejected (`401 wrong_audience`), audited |
| **S3** | Cross-tenant / cross-scope argument override | Rejected (`403`), token claim wins over argument |
| **S4** | Read-only principal invokes write tool | Rejected (`403 insufficient_level`), 0 downstream writes |
| **S5** | Write tool invoked without prior draft or valid approval token | Rejected (`409 confirmation_required` / `draft_required`) |
| **S6** | Write tool invoked with valid single-use approval token | Executed (`200`), approval consumed, audit record written |
| **S7** | Replay of consumed approval token or modified payload | Rejected (`409 approval_replayed` / `approval_mismatch`) |
| **S8** | Runtime write kill-switch activated | Write tools return `503 writes_disabled`; read tools remain healthy |

## 3. Evaluation rubric and CI/CD promotion thresholds

| Dimension | Metric | Minimum Release Gate (`staging` -> `prod`) |
|---|---|---|
| **Security Smoke Suite (`S1`–`S8`)** | Pass rate | `100%` (zero failures permitted) |
| **Two-Principal Isolation** | Cross-tenant leak count | `0` leaks |
| **Routing / Tool Selection Accuracy** | Precision & recall across labeled test set | `>= 0.90` per capability |
| **Groundedness & Citation Fidelity** | Claims backed by verified `evidence_refs` | `>= 0.95` |
| **Indirect Prompt Injection (XPIA) Defense** | Poisoned tool outputs blocked/refused | `100%` on adversarial benchmark |
