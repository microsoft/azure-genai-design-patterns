# ADR 0003: Collector first; the lightweight receiver as a reference and for narrow routes

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [README: Decision matrix](../../README.md#decision-matrix), [README: When not to use](../../README.md#when-not-to-use), [Architecture: Anti-patterns](../architecture.md#anti-patterns), [Evaluation and troubleshooting: Test matrix](../evaluation-and-troubleshooting.md#test-matrix), [Samples: What the scenarios cover](../../samples/README.md#what-the-scenarios-cover)

## Context

Once a team decides to export a second copy of its hosted agent traces ([ADR 0001](0001-dual-export-not-replace-platform-tracing.md)) and to redact that copy by allowlist ([ADR 0002](0002-redact-at-receiver-allowlist-attributes.md)), it still has to choose what terminates the OTLP connection.

**The OpenTelemetry Collector already does most of the work.** At the time of writing, its core and contrib distributions include:

- an `otlp` receiver for OTLP/HTTP (protobuf and JSON) and gRPC;
- batching, memory limiting, and a sending queue with retry on each exporter;
- processors that change attributes, including a `redaction` processor with an allowlist that fails closed, masking of blocked values, and keyed hash functions (`hmac-sha256`, `hmac-sha512`) for selected values;
- `filter` and `transform` processors, and a routing connector that sends data to different pipelines;
- server authenticator extensions, including an OIDC authenticator that validates the issuer and audience of a bearer token and exposes its claims to processors;
- exporters for many backends, including Azure Monitor and blob-style object stores.

Component names, options and stability levels change between Collector releases, and several of the components above are in the contrib distribution at alpha or beta stability. Verify each one against the release you deploy.

**A custom receiver means owning protocol code.** The sample in this asset decodes OTLP/JSON only. A production receiver also needs:

- a protobuf decoder, because most SDK exporters send protobuf by default;
- durable, idempotent sinks;
- back-pressure that holds across replicas;
- patching, monitoring, and security review of the receiver itself.

Most of that already exists in the Collector.

**Some rules are still easier to express in code.** Examples are routing from a validated client id to a per-route policy, refusing to load a policy that would enable content capture on a regulated route, and a per-route keyed hash. A Collector can often express them through configuration. A small, tested reference makes it easy to check that it does.

## Decision

1. **The OpenTelemetry Collector is the production default** for the second copy. Agents export to a Collector the team owns. The Collector fans out to the team's destinations, or forwards a subset to a custom receiver.
2. **The Collector configuration implements the same rules as the sample.**
   - Authenticate the exporter with an Entra token: issuer, audience, tenant, client id allowlist and app role ([claims the receiver must check](../authentication-and-governance.md#claims-the-receiver-must-check)).
   - Derive the route from the validated token, not from caller-supplied attributes. Upsert the route attribute from the authenticated claims so that it overwrites any incoming value.
   - Allowlist span, event and resource attributes. Check which attribute scopes each processor covers; resource attributes may need a separate processor.
   - Leave content attributes off the allowlist.
   - Use a keyed hash for identifiers, with the key held in Key Vault and injected at runtime, never in the configuration file.
   - Drop status and exception messages; keep codes and types.
   - Reject logs and metrics on a traces-only route.
   Where the Collector cannot check a claim natively (for example, an app role or an `azp` allowlist), add a filter on the exposed claims, or terminate authentication at an ingress that can. Verify for your Collector version.
3. **The sample's scenarios are the Collector's acceptance tests.** Write one test per scenario in the [test matrix](../evaluation-and-troubleshooting.md#test-matrix) against the Collector configuration, and run them in CI on every configuration or version change. Where the Collector behaves differently by design, record the difference next to the test. One example is a whole request rejected rather than a partial success for one malformed span.
4. **The lightweight receiver has three roles.**
   - **Local development and verification.** Run `--serve` on loopback to see exactly what an agent exports before a Collector or backend is involved.
   - **Reference and specification.** It is the executable description of what a Collector processor, or a custom processor, must do.
   - **A narrow custom route, after review.** Use it only when the Collector cannot express a rule. Run it directly, or behind a Collector that forwards only that route's subset.
5. **A custom receiver in production meets the same bar as a Collector.** That means:
   - a protobuf decoder;
   - durable, idempotent sinks;
   - back-pressure with `Retry-After`;
   - rate limits and state that hold across replicas (the sample's limiter is per process);
   - a managed identity for its own sink access;
   - private ingress;
   - patching and vulnerability scanning;
   - monitoring of its own status codes and redaction reports ([observability of the receiver itself](../architecture.md#observability-of-the-receiver-itself)).

## Consequences

### Positive

- Protocol coverage, queueing, retry and exporters come from a widely used project rather than team-owned code.
- The redaction and routing rules are written once as tests and checked against whichever component enforces them.
- Teams can start with the sample on loopback, move to a Collector, and keep the same acceptance tests.
- A custom route stays small, because the Collector handles everything except the rule that needed code.

### Trade-offs

- Collector configuration is code. It needs review, tests and version pinning, and a misordered processor list can silently undo a rule.
- Component stability varies. A beta or alpha processor can change its options between releases, so upgrades need the acceptance tests.
- Claims-based routing and claim checks beyond issuer and audience need extra configuration, and may differ between Collector versions.
- The team runs, patches and scales the Collector. A custom receiver behind it adds a second component to operate.
- The sample and the Collector configuration can drift apart unless the same scenario list runs against both.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Custom receiver for every route | The team owns protobuf decoding, queueing, retry and every exporter; most of that duplicates the Collector |
| Collector with no acceptance tests | Redaction and routing rules fail silently; a configuration slip stores content or misroutes spans with no error |
| Export from the agent straight to a vendor backend | No central place for auth, redaction or routing; each agent needs the vendor's credentials and its own correct configuration |
| Built-in tracing only | The right answer when it meets the need ([README: When not to use](../../README.md#when-not-to-use)); not enough when a route needs residency, retention or a destination the platform does not offer |
| Receiver logic in a serverless function | Same protocol and back-pressure ownership as a custom receiver, plus host-specific limits on request size and duration to verify |
| A custom Collector processor instead of a separate receiver | A valid option when the team can build and ship a custom Collector distribution; the sample still serves as its specification and test list |
