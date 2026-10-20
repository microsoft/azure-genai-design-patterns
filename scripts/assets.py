"""Validate assets, scaffold new contributions, generate catalogs, and scan for unsafe content.

Usage:
    python scripts/assets.py validate
    python scripts/assets.py catalog [--check]
    python scripts/assets.py new --category <category> --id <asset-id> [--kind <kind>] [--title <title>]
                                 [--with-infra] [--with-src]
    python scripts/assets.py scan
"""

from __future__ import annotations

import argparse
from datetime import date
import json
import re
import shutil
import sys
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

SCRIPTS = Path(__file__).resolve().parent
ROOT = SCRIPTS.parent
ASSETS = ROOT / "assets"
TEMPLATES = ROOT / "templates" / "asset"
OPTIONAL_TEMPLATES = ROOT / "templates" / "optional"
CATEGORY_TEMPLATE = ROOT / "templates" / "category" / "README.md"
SCHEMA = SCRIPTS / "manifest.schema.json"
README = ROOT / "README.md"

# Categories are fixed. A category folder is created with its first asset, so a category
# can be "planned" (listed here and in the schema) without a folder in assets/.
CATEGORIES: dict[str, dict[str, str]] = {
    "delivery-and-ops": {
        "title": "Delivery and Operations",
        "description": "Playbooks, readiness gates, evaluation frameworks, and AgentOps operating models.",
    },
    "m365-and-copilot": {
        "title": "Microsoft 365 and Copilot Integration",
        "description": "Declarative agents, custom engine agents, MCP plug-ins, MCP Apps (`ui://`), Teams, and Cowork surfaces.",
    },
    "security-and-governance": {
        "title": "Security and Governance",
        "description": "Defense-in-depth tool governance, Entra Agent ID, Foundry Toolbox, guardrails, Purview, and Sentinel audit.",
    },
    "landing-zones-and-infra": {
        "title": "Landing Zones and Infrastructure",
        "description": "Private Microsoft Foundry landing zones, network isolation, Bicep/Terraform IaC, and multi-stage CI/CD.",
    },
    "orchestration-and-workflows": {
        "title": "Orchestration and Workflows",
        "description": "Multi-agent routers, planner-and-specialist handoffs, durable state/caching, and workflow engines.",
    },
    "data-and-tools": {
        "title": "Data and Tools",
        "description": "Governed MCP server patterns, On-Behalf-Of (OBO) data connectors, RAG grounding, and action safety gates.",
    },
}

PILLAR_DOCS = "assets/delivery-and-ops/agent-path-to-production/docs/pillars"
PILLARS: dict[str, dict[str, str]] = {
    "coe-and-governance": {
        "title": "1. AI CoE & Governance",
        "focus": "Operating model, intake and graduation, responsible AI policy, agent inventory, cost accountability.",
        "doc": f"{PILLAR_DOCS}/01-ai-coe-and-governance.md",
    },
    "landing-zones": {
        "title": "2. AI Landing Zones",
        "focus": "Network topology, private connectivity, identity and RBAC, policy, environments, regional design.",
        "doc": f"{PILLAR_DOCS}/02-ai-landing-zones.md",
    },
    "agent-ops": {
        "title": "3. Agent Ops",
        "focus": "CI/CD, versioning, tracing, evaluation gates, monitoring, incident runbooks.",
        "doc": f"{PILLAR_DOCS}/03-agent-ops.md",
    },
    "security-and-observability": {
        "title": "4. AI Security & Observability",
        "focus": "Agent and tool authorization, prompt-injection defenses, sensitive data, red teaming, audit trails.",
        "doc": f"{PILLAR_DOCS}/04-ai-security-and-observability.md",
    },
    "solution-optimization": {
        "title": "5. Solution Optimization",
        "focus": "Performance, latency, cost, retrieval quality, semantic layers, scaling.",
        "doc": f"{PILLAR_DOCS}/05-solution-optimization.md",
    },
    "agent-architecture-patterns": {
        "title": "6. Proven Patterns for Agent Architectures",
        "focus": "Orchestration, quality controls, interoperability, state, front doors, scale.",
        "doc": f"{PILLAR_DOCS}/06-proven-patterns-for-agent-architectures.md",
    },
}

