# Infrastructure: streaming model gateway

> **Illustrative and undeployed.** These templates compile with the Bicep CLI, but they have not been deployed as-is. Review every resource, role and network setting against your landing zone before you use them. Container Apps, Key Vault and Foundry API versions change; hosted agent and Foundry connection behaviour is observed in preview; verify against current docs.

This folder deploys the gateway's own resources on top of an existing [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md). It does not create the Foundry account, model deployments, registry, network or DNS. The step-by-step guide, with verification at every phase, is [Deployment and operations](../docs/deployment-and-operations.md); this page is the reference for the files.

## Files

| File | Purpose |
|---|---|
| [`main.bicep`](main.bicep) | Gateway identity, telemetry, Key Vault, Container Apps environment, role assignments and (second pass) the gateway container app |
| [`modules/foundry-model-role.bicep`](modules/foundry-model-role.bicep) | Grants the model inference role on an existing Foundry account; returns its OpenAI v1 base URL and whether key authentication is disabled |
| [`modules/registry-pull-role.bicep`](modules/registry-pull-role.bicep) | Grants AcrPull on an existing registry; returns its login server |
| [`parameters.example.json`](parameters.example.json) | Fail-closed parameter example. Every environment-specific value is blank or zero, so validation fails until someone populates it |
| [`gateway-policy.example.json`](gateway-policy.example.json) | Example policy document (callers, routes, budget window). Identifiers only, no URLs or keys |
| [`pipelines/gateway-deploy.yml`](pipelines/gateway-deploy.yml) | Reference GitHub Actions workflow: lint, what-if, foundation, image, app, smoke, promote. OIDC only |

## What gets deployed

| Resource | Name | Notes |
|---|---|---|
| User-assigned managed identity | `<namePrefix>-gw-id` | The gateway's only identity. Used for the image pull, the Key Vault reference and every Foundry call |
| Log Analytics workspace | `<namePrefix>-law` | Retention is a required parameter. Receives environment and Key Vault logs |
| Application Insights | `<namePrefix>-appi` | Workspace-based. Receives gateway traces and `gateway.usage` events |
| Key Vault | `kv<prefix><unique>` | RBAC mode, purge protection, public network access disabled by default. Holds only the bring-your-own provider key |
| Key Vault private endpoint | `<namePrefix>-kv-pe` | Optional; created when `privateEndpointSubnetId` is set. DNS zone group when `keyVaultPrivateDnsZoneId` is set |
| Container Apps environment | `<namePrefix>-env` | Workload profiles (Consumption). Internal and joined to your subnet unless you choose otherwise |
| Gateway container app | `<namePrefix>-gw` | Second pass only (`deployGatewayApp = true`). Multiple-revision mode, port 8080, health probes on `/healthz`, HTTP concurrency scale rule |
| Role: model inference | Foundry account (and optional second account) | Gateway identity. Role definition ID is a required parameter |
| Role: Key Vault Secrets User | Key Vault | Gateway identity |
| Role: AcrPull | Registry | Gateway identity |
| Role: Key Vault Secrets Officer | Key Vault | Optional secret seeder principal only. Grant just in time and remove afterwards |

## What does not get deployed

