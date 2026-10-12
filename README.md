# Agentic AI Design Patterns

Field-proven design patterns, reference architectures and solution accelerators for building agentic AI systems on Azure and Microsoft Foundry.

Every asset in this repository is reviewed by a curator before it is merged, follows the same folder layout, and carries a machine-readable `manifest.yaml`, so you can browse it here or discover it through search tools.

## Catalog

<!-- catalog:start -->
| Asset | Kind | Maturity | Summary |
|-------|------|----------|---------|
| [Agent Path to Production](assets/agent-path-to-production/README.md) | `playbook` | experimental | An evidence-led delivery playbook for taking an agentic application from early design or a pilot to an operable production workload, with specialist routing, readiness gates, and reusable learning. |
<!-- catalog:end -->

The catalog above is generated from each asset's `manifest.yaml` by `python tools/assets.py catalog`. Do not edit it by hand.

## Asset kinds

| Kind | What it is |
|------|------------|
| `design-pattern` | A reusable approach to a recurring agentic design problem, with trade-offs and when (not) to use it. |
| `reference-architecture` | An end-to-end architecture for a scenario, with components, data flows, identity, security and operations. |
| `solution-accelerator` | Deployable code and infrastructure that implements a pattern or architecture. |
| `playbook` | A step-by-step delivery or adoption guide. |
| `sample` | A small, focused code sample that demonstrates one technique. |

## Repository layout

```
assets/<asset-id>/        one folder per asset; the folder name is the asset id
  manifest.yaml           metadata: kind, summary, tags, owners, version, maturity
  README.md               overview, when to use, architecture, getting started
  CHANGELOG.md            what changed in each version
  docs/                   architecture details, decisions (ADRs), diagrams
  infra/                  infrastructure as code (optional)
  src/                    source code (optional)
  samples/  tests/        optional
templates/asset/          starting point for a new asset
schema/                   JSON schema for manifest.yaml
tools/                    validation and catalog generation
```

Assets live in one flat `assets/` folder; the kind is a manifest field, not a folder. Assets therefore never move and their links stay stable.

## Using an asset

Start with the asset's `README.md`. Link to a specific version with a tag or commit permalink rather than `main`, because assets evolve.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). In short: copy `templates/asset/`, fill in the manifest and README, run `python tools/assets.py validate`, and open a pull request. A curator reviews every contribution.

Everything in this repository is public. Do not commit customer names, customer data, internal links, unreleased product information or secrets.

## Earlier content

The 2023–2024 content of this repository (foundational concepts, the original design patterns, reference architecture and accelerators) is preserved at commit [`a4c1ee8`](https://github.com/microsoft/agenticai-design-patterns/tree/a4c1ee891c93a2fb2995dbd10f9ced73795d6f64).

## Trademarks

This project may contain trademarks or logos for projects, products, or services. Authorized use of Microsoft trademarks or logos is subject to and must follow [Microsoft's Trademark & Brand Guidelines](https://www.microsoft.com/en-us/legal/intellectualproperty/trademarks/usage/general). Use of Microsoft trademarks or logos in modified versions of this project must not cause confusion or imply Microsoft sponsorship. Any use of third-party trademarks or logos is subject to those third parties' policies.
