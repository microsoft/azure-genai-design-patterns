# ADR 0002: Retry and fail over only before the first byte

- **Status:** Accepted
- **Date:** 2026-10-21
- **Related documentation:** [Architecture: Retries only before the first byte](../architecture.md#retries-only-before-the-first-byte), [Architecture: Failover before the first byte versus failure mid-stream](../architecture.md#failover-before-the-first-byte-versus-failure-mid-stream), [Asset README](../../README.md), [Evaluation and troubleshooting](../evaluation-and-troubleshooting.md#double-billing-on-retry)

## Context

The gateway can route an alias to more than one backend. When a backend fails, retrying on another backend hides the failure from the agent. Whether that is safe depends on one thing: has the agent already received any of the answer?

- **Before the first data frame,** the agent has seen nothing but, at most, response headers or keepalive comments. A retry is invisible. The only cost is the work the failed backend may have done.
- **After the first data frame,** the agent has part of an answer. A second backend generates a new answer from the start; it does not continue the first one. Relaying it produces spliced or duplicated text, tool-call arguments that are not valid JSON, and two bills for one turn. Only the agent knows its conversation state and whether its tools have side effects, so only the agent can decide to retry the whole turn.

Two more facts shape the decision:

- A backend that accepts the connection and then waits before its first token is a failure the gateway can only detect with a deadline. Without one, a slow backend holds the request until an idle timeout cuts it.
- Agent SDKs and runtimes may retry on their own. The gateway's rule has to hold even when a client retries a request the gateway has already served.

The offline simulator includes an anti-pattern scenario, `anti_pattern_retry_after_first_byte_shows_harm`, that shows the duplicated output and double usage of a gateway that retries after the first byte.

## Decision

1. **The gateway retries and fails over only before it relays the first data frame** for a request. Connect errors, timeouts, `408`, `429` and `5xx` from a backend before that point move the request to the next backend in the route, up to the configured number of attempts.
2. **Client errors are not retried.** `400` and `401` from a backend are configuration or request problems; another backend will not fix them.
3. **A first-byte deadline applies to every attempt.** When it expires, the gateway cancels the attempt and fails over. The deadline, the number of attempts and the keepalive interval are checked against the ingress idle timeout at startup, so failover cannot outlast the connection.
4. **After the first data frame, a failure ends the stream** with an SSE error frame (`upstream_failed_mid_stream`, `retryable: false`) and no `[DONE]`. The gateway does not retry, and `retryable: false` tells clients not to resend the request automatically.
5. **Agents send an `Idempotency-Key`** per logical model call. The gateway rejects a repeat of a key it has already completed with `409 duplicate_request`, so an automatic client retry cannot bill twice.
6. **The decision to retry a turn belongs to the agent.** On an error marked `retryable: true`, the agent may resend with a new idempotency key and backoff. On a mid-stream error frame (`retryable: false`), the agent discards the partial answer and either re-runs the turn as a deliberate decision, with a new key and knowing whether any tool calls already ran, or reports the failure.

## Consequences

### Positive

- Agents never see duplicated, spliced or corrupted output from the gateway.
- One logical call is billed once, even when clients retry.
- Backend failures before the answer starts, which are the common case, are invisible to agents.
- The rule is simple to test offline and to explain to agent developers.

### Trade-offs

- A backend that fails mid-answer surfaces to the agent as an error, so agents must handle error frames and partial answers.
- In hold-headers mode, failover time adds to the time before the first token. In early-commit mode, keepalive comments cover it, but errors after commit must be SSE frames rather than JSON responses.
- A failed attempt may still be billed by its backend for prompt processing. The usage event records every attempt so this is visible.
- Idempotency needs a store. The reference store is in memory and single-replica; production needs a shared store.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Retry at any point, replaying from the start | Duplicates text the agent already has, double-bills, corrupts tool-call arguments |
| Retry mid-stream and skip the characters already sent | A second model generates a different answer; skipping by length splices two unrelated answers |
| Never retry in the gateway | Every transient backend failure reaches every agent; failover across deployments would need logic in each agent |
| Buffer the whole answer, then retry freely | Removes streaming, which is the reason for the pattern, and makes long answers hit idle timeouts |
| Rely on the agent SDK's built-in retry | SDK retries do not know whether output was delivered, and they do not fail over across backends |
