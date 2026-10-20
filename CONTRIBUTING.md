# Contributing

Thank you for contributing a pattern, architecture or accelerator. This guide explains what we accept, how to structure it, and how review works.

## What belongs here

Assets that are reusable beyond a single engagement: a design pattern, a reference architecture, a solution accelerator, a delivery playbook or a focused sample. Short tips and one-off notes are better shared elsewhere.

## Public-safety rules

This repository is public. Before you open a pull request, make sure your contribution contains **none** of the following:

- Customer names, logos, identifiable customer scenarios or customer data, unless the customer has given written permission for a public reference.
- Internal links (internal SharePoint sites, internal wikis, internal-only `aka.ms` links), internal document names or internal-only classifications.
- Unreleased product features, roadmap commitments, pricing or commercial terms.
- Secrets of any kind: keys, connection strings, tokens, SAS URLs, certificates, real tenant or subscription IDs.
- Personal data. Use synthetic data in samples and screenshots.
- Third-party code or content whose license is not compatible with the MIT License.

The validation workflow scans for common mistakes, but it cannot catch everything. You and the curator are responsible for what is published.

## Adding an asset

Before authoring, read the **[Contribution & Pattern Standardization Guide](docs/contribution-guide.md)** for the full generalization methodology, depth requirements by `kind`, and offline self-test standards.

1. **Choose the category (`<category>`)** under [`assets/`](assets/README.md):
   - `delivery-and-ops` — Delivery playbooks, readiness gates, evaluation frameworks, and AgentOps
   - `m365-and-copilot` — Microsoft 365 Copilot, Teams, Declarative Agents, Custom Engine Agents, MCP plug-ins, and Cowork
   - `security-and-governance` — Entra Agent ID, tool governance, guardrails, Purview, and Sentinel observability
   - `landing-zones-and-infra` — Private Microsoft Foundry landing zones, Bicep/Terraform IaC, and CI/CD pipelines
   - `orchestration-and-workflows` — Multi-agent routers, planner-specialist handoffs, durable state/caching
   - `data-and-tools` — Governed MCP servers, OBO data connectors, RAG grounding, and action safety gates
2. **Pick a stable `id`:** lowercase words separated by hyphens, for example `human-approval-gate`.
3. **Scaffold the asset from `templates/asset/`:**

   ```bash
   pip install -r tools/requirements.txt
   python tools/assets.py new --category <category> --id <asset-id> --kind <kind> --title "<Title>"
   ```

4. **Generalize and author the content:**
   - Frame the pattern as **domain-agnostic** (demonstrating applicability across at least three domains such as ITSM/SecOps, CRM/Sales, and FinOps/HR/Ops via Domain Packs rather than a single customer case study).
   - Complete `manifest.yaml` ([`schema/manifest.schema.json`](schema/manifest.schema.json)) and `README.md`.
   - Author deep architecture, identity/authentication, and evaluation/troubleshooting guides in `docs/`, plus at least one ADR in `docs/decisions/`.
   - Include runnable, zero-dependency reference code with an offline `--self-test` mode in `samples/` and/or compilable Bicep/Terraform templates in `infra/`. Delete unused template folders (`infra/` or `src/` or `samples/`).
5. **Validate and regenerate the catalogs:**

   ```bash
   python tools/assets.py catalog
   python tools/assets.py validate
   python tools/assets.py catalog --check
   ```

6. **Open a pull request** using the pull request template. One asset per pull request.

### Files and sizes

- Keep each file under 10 MB. Publish large binaries (videos, datasets, packaged builds) as release attachments and link to them.
- Prefer Markdown and diagram sources (for example Mermaid, draw.io) over Office documents and screenshots, so content stays reviewable and searchable.

## Updating an asset

Bump `version` in `manifest.yaml` (semantic versioning), add a `CHANGELOG.md` entry, and update `last_reviewed`. To retire an asset, set `maturity: deprecated` and, if there is a replacement, `deprecated_by: <asset-id>`. Assets are not deleted, so existing links keep working.

## Review

- A curator listed in [`.github/CODEOWNERS`](.github/CODEOWNERS) must approve every pull request. Contributors cannot approve their own contributions.
- Curators check public safety, technical quality, reusability, overlap with existing assets, and that the manifest tags are accurate.
- The `validate` workflow must pass before merge.

## Contributor License Agreement

This project welcomes contributions and suggestions. Most contributions require you to agree to a Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions provided by the bot. You will only need to do this once across all repos using our CLA.

## Code of Conduct

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.
