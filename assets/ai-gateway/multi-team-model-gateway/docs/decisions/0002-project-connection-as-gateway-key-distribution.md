# ADR 0002: Distribute each team's gateway key through a connection in its own Foundry project

- **Status:** Accepted
- **Date:** 2026-10-21
- **Scope:** How team gateway keys are stored, read, rotated and revoked; the Foundry projects and connections created by [team.bicep](../../infra/modules/team.bicep); and the client in [team_client.py](../../src/team_client.py)
- **Related Documentation:** [Asset overview](../../README.md) · [Architecture](../architecture.md) · [Authentication and governance](../authentication-and-governance.md) · [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md)

## Context

[ADR 0001](0001-apim-product-per-team-as-quota-counter-key.md) ties each team's budget to an APIM subscription key. That key is a secret, and it has to reach the team's developers and apps somehow. The requirements are:

- No team ever handles a raw model key or another team's gateway key.
- Access to a key follows team membership in Microsoft Entra ID, so joining or leaving a team needs no secret handling.
- Keys never appear in `.env` files, pipeline variables, deployment outputs or chat messages.
- Rotation does not need each team to update its configuration.
- Teams already work in Microsoft Foundry projects, one per team, with project-scoped roles.

A Foundry project connection of category `ApiManagement` stores a target URL and an `ApiKey` credential. A project member with the right role can read it with `connections.get(name, include_credentials=True)`. With `isSharedToAll: false`, the connection belongs to one project only.

## Decision

1. Each team has its own Foundry project, `team-<name>`, under the shared Foundry account.
2. The deployment writes the team's subscription key into connection `ai-gateway-team-<name>` in that project: category `ApiManagement`, `authType: ApiKey`, `isSharedToAll: false`, target `<gatewayUrl>/<apiPathPrefix>`, and metadata with the APIM resource ID and type `azure_api_management`. The key is read with `listSecrets()` inside the template and is never output.
3. Team members are given the Foundry User role on their own project only. They get no role on the account, the model, APIM or any other project.
4. Clients sign in to their project with `DefaultAzureCredential`, read the connection, and create an `AzureOpenAI` client from its target and key. They keep the key in memory only.
5. Connection names carry the team name, because connection names must be unique across the whole account even when connections are not shared.
6. To rotate, the platform team regenerates the key and redeploys. Clients re-read the connection once when they get a 401.

## Consequences

### Positive

- Access to a team's key is managed by Entra group membership on the team's project. Joining, leaving and access reviews use the same process as the rest of the project.
- Configuration holds only the project endpoint and connection name. Neither is a secret.
- Rotation is central. Running clients recover after one 401 without a redeploy.
- Agents and tools in the project can use the same connection.

### Trade-offs and operational obligations

- The key is still a shared secret once read. Anyone who can read it can copy it, so rotate after a member with access leaves under unclear circumstances.
- The design depends on Foundry User being allowed to read connection credentials. That is true in the proof of concept behind this asset; verify it in your tenant.
- Connection resources use a preview API version. Expect schema changes.
- Incremental deployments do not delete connections. Retiring a team needs explicit deletion.
- The gateway URL appears as the connection target in every project. It is not secret, but plan for the change if you add a custom domain.

## Alternatives considered

| Alternative | Why it was rejected | When to reconsider |
|---|---|---|
| **Key in `.env` or app settings** | Copies the secret into many places; rotation means touching every copy; no link to team membership | Never for shared environments |
| **Key Vault secret per team** | Works, but adds a vault, a role and secret-reading code per team, and duplicates the project as an access boundary | If teams do not use Foundry projects, or need vault features such as expiry notifications |
| **One connection shared to all projects** | Every team could read every key | Never |
| **Entra-only gateway auth, no key** | Removes the shared secret, but needs `validate-jwt`, an app role per team and an Entra connection auth type | When your Foundry release supports it for `ApiManagement` connections; see [authentication-and-governance.md](../authentication-and-governance.md#7-entra-only-alternative) |
| **Platform team hands out keys** | Manual, untracked and hard to rotate | Never |

## Follow-ups

- Run the two-principal protocol in [authentication-and-governance.md](../authentication-and-governance.md#5-two-principal-isolation-protocol) in each environment.
- Add an alert for 401 spikes per subscription, a sign of a leaked or stale key.
- Review the Entra-only alternative when connection auth types allow it. [Backend Identity Propagation](../../../backend-identity-propagation/README.md) covers passing user identity through a gateway.
