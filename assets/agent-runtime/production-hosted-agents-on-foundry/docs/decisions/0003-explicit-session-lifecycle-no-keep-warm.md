# ADR 0003: Explicit session lifecycle, no keep-warm

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md#6-request-and-session-lifecycle), [Scaling and cost](../architecture.md#12-scaling-and-cost-behaviour), [Operational runbook](../evaluation-and-troubleshooting.md#operational-runbook), [Asset README](../../README.md)

## Context

Each hosted agent session runs in its own sandbox started from your image. The first turn in a new session pays a cold start. Behaviours observed while testing preview builds **(verify against current docs)**:

- A request that does not identify an existing session can create a new one, so naive retries leak sessions.
- Sessions that are never ended stay allocated until an idle timeout or expiry.
- There is no platform minimum-replica or keep-warm setting for sessions.
- Creating or activating a new agent version does not evict sandboxes already running for existing sessions.
- Session status can report ACTIVE before the server in the container accepts requests.

Teams that see cold starts in a demo often add scheduled "keep-warm" pings. Those pings keep sandboxes allocated and billed without users, hide the real first-turn experience from telemetry, and keep old versions alive after a rollout, including after a security fix.

## Decision

1. **Pin.** Every session is created pinned to an explicit agent version, and every call carries the session (or the protocol's conversation identifier). No unpinned retries.
2. **Ready means a real turn.** After the status reports ACTIVE, the client or pipeline sends a minimal turn with bounded retry before routing user traffic.
3. **Heartbeat only while a user is active.** A lightweight heartbeat on the same session keeps it alive while a human is in the conversation. Heartbeats stop when the user leaves.
4. **End explicitly.** Clients end the session when the conversation ends. Event-driven callers end the session immediately after the turn.
5. **Sweep orphans.** A scheduled sweeper inside the network lists sessions and ends those idle beyond policy. It is a dry run by default, requires an explicit apply flag, and honours a keep list.
6. **Detect keep-warm.** The sweeper flags sessions whose recent activity is only heartbeats with no user turns.
7. **Drain on rollout.** New sessions pin to the new version; sessions on the previous version are drained per policy.
8. **State outside the sandbox.** Conversation state is rehydrated from Cosmos DB on a new session, so ending or losing a session never loses data.

## Consequences

### Positive

- Session counts track real usage, which makes cost predictable and leaks visible.
- Cold-start telemetry is honest, so optimization targets real causes (image size, imports, credential and token setup).
- Rollouts and security fixes reach users after a defined drain, not whenever pings stop.

### Trade-offs

- Users starting a new conversation see the cold start. Mitigate within the container, and with UX (progress indication), not with synthetic traffic.
- Clients must implement pin, heartbeat, end and rehydrate correctly.
- The sweeper and its keep list are another operational component.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Scheduled keep-warm pings | Cost without users; hides cold starts; prolongs old versions |
| Rely on idle timeout only, never end sessions | Sessions linger; counts and cost drift; leaks go unnoticed |
| One session per request for conversational agents | Cold start on every turn; loses in-sandbox caches |
| One long-lived session shared by many users | Mixes user state in one sandbox; breaks isolation |
| Wait for a platform keep-warm feature | Not available at the time of writing; revisit if it ships, with an explicit cost decision |
