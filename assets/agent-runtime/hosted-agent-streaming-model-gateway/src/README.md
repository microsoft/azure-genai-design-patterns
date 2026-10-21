# Source: the streaming model gateway and the hosted-agent client

> **Illustrative.** [`gateway/app.py`](gateway/app.py) is a reference implementation of the contracts in [architecture.md](../docs/architecture.md), and [`agent/main.py`](agent/main.py) shows the hosted-agent side. They are not built in CI and no repository test imports them; the behaviour they implement is exercised offline by [the simulator](../samples/README.md). Review, test and harden both before production use. The gateway needs Python 3.11 or later. Foundry hosted agents, the hosting adapter used in `agent/main.py`, and Foundry model gateway connections are observed in preview; verify against current docs.

This folder holds the two pieces of code that make the pattern work. The **gateway** is a container app that accepts OpenAI-compatible streaming Chat Completions requests, checks who is calling and what they may use, and relays server-sent events from a Foundry deployment or a bring-your-own model backend without buffering them. The **agent client** is the small change inside a hosted agent container that sends every model call to the gateway with the agent's own Entra token instead of calling a model deployment directly.

## Layout

| Path | Purpose |
|---|---|
| [`gateway/app.py`](gateway/app.py) | Starlette app: token validation, policy, failover before the first byte, SSE relay with keepalive, cancellation, usage events |
| [`gateway/requirements.txt`](gateway/requirements.txt) | Direct dependencies of the gateway |
| `gateway/requirements.lock` (yours) | Hashed lock generated from `requirements.txt`; the Dockerfile and the pipeline refuse to build without it |
| [`gateway/Dockerfile`](gateway/Dockerfile) | Multi-stage build, approved base image by digest (no default), non-root user, read-only app directory |
| [`agent/main.py`](agent/main.py) | Hosted-agent entrypoint: settings that fail closed, `GatewayAuth` for token, trace context and idempotency key, model client pointed at the gateway |
| [`agent/requirements.txt`](agent/requirements.txt) | Direct dependencies of the agent example |

The gateway's policy file lives with the infrastructure: [`../infra/gateway-policy.example.json`](../infra/gateway-policy.example.json).

## The gateway

### Per-request pipeline

1. **Authenticate.** Validate the bearer token against the tenant's signing keys: signature, expiry, issuer, audience, tenant and the required app role. The caller is the token's `azp` (v2) or `appid` (v1) claim, and it must appear in the policy's `callers` map. The token is never forwarded to a backend.
2. **Parse and authorize.** The body must be a Chat Completions request with `stream: true`. The requested model must be an alias in the caller's `allowed_models`. Blocked terms in the input stop the request before any backend is called.
3. **Reserve budget.** The gateway caps `max_tokens` at the caller's `max_tokens_cap`, estimates prompt tokens, and reserves the total against the caller's budget for the current window.
4. **Check the idempotency key.** A key already in flight, or one that already produced output in this window, gets `409`. A key whose attempt failed with no output is released so the caller can retry.
5. **Connect.** Try the alias's backends in route order, up to `GATEWAY_MAX_ATTEMPTS`, until one sends its first data event within `GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS`. A backend that answers `400`, `401`, `403`, `404` or `422` stops failover, because another backend would reject the request the same way.
6. **Relay.** Forward each event as soon as it arrives, with a keepalive comment whenever the backend is quiet for `GATEWAY_KEEPALIVE_SECONDS`. Once the first data byte has gone to the client, there is no failover: a later backend failure ends the stream with an error event.
7. **Settle.** Replace the reservation with the backend's reported usage, or with the estimate if the stream ended early, and emit exactly one usage event to the `gateway.usage` logger.

### Routes

| Method and path | Purpose | Auth |
|---|---|---|
| `GET /healthz` | `200 {"status":"ok"}` once configuration has loaded; `503 {"status":"starting"}` otherwise. Touches no backend | None |
| `GET /v1/models` | The aliases the caller may use, in OpenAI list shape | Bearer token |
| `POST /v1/chat/completions` | Streaming Chat Completions only; non-streaming requests get `400 invalid_request` | Bearer token |

### Configuration

All configuration comes from environment variables. The gateway collects every problem at startup and refuses to start if there is any, including a value that still contains a `<placeholder>`. Timing values have no defaults on purpose: choose them from your own measurements of your backends and your ingress path.

