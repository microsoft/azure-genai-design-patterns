# Changelog

All notable changes to this asset are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this asset
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - 2026-10-21

### Added

- `README.md` with the core thesis (entitlement decides which tools a caller may ever see; progressive
  loading decides when an eligible tool is shown), when to use, when not to use, the architecture
  diagram, getting started, Domain Packs and the production-readiness pillar mapping.
- `docs/architecture.md` with problem and forces, scope against sibling assets, components, disclosure
  tiers, the loading-trigger decision matrix, context budgeting and eviction, the `SKILL.md` folder
  convention, MCP alignment (`tools/list`, `listChanged`, `notifications/tools/list_changed`),
  bounded-autonomy and evidence-ledger interaction, telemetry, Azure mapping, wire contracts, Domain
  Packs, failure modes, anti-patterns and a production checklist.
- `docs/authentication-and-governance.md` covering the separation of loading from authorization,
  identity flow to the catalog, skill registry governance, ledger events and responsible AI.
- `docs/evaluation-and-troubleshooting.md` covering the scenario matrix, loaded-versus-used metrics,
  misses, wrong-tool calls on labeled tasks, red-team prompts, release gates and troubleshooting.
- ADR 0001: progressive loading is not authorization.
- ADR 0002: phase-driven loading with a meta-tool fallback.
- `samples/progressive_loader.py`, a standard-library-only reference loader with four Domain Packs
  (retail customer service, manufacturing maintenance, healthcare intake, financial services
  operations), a `CatalogSource` protocol with an in-memory fake and an MCP gateway adapter stub, a
  `SkillSearch` protocol with a keyword fake and an Azure AI Search adapter stub, and a `--self-test`
  that runs twelve scenarios per pack plus a seam check.
- `samples/README.md` describing how to run the sample and what each scenario proves.
