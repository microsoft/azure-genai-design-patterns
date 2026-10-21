# Samples: OTLP/HTTP trace receiver reference

`otlp_trace_receiver.py` is a single-file reference for the receiver described in
[architecture.md](../docs/architecture.md). It uses only the Python standard library. A
fake hosted-agent exporter builds an OTLP/JSON `ExportTraceServiceRequest` for one agent
turn, with `invoke_agent`, `chat` and `execute_tool` spans that carry `gen_ai.*`
attributes. A team-owned receiver then processes the batch in this order:

1. Authenticates the exporter.
2. Rate-limits the caller.
3. Bounds the payload.
4. Decodes it.
5. Validates every span.
6. Redacts attributes against an allowlist.
7. Stamps a trusted route taken from the token.
8. Fans the batch out to one or more sinks.
9. Answers with an OTLP-shaped response: full success, partial success, or an error.

Use the file to read the logic, to agree on edge cases with your platform and security
teams, and as an executable specification for tests against your own receiver or
Collector configuration. Each scenario is a short function that checks one rule from the
docs or an ADR.

> **Test only.** The file makes no outbound network call, and the optional `--serve`
> mode binds to `127.0.0.1` only. Tokens are unsigned fake JWTs that only
> `FakeEntraTokenValidator` accepts. The HMAC keys are demo values, not secrets. Real
> Microsoft Entra tokens are signed and must be validated against the tenant's published
> keys (the `EntraTokenValidator` stub). The OTLP wire shapes follow the OTLP
> specification. The OpenTelemetry GenAI semantic conventions are in Development status
> and may change; verify attribute names against the version you pin. Bounds are
> configurable parameters, not recommendations. This sample contains no latency,
> throughput, capacity or cost figures.

## Prerequisites

- Python 3.10 or later. There are no packages to install.

## Run it

From the repository root:

```powershell
python assets/agent-runtime/hosted-agent-otel-trace-receiver/samples/otlp_trace_receiver.py --self-test
python assets/agent-runtime/hosted-agent-otel-trace-receiver/samples/otlp_trace_receiver.py --domain all
python assets/agent-runtime/hosted-agent-otel-trace-receiver/samples/otlp_trace_receiver.py --demo
```

| Mode | What it does | Exit code |
| --- | --- | --- |
| `--self-test` | Runs every named scenario, prints `PASS <name>` or `FAIL <name>: <reason>`, then `N/M scenarios passed`. | 0 when all pass, 1 otherwise |
| `--domain contoso-retail\|fabrikam-health\|northwind-logistics\|all` | Runs a fictional Domain Pack with its own redaction and routing policy, then prints one `PASS`/`FAIL` line per check. | 0 when all checks pass, 1 otherwise |
| `--demo` | Narrates one export end to end: the batch sent, the response, the redaction report, the stored attributes, the trusted stamps, a `429` honoured with `Retry-After`, and a partial-success response. | 0 when the narrative completes |
| `--serve [--port N]` | Listens on `http://127.0.0.1:N/v1/traces` (default port 4318) and prints one line per accepted batch. It accepts fake tokens only. Stop it with Ctrl+C. | 0 |
| `--send [--port N]` | Posts one fake export batch to a local `--serve` and prints the HTTP status and body. | 0 on HTTP 200 |

Example of a local loopback round trip, using two terminals:

```powershell
# terminal 1
python assets/agent-runtime/hosted-agent-otel-trace-receiver/samples/otlp_trace_receiver.py --serve --port 4318
# terminal 2
python assets/agent-runtime/hosted-agent-otel-trace-receiver/samples/otlp_trace_receiver.py --send --port 4318
```

`make_server` refuses any host other than `127.0.0.1`. The self-test scenario
`serve_roundtrip_loopback` starts the server on an ephemeral loopback port, sends
requests over a real socket, and then shuts the server down and closes it.

## What the scenarios cover

| Area | Scenarios |
| --- | --- |
| OTLP success and partial success | `full_success_json`, `empty_request_success`, `partial_success_rejects_invalid_span`, `span_limit_partial_success` |
| Exporter authentication (fake Entra token) | `missing_token_401`, `expired_token_401`, `wrong_audience_401`, `untrusted_tenant_401`, `v2_token_with_azp_accepted`, `unlisted_client_403`, `missing_app_role_403`, `shared_header_dev_only` |
| Protocol surface | `wrong_path_404`, `method_not_allowed_405`, `protobuf_needs_decoder_415`, `unsupported_content_type_415`, `malformed_json_400` |
| Payload bounds | `payload_too_large_413`, `gzip_bomb_413`, `gzip_roundtrip_success`, `attribute_value_truncated`, `attribute_count_bounded` |
| Redaction | `content_dropped_by_default`, `identifiers_hashed_stably`, `unknown_attribute_dropped_and_reported`, `nested_and_bytes_values_dropped`, `status_and_exception_messages_dropped`, `redaction_report_has_no_values` |
| Trust and correlation | `resource_attributes_not_used_for_routing`, `trace_context_preserved` |
| Back-pressure and retry | `rate_limited_429_retry_after`, `sink_unavailable_503_retry_after`, `exporter_retry_semantics` |
| Signals other than traces | `logs_and_metrics_discarded_by_policy` |
| Safety of the code itself | `production_stubs_raise`, `policy_guards`, `serve_roundtrip_loopback` |