| Variable | Required | Example (placeholder) | Notes |
|---|---|---|---|
| `GATEWAY_TENANT_ID` | Yes | `<tenant-id>` | Tokens from any other tenant are rejected |
| `GATEWAY_AUDIENCES` | Yes | `api://<gateway-app-id>,<gateway-app-id>` | Comma-separated; list both forms if your callers may receive either |
| `GATEWAY_REQUIRED_ROLE` | Yes | `Gateway.Invoke` | App role the caller's identity must hold |
| `GATEWAY_POLICY_JSON` | Yes | Contents of your populated policy file | Callers, budgets and routes; see below |
| `GATEWAY_KEEPALIVE_SECONDS` | Yes | `<seconds>` | Zero turns keepalive off. Must be shorter than the ingress idle timeout |
| `GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS` | Yes | `<seconds>` | Per attempt. Also bounds how long a backend may stay silent mid-stream |
| `GATEWAY_MAX_ATTEMPTS` | Yes | `<integer>` | Backends tried per request, in route order |
| `GATEWAY_INGRESS_IDLE_TIMEOUT_SECONDS` | Yes | `<seconds>` | The shortest idle timeout on any hop between caller and gateway. Used only to check the other values |
| `GATEWAY_EARLY_COMMIT` | No | `false` | See "Hold mode and early commit" |
| `AZURE_CLIENT_ID` | In Azure | `<gateway-identity-client-id>` | Selects the user-assigned managed identity. Unset only for local development |
| `BACKEND_FOUNDRY_URL`, `BACKEND_FOUNDRY_SECONDARY_URL`, `BACKEND_BYO_URL` | When a route names them | `https://<foundry-account>.openai.azure.com/openai/v1` | Names come from the policy's `base_url_env`. https only; plain http is accepted for loopback hosts only |
| `BYO_BACKEND_API_KEY` | When a route names it | Container Apps secret reference to Key Vault | Only for `openai-compatible` backends that cannot accept an Entra token |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | No | Set by the deployment | Enables the Azure Monitor exporter when the package is installed |
| `LOG_LEVEL` | No | `INFO` | |

**Hold mode and early commit.** By default the gateway holds the HTTP response until a backend sends its first data event, so if every backend fails the caller receives a real `502` with a JSON error envelope. While it holds, nothing flows to the client, so the startup check requires `GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS × GATEWAY_MAX_ATTEMPTS` to stay below the ingress idle timeout. If your backends need longer, set `GATEWAY_EARLY_COMMIT=true`: the gateway sends `200` and keepalive comments straight away, and reports an all-backends failure as an SSE error event. Early commit requires keepalive above zero. [ADR 0002](../docs/decisions/0002-retry-only-before-first-byte.md) explains the trade-off.

**Policy.** Start from [`gateway-policy.example.json`](../infra/gateway-policy.example.json). Callers are keyed by the client ID of the hosted agent's identity and carry `allowed_models`, `token_budget_per_window`, `max_tokens_cap`, `blocked_terms` and a `display_name` for logs. Routes are keyed by model alias; each backend has a `name`, a `kind` (`azure-openai` or `openai-compatible`), a `base_url_env`, an `upstream_model` and, for `openai-compatible` only, an optional `api_key_env`. Keys that start with an underscore are ignored. Startup fails if an allowed model has no route, if a named variable is unset, or if a placeholder remains. The populated policy holds identifiers, not secrets: keep it in source control and review changes like code.

### Errors

Every error uses the envelope in [architecture.md](../docs/architecture.md): `message`, `type`, `code`, `param`, `request_id` and `retryable`. Messages never echo tokens, prompts, URLs or backend response bodies.

| Status | Codes | Retryable |
|---|---|---|
| `400` | `invalid_request`, `content_filtered` | No |
| `401` | `missing_token`, `invalid_token`, `invalid_issuer`, `invalid_audience`, `invalid_tenant`, `token_expired` | No; fix the token request |
| `403` | `missing_app_role`, `caller_not_allowed`, `model_not_allowed` | No; fix the role or the policy |
| `409` | `duplicate_request` | No; the key already produced output |
| `429` | `token_budget_exceeded` | Yes, after the budget window moves on |
| `502` | `upstream_failed` | Yes, unless a backend rejected the request itself |
| `502` | `upstream_failed_mid_stream` (SSE error event) | No automatic retry; the client already holds partial output |
| `503` | `gateway_not_ready` | Yes |

### Design notes