VALID_KINDS = ("design-pattern", "reference-architecture", "solution-accelerator", "playbook", "sample")
REQUIRED_FILES = ("manifest.yaml", "README.md", "CHANGELOG.md")
REQUIRED_README_HEADINGS = ("## When to use", "## When not to use", "## Architecture", "## Getting started")
MAX_FILE_BYTES = 10 * 1024 * 1024
BLOCKED_SUFFIXES = {".pfx", ".p12", ".pem", ".key", ".env", ".publishsettings", ".pyc"}
TEXT_SUFFIXES = {
    ".md", ".txt", ".yaml", ".yml", ".json", ".py", ".ipynb", ".ts", ".js", ".cs", ".java",
    ".go", ".ps1", ".sh", ".bicep", ".tf", ".tfvars", ".toml", ".ini", ".cfg", ".xml",
    ".html", ".csv", ".sample", ".mmd", ".drawio", ".kql",
}

# Heuristics for content that must not be published. They catch common mistakes; they do
# not replace curator review or GitHub secret scanning.
BLOCKED_PATTERNS = {
    "internal SharePoint URL": re.compile(r"https?://[a-z0-9-]+(?:-my)?\.sharepoint\.com/", re.I),
    "internal corpnet host": re.compile(r"\b[a-z0-9.-]+\.corp\.microsoft\.com\b", re.I),
    "internal devtunnel URL": re.compile(r"\b[a-z0-9.-]+\.devtunnels\.ms\b", re.I),
    "confidentiality marking": re.compile(r"\b(?:Microsoft (?:Highly )?Confidential|Internal Use Only)\b", re.I),
    "storage account key": re.compile(r"AccountKey=[A-Za-z0-9+/=]{20,}"),
    "SAS token signature": re.compile(r"[?&]sig=[A-Za-z0-9%+/=]{20,}"),
    "private key": re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "GitHub token": re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b"),
    "AWS access key": re.compile(r"\bAKIA[0-9A-Z]{16}\b"),
}

# Extra checks used by `scan` across the whole repository.
GUID_RE = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,})\b")
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "contoso.com", "fabrikam.com",
                         "users.noreply.github.com")
