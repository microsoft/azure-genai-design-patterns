# Deployment and Operations

A step-by-step guide for deploying a hosted agent onto an existing [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md). Every value is a placeholder. Every API call is described by intent because the hosted agent SDK and REST shapes are in preview; take the exact operation names, parameters and API versions from the current Microsoft Learn documentation, for example [Set up CI/CD for a hosted agent](https://learn.microsoft.com/azure/foundry/agents/quickstarts/set-up-cicd-hosted-agent).

## Placeholders

| Placeholder | Meaning |
|---|---|
| `<subscription-id>` | Target subscription. Pin it explicitly in every script |
| `<resource-group>` | Workload resource group from the landing zone |
| `<foundry-account>` | Foundry account name |
| `<project>` | Foundry project name |
| `<registry>` | Private container registry name, for example `contosoagentsdevacr` |
| `<cosmos-account>` | Cosmos DB account name |
| `<agent-name>` | Hosted agent name, for example `contoso-research-agent` |
| `<model-deployment>` | Model deployment name used by the agent |
| `<preview-api-version>` | Current hosted agent API version from the docs |
| `<runtime-principal-id>` | Object id of the agent's instance identity (or customer-managed identity) |
| `<pipeline-principal-id>` | Object id of the pipeline identity |

Project endpoint shape: `https://<foundry-account>.services.ai.azure.com/api/projects/<project>`.

## Prerequisites

- The landing zone is deployed: private Foundry account and project, private registry, private Cosmos DB, Application Insights, private DNS zones, operator VNet and an in-network runner. See the landing zone [deployment and CI/CD guide](../../../landing-zones-and-infra/secured-foundry-landing-zone/docs/deployment-and-cicd.md).
- The hosted agent feature is available in your region and project. Verify on Microsoft Learn.
- A pipeline identity exists with workload identity federation to your CI environment.
- A model deployment exists for `<model-deployment>`. Creating an agent version does not validate it.

## Step 1: Add the runtime layer infrastructure

Deploy the additions described in [`infra/README.md`](../infra/README.md): Cosmos DB database and containers, AcrPush for the pipeline, AcrPull for the image pull identity, the pipeline's project role, and telemetry wiring. The illustrative template is [`infra/main.bicep`](../infra/main.bicep).

Verify:

- The pipeline identity can push to `<registry>` from the runner.
- Public network access remains disabled on every resource.
- Cosmos DB local authentication is disabled.

## Step 2: Build the image on the in-network runner

1. Check out the reviewed commit.
2. Build the image from an approved base, following the pattern in [`src/Dockerfile`](../src/Dockerfile).
3. Generate a software bill of materials.
4. Scan for vulnerabilities and secrets. Stop on unaccepted findings.
5. Optionally sign the digest.

Do not use registry-side build tasks against a private registry; they could not reach it in testing **(observed in preview; verify against current docs)**.

## Step 3: Push by digest

1. Authenticate to `<registry>` with the pipeline identity.
2. Push the image. Record the digest, for example `<registry>.azurecr.io/<agent-name>@sha256:<digest>`.
3. Never deploy by tag.

## Step 4: Ensure runtime roles

1. Resolve the runtime principal. If you use the platform's instance identity, read it from the version inspection after Step 5; if it is stable across versions, this step is a verification only.
2. The role-assignment identity grants, at the narrowest scope:
   - Cognitive Services OpenAI User on the Foundry account.
   - Cosmos DB Built-in Data Contributor through Cosmos DB role assignments.
   - Monitoring Metrics Publisher on Application Insights if ingestion uses Entra authentication.
   - The gateway app role, if the agent calls tools through the MCP gateway.
3. Verify the names against current documentation. See [authentication-and-governance.md](authentication-and-governance.md#role-assignments).

## Step 5: Create the agent version

From the runner, using the project endpoint:

1. Record the currently active version as the rollback target.
2. Create a new version with:
   - the image reference by digest;
   - the protocol (Responses or Invocations);
   - environment variables with an `AGENT_` prefix, for example `AGENT_MODEL_DEPLOYMENT=<model-deployment>`, `AGENT_COSMOS_ENDPOINT=https://<cosmos-account>.documents.azure.com:443/`, `AGENT_LOG_LEVEL=INFO`;
   - resource settings within documented limits.
3. A definition takes either an image configuration or a code configuration, not both.
4. Inspect the new version and record the instance principal id in the release record.
5. If the principal is new, run Step 4 now, before the smoke turn.

## Step 6: Smoke turn from inside the network

1. Create a session pinned to the new version with a smoke session id, for example `smoke-<build-id>`.
2. Poll its status until ACTIVE or FAILED, with a bounded budget.
3. Send a minimal turn with bounded retry, because ACTIVE can precede readiness.
4. Send the dependency smoke turn and check markers and the reported version.
5. Replay the same idempotency key and check that only one side effect occurred.
6. End the smoke session.
7. Query Application Insights for spans with the smoke correlation id.

Gate details are in [evaluation-and-troubleshooting.md](evaluation-and-troubleshooting.md#release-gates).

## Step 7: Evaluate and promote

1. Run the evaluation gate (see the [Agent Production Readiness Guide](../../../agentops-and-readiness/agent-production-readiness/README.md)).
2. In production, require an approval from someone other than the author.
3. Switch the active version to the new version. New sessions are pinned to it.
4. Drain sessions on the previous version per the business drain policy.

## Step 8: Promote to the next environment

1. Import the same digest into the next environment's registry. Do not rebuild.
2. Repeat Steps 4 to 7 in that environment with its own runner, configuration and approvals.

## Step 9: Operate

| Cadence | Operation | Reference |
|---|---|---|
| Continuous | Alerts on 401 and 403 rates, session failures, session count growth, missing telemetry | [Architecture, observability](architecture.md#11-observability) |
| Scheduled | Orphan sweeper (dry run, then apply) and keep-warm detection | [Operational runbook](evaluation-and-troubleshooting.md#clean-up-orphans) |
| Scheduled | Negative tests | [Negative tests](evaluation-and-troubleshooting.md#negative-tests) |
| Scheduled | Base image refresh and rebuild | [Rotate](evaluation-and-troubleshooting.md#rotate) |
| On incident | Roll back to the recorded version and drain | [Roll back](evaluation-and-troubleshooting.md#roll-back) |
| On retirement | Retire versions that are neither active nor a rollback target; remove per-version role assignments | [Governance](authentication-and-governance.md#governance) |

## Practical notes

- Pin the subscription at the top of every script: the runner may be shared.
- When the runner is a Container Apps job, pass the complete environment on every execution; values from an earlier execution were observed to persist.
- Silence verbose SDK HTTP logging in scripts so errors stand out.
- Keep the release record (commit, digest, version, principal, approver, rollback target) as the single source of truth for what is running.
- Exercise rollback in a non-production environment on a schedule.