- The Foundry account, project and model deployments. The template references an existing account by name and **reports** whether key authentication is disabled (`foundryLocalAuthDisabled`); the pipeline stops if it is not.
- The container registry, virtual network, subnets, private DNS zones and the in-network runner.
- The Entra app registration for the gateway, its `Gateway.Invoke` app role, and the role assignments to hosted agent identities. See [Register the gateway in Entra](../docs/authentication-and-governance.md#register-the-gateway-in-entra).
- Any secret value. The bring-your-own provider key is seeded into Key Vault out of band (phase 3) and read at runtime through a Key Vault reference.
- The hosted agents themselves. See [Production Hosted Agents on Foundry](../../production-hosted-agents-on-foundry/README.md).

## Prerequisites

- Azure CLI with Bicep, and `jq` for the parameter commands below.
- A workload resource group from the landing zone, and a deployment identity that can create resources there and create role assignments on the Foundry account, Key Vault and registry (scope it as narrowly as your process allows).
- A subnet delegated to `Microsoft.App/environments`, sized for your replica range, when the environment is internal (the default).
- A Foundry account with key authentication and public network access disabled, and a model deployment for each alias you plan to route.
- The gateway app registration (phase 0): tenant ID, app ID URI and client ID.
- Private DNS that resolves Foundry, Key Vault and the registry to private addresses from the gateway subnet.

## Model role definition ID

The template grants the gateway one model inference role on the Foundry account. The role definition ID is a **required** parameter with no default, so the choice of role is explicit per environment and the template never guesses. Built-in role IDs are the same in every tenant, but resolve the ID with the Azure CLI instead of hand-copying it, and put it in your populated parameters file:

```bash
az role definition list --name "Cognitive Services OpenAI User" --query "[0].name" -o tsv
```

Use the role your organisation has approved for model inference on Foundry (for example Cognitive Services OpenAI User, or Foundry User where that role is your standard). Choose the narrowest role that allows inference on the deployments you route to. See [Model role on the Foundry account](../docs/authentication-and-governance.md#model-role-on-the-foundry-account).

The Key Vault Secrets User, Key Vault Secrets Officer and AcrPull role IDs are well-known built-ins and have defaults.

## Deploy order

The template runs twice. The phases match [Deployment and operations](../docs/deployment-and-operations.md#provisioning-phases).

| Phase | Owner | What | Template switch |
|---|---|---|---|
| 0 | Identity administrator | App registration, app ID URI, `Gateway.Invoke` app role, assignment required | Not in this template |
| 1 | Platform | Landing zone checks; look up the model role definition ID | Not in this template |
| 2 | Pipeline | Foundation: identity, telemetry, Key Vault, environment, role assignments | `deployGatewayApp = false` |
| 3 | Secret owner | Seed the bring-your-own key into Key Vault from inside the network (only for a bring-your-own route) | Not in this template |
| 4 | Pipeline (in-network runner) | Build, scan and push the image; record the digest | Not in this template |
| 5 | Pipeline | Gateway app revision with the image digest and policy | `deployGatewayApp = true` |
| 6 | Identity administrator and policy owner | Assign `Gateway.Invoke` to each agent identity and add its caller entry to the policy | New revision for the policy change |
| 7 | Agent owners | Point agents at the gateway | Not in this template |
| 8 | Platform | Verify, then remove agents' direct model roles (close the bypass) | Not in this template |

### Phase 2: foundation

```bash
mkdir -p .local
jq '.gateway' parameters.example.json > .local/gateway.parameters.json
# Populate .local/gateway.parameters.json. Never commit it.

az deployment group what-if -g <resource-group> -f main.bicep \
  -p @.local/gateway.parameters.json -p deployGatewayApp=false

az deployment group create -g <resource-group> -n gateway-foundation -f main.bicep \
  -p @.local/gateway.parameters.json -p deployGatewayApp=false

az deployment group show -g <resource-group> -n gateway-foundation \
  --query properties.outputs.foundryLocalAuthDisabled.value
```

Stop if `foundryLocalAuthDisabled` is `false`: a Foundry account the gateway calls still accepts keys, so callers could skip the gateway with a key.

### Phase 3: seed the bring-your-own secret

Only when `byoBackendSecretName` and `byoBackendUrl` are set. Set `secretSeederPrincipalId` for the deployment that grants the seeder Key Vault Secrets Officer, write the secret from inside the network, then delete that role assignment (`az role assignment delete`) and clear the parameter. Redeploying with the parameter empty does not remove the grant, because deployments are incremental. Read the value from a protected prompt or a file you delete afterwards, never from a command-line argument that ends up in shell history.

### Phase 5: gateway app

```bash
# Inject the reviewed policy (compact, without "_" keys) and the image digest
# into a copy of the parameters file, so the JSON string is passed verbatim.
jq --arg policy "$(jq -c 'with_entries(select(.key | startswith("_") | not))' <reviewed-policy>.json)" \
   --arg img '<registry>.azurecr.io/model-gateway@sha256:<digest>' '
  .parameters.gatewayPolicyJson.value = $policy
  | .parameters.gatewayImage.value = $img
  | .parameters.deployGatewayApp.value = true' \
  .local/gateway.parameters.json > .local/gateway.app.parameters.json

az deployment group create -g <resource-group> -n gateway-app -f main.bicep \
  -p @.local/gateway.app.parameters.json
```

The gateway refuses to start if the policy is empty, still contains a `<placeholder>`, names an allowed model without a route, names a route whose URL setting is unset, or if the timeouts are in the wrong order (keepalive not below the ingress idle timeout, or first-byte deadline times attempts not below it in hold-headers mode). A revision that will not start is the intended result of a bad policy.

## Networking notes

- **Internal environment (default).** `ingressExternal = false` creates an internal environment joined to `infrastructureSubnetId`. The deployment fails if the subnet is missing, rather than silently creating a public environment.
- **App ingress on an internal environment.** The app's ingress is set to accept traffic from outside the *environment*, which on an internal environment means from your virtual network only. Create a private DNS zone for the environment's default domain (`environmentDefaultDomain` output) with a wildcard record pointing to `environmentStaticIp`, linked to the virtual networks your hosted agents use. Without it, agents cannot resolve the gateway host name.
- **Hosted agents must reach the gateway.** Confirm the hosted agent runtime's outbound path can reach your virtual network before you choose an internal gateway. Observed in preview; verify against current docs.
- **Idle timeouts.** Every layer between agent and gateway (ingress, firewalls, proxies) has its own idle timeout, and some have hard limits that you cannot raise. Set `ingressIdleTimeoutSeconds` to the shortest of them and `keepaliveSeconds` below it. Verify current Container Apps ingress limits against current docs; this asset states no figures.
- **Key Vault references over a private endpoint.** The Container Apps environment must resolve and reach the vault privately for the Key Vault reference to work. Verify current support for Key Vault references from internal environments against current docs.
- **Bring-your-own egress.** A bring-your-own provider is a public endpoint. Allow its host name explicitly at your egress firewall; do not open general internet egress for the gateway subnet.

## Revisions, blue-green and rollback

The app runs in multiple-revision mode. Four parameters control traffic:

| Parameter | First app deployment | Later deployments |
|---|---|---|
| `stableRevisionName` | Empty: all traffic goes to the latest revision | Name of the revision that currently serves traffic. Gets the `stable` label |
| `revisionSuffix` | Optional | Required: the new revision is named `<namePrefix>-gw--<revisionSuffix>` |
| `candidateLabel` | Not used | Label for the new revision, giving it its own test address |
| `candidateWeight` | Not used | `0` to create the candidate with no traffic; raise it to shift traffic |

To roll back, deploy again with `candidateWeight = 0`, or shift label weights back with `az containerapp ingress traffic set`. After moving all traffic, keep the previous revision active until streams already in progress have finished, then deactivate it; deactivating immediately cuts streams mid-answer. The label address form (`<app>---<label>.<environmentDefaultDomain>`) is observed in Container Apps; verify against current docs.

Changing a Key Vault secret does not create a new revision, and running replicas keep the old value until they restart. Changing `gatewayPolicyJson` or any other template setting creates a new revision. Verify current behaviour against current Container Apps documentation.

## Teardown order

Reverse of deployment, and stop traffic before removing anything it depends on. Details in [Teardown order](../docs/deployment-and-operations.md#teardown-order).

1. Move agents off the gateway (restore their direct model role first if they go back to calling Foundry directly).
2. Remove `Gateway.Invoke` assignments and the callers' policy entries.
3. Shift traffic away, drain in-flight streams, deactivate revisions.
4. Delete the container app, then the Container Apps environment.
5. Remove the gateway identity's role assignments on the Foundry account(s), Key Vault and registry. Role assignments outlive a deleted principal as orphans.
6. Delete Key Vault (purge protection keeps it soft-deleted for the retention period) and revoke the bring-your-own key at the provider.
7. Delete the managed identity.
8. Delete Application Insights and Log Analytics only after your retention obligations are met.
9. Delete the app registration last.

## Parameters

Required parameters with no default must be stated per environment. Timeouts, replica bounds, concurrency, CPU, memory and retention deliberately have no defaults: choose them from your own measurements.

| Parameter | Required | Description |
|---|---|---|
| `location` | No | Region; defaults to the resource group's |
| `namePrefix` | Yes | Prefix for every resource name |
| `tags` | No | Tags for every resource |
| `foundryAccountName` | Yes | Existing Foundry account (keys disabled) |
| `foundryResourceGroupName` | No | Its resource group; defaults to the deployment's |
| `foundrySecondaryAccountName` | No | Optional failover account in another region |
| `foundrySecondaryResourceGroupName` | No | Its resource group; defaults to the deployment's |
| `containerRegistryName` | Yes | Existing private registry |
| `containerRegistryResourceGroupName` | No | Its resource group; defaults to the deployment's |
| `ingressExternal` | No | `false` (default) for an internal environment |
| `infrastructureSubnetId` | When internal | Subnet delegated to `Microsoft.App/environments` |
| `ipSecurityRestrictions` | No | Optional ingress IP rules |
| `keyVaultPublicNetworkAccess` | No | `Disabled` (default) or `Enabled` |
| `privateEndpointSubnetId` | No | Subnet for the Key Vault private endpoint |
| `keyVaultPrivateDnsZoneId` | No | Existing `privatelink.vaultcore.azure.net` zone |
| `deployGatewayApp` | No | `false` for phase 2, `true` for phase 5 |
| `gatewayImage` | Phase 5 | Image by digest |
| `gatewayPolicyJson` | Phase 5 | Policy document as a compact JSON string |
| `gatewayTenantId` | Yes | The one tenant the gateway accepts tokens from |
| `gatewayAudiences` | Yes | App ID URI and client ID of the gateway app registration |
| `gatewayRequiredRole` | No | App role callers must carry; default `Gateway.Invoke` |
| `keepaliveSeconds` | Yes | SSE keepalive interval; below `ingressIdleTimeoutSeconds` |
| `firstByteTimeoutSeconds` | Yes | Per-attempt deadline for the backend's first event |
| `maxAttempts` | Yes | Backend attempts per request, all before the first byte |
| `ingressIdleTimeoutSeconds` | Yes | Shortest idle timeout of any layer on the path |
| `earlyCommit` | No | `false` (hold headers until first byte) or `true` (headers first, then keepalives) |
| `containerCpu`, `containerMemory` | Yes | Container size from your sizing tests |
| `minReplicas`, `maxReplicas` | Yes | Replica range. Keep them equal until budgets and idempotency use a shared store |
| `concurrentRequests` | Yes | Concurrent requests per replica that triggers scale-out |
| `terminationGracePeriodSeconds` | Yes | Time for in-flight streams when a replica stops |
| `revisionSuffix`, `stableRevisionName`, `candidateLabel`, `candidateWeight` | No | Blue-green controls; see above |
| `byoBackendSecretName`, `byoBackendUrl` | No | Both set to enable a bring-your-own route |
| `secretSeederPrincipalId`, `secretSeederPrincipalType` | No | Just-in-time Key Vault Secrets Officer for seeding |
| `logRetentionDays` | Yes | Log Analytics retention |
| `modelInferenceRoleDefinitionId` | Yes | Looked up in your tenant; see above |
| `keyVaultSecretsUserRoleDefinitionId`, `keyVaultSecretsOfficerRoleDefinitionId`, `acrPullRoleDefinitionId` | No | Well-known built-in role IDs; defaults provided |

## Outputs

| Output | Use |
|---|---|
| `gatewayFqdn` | Gateway host name; agents use `https://<gatewayFqdn>/v1`. Empty when the app is not deployed |
| `latestRevisionName` | Revision created by this deployment. Empty when the app is not deployed |
| `gatewayIdentityPrincipalId`, `gatewayIdentityClientId` | The gateway identity, for audits and for any additional role grants |
| `keyVaultName`, `keyVaultUri` | Where to seed the bring-your-own key |
| `environmentId`, `environmentDefaultDomain`, `environmentStaticIp` | For the private DNS wildcard record of an internal environment |
| `foundryLocalAuthDisabled` | `true` only if every Foundry account the gateway calls has key authentication disabled |

## Settings the gateway receives

The template maps parameters to environment variables on the container. The names are documented in [`src/README.md`](../src/README.md). Two are secret references and never appear as values: `BYO_BACKEND_API_KEY` (a Key Vault reference read with the gateway identity) and `APPLICATIONINSIGHTS_CONNECTION_STRING`. The Foundry base URL is derived from the account's custom subdomain in the OpenAI v1 form (`https://<subdomain>.openai.azure.com/openai/v1`); verify the current endpoint form against current docs.

## Verify against current docs

- Foundry hosted agent networking and the model gateway connection: observed in preview; verify against current docs.
- The OpenAI v1 base URL form for Foundry accounts.
- Key Vault references from internal Container Apps environments over a private endpoint.
- Diagnostic settings `categoryGroup` support for Container Apps environments and Key Vault.
- Container Apps ingress idle timeout limits, revision label addresses, and whether secret changes need a restart.
