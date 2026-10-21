# Samples: Governed Harness Simulator

`harness_sim.py` is a single-file, standard-library-only simulator of the governed
context-aware agent harness described in [architecture.md](../docs/architecture.md).
It runs one complete governed episode end to end:

1. plan
2. discover and load skills
3. retrieve sources through label-based admission
4. compute with an allowlisted arithmetic evaluator
5. render the artifact
6. verify independently, with bounded correction attempts
7. request separate approval of the exact artifact digest
8. retain memory in a scoped store
9. emit a completion certificate

Every step writes to a hash-chained evidence ledger and emits progress events and spans.
Seventeen self-test scenarios then try to break each boundary.

The simulator models the **harness decisions and wire contracts**. It does not model
Microsoft Agent Framework, Microsoft Foundry, Azure OpenAI, Azure Monitor, or any
storage service, and it makes no network calls. Use it to read the control logic,
to argue about edge cases, and as an executable specification for the tests of your
own harness ([evaluation-and-troubleshooting.md](../docs/evaluation-and-troubleshooting.md)).

> **Test only.** The model is a scripted client that replays seeded tool calls. The
> approver in the default run is an automated rehearsal approver, which a live profile
> refuses. The arithmetic evaluator runs in-process; it is an allowlist, **not a
> sandbox**. Memory receipts are format-checked, not signed. Configuration values
> (ceilings, attempts, timeout) are test values, not sizing guidance.

## Prerequisites

- Python 3.10 or later. No packages to install.

## Run it

From the repository root:

```powershell
python assets/orchestration-and-workflows/governed-context-aware-agent-harness/samples/harness_sim.py --self-test
python assets/orchestration-and-workflows/governed-context-aware-agent-harness/samples/harness_sim.py
```

Or from this folder:

```powershell
python harness_sim.py                                       # offline profile, readable episode trace
python harness_sim.py --near-miss                           # verifier forces one bounded correction
python harness_sim.py --show-certificate                    # also print the certificate JSON
python harness_sim.py --profile observe-only --model scripted   # all skills loaded, observe gate
python harness_sim.py --approve-interactively               # you approve the exact digest on the console
python harness_sim.py --self-test                           # every scenario, PASS/FAIL
python harness_sim.py --profile governed                    # live profile: refuses, exits 2
```

