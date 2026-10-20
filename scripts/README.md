# Repository scripts

Maintenance tooling for the asset catalog. Contributors use it to scaffold assets and run the same checks that CI runs on every pull request.

| File | Purpose |
|------|---------|
| [`assets.py`](assets.py) | Command-line tool to scaffold, validate, catalog and scan assets. |
| [`manifest.schema.json`](manifest.schema.json) | JSON Schema for every `assets/<category>/<asset-id>/manifest.yaml`. |
| [`requirements.txt`](requirements.txt) | Python dependencies for `assets.py` (PyYAML and jsonschema). |

## Setup

```bash
pip install -r scripts/requirements.txt
```

Run every command from the repository root.

## Commands

| Command | What it does |
|---------|--------------|
| `python scripts/assets.py new --category <category> --id <asset-id> --kind <kind> --title "<Title>" [--with-infra] [--with-src]` | Copies `templates/asset/` into `assets/<category>/<asset-id>/` and fills in the manifest and README. `--with-infra` and `--with-src` add the optional `infra/` and `src/` folders from `templates/optional/`. If the category folder does not exist yet (a planned category), it is created from `templates/category/README.md`. |
| `python scripts/assets.py validate` | Validates every manifest against the schema, checks that `id` and `category` match the folder, checks required files and README headings, enforces unique ids and existing `related` references, checks file size and blocked file types, flags internal URLs and secrets, and verifies relative Markdown links. |
| `python scripts/assets.py catalog` | Regenerates the pillar map and asset catalog in the root `README.md` and the asset table in each category `README.md`. |
| `python scripts/assets.py catalog --check` | Fails if any generated section is out of date. CI runs this. |
| `python scripts/assets.py scan` | Scans the whole repository (not only assets) for internal URLs, secrets, real-looking GUIDs and email addresses that are not on the allowlist. |

CI runs `validate`, `catalog --check`, `scan` and the template sample self-test in [`.github/workflows/validate.yml`](../.github/workflows/validate.yml).

### What `scan` allows

`scan` is a heuristic, not a replacement for curator review or GitHub secret scanning. It allows:

- **Placeholder GUIDs** such as `00000000-0000-0000-0000-000000000000`.
- **Public, well-known GUIDs** listed in `WELL_KNOWN_GUIDS` in `assets.py`: Azure built-in role definition ids and Microsoft first-party application ids, which are identical in every tenant. Add an id only if Microsoft documents it publicly, and record what it is.
- **Sample emails and domains** on `example.com`/`.org`/`.net`, `contoso.com`, `fabrikam.com`, and the reserved top-level domains `.example`, `.test`, `.invalid` and `.localhost`.

Never add a tenant, subscription, or customer-specific id to the allowlist; replace it with a placeholder.

## Manifest fields

The schema is authoritative; this is a summary.

| Field | Required | Notes |
|-------|----------|-------|
| `schema_version` | Yes | Always `1`. |
| `id` | Yes | Kebab-case; must equal the asset folder name. |
| `category` | Yes | Must equal the parent folder: `delivery-and-ops`, `m365-and-copilot`, `security-and-governance`, `landing-zones-and-infra`, `orchestration-and-workflows` or `data-and-tools`. |
| `title`, `summary` | Yes | Shown in the generated catalogs. |
| `kind` | Yes | `design-pattern`, `reference-architecture`, `solution-accelerator`, `playbook` or `sample`. |
| `maturity` | Yes | `experimental`, `preview`, `stable` or `deprecated`. |
| `version` | Yes | Semantic version. Bump it with every change and add a `CHANGELOG.md` entry. |
| `tags` | Yes | 1 to 15 kebab-case tags. |
| `owners` | Yes | GitHub handles, for example `@octocat`. |
| `last_reviewed` | Yes | `YYYY-MM-DD`. |
| `pillars` | No | Path-to-production pillars the asset helps with: `coe-and-governance`, `landing-zones`, `agent-ops`, `security-and-observability`, `solution-optimization`, `agent-architecture-patterns`. Drives the pillar map in the root README. |
| `azure_services` | No | Product names the asset uses. |
| `related` | No | Ids of related assets; each must exist. |
| `deprecated_by` | No | Id of the replacement asset when `maturity` is `deprecated`. |

## Adding a category

Categories are defined in `CATEGORIES` in `assets.py` and in the `category` enum of `manifest.schema.json`. A new category needs a curator decision, a change to both lists, and an update to [`docs/contribution-guide.md`](../docs/contribution-guide.md).