- **Streaming headers.** Responses use `text/event-stream` with `Cache-Control: no-cache` and `X-Accel-Buffering: no`. There is no compression middleware, and upstream requests ask for `Accept-Encoding: identity`, because compression buffers the stream.
- **Identity only from the token.** uvicorn runs without `--proxy-headers`. Forwarded headers and `x-ms-client-request-id` are used for correlation only, never for policy.
- **Two identities, never mixed.** The caller's token proves who is calling. The gateway calls Foundry with its own managed identity and the `https://cognitiveservices.azure.com/.default` scope. The caller's token never leaves the gateway.
- **Trace context.** The gateway continues the caller's `traceparent` with a new span ID for each backend call, or starts a new trace if the header is missing or malformed.
- **Cancellation.** The response watches for the client disconnecting and closes the backend stream immediately, so an abandoned request stops consuming tokens.
- **Single replica as written.** The token ledger and the idempotency store are in memory. Running more than one replica needs a shared store for both; [deployment-and-operations.md](../docs/deployment-and-operations.md) covers scaling.
- **Backend bodies are not read on error.** A non-`200` backend status is recorded by status code only.

### Dockerfile and lock file

The [Dockerfile](gateway/Dockerfile) follows the same rules as the [hosted agent container pattern](../../production-hosted-agents-on-foundry/src/README.md): an approved base image mirrored into your registry and pinned by digest, passed as the `BASE_IMAGE` build argument with no default; dependencies installed from a hashed lock; non-root user; read-only application directory; exec-form `CMD`; no secrets in any layer. Generate the lock next to the Dockerfile before the first build, and regenerate it whenever `requirements.txt` changes:

```bash
cd src/gateway
python -m pip install pip-tools
pip-compile --generate-hashes requirements.txt -o requirements.lock
```

## The hosted-agent client

[`agent/main.py`](agent/main.py) changes only how the agent reaches its model. The agent loop, tools and hosting adapter are examples; keep `GatewayAuth` and the client settings and use your own framework for the rest.

| Setting | Value | Why |
|---|---|---|
| Base URL | `GATEWAY_BASE_URL` | Every model call goes to the gateway |
| Model | `GATEWAY_MODEL_ALIAS` | An alias from the gateway policy, not a deployment name |
| Authentication | `GatewayAuth`, a token for `GATEWAY_APP_ID_URI` + `/.default` | Fresh Entra token from the agent's identity on every call; any `api-key` header is removed |
| `Idempotency-Key` | New random value per HTTP request, unless one is already set | A replayed request gets `409` instead of being billed twice |
| `traceparent` | From the active OpenTelemetry span, when one exists | The gateway's spans join the agent's trace |
| SDK retries | Off | Resend automatically, with a new key and backoff, only when the error says `retryable: true`; after a mid-stream error frame, re-running the turn is a deliberate agent decision ([ADR 0002](../docs/decisions/0002-retry-only-before-first-byte.md)) |
| API style | Chat Completions, streaming, usage included | The gateway serves streaming Chat Completions only |

| Variable | Required | Example (placeholder) |
|---|---|---|
| `GATEWAY_BASE_URL` | Yes | `https://<gateway-fqdn>/v1` |
| `GATEWAY_APP_ID_URI` | Yes | `api://<gateway-app-id>` |
| `GATEWAY_MODEL_ALIAS` | Yes | `gpt-4o-mini` |
| `AZURE_CLIENT_ID` | No | Client ID of a user-assigned identity, when the agent uses one |
| `PORT` | No | Port the hosting adapter listens on; the code's default is the observed hosted-agent port (preview; verify) |
| `LOG_LEVEL` | No | `INFO` |

`load_settings()` lists every problem at once and refuses to start. It rejects a base URL that is not https (plain http is accepted for loopback hosts only), any `GATEWAY_` variable whose name looks like a secret, and the presence of `OPENAI_API_KEY`, `AZURE_OPENAI_API_KEY` or `AZURE_CLIENT_SECRET`, so no client in the container can fall back to a key.

The agent identity holds the gateway's `Gateway.Invoke` app role and **no** model role on the Foundry account, so the gateway is the only way it can reach a model. How the hosted-agent runtime exposes the agent's identity to `DefaultAzureCredential` is observed in preview; verify against current docs. [authentication-and-governance.md](../docs/authentication-and-governance.md) covers the role assignments and token claims in detail.