| Flag | Effect |
| --- | --- |
| *(none)* | Runs the `offline` profile: scripted model, progressive skills, strict gate, memory on. |
| `--near-miss` | The first scripted attempt uses an internally consistent but wrong event date. The verifier fails `event_date_alignment`, and the second attempt passes. |
| `--show-certificate` | Prints the full completion certificate as JSON. |
| `--profile {offline,governed,observe-only}` | Selects a run profile ([architecture section 7](../docs/architecture.md#7-run-profiles-and-control-matrix)). |
| `--model {scripted,live}` | Overrides the profile's model client. There is never a silent fallback. |
| `--approve-interactively` | Replaces the rehearsal approver with a console approver. It shows the artifact ID, the artifact digest, the verification digest, and every section, then asks `[y/N]`. |
| `--self-test` | Runs every scenario and prints `PASS <scenario>` or `FAIL <scenario>: <reason>`, then `N/17 scenarios passed`. |

Exit codes:

- `0`: the episode completed, or produced an `unverified_preview` in observe mode, or every scenario passed.
- `1`: any other outcome (escalated, rejected, budget exhausted, integrity failure), or a failed scenario.
- `2`: the live model client is not wired. The `governed` profile prints why and stops; it does not quietly switch to the scripted client.

### What the output looks like

The default run prints one progress line per tool call, then a summary. Hashes, receipt
IDs, and ledger counts vary with content, so they are shown here as placeholders:

```text
profile=offline model=scripted gate=strict skills=progressive memory=on
Worked example: equity-event briefing (synthetic data; not investment advice).

[01] plan     ok       get_episode_state
[02] select   ok       list_skills
[03] plan     ok       memory_list
[04] plan     ok       record_plan
...
[14] render   ok       render_briefing
[15] verify   ok       verify_artifact
[16] approve  ok       request_approval
[17] retain   ok       memory_write
[18] certify  ok       complete_episode

outcome      : completed
artifact     : briefing-ep-0001-r1 sha256=<digest-prefix>...
figures      : event_date=2026-04-29 abnormal_return=-0.0650
verification : attempt 1 passed
ledger       : <n> entries, chain valid
certificate  : governed-episode-completion approver_kind=automated-rehearsal receipt=rcpt-<id> evidence=<n> hashes
```

With `--near-miss` you see `verification : attempt 1 failed: event_date_alignment`,
then a second `execute_analysis`, `render_briefing`, and `verify_artifact`, then
`attempt 2 passed`. The artifact ID ends in `-r2`, and only that revision reaches the
approver. With `--profile observe-only --model scripted` the outcome is
`unverified_preview` and the certificate line reads `none`, because only a verified,
approved, and retained episode is certified.

## The fictional world

**Worked example: equity-event briefing (synthetic).** The issuer is "Contoso
Instruments (synthetic)". Every filing, price, calendar, and position is invented. Nothing
here is investment advice. The domain is a stand-in; see
[architecture section 12](../docs/architecture.md#12-domain-packs) for insurance,
healthcare, manufacturing, and public-sector domain packs.

The task: produce a briefing on a guidance revision announced after the market close,
with a market-adjusted abnormal return on the correct event date.

| Source | Labels | Admission |
| --- | --- | --- |
| `src-filing-001` (filing) | trusted, public | Admitted |
| `src-prices-001` (daily returns) | trusted, public | Admitted |
| `src-calendar-001` (trading sessions) | trusted, public | Admitted |
| `src-commentary-001` (commentary with an injected instruction) | untrusted, public | Excluded (`untrusted_excluded`) |
| `src-vendor-note-001` (vendor note) | unknown integrity label | Excluded (`unknown_label_fail_closed`) |
| `src-house-policy-001` (approved method and event-date rule) | trusted, confidential | Admitted through `lookup_restricted_api` |
| `src-portfolio-001` (position details) | trusted, restricted | Projection only: the exposure band. Quantities and cost basis never leave the tool. |

Skills, packaged as `SKILL.md` text with frontmatter (`name`, `description`,
`metadata.always_on`):

| Skill | Role | Loaded in the progressive profile |
| --- | --- | --- |
| `research-policy` | Always-on house policy | Preloaded |
| `evidence-verification` | Always-on verifier contract | Preloaded |
| `financial-source-retrieval` | Owns `search_public_sources` and `lookup_restricted_api` | On demand |
| `event-date-alignment` | Co-owns `execute_analysis` | On demand |
| `abnormal-return-model` | Co-owns `execute_analysis` | On demand |
| `interactive-briefing` | Owns `render_briefing` | On demand |
| `options-surface-analysis`, `credit-event-analysis`, `corporate-actions-monitor` | Distractors | Never (discoverable, not needed) |

The harness, not the skill, owns the tool-to-skill map (`TOOL_REQUIRED_SKILLS`). A
domain tool is refused until every skill that owns it is loaded, and `load_skill` is
refused until `list_skills` has run.

The verifier (`DeterministicBriefingVerifier`, version `1.1`) runs eight checks:
`script_execution`, `approved_method`, `independent_recalculation`,
`event_date_alignment`, `claim_provenance`, `required_sections`,
`restricted_data_excluded`, and `causal_claim_guard`. Version `1.0` deliberately omits
the event-date check, so scenario 5 can show it certifying the near-miss.

Profiles:

| Profile | Model | Skills | Gate | Memory | Used for |
| --- | --- | --- | --- | --- | --- |
| `offline` | scripted | progressive | strict | on, scope `offline-rehearsal` | Development, CI, demos |
| `governed` | live | progressive | strict | on, scope `contoso-analyst` | Production (needs a wired live client) |
| `observe-only` | live | all loaded | observe | off | Evaluation baselines. Never yields a certificate. |

## Self-test scenarios

The invariants match [evaluation-and-troubleshooting.md section 1](../docs/evaluation-and-troubleshooting.md#1-harness-scenario-catalog).

| # | Scenario | What it proves |
| --- | --- | --- |
| 1 | `happy-path-certificate` | A full episode yields a certificate that binds the receipt to the artifact digest. A failing telemetry exporter does not change admission. |
| 2 | `domain-tool-before-plan-refused` | Domain tools are refused before a plan, before state is read in execute, and when the plan lacks a mandatory stage. |
| 3 | `undiscovered-skill-load-refused` | No load before discovery; unknown and disabled skills cannot load; tools wait for their owning skills; distractors stay unloaded. |
| 4 | `unsafe-expression-rejected` | The `ast` allowlist rejects imports, attributes, calls, lambdas, literals, exponentiation, bad constants, unknown names, and division by zero, without consuming attempts. |
| 5 | `verifier-catches-wrong-number-then-corrects` | The near-miss fails one check, is corrected within budget, and completes. Verifier `1.0` certifies the same near-miss. |
| 6 | `correction-budget-exhausted-unverified-preview-no-approval` | An exhausted correction budget escalates and nothing reaches the approver. Observe mode yields an `unverified_preview` with no certificate. |
| 7 | `approval-digest-mismatch-rejected` | A wrong digest, a post-verification edit, and an explicit rejection all end in `approval_rejected` with no receipt and no memory write. |
| 8 | `agent-cannot-approve-its-own-artifact` | The agent identity cannot approve. A live run refuses an automated approver and accepts a human one. |
| 9 | `ledger-tamper-detected` | Payload, hash, link, and sequence tampering are detected; in-run tampering and source drift stop the episode. |
| 10 | `no-silent-live-to-scripted-fallback` | A scripted client is refused where live is configured, and an unwired live client stops with no tool calls. |
| 11 | `memory-scope-isolation` | Scopes sharing a store cannot read each other; malformed scope names are rejected. |
| 12 | `cross-run-memory-recall-validated` | Fabricated recalls are refused; poisoned records are withheld and ledgered. |
| 13 | `tool-call-ceiling-enforced` | Tool-call ceiling, model-request ceiling, and the no-progress detector each stop with a distinct status. |
| 14 | `certificate-lists-all-evidence` | The certificate pins the plan, snapshots, loaded skills, ledger head, entry count, and every entry hash, and reports `timeout_enforced: false`. |
| 15 | `narrated-done-is-not-completion` | Saying "done" never completes; approval alone is not completion; a pending approval parks the episode. |
| 16 | `immutable-controls-rejected-config-applies-next-run` | Immutable controls cannot be configured; a mid-episode config change applies to the next episode. |
| 17 | `policy-excludes-untrusted-and-restricted` | Untrusted sources are excluded, restricted data is projected, and an injected causal claim fails verification. |

## Protocol seams

Each external dependency sits behind a `typing.Protocol`, so the harness core never
imports a vendor SDK. Swap implementations without touching the gate.

| Seam | In-memory implementation | Gated production stub | Production implementation (illustrative) |
| --- | --- | --- | --- |
| `ModelClient` | `ScriptedModelClient` (seeded tool calls; `$ref` placeholders read only real tool results) | `AgentFrameworkModelClient`: refuses construction unless `HARNESS_SIM_ENABLE_AGENT_FRAMEWORK=1`, and `next_action` always raises | An Agent Framework agent over your `<chat-deployment>` in Microsoft Foundry, authenticated with Microsoft Entra ID |
| `SkillRegistry` | `InMemorySkillRegistry` (`SKILL.md` strings) | None | A registry of reviewed `skills/<name>/SKILL.md` folders, pinned by version |
| `MemoryStore` | `InMemoryMemoryStore` (`poison` helper for tests) | None | A scope-partitioned store, for example Blob Storage or Azure Cosmos DB |
| `Verifier` | `DeterministicBriefingVerifier` | None | Your domain pack's deterministic checks |
| `Approver` | `RehearsalApprover`, `ConsoleApprover` | `ApprovalQueueApprover` (returns `pending`, which parks the episode) | A review UI or queue with an Entra-authenticated human approver |
| `TelemetrySink` | `InMemoryTelemetry` | `OpenTelemetrySink` (always raises) | OpenTelemetry exported to Azure Monitor / Application Insights |

The stubs' docstrings list the wiring steps. [src/README.md](../src/README.md) maps
every simulator symbol to a production module and walks through Agent Framework
integration. Verify Agent Framework API names against its current documentation.

## Extend it

- **New domain pack.** Replace `make_world` with your synthetic sources and labels, add
  skills to `SKILL_TEXTS`, map your domain tools in `TOOL_REQUIRED_SKILLS`, implement the
  tools on `GovernedHarness`, and write a verifier that recomputes every figure from
  admitted evidence. Keep a weaker verifier version so you can prove each check matters.
- **New scenario.** Write a `scenario_*` function that raises on any broken invariant
  (use the `check` helper) and add it to `SCENARIOS`. Script deviations by editing the
  output of `build_script`.
- **Live model.** Follow the `AgentFrameworkModelClient` docstring, then run the live
  probes in [evaluation-and-troubleshooting.md section 4](../docs/evaluation-and-troubleshooting.md#4-live-probe-checklist).

## Limitations

- The `ast` allowlist evaluator runs in the harness process. It is not an OS sandbox,
  and neither is Python's `-I` isolated mode. Run model-authored or reviewed code in an
  isolated runtime such as
  [sandboxed session pools](../../../agent-runtime/code-execution-sandbox-session-pools/README.md).
- `execution_timeout_seconds` is recorded, not enforced. The certificate says so
  (`timeout_enforced: false`).
- Automatic context compaction is not implemented
  ([architecture section 9.5](../docs/architecture.md#95-compaction-designed-not-implemented)).
- There is one agent with skills; there are no sub-agents.
- The approver identity is a string, and receipts are format-checked. In production,
  take the approver from a validated Entra token and sign receipts.
- Everything runs in one process with a logical clock, so ordering is deterministic.
  Concurrency, persistence, and crash recovery are out of scope.
- The verifier is specific to the worked example; it says nothing about your domain.

## Related

- [Asset overview](../README.md) and [architecture](../docs/architecture.md)
- [Authentication and governance](../docs/authentication-and-governance.md)
- ADRs: [0001 boundaries, not transcripts](../docs/decisions/0001-harness-enforces-boundaries-not-transcripts.md),
  [0002 progressive skill disclosure](../docs/decisions/0002-progressive-skill-disclosure-over-all-loaded.md),
  [0003 approval binds to the verified digest](../docs/decisions/0003-approval-binds-to-verified-artifact-digest.md)
- [Verify before approve](../../verify-before-approve/README.md)
- [Choosing prompt vs hosted agents](../../../agent-runtime/choosing-prompt-vs-hosted-agents/README.md)
- [Production hosted agents on Foundry](../../../agent-runtime/production-hosted-agents-on-foundry/README.md)
- [Agent path to production](../../../delivery-and-ops/agent-path-to-production/README.md)
- [Agent security and governance](../../../security-and-governance/agent-security-and-governance/README.md)
