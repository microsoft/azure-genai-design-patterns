# ADR 0002: Agent identity gets least-privilege data-plane roles

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Authentication and governance](../authentication-and-governance.md#role-assignments), [Architecture](../architecture.md#7-identity-model), [Asset README](../../README.md)

## Context

A hosted agent's code runs as an identity inside each session sandbox. In preview, Foundry creates an agent identity blueprint and an instance identity for the agent version, and the instance identity starts with no data-plane roles **(observed in preview; verify against current docs)**. The first real turn then fails with an authentication error from the model client or the Cosmos DB client, which surfaced as 401 rather than 403 in testing.

The quick fixes are harmful: granting Contributor (a control-plane role that does not grant data access and adds management rights), granting broad roles at subscription scope, or re-enabling key-based authentication "as a fallback". Each turns a prompt-injection or code defect into wide data access, and keys bypass Entra audit.

## Decision

1. The runtime identity holds only data-plane roles, at the narrowest scope that works:
   - **Cognitive Services OpenAI User** on the Foundry account, for model calls (also observed to be needed for memory writes).
   - **Cosmos DB Built-in Data Contributor** on the specific Cosmos DB account or database, assigned through Cosmos DB SQL role assignments.
   - **Foundry User** (formerly named Azure AI User) on the project, only if the agent calls project-level services.
   - **Monitoring Metrics Publisher** on Application Insights, only if telemetry ingestion uses Entra authentication.
   - The MCP gateway's app role, for tools.
   Verify every role name and scope against current Microsoft Learn documentation.
2. The runtime identity never holds Owner, Contributor, Cognitive Services Contributor, User Access Administrator or any control-plane role.
3. Local (key) authentication is disabled on every dependency that supports Entra authentication. There is no key-based fallback in code, configuration or images.
4. A separate role-assignment identity grants these roles, limited by conditions to this list. The release pipeline that deploys code cannot grant its own runtime new roles.
5. The image pull path uses its own identity with AcrPull; the pipeline uses AcrPush. The runtime identity has no registry role.
6. If the instance principal changes per version, the pipeline grants roles to the new principal before the smoke turn and removes them when the version is retired.
7. Role definition ids are parameters in infrastructure code. Ids are hardcoded only where they are well-known public built-in ids.

## Consequences

### Positive

- A compromised or misbehaving agent can reach only its own model deployments, its own state and its own tools.
- Missing roles are caught by the smoke turn with a precise message instead of in production.
- Audit logs attribute every data access to a named principal.

### Trade-offs

- More role assignments to manage, especially if principals are per version.
- Propagation delay after granting roles; the pipeline waits with a bounded retry.
- Cosmos DB data-plane roles use a separate assignment mechanism from Azure RBAC, which teams often miss.
- Role names have changed over time; documentation and templates need periodic review.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Contributor on the resource group | Control-plane rights; does not grant data access; violates least privilege |
| Key-based auth with keys in Key Vault | Keys leak, bypass Entra audit and mask role problems |
| One shared identity for pipeline and runtime | No separation of duties; pipeline compromise equals data access |
| Grant roles at subscription scope for simplicity | Blast radius covers unrelated workloads |
| Let the pipeline grant its own runtime roles | Allows privilege escalation in a single unreviewed change |
