# GitHub Copilot & AI Agent Authoring Instructions

When creating, updating, or reviewing assets in `microsoft/agenticai-design-patterns`, follow these mandatory repository conventions and [`docs/contribution-guide.md`](../docs/contribution-guide.md):

## 1. Two-Level Category Hierarchy (`assets/<category>/<asset-id>/`)

Never place an asset directly at `assets/<asset-id>/`. Every asset must reside inside one of the six category folders under `assets/`:
- `delivery-and-ops`
- `m365-and-copilot`
- `security-and-governance`
- `landing-zones-and-infra`
- `orchestration-and-workflows`
- `data-and-tools`

To scaffold a new asset from `templates/asset/`, run:
```bash
python tools/assets.py new --category <category> --id <asset-id> --kind <kind> --title "<Title>"
```

## 2. Domain-Agnostic Generalization ("Domain Packs")

- Never frame an asset around a single customer case study or engagement narrative.
- Separate the **invariant runtime/platform architecture** from the **Domain Pack** (entity vocabulary, tool schemas, and specialist instructions) and include a comparison table showing how the pattern applies across at least three domains (for example, `ITSM / SecOps`, `CRM / Enterprise Sales`, and `FinOps / HR / Operations`).

## 3. Production Engineering Depth

- Avoid high-level summaries. Include protocol-level Mermaid sequence diagrams, concrete JSON wire contracts/schemas, AADSTS/HTTP error classification matrices, failure-localization pipelines, and Architecture Decision Records (`docs/decisions/0001-*.md`).
- Every Python sample in `samples/*.py` must be runnable offline using standard library in-memory fakes (`python samples/<module>.py` or `--self-test`) while exposing clear production adapter classes. Delete any `__pycache__/` folders after testing.
- Every Bicep template in `infra/*.bicep` must compile with zero errors via `az bicep build --file <file> --stdout`.

## 4. Public-Safety Rules

- Zero customer names, internal project codenames, workshop participant names, internal Microsoft org acronyms, internal domains (`*.sharepoint.com`, `*.corp.microsoft.com`, `*.devtunnels.ms`, `*.microsofticm.com`), or real Entra tenant / subscription GUIDs.
- Use RFC 2606 / neutral documentation domains (`*.contoso.com`, `*.example.com`) and all-zero placeholder UUIDs (`00000000-0000-0000-0000-000000000001`).

## 5. Required Verification Commands

Before completing any change, run:
```bash
python tools/assets.py catalog
python tools/assets.py validate
python tools/assets.py catalog --check
```
