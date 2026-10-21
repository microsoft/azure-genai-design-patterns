# Samples: Hosted Agent Operations Simulator

`hosted_agent_ops_sim.py` is a single-file, standard-library-only simulator of the
operational behaviour described in [architecture.md](../docs/architecture.md). It models
one fictional Contoso hosted agent: the release pipeline that builds and ships it, the
Foundry Agent Service runtime that runs its sessions, the container that answers turns,
the identities and role assignments around it, and the network placements that decide
which calls are allowed at all.

Use it to read the logic, to argue about edge cases with your platform and security
teams, and as an executable specification for the tests you write against your own
deployment. Each scenario is a short function that asserts one behaviour from the docs
or an ADR.

> **Test only.** Every time value is a logical clock tick. Ticks only order events: they
> are not seconds and say nothing about real cold start, latency, capacity or cost. There
> is no network call anywhere in the file. Status codes, header names, session state
> names and role names model behaviour observed in preview. Verify them against
> Microsoft Learn and the SDK version you pin before you rely on them.

## Prerequisites

- Python 3.10 or later. No packages to install.

## Run it

From the repository root:

```powershell
python assets/agent-runtime/production-hosted-agents-on-foundry/samples/hosted_agent_ops_sim.py --self-test
python assets/agent-runtime/production-hosted-agents-on-foundry/samples/hosted_agent_ops_sim.py --demo
```

- `--self-test` runs every scenario. It prints `PASS <scenario>` or
  `FAIL <scenario>: <reason>` for each one, then `N/24 scenarios passed`. It exits 0
  when all pass and 1 otherwise.
- `--demo` (also the default with no argument) prints a readable trace:
  1. a release from a public runner that fails, then the same release from a runner
     inside the VNet that is promoted
  2. a pinned session that is ACTIVE before it is ready
  3. a lost response retried with the same idempotency key, with one side effect
  4. idle expiry and recovery onto the pinned version, then an explicit end
  5. a release with a missing setting that the smoke test rolls back
  6. an orphan sweep and a keep-warm finding
  7. the turn spans the container exported

The demo never prints a token, a prompt or an idempotency key.

## The fictional world

All names are placeholders. Principal names stand in for managed identity object IDs.

| Principal | Constant | Role assignments in the default world |
| --- | --- | --- |
| `contoso-release-pipeline` | `PIPELINE` | AcrPush on the registry; Foundry User on the project |
| `contoso-research-agent-runtime` | `RUNTIME` | Cognitive Services OpenAI User on the Foundry account; Cosmos DB Built-in Data Contributor on the Cosmos DB account |
| `contoso-project-identity` | `PROJECT_IDENTITY` | AcrPull on the registry (pulls the image when a session starts) |
| `contoso-research-gateway` | `CALLER` | Foundry User on the project (the client that invokes the agent) |
| `contoso-agent-operator` | `OPERATOR` | Foundry User on the project (sweeps, drains, diagnostics) |
| `contoso-developer` | `DEVELOPER` | Contributor on the project only: control plane, no data-plane actions |

Role names are public built-in names at the time of writing; see
[ADR 0002](../docs/decisions/0002-agent-identity-gets-least-privilege-data-plane-roles.md)
and verify current names on Microsoft Learn.

Network placements (`Placement`):

| Placement | In the VNet | Can reach | Stands in for |
| --- | --- | --- | --- |
| `INSIDE` (`ci-runner-in-vnet`) | Yes | Entra, Foundry, registry | Self-hosted runner or job in the runner subnet |
| `INTERNET` (`public-hosted-runner`) | No | Entra, Foundry, registry | A public hosted runner or a laptop on the internet |
| `SANDBOX` (`agent-sandbox`) | Yes | Entra, models, Cosmos DB, tool gateway, monitoring | The hosted agent container's egress |
| `PLATFORM_PULL` (`foundry-platform-pull`) | Yes | Registry | The platform pulling the image for a session |

Token audiences: Foundry (`https://ai.azure.com/.default`), models
(`https://cognitiveservices.azure.com/.default`), Cosmos DB (account placeholder),
monitoring, and the tool gateway (`api://<gateway-app-id>/.default`). Verify the Foundry
audience for the API version you call.

Session states: `CREATED`, `ACTIVE`, `IDLE`, `EXPIRED`, `CLOSED`, plus `FAILED` when the
container cannot start. `CREATED`, `ACTIVE` and `IDLE` are live.

## Scenario catalog