ALLOWED_EMAILS = {"opencode@microsoft.com", "secure@microsoft.com", "git@github.com"}
# RFC 2606 / RFC 6761 reserved top-level domains are never routable, so they are safe in samples.
RESERVED_TLDS = ("example", "test", "invalid", "localhost")
# Public, well-known identifiers that are identical in every tenant (Azure built-in role
# definition ids and Microsoft first-party application ids). They are not secrets and
# they are not tenant-specific, so `scan` does not flag them. Add an entry only for an id
# that Microsoft documents publicly, and say what it is.
WELL_KNOWN_GUIDS = {
    "53ca6127-db72-4b80-b1b0-d745d6d5456d": "Azure AI User (built-in role)",
    "230815da-be43-4aae-9cb4-875f7bd000aa": "Cosmos DB Operator (built-in role)",
    "b86a8fe4-44ce-4948-aee5-eccb2c155cd7": "Key Vault Secrets Officer (built-in role)",
    "4633458b-17de-408a-b874-0445c86b69e6": "Key Vault Secrets User (built-in role)",
    "ba92f5b4-2d11-453d-a403-e96b0029c9fe": "Storage Blob Data Contributor (built-in role)",
    "b7e6dc6d-f1e8-4753-8033-0f276bb0955b": "Storage Blob Data Owner (built-in role)",
    "8ebe5a00-799e-43f5-93ac-243d3dce84a7": "Search Index Data Contributor (built-in role)",
    "1407120a-92aa-4202-b7e9-c0e197c71c8f": "Search Index Data Reader (built-in role)",
    "7ca78c08-252a-4471-8644-bb5ff32d4ba0": "Search Service Contributor (built-in role)",
    "7f951dda-4ed3-4680-a7ca-43fe172d538d": "AcrPull (built-in role)",
    "1fec8e78-bce4-4aaf-ab1b-5451cc387264": "Microsoft Teams desktop/mobile client (first-party app)",
    "5e3ce6c0-2b1f-4285-8d4b-75ee78787346": "Microsoft Teams web client (first-party app)",
    "2ff814a6-3304-4ab8-85cb-cd0e6f879c1d": "Azure Databricks resource (first-party app)",
    "ab3be6b7-f5df-413d-ac2d-abf1e3fd9c0b": "Microsoft enterprise token store (first-party app)",
}
SCAN_DIRS = (".github", "assets", "docs", "scripts", "templates")
SCAN_ROOT_GLOBS = ("*.md", "*.yml", "*.yaml")

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^```")
KEBAB_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


def load_manifest(path: Path) -> dict:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValueError("manifest.yaml must contain a mapping")
    # YAML parses unquoted dates into date objects; the schema expects strings.
    return json.loads(json.dumps(data, default=str))


def category_dirs() -> list[Path]:
    if not ASSETS.is_dir():
        return []
    return sorted(p for p in ASSETS.iterdir() if p.is_dir() and not p.name.startswith("."))


def planned_categories() -> list[str]:
    return [c for c in CATEGORIES if not (ASSETS / c).is_dir()]


def asset_dirs() -> list[Path]:
    """Return all asset directories at assets/<category>/<asset-id>/."""
    found: list[Path] = []
    for cat_dir in category_dirs():
        if cat_dir.name not in CATEGORIES:
            continue
        for sub in sorted(cat_dir.iterdir()):
            if sub.is_dir() and not sub.name.startswith("."):
                found.append(sub)
    return found


def published_assets() -> list[tuple[str, Path, dict]]:
    """Return (category, asset dir, manifest) for every asset with a manifest, in category order."""
    result: list[tuple[str, Path, dict]] = []
    for cat_name in CATEGORIES:
        cat_dir = ASSETS / cat_name
        if not cat_dir.is_dir():
            continue
        for asset in sorted(p for p in cat_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            if (asset / "manifest.yaml").is_file():
                result.append((cat_name, asset, load_manifest(asset / "manifest.yaml")))
    return result


def check_markdown_links(md_path: Path, errors: list[str]) -> None:
    rel = md_path.relative_to(ROOT).as_posix()
    lines = md_path.read_text(encoding="utf-8", errors="ignore").splitlines()
    in_fence = False
    for line_no, line in enumerate(lines, start=1):
        if FENCE_RE.match(line.strip()):
            in_fence = not in_fence
            continue
        if in_fence:
            continue
        for match in LINK_RE.finditer(line):
            target = match.group(1).strip()
            if not target or target.startswith(("#", "http://", "https://", "mailto:", "ui://")):
                continue
            target_path = target.split("#", 1)[0].strip()
            if not target_path:
                continue
            resolved = (md_path.parent / target_path).resolve()
            if not resolved.exists():
                errors.append(f"{rel}:{line_no}: broken relative link -> {target}")


def is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name == "Dockerfile"


def check_file_safety(path: Path, errors: list[str]) -> str | None:
    """Apply size, file-type, and blocked-pattern checks. Return the text for text files."""
    rel = path.relative_to(ROOT).as_posix()
    if path.stat().st_size > MAX_FILE_BYTES:
        errors.append(f"{rel}: larger than 10 MB; publish it as a release attachment instead")
    if path.suffix.lower() in BLOCKED_SUFFIXES or path.name.lower() == ".env":
        errors.append(f"{rel}: file type not allowed (possible secret or bytecode artifact)")
        return None
    if not is_text_file(path):
        return None
    text = path.read_text(encoding="utf-8", errors="ignore")
    for label, pattern in BLOCKED_PATTERNS.items():
        match = pattern.search(text)
        if match:
            line = text.count("\n", 0, match.start()) + 1
            errors.append(f"{rel}:{line}: possible {label}")
    return text


def scan_files(asset: Path, errors: list[str]) -> None:
    for path in sorted(asset.rglob("*")):
        if not path.is_file():
            continue
        if check_file_safety(path, errors) is not None and path.suffix.lower() == ".md":
            check_markdown_links(path, errors)


def validate() -> int:
    validator = Draft202012Validator(json.loads(SCHEMA.read_text(encoding="utf-8")))
    errors: list[str] = []
    manifests: dict[str, dict] = {}
    asset_locations: dict[str, str] = {}

    for cat_dir in category_dirs():
        if cat_dir.name not in CATEGORIES:
            if (cat_dir / "manifest.yaml").is_file():
                errors.append(
                    f"assets/{cat_dir.name}: asset must be placed inside a category folder "
                    f"(assets/<category>/{cat_dir.name}/), where <category> is one of: "
                    f"{', '.join(CATEGORIES)}"
                )
            else:
                errors.append(
                    f"assets/{cat_dir.name}: unknown category folder; allowed categories: "
                    f"{', '.join(CATEGORIES)}"
                )
            continue
        cat_readme = cat_dir / "README.md"
        if not cat_readme.is_file():
            errors.append(f"assets/{cat_dir.name}: missing category README.md")
        else:
            check_markdown_links(cat_readme, errors)

    for asset in asset_dirs():
        name = asset.name
        cat_name = asset.parent.name
        rel_asset = f"assets/{cat_name}/{name}"
        if name in asset_locations:
            errors.append(f"{rel_asset}: duplicate asset id '{name}' (already at {asset_locations[name]})")
        asset_locations[name] = rel_asset

        missing = [f for f in REQUIRED_FILES if not (asset / f).is_file()]
        if missing:
            errors.append(f"{rel_asset}: missing {', '.join(missing)}")
        if (asset / "manifest.yaml").is_file():
            try:
                manifest = load_manifest(asset / "manifest.yaml")
            except (yaml.YAMLError, ValueError) as exc:
                errors.append(f"{rel_asset}/manifest.yaml: {exc}")
            else:
                for err in sorted(validator.iter_errors(manifest), key=lambda e: list(e.path)):
                    where = "/".join(str(p) for p in err.path) or "(root)"
                    message = err.message
                    if err.validator == "not":
                        message = "deprecated_by is only allowed when maturity is 'deprecated'"
                    errors.append(f"{rel_asset}/manifest.yaml: {where}: {message}")
                if manifest.get("id") != name:
                    errors.append(f"{rel_asset}/manifest.yaml: id must equal the folder name '{name}'")
                if manifest.get("category") != cat_name:
                    errors.append(
                        f"{rel_asset}/manifest.yaml: category '{manifest.get('category')}' "
                        f"must equal the parent category folder '{cat_name}'"
                    )
                manifests[name] = manifest
        readme = asset / "README.md"
        if readme.is_file():
            content = readme.read_text(encoding="utf-8")
            if not re.search(r"^# \S", content, re.M):
                errors.append(f"{rel_asset}/README.md: needs a level-1 title")
            for heading in REQUIRED_README_HEADINGS:
                if not re.search(rf"^{re.escape(heading)}\s*$", content, re.M):
                    errors.append(f"{rel_asset}/README.md: missing section '{heading}'")
        scan_files(asset, errors)

    shared_docs = [README, ROOT / "CONTRIBUTING.md", ASSETS / "README.md", SCRIPTS / "README.md"]
    for folder in (ROOT / "docs", ROOT / ".github"):
        if folder.is_dir():
            shared_docs.extend(sorted(folder.rglob("*.md")))
    # Files under templates/asset, templates/category, and templates/optional are copied into
    # assets/<category>/<asset-id>/ by `new`, so their links resolve from there, not here.
    shared_docs.extend(sorted((ROOT / "templates").glob("*.md")))
    for doc in shared_docs:
        if doc.is_file():
            check_markdown_links(doc, errors)

    for name, manifest in manifests.items():
        rel_asset = asset_locations[name]
        for ref in [*manifest.get("related", []), manifest.get("deprecated_by")]:
            if ref and ref not in manifests:
                errors.append(f"{rel_asset}/manifest.yaml: references unknown asset '{ref}'")

    for error in errors:
        print(f"ERROR {error}")
    active = len(CATEGORIES) - len(planned_categories())
    print(
        f"Checked {len(asset_dirs())} asset(s) across {active} categories "
        f"({len(planned_categories())} planned): {len(errors)} error(s)."
    )
    return 1 if errors else 0


def _cell(value: object) -> str:
    return " ".join(str(value).split()).replace("|", "\\|")


def _asset_rows(entries: list[tuple[str, Path, dict]], link_prefix: str) -> list[str]:
    rows = []
    for cat_name, asset, m in entries:
        link = f"{link_prefix}{cat_name}/{asset.name}/README.md" if link_prefix else f"{asset.name}/README.md"
        rows.append(
            f"| [{_cell(m.get('title', asset.name))}]({link}) | `{m.get('kind', '')}` | "
            f"{m.get('maturity', '')} | {_cell(m.get('summary', ''))} |"
        )
    return rows


TABLE_HEADER = "| Asset | Kind | Maturity | Summary |\n|-------|------|----------|---------|"


def render_category_catalog(cat_name: str, entries: list[tuple[str, Path, dict]]) -> str:
    rows = _asset_rows([e for e in entries if e[0] == cat_name], link_prefix="")
    if not rows:
        return "_No standalone assets have been published in this category yet._"
    return TABLE_HEADER + "\n" + "\n".join(rows)


def render_catalog(entries: list[tuple[str, Path, dict]]) -> str:
    sections: list[str] = []
    for cat_name, meta in CATEGORIES.items():
        in_cat = [e for e in entries if e[0] == cat_name]
        if not in_cat:
            continue
        sections.append(
            f"### [{meta['title']}](assets/{cat_name}/README.md) (`{cat_name}`)\n\n"
            f"{meta['description']}\n\n"
            + TABLE_HEADER + "\n" + "\n".join(_asset_rows(in_cat, link_prefix="assets/"))
        )
    body = "\n\n".join(sections) if sections else "_No assets have been published yet._"
    planned = planned_categories()
    if planned:
        names = ", ".join(f"{CATEGORIES[c]['title']} (`{c}`)" for c in planned)
        body += (
            f"\n\n**Planned categories** (folder is created with the first asset): {names}. "
            "See [docs/path-to-production.md](docs/path-to-production.md) for related content today."
        )
    return body


def render_pillars(entries: list[tuple[str, Path, dict]]) -> str:
    rows = ["| Pillar | Focus | Assets |", "|--------|-------|--------|"]
    for pillar_id, meta in PILLARS.items():
        links = [
            f"[{_cell(m.get('title', asset.name))}](assets/{cat_name}/{asset.name}/README.md)"
            for cat_name, asset, m in entries
            if pillar_id in (m.get("pillars") or [])
        ]
        title = f"[{meta['title']}]({meta['doc']})" if (ROOT / meta["doc"]).is_file() else meta["title"]
        rows.append(f"| {title} | {meta['focus']} | {', '.join(links) if links else '_None yet_'} |")
    return "\n".join(rows)


def _replace_block(text: str, marker: str, content: str) -> str | None:
    pattern = re.compile(rf"(<!-- {marker}:start -->)(.*?)(<!-- {marker}:end -->)", re.S)
    if not pattern.search(text):
        return None
    return pattern.sub(lambda m: f"{m.group(1)}\n{content}\n{m.group(3)}", text)


def catalog(check: bool) -> int:
    entries = published_assets()
    text = README.read_text(encoding="utf-8")
    updated = text
    for marker, content in (("pillars", render_pillars(entries)), ("catalog", render_catalog(entries))):
        replaced = _replace_block(updated, marker, content)
        if replaced is None:
            print(f"ERROR README.md is missing the <!-- {marker}:start/end --> markers")
            return 1
        updated = replaced

    cat_updates: list[tuple[Path, str, str]] = []
    for cat_name in CATEGORIES:
        cat_readme = ASSETS / cat_name / "README.md"
        if not cat_readme.is_file():
            continue
        cat_text = cat_readme.read_text(encoding="utf-8")
        new_cat_text = _replace_block(cat_text, "category-catalog", render_category_catalog(cat_name, entries))
        if new_cat_text is not None:
            cat_updates.append((cat_readme, cat_text, new_cat_text))

    if check:
        if updated != text or any(old != new for _, old, new in cat_updates):
            print("ERROR catalog is out of date; run: python scripts/assets.py catalog")
            return 1
        print("Catalog is up to date.")
        return 0

    changed = False
    if updated != text:
        README.write_text(updated, encoding="utf-8", newline="\n")
        changed = True
    for path, old, new in cat_updates:
        if old != new:
            path.write_text(new, encoding="utf-8", newline="\n")
            changed = True
    print("Catalog updated." if changed else "Catalog already up to date.")
    return 0


def create_category(category: str) -> None:
    cat_dir = ASSETS / category
    cat_dir.mkdir(parents=True)
    meta = CATEGORIES[category]
    text = CATEGORY_TEMPLATE.read_text(encoding="utf-8")
    text = (
        text.replace("# Category Title (`<category-id>`)", f"# {meta['title']} (`{category}`)")
        .replace("<category-id>", category)
    )
    (cat_dir / "README.md").write_text(text, encoding="utf-8", newline="\n")
    print(f"Created planned category folder assets/{category}/ from templates/category/README.md")


def scaffold_new(category: str, asset_id: str, kind: str, title: str | None,
                 with_infra: bool, with_src: bool) -> int:
    if not KEBAB_RE.match(asset_id):
        print(f"ERROR invalid asset id '{asset_id}' (use lowercase kebab-case)")
        return 1
    for existing in asset_dirs():
        if existing.name == asset_id:
            print(f"ERROR asset id '{asset_id}' already exists at {existing.relative_to(ROOT).as_posix()}")
            return 1
    if not (ASSETS / category).is_dir():
        create_category(category)
    target = ASSETS / category / asset_id
    shutil.copytree(TEMPLATES, target)
    for enabled, folder in ((with_infra, "infra"), (with_src, "src")):
        if enabled:
            shutil.copytree(OPTIONAL_TEMPLATES / folder, target / folder)
    resolved_title = title or asset_id.replace("-", " ").title()
    today = date.today().isoformat()

    manifest_path = target / "manifest.yaml"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_text = re.sub(r"^category:.*$", f"category: {category}", manifest_text, count=1, flags=re.M)
    manifest_text = (
        manifest_text.replace("id: asset-id", f"id: {asset_id}")
        .replace("title: Short descriptive title", f"title: {resolved_title}")
        .replace("kind: design-pattern", f"kind: {kind}", 1)
        .replace("last_reviewed: 2026-01-01", f'last_reviewed: "{today}"')
    )
    manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")

    readme_path = target / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")
    readme_text = (
        readme_text.replace("# Short descriptive title", f"# {resolved_title}", 1)
        .replace("**Kind:** design-pattern", f"**Kind:** {kind}")
        .replace("<category>", category)
        .replace("<asset-id>", asset_id)
    )
    readme_path.write_text(readme_text, encoding="utf-8", newline="\n")

    print(f"Scaffolded new asset at assets/{category}/{asset_id}/")
    print("Next steps:")
    print(f"  1. Fill in manifest.yaml (summary, tags, owners, pillars) and author README.md, docs/, samples/")
    print("  2. Run: python scripts/assets.py validate")
    print("  3. Run: python scripts/assets.py catalog")
    print("  4. Run: python scripts/assets.py scan")
    return 0


def _guid_allowed(value: str) -> bool:
    value = value.lower()
    return len(set(value.replace("-", ""))) <= 3 or value in WELL_KNOWN_GUIDS


def _email_allowed(address: str, domain: str) -> bool:
    domain = domain.lower()
    if address.lower() in ALLOWED_EMAILS:
        return True
    if domain.rsplit(".", 1)[-1] in RESERVED_TLDS:
        return True
    return any(domain == d or domain.endswith("." + d) for d in ALLOWED_EMAIL_DOMAINS)


def scan() -> int:
    errors: list[str] = []
    files: list[Path] = []
    for folder in SCAN_DIRS:
        if (ROOT / folder).is_dir():
            files.extend(p for p in (ROOT / folder).rglob("*") if p.is_file())
    for pattern in SCAN_ROOT_GLOBS:
        files.extend(p for p in ROOT.glob(pattern) if p.is_file())
    this_file = Path(__file__).resolve()
    checked = 0
    for path in sorted(set(files)):
        if path.resolve() == this_file or "__pycache__" in path.parts:
            continue
        checked += 1
        text = check_file_safety(path, errors)
        if text is None:
            continue
        rel = path.relative_to(ROOT).as_posix()
        for match in GUID_RE.finditer(text):
            if not _guid_allowed(match.group(0)):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{rel}:{line}: possible real GUID {match.group(0)} (use a placeholder such as 00000000-0000-0000-0000-000000000000, or add a public built-in id to WELL_KNOWN_GUIDS)")
        for match in EMAIL_RE.finditer(text):
            if not _email_allowed(match.group(0), match.group(1)):
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{rel}:{line}: possible personal email address {match.group(0)} (use user@contoso.com)")
    for error in errors:
        print(f"ERROR {error}")
    print(f"Scanned {checked} file(s): {len(errors)} finding(s).")
    return 1 if errors else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate every asset, category, and relative link")
    cat = sub.add_parser("catalog", help="regenerate the root pillar map and the root and category catalogs")
    cat.add_argument("--check", action="store_true", help="fail if any catalog is out of date")
    new_cmd = sub.add_parser("new", help="scaffold a new asset from templates/asset/")
    new_cmd.add_argument("--category", required=True, choices=list(CATEGORIES), help="target category")
    new_cmd.add_argument("--id", required=True, dest="asset_id", help="kebab-case asset id")
    new_cmd.add_argument("--kind", default="design-pattern", choices=list(VALID_KINDS), help="asset kind")
    new_cmd.add_argument("--title", default=None, help="human-readable asset title")
    new_cmd.add_argument("--with-infra", action="store_true", help="add an infra/ folder for IaC")
    new_cmd.add_argument("--with-src", action="store_true", help="add a src/ folder for deployable code")
    sub.add_parser("scan", help="scan the whole repository for secrets, internal links, real GUIDs, and emails")
    args = parser.parse_args()
    if args.command == "validate":
        return validate()
    if args.command == "catalog":
        return catalog(args.check)
    if args.command == "scan":
        return scan()
    return scaffold_new(args.category, args.asset_id, args.kind, args.title, args.with_infra, args.with_src)


if __name__ == "__main__":
    sys.exit(main())
