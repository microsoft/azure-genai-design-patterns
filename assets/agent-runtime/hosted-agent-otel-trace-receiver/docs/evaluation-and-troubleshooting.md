# Evaluation and troubleshooting

A trace receiver fails quietly. OpenTelemetry exporters run on a background thread, retry for a while, then drop spans and move on. The agent keeps answering users, and nobody notices that the second copy has stopped. That is why this document tests three things separately:

- the receiver keeps its rules: authentication, bounds, redaction and honest responses;
- spans really arrive in the destination;
- the platform's built-in tracing still works next to the second export.

It then lists the failures teams hit most often and gives a runbook for triage.

> **No numbers.** This document gives no latency, throughput, batch-size or timeout values. Pick your bounds and exporter settings deliberately, and record them per environment. Behaviour marked "observed in preview; verify against current docs" may have changed.

- [Samples guide](../samples/README.md)
- [Architecture](architecture.md)
- [Authentication and governance](authentication-and-governance.md)

## Test layers

| Layer | Runs where | Proves | When |
|---|---|---|---|
| Offline sample | Any machine, CI | Receiver rules: status codes, partial success, bounds, redaction, trusted stamps, retry semantics | Every change to the receiver, its policy or a Collector configuration that copies its rules |
| Loopback round trip | Developer machine | The HTTP layer: real sockets, `Content-Length`, gzip, body limits before the read | When you change the HTTP handling |
| Domain Pack checks | CI | Each route's policy: what it drops, what it hashes, how it routes | Every policy change |
| Smoke test | Pipeline, from inside the network | The deployed receiver is healthy, rejects an unauthenticated request with `401`, and accepts one batch from a test identity | Every deployment |
| Negative authentication | Pipeline or operator, per environment | Wrong tokens and unregistered identities are rejected with the right codes | Every deployment |
| Dual-export proof | A hosted agent in a non-production project | The same trace reaches Application Insights and the destination | First wiring, every agent release, and after any change to the exporter or receiver |
| Content-capture check | A hosted agent in a non-production project | No message content reaches the destination unless the route allows it | Every agent release |

Run the offline sample first. It needs no Azure resources and finds most rule mistakes:

```powershell
python assets/agent-runtime/hosted-agent-otel-trace-receiver/samples/otlp_trace_receiver.py --self-test
python assets/agent-runtime/hosted-agent-otel-trace-receiver/samples/otlp_trace_receiver.py --domain all
```

If you run a Collector instead of this receiver ([ADR 0003](decisions/0003-collector-first-receiver-as-reference.md)), the scenario names below are still your acceptance list. Write one test per row against the Collector configuration.

## Test matrix

Each row names the self-test scenario that proves the rule offline and the check that proves it in a deployed environment.

### Protocol and responses

| Behaviour | Self-test scenario | Live check | Expected |
|---|---|---|---|
| Valid batch stored | `full_success_json` | Send one batch with a test identity | `200`, empty or no `partialSuccess`, spans in the destination |
| Empty request | `empty_request_success` | Not needed | `200` |
| Invalid span rejected, others kept | `partial_success_rejects_invalid_span` | Offline only | `200` with `partialSuccess.rejectedSpans` and a reason |
| Wrong path | `wrong_path_404` | `POST` to the base URL without `/v1/traces` | `404` |
| Wrong method | `method_not_allowed_405` | `GET /v1/traces` | `405` |
| Protobuf without a decoder | `protobuf_needs_decoder_415` | Default SDK exporter against the sample receiver | `415` |
| Unknown content type | `unsupported_content_type_415` | `Content-Type: text/plain` | `415` |
| Body over the bound | `payload_too_large_413` | Oversized test body | `413`, rejected before the body is read |
| Decompression bomb | `gzip_bomb_413` | Offline only | `413` |
| Gzip accepted | `gzip_roundtrip_success` | Exporter with gzip compression on | `200` |
| Malformed JSON | `malformed_json_400` | Truncated test body | `400` with a JSON error body |
| More spans than the span bound | `span_limit_partial_success` | Exporter batch size above the bound in non-production | `200` with `rejectedSpans` and reason `over_span_limit` |
| Logs and metrics on the traces receiver | `logs_and_metrics_discarded_by_policy` | Base-endpoint wiring (Path 1) | `200` with `rejectedLogRecords` or `rejectedDataPoints` under `discard`; `404` under `reject` |
| Loopback HTTP layer | `serve_roundtrip_loopback` | Not applicable | Real socket round trip, clean shutdown |

