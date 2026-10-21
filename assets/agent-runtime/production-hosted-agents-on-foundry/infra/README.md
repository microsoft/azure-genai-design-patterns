# Infrastructure: the runtime layer on top of the landing zone

> **Illustrative and undeployed.** [`main.bicep`](main.bicep) compiles with `bicep build` but has not been deployed as-is. Hosted agents are in preview. Verify API versions, built-in role names, role definition ids and which identity Foundry uses for each step against current Microsoft Learn documentation before you adapt it.

This folder adds the **runtime layer** for hosted agents. It does not create a network. The [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md) already provides the VNet, the delegated agent subnet, private endpoints, private DNS, the private container registry, the operator runner and the baseline release pipeline. Deploy that first, then use this template to wire identities, data-plane roles and agent state on top of it.

## What the landing zone provides and what this asset adds

| Concern | Provided by the landing zone | Added here |
|---|---|---|
| VNet, subnets, private endpoints, private DNS | Yes ([landing zone infra](../../../landing-zones-and-infra/secured-foundry-landing-zone/infra/README.md)) | Nothing. Do not add subnets out of band; the landing zone owns the subnet array |
| Private container registry and its private endpoint | Yes | AcrPush for the release identity; AcrPull for the image pull identity |
| Foundry account and project (private) | Yes | Project-scoped data-plane role for the release identity and, optionally, the caller application |
| In-network runner | Yes (operator VNet VM, no public IP) | A release identity to attach to it, with an optional federated credential |
| Cosmos DB account (local auth disabled) | Yes | A database and containers for threads, checkpoints, idempotency records and the session registry; a data-plane role scoped to that database |
| Log Analytics and Application Insights | Yes | Optional Monitoring Metrics Publisher for the runtime identity when ingestion uses Entra authentication |
| Agent versions and sessions | No | No. These are data-plane objects the pipeline creates from inside the VNet, not Azure Resource Manager resources (observed in preview; verify) |

## Resources and role assignments

| Resource or assignment | Principal | Scope | Role | Why |
|---|---|---|---|---|
| `id-<prefix>-release` | Release pipeline | Resource group | n/a (created) | One identity for the whole release flow, attached to the in-network runner or federated to CI |
| Federated credential | Release pipeline | Release identity | n/a (optional) | Lets CI exchange its OIDC token without a stored secret |
| Cosmos DB database and four containers | n/a | Landing zone Cosmos DB account | n/a (created) | Durable agent state outside the session sandbox |
| `releaseAcrPush` | Release identity | Registry | AcrPush | Push images by digest |
| `releaseProjectUser` | Release identity | Foundry project | Foundry User (formerly named Azure AI User) | Create versions, run smoke turns, switch the active version |
| `imagePullAcrPull` | Image pull identity | Registry | AcrPull | Pull the image into session sandboxes |
| `runtimeOpenAiUser` | Runtime identity | Foundry account | Cognitive Services OpenAI User | Call model deployments |
| `runtimeCosmosData` | Runtime identity | Cosmos DB database | Cosmos DB Built-in Data Contributor (SQL role) | Read and write threads, checkpoints, idempotency records |
| `runtimeMetricsPublisher` | Runtime identity | Application Insights | Monitoring Metrics Publisher (optional) | Telemetry ingestion with Entra authentication |
| `callerProjectUser` | Caller application | Foundry project | Foundry User (optional) | Invoke the agent through the private endpoint |

Role assignment names use `guid(scope, principal, roleDefinitionId)`, so re-running the template is idempotent. Role definition ids are parameters. Only the well-known AcrPull and Foundry User ids carry defaults; the others are required, so the template never guesses an id. If your registry uses repository-scoped (ABAC) permissions, the registry role names differ; check the current registry documentation.

## Cosmos DB containers

| Container | Partition key | TTL | Holds |
|---|---|---|---|
| `threads` | `/conversation_key` | On, per item | Conversation history the agent rehydrates on a new session |
| `checkpoints` | `/conversation_key` | On, per item | Workflow or graph checkpoints at turn boundaries |
| `idempotency` | `/conversation_key` | `idempotencyTtlSeconds` from your retention policy | `key -> result` records that stop duplicate side effects |
| `sessions` | `/agent_name` | On, per item | Session registry used by the orphan sweeper (session id, version, owner, last user activity) |

