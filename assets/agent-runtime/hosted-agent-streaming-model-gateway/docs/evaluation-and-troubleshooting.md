# Evaluation and troubleshooting

A streaming gateway can return the right bytes and still be broken: it may deliver them all at the end, cut long answers, bill twice, or let unauthorised callers through. This document lists what to test, how to prove the stream is really streaming, the failures teams hit most often, and a runbook for triage.

> **No numbers.** This document gives no latency, throughput or timeout values. Measure first-chunk and last-chunk arrival in your own environment, and record your own timeouts per layer. Hosted agent and Foundry connection behaviour marked "observed in preview; verify against current docs" may have changed.

- [Samples guide](../samples/README.md)
- [Architecture](architecture.md)
- [Authentication and governance](authentication-and-governance.md)
- [Deployment and operations](deployment-and-operations.md)

## Test layers

| Layer | Runs where | Proves | When |
|---|---|---|---|
| Offline simulation | Any machine, CI | The gateway rules: policy order, SSE framing, failover only before the first byte, keepalive, cancellation, idempotency, usage accounting | Every change to the gateway or its policy |
| Startup checks | CI, against each environment's policy document | The policy loads and the gateway refuses inconsistent configuration | Every policy change |
| Smoke test | Pipeline, from inside the network | The deployed revision is healthy, rejects unauthenticated calls, and streams one answer for a test caller | Every deployment |
| Streaming proof | Each network layer in turn | No layer buffers; keepalives cross every hop | First deployment, and after any network or proxy change |
| Negative authentication | Pipeline or operator, per environment | Wrong tokens and unassigned identities are rejected with the right codes | Every deployment |
| Failover and cancellation drills | Non-production | Failover is invisible before the first byte; cancellation closes the upstream and settles budgets | Before go-live and after backend changes |
| End-to-end agent run | The hosted agent in a non-production project | The agent's model client works through the gateway, including tool calls and the usage chunk | Every agent or gateway release |

Run the offline simulation first. It needs no Azure resources and fails fast on most logic mistakes:

```bash
python samples/streaming_gateway_sim.py --self-test
python samples/streaming_gateway_sim.py --domain all
```

## Test matrix

Each row names the simulation scenario that proves the rule offline and the check that proves it in a deployed environment.