### Authentication and routing

| Behaviour | Self-test scenario | Live check | Expected |
|---|---|---|---|
| No token | `missing_token_401` | `curl` without `Authorization` | `401` with a `WWW-Authenticate` challenge |
| Expired token | `expired_token_401` | Offline only | `401 token expired` |
| Wrong audience | `wrong_audience_401` | Token for another resource | `401 invalid audience` |
| Other tenant | `untrusted_tenant_401` | Offline only, or a token from a test tenant | `401 untrusted tenant` |
| v2 token with `azp` | `v2_token_with_azp_accepted` | Only if your registration issues v2 tokens | `200` |
| Identity not registered | `unlisted_client_403` | A managed identity that has the role but is not in the policy | `403 client is not allowed to export to this receiver` |
| Role missing | `missing_app_role_403` | A registered identity without the role assignment | `403 token lacks the Traces.Write app role` |
| Shared header limited to development | `shared_header_dev_only` | Configuration review | Refused outside the development environment |
| Payload cannot choose the route | `resource_attributes_not_used_for_routing` | Send a spoofed `service.name` or tenant attribute | Stored under the token's route; spoofed `trace_receiver.*` keys stripped |
| Trace context kept | `trace_context_preserved` | [Prove dual export](#prove-dual-export) | Same trace id and parent links in both copies |

### Redaction

| Behaviour | Self-test scenario | Live check | Expected |
|---|---|---|---|
| Content dropped by default | `content_dropped_by_default` | [Prove content stays out](#prove-content-stays-out) | No `gen_ai.input.messages` or similar in the destination |
| Identifiers hashed stably | `identifiers_hashed_stably` | Two turns of one conversation | Same `hmac-sha256:` value for both turns; no raw id |
| Unknown attributes dropped and reported | `unknown_attribute_dropped_and_reported` | Add a custom attribute in a test agent | Dropped; key listed in `droppedKeys` |
| Long values truncated | `attribute_value_truncated` | Offline only | Value cut to the bound; `truncated` count |
| Attribute count bounded | `attribute_count_bounded` | Offline only | Extra attributes dropped; `overAttributeLimit` count |
| Nested and bytes values dropped | `nested_and_bytes_values_dropped` | Offline only | Dropped; `unsupportedValue` count |
| Status and exception messages dropped | `status_and_exception_messages_dropped` | Force a tool error in a test agent | Status code kept, message dropped; `statusMessagesDropped` count |
| The report never carries values | `redaction_report_has_no_values` | Inspect the receiver's logs | Counts and key names only |

### Resilience

| Behaviour | Self-test scenario | Live check | Expected |
|---|---|---|---|
| Rate limit | `rate_limited_429_retry_after` | Tight limit for a test identity in non-production | `429` with `Retry-After`; the exporter retries later |
| Sink outage | `sink_unavailable_503_retry_after` | Make the destination unreachable in non-production | `503` with `Retry-After`; nothing reported as stored |
| Exporter retry rules | `exporter_retry_semantics` | Exporter logs during the two checks above | Retries only on `429`, `502`, `503` and `504`; `Retry-After` wins over the computed backoff; backoff grows and is capped |
| Production stubs | `production_stubs_raise` | Not applicable | Stubs raise `NotImplementedError`, and a non-loopback bind is refused, so the sample cannot be deployed by accident |
| Policy guards | `policy_guards` | Startup of the deployed receiver | A policy with no span bound, an unknown non-trace behaviour, content capture on a route that forbids it, or an empty hashing key is refused |

## Prove spans arrive

Work outward from the receiver, one hop at a time.

1. **Loopback.** Start `--serve` in one terminal and run `--send` in another. You should see `200` and one line per accepted batch. This proves the HTTP handling on your machine; it says nothing about the network.
2. **Unauthenticated probe.** From inside the network where the agent runs, send a request without a token:

   ```bash
   curl -i -X POST "https://<receiver-host>/v1/traces" \
     -H "Content-Type: application/json" \
     --data '{"resourceSpans":[]}'
   ```

   Expect `401` with a `WWW-Authenticate` header. A timeout means egress or name resolution is blocked. A `404` means the path is wrong. A `200` means authentication is off, which must never pass a release gate.
3. **Authenticated test batch.** Use a test identity that has the `Traces.Write` role and is registered in the receiver policy. Send a small hand-written batch that follows the [request subset](architecture.md). Expect `200`, then find the span in the destination by its trace id.
4. **Real agent.** Invoke the hosted agent once in a non-production project, note the trace id from the agent's response metadata or from Application Insights, and look for it in the destination.

If step 3 works and step 4 does not, the problem is on the exporter side: endpoint, protocol, token or flush. See [Common failures](#common-failures).

## Prove dual export

The second export must never cost you the first. After every wiring change, check both copies for the **same** trace.

1. Invoke the agent once and capture the trace id (32 lowercase hex characters).
2. In Application Insights, find the operation. With workspace-based Application Insights, the trace id appears as `OperationId` (shown as `operation_Id` in the classic schema; observed in preview; verify against current docs):

   ```kusto
   let trace_id = "<trace-id>";
   union AppRequests, AppDependencies
   | where OperationId == trace_id
   | project TimeGenerated, Name, Id, ParentId, Success
   | order by TimeGenerated asc
   ```

3. In the destination, list the spans with the same `traceId` and compare:
   - the same span names for the agent, model and tool operations;
   - the same `spanId` and `parentSpanId` values, so the tree has the same shape;
   - the receiver's trusted stamps (`trace_receiver.route`, `trace_receiver.client_id`) present on every stored span.
4. Check the Foundry tracing view for the same run. It must still show the full run.

Differences you should expect: the destination has fewer attributes (redaction) and may have hashed identifiers; Application Insights may hold content if the agent captures it (see [Content capture governance](authentication-and-governance.md#content-capture-governance)). Differences you should not accept: a missing span, a different parent, or a trace that appears in only one copy.

## Prove content stays out

1. In a non-production agent, deliberately turn message content capture **on** for one run (for example with `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`; the accepted values depend on the instrumentation and on `OTEL_SEMCONV_STABILITY_OPT_IN`; verify for your version).
2. Invoke the agent with a recognisable test phrase.
3. In the destination, search the stored attributes for the phrase. It must not appear on a route whose policy does not allow content.
4. In the receiver's redaction report for that batch, `contentDropped` must be greater than zero.
5. Turn capture off again, and confirm `contentDropped` returns to zero for the next run.

This check also tells you whether the agent captures content by default. If `contentDropped` is above zero when you did not turn capture on, the built-in copy in Application Insights contains content too. That is a governance decision for the platform copy, not something the receiver can fix.

## Negative authentication tests

Run these from inside the network in every environment, after every deployment. Each must fail with the exact code. A request that succeeds is a release blocker. The messages are the sample's; keep yours equally specific in logs, and equally vague in what you return to callers you do not trust.

| Request | How to produce it | Expected |
|---|---|---|
| No `Authorization` header | `curl` without the header | `401 missing bearer token` with a `WWW-Authenticate: Bearer` challenge |
| Malformed or unsigned token | Any string that is not a signed JWT | `401 malformed token` (the production validator also rejects a bad signature) |
| Valid token for another resource | A token for Azure Resource Manager or Microsoft Graph from a test identity | `401 invalid audience` |
| Token from another tenant | A test tenant, if you have one | `401 untrusted tenant` |
| Token with an unexpected issuer | A v2 token when the policy lists only v1 issuers, or the reverse | `401 untrusted issuer`; list both issuer forms for your tenant if you accept both versions |
| Expired token | Replay a token captured earlier in non-production | `401 token expired` |
| Identity with the `Traces.Write` role, not registered in the policy | A second managed identity with the role assignment | `403 client is not allowed to export to this receiver` |
| Registered identity without the role | Remove the assignment in non-production, then wait for cached tokens to expire | `403 token lacks the Traces.Write app role`, or no token at all if *Assignment required* is set on the enterprise application |
| User token for the receiver's audience | Only where the tenant lets a user obtain one, for example through a developer tool | `403 client is not allowed to export to this receiver`; the client id is the tool's, not an agent's |
| Shared header outside development | Configuration review, or start the receiver with it in a non-development environment | The receiver refuses to start |
| Valid identity, spoofed `service.name`, tenant attribute or `trace_receiver.route` | A test batch with those attributes | `200`; spans stored under the identity's own route; spoofed `trace_receiver.*` keys stripped |

The last row is not an authentication failure, but it belongs here: it proves that nothing in the payload can choose where data goes. See [Attributes are not authorization](authentication-and-governance.md#attributes-are-not-authorization).

## Common failures

### Spans stop arriving with no error

- **Symptom.** The agent answers normally and Application Insights shows new traces, but the destination has nothing new, or fewer spans than before. The receiver logs few or no requests from that client, and nobody saw an error.
- **Cause.** OpenTelemetry exporters fail quietly on a background thread. The usual causes:
  - the receiver or its sink was down for longer than the exporter's timeout and retry budget (`OTEL_EXPORTER_OTLP_TIMEOUT`, `OTEL_EXPORTER_OTLP_TRACES_TIMEOUT` and the SDK's own retry limit), so the exporter dropped the batches;
  - the batch span processor's queue filled (`OTEL_BSP_MAX_QUEUE_SIZE`) while exports were slow or failing, so new spans were dropped before export;
  - the agent process stops without calling `force_flush()` and `shutdown()` on the tracer provider, so the last batch never leaves;
  - in Path 2, the code that attaches the managed-identity token raises on the export thread (an expired cache, a wrong scope, an identity endpoint that is not reachable), and the exporter logs the error and drops the batch;
  - a network change blocked egress from the agent to the receiver;
  - a sampler change (`OTEL_TRACES_SAMPLER`, `OTEL_TRACES_SAMPLER_ARG`) or a new tracer provider without the extra span processor. If both copies drop together, suspect the sampler.
- **Fix.**
  1. In a non-production run, enable the `opentelemetry` logger namespace at `WARNING` or lower in the agent. Export failures, timeouts and dropped spans appear there and nowhere else.
  2. Run the [dual-export gap query](#queries) to find when the second copy stopped and whether every agent is affected or one.
  3. Run the unauthenticated probe from [Prove spans arrive](#prove-spans-arrive) to rule out egress and name resolution.
  4. Check the receiver's `429` and `503` counts for the period. If the sink was down, the gap matches the outage.
  5. Make sure the agent flushes on shutdown, and that the token code catches its own errors and logs them.

### Every export gets 404

- **Symptom.** The exporter logs `404`. The receiver logs requests to `/v1/traces/v1/traces`, or to `/` or `/otlp`.
- **Cause.** The endpoint variables were mixed up. `OTEL_EXPORTER_OTLP_ENDPOINT` is a base URL, and the exporter appends `/v1/traces`. `OTEL_EXPORTER_OTLP_TRACES_ENDPOINT` is used as-is. Putting the full path in the base variable doubles it; putting the base URL in the per-signal variable drops it.
- **Fix.** Use one variable deliberately, as in [Wiring a hosted agent](../README.md#wiring-a-hosted-agent). In Path 2, pass the full `/v1/traces` URL to the span exporter.

### Every export gets 415

- **Symptom.** `415 Unsupported Media Type`, and the message mentions a decoder.
- **Cause.** The exporter sends `application/x-protobuf`, which is the default for most OTLP/HTTP exporters, and the receiver only decodes `application/json`. The sample's protobuf decoder is a stub on purpose.
- **Fix.** Register a protobuf decoder in the receiver, or put a Collector in front ([ADR 0003](decisions/0003-collector-first-receiver-as-reference.md)). Switching the exporter to JSON (`OTEL_EXPORTER_OTLP_PROTOCOL=http/json`) works only where the SDK supports it; support varies by language and version, so verify.

### 401 invalid audience

- **Symptom.** Every export from one agent fails with `401 invalid audience`.
- **Cause.** The agent asks for a token for the wrong resource, often a scope copied from code that calls a model or Foundry directly. Or the token is a v2 token whose `aud` is the receiver's client id, while the policy lists only the app ID URI.
- **Fix.** The agent requests `api://<receiver-app-id>/.default` (or your app ID URI followed by `/.default`). The receiver lists both audience forms if it accepts both token versions. See [Claims the receiver must check](authentication-and-governance.md#claims-the-receiver-must-check).

### 401 untrusted tenant or untrusted issuer

- **Cause.** The token comes from another tenant, or its version does not match the issuers the policy lists. Managed-identity tokens are usually v1 (observed in preview; verify against current docs).
- **Fix.** Accept your own tenant only. List the v1 and v2 issuer for that tenant if you accept both, or set `requestedAccessTokenVersion` on the app registration and list one.

### 403 right after assigning the role

- **Symptom.** `403 token lacks the Traces.Write app role` even though the assignment exists.
- **Cause.** The agent still holds a token issued before the assignment. Tokens carry roles from issuance time.
- **Fix.** Wait for the cached token to expire, or restart the agent so it gets a new one.

### 403 client is not allowed

- **Cause.** The identity is not in the policy's `clients` map, or the map is keyed by the wrong value: the object id or the display name instead of the client id that appears in `azp` (v2) or `appid` (v1).
- **Fix.** Key the map by the managed identity's client id. A new agent identity needs a policy change and a review, not only a role assignment.

### Content shows up in the redaction report

- **Symptom.** `contentDropped` rises for one client, though nobody changed the receiver.
- **Cause.** Content capture was turned on in the agent, by an environment variable, an SDK upgrade that changed the default, or a framework setting.
- **Fix.** The receiver policy is the backstop and has already dropped the content from the destination. Turn capture off in the agent, then check the built-in copy: if content was captured, it is in Application Insights too. Follow [Content capture governance](authentication-and-governance.md#content-capture-governance).

### Expected attributes are missing in the destination

- **Symptom.** Dashboards show empty columns for the provider, the model or token usage. `droppedKeys` in the redaction report lists keys such as `gen_ai.system`.
- **Cause.** The GenAI semantic conventions are still in development and attributes are renamed between versions. The instrumentation emits an older or newer name than the allowlist holds. For example, `gen_ai.system` was replaced by `gen_ai.provider.name`.
- **Fix.** Review `droppedKeys` after every SDK or instrumentation upgrade. Add a renamed attribute to the allowlist only after checking that it is not content and does not identify a person. Never allow `gen_ai.*` with a wildcard.

### Logs and metrics reach the traces receiver

- **Symptom.** `rejectedLogRecords` or `rejectedDataPoints` in responses, or `404` on `/v1/logs` and `/v1/metrics`.
- **Cause.** Path 1 exports all signals to the base endpoint (observed in preview).
- **Fix.** Keep `other_signals` at `discard` so the exporter does not retry, or move to Path 2, which exports traces only. Do not store the other signals in a traces destination without a separate review.

### 413 on some batches

- **Cause.** A batch with large attributes, many events or content exceeds the body or decompressed bound. Gzip helps the body bound but not the decompressed bound.
- **Fix.** Lower the exporter batch size (`OTEL_BSP_MAX_EXPORT_BATCH_SIZE`), turn content capture off, or raise the bound deliberately after a review. The exporter drops a batch that gets `413`; it does not retry.

### Partial success with over_span_limit

- **Symptom.** `200` with `partialSuccess.rejectedSpans` and `over_span_limit` in `errorMessage`. Some spans of a run are missing.
- **Cause.** The exporter's batch size is larger than `max_spans_per_request`.
- **Fix.** Align `OTEL_BSP_MAX_EXPORT_BATCH_SIZE` with the receiver bound. Partial success is not retried, so the extra spans are lost until you do.

### Retry storms, or Retry-After ignored

- **Symptom.** `429` counts climb in waves; the same client retries immediately.
- **Cause.** The exporter does not honour `Retry-After`, or many agents restart together and export their queues at once. Whether a given SDK version honours `Retry-After` varies; verify.
- **Fix.** Check the exporter version, and rely on its exponential backoff when it ignores the header. Put a Collector in front to absorb bursts, or raise the per-client limit after a review. Never answer `200` to shed load.

### Duplicate spans after a retry

- **Cause.** The receiver stored a batch, then the exporter retried after a timeout or a `503` from a second sink. Retries carry the same span ids.
- **Fix.** Sinks upsert on `(traceId, spanId)` or use deterministic object names. Analytics must not count spans that share both ids twice.

### The Foundry tracing view is empty after wiring

- **Cause.** Someone replaced the platform's Application Insights export instead of adding a second exporter, or removed the Application Insights connection while wiring the receiver.
- **Fix.** Restore the platform export. The receiver is an addition, never a replacement. See [ADR 0001](decisions/0001-dual-export-not-replace-platform-tracing.md).

### Spans land under the wrong route

- **Cause.** The `clients` map points the identity at the wrong route, or someone expected an attribute such as `service.name` or a tenant attribute to choose the route.
- **Fix.** Correct the map and review it. Routes come from the validated client id only; payload attributes never route ([`resource_attributes_not_used_for_routing`](../samples/README.md#what-the-scenarios-cover)).

## Runbook

### Triage

1. **Get the trace id.** Ask the agent owner for the run, then find its trace id in Application Insights (`OperationId`) or in the Foundry tracing view.
2. **Check the platform copy first.** If the trace is missing in Application Insights too, the problem is in the agent or its sampling, not in the receiver. Fix that before anything else.
3. **Check the receiver for that client** over the same period. Look at status counts:
   - `401` or `403`: identity or policy. Compare the token's `aud`, `tid`, `azp` or `appid`, and `roles` with the policy.
   - `404` or `415`: exporter wiring. See the matching entries in [Common failures](#common-failures).
   - `413` or partial success: bounds against exporter batch settings.
   - `429` or `503`: rate limits or a sink outage.
   - No requests at all: [Spans stop arriving with no error](#spans-stop-arriving-with-no-error).
4. **Check the redaction report** for the batches that carried the trace. Rising `contentDropped` or new `droppedKeys` explain "missing" attributes and point at an upstream change.
5. **Check sink health.** Write failures, throttling or a full store on the route's destination.
6. **Decide.** An agent-side wiring problem goes to the agent owner with the evidence. A receiver policy change goes through review and a new deployment; never edit the policy on a running instance. A sink outage goes to the destination owner; exporters retry within their budget, and anything beyond it is lost from the second copy only.

### Queries

These queries assume the receiver emits one structured log record per request, named `trace_receiver.batch`, with `status`, `client_id`, `route` and the redaction report counts in its properties. With the Azure Monitor OpenTelemetry distribution and a workspace-based Application Insights resource, the record lands in `AppTraces` with its fields in `Properties`. The destination table `AgentSpans_CL` is an illustrative custom table. Adjust every name to what you emit and store.

Requests by status and client:

```kusto
let lookback = 1d;
AppTraces
| where TimeGenerated > ago(lookback) and Message == "trace_receiver.batch"
| extend status = toint(Properties.status), client = tostring(Properties.client_id), route = tostring(Properties.route)
| summarize requests = count() by route, client, status
| order by route asc, requests desc
```

Rejected spans and redaction by route:

```kusto
let lookback = 1d;
AppTraces
| where TimeGenerated > ago(lookback) and Message == "trace_receiver.batch"
| extend route = tostring(Properties.route),
         received = toint(Properties.spans_received), accepted = toint(Properties.spans_accepted),
         content_dropped = toint(Properties.content_dropped), not_allowlisted = toint(Properties.not_allowlisted)
| summarize received = sum(received), accepted = sum(accepted),
            content_dropped = sum(content_dropped), not_allowlisted = sum(not_allowlisted) by route
| extend rejected = received - accepted
```

New dropped keys, to review after an SDK or instrumentation upgrade:

```kusto
let lookback = 1d;
AppTraces
| where TimeGenerated > ago(lookback) and Message == "trace_receiver.batch"
| mv-expand key = todynamic(tostring(Properties.dropped_keys)) to typeof(string)
| summarize first_seen = min(TimeGenerated), batches = count() by key, route = tostring(Properties.route)
| order by first_seen desc
```

Dual-export gap: traces that reached Application Insights but not the destination. In Application Insights, `AppRoleName` usually carries `service.name`, which for hosted agents is the agent name (observed in preview; verify).

```kusto
let lookback = 1d;
let platform = union AppRequests, AppDependencies
    | where TimeGenerated > ago(lookback) and AppRoleName == "<agent-name>"
    | distinct OperationId;
let second_copy = AgentSpans_CL
    | where TimeGenerated > ago(lookback)
    | distinct TraceId;
platform
| join kind=leftanti (second_copy) on $left.OperationId == $right.TraceId
| summarize missing_traces = count()
```

Run the gap query on a schedule and alert when `missing_traces` stays above zero. Allow for the export delay, and exclude traces that the route's policy deliberately does not keep. If the destination is not Log Analytics, run the same comparison in the destination's own query tool, keyed on the trace id.

### Release gate

Before a receiver deployment, a policy change or an agent release that changes telemetry wiring:

- the offline self-test and the Domain Pack checks pass in CI, or, with a Collector, its acceptance tests cover the same scenario list;
- the policy for each environment loads without tripping the startup guards;
- the smoke test passes: the unauthenticated probe gets `401`, and a test batch from a test identity gets `200` and appears in the destination;
- the [negative authentication table](#negative-authentication-tests) passes;
- the shared-header authenticator is off outside development, and the receiver exposes no read endpoints;
- the [dual-export proof](#prove-dual-export) and the [content check](#prove-content-stays-out) have been run for this agent release;
- retention, residency and reader access on the destination match the [governance review](authentication-and-governance.md#review-checklist).

The [Agent Production Readiness Guide](../../../agentops-and-readiness/agent-production-readiness/README.md) describes how gates like these fit into the wider release process.

## Related

- [Architecture](architecture.md)
- [Authentication and governance](authentication-and-governance.md)
- [Samples guide](../samples/README.md)
- [Production Hosted Agents on Foundry: observability](../../production-hosted-agents-on-foundry/docs/architecture.md#11-observability)
- [Hosted-agent streaming model gateway: trace propagation](../../hosted-agent-streaming-model-gateway/docs/architecture.md#trace-propagation)
- [Configure telemetry for hosted agents](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-hosted-agent-telemetry)
- [OTLP specification](https://opentelemetry.io/docs/specs/otlp/)
- [OpenTelemetry SDK environment variables](https://opentelemetry.io/docs/specs/otel/configuration/sdk-environment-variables/)
