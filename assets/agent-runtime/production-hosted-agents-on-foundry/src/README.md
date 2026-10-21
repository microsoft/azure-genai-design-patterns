# Source: the hosted agent container

> **Illustrative.** [`Dockerfile`](Dockerfile) and [`app_skeleton.py`](app_skeleton.py) show the container pattern. They are not built in CI, and no test imports them. The skeleton loads without third-party packages, but its production paths need `azure-identity`, `azure-monitor-opentelemetry`, `opentelemetry-api` and the hosting adapter of your agent framework. The hosting protocol, port and paths are preview details; verify them against current Microsoft Learn documentation.

A hosted agent is a container image you build. Foundry Agent Service runs it in a per-session sandbox and invokes it through the agent APIs. This folder shows what goes **inside** that image so that it works in a private project: no secrets, one credential, cached tokens, correlated telemetry, idempotent turns and a clean shutdown. For whether you need a hosted agent at all, see [Choosing Prompt vs Hosted Agents](../../choosing-prompt-vs-hosted-agents/README.md).

## Layout

| Path in this folder | Path in the image | Purpose |
|---|---|---|
| [`Dockerfile`](Dockerfile) | n/a | Multi-stage build, approved base by digest, non-root user, read-only app directory |
| `requirements.lock` (yours) | n/a (builder stage only) | Dependency lock with hashes; installed with `--require-hashes` |
| [`app_skeleton.py`](app_skeleton.py) | `/app/app_skeleton.py` | Entrypoint: configuration, token cache, telemetry, correlation, idempotency, shutdown, health |
| Your agent package | `/app/<package>/` | Agent loop, tool clients, prompt templates under version control |
| n/a | `/home/agent/scratch` | Session-local scratch space. A cache, never the source of truth |

## Dockerfile pattern

| Rule | Why |
|---|---|
| Base image from an approved list, mirrored into the private registry, pinned by digest | The in-network build does not depend on public egress, and the base cannot change under you |
| Multi-stage build; compilers and build tools stay in the builder stage | Smaller runtime image, smaller attack surface, faster pull on a cold session |
| Install dependencies from a lock file with hashes | Reproducible and tamper-evident dependency set |
| Non-root user; application directory not writable | A compromised turn cannot rewrite the agent's own code |
| `PYTHONUNBUFFERED=1` | Log lines reach platform log streaming immediately |
| Exec-form `CMD` | The process receives `SIGTERM` directly and can flush telemetry |
| No `ENV` with secrets, keys or connection strings | Image layers and version definitions are readable by anyone who can pull or inspect them |
| Build for `linux/amd64` | Observed requirement for hosted agent sandboxes in preview; verify |
| No `HEALTHCHECK` | The platform owns liveness; readiness is proven by a real turn from the pipeline |

Build on the in-network runner and push to the private registry by digest. Registry-side build services were observed not to work against a registry with public access disabled (preview; verify). The release flow is in [deployment-and-operations.md](../docs/deployment-and-operations.md).

## Entrypoint responsibilities

The skeleton keeps each concern in one small function so that you can map it onto your framework's hosting adapter.

### 1. Configuration

- Your own variables use the `AGENT_` prefix so they never collide with platform-injected variables.
- `load_settings()` lists **every** missing required variable in one error and exits. A version that cannot start should fail the smoke test immediately, not on the first tool call.
- It refuses `AGENT_` variables whose names look like secrets (`KEY`, `SECRET`, `PASSWORD`). Secrets belong in Key Vault, read with the managed identity, or are avoided entirely by using Entra-authenticated dependencies.
- The same image runs in every environment. The pipeline sets per-environment values on the agent version: endpoints, `<model-deployment>`, the gateway URL and the log level.

| Variable | Required | Example (placeholder) |
|---|---|---|
| `AGENT_MODEL_ENDPOINT` | Yes | `https://<foundry-account>.services.ai.azure.com` |
| `AGENT_MODEL_DEPLOYMENT` | Yes | `<model-deployment>` |
| `AGENT_COSMOS_ENDPOINT` | Yes | `https://<cosmos-account>.documents.azure.com` |
| `AGENT_GATEWAY_URL` | No | `https://<gateway-host>/mcp` |
| `AGENT_VERSION_LABEL` | No | Set by the pipeline; emitted as a span attribute |
| `AGENT_LOG_LEVEL` | No | `INFO` |
| `AGENT_TOKEN_REFRESH_SKEW_SECONDS` | No | Policy value; refresh this long before expiry |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | Platform-injected | Observed in preview; verify |

### 2. One credential and a per-audience token cache