Platform memory was observed to lack VNet integration in preview, which is why state lives in your own private Cosmos DB account (verify against current docs).

## Parameters

| Parameter | Required | Notes |
|---|---|---|
| `foundryAccountName`, `foundryProjectName` | Yes | From the landing zone outputs |
| `containerRegistryName`, `cosmosAccountName`, `appInsightsName` | Yes | From the landing zone outputs |
| `imagePullPrincipalId` | Yes | The identity Foundry uses to pull images. Current docs point to the project managed identity; verify |
| `idempotencyTtlSeconds` | Yes | Retention policy value for idempotency records |
| `roleIdAcrPush`, `roleIdOpenAiUser` | Yes | Built-in role definition ids from the Azure built-in roles reference |
| `runtimePrincipalId` | No | Empty on the first run. Set it to the instance identity once the first agent version exists |
| `callerPrincipalId` | No | The web tier or gateway managed identity |
| `federatedIssuer`, `federatedSubject` | No | Set both to federate the release identity to your CI system |
| `roleIdMetricsPublisher` | No | Only when telemetry ingestion uses Entra authentication |
| `roleIdFoundryUser`, `roleIdAcrPull`, `cosmosDataRoleId` | No | Defaults are public built-in ids; verify they are still current |

## Deploy (from inside the network)

Run these from the landing zone's in-network runner or an operator workstation that resolves the private DNS zones. Every value is a placeholder.

```bash
az bicep build --file infra/main.bicep

# First run: no runtime principal yet.
az deployment group create \
  --resource-group rg-contoso-agents-prod \
  --template-file infra/main.bicep \
  --parameters \
      foundryAccountName=<foundry-account> \
      foundryProjectName=<project> \
      containerRegistryName=<registry> \
      cosmosAccountName=<cosmos-account> \
      appInsightsName=<app-insights> \
      imagePullPrincipalId=<project-mi-principal-id> \
      idempotencyTtlSeconds=<retention-seconds> \
      roleIdAcrPush=<acrpush-role-id> \
      roleIdOpenAiUser=<openai-user-role-id>

# After the pipeline creates the first agent version and you read its instance identity:
az deployment group create ... --parameters runtimePrincipalId=<instance-principal-id> ...
```

The deployer of this template needs a control-plane role that can write role assignments (for example User Access Administrator or Role Based Access Control Administrator with a condition) on the target scopes. Keep that with the platform team; the release identity this template creates must **not** hold it. See [Separation of duties](../docs/authentication-and-governance.md).

## Runner options

| Option | Provided by | Notes |
|---|---|---|
| Self-hosted runner VM in the operator VNet | Landing zone | Attach `id-<prefix>-release` to the VM, or federate it. No public IP; NAT egress to the CI service |
| Container Apps job in a delegated subnet | Your platform team, through the landing zone subnet array | Pass the **complete** environment on every execution. Job environment variables were observed to persist between executions (verify) |
| Hosted runner on the public internet | Not supported | Data-plane calls to a private project are rejected with 403 ([ADR 0001](../docs/decisions/0001-deploy-and-invoke-from-inside-the-vnet.md)) |

Registry-side build services were observed not to build against a registry with public access disabled in preview, so build the image on the in-network runner and push by digest (verify against current docs).

## Instance identity per version

The runtime principal is the agent's instance identity, and it starts with no roles. Check in your build whether that principal is stable across versions. If it changes per version, re-run this template (or a smaller role-only deployment) with the new `runtimePrincipalId` **before** the smoke turn, and remove the old assignments when you retire the old version. The pipeline in [deployment-and-operations.md](../docs/deployment-and-operations.md) shows where that step sits.

## What is not here

- Network, DNS, private endpoints and the runner VM: see the [landing zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md).
- An MCP or outbound gateway: see [Multi-tenant MCP Gateway](../../../ai-gateway/multi-tenant-mcp-gateway/README.md).
- Alerts and dashboards: define them next to your Application Insights resource. The signals to alert on are listed in [architecture.md](../docs/architecture.md).
- Azure Policy assignments: ideas are in [authentication-and-governance.md](../docs/authentication-and-governance.md); none are deployed here.

## Validate

```bash
az bicep build --file infra/main.bicep
az deployment group what-if --resource-group <rg> --template-file infra/main.bicep --parameters <...>
```

`what-if` must also run from inside the network, because it reads the private resources.
