# Authentication and governance

This document covers how the exporter proves who it is, which claims the receiver checks, why nothing inside the telemetry may decide access or routing, and how the second copy of the telemetry is classified, retained and read. The checks described here are implemented offline in [`samples/otlp_trace_receiver.py`](../samples/otlp_trace_receiver.py) (`TrustConfig`, `FakeEntraTokenValidator`, `SharedHeaderAuthenticator` and the `EntraTokenValidator` stub) and exercised by the `*_401`, `*_403`, `shared_header_dev_only` and `resource_attributes_not_used_for_routing` scenarios.

> **Preview.** Hosted agents, their telemetry settings and the identity the runtime gives the agent were observed in preview; verify against current docs before you rely on any statement marked that way. The OpenTelemetry GenAI semantic conventions are in Development status.

## Identity map

| Hop | Caller | Credential | Validated by | Grants |
|---|---|---|---|---|
| Agent to receiver (recommended) | Hosted agent identity | Entra access token for the receiver's app ID URI, requested from the agent's own identity | Receiver (signature, issuer, tenant, audience, lifetime, client allowlist, app role) | Writing traces to one route, nothing else |
| Agent to receiver (development only) | Whoever holds the secret | Static shared header | Receiver (constant-time compare) | Writing traces to the `dev` route |
| Agent to Collector, Collector to receiver (when a Collector sits in front) | Agent identity, then the Collector's own identity | Entra token on each hop, or the Collector's authentication extensions | Collector, then receiver | The receiver sees the Collector as the client; routing moves into Collector configuration |
| Agent to Application Insights | Platform | Managed by the hosted-agent runtime | Azure Monitor | The built-in copy; not touched by this asset |
| Receiver to sink | Receiver user-assigned managed identity | Entra access token for the sink's resource | Storage, Azure Monitor Logs Ingestion or your pipeline | Writing to the route's destination only |
| Pipeline to Azure | Deployment workload identity | OIDC federated credential, no stored secret | Microsoft Entra ID and Azure RBAC | Deploying the receiver and its role assignments |

The agent holds **no** receiver key and **no** sink credential. The receiver holds **no** agent credential. Readers of the destination never talk to the receiver.

## Exporter authentication options

| Option | How the agent authenticates | Strengths | Weaknesses | Use for |
|---|---|---|---|---|
| **Entra managed identity token (preferred)** | A second span exporter in the agent code attaches a fresh token for `api://<receiver-app-id>/.default` on each request | No secret to store or rotate; short-lived; bound to one audience; the client id is a signed claim; revocable by removing a role assignment | Needs a code change in the agent (an extra span processor and an authenticated HTTP session) | Every shared or production receiver |
| **Shared header** | `OTEL_EXPORTER_OTLP_HEADERS` or `OTEL_EXPORTER_OTLP_TRACES_HEADERS`, with the value referenced from a Foundry connection | No code change; works with the runtime's own exporter | A long-lived bearer secret; every holder looks the same; rotation means a new agent version; easy to leak into logs and definitions | Local and development only; the sample refuses it outside `dev` |
| **Mutual TLS** | Standard OpenTelemetry client certificate settings, where the SDK supports them | Strong channel binding | Puts a private key into the agent container and brings back secret distribution and rotation; support varies by SDK and was not verified for hosted agents | Only when a platform team already runs a client certificate authority for workloads |
| **Network position only** | None; the receiver trusts its private network | Simple | Any workload that can reach the network can write, and route spoofing becomes possible | Never on its own |

### Why the managed identity token is preferred

- **There is no secret to leak.** The identity library obtains and refreshes the token. Nothing lands in the version definition, the image, a connection or a log line.
- **The caller is a signed claim.** `azp` or `appid` identifies exactly which agent identity sent the batch, so routing, rate limits and audit attach to an identity rather than to whoever copied a header.
- **Revocation is a role assignment.** Removing the `Traces.Write` assignment stops new tokens from being issued for that identity (with *Assignment required* set), without touching other agents.
- **Audience binding.** A token issued for the receiver is useless against other APIs, and tokens issued for other APIs are rejected by the receiver.

### Why a static header cannot carry the token

