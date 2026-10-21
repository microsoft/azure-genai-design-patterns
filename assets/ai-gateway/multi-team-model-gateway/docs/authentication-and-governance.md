# Authentication and governance

This document describes who can call what in the multi-team model gateway, how each team's gateway key is issued, read, rotated and revoked, how to tell the different 401 and 403 responses apart, and how to prove in your own tenant that one team cannot use another team's budget or key.

The short version: a team member signs in to **their own Foundry project** with Microsoft Entra ID, reads **their own team's** gateway key from a project connection, and calls API Management with it. The key decides the team budget. API Management removes the key and calls the model with **its own managed identity**. Nobody holds a model key, because key authentication is disabled on the model account.

## 1. Identity inventory

| # | Identity | Type | Authenticates to | With | Granted by |
|---|---|---|---|---|---|
| 1 | Team member or team app | Entra user, group member, or managed identity | Foundry project data plane | Entra access token (`DefaultAzureCredential`) | Foundry User on the team's project ([team.bicep](../infra/modules/team.bicep)) |
| 2 | Team gateway key | APIM subscription key (`sub-team-<name>`) | APIM gateway | `api-key` header | Stored in the team's project connection by the deployment |
| 3 | Gateway identity | APIM system-assigned managed identity | Foundry account (model endpoint) | Entra access token for `https://cognitiveservices.azure.com` | Model inference role on the account ([role-assignment.bicep](../infra/modules/role-assignment.bicep)) |
| 4 | Deployment identity | Workload identity federated with your CI | Azure Resource Manager | OIDC token exchanged for an Entra token | Contributor plus a role that can assign roles, on the resource group |
| 5 | Platform operator | Entra user | Resource Manager, APIM, projects | Entra access token | Resource group roles, plus Foundry User on each project for testing |

What is deliberately **absent**:

- There is no model key. `disableLocalAuth` is set on the account, so key-based calls to the model are refused even if a key were found.
- There is no gateway key in any file, pipeline variable or app setting. Deployment outputs contain names and endpoints only.
- Team identities have no role on the account itself, only on their project.

## 2. Who can do what

| Action | Member of alpha | Member of beta | Gateway identity | Platform operator |
|---|---|---|---|---|
| Read `ai-gateway-team-alpha` with credentials | Yes | No (403) | No | Yes, if also Foundry User on team-alpha |
| Read `ai-gateway-team-beta` with credentials | No (403) | Yes | No | Yes, if also Foundry User on team-beta |
| List connections in team-alpha (no keys) | Yes | No | No | Yes |
| Call the gateway on the alpha budget | Yes, with the alpha key | Only with a leaked alpha key | Not applicable | Yes, as a member of alpha |
| Call the model deployment directly | No | No | Yes | Only if you grant it; do not |
| Change a team budget | No | No | No | Yes, by redeploying with a new `tokensPerMinute` |
| Regenerate a team key | No | No | No | Yes |

"Only with a leaked alpha key" is the residual risk of a shared secret. A leaked key spends the alpha budget and shows up as alpha usage; it cannot reach any other team's budget or the model directly. The response is to rotate the key (section 3).

## 3. Key lifecycle

### Issue

The deployment creates subscription `sub-team-<name>` on product `team-<name>`, reads its primary key with `listSecrets()` inside the Bicep, and writes it into connection `ai-gateway-team-<name>` in project `team-<name>` with `isSharedToAll: false`. The key never appears in a deployment output or log.

### Read

The team client calls `AIProjectClient(endpoint=<team project endpoint>, credential=DefaultAzureCredential())`, then `connections.get("ai-gateway-team-<name>", include_credentials=True)`. It keeps the key in memory for the lifetime of the client and does not write it anywhere. See [team_client.py](../src/team_client.py).

### Rotate

1. Regenerate the subscription's primary key in APIM. The old key stops working at once.
2. Re-run the deployment. It reads the new key and updates the connection.
3. Running clients get 401 on their next call, re-read the connection once, and continue with the new key.

For no interruption, use the two-key routine: regenerate the secondary key, point the connection at it, wait for clients to pick it up, then regenerate the primary. That needs a small change to the Bicep, which reads `primaryKey`. Commands are in [infra/README.md](../infra/README.md).

### Revoke

- **One person leaves the team.** Remove them from the team's Entra group. They can no longer read the connection. If they may have copied the key, rotate it.
- **A team is retired.** Remove the team from the `teams` parameter and delete its subscription, product, project and connection. Deployments in incremental mode do not delete resources; remove them explicitly.
- **Emergency stop for one team.** Suspend the team's subscription in APIM. Calls get 401 until you reactivate it. Other teams are unaffected.

## 4. Error classification

Different layers return 401 and 403 for different reasons. Classify before you change anything.

