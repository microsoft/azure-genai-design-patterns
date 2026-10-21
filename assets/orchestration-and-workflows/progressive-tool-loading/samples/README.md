# Progressive loader sample

`progressive_loader.py` is a single-file, standard-library-only reference implementation of
progressive tool and skill loading. It runs on Python 3.9 or later with no packages, network access,
credentials or Azure resources. All tenants, skills, tools, arguments and results are synthetic.

The sample shows the harness-side mechanics described in
[docs/architecture.md](../docs/architecture.md): a tiered context view, phase-driven loading, the
`load_skill` / `unload_skill` / `search_tools` / `read_skill_reference` meta-tools, a context budget
with least-recently-used eviction or refusal, rejection of calls to tools that are not loaded, and a
ledger event for every load, unload, call and refusal.

## Run it

```powershell
# All scenarios across all four Domain Packs; exits non-zero if any scenario fails
python progressive_loader.py --self-test

# One pack only
python progressive_loader.py --self-test --domain financial-operations

# Walk a run through plan -> gather -> act -> verify and print state, telemetry and the ledger
python progressive_loader.py --domain manufacturing-maintenance

# Observe-only profile: write tools are never eligible, so the act phase loads nothing new
python progressive_loader.py --domain healthcare-intake --profile observe-only

# Refuse instead of evict when a load would exceed the budget
python progressive_loader.py --overflow refuse --budget-chars 4000
```

| Flag | Default | Meaning |
|---|---|---|
| `--self-test` | off | Run every scenario and print `PASS <scenario> (<pack>)` or `FAIL ...`, then a summary line. |
| `--domain` | all packs for `--self-test`, `retail-service` for the demo | `retail-service`, `manufacturing-maintenance`, `healthcare-intake` or `financial-operations`. |
| `--profile` | `supervised` | `observe-only` (read tools only) or `supervised` (read and write tools). |
| `--budget-chars` | `12000` | Approximate context budget for the disclosed view, in characters. Illustrative only. |
| `--overflow` | `evict` | `evict` drops the least recently used non-pinned skill; `refuse` returns `budget_exceeded`. |

The demo exits with code 2 and a message if the configuration cannot work, for example when the
always-on skills alone do not fit `--budget-chars`.

## Context size is an approximation

The loader measures the canonical JSON of the disclosed view (`context_view()`) in characters. That
is a stable, dependency-free proxy, not a token count. Tokenizers differ by model and by language, so
replace `ProgressiveLoader.context_chars()` with a tokenizer-based count for your `<model-deployment>`
if you need closer budgeting. Every budget in the sample is an illustrative default.

## Scenarios

`--self-test` runs twelve scenarios against each Domain Pack, plus one pack-independent check.

| Scenario | What it proves |
|---|---|
| `index-only-at-start` | A new run discloses the meta-tools, the skill index and the always-on policy skill only. No tool definitions are present, and the view is smaller than the all-loaded view. |
| `phase-transition-loads-and-unloads` | Entering a phase loads that phase's skills and unloads non-pinned skills from the previous phase. Pinned skills stay. |
| `meta-tool-load-within-budget` | `load_skill` discloses an off-phase skill when it fits. `read_skill_reference` returns reference text without changing the view, and is refused for a skill that is not loaded. |
| `budget-exceeded-evicts-lru` | With `overflow=evict`, a load that does not fit evicts the least recently used non-pinned skill and reports it in `evicted`. |
| `budget-exceeded-refuses` | With `overflow=refuse`, the same load returns `budget_exceeded` and the view is unchanged. |
| `entitlement-filtered-never-appears` | A tool the caller is not entitled to never appears in the index, search results or view. Loading its skill and calling it return the same uniform "unknown" responses as a name that does not exist, and the catalog denies a direct call. |
| `not-loaded-tool-call-rejected` | A call to an eligible tool whose skill is not loaded is rejected with `tool_not_loaded` and a hint; fabricated and injected names get the uniform `Unknown tool.` response. |
| `observe-only-excludes-write-tools` | Under `observe-only`, write tools are not eligible: they never appear and cannot be loaded or called. |
| `deterministic-ordering` | Two loaders that reach the same state by different paths produce byte-identical views and the same view digest. |
| `revocation-enforced-at-call-time` | After the catalog revokes a tool, a loader that missed the change notification still fails the call because the catalog re-checks. With the notification, the tool disappears from the view at once. |
| `skill-md-round-trip` | A skill serializes to a `SKILL.md` document and parses back to the same content hash. |
| `telemetry-loaded-vs-used` | Telemetry reports tools exposed, tools used, exposed-but-unused, misses and evictions. |
| `production-seams-are-explicit` | The MCP gateway catalog adapter and the Azure AI Search skill index adapter satisfy their protocols and raise `NotImplementedError` until implemented. |

