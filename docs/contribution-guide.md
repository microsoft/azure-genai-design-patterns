# Contribution & Pattern Standardization Guide

This guide defines the engineering, architectural, and public-safety standards for every asset in `microsoft/agenticai-design-patterns`. Whether you are authoring a contribution by hand or using an AI coding agent, follow this guide so every asset in the catalog is **domain-agnostic, production-deep, runnable offline, and 100% public-safe**.

For how the catalog maps to the six path-to-production pillars, see [Path to production](path-to-production.md). For the short version of the rules, see [CONTRIBUTING.md](../CONTRIBUTING.md).

---

## 1. Repository Hierarchy & Category Taxonomy

Assets are organized in a two-level hierarchy:

```text
assets/<category>/<asset-id>/
├── manifest.yaml                 # Validated against scripts/manifest.schema.json
├── README.md                     # Entry point, Domain Pack map, decision matrix, quickstart
├── CHANGELOG.md                  # Semantic version history
├── docs/
│   ├── architecture.md           # Deep topology, wire contracts, sequence diagrams, scaling
│   ├── authentication-*.md       # Identity layers, OBO/RBAC, failure classification
│   ├── evaluation-*.md           # Rubric, smoke tests (S1–S8), troubleshooting runbook
│   └── decisions/
│       └── 0001-*.md             # Architecture Decision Records (ADRs)
├── samples/                      # Zero-dependency runnable reference modules (--self-test)
├── infra/                        # Optional (--with-infra): compilable Bicep / Terraform + CI/CD pipelines
└── src/                          # Optional (--with-src): deployable application code
```

### Choosing the right `<category>`

Every asset belongs to exactly one primary category folder under `assets/`. If an asset touches multiple concerns (for example, an M365 Custom Engine Agent that also uses OBO and Cosmos DB), place it in the category that matches the **primary architectural question the reader is trying to solve**, and link complementary assets in `manifest.yaml` (`related:`).

| Category ID | Category Title | What belongs here | Examples |
|---|---|---|---|
| `delivery-and-ops` | **Delivery and Operations** | End-to-end delivery playbooks, readiness gates, six-pillar review checklists, and AgentOps operating models | `agent-path-to-production` |
| `m365-and-copilot` | **Microsoft 365 and Copilot Integration** | Declarative agents, custom engine agents, MCP plug-ins, MCP Apps (`ui://` widgets), Teams bots, and Cowork surface integrations | `m365-azure-custom-agent`, `m365-mcp-plugin-integration` |
| `security-and-governance` | **Security and Governance** | Tool-call governance, Entra Agent ID, Foundry Toolbox allow-lists, runtime guardrails, Purview DSPM for AI, and Sentinel KQL audit | `agent-security-and-governance` |
| `landing-zones-and-infra` | **Landing Zones and Infrastructure** | Private Microsoft Foundry landing zones, Private Link / DNS topologies, Bicep/Terraform IaC, and multi-stage CI/CD promotion pipelines | `secured-foundry-landing-zone` |
| `orchestration-and-workflows` | **Orchestration and Workflows** | Standalone multi-agent routers, planner/specialist handoff protocols, durable state/caching engines, and workflow patterns | *Planned* — the folder is created with the first asset |
| `data-and-tools` | **Data and Tools** | Standalone MCP server reference architectures, On-Behalf-Of (OBO) data-platform connectors, Foundry IQ / RAG grounding, and action gates | *Planned* — the folder is created with the first asset |

Each `assets/<category>/README.md` acts as a **Category Decision Guide** with a comparison matrix showing when to choose each sibling asset in that category, plus an auto-generated `<!-- category-catalog:start --> ... <!-- category-catalog:end -->` table maintained by `python scripts/assets.py catalog`.