| Where | Status | Body or signal | Meaning | Fix |
|---|---|---|---|---|
| Foundry project, reading the connection | 401 | Authentication error from `azure-identity` | No usable Entra credential | `az login`, or check the app's managed identity |
| Foundry project, reading the connection | 403 | Authorization failed | Caller has no Foundry User role on this project | Add the caller to the team's group; do not grant cross-team access |
| Foundry project, reading the connection | 404 | Not found | Wrong connection name or wrong project endpoint | Use `teams[].connectionName` and `teams[].projectEndpoint` from the deployment outputs |
| Gateway | 401 | `{"statusCode": 401, ...}` | Missing, wrong, regenerated or suspended key | Re-read the connection; if it persists, check the subscription state |
| Gateway | 403 | `{"error": {"code": "team_product_required"}}` | Key is not a team product key (all-access or API-scoped) | Use the team's connection; disable the all-access subscription |
| Gateway | 403 | `{"statusCode": 403, ...}` | Period token quota used, if you enabled one | Wait for the next period or raise the quota |
| Gateway | 404 | `{"error": {"code": "deployment_not_allowed"}}` | Deployment not on the allow-list | Use an allowed deployment or update `allowedModelDeployments` |
| Model, through the gateway | 401 or 403 | `{"error": {...}}` from the model | Gateway identity lacks its role, or the role assignment has not propagated | Check the role assignment on the account |
| Model, called directly with a key | 401 | `AuthenticationTypeDisabled` | Key auth is off, as intended | Call through the gateway |

[run_team.py](../src/run_team.py) maps these to exit codes: 3 for no access to the project, 4 for the team's budget, 1 for other gateway or connection problems.

## 5. Two-principal isolation protocol

Run this in each environment before onboarding production teams. You need one test principal in the alpha group only, one in the beta group only, and an operator in both. Use a small alpha budget in the test environment.

1. **Own key works.** As the alpha-only principal, run `python run_team.py --team alpha`. Expect exit 0 and the `x-tokens-consumed` and `x-remaining-tokens` headers.
2. **Other team's key is unreadable.** As the alpha-only principal, run `python run_team.py --team beta`. Expect exit 3, access denied, from the beta project.
3. **Listing does not leak keys.** As the alpha-only principal, run `python run_team.py --team alpha --list-connections`. Expect connection names and targets, and no keys.
4. **No bypass with other keys.** As the operator, call the API with the built-in all-access key (if it is still enabled in test). Expect 403 `team_product_required`.
5. **No direct model access.** As the alpha-only principal, call the model endpoint directly with Entra ID. Expect 401 or 403: the principal has no role on the account.
6. **Budget isolation.** As the operator, run `python tpm_burst_test.py --burst-team alpha --control-team beta`. Expect exit 0: team alpha receives 429 with `retry-after` from the gateway, and team beta's call still succeeds.
7. **Header spoofing has no effect.** Repeat a call with the alpha key and `x-team: beta`. Expect the usage to appear under `team-alpha` in the token metric.
8. **Rotation.** Regenerate the beta key, redeploy, and confirm that a running beta client recovers after one 401.

Record the results with the date and the environment. The offline [simulator](../samples/README.md) checks the same rules on every pipeline run.

## 6. Governance

| Activity | Owner | How | Evidence |
|---|---|---|---|
| Onboard a team | Platform team | Add an entry to `teams`, with an Entra group and a budget; redeploy | Deployment record, new rows in the `teams` output |
| Change a budget | Platform team, approved by the capacity owner | Change `tokensPerMinute`; redeploy | Pull request and deployment record |
| Add a deployment to the gateway | Platform team, with responsible AI review | Add to `allowedModelDeployments`; redeploy | Pull request |
| Grant a person access | Team owner | Add to the team's Entra group | Group audit log |
| Rotate keys | Platform team | Section 3 | APIM activity log |
| Review usage | Finance and team owners | Chargeback query on the `Team` dimension | Monthly report |
| Review access | Security | Group membership and role assignments per project | Access review record |

Suggested guardrails, enforced outside this asset:

- Audit or deny Foundry accounts with local auth enabled.
- Disable the built-in all-access subscription in every APIM instance.
- Require diagnostics on APIM, with no body logging.
- Restrict who can create role assignments on team projects.

## 7. Entra-only alternative

The subscription key is a shared secret. An Entra-only design removes it: each team's app gets an identity with an app role on a gateway app registration, the gateway validates the token (`validate-jwt` or `validate-azure-ad-token`), and the token limit is keyed on a claim the caller cannot choose, such as `appid` or `azp`. Foundry connections would then use an Entra auth type instead of `ApiKey`.

**Verify in your tenant** which auth types your Foundry release supports for `ApiManagement` connections, and plan for one app registration or app role per team. The [MCP OAuth Front Door](../../mcp-oauth-front-door/README.md) and [Backend Identity Propagation](../../backend-identity-propagation/README.md) assets cover Entra validation at a gateway and passing user identity to backends.

## 8. Verify in your tenant

- That the Foundry User role lets a project member read connection credentials with `include_credentials=True`. It did in the proof of concept behind this design.
- The role definition ID for the gateway identity's model inference role. Look it up with `az role definition list --name "Cognitive Services User" --query "[0].name" -o tsv`, or use Cognitive Services OpenAI User for a narrower grant.
- That connection names are unique across the account, as described in [architecture.md](architecture.md).
- How long role assignments take to propagate before the first call succeeds.

## Related

- [Architecture](architecture.md)
- [Evaluation and troubleshooting](evaluation-and-troubleshooting.md)
- [ADR 0002: Project connection as gateway key distribution](decisions/0002-project-connection-as-gateway-key-distribution.md)
- [Infrastructure guide](../infra/README.md)
- [Client source](../src/README.md)