Build the agent image with the same rules as the [hosted agent container pattern](../../production-hosted-agents-on-foundry/src/README.md), from a hashed lock generated from [`agent/requirements.txt`](agent/requirements.txt).

## Run locally

To see the behaviour without Azure (failover, cancellation, budgets, keepalive, idempotency), run the offline simulator:

```bash
python samples/streaming_gateway_sim.py --demo
```

To run the real gateway on your machine, use a development app registration that exposes the `Gateway.Invoke` role, assign that role to your user, and give your user a model inference role on a development Foundry account. Locally, the gateway calls Foundry with your developer sign-in. In the policy, the caller key is the `azp` claim of the token you will send, which is the client ID of the tool you sign in with, not your user ID.

```bash
cd src/gateway
python -m pip install -r requirements.txt
export GATEWAY_TENANT_ID=<tenant-id>
export GATEWAY_AUDIENCES=api://<dev-gateway-app-id>
export GATEWAY_REQUIRED_ROLE=Gateway.Invoke
export GATEWAY_KEEPALIVE_SECONDS=<seconds>
export GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS=<seconds>
export GATEWAY_MAX_ATTEMPTS=<integer>
export GATEWAY_INGRESS_IDLE_TIMEOUT_SECONDS=<seconds>
export BACKEND_FOUNDRY_URL=https://<dev-foundry-account>.openai.azure.com/openai/v1
export GATEWAY_POLICY_JSON="$(cat <path-to-your-dev-policy>.json)"
python -m uvicorn app:app --host 127.0.0.1 --port 8080

# In a second shell:
curl -s localhost:8080/healthz
# No token: expect 401 with code missing_token.
curl -s -X POST localhost:8080/v1/chat/completions -H 'Content-Type: application/json' \
  -d '{"model":"gpt-4o-mini","stream":true,"messages":[{"role":"user","content":"hello"}]}'
# With a token: events arrive one by one; -N stops curl from buffering them.
TOKEN=$(az account get-access-token --scope api://<dev-gateway-app-id>/.default --query accessToken -o tsv)
curl -N -X POST localhost:8080/v1/chat/completions \
  -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: local-demo-0001' \
  -d '{"model":"gpt-4o-mini","stream":true,"messages":[{"role":"user","content":"hello"}]}'
```

Send the last request again with the same `Idempotency-Key` to see `409 duplicate_request`. To run the agent against this gateway, set `GATEWAY_BASE_URL=http://127.0.0.1:8080/v1` and `GATEWAY_APP_ID_URI=api://<dev-gateway-app-id>`, register the agent's client ID as a caller in the policy, and install [`agent/requirements.txt`](agent/requirements.txt). [evaluation-and-troubleshooting.md](../docs/evaluation-and-troubleshooting.md) shows how to check that events are not buffered on the way.

## What not to do

| Anti-pattern | Instead |
|---|---|
| An API key for the gateway in the agent container or version definition | The agent's Entra token for the gateway's app ID URI, checked for issuer, audience, tenant, app role and caller |
| Forwarding the caller's token to a backend | The gateway's own managed identity for Foundry; a Key Vault reference for a bring-your-own key |
| Compression, response buffering or a proxy that waits for the whole body | Pass-through SSE, one event at a time, with keepalive below every idle timeout on the path |
| Retrying in the model SDK or in an intermediary after output has started | Failover only before the first data byte; whole-turn retry with a new key on `retryable: true` |
| Default timeouts copied from somewhere else | Values measured on your backends and your ingress path; the startup checks keep them consistent |
| Several gateway replicas with the in-memory ledger | One replica, or a shared ledger and idempotency store |
| Logging prompts, completions or backend error bodies | Request IDs, caller IDs, aliases, backend names, token counts and outcomes |

## Related

- [Architecture](../docs/architecture.md), [Authentication and governance](../docs/authentication-and-governance.md), [Evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md) and [Deployment and operations](../docs/deployment-and-operations.md)
- [ADR 0002: Retry only before the first byte](../docs/decisions/0002-retry-only-before-first-byte.md) and [ADR 0003: Entra and managed identity only](../docs/decisions/0003-gateway-auth-entra-mi-only.md)
- [Infrastructure for this asset](../infra/README.md) and the [offline simulator](../samples/README.md)
- [Production Hosted Agents on Foundry](../../production-hosted-agents-on-foundry/README.md)
- [Request Context Is Not Authorization](../../request-context-is-not-authorization/README.md)