Planned categories have no folder yet. Until their first asset lands, [Path to production](path-to-production.md#planned-categories-where-to-look-today) points to the published assets that cover those topics today. `python scripts/assets.py new` creates the category folder and its README from `templates/category/README.md` the first time an asset is added to it.

### Mapping to the path-to-production pillars

Every manifest declares `pillars:` — one or more of the six pillars the asset helps a team move through on the way to production:

| Pillar id | Pillar |
|---|---|
| `coe-and-governance` | 1. AI CoE & Governance |
| `landing-zones` | 2. AI Landing Zones |
| `agent-ops` | 3. Agent Ops |
| `security-and-observability` | 4. AI Security & Observability |
| `solution-optimization` | 5. Solution Optimization |
| `agent-architecture-patterns` | 6. Proven Patterns for Agent Architectures |

List only the pillars where the asset gives a reader something concrete to do (a gate, a template, a runnable sample, or IaC), not every pillar it mentions. The pillar table in the root `README.md` is generated from this field.

---

## 2. Field-to-Pattern Generalization Standard ("Domain Packs")

Most high-value assets originate from real field engagements, customer architectures, or technical workshops. However, **a public design pattern must never read like a single-customer case study**.

### The 5-Step Generalization Workflow

1. **Extract the structural invariant.** Ask: *What is the recurring architectural mechanism that would stay identical if we deployed this for a bank, a hospital, a retailer, or a SecOps team?* (Examples: a 4-gate tool entitlement pipeline, an assertion-hashed MSAL OBO cache, a two-stage private Foundry landing zone, or a planner-and-specialist `HandoffEnvelope` wire contract.)
2. **Separate the invariant engine from the "Domain Pack".** Frame your architecture and code around a reusable core engine plus pluggable **Domain Packs** (entity types, tool schemas, and specialist cards).
3. **Demonstrate at least three industries side-by-side.** In `README.md` and `docs/architecture.md`, include a Domain Pack table showing how the exact same architecture adapts across at least three enterprise domains:

   | Domain Pack | Primary Entity | Signal & Context Sources | Specialist / Tool Capabilities | Governed Write Actions (`W2`–`W4`) |
   |---|---|---|---|---|
   | **ITSM / SecOps** (`secops`) | Service, asset, or incident | SIEM alerts, change logs, runbooks | Status triage, remediation planner, incident briefer | Draft incident note (`W2`), update ticket (`W3`), page on-call (`W4`) |
   | **CRM / Enterprise Sales** (`sales`) | Customer account or opportunity | Usage telemetry, CRM pipeline, external news | Account pulse, next-best action, deal coach | Draft outreach email (`W2`), log CRM activity (`W3`), bulk send (`W4`) |
   | **FinOps / HR / Operations** (`ops`) | Cost center, team, or case | Budget variance, requisition queues, policy docs | Queue analyst, policy advisor, case briefer | Draft case update (`W2`), record milestone (`W3`), publish schedule (`W4`) |

4. **Use neutral domain-agnostic identifiers in code and schemas.** Prefer generic contract names (`get_entity_profile`, `list_records`, `apply_change`, `HandoffEnvelope`, `SideEffectGate`) or ship multiple Domain Packs (`--domain sales|secops|ops`) in your runnable Python samples.
5. **Preserve hard-won engineering specifics.** Generalizing does **not** mean watering down technical depth into high-level prose! Keep the exact AADSTS error codes, JSON-RPC wire payloads, Cosmos DB partition keys (`/tenant_id`, `/principal_oid`), Private DNS zone names (`privatelink.services.ai.azure.com`), Bicep resource dependencies, and KQL correlation queries.

---

## 3. Depth & Completeness Bar by Asset `kind`

Every asset must pass both automated schema validation and curator review against the depth bar for its `kind`:

| Requirement | `design-pattern` | `reference-architecture` | `solution-accelerator` | `playbook` | `sample` |
|---|---|---|---|---|---|
| **`manifest.yaml`, `README.md`, `CHANGELOG.md`** | Required | Required | Required | Required | Required |
| **Domain Pack / Multi-Industry Framing Table** | Required | Required | Recommended | Recommended | Optional |
| **Architecture & Implementation Map in `README.md`** | Required | Required | Required | Required | Required |
| **Decision Matrix ("When to use X vs. Y")** | Required | Required | Required | Required | Recommended |
| **`docs/architecture.md` with Mermaid Topology & Sequence Diagrams** | Required (`350+` lines) | Required (`400+` lines) | Required (`400+` lines) | Via `docs/runbook.md` & pillar chapters | Concise (`150+` lines) |
| **Wire Contracts / JSON Schemas / Protocol Payloads** | Required | Required | Required | Readiness record templates | Required |
| **Identity, Auth & Entitlement Deep Dive (`docs/authentication-*.md`)** | Required when auth is in scope | Required | Required | Pillar 1 & 4 chapters | Inline in `README.md` |
| **Evaluation, Release Gates & Troubleshooting (`docs/evaluation-*.md` or `docs/troubleshooting-*.md`)** | Required | Required | Required | Pillar 3 chapter + gates | Inline in `samples/README.md` |
| **Architecture Decision Record (`docs/decisions/0001-*.md`)** | Required (`>= 1` ADR) | Required (`>= 1` ADR) | Required (`>= 1` ADR) | Optional | Optional |
| **Runnable Offline-Testable Samples (`samples/*.py`) or Compilable IaC (`infra/*.bicep`)** | Required (`samples/`) | Required (`samples/`) | Required (`infra/` + pipelines) | Optional | Required (`samples/` or `src/`) |

### Standards for Runnable Python Samples (`samples/*.py`)

When an asset includes Python reference modules in `samples/`:
- **Hermetic offline self-test:** Every module must be runnable with `python samples/<module>.py` (or `--self-test`) using the Python standard library and in-memory fakes when cloud environment variables are absent.
- **Production adapter seams:** Pair every in-memory fake used by the self-test with the real production adapter class or protocol (for example, `CosmosDocumentStore`, `MsalOboBroker`, `StreamableHttpMcpClient`, `BotConnectorSender`) in the same module.
- **Explicit pass/fail assertions:** Print each verified scenario (`PASS <scenario>`) and exit with code `0` on success or `1` on failure so CI can execute the suite directly.
- **No committed `__pycache__` or `.pyc` files:** Clean up bytecode artifacts before committing (`scripts/assets.py validate` rejects `.pyc` files).

### Standards for Infrastructure-as-Code (`infra/*.bicep`)

When an asset includes Bicep templates in `infra/`:
- **Compile cleanly:** Every `.bicep` file must pass `az bicep build --file <file> --stdout`.
- **Zero hardcoded IDs or secrets:** Parameterize all tenant IDs, subscription IDs, object IDs, and environment names. Leave environment-specific values blank (`""`) in `parameters.example.json` so unconfigured deployments fail closed.
- **Disable local auth by default:** Set `disableLocalAuth: true` on Foundry/Cognitive Services, Cosmos DB, and AI Search, and use Managed Identity + RBAC for all service-to-service connections.
- **Document dependency ordering:** Explicitly document multi-phase provisioning and teardown order (for example, capability host deletion before subnet removal).

---

## 4. Public-Safety & Sanitization Rules

This repository is public under the MIT License. Contributions must contain **zero** customer-identifiable or internal-only information:

| Category | Strictly Prohibited | Required Public-Safe Replacement |
|---|---|---|
| **Customer & Partner Names** | Real customer names, project codenames, internal team/product names from engagements | Neutral placeholders (`Contoso`, `Fabrikam`, `Northwind`, `Woodgrove`) or domain-pack names (`secops`, `sales`, `ops`) |
| **People & Aliases** | Workshop participant names, employee aliases, real email addresses | Role titles (`Platform Engineer`, `Security Architect`) and placeholder addresses (`user-a@contoso.com`) |
| **Identifiers & GUIDs** | Real Microsoft Entra tenant IDs, Azure subscription IDs, app registration client IDs | Documentation-reserved UUIDs (`00000000-0000-0000-0000-000000000000`, `11111111-1111-1111-1111-111111111111`) or `<TENANT_ID>` placeholders |
| **Hosts & URLs** | `*.sharepoint.com`, `*.corp.microsoft.com`, `*.devtunnels.ms`, `*.microsofticm.com`, internal `aka.ms` links, customer domains | RFC 2606 / example domains (`*.contoso.com`, `*.example.com`) and public `https://learn.microsoft.com/...` URLs |
| **Internal Org Terminology** | Internal sales/field acronyms, internal program tracking IDs, commercial terms | Standard public industry and Azure Cloud Adoption Framework / Well-Architected terminology |
| **Secrets & Keys** | API keys, client secrets, SAS tokens, PEM/PFX certificates, `.env` files, raw JWTs signed with real keys | Ephemeral runtime-generated keys in self-tests; Key Vault / Managed Identity references in templates |

---

## 5. Step-by-Step Contribution Workflow

### Step 0: Propose the pattern

Open a **Pattern proposal** issue (from the issue templates) before writing content. A curator confirms that the pattern is not already covered, agrees the category, `kind`, and pillars, and flags any public-safety concerns early.

### Step 1: Scaffold the asset

Install the tooling once, then use `scripts/assets.py new` to create the asset folder from `templates/asset/`:

```bash
pip install -r scripts/requirements.txt

python scripts/assets.py new \
  --category m365-and-copilot \
  --id my-pattern-id \
  --kind design-pattern \
  --title "Human-Readable Pattern Title"
```

- Add `--with-infra` to include an `infra/` folder (IaC and pipelines) and `--with-src` to include a `src/` folder (deployable application code). Both come from `templates/optional/`.
- If the category is still planned, the command also creates `assets/<category>/README.md` from `templates/category/README.md`. Fill in its decision guide.

### Step 2: Author the documentation, ADRs, and runnable samples/IaC

1. Fill out `manifest.yaml` (verify `category`, `kind`, `maturity`, `pillars`, `tags`, `azure_services`, `owners`, `related`).
2. Author `README.md` and `docs/` following the depth bar in Section 3. Delete `samples/` if your asset has no runnable modules; `infra/` and `src/` only exist if you asked for them.
3. Run all Python self-tests and/or Bicep builds locally:
   ```bash
   # Run any Python reference module self-tests
   python assets/<category>/<asset-id>/samples/<module>.py

   # Compile any Bicep templates
   az bicep build --file assets/<category>/<asset-id>/infra/<template>.bicep --stdout > /dev/null
   ```

### Step 3: Validate, scan, and regenerate catalogs

Run the same checks as CI before opening a pull request:

```bash
python scripts/assets.py catalog         # regenerate the pillar map and catalogs
python scripts/assets.py validate        # structure, schema, required sections, links
python scripts/assets.py catalog --check # fail if a generated table is stale
python scripts/assets.py scan            # public-safety scan across the repository
```

`python scripts/assets.py validate` verifies:
- Every asset lives inside a valid `assets/<category>/<asset-id>/` folder; unknown category folders are errors.
- Every `manifest.yaml` conforms to `scripts/manifest.schema.json`, `id` matches `<asset-id>`, and `category` matches `<category>`.
- Every category folder has an `assets/<category>/README.md`.
- Every asset `README.md` has required sections (`## When to use`, `## When not to use`, `## Architecture`, `## Getting started`).
- Asset ids are unique and every `related` / `deprecated_by` reference points to an existing asset.
- Every relative Markdown link in the root `README.md` and `CONTRIBUTING.md`, `assets/`, `docs/`, `scripts/README.md`, `.github/`, and `templates/` resolves to an existing file.
- No blocked file extensions (`.pfx`, `.pem`, `.key`, `.env`, `.pyc`) or oversized files (`> 10 MB`) exist inside an asset.

`python scripts/assets.py scan` checks every text file in the repository for internal URLs, secrets, confidentiality markings, GUIDs that are not documentation placeholders, and email addresses outside the placeholder domains. It is a safety net, not a substitute for curator review. See [scripts/README.md](../scripts/README.md) for details.

### Step 4: Open the pull request

- Keep one asset (or one asset update) per pull request.
- Complete the pull request template checklist, including the public-safety confirmation.
- A curator listed in `.github/CODEOWNERS` reviews the content against this guide, and the `validate` workflow must pass before merge.
