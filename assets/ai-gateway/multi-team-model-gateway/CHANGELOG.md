# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-21

### Added

- Initial preview release of the solution accelerator for a multi-team model gateway: several product teams share one Microsoft Foundry model deployment pool behind Azure API Management as an AI gateway, illustrated with a fictional company, Contoso, and two teams, `alpha` and `beta`.
- `README.md` with the problem, the solution overview, an architecture diagram, when to use and when not to use, key design decisions, a quickstart (deploy the infrastructure, run the offline simulator, run the team client), related assets, the production-readiness pillar mapping, security considerations, and limitations.
- `docs/architecture.md` covering the components, the request flow, the identity model, the per-team quota model, chargeback telemetry, failure modes, and alternatives (a deployment per team, Entra-based gateway authentication).
- `docs/authentication-and-governance.md` covering the identity inventory, who can do what, the gateway key lifecycle (issue, read, rotate, revoke), error classification, a two-principal isolation protocol, governance guardrails, and an Entra-only alternative marked "verify in your tenant".
- `docs/evaluation-and-troubleshooting.md` covering the offline self-test, live checks and exit codes, release gates, triaging a 429 (gateway budget versus model capacity), a symptom-to-fix table, example KQL for per-team usage, and alert suggestions.
- ADR 0001: use one API Management product per team as the token-limit counter key.
- ADR 0002: distribute each team's gateway key through a non-shared Foundry project connection.
- ADR 0003: share one model deployment with per-team gateway quotas instead of a deployment per team.
- `samples/team_quota_gateway.py`, a stdlib-only simulator of per-team token budgets, prompt-token estimation, 429 with `retry-after`, remaining and consumed token headers, inbound credential stripping, connection-name uniqueness, a chargeback ledger, and key-misuse checks, with a `--demo` mode and an offline `--self-test` covering 35 scenarios.
- `samples/README.md` describing how to run and extend the simulator.
- `src/team_client.py`, `src/run_team.py`, and `src/tpm_burst_test.py`: Python client code that resolves a team's gateway client from its Foundry project connection with `DefaultAzureCredential`, a single-team CLI, and a burst test that shows one team's budget being exhausted while another team keeps working. Includes `src/requirements.txt`, `src/.env.sample` (placeholders only, no keys), and `src/README.md`.
- `infra/main.bicep` and modules for the Foundry account and model deployment, the API Management gateway with a system-assigned managed identity, the per-team product, subscription, Foundry project, project connection, and project-scoped Foundry User role assignment, and the gateway's role assignment on the Foundry account.
- `infra/policies/` with the API-scope gateway policy, the per-team token-limit product policy, and the token-metric policy fragment for chargeback dimensions.
- `infra/parameters.example.json` with Contoso placeholder values, `infra/pipelines/multi-team-gateway-deploy.yml` (a GitHub Actions workflow with lint, what-if, deploy, and verify stages that signs in with OpenID Connect through workload identity federation), and `infra/README.md` describing files, parameters, deployment, verification, teardown, and items to verify in your tenant.
