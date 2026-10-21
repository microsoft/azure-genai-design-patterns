## Asset

- Category: `assets/<category>` (`delivery-and-ops` | `m365-and-copilot` | `security-and-governance` | `landing-zones-and-infra` | `agent-runtime` | `ai-gateway` | `orchestration-and-workflows` | `data-and-tools`)
- Asset path: `assets/<category>/<asset-id>`
- Kind: `design-pattern` | `reference-architecture` | `solution-accelerator` | `playbook` | `sample`
- Pillars: (from `manifest.yaml`)
- New asset or update:

## Summary

What this asset is, the recurring architectural problem it solves, and how it is generalized across domains.

## Checklist

- [ ] Placed in `assets/<category>/<asset-id>/` with `manifest.yaml`, `README.md`, and `CHANGELOG.md` (scaffolded with `python scripts/assets.py new`).
- [ ] Generalized as a domain-agnostic pattern (for example, a domain table covering several industries, not a single-engagement summary) following [`docs/contribution-guide.md`](../docs/contribution-guide.md).
- [ ] Meets the depth bar for its `kind`: architecture diagrams, wire contracts or schemas, identity and governance flow, evaluation or troubleshooting guidance, and ADRs where decisions were made.
- [ ] `pillars:` in `manifest.yaml` lists the path-to-production pillars the asset helps with.
- [ ] Python modules in `samples/` pass offline self-tests (`python ... --self-test`), and Bicep templates in `infra/` pass `az bicep build`.
- [ ] `python scripts/assets.py validate` and `python scripts/assets.py scan` pass with 0 errors, and `python scripts/assets.py catalog` has updated the root `README.md` and `assets/<category>/README.md`.
- [ ] No customer names or data, participant names, internal links, internal acronyms, unreleased product information, or real tenant or subscription GUIDs (see [CONTRIBUTING.md](../CONTRIBUTING.md#public-safety-rules)).
- [ ] For updates: `version` bumped, `CHANGELOG.md` entry added, `last_reviewed` updated.
