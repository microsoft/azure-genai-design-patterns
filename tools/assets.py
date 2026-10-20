"""Validate assets, scaffold new contributions, and generate the category & root catalogs.

Usage:
    python tools/assets.py validate
    python tools/assets.py catalog [--check]
    python tools/assets.py new --category <category> --id <asset-id> [--kind <kind>] [--title <title>]
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

ROOT = Path(__file__).resolve().parent.parent
ASSETS = ROOT / "assets"
TEMPLATES = ROOT / "templates" / "asset"
SCHEMA = ROOT / "schema" / "manifest.schema.json"
README = ROOT / "README.md"

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

LINK_RE = re.compile(r"\[[^\]]+\]\(([^)]+)\)")
FENCE_RE = re.compile(r"^```")


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


def scan_files(asset: Path, errors: list[str]) -> None:
    for path in sorted(asset.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        if path.stat().st_size > MAX_FILE_BYTES:
            errors.append(f"{rel}: larger than 10 MB; publish it as a release attachment instead")
        if path.suffix.lower() in BLOCKED_SUFFIXES or path.name.lower() == ".env":
            errors.append(f"{rel}: file type not allowed (possible secret or bytecode artifact)")
            continue
        if path.suffix.lower() not in TEXT_SUFFIXES and path.name != "Dockerfile":
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for label, pattern in BLOCKED_PATTERNS.items():
            match = pattern.search(text)
            if match:
                line = text.count("\n", 0, match.start()) + 1
                errors.append(f"{rel}:{line}: possible {label}")
        if path.suffix.lower() == ".md":
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

    for top_doc in (README, ROOT / "CONTRIBUTING.md", ASSETS / "README.md"):
        if top_doc.is_file():
            check_markdown_links(top_doc, errors)
    docs_dir = ROOT / "docs"
    if docs_dir.is_dir():
        for md in sorted(docs_dir.rglob("*.md")):
            check_markdown_links(md, errors)

    for name, manifest in manifests.items():
        rel_asset = asset_locations[name]
        for ref in [*manifest.get("related", []), manifest.get("deprecated_by")]:
            if ref and ref not in manifests:
                errors.append(f"{rel_asset}/manifest.yaml: references unknown asset '{ref}'")

    for error in errors:
        print(f"ERROR {error}")
    print(f"Checked {len(asset_dirs())} asset(s) across {len(CATEGORIES)} categories: {len(errors)} error(s).")
    return 1 if errors else 0


def render_category_catalog(cat_name: str) -> str:
    cat_dir = ASSETS / cat_name
    rows = []
    if cat_dir.is_dir():
        for asset in sorted(p for p in cat_dir.iterdir() if p.is_dir() and not p.name.startswith(".")):
            path = asset / "manifest.yaml"
            if not path.is_file():
                continue
            m = load_manifest(path)
            summary = " ".join(str(m.get("summary", "")).split()).replace("|", "\\|")
            title = str(m.get("title", asset.name)).replace("|", "\\|")
            rows.append(
                f"| [{title}]({asset.name}/README.md) | `{m.get('kind', '')}` | "
                f"{m.get('maturity', '')} | {summary} |"
            )
    if not rows:
        return "_No standalone assets have been published in this category yet._"
    header = "| Asset | Kind | Maturity | Summary |\n|-------|------|----------|---------|"
    return header + "\n" + "\n".join(rows)


def render_catalog() -> str:
    sections: list[str] = []
    for cat_name, meta in CATEGORIES.items():
        cat_dir = ASSETS / cat_name
        if not cat_dir.is_dir():
            continue
        assets_in_cat = [
            p for p in sorted(cat_dir.iterdir())
            if p.is_dir() and not p.name.startswith(".") and (p / "manifest.yaml").is_file()
        ]
        if not assets_in_cat:
            continue
        rows = []
        for asset in assets_in_cat:
            m = load_manifest(asset / "manifest.yaml")
            summary = " ".join(str(m.get("summary", "")).split()).replace("|", "\\|")
            title = str(m.get("title", asset.name)).replace("|", "\\|")
            rel_readme = f"assets/{cat_name}/{asset.name}/README.md"
            rows.append(
                f"| [{title}]({rel_readme}) | `{m.get('kind', '')}` | "
                f"{m.get('maturity', '')} | {summary} |"
            )
        section = (
            f"### [{meta['title']}](assets/{cat_name}/README.md) (`{cat_name}`)\n\n"
            f"{meta['description']}\n\n"
            "| Asset | Kind | Maturity | Summary |\n"
            "|-------|------|----------|---------|\n"
            + "\n".join(rows)
        )
        sections.append(section)
    if not sections:
        return "_No assets have been published yet._"
    return "\n\n".join(sections)


def catalog(check: bool) -> int:
    text = README.read_text(encoding="utf-8")
    pattern = re.compile(r"(<!-- catalog:start -->\n)(.*?)(\n<!-- catalog:end -->)", re.S)
    if not pattern.search(text):
        print("ERROR README.md is missing the catalog markers")
        return 1
    updated = pattern.sub(lambda m: m.group(1) + render_catalog() + m.group(3), text)

    cat_pattern = re.compile(r"(<!-- category-catalog:start -->\n)(.*?)(\n<!-- category-catalog:end -->)", re.S)
    cat_updates: list[tuple[Path, str, str]] = []
    for cat_name in CATEGORIES:
        cat_readme = ASSETS / cat_name / "README.md"
        if not cat_readme.is_file():
            continue
        cat_text = cat_readme.read_text(encoding="utf-8")
        if cat_pattern.search(cat_text):
            new_cat_text = cat_pattern.sub(
                lambda m, c=cat_name: m.group(1) + render_category_catalog(c) + m.group(3),
                cat_text,
            )
            cat_updates.append((cat_readme, cat_text, new_cat_text))

    if check:
        stale = updated != text or any(old != new for _, old, new in cat_updates)
        if stale:
            print("ERROR catalog is out of date; run: python tools/assets.py catalog")
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


def scaffold_new(category: str, asset_id: str, kind: str, title: str | None) -> int:
    if category not in CATEGORIES:
        print(f"ERROR unknown category '{category}'. Allowed: {', '.join(CATEGORIES)}")
        return 1
    if not re.match(r"^[a-z0-9]+(-[a-z0-9]+)*$", asset_id):
        print(f"ERROR invalid asset id '{asset_id}' (use lowercase kebab-case)")
        return 1
    for existing in asset_dirs():
        if existing.name == asset_id:
            print(f"ERROR asset id '{asset_id}' already exists at {existing.relative_to(ROOT).as_posix()}")
            return 1
    target = ASSETS / category / asset_id
    shutil.copytree(TEMPLATES, target)
    resolved_title = title or asset_id.replace("-", " ").title()
    today = date.today().isoformat()

    manifest_path = target / "manifest.yaml"
    manifest_text = manifest_path.read_text(encoding="utf-8")
    manifest_text = (
        manifest_text.replace("id: asset-id", f"id: {asset_id}")
        .replace("category: m365-and-copilot", f"category: {category}")
        .replace("title: Short descriptive title", f"title: {resolved_title}")
        .replace("kind: design-pattern", f"kind: {kind}")
        .replace("last_reviewed: 2026-01-01", f'last_reviewed: "{today}"')
    )
    manifest_path.write_text(manifest_text, encoding="utf-8", newline="\n")

    readme_path = target / "README.md"
    readme_text = readme_path.read_text(encoding="utf-8")
    readme_text = (
        readme_text.replace("# Short descriptive title", f"# {resolved_title}")
        .replace("<category>", category)
        .replace("<asset-id>", asset_id)
    )
    readme_path.write_text(readme_text, encoding="utf-8", newline="\n")

    print(f"Scaffolded new asset at assets/{category}/{asset_id}/")
    print("Next steps:")
    print(f"  1. Author assets/{category}/{asset_id}/README.md, docs/, and samples/ or infra/")
    print("  2. Run: python tools/assets.py validate")
    print("  3. Run: python tools/assets.py catalog")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("validate", help="validate every asset, category, and relative link")
    cat = sub.add_parser("catalog", help="regenerate the root and category README catalogs")
    cat.add_argument("--check", action="store_true", help="fail if any catalog is out of date")
    new_cmd = sub.add_parser("new", help="scaffold a new asset from templates/asset/")
    new_cmd.add_argument("--category", required=True, choices=list(CATEGORIES), help="target category folder")
    new_cmd.add_argument("--id", required=True, dest="asset_id", help="kebab-case asset id")
    new_cmd.add_argument("--kind", default="design-pattern", choices=list(VALID_KINDS), help="asset kind")
    new_cmd.add_argument("--title", default=None, help="human-readable asset title")
    args = parser.parse_args()
    if args.command == "validate":
        return validate()
    if args.command == "catalog":
        return catalog(args.check)
    return scaffold_new(args.category, args.asset_id, args.kind, args.title)


if __name__ == "__main__":
    sys.exit(main())
