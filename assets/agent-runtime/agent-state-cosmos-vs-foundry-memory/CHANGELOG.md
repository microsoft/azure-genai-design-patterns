# Changelog

All notable changes to this asset are documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this asset uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-21

### Added

- Initial preview release of the **Agent State and Memory: Private Cosmos DB vs Foundry Memory** design pattern.
- `README.md` with the core thesis, implementation map, Domain Packs, an architecture diagram, key design decisions, getting started, a file map and related assets.
- `manifest.yaml` with the asset metadata, pillars, Azure services, tags and related assets.
- `docs/architecture.md`: the three kinds of agent state and why conflating them fails, invariants, the `StateStore` / `MemoryStore` / `ContextProvider` interfaces, a decision matrix (private Cosmos DB, Foundry Memory, self-hosted vector store, local SQLite or file), the Cosmos DB data model with hierarchical partition keys, ETag concurrency and time to live, the Foundry Memory model with preview labels, a Mermaid turn-lifecycle sequence, wire contracts, resume and idempotency semantics, erasure across both stores, Domain Packs, failure modes, anti-patterns and a production checklist.
- `docs/authentication-and-governance.md`: managed identity and data-plane RBAC, private endpoints, Foundry project identity, scope from verified identity only, consent and sensitive categories, retention, the erasure workflow with audit, encryption and logging without memory contents.
- `docs/evaluation-and-troubleshooting.md`: evaluation dimensions, a test matrix mapped to the reference suite, memory-quality checks, CI gating and a troubleshooting table.
- ADR 0001 (separate conversation state from long-term memory) and ADR 0002 (scope memory to the verified user and tenant).
- `samples/agent_state_store.py`: a standard-library reference with `StateStore`, `MemoryStore` and `ContextProvider` protocols, in-memory and SQLite state stores, an in-memory memory store, a memory write policy, an asynchronous memory writer, a reflector, an erasure coordinator, documented Cosmos DB, Foundry Memory and vector-store stubs, 16 self-test scenarios and three domain packs.
