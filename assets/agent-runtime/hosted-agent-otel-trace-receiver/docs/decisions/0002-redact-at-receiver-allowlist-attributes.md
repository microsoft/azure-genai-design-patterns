# ADR 0002: Redact at the receiver with an attribute allowlist

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture: Redaction design](../architecture.md#redaction-design), [Architecture: Redaction report](../architecture.md#redaction-report), [Authentication and governance: Telemetry data classification](../authentication-and-governance.md#telemetry-data-classification), [Authentication and governance: Content capture governance](../authentication-and-governance.md#content-capture-governance), [Evaluation and troubleshooting: Prove content stays out](../evaluation-and-troubleshooting.md#prove-content-stays-out)

## Context

GenAI spans can carry far more than operational data:

- prompts, completions and system instructions (`gen_ai.input.messages`, `gen_ai.output.messages`, `gen_ai.system_instructions`);
- tool definitions, arguments and results;
- end-user identifiers, conversation ids, and domain identifiers that frameworks or application code add;
- exception messages and status descriptions that quote inputs, file paths or connection details.

Three facts make "just configure the agent correctly" insufficient on its own:

- **Content capture is controlled in the agent by instrumentation settings** whose names and values differ between libraries and versions. They are in Development and change between releases. Content was observed in preview hosted-agent spans, so it cannot be assumed off.
- **Attribute names change.** The OpenTelemetry GenAI semantic conventions are in Development. For example, `gen_ai.system` was replaced by `gen_ai.provider.name`. Agent frameworks also add their own attributes. A list of "known sensitive keys" goes stale with every upgrade.
- **The second copy may land in a less-governed store** than Application Insights: a blob archive, an analytics workspace or an evaluation pipeline with a wider reader group.

Every span in the second copy crosses the receiver (or the Collector that implements the same rules) before it is stored. That is the one place where one policy can apply to every agent, whatever its framework or configuration.

## Decision

1. **Store only allowlisted keys.** Each route's policy has a `span_allowlist` (also applied to event attributes) and a `resource_allowlist`. Any key not on the list is dropped and named in the redaction report. A narrow, closed prefix such as `telemetry.sdk.*` is acceptable; a wildcard over an open namespace such as `gen_ai.*` is not, because new content attributes appear there.
2. **Content capture is off by default.** Keys in `content_attributes` are dropped unless the route sets `capture_content`. A route that must never hold content sets `content_capture_allowed: false`, and the policy then refuses to load if capture is turned on. Turning capture on needs a named data owner, an end date, a destination classified for that content and a matching reader list.
3. **Hash identifiers with a keyed HMAC; do not store them raw.** Keys in `hashed_attributes` are replaced with `hmac-sha256:` and a truncated digest, so analysts can still group and join. The key comes from Key Vault, is readable only by the receiver's identity, and is separate per route or data owner. A plain hash is not allowed: small identifier spaces can be reversed by hashing every candidate.
4. **Drop free text; keep its shape.** Status messages and `exception.message` are dropped. The status code, `error.type` and `exception.type` are kept.
5. **Drop what cannot be bounded.** `kvlistValue` and `bytesValue` values are dropped, arrays must hold scalars and are bounded, strings are truncated, and attribute and event counts are capped ([payload bounds](../architecture.md#payload-bounds)).
6. **The receiver owns its namespace.** Incoming `trace_receiver.*` attributes are stripped before redaction; the receiver then stamps the route, client id, authentication method and policy name from the validated token.
7. **Report keys and counts, never values.** Each batch produces a redaction report with counts per reason and the names of dropped keys. It is logged with the receiver's operational logs and reviewed after every SDK or instrumentation upgrade.
8. **The agent stays the first layer.** Content capture stays off in the agent as well. The receiver is defense in depth, not the only control, and it does nothing for the built-in copy in Application Insights.

## Consequences

### Positive

- Attributes nobody has reviewed never reach the destination, however the agent or its framework changes.
- One policy per route applies to every agent that exports to it, and a regulated route cannot be switched to capture content by a configuration slip.
- Hashed identifiers still support per-user grouping, cost attribution and joins within a route.
- The redaction report makes drift visible: rising `contentDropped` shows capture was turned on upstream, and new `droppedKeys` show renamed or new attributes.
- A Collector can implement the same rules, and the sample's scenarios become its acceptance tests ([ADR 0003](0003-collector-first-receiver-as-reference.md)).

### Trade-offs

- Useful new attributes are dropped until someone reviews and allowlists them, so dashboards can show empty columns after an upgrade.
- The allowlist needs maintenance as the semantic conventions move.
- Hashes do not join across routes or across a key rotation. Rotation needs to be recorded with the destination.
- Dropped exception messages make debugging from the second copy harder. Use the built-in copy, under its own governance, for that.
- The receiver holds unredacted spans in memory while it processes them, so it is in scope for data handling and must run inside the route's residency boundary.
- Redaction at the receiver does not reduce what the agent sends over the network.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Denylist known content keys | Renamed and new keys pass through until someone notices; the failure is silent and the data is already stored |
| Redact only in the agent | Settings differ by instrumentation and version; one misconfigured agent leaks, and there is no central evidence of what was dropped |
| Redact in the destination after storage | The content is already stored and may already have been read; deleting it reliably is harder than never writing it |
| Plain SHA-256 of identifiers | Reversible for small identifier spaces such as order or patient numbers |
| Drop all identifiers | Loses per-user grouping and cost attribution. Still available per route: leave the key off both the allowlist and the hashed list. |
| Encrypt content instead of dropping it | Still a second copy of conversation data, plus key management and access paths to govern; out of scope for this pattern |
