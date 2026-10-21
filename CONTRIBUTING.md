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

The validation workflow scans for common mistakes, but it cannot catch everything. You and the reviewing maintainer are responsible for what is published.

## Adding an asset

Read the **[Contribution & Pattern Standardization Guide](docs/contribution-guide.md)** for the generalization method, depth requirements by `kind`, and self-test standards, and the **[Production-readiness map](docs/production-readiness-map.md)** for how assets map to the six production-readiness pillars.

1. **Propose it.** Open a **Pattern proposal** issue so a maintainer can confirm the scope, category, and pillars before you write content.
2. **Choose the category (`<category>`)** under [`assets/`](assets/README.md):
   - `m365-and-copilot` — Microsoft 365 Copilot, Teams, Declarative Agents, Custom Engine Agents, MCP plug-ins, and Cowork
   - `security-and-governance` — Entra Agent ID, tool governance, guardrails, Purview, and Sentinel observability
   - `landing-zones-and-infra` — Private Microsoft Foundry landing zones, Bicep/Terraform IaC, and CI/CD pipelines
   - `agent-runtime` — Prompt vs hosted agents, production hosted agents on Foundry, sessions, and sandboxed code execution
   - `ai-gateway` — API Management as the control plane for model, MCP tool, and agent traffic
   - `orchestration-and-workflows` — Governed agent harnesses, verification gates, multi-agent routers, and durable workflows
   - `agentops-and-readiness` — Production-readiness guides, evidence-based readiness gates, evaluation frameworks, and AgentOps operating models
   - `data-and-tools` *(planned)* — Governed MCP servers, OBO data connectors, RAG grounding, and action safety gates

   Planned categories have no folder yet; the scaffolding command creates it with the first asset.
3. **Pick a stable `id`:** lowercase words separated by hyphens, for example `human-approval-gate`.
4. **Scaffold the asset from `templates/asset/`:**

   ```bash
   pip install -r scripts/requirements.txt
   python scripts/assets.py new --category <category> --id <asset-id> --kind <kind> --title "<Title>" [--with-infra] [--with-src]
   ```

5. **Generalize and author the content:**
   - Frame the pattern as **domain-agnostic** (demonstrating applicability across at least three domains such as ITSM/SecOps, CRM/Sales, and FinOps/HR/Ops via Domain Packs rather than a single customer case study).
   - Complete `manifest.yaml` (validated against [`scripts/manifest.schema.json`](scripts/manifest.schema.json)), including the `pillars` it serves, and `README.md`.
   - Author deep architecture, identity/authentication, and evaluation/troubleshooting guides in `docs/`, plus at least one ADR in `docs/decisions/`.
   - Include runnable, zero-dependency reference code with an offline `--self-test` mode in `samples/` and/or compilable Bicep/Terraform templates in `infra/`. Delete `samples/` if it does not apply.
6. **Validate, scan, and regenerate the catalogs** (the same checks CI runs):

   ```bash
   python scripts/assets.py catalog
   python scripts/assets.py validate
   python scripts/assets.py catalog --check
   python scripts/assets.py scan
   ```

7. **Open a pull request** using the pull request template. One asset per pull request.

### Files and sizes

- Keep each file under 10 MB. Publish large binaries (videos, datasets, packaged builds) as release attachments and link to them.
- Prefer Markdown and diagram sources (for example Mermaid, draw.io) over Office documents and screenshots, so content stays reviewable and searchable.

## Updating an asset

Bump `version` in `manifest.yaml` (semantic versioning), add a `CHANGELOG.md` entry, and update `last_reviewed`. To retire an asset, set `maturity: deprecated` and, if there is a replacement, `deprecated_by: <asset-id>`. Assets are not deleted, so existing links keep working.

## Review

- A maintainer listed in [`.github/CODEOWNERS`](.github/CODEOWNERS) must approve every pull request. Contributors cannot approve their own contributions.
- Maintainers check public safety, technical quality, reusability, overlap with existing assets, and that the manifest tags are accurate.
- The `validate` workflow must pass before merge.

## Contributor License Agreement

This project welcomes contributions and suggestions. Most contributions require you to agree to a Contributor License Agreement (CLA) declaring that you have the right to, and actually do, grant us the rights to use your contribution. For details, visit https://cla.opensource.microsoft.com.

When you submit a pull request, a CLA bot will automatically determine whether you need to provide a CLA and decorate the PR appropriately (e.g., status check, comment). Simply follow the instructions provided by the bot. You will only need to do this once across all repos using our CLA.

## Code of Conduct

This project has adopted the [Microsoft Open Source Code of Conduct](https://opensource.microsoft.com/codeofconduct/). For more information see the [Code of Conduct FAQ](https://opensource.microsoft.com/codeofconduct/faq/) or contact [opencode@microsoft.com](mailto:opencode@microsoft.com) with any additional questions or comments.
