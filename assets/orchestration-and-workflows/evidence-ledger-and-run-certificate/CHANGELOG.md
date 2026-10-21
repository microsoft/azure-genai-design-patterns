# Changelog

All notable changes to this asset are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the asset uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-21

### Added

- Initial preview release of the evidence ledger and run certificate design pattern: an append-only, hash-chained ledger of digests and references for every agent step, a signed run certificate that pins the ledger head and binds the artifact, the required checks, the approval and the policy, offline verification, and a single definition of done.
- `README.md` with the core thesis, an architecture and implementation map, four Domain Packs, guidance on when to use and when not to use the pattern, a component diagram, key design decisions, a quickstart for the sample, related assets, a production-readiness pillar mapping, security and responsible AI notes, and limitations.
- `docs/architecture.md` covering the problem and forces, terminology, components, the ledger entry and certificate data model, canonical JSON and digests, chain verification, certificate issuance and withheld reasons, offline verification, a threat model, redaction and purge, concurrency and sealing, sequence diagrams, a decision matrix, illustrative wire contracts, the mapping onto Azure services, the relationship to sibling assets, Domain Packs, failure modes, anti-patterns, a production checklist and extension points.
- `docs/authentication-and-governance.md` covering the identity inventory, app roles and data-plane RBAC, separation of duties, key management and rotation, retention, legal hold and erasure, releasing ledger and certificate changes, the kill switch and incident response, audit, a threat model and the operating model.
- `docs/evaluation-and-troubleshooting.md` covering what to evaluate, the scenarios as evaluation cases, tamper drills, telemetry and dashboards, a reason-code troubleshooting guide, debugging a single run and release gates.
- ADR 0001: a hash chain plus a signed run certificate.
- ADR 0002: the ledger stores digests and references, not content.
- ADR 0003: a withheld certificate means the run is not done.
- `samples/evidence_ledger.py`, a single-file, stdlib-only ledger, issuer and offline verifier with `typing.Protocol` seams for the ledger store, evidence archive and signer, production adapter stubs for Azure Cosmos DB, Azure Blob Storage and Azure Key Vault, four Domain Packs, a demo run, a JSON output mode and a 20-scenario `--self-test`.
- `samples/README.md` describing how to run, read and extend the sample.
