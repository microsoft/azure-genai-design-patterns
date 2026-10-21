# ADR 0001: Never execute generated code in-process

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md#3-threat-model), [Asset README](../../README.md), [Authentication and governance](../authentication-and-governance.md#4-no-secrets-into-sessions), [Production Hosted Agents on Foundry](../../../production-hosted-agents-on-foundry/README.md)

## Context

Agents that analyse data, transform files, draw charts or do arithmetic increasingly write code and run it. The code is produced by a model from instructions, retrieved documents, uploaded files and tool results. Any of those inputs can carry a prompt injection, so the code must be treated as untrusted input from an unknown author, even when the user is trusted.

The quickest implementation is to pass the generated code to the language runtime the agent already runs in (an `exec` call, a REPL helper, or a subprocess in the same container). In that position the code inherits everything the agent has: its managed identity and the token endpoint that serves it, its network line of sight to private endpoints, environment variables and mounted secrets, the filesystem with other users' working files, and the process memory that holds conversation history and cached tokens. A single injected line can read a token and call a data-plane API as the agent, or post a customer file to an attacker's host.

Restricted interpreters and import blocklists have a long history of bypasses, and they also break the libraries that make code execution useful. A subprocess in the same container shares the identity endpoint, the network and the filesystem, so it narrows nothing that matters.

## Decision

1. Model-generated code is never executed in the agent's process, container, host, job or virtual network segment. This applies to prompt agents, hosted agents and any custom orchestration.
2. Execution happens in an isolated sandbox with its own boundary. The default is an Azure Container Apps dynamic session pool, which per current documentation isolates each session with Hyper-V; verify against current Microsoft Learn documentation. The Foundry Code Interpreter tool is an acceptable alternative for prompt agents when its curated environment is enough.
3. A single broker component, running under its own identity, is the only caller of the pool. The agent's identity has no role on the pool, and the sandbox receives no credentials, tokens or connection strings from the agent.
4. The broker refuses any request that asks for host-side execution, and code review rejects any code path that evaluates model output locally. The simulator encodes this as an explicit refusal (scenario 26) and asserts that the sandbox context exposes no agent credentials (scenario 29).
5. The broker's static pre-screen is a signal for audit and early rejection, not the isolation boundary. Isolation comes from the sandbox.

## Consequences

### Positive

- A successful injection is contained to one disposable session with no agent identity, no private network reach and egress disabled by default.
- Security review has one boundary to reason about, and one broker identity to audit.
- Users from different conversations cannot see each other's files or variables, because nothing is shared on the host.

### Trade-offs

- Each execution crosses a network boundary, and the first execution in a conversation can wait for a session to be allocated; design the user experience to tolerate it.
- The team operates a session pool, a broker identity and role assignments, or accepts the curated limits of the Foundry Code Interpreter tool.
- Files must move through the session file API instead of a shared disk, which adds upload and download steps and size limits.
- Local debugging needs the simulator or a development pool; there is no shortcut that runs the code on the developer's machine through the agent.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| In-process execution with a restricted interpreter or import blocklist | Bypasses are routine; inherits identity, network, secrets and memory; breaks common libraries |
| Subprocess in the agent container | Shares the identity endpoint, network, filesystem and environment; no real boundary |
| Self-hosted container or job per execution | A real boundary, but the team builds isolation, lifecycle, file transfer and cleanup; use only when the platform options cannot meet a hard requirement |
| Foundry Code Interpreter tool only | Good for prompt agents with curated needs; no control over packages or egress, so it cannot be the only option |
| Azure Container Apps dynamic session pool behind a broker | Chosen as the default: per-session isolation, egress off by default, explicit lifecycle, role-based access |