Expected tail of the output:

```text
PASS telemetry-loaded-vs-used (retail-service)
PASS production-seams-are-explicit
49 passed, 0 failed
```

## Seams

| Seam | Sample implementation | Production adapter (stub) | What the adapter must do |
|---|---|---|---|
| `CatalogSource` | `InMemoryCatalog`, with per-principal-class entitlements, a version counter and change subscribers | `McpGatewayCatalog(endpoint, token_provider)` | Call `tools/list` with the caller's delegated token and follow `nextCursor`; call `tools/call` and let the server re-check entitlement; subscribe to `notifications/tools/list_changed` when the server advertises `capabilities.tools.listChanged`, otherwise refresh at phase boundaries. Take each tool's effect from governed catalog metadata, not from `readOnlyHint`. |
| `SkillSearch` | `KeywordSkillSearch`, which deliberately indexes every skill so the loader's post-filter is exercised | `AzureAISearchSkillIndex(endpoint, index_name, credential)` | Run keyword, vector or hybrid queries over skill names and descriptions; apply a security filter where the index supports it. The loader still intersects results with the eligible set. |
| `EventSink` | `ListSink`, which adds a sequence number | Your evidence store | Append ledger events to a tamper-evident store and emit OpenTelemetry spans or logs. |
| `AutonomyProfile` | `observe-only` and `supervised` in `PROFILES` | Your bounded-autonomy envelope | Supply the effects a run may use. Approval gates and step limits stay in the bounded-autonomy layer. |

## Domain Packs

| Pack | Organization | Always-on skill | Off-phase skill with references | Withheld tool |
|---|---|---|---|---|
| `retail-service` | Contoso Retail | `service-conduct` | `product-knowledge` | `refunds.approve_exception` |
| `manufacturing-maintenance` | Fabrikam | `maintenance-safety` | `diagnostic-procedures` | `safety.override_lockout` |
| `healthcare-intake` | Northwind Clinics | `intake-privacy` | `form-guidance` | `clinical.get_visit_notes` |
| `financial-operations` | Contoso Financial Operations | `payment-controls` | `procedure-library` | `limits.override_limit` |

Each pack has one skill per phase (two in gather), an off-phase skill the model can load with
`load_skill`, and a restricted skill whose only tool the caller is not entitled to. The restricted
skill exists in the registry so the scenarios can prove it never reaches the model.

## What the sample does not do

- It does not call a model. Tool calls are scripted so the scenarios are deterministic.
- It does not authenticate anyone. The `Caller` is a fixed tenant and principal class.
- It does not implement approval gates, step budgets or plan approval. See
  [Bounded Autonomy](../../bounded-autonomy/README.md).
- Ledger events are kept in memory. See
  [Evidence Ledger and Run Certificate](../../evidence-ledger-and-run-certificate/README.md) for a
  durable, verifiable design.
- `SKILL.md` parsing supports only the restricted frontmatter subset the sample writes. Use a real
  YAML parser in production.