## How the pieces map to a real deployment

| Sample | Real component |
| --- | --- |
| `build_export_request` | The hosted agent's OpenTelemetry SDK, through a `BatchSpanProcessor` and an OTLP/HTTP span exporter |
| `export_with_retry`, `exporter_backoff_seconds` | The OTLP exporter's retry behaviour: retry `429`, `502`, `503` and `504`; honour `Retry-After`; never retry partial success or other `4xx` responses |
| `FakeEntraTokenValidator`, `TrustConfig` | Microsoft Entra access-token validation for the agent's managed identity: signature, `tid`, `iss`, `aud`, expiry, the `azp`/`appid` allowlist and the app role (the `EntraTokenValidator` stub) |
| `SharedHeaderAuthenticator` | A shared header for local development only. It refuses to run outside `dev`. |
| `JsonTraceDecoder` | OTLP/JSON decoding. Most SDK exporters send protobuf, so a production receiver needs `ProtobufTraceDecoder` (stub) or an OpenTelemetry Collector in front of it. |
| `ReceiverPolicy`, `AttributeRedactor` | The receiver policy loaded at startup (see [architecture.md](../docs/architecture.md#receiver-policy)) |
| `FixedWindowRateLimiter` | A per-client limit. The in-memory limiter works on a single replica only. |
| `InMemorySink` | An idempotent sink keyed on `(traceId, spanId)`: `BlobSink` (stub) or `LogAnalyticsSink` (stub) |
| `ConsoleSink` | The `--serve` sink. It prints span names only. |

## The fictional world

All names are placeholders.

| Domain pack | Exporter identity → route | Policy highlights | Checks |
| --- | --- | --- | --- |
| `contoso-retail` | `contoso-shopping-assistant-mi` → `contoso-retail` | Customer and order ids are hashed; store region is allowlisted; a debug payload attribute is dropped; the conversation id is kept for correlation | 7 |
| `fabrikam-health` | `fabrikam-care-navigator-mi` → `fabrikam-health` | Content capture is locked off by policy; patient id, record number, conversation id and end-user id are hashed with a domain-specific key; ward is dropped; errors keep only the status code and `exception.type` | 10 |
| `northwind-logistics` | `dispatch-agent-mi` → `northwind-emea`, `planner-agent-mi` → `northwind-amer` | Routing comes from the token. A spoofed `service.name`, a tenant resource attribute and a forged `trace_receiver.route` are ignored or stripped. Shipment and depot ids are kept. | 7 |

## Production stubs

`EntraTokenValidator`, `ProtobufTraceDecoder`, `BlobSink` and `LogAnalyticsSink` raise
`NotImplementedError`. They mark where the reference stops and your deployable code
begins:

| Stub | What to use instead |
| --- | --- |
| `EntraTokenValidator` | A maintained JWT library that validates the signature against the tenant's signing keys, plus the claim checks in [authentication-and-governance.md](../docs/authentication-and-governance.md#claims-the-receiver-must-check) |
| `ProtobufTraceDecoder` | The `opentelemetry-proto` generated classes, or an OpenTelemetry Collector `otlp` receiver that forwards to you |
| `BlobSink` | Azure Blob Storage through a managed identity with a data-plane role scoped to the container, using deterministic blob names for idempotency |
| `LogAnalyticsSink` | The Azure Monitor Logs Ingestion API with a data collection rule and a custom table |

Do not ship the reference as-is. It accepts fake tokens and holds rate-limit state in
memory.

## Extending the sample

- **Add an allowlisted attribute.** Add the key to `span_allowlist` or
  `resource_allowlist` in a policy, then add a scenario that sends the key and checks it
  is stored. Use `hashed_attributes` for anything that identifies a person, an account
  or a conversation.
- **Add a sink.** Implement the `TraceSink` protocol (`name` and
  `write(batch, principal)`). Raise `SinkUnavailable` for transient failures so that the
  receiver returns `503` with `Retry-After`.
- **Add a domain.** Write a `*_policy` factory and a `domain_*` function that returns
  `(check name, passed)` pairs, then register it in `DOMAINS`.
- **Run the mutation habit.** Before trusting a new scenario, break the rule it checks
  (for example, skip redaction) and confirm the scenario fails.
