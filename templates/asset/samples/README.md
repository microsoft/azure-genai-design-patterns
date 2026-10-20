# Reference Samples

This directory contains runnable, zero-external-dependency reference implementations of the core seams in [`../docs/architecture.md`](../docs/architecture.md). Every module ships with a hermetic `--self-test` mode that executes offline using the Python standard library so it can run in local development and CI without cloud credentials or network access.

## Files

| File | Architectural Seam | Offline Self-Test Command |
|---|---|---|
| [`reference_sample.py`](reference_sample.py) | Token-scoped entitlement check, `R`/`W1`–`W4` side-effect safety gate, and structured audit sink | `python reference_sample.py --self-test` |

## Running the offline self-tests

```bash
python assets/<category>/<asset-id>/samples/reference_sample.py --self-test
```

Delete any generated `__pycache__/` directory before running `python scripts/assets.py validate`.
