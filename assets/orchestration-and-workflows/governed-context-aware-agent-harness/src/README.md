# Reference source layout

This folder ships **no production code**. The runnable artifact is the single-file,
stdlib-only simulator in [`samples/harness_sim.py`](../samples/harness_sim.py), which keeps
everything in one file so you can read it from top to bottom and run it anywhere.

This page shows how to split that simulator into a maintainable package when you build
your own harness, which simulator symbols map to each module, how skills are packaged,
and how to wire the seams to Microsoft Agent Framework. The module contracts are defined
in [architecture section 6](../docs/architecture.md#6-module-contracts), and the JSON
shapes in [section 8](../docs/architecture.md#8-wire-contracts).

## Reference module layout

```text
your_harness/
  harness/                 # admission logic: no SDK imports, no network, no clock reads
    policy.py              # dispatch + gate: plan-before-act, skill ownership, freeze, ceilings
    capabilities.py        # skill catalog, discovery, load, exposed-tool computation
    state.py               # EpisodeState and the get_episode_state view (required_next)
    ledger.py              # append-only, hash-chained evidence ledger
    verifier.py            # Verifier protocol + report type; domain checks plug in
    diagnosis.py           # failed report -> bounded correction hint or escalation
    certificate.py         # completion gaps + completion-certificate/v1
    memory.py              # scoped memory record schema, validate-on-read, write rules
  runtime/                 # the loop and the adapters; SDK imports live only here
    loop.py                # two-turn episode, continuations, model-request ceiling
    config.py              # HarnessConfig, profiles, versioned ConfigStore
    sources.py             # label-based admission, projections, snapshots
    analysis.py            # ast-allowlisted evaluator (or a client for a sandbox service)
    progress.py            # progress-event/v1 stream for the UI
    telemetry.py           # span/event helpers, redaction, failure counting
    adapters/
      model_agent_framework.py   # ModelClient backed by Agent Framework
      approver_queue.py          # Approver backed by your approval queue + UI
      memory_store.py            # MemoryStore backed by your governed store
      telemetry_otel.py          # TelemetrySink backed by OpenTelemetry
  domain/                  # one domain pack (see architecture section 12)
    tools.py               # domain tool handlers + TOOL_REQUIRED_SKILLS map
    checks.py              # domain verifier checks
    sources.py             # source catalog and labels for this domain
  skills/
    research-policy/SKILL.md         # always-on
    evidence-verification/SKILL.md   # always-on
    <working-skill>/SKILL.md         # loaded on demand
  tests/
    test_scenarios.py      # the self-test scenarios, ported to your test runner
```

Two dependency rules keep the harness testable and auditable:

1. **`harness/` imports nothing from `runtime/`, `domain/`, or any SDK.** It talks to the
   outside world only through the six protocol seams. That is what lets the same admission
   logic run under the scripted client in CI and under a live model in production.
2. **Only `runtime/adapters/` imports SDKs.** Upgrading Agent Framework, the OpenTelemetry
   exporter, or a storage SDK touches one adapter, not the gate or the ledger.

## Module map to the simulator

| Module | Responsibility | Simulator symbols in `harness_sim.py` |
|---|---|---|
| `harness/policy.py` | Single enforcement point for every tool call | `GovernedHarness._dispatch`, `_gate`, `ControlRejected`, `HarnessStop` subclasses, `TOOL_STEP` |
| `harness/capabilities.py` | Catalog, discovery, load, exposed tools | `InMemorySkillRegistry`, `parse_frontmatter`, `skill_body`, `_preload`, `_load_into`, `_exposed_tools`, `_tool_list_skills`, `_tool_load_skill` |
| `harness/state.py` | Typed per-episode state and its read view | `EpisodeState`, `_tool_get_episode_state`, `_required_next` |
| `harness/ledger.py` | Append-only hash chain | `EvidenceLedger` (`append`, `head`, `verify_chain`), `GENESIS_HASH`, `canonical`, `digest` |
| `harness/verifier.py` | Independent deterministic checks | `Verifier` protocol, `VerificationReport`, `DeterministicBriefingVerifier` |
| `harness/diagnosis.py` | Correction hint or escalation | correction branch of `_tool_verify_artifact`, `CorrectionBudgetExhausted` |
| `harness/certificate.py` | Completion admission and certificate | `_completion_gaps`, `_build_certificate`, `_tool_complete_episode` |
| `harness/memory.py` | Scoped memory rules | `MemoryStore` protocol, `_validate_memory_record`, `_tool_memory_read`, `_tool_memory_write`, `SCOPE_RE` |
| `runtime/loop.py` | Episode loop | `GovernedHarness.run_episode`, `_run_phase`, `_next_action`, `EpisodeResult` |
| `runtime/config.py` | Versioned configuration | `HarnessConfig`, `PROFILES`, `ConfigStore`, `IMMUTABLE_CONTROLS` |
| `runtime/sources.py` | Source admission and snapshots | `admit_source`, `_snapshot`, `_assert_snapshots`, `make_world` (fixture) |
| `runtime/analysis.py` | Allowlisted computation | `compile_expression`, `evaluate_expression`, `ALLOWED_NAMES`, `_tool_execute_analysis` |
| `runtime/progress.py` | UI progress stream | `_progress` |
| `runtime/telemetry.py` | Spans, events, redaction | `TelemetrySink` protocol, `InMemoryTelemetry`, `_span`, `_emit`, `_redact` |
| `runtime/adapters/*` | Production seams | `AgentFrameworkModelClient` and `OpenTelemetrySink` (stubs), `ApprovalQueueApprover`, `ConsoleApprover` |
| `domain/*` | Equity-event worked example (synthetic) | `TOOL_REQUIRED_SKILLS`, `SKILL_TEXTS`, `align_event_date`, domain tool handlers |

## Protocol seams

The simulator declares six seams with `typing.Protocol`. Keep these signatures stable and
put every SDK behind them:

| Seam | Methods | Production implementation |
|---|---|---|
| `ModelClient` | `next_action(phase, last_result) -> ToolCall or FinalText` | Agent Framework chat agent (see below) |
| `SkillRegistry` | `catalog() -> list[dict]`, `load(name) -> dict` | Folder or package registry of reviewed `SKILL.md` files |
| `MemoryStore` | `list(scope)`, `read(scope, key)`, `write(scope, key, record)` | Governed store partitioned by scope |
| `Verifier` | `verify(artifact, evidence) -> VerificationReport` | Domain verifier, versioned with the domain pack |
| `Approver` | `decide(request, artifact_body) -> dict` | Approval queue plus a separate reviewer UI |
| `TelemetrySink` | `emit(name, attrs)`, `span(name, attrs)` | OpenTelemetry with the Azure Monitor exporter |

## Skill packaging convention

Each skill is a folder with a `SKILL.md` at its root: a small frontmatter block followed by
plain markdown instructions.

```markdown
---
name: event-date-alignment
description: Align an announcement to the correct trading session.
license: MIT
metadata:
  version: 1.0.0
  always_on: false
---

# event-date-alignment

If the announcement is after the close, the event date is the next trading session
in the admitted calendar.
```

Rules the harness enforces (see
[ADR 0002](../docs/decisions/0002-progressive-skill-disclosure-over-all-loaded.md)):

- **Discovery returns metadata only.** `list_skills` returns `name`, `description`, and
  `always_on`. The `description` is all the model sees until it loads the skill, so write
  it as a one-line statement of when the skill applies.
- **Load requires prior discovery.** `load_skill(name)` is refused unless `list_skills` ran
  in this episode and the name is in the discovered catalog. Every load is ledgered with the
  skill's `version` and the `sha256` of its full text.
- **Always-on skills are preloaded** in progressive mode, and configuration cannot disable
  them. Keep them short: policy and verification rules only.
- **Skills never grant tools.** The tool-to-skill map (`TOOL_REQUIRED_SKILLS`) is
  harness-owned code in the domain pack. A domain tool is exposed, and admitted by the
  gate, only when all of its owning skills are loaded. Text inside a `SKILL.md` cannot
  change that map.
- **Skills are instructions, not code.** If a skill needs new behavior, add a reviewed tool
  and map it to the skill in code.
- **Skill packages are release artifacts.** Review them, version them, and promote them
  through the same stages as code. The content hash in the ledger and certificate proves
  which text the model actually saw.

The simulator's `parse_frontmatter` supports one nesting level, which is enough for this
convention. In production, use a YAML parser in safe-load mode and reject unknown keys.

## Integrating with Microsoft Agent Framework

The source implementation behind this pattern pinned `agent-framework-core` 1.18.0 and
`agent-framework-openai` 1.14.3. Names and features change between releases, and some
(progressive tool registration, file-backed memory providers) were marked experimental.
**Check every name here against the current Agent Framework documentation.** The mapping
table is in [architecture section 10](../docs/architecture.md#10-mapping-to-microsoft-agent-framework).

1. **Model client.** Build a chat client for your Azure OpenAI or Foundry Models deployment
   (`<chat-deployment>`) with `DefaultAzureCredential`. No API keys.
2. **One session per episode.** Create a fresh agent session for each episode and reuse it
   for the PLAN and EXECUTE turns only. Continuity across episodes comes from the memory
   store, never from a long-lived session.
3. **Tools.** Register the control tools and the domain tools as function tools with typed
   parameters. Return structured JSON, including structured errors for refusals.
4. **Policy gate as function middleware.** The simulator's `ModelClient.next_action` is a
   step-wise seam that keeps the self-test deterministic. With Agent Framework, the
   framework runs the tool-calling loop, so move the body of `_dispatch` and `_gate` into
   function middleware that runs before every tool invocation. The middleware is then the
   single enforcement point. A refusal becomes a tool error result. A stop ends the run.
5. **Context injection.** Use a context provider (or equivalent hook) to add the phase rules
   and the current `required_next` value before each model call.
6. **Skills.** If your version ships a skills provider, confirm that it ledgers each load
   and supports the discovery-before-load rule. Otherwise keep the custom `list_skills` and
   `load_skill` tools from the simulator.
7. **Telemetry.** Enable the framework's OpenTelemetry instrumentation and export with the
   Azure Monitor OpenTelemetry distro. Keep a redaction processor in front of the exporter,
   as `_redact` does in the simulator.
8. **Approval stays out of band.** Do not give the agent an approve tool. The harness sends
   the digest-bound request to the approver service and validates the returned decision.
   See [ADR 0003](../docs/decisions/0003-approval-binds-to-verified-artifact-digest.md).
9. **Keep these off:** automatic compaction, built-in web search, and tool auto-approval.
   Turning any of them on is a design change; see
   [architecture section 9.5](../docs/architecture.md#95-compaction-designed-not-implemented).

## Persistence and identity

- **Evidence.** Artifacts, ledgers, and certificates go to an append-only store keyed by
  `trace_id` and artifact digest (for example, Blob Storage with an immutability policy).
- **Approvals and memory.** The approval queue and memory scopes go to a store with
  per-scope partitioning and role-based access (for example, Cosmos DB). These products are
  illustrative; the pattern needs append-only evidence and per-scope memory.
- **Identities.** The hosted agent runs as a managed identity with one role assignment per
  source API. Reviewers sign in with their own Entra ID identities. The agent identity has
  no write access to approval decisions. See
  [authentication and governance](../docs/authentication-and-governance.md).
- **Configuration.** Store config with a version number. Each episode pins the version it
  starts with, and changes apply to the next episode.

## Testing your build

Port the simulator's self-test scenarios to your test runner and run them against the
scripted model client on every change to policy, skills, verifier, or config. The scenario
catalog and the matching live probes are in
[evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md#1-harness-scenario-catalog).

## Related

- [Asset overview](../README.md) and the [sample guide](../samples/README.md)
- [Architecture](../docs/architecture.md)
- [ADR 0001: the harness enforces boundaries, not transcripts](../docs/decisions/0001-harness-enforces-boundaries-not-transcripts.md)
- [Choosing Prompt Agents vs Hosted Agents in Microsoft Foundry](../../../agent-runtime/choosing-prompt-vs-hosted-agents/README.md)
- [Sandboxed Code Execution with Session Pools](../../../agent-runtime/code-execution-sandbox-session-pools/README.md)