| Behaviour | Simulation scenario | Live check | Expected |
|---|---|---|---|
| Happy path frame order | `happy_path_role_deltas_finish_usage_done` | Stream one answer with `include_usage` | Role chunk, deltas, finish chunk, usage chunk, `[DONE]` |
| Streaming headers | `sse_framing_and_streaming_headers` | `curl -i -N` on a streaming request | `text/event-stream`, `no-cache`, `X-Accel-Buffering: no`, `X-Request-Id` |
| Usage chunk only on request | `usage_chunk_only_when_requested` | Stream without `stream_options` | No chunk with empty `choices` |
| Token validation | `auth_rejects_bad_tokens_fail_closed`, `auth_accepts_v1_appid_claim` | Negative authentication table below | Exact `401` and `403` codes |
| Model allowlist | `policy_denies_model_not_allowed` | Ask for an alias the test caller is not allowed | `403 model_not_allowed`, no backend call |
| Token budget | `policy_enforces_token_budget` | Small budget for the test caller in non-production | `429 token_budget_exceeded` |
| Completion cap | `policy_clamps_max_tokens` | Ask for more than the cap | Answer stops at the cap; usage reflects it |
| Input safety | `content_safety_hook_blocks_input` | Send a blocked term for that caller | `400 content_filtered`, no backend call |
| Failover before first byte | `failover_before_first_byte`, `first_byte_deadline_cancels_and_fails_over` | Point the primary route at an unreachable host in non-production | Normal answer; usage event shows two attempts |
| All backends down | `all_backends_down_returns_json_502` | Point every route at an unreachable host | JSON `502 upstream_failed`, `retryable: true` |
| Mid-stream failure | `mid_stream_failure_error_frame_no_retry`, `anti_pattern_retry_after_first_byte_shows_harm` | Offline only; hard to force live | Error frame, no `[DONE]`, one attempt |
| Client disconnect | `client_disconnect_cancels_upstream` | Interrupt a long answer | Usage event `client_cancelled`, `usage_source=estimated` |
| Keepalive | `keepalive_survives_idle_gaps`, `no_keepalive_ingress_cuts_stream` | Slow test backend, see [Idle timeout test](#idle-timeout-test) | Stream survives with keepalive; is cut without it |
| Early-commit mode | `early_commit_keepalive_then_failover`, `early_commit_exhausted_is_sse_error` | Enable early commit on a non-production revision | Keepalive comments before content; errors as frames |
| No buffering | `buffering_detector` | [Prove the stream is not buffered](#prove-the-stream-is-not-buffered) | Frames spread over the answer, not one burst |
| Idempotency | `idempotency_key_blocks_double_billing` | Repeat a completed request with the same key | `409 duplicate_request` |
| Trace propagation | `traceparent_propagated` | Send a known `traceparent`, look it up in Application Insights | Gateway and backend spans share the trace ID |
| Usage without content | `usage_attributed_per_caller_without_content` | Inspect usage events | Caller and token counts, no prompt or completion text |
| Fail-closed configuration | `config_fails_closed` | Deploy a revision with a broken policy in non-production | Revision unhealthy, previous revision keeps serving |
| Production stubs | `production_stubs_raise` | Not applicable | The simulation cannot be shipped by accident |

## Prove the stream is not buffered

A buffered stream looks correct to every functional test: same frames, same order, same `[DONE]`. The only difference is **when** the frames arrive. So measure arrival, not content.

### Method

1. Pick a prompt that produces a long answer, so a buffered response is obvious.
2. Record, for each frame, the time it arrived relative to the moment the request was sent.
3. Compare three things:
   - **Time to first data frame** against **time to last data frame**. Streaming: the first frame arrives early and the rest spread out. Buffered: the first and last arrive almost together, at the end.
   - **Number of network reads** that delivered frames. Streaming: many reads. Buffered: one or two large reads.
   - **Keepalive comments.** If you configured them and the backend paused, you should see them. A layer that buffers usually swallows them.
4. Repeat from each vantage point, from the inside out: from a container in the same Container Apps environment, through the environment's ingress, through every proxy or firewall in front of it, and finally from the hosted agent's network. The first vantage point where frames bunch up is the layer that buffers.

### With curl

Run from a machine inside the network with an identity that has the `Gateway.Invoke` role, for example a jump host with a managed identity that is a test caller in a non-production policy:

```bash
az login --identity
TOKEN=$(az account get-access-token --resource "<gateway-app-id-uri>" --query accessToken -o tsv)

curl -sS -N "https://<gateway-fqdn>/v1/chat/completions" \
  -H "Authorization: Bearer ${TOKEN}" \
  -H "Content-Type: application/json" \
  -H "Accept: text/event-stream" \
  -d '{"model":"gpt-4o-mini","stream":true,"messages":[{"role":"user","content":"Write a long numbered list of packing tips."}]}' \
  | while IFS= read -r line; do printf '%s %s\n' "$(date +%T.%N)" "${line:0:60}"; done
```

`-N` turns off curl's own output buffering. Without it you would be testing curl, not the gateway.

### With a small Python probe

```python
import os, time, httpx
from azure.identity import DefaultAzureCredential

base_url = os.environ["GATEWAY_BASE_URL"]          # for example https://<gateway-fqdn>/v1
scope = os.environ["GATEWAY_APP_ID_URI"] + "/.default"
token = DefaultAzureCredential().get_token(scope).token

body = {"model": "gpt-4o-mini", "stream": True,
        "messages": [{"role": "user", "content": "Write a long numbered list of packing tips."}]}
start = time.monotonic()
data_times, keepalives, reads, done = [], 0, 0, False
with httpx.stream("POST", f"{base_url}/chat/completions", json=body, timeout=None,
                  headers={"Authorization": f"Bearer {token}", "Accept": "text/event-stream"}) as r:
    r.raise_for_status()
    for raw in r.iter_raw():
        reads += 1
        for line in raw.decode("utf-8", "replace").splitlines():
            if line.startswith(": "):
                keepalives += 1
            elif line == "data: [DONE]":
                done = True
            elif line.startswith("data: "):
                data_times.append(time.monotonic() - start)

print(f"reads={reads} data_frames={len(data_times)} keepalives={keepalives} done={done}")
print(f"first_data={data_times[0]:.3f}s last_data={data_times[-1]:.3f}s")
```

Read the output relatively: if `first_data` is close to `last_data` and `reads` is small compared with `data_frames`, something on the path is buffering. A frame can span two network reads, so `reads` is a hint, and the timestamps are the evidence.

### Usual culprits

- Compression or response-logging middleware on the gateway's streaming route.
- A gateway that reads the upstream body before writing (`await response.aread()`), or collects deltas into a list.
- A proxy or firewall that inspects TLS and holds the response until it can scan it.
- An application gateway or CDN in front of the ingress with response buffering enabled.
- The client reading the full body (`response.json()`, a non-streaming SDK call) instead of iterating.

## Negative authentication tests

Run these from inside the network in every environment. Each must fail with the exact code. A request that succeeds is a release blocker.

| Request | Expected |
|---|---|
| No `Authorization` header | `401 missing_token` |
| Malformed or unsigned token | `401 invalid_token` |
| Valid token for a different API (for example the Azure Resource Manager audience) | `401 invalid_audience` |
| Token from another tenant | `401 invalid_issuer` or `401 invalid_tenant` |
| Expired token | `401 token_expired` |
| Identity in the tenant without the `Gateway.Invoke` role | No token issued (if *Assignment required* is set), otherwise `403 missing_app_role` |
| Identity with the role but missing from the policy | `403 caller_not_allowed` |
| Allowed caller, alias not allowed | `403 model_not_allowed` |
| Allowed caller, header claiming to be another agent | Treated as itself; usage event shows its own `caller_id` |
| `GET /healthz` without a token | `200` with no configuration in the body |
| `GET /v1/models` without a token | `401 missing_token` |

## Failover and cancellation drills

Run these in non-production only.

- **Primary down.** Set the primary route's URL setting to an unreachable host and deploy a new revision. Agents should notice nothing. Usage events should show two attempts, the first `failed_before_first_byte`.
- **Primary slow.** Use a test backend that accepts the connection and waits before its first token. With hold-headers mode, the first-byte deadline should cancel it and fail over. If the agent's own timeout or the ingress cuts first, your deadlines are in the wrong order.
- **All down.** Point every route at unreachable hosts. Expect JSON `502 upstream_failed` with `retryable: true`, and confirm the agent retries with backoff rather than in a tight loop.
- **Client disconnect.** Start a long answer and interrupt the client. Expect a usage event with `outcome=client_cancelled` and `usage_source=estimated`, and no further upstream traffic for that request in backend logs.
- **Key rotation.** Rotate the bring-your-own key following [Authentication and governance](authentication-and-governance.md#bring-your-own-backend-credentials) and confirm there is no failure window.

### Idle timeout test

1. List every hop between the agent and the backend and its idle timeout (agent client, proxies, ingress, gateway upstream client, backend). The smallest one wins.
2. In non-production, use a test backend that pauses mid-answer for longer than that smallest timeout.
3. With keepalive enabled below that timeout, the stream must survive and the client must see `: keepalive` comments during the pause.
4. With keepalive disabled (non-production only), the stream should be cut and the usage event should show `ingress_idle_cut` or `client_cancelled`. That confirms you know where the limit is.

## Common failures

### The whole answer arrives at once

- **Symptom.** Functional tests pass; users see nothing, then the whole answer. Long answers sometimes fail with a timeout.
- **Cause.** A layer buffers: compression middleware, a proxy, a TLS-inspecting firewall, or a client that reads the whole body.
- **Fix.** Find the layer with [Prove the stream is not buffered](#prove-the-stream-is-not-buffered), then disable buffering there or route around it.

### 401 invalid_audience

- **Symptom.** Every call from one agent fails with `401 invalid_audience`.
- **Cause.** The agent requests a token for the wrong resource (often the Cognitive Services scope, copied from code that called Foundry directly), or the gateway's `GATEWAY_AUDIENCES` lists only the app ID URI while the API issues v2 tokens whose audience is the client ID.
- **Fix.** The agent requests `<app-id-uri>/.default`. The gateway lists both audience forms.

### 401 invalid_issuer

- **Cause.** The token version does not match what the gateway expects, or the token comes from another tenant.
- **Fix.** The gateway accepts both the v1 and v2 issuer for your tenant only. Check `requestedAccessTokenVersion` on the app registration.

### 403 missing_app_role right after assigning the role

- **Cause.** The agent still holds a token issued before the assignment. Tokens carry roles from issuance time.
- **Fix.** Wait for the cached token to expire, or restart the agent session so it gets a new token.

### 403 caller_not_allowed

- **Cause.** The identity has the role but its client ID is not a key in the policy's `callers`, or the policy was keyed by the object ID instead of the client ID.
- **Fix.** Key callers by the client ID that appears in `azp` or `appid`, then deploy the policy as a new revision.

### Streams are cut during long pauses

- **Symptom.** Answers end without `[DONE]` when the model pauses, for example while it builds a tool call or a slow model thinks.
- **Cause.** No keepalive, or a keepalive interval longer than the shortest idle timeout on the path. In hold-headers mode, a first-byte wait that is longer than the ingress idle timeout.
- **Fix.** Set the keepalive interval below the smallest timeout. Use early-commit mode for slow routes. The gateway's startup checks reject the obvious misconfigurations.

### Double billing on retry

- **Symptom.** Backend usage is higher than gateway usage, or the agent sometimes shows an answer that starts twice.
- **Cause.** Something retried after output was delivered: the agent SDK's automatic retry, the hosted-agent runtime, or a gateway change that retries after the first byte.
- **Fix.** The agent sends an `Idempotency-Key` per logical model call, and the gateway rejects repeats with `409`. Keep gateway retries strictly before the first data frame ([ADR 0002](decisions/0002-retry-only-before-first-byte.md)). Review the SDK's retry settings for streaming calls.

### Fast 502 on every Foundry route

- **Symptom.** `502 upstream_failed` immediately; backend logs show nothing.
- **Cause.** The gateway cannot get a managed identity token: `AZURE_CLIENT_ID` is missing or wrong, or a credential chain picked a credential that does not work in Container Apps.
- **Fix.** Set `AZURE_CLIENT_ID` to the user-assigned identity's client ID. See [The client ID gotcha in Container Apps](authentication-and-governance.md#the-client-id-gotcha-in-container-apps).

### 502 on the bring-your-own route only

- **Cause.** The key expired or was rotated without a restart; the base URL setting is missing the version path; the `upstream_model` name is wrong; or egress control blocks the provider's host name.
- **Fix.** Check the upstream status in gateway logs (never in the error envelope). Fix the secret, URL, model name or egress rule.

### Agent crashes on the last chunk

- **Symptom.** The answer streams, then the agent throws an index error at the end.
- **Cause.** The agent asked for `include_usage` and its code indexes `choices[0]` on the usage chunk, which has an empty `choices` list.
- **Fix.** Check `choices` before indexing, or do not request the usage chunk. The gateway still settles usage either way.

### Prompt agent run fails with an opaque server error

- **Cause.** A Foundry model gateway connection always calls with `stream=true`, and the gateway (or a route behind it) returned a non-streaming JSON body. Observed in preview; verify against current docs.
- **Fix.** Always stream when `stream=true`. Confirm the connection's base URL is the gateway base path, because Foundry appends `chat/completions`.

### Gateway revision never becomes healthy

- **Cause.** The startup checks refused the configuration: a missing required setting, a policy alias without a route, a route pointing at an unset variable, or a keepalive that is not shorter than the idle timeout.
- **Fix.** Read the gateway's startup log line; it names the problem. The previous revision keeps serving while you fix it.

### 429 for one caller

- **Cause.** The budget is too small, the agent is looping, or several replicas use in-memory budgets that do not match.
- **Fix.** Look at that caller's usage events before raising the budget. Use a shared budget store before running more than one replica.

## Runbook

### Triage

1. **Get the request ID.** Every response carries `X-Request-Id`, and every error envelope has `request_id`. Ask the agent owner for it, or find it from the trace ID.
2. **Find the usage event** for that request. `outcome`, `status` and `attempts` usually explain the failure.
3. **Classify:**
   - `401` or `403`: identity or policy. Check the token claims in the gateway log line for that request.
   - `429`: budget. Check the caller's recent usage.
   - `502` with `failed_before_first_byte` on every attempt: backends or the gateway identity.
   - `failed_after_first_byte`: a backend failed mid-answer. Check backend health.
   - `client_cancelled` or `ingress_idle_cut`: client or timeout. Check keepalive and timeouts.
4. **Check the trace.** Follow the trace ID from the agent span through the gateway span to the backend call.
5. **Decide.** A backend incident goes to the backend owner. A gateway configuration problem is fixed by a new revision. Do not edit settings on the live revision.

### Queries

The usage event is emitted as a structured log record named `gateway.usage`. With the Azure Monitor OpenTelemetry distribution and a workspace-based Application Insights resource, it lands in the `AppTraces` table with its fields in `Properties`. Adjust names if you emit it differently.

Outcomes by backend:

```kusto
let lookback = 1d;
AppTraces
| where TimeGenerated > ago(lookback) and Message == "gateway.usage"
| extend outcome = tostring(Properties.outcome), backend = tostring(Properties.backend)
| summarize requests = count() by backend, outcome
| order by backend asc, requests desc
```

Token usage per caller and model:

```kusto
let lookback = 1d;
AppTraces
| where TimeGenerated > ago(lookback) and Message == "gateway.usage"
| extend caller = tostring(Properties.caller_id), model = tostring(Properties.model),
         prompt = toint(Properties.prompt_tokens), completion = toint(Properties.completion_tokens),
         source = tostring(Properties.usage_source)
| summarize prompt_tokens = sum(prompt), completion_tokens = sum(completion), requests = count() by caller, model, source
```

Authentication and policy denials:

```kusto
let lookback = 1d;
AppTraces
| where TimeGenerated > ago(lookback) and Message == "gateway.usage"
| extend status = toint(Properties.status), outcome = tostring(Properties.outcome), caller = tostring(Properties.caller_id)
| where status in (401, 403, 429)
| summarize denials = count() by outcome, caller
| order by denials desc
```

Requests where failover happened:

```kusto
let lookback = 1d;
AppTraces
| where TimeGenerated > ago(lookback) and Message == "gateway.usage"
| extend attempts = todynamic(tostring(Properties.attempts))
| where array_length(attempts) > 1
| project TimeGenerated, request_id = tostring(Properties.request_id), attempts, outcome = tostring(Properties.outcome)
```

### Release gate

Before a gateway revision takes production traffic:

- the simulation self-test passes in CI;
- the policy document passes the startup checks;
- the smoke test streams one answer and the unauthenticated request gets `401`;
- the negative authentication table passes;
- the streaming proof has been run since the last network change.

The [Agent Production Readiness Guide](../../../agentops-and-readiness/agent-production-readiness/README.md) describes how gates like these fit into the wider release process.
