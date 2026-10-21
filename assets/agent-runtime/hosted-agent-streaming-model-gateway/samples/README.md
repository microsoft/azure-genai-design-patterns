# Samples: Streaming Model Gateway Simulator

`streaming_gateway_sim.py` is a single-file, standard-library-only simulation of the
gateway described in [architecture.md](../docs/architecture.md). It models a hosted
agent calling its model through a team-owned, OpenAI-compatible streaming gateway, the
ingress layer between them, and a set of scripted model backends that can be fast, slow
to the first token, down, or fail halfway through a stream.

Use it to read the logic, to argue about edge cases with your platform and security
teams, and as an executable specification for the tests you write against your own
gateway. Each scenario is a short function that asserts one rule from the docs or an ADR.

> **Test only.** Every delay is a logical clock tick. Ticks only order events: they are
> not seconds and say nothing about real latency, throughput, capacity or cost. There is
> no network call anywhere in the file. The fake tokens are HMAC-signed with an
> in-memory key that exists only for the simulation; real Microsoft Entra tokens are
> RS256-signed and validated against the tenant's published keys (see
> [src/gateway/app.py](../src/gateway/app.py)). Wire shapes follow the OpenAI Chat
> Completions streaming format as observed in preview; verify against current docs and
> the SDK version you pin.

## Prerequisites

- Python 3.10 or later. No packages to install.

## Run it

From the repository root:

```powershell
python assets/agent-runtime/hosted-agent-streaming-model-gateway/samples/streaming_gateway_sim.py --self-test
python assets/agent-runtime/hosted-agent-streaming-model-gateway/samples/streaming_gateway_sim.py --domain all
python assets/agent-runtime/hosted-agent-streaming-model-gateway/samples/streaming_gateway_sim.py --demo
```

- `--self-test` runs every scenario. It prints `PASS <scenario>` or
  `FAIL <scenario>: <reason>` for each one, then `N/25 scenarios passed`. It exits 0
  when all pass and 1 otherwise.
- `--domain contoso-retail|fabrikam-health|northwind-logistics|all` runs a fictional
  domain pack: a caller policy, a route table and four requests with expected outcomes.
  It prints one line per request (status, outcome, keepalives, backend path) and the
  caller's ledger, and exits 1 if any expectation is not met.
- `--demo` prints a readable trace of the SSE frames the agent would receive:
  1. a happy path with the usage event the gateway emits
  2. a failover before the first byte
  3. a failure mid-stream: an error frame and no `[DONE]`
  4. keepalive comments across long pauses
  5. a policy deny that never reaches a backend

The demo never prints a token or prompt text, and the usage event never contains one.

## What the scenarios cover

| Area | Scenarios |
| --- | --- |
| Wire format | `happy_path_role_deltas_finish_usage_done`, `sse_framing_and_streaming_headers`, `usage_chunk_only_when_requested` |
| Caller authentication | `auth_rejects_bad_tokens_fail_closed` (missing, forged, wrong issuer, wrong audience, wrong tenant, expired, no app role, unknown caller), `auth_accepts_v1_appid_claim` |
| Policy | `policy_denies_model_not_allowed`, `policy_enforces_token_budget`, `policy_clamps_max_tokens`, `content_safety_hook_blocks_input` |
| Routing and failover | `failover_before_first_byte`, `first_byte_deadline_cancels_and_fails_over`, `all_backends_down_returns_json_502`, `mid_stream_failure_error_frame_no_retry`, `anti_pattern_retry_after_first_byte_shows_harm` |
| Streaming transport | `client_disconnect_cancels_upstream`, `keepalive_survives_idle_gaps`, `no_keepalive_ingress_cuts_stream`, `early_commit_keepalive_then_failover`, `early_commit_exhausted_is_sse_error`, `buffering_detector` |
| Billing and telemetry | `idempotency_key_blocks_double_billing`, `traceparent_propagated`, `usage_attributed_per_caller_without_content` |
| Safety of the code itself | `config_fails_closed`, `production_stubs_raise` |

## How the pieces map to the real gateway

| Simulation | Real component |
| --- | --- |
| `issue_token` and `StreamingGateway.authenticate` | Entra access token for the hosted agent's identity, validated with the tenant JWKS (`EntraTokenValidator` stub) |
| `CallerPolicy`, `GatewayConfig` | The policy JSON loaded at startup (see [architecture.md](../docs/architecture.md#policy-configuration)) |
| `TokenLedger` | A shared budget store; the in-memory ledger is single-replica only (`SharedTokenLedger` stub) |
| `KeywordSafetyHook` | A content-safety service call (`AzureContentSafetyHook` stub) |
| `FakeBackend` | A Foundry deployment (`FoundryChatBackend` stub) or a bring-your-own model endpoint whose credential lives in Key Vault (`KeyVaultBackendSecret` stub) |
| `ClientConnection` | The agent's HTTP connection seen through every proxy and load balancer in the path, with the shortest idle timeout and an optional buffering defect |
| `MemoryUsageSink` | OpenTelemetry logs to Application Insights (`AppInsightsUsageSink` stub) |

## Hold-headers mode and early-commit mode

The gateway has two ways to start a response, and the simulation runs both:

- **Hold-headers (default).** The gateway does not send the `200` and SSE headers until
  the first upstream delta arrives. If every backend fails first, the agent gets a clean
  JSON `502` it can retry. The total first-byte wait (deadline times attempts) must stay
  under the ingress idle timeout; the config check refuses to start otherwise.
- **Early-commit.** The gateway sends the headers at once and keeps the connection alive
  with SSE comments while it waits, which suits slow bring-your-own or reasoning
  backends. Failover is still allowed while no `data:` frame has been sent, because
  comments carry no content. Once every attempt fails, the error has to be an SSE error
  frame, because the status is already `200`.

## The fictional world

All names are placeholders.

| Domain pack | Caller | Models and routes | What it shows |
| --- | --- | --- | --- |
| `contoso-retail` | `agent-contoso-shopper` | `gpt-4o-mini`, `gpt-4o` on two Foundry deployments | failover when the primary is down, payment terms blocked at the gateway, an unlisted BYO model denied |
| `fabrikam-health` | `agent-fabrikam-care` | `gpt-4o` on Foundry deployments only; no BYO route by policy | a slow primary cancelled at the first-byte deadline, an identifier blocked, BYO denied for data-boundary reasons |
| `northwind-logistics` | `agent-northwind-dispatch` | `byo-chat` on a bring-your-own endpoint, `gpt-4o-mini` on Foundry | early-commit with keepalives across long pauses, a mid-stream failure, a dispatcher who closes the tab, and a small budget running out |

## Production stubs

The classes at the bottom of the file (`EntraTokenValidator`, `FoundryChatBackend`,
`KeyVaultBackendSecret`, `AzureContentSafetyHook`, `AppInsightsUsageSink`,
`SharedTokenLedger`) raise `NotImplementedError`. They mark where the simulation stops
and the deployable code in [src/](../src/README.md) begins. Do not ship the simulation.