| # | Scenario | What it proves | Where it is documented |
| --- | --- | --- | --- |
| 1 | `outside-vnet-403` | A release or session call from outside the VNet gets 403 `public_access_disabled`; the same call from inside works. The client does not retry it. | [architecture](../docs/architecture.md) section 8.1, [ADR 0001](../docs/decisions/0001-deploy-and-invoke-from-inside-the-vnet.md) |
| 2 | `missing-role-403-clear-message` | Contributor grants no data-plane actions, and the 403 says which role is missing. A runtime identity without its model role fails once with `dependency_authorization_failed`, not after retries. | [architecture](../docs/architecture.md) section 7.1, [ADR 0002](../docs/decisions/0002-agent-identity-gets-least-privilege-data-plane-roles.md) |
| 3 | `token-cache-per-audience` | One cached token per audience; a token for one audience is rejected by another; tokens never print. Inside the container, later turns hit the cache. | [architecture](../docs/architecture.md) section 7.3, [ADR 0004](../docs/decisions/0004-cache-tokens-per-audience.md) |
| 4 | `token-refresh-skew` | With refresh skew the provider renews early; without it, an in-flight call fails with `token_expired`. | [ADR 0004](../docs/decisions/0004-cache-tokens-per-audience.md) |
| 5 | `session-state-machine` | CREATED to ACTIVE to IDLE to ACTIVE to CLOSED; create is idempotent for a live ID; ending twice is harmless; a closed ID cannot be reused. | [architecture](../docs/architecture.md) section 6.2 |
| 6 | `heartbeat-extends-idle` | Heartbeats keep an in-use session alive; a quiet session expires with `idle_timeout`; the timeout applies again when heartbeats stop. | [architecture](../docs/architecture.md) section 6.5, [ADR 0003](../docs/decisions/0003-explicit-session-lifecycle-no-keep-warm.md) |
| 7 | `idle-expiry` | IDLE then EXPIRED; the container stops and flushes buffered spans; the next invoke gets 404 that the client treats as "recover", not "retry". | [architecture](../docs/architecture.md) section 6.6 |
| 8 | `invoke-retry-idempotent` | A lost response is retried with the same idempotency key and replayed, so the tool side effect happens once. The key also works in request metadata. | [architecture](../docs/architecture.md) section 6.7 |
| 9 | `active-not-ready-424-bounded-retry` | ACTIVE is not ready: the first turn gets 424 while the agent server starts. A cheap readiness probe retries within a budget. | [architecture](../docs/architecture.md) section 6.3 |
| 10 | `retry-classification` | The full table of retry, recover and fail decisions; capped exponential backoff; the attempt budget stops a retry storm. | [architecture](../docs/architecture.md) section 6.7, [troubleshooting](../docs/evaluation-and-troubleshooting.md) |
| 11 | `wrong-audience-401` | A caller token for the wrong audience gets 401 naming the expected audience; a missing token gets 401. | [architecture](../docs/architecture.md) section 15 |
| 12 | `ingress-drops-unprefixed-headers` | Only allow-listed headers reach the container: `Authorization` and arbitrary headers are dropped; correlation, idempotency and trace context survive; the caller ID is the calling principal. | [architecture](../docs/architecture.md) section 14.3 |
| 13 | `unpinned-retry-leaks-sessions` | Anti-pattern: blind retries without a session ID start a new session per attempt and never get an answer. The sweeper cleans up; a pinned client starts exactly one session. | [architecture](../docs/architecture.md) section 16 |
| 14 | `expired-session-rehydrates-same-key` | After expiry the client opens a new session on the same version, rehydrates state from the store, and replays a repeated key without a second side effect. | [architecture](../docs/architecture.md) sections 6.6 and 14.6 |
| 15 | `new-version-does-not-evict` | Promoting a new version leaves live sessions on the old one; a drain ends them and the conversation moves to the active version. | [architecture](../docs/architecture.md) section 6.8, [runbook](../docs/evaluation-and-troubleshooting.md) |
| 16 | `image-pull-failure-session-failed` | Without AcrPull, or with the image purged by retention, new sessions go to FAILED with a message naming the cause. Running sessions keep working. | [architecture](../docs/architecture.md) section 15, [infra](../infra/README.md) |
| 17 | `pipeline-promote` | Stage order build, scan, sign, push, verify roles, create version, smoke, promote. Versions reference a digest, never a mutable tag. An optional evaluation gate runs once. | [architecture](../docs/architecture.md) section 9.1, [deployment guide](../docs/deployment-and-operations.md) |
| 18 | `pipeline-rollback` | A missing setting or a failed evaluation gate keeps the previous version active. Rolling back a promoted version drains its sessions. Rolling back the first release fails loudly. | [architecture](../docs/architecture.md) section 9.4, [runbook](../docs/evaluation-and-troubleshooting.md) |
| 19 | `pipeline-fail-fast-gates` | A secret finding, an unapproved base image, a missing AcrPush role or a missing runtime role stops the release before anything goes live. | [authentication and governance](../docs/authentication-and-governance.md) |
| 20 | `spans-carry-correlation-and-version` | Each turn span carries the caller's correlation ID, trace ID, agent version and a hashed conversation key, and contains no prompt text or keys. | [architecture](../docs/architecture.md) sections 11 and 14.5 |
| 21 | `telemetry-flush-and-blocked-egress` | Spans are batched and flushed on shutdown. With monitoring egress blocked they are dropped with a warning. A blocked tool call fails once, has no side effect, shows in telemetry, and the smoke test catches it. | [architecture](../docs/architecture.md) sections 8.2 and 11.2 |
| 22 | `orphan-sweep` | The sweeper is a dry run by default, honours a keep list, ends only stale sessions, and itself needs the network and the data-plane role. | [runbook](../docs/evaluation-and-troubleshooting.md) |
| 23 | `keep-warm-detector` | Heartbeats without user turns hide sessions from an idle-based sweep; the detector still flags them. When the pings stop, the platform expires them. | [ADR 0003](../docs/decisions/0003-explicit-session-lifecycle-no-keep-warm.md), [architecture](../docs/architecture.md) section 12 |
| 24 | `production-adapter-gated` | The production adapter refuses to construct without an opt-in and never pretends to work. Every fake satisfies its protocol seam. | This page, "Adapter seams" |

