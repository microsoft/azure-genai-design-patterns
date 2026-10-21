# ADR 0003: Built-in pool for MCP, custom-container pool for packages

- **Status:** Accepted
- **Date:** 2026-10-20
- **Related documentation:** [Architecture](../architecture.md#10-pool-selection-and-the-mcp-gap), [Decision matrix](../architecture.md#7-decision-matrix), [Asset README](../../README.md), [Multi-tenant MCP gateway](../../../../ai-gateway/multi-tenant-mcp-gateway/README.md)

## Context

Dynamic session pools come in two broad kinds. A built-in (managed) pool runs a platform-curated Python environment, referred to here as PythonLTS; a custom-container pool runs an image the team builds. The names, available languages and api-versions differ by region and change during preview, so verify against current Microsoft Learn documentation and the resource provider.

The two kinds behaved differently in preview testing **(observed in preview; verify current behaviour)**:

- The built-in pool exposed an MCP endpoint that a prompt agent or hosted agent could attach as a tool. On a custom-container pool, the deployment accepted the MCP setting and did not apply it; reading the pool back showed no endpoint.
- The built-in pool's curated packages could not be extended at run time, because egress is disabled and installation needs a package index.
- The warm or ready-instance setting was not honoured on the managed pool. Custom pools rejected zero ready instances, and for them the maximum number of concurrent sessions mattered more than warmth.
- Documentation pages listed pool types that a given region or api-version did not accept; the resource provider's own answer was authoritative.

## Decision

1. Choose the pool by need, in the broker, before any session exists. The simulator's pool selector encodes the rules below.
2. If the agent needs MCP access to the sandbox and the curated packages are enough, use the built-in pool (scenario 22). Treat the endpoint credential as a secret referenced from Key Vault, and have the application pin the per-conversation identifier; the model never chooses it.
3. If the workload needs packages, system libraries or binaries outside the curated set, use a custom-container pool with those dependencies baked into the image, deployed by digest (scenario 21). A request for MCP on that pool fails with an explicit error rather than silently falling back.
4. If a workload needs both custom packages and an MCP surface, use the custom pool behind the team's own tool front (an MCP server or an OpenAPI tool in the broker, optionally fronted by a gateway). Do not wait for the platform setting (scenario 23).
5. Read the pool configuration back after every deployment and fail the release if MCP, egress or capacity settings differ from what was requested. Do not rely on the warm setting on managed pools, and size custom pools by maximum sessions (scenario 24).
6. Discover supported pool types from the resource provider in the pipeline; never create pools on demand from the request path (scenario 25).

## Consequences

### Positive

- MCP-dependent agents get the platform endpoint without extra components.
- Package needs are met reproducibly from a scanned image instead of run-time installs through an open network.
- Silent configuration drift is caught at release time instead of in production.

### Trade-offs

- Two pool kinds may run side by side, each with its own identity, role assignments and monitoring.
- Custom pools add an image pipeline (build, scan, push by digest, pull with the pool identity) and a running cost for at least one ready instance.
- Workloads that need both packages and MCP carry a broker-side tool front until the platform supports MCP on custom pools.
- These findings are preview observations; revisit this ADR when the documented behaviour changes.

## Alternatives considered

| Alternative | Why not chosen |
|---|---|
| Always use a custom-container pool | Loses the platform MCP endpoint; adds image operations for workloads the curated set covers |
| Always use the built-in pool | Cannot add packages with egress disabled; pushes teams to open egress |
| Install packages at run time with an allowlist to a package index | Opens a supply-chain and exfiltration channel; slow and non-reproducible |
| Foundry Code Interpreter tool only | Simple for prompt agents, but packages and egress are platform-controlled and there is no custom image option |
| Rely on the MCP setting on custom pools as documented | Observed to be accepted and not applied in preview; a silent failure is worse than an explicit one |