The runtime's exporter reads headers from environment variables once, and hosted agent environment variables are fixed per version (observed in preview; verify against current docs). An Entra token expires and must be refreshed, so it cannot be placed in a static header. Two consequences follow:

1. **Path 1 (runtime environment variables)** can only carry a long-lived secret, which is why it is development-grade authentication. It is still a good fit when the endpoint is a private Collector that authenticates onward with its own identity.
2. **Path 2 (an extra span processor in code)** gives the exporter an HTTP session that asks the agent's identity for a token on each request. The identity library caches tokens, so the per-request cost is a cache lookup. Whether a given exporter version accepts a custom session or an authentication hook varies; verify against the SDK version you pin.

Path 2 also exports traces only, so the receiver does not have to discard metrics and logs it never wanted. See [Wiring a hosted agent](../README.md#wiring-a-hosted-agent) for the setup.

### Shared header rules for development

If you use a shared header on a developer machine or in a throwaway development environment:

- Keep the value in a Foundry connection and reference it with `${{connections.<connection>.credentials.<field>}}`. Never write it into the agent version definition, a template or a pipeline variable that prints.
- Compare it with a constant-time comparison, as `SharedHeaderAuthenticator` does with `hmac.compare_digest`.
- Route it to a development destination only. The sample stamps the route `dev` and refuses to construct the authenticator when the policy environment is anything other than `dev`.
- Rotate it whenever anyone who could read the connection leaves the team.

## Register the receiver in Entra

Create one app registration per environment for the receiver. It represents the receiver as a resource (an API); the receiver never signs in with it.

1. **App ID URI.** Set `api://<receiver-app-client-id>` or a verified-domain URI. Exporters request tokens for `<app-id-uri>/.default`.
2. **App role.** Add an application app role with the value `Traces.Write`, allowed member type *Applications*. Managed identities receive app-only tokens, and app roles are how those tokens carry an authorization claim.
3. **Assignment required.** On the receiver's enterprise application, set *Assignment required* to yes, so Entra refuses to issue a token to any identity without a role assignment.
4. **Assign the role** to each hosted agent identity (or Collector identity) that may export, and to nothing else. Assigning app roles to hosted agent identities was observed in preview; verify against current docs.
5. **Map identities to routes.** Add each identity's client id to the receiver's `clients` map with its route. An identity with the role but no route is still refused with `403`.

## Claims the receiver must check

The receiver validates every export request. It never caches a "this connection is authenticated" decision, because exporters reuse connections across token refreshes.

| Check | v1 access token | v2 access token | Sample failure |
|---|---|---|---|
| Signature | Signing keys from the tenant's OpenID configuration (JWKS), cached and refreshed when an unknown key id appears | Same | Not simulated; the `EntraTokenValidator` stub raises `NotImplementedError` |
| Algorithm | Only the asymmetric algorithm Entra uses; never `none` or a symmetric algorithm | Same | Not simulated |
| Bearer present | `Authorization: Bearer <token>` | Same | `401 missing bearer token`, with a `WWW-Authenticate` challenge |
| Tenant (`tid`) | Your tenant only | Same | `401 untrusted tenant` |
| Issuer (`iss`) | `https://sts.windows.net/<tenant-id>/` | `https://login.microsoftonline.com/<tenant-id>/v2.0` | `401 untrusted issuer` |
| Audience (`aud`) | Usually the app ID URI | Usually the receiver app's client id | `401 invalid audience` |
| Lifetime (`exp`, `nbf`) | Enforced with a small, configurable clock-skew allowance | Same | `401 token expired` or `401 token not yet valid` |
| Client allowlist | `appid` is a key in `clients` | `azp` is a key in `clients` | `403 client is not allowed to export to this receiver` |
| App role (`roles`) | Contains `Traces.Write` | Same | `403 token lacks the Traces.Write app role` |

Practical notes:

- **401 versus 403.** `401` means "I do not know who you are" and carries a `WWW-Authenticate` challenge. `403` means "I know who you are, and you may not write here". Both are non-retryable for an OTLP exporter, so both drop the batch; alert on them.
- **Configure both audience forms.** Managed identity tokens are usually v1 and carry `appid`; v2 tokens carry `azp`. Put the app ID URI and the client id in `audiences` so a token version change does not take the receiver down (verify which version your tenant issues).
- **Allow exactly one tenant** unless you deliberately operate a multi-tenant receiver. Do not accept the `common` or `organizations` issuers.
- **Fail closed.** If the signing keys cannot be fetched, reject the request. Do not skip signature checks to stay available; the exporter will retry or drop, and the built-in copy is unaffected.
- **Never log the token.** Log the claims you used (`tid`, `azp` or `appid`, `roles`, the token's unique id) with the request id. Error bodies never echo the token.
- **Platform authentication can do the signature step.** Built-in authentication on the hosting platform (for example Container Apps authentication) can validate the signature and issuer. The client allowlist, role check and route mapping still belong in the receiver.

## Platform versus receiver responsibilities

| Concern | Platform (Foundry and Application Insights) | Your receiver |
|---|---|---|
| Built-in trace export | Owns it; reserved settings | Never changes it ([ADR 0001](decisions/0001-dual-export-not-replace-platform-tracing.md)) |
| `service.name` | Set to the agent name; `OTEL_SERVICE_NAME` ignored (observed in preview; verify) | Stores it as data; never trusts it |
| Exporter authentication | Supplies the agent identity | Validates the token and maps the client to a route |
| Content capture | Controlled by instrumentation settings in the agent | Drops content attributes unless the route's policy allows capture ([ADR 0002](decisions/0002-redact-at-receiver-allowlist-attributes.md)) |
| Retention and readers of the built-in copy | Application Insights and Log Analytics settings | Not in scope |
| Retention and readers of the second copy | Not in scope | Owned by the route's destination and its data owner |
| Egress from the agent | Agent network configuration | Publishes a private endpoint or a fixed hostname for the allowlist |

## Attributes are not authorization

Everything inside an OTLP payload is data written by the exporter: `service.name`, `service.namespace`, `deployment.environment`, tenant or team attributes, `enduser.id`, baggage copied into attributes, and any header the agent forwarded. A compromised or misconfigured agent can set any of them. The receiver therefore:

- **Routes from the token.** The route comes from `clients[azp or appid]` and nothing else. The sample's `resource_attributes_not_used_for_routing` scenario and the Northwind Logistics pack send spoofed `tenant.id`, `northwind.tenant` and `trace_receiver.route` attributes and prove the route still comes from the token.
- **Owns its namespace.** Incoming attributes that start with `trace_receiver.` are stripped before redaction, and the receiver then stamps `trace_receiver.route`, `trace_receiver.client_id`, `trace_receiver.auth_method` and `trace_receiver.policy` on each resource. Downstream readers can trust those four keys and nothing else.
- **Does not authorize readers with span data.** A query layer over the destination must not decide who sees a span by reading a tenant attribute inside it. Partition by the stamped route and grant access per partition.
- **Treats `traceparent` as correlation only.** A trace id from the streaming gateway links spans; it proves nothing about who sent them.

This is the telemetry case of the general rule in [Request Context Is Not Authorization](../../request-context-is-not-authorization/README.md).

## Telemetry data classification

GenAI telemetry is not "just logs". Classify each attribute group before you allowlist it.

| Data | Typical keys | Default handling | Classification guidance |
|---|---|---|---|
| Operation shape | `gen_ai.operation.name`, `gen_ai.agent.name`, `gen_ai.tool.name`, span names, status codes | Allowlisted | Internal operational data |
| Model and usage | `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.response.model`, `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens` | Allowlisted | Internal; useful for cost attribution |
| Correlation ids | trace and span ids, `gen_ai.conversation.id`, `gen_ai.response.id` | Ids kept; conversation id hashed where policy requires | Pseudonymous; can link a person's activity across systems |
| End-user and domain identifiers | `enduser.id`, customer, patient or account keys | Hashed with a keyed HMAC, or dropped | Personal data; hash or drop |
| Message content | `gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions` | Dropped unless capture is explicitly allowed | As sensitive as the conversation itself; may contain personal, health, financial or confidential business data |
| Tool payloads | `gen_ai.tool.call.arguments`, `gen_ai.tool.call.result`, `gen_ai.tool.definitions` | Dropped unless capture is explicitly allowed | As sensitive as the tool's data source |
| Error text | span status message, `exception.message`, `exception.stacktrace` | Status code and `exception.type` kept; messages dropped | Can echo inputs, paths or connection details |
| Unknown keys | anything not on the allowlist | Dropped and named in the redaction report | Unreviewed; treat as sensitive until classified |

The redaction report itself contains counts and key names only, never values, so it can be logged at the same level as the receiver's other operational logs.

## Content capture governance

Content capture is controlled in two places, and both must agree before content reaches a destination:

1. **In the agent.** GenAI instrumentations record message content only when a setting enables it, for example `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT`. Under the latest experimental conventions (`OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`) the value selects where content goes rather than a simple on or off. Names and values differ between instrumentation libraries and versions; verify against the ones you pin. Content was observed in preview hosted-agent spans, so do not assume it is off.
2. **At the receiver.** `capture_content` is off by default. A route whose data must never hold content sets `content_capture_allowed=False`, and the policy then refuses to load with capture on. The Fabrikam Health pack does this.

Turning capture on for a route is a change with a named data owner's approval, a fixed end date, a destination classified for that content and a reader list that matches it. This mirrors the platform rule in the primary asset's [telemetry governance](../../production-hosted-agents-on-foundry/docs/authentication-and-governance.md#telemetry-governance).

## Identifier hashing and key management

The receiver replaces configured identifiers with `hmac-sha256:<truncated hex>` so analysts can still count and join on them without reading them.

- **Use a keyed HMAC, not a plain hash.** Identifiers such as patient numbers or order ids come from small spaces; an unkeyed hash can be reversed by hashing every candidate.
- **Keep the key in Key Vault** and give only the receiver's identity read access to it. The sample's `ReceiverPolicy` requires a non-empty key and the Fabrikam Health pack uses its own key, so values do not join across domains.
- **One key per route or data owner.** A shared key lets anyone with two destinations correlate users across them.
- **Rotation breaks correlation.** The same identifier hashes to a different value after rotation. Rotate on a schedule the data owner agrees, record the key version and the rotation time with the destination, and do not expect joins across the boundary.
- **Truncation is for readability, not secrecy.** The key provides the protection; a longer output only reduces collisions.

## Retention and residency

- **Per route, set by the data owner.** Each route has its own destination, retention period and region. The retention of the second copy follows the most sensitive data that route can hold, which is not necessarily the Application Insights retention.
- **Residency is decided by where the destination lives**, and by where the receiver runs, because the receiver holds batches in memory while it processes them. Run a receiver instance in each residency boundary rather than routing across regions.
- **Do not widen the footprint.** If content is captured, the second copy is a new store of conversation data. Include it in data subject requests, deletion and legal hold processes, or do not capture content.
- **Expire raw batches.** Blob destinations need a lifecycle policy; log destinations need a table retention setting. A sink with no expiry is a finding.

## Who can read the second copy

- **Readers are granted on the destination, per route,** with data-plane RBAC (for example Storage Blob Data Reader on one container, or table-level access in Log Analytics). Nobody reads through the receiver.
- **The receiver has no read endpoints.** No `/debug`, `/spans` or "last batch" route. A debug endpoint on a telemetry receiver is a data exfiltration path.
- **The receiver writes; it does not read.** Its identity holds a write role on the route's destination only, for example Storage Blob Data Contributor scoped to one container, or the Monitoring Metrics Publisher role on one data collection rule (verify the role your sink needs).
- **Review readers with the data owner** on the same cadence as the conversation store. Telemetry readers are often a wider group than application data readers; that gap is the risk this asset exists to close.

## Network and egress

- **Private by default.** Publish the receiver (or the Collector in front of it) on a private endpoint or an internal ingress, reachable from the agent's network only.
- **Allowlist the egress.** Hosted agent egress was observed open by default in preview; verify against current docs and close it. Add the receiver's hostname to the agent's egress allowlist explicitly, as described in the primary asset's [egress section](../../production-hosted-agents-on-foundry/docs/architecture.md#82-egress) and in the [Secured Foundry Landing Zone](../../../landing-zones-and-infra/secured-foundry-landing-zone/README.md).
- **TLS everywhere except loopback.** The sample's `--serve` mode binds to `127.0.0.1` only and speaks plain HTTP for local verification. Never expose it.

## Threat model

| Threat | Control | Residual risk |
|---|---|---|
| Anyone on the network writes fake spans | Entra token required; `Traces.Write` role with *Assignment required*; client allowlist | An admin assigning the role by mistake; review assignments |
| One agent writes into another team's route | Route comes from the token's client id; `trace_receiver.` namespace stripped and re-stamped | None if the `clients` map is reviewed |
| Shared header leaks | Shared header allowed in `dev` only; secret in a connection; constant-time compare | Developer environments that hold real data; keep them synthetic |
| Prompts or tool results stored in a broadly readable place | Content dropped by default; capture requires policy and approval; per-route readers | Content in an allowlisted key that is not a known content key; review new keys before allowlisting |
| Personal identifiers stored in clear | Keyed HMAC for configured keys; unknown keys dropped | A new identifier added under an allowlisted wildcard; avoid broad wildcards |
| Resource exhaustion through oversized or compressed bodies | Body, decompressed size, span, attribute, value and event bounds; `413` before reading oversized bodies | Many small valid requests; rate limit per client and scale out |
| Retry storms after an outage | `429` and `503` with `Retry-After`; exporters back off | Exporters that ignore `Retry-After`; their batches are dropped, not the receiver |
| Duplicate spans after retries | Sinks upsert on `(traceId, spanId)` | Sinks that append; dedupe at query time |
| Receiver used to read telemetry | No read endpoints; readers granted on the destination | Mis-scoped destination roles; audit them |
| Receiver identity over-privileged | Write role on one destination per route; key read on one vault | The receiver is a high-value target; protect its pipeline and revisions |
| Built-in tracing switched off to "save cost" | Dual export is a design rule; release gate checks both copies | A platform setting change outside the pipeline; alert on missing Application Insights data |

## Review checklist

Before a route goes live:

- [ ] The receiver app registration has an app ID URI, the `Traces.Write` app role and *Assignment required* set.
- [ ] Only approved hosted agent (or Collector) identities have the role, and each one appears in `clients` with an owner and a route.
- [ ] `tenant_ids` has exactly one tenant and `audiences` holds both the app ID URI and the client id.
- [ ] No shared-header authenticator is configured outside `dev`.
- [ ] The route's allowlist was reviewed against the classification table; there are no wildcards over identifier or content keys.
- [ ] `capture_content` is off, or there is a recorded approval with an end date and `content_capture_allowed` is true for that route only.
- [ ] The HMAC key is in Key Vault, unique to the route or data owner, with a rotation plan.
- [ ] The destination has a retention setting, sits in the required region and has a reviewed reader list.
- [ ] The receiver has no read or debug endpoints and its identity cannot read the destination.
- [ ] The agent's egress allowlist names the receiver; the receiver is not publicly reachable.
- [ ] A request with no token, an expired token, a wrong-audience token, an unlisted client and a missing role each fail with the expected code (see [Evaluation and troubleshooting](evaluation-and-troubleshooting.md)).
- [ ] Application Insights still receives the same traces (dual export confirmed).

## Related

- [Architecture](architecture.md)
- [Evaluation and troubleshooting](evaluation-and-troubleshooting.md)
- [ADR 0001: Dual export, never replace platform tracing](decisions/0001-dual-export-not-replace-platform-tracing.md)
- [ADR 0002: Redact at the receiver with an attribute allowlist](decisions/0002-redact-at-receiver-allowlist-attributes.md)
- [Production Hosted Agents on Foundry: authentication and governance](../../production-hosted-agents-on-foundry/docs/authentication-and-governance.md)
- [Hosted agent behind a streaming model gateway: authentication and governance](../../hosted-agent-streaming-model-gateway/docs/authentication-and-governance.md)
- [Request Context Is Not Authorization](../../request-context-is-not-authorization/README.md)
- [Agent Security and Governance](../../../security-and-governance/agent-security-and-governance/README.md)
- [Microsoft identity platform access tokens](https://learn.microsoft.com/entra/identity-platform/access-tokens)
- [Configure telemetry for hosted agents](https://learn.microsoft.com/azure/foundry/agents/how-to/configure-hosted-agent-telemetry)
- [OpenTelemetry semantic conventions for generative AI](https://opentelemetry.io/docs/specs/semconv/gen-ai/)