## Adapter seams

The fakes sit behind `typing.Protocol` seams so you can see where a real adapter would
plug in.

| Protocol | Fake in the simulator | Production counterpart |
| --- | --- | --- |
| `Clock` | `LogicalClock` | Wall clock; inject it so tests stay deterministic |
| `TokenProvider` | `CachedTokenProvider` with `FakeEntraIssuer` | Managed identity credential with a per-audience cache and refresh skew |
| `NetworkContext` | `Placement` | Where the code really runs: runner subnet, agent egress, or public internet |
| `SessionStore` | `InMemoryStateStore` | Cosmos DB containers for conversation state, idempotency records and results |
| `TelemetrySink` | `InMemoryTelemetrySink` behind `BatchingExporter` | OpenTelemetry exporter to Azure Monitor or an OTLP collector inside the network |
| `AgentRuntime` | `FakeFoundryRuntime` | `FoundryHostedAgentAdapter`, a gated stub you implement against the current preview API |

`FoundryHostedAgentAdapter` raises `NotImplementedError` unless
`HOSTED_AGENT_SIM_ENABLE_LIVE=1` is set, and every method raises `NotImplementedError`
even then. It is a marker for where real data-plane calls belong, not a client. Build a
real one from the Foundry Agent Service documentation and the SDK version you pin, run it
only from inside the network, and keep it out of unit tests.

## Extend it

- **Add a scenario.** Write a `scenario_<name>()` function that builds a `World()`,
  drives it, and uses `check(...)` and `expect_error(...)`. Add it to `SCENARIOS`.
- **Inject a fault.** Append to `World().runtime.faults`: `("lose_response", 0)` drops
  the next response after the agent ran, and `("status", 503)` fails the next invoke
  before it reaches the agent.
- **Change the network.** Pass a different `Placement` to `World(sandbox=...)` to model
  an egress rule, or to `w.client(placement=...)` to model a caller elsewhere.
- **Change the grants.** Call `w.rbac.revoke(...)` or `w.rbac.grant(...)` to rehearse
  a missing or extra role assignment before you change it in Azure.
- **Port a scenario to a live test.** Keep the assertion and replace the fake with your
  adapter. Run live tests from a runner inside the VNet, against a non-production
  project.

## Limitations

- It does not model Azure Container Registry, Microsoft Entra ID, Cosmos DB, Azure
  Monitor or the Foundry Agent Service APIs. It models the decisions and contracts
  around them.
- Request and response shapes are simplified. The preview API may differ in field
  names, status codes and state names.
- Tick values are arbitrary and only order events. Do not read cold start, idle timeout
  or token lifetime values from them.
- Role checks are exact matches on (principal, role, scope). Real Azure RBAC inherits
  down the scope hierarchy, supports custom roles and conditions, and Cosmos DB uses its
  own data-plane role assignments.
- Signing, scanning and evaluation are labels, not real tools.

## Related

- [Asset README](../README.md)
- [Architecture](../docs/architecture.md)
- [Authentication and governance](../docs/authentication-and-governance.md)
- [Evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md)
- [Deployment and operations](../docs/deployment-and-operations.md)
- [Infrastructure](../infra/README.md) and [container source](../src/README.md)
- [Choosing prompt agents vs hosted agents](../../choosing-prompt-vs-hosted-agents/README.md)
- [Secured Foundry landing zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md)
- [Backend identity propagation](../../../ai-gateway/backend-identity-propagation/README.md)