- `build_credential()` runs once per process. Creating a credential per request repeats discovery and slows every turn.
- `TokenCache` keeps one token per audience behind a lock, and refreshes when the remaining lifetime falls inside the skew window. Different audiences never share a token ([ADR 0004](../docs/decisions/0004-cache-tokens-per-audience.md)).
- Audiences used by a typical agent:

| Dependency | Scope |
|---|---|
| Model deployments | `https://cognitiveservices.azure.com/.default` |
| Foundry project APIs | `https://ai.azure.com/.default` (verify) |
| Cosmos DB | `https://<cosmos-account>.documents.azure.com/.default` |
| Azure Monitor ingestion with Entra auth | `https://monitor.azure.com/.default` |
| Your outbound gateway | `api://<gateway-app-id>/.default` |

There is no key fallback. If a token request fails, the turn fails with a clear error and the role assignment is fixed ([ADR 0002](../docs/decisions/0002-agent-identity-gets-least-privilege-data-plane-roles.md)).

### 3. Telemetry

- `setup_telemetry()` configures the Azure Monitor OpenTelemetry distribution when the platform-injected connection string is present, and falls back to no-op spans locally.
- Logs go to standard output, structured, at the configured level.
- If you point the standard OTLP exporter variable at your own collector, platform telemetry follows it too (observed in preview; verify). That collector must be private and governed.

### 4. Correlation

- `start_span()` extracts the W3C `traceparent` so the container's spans continue the caller's trace instead of starting a new root.
- `x-client-correlation-id` becomes a span attribute and a log field.
- The platform call identifier header is opaque. Forward it verbatim when calling Foundry services; never parse it.
- The `Authorization` header from the caller does not reach the container (observed in preview; verify). The caller identifier header names the calling principal, not the end user. To act on behalf of the end user, follow [Backend Identity Propagation](../../../ai-gateway/backend-identity-propagation/README.md).

### 5. Idempotent turns

- Every turn requires `x-client-idempotency-key` and `metadata.conversation_key`.
- A completed record for the same key returns the stored result with `replayed: true`. No tool runs twice.
- The record shape matches [architecture.md, section 14.6](../docs/architecture.md). `LocalIdempotencyStore` is for local runs only; production uses the Cosmos DB `idempotency` container created by [`infra/main.bicep`](../infra/main.bicep).
- When a session expires mid-conversation, the client creates a new pinned session and retries with the **same** key. The agent rehydrates from Cosmos DB and the record prevents duplicate side effects.

### 6. Graceful shutdown

- `SIGTERM` triggers a drain: stop accepting work, let the in-flight turn finish, then flush the tracer provider.
- Without the flush, the spans of the last turn before a restart or version change are lost, and those are usually the ones you need.

### 7. Health

- `handle_health()` is cheap and touches no dependency. A health check that calls the model turns a model incident into a container restart loop.
- Readiness is different: a session can report active before the container answers. The pipeline proves readiness with a real minimal turn and a bounded retry on the not-ready status ([architecture.md, section 6.3](../docs/architecture.md)).

## Run the skeleton locally

```bash
cd src
AGENT_MODEL_ENDPOINT=https://<foundry-account>.services.ai.azure.com \
AGENT_MODEL_DEPLOYMENT=<model-deployment> \
AGENT_COSMOS_ENDPOINT=https://<cosmos-account>.documents.azure.com \
python app_skeleton.py
# In a second shell:
curl -s localhost:8088/healthz
curl -s -X POST localhost:8088/responses \
  -H 'x-client-idempotency-key: turn-demo-0001' \
  -H 'x-client-correlation-id: corr-demo-0001' \
  -d '{"input":"hello","metadata":{"conversation_key":"conv-demo"}}'
```

Without `azure-identity` installed, the skeleton runs with no token cache and no-op spans. The placeholder agent turn makes no network calls. Send the same request twice to see `replayed: true`.

## What not to do

| Anti-pattern | Instead |
|---|---|
| `ENV OPENAI_API_KEY=...` or a key in the agent version definition | Managed identity and data-plane roles |
| A new credential or HTTP client per request | One of each per process |
| Writing conversation state to the sandbox disk | Cosmos DB, keyed by conversation |
| A background thread that pings the model or the platform to stay warm | Accept the cold start; heartbeat only while a user is active ([ADR 0003](../docs/decisions/0003-explicit-session-lifecycle-no-keep-warm.md)) |
| Logging prompts, completions or tool arguments by default | Log identifiers, sizes and hashes |
| `latest` or other floating base image tags | Digest-pinned approved base |

## Related

- [Architecture](../docs/architecture.md) and [Authentication and governance](../docs/authentication-and-governance.md)
- [Infrastructure for this asset](../infra/README.md)
- [Offline operations simulator](../samples/README.md)
- [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md)
