# Templates

Starting points used by [`scripts/assets.py new`](../scripts/README.md). Do not edit a copy here to author an asset; scaffold one instead:

```bash
python scripts/assets.py new --category <category> --id <asset-id> --kind <kind> --title "Title" [--with-infra] [--with-src]
```

| Folder | Copied to | When |
|---|---|---|
| [`asset/`](asset/README.md) | `assets/<category>/<asset-id>/` | Every new asset: `manifest.yaml`, `README.md`, `CHANGELOG.md`, `docs/`, `samples/` |
| [`optional/infra/`](optional/infra/README.md) | `assets/<category>/<asset-id>/infra/` | With `--with-infra` (Bicep or Terraform) |
| [`optional/src/`](optional/src/README.md) | `assets/<category>/<asset-id>/src/` | With `--with-src` (deployable application code) |
| [`category/`](category/README.md) | `assets/<category>/README.md` | The first time an asset is added to a planned category |

Links inside these templates are written relative to their scaffolded destination, so `validate` skips them here and checks them after scaffolding. See the [contribution guide](../docs/contribution-guide.md) for authoring standards.
