## Asset

- Category: `assets/<category>` (`delivery-and-ops` | `m365-and-copilot` | `security-and-governance` | `landing-zones-and-infra` | `orchestration-and-workflows` | `data-and-tools`)
- Asset path: `assets/<category>/<asset-id>`
- Kind: `design-pattern` | `reference-architecture` | `solution-accelerator` | `playbook` | `sample`
- New asset or update:

## Summary

What this asset is, the recurring architectural problem it solves, and how it is generalized across domains.

## Checklist

- [ ] Placed in the appropriate category folder `assets/<category>/<asset-id>/` with `manifest.yaml`, `README.md`, and `CHANGELOG.md`.
- [ ] Generalized as a domain-agnostic pattern (e.g., Domain Pack table across multiple industries, not a single-customer engagement summary) following [`docs/contribution-guide.md`](../docs/contribution-guide.md).
- [ ] Meets the depth bar for its `kind`: architecture diagrams, wire contracts/schemas, identity/governance flow, evaluation/troubleshooting guide, and ADR(s).
- [ ] All Python reference modules in `samples/` pass offline self-tests (`python ... --self-test`), and all Bicep templates in `infra/` pass `az bicep build`.
- [ ] `python tools/assets.py validate` passes with 0 errors (including relative Markdown link checks) and `python tools/assets.py catalog` has updated both the root `README.md` and `assets/<category>/README.md`.
- [ ] Zero customer names or data, workshop participant names, internal links (`*.sharepoint.com`, `*.corp.microsoft.com`, `*.devtunnels.ms`), internal org acronyms, unreleased product info, or real tenant/subscription GUIDs (see [CONTRIBUTING.md](../CONTRIBUTING.md#public-safety-rules)).
- [ ] For updates: `version` bumped, `CHANGELOG.md` entry added, `last_reviewed` updated.
