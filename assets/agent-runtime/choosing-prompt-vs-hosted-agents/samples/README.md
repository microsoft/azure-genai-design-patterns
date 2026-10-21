# Samples: runtime selector

[`runtime_selector.py`](runtime_selector.py) turns the decision matrix in [`docs/architecture.md`](../docs/architecture.md#7-decision-matrix) and [ADR 0001](../docs/decisions/0001-default-to-prompt-agents-escalate-to-hosted.md) into a repeatable, reviewable recommendation. You describe a workload as a small JSON profile of yes/no answers. The selector scores it against a weighted rubric and recommends one of three runtimes:

| Runtime | Meaning |
|---|---|
| `prompt` | A declarative Foundry prompt agent: model, instructions, and platform-managed tools, run by the Foundry Agent Service. |
| `hosted` | A Foundry hosted agent: your own container and code (for example Microsoft Agent Framework or LangGraph), run by Foundry's hosted-agent runtime. |
| `mixed` | A prompt agent front door with a hosted capability (or other code) behind a tool, or a hosted orchestrator that calls prompt agents. Exactly one layer plans. |

Each result includes the scores, the rationale lines that drove them, and warnings for the controls you must still put in place. The selector is a review aid, not an oracle. Treat its output as the starting point for a design review, and record the final decision in an ADR.

## Prerequisites

- Python 3.10 or later.
- No packages to install. The sample uses only the standard library and makes no network calls.
- No Azure subscription is needed. The selector reasons about a design; it does not deploy anything.

## Run it

Run the commands from the repository root.

```bash
# Offline reference scenarios. Prints PASS <scenario> per check; exits 0 on success, 1 on failure.
python assets/agent-runtime/choosing-prompt-vs-hosted-agents/samples/runtime_selector.py --self-test

# Score the example profiles (or your own file).
python assets/agent-runtime/choosing-prompt-vs-hosted-agents/samples/runtime_selector.py --profile assets/agent-runtime/choosing-prompt-vs-hosted-agents/samples/example-profiles.json

# Same, as one JSON object per profile (for pipelines or review tools).
python assets/agent-runtime/choosing-prompt-vs-hosted-agents/samples/runtime_selector.py --profile assets/agent-runtime/choosing-prompt-vs-hosted-agents/samples/example-profiles.json --json

# Print the rubric: every criterion, when it applies, its weights, and why.
python assets/agent-runtime/choosing-prompt-vs-hosted-agents/samples/runtime_selector.py --list-criteria
```

With no arguments, the selector prints its help and exits with code 2. Invalid profiles (unknown field, non-boolean answer, missing name, unreadable file) also exit with code 2 and an `error:` message.

The self-test runs six reference scenarios and four input and adapter checks:

| Check | Expected result | What it proves |
|---|---|---|
| `faq-rag-assistant` | `prompt` | A grounded Q&A assistant with platform tools has no named control, so it stays a prompt agent. |
| `proprietary-multistep-workflow` | `hosted` | A fixed-order workflow with proprietary in-process libraries and custom state needs its own loop. |
| `m365-front-door-heavy-backend` | `mixed` | A governed conversational front door plus heavy back-end processing splits across both runtimes. |
| `untrusted-code-analysis` | `hosted` | An analysis agent with custom libraries and working files gets a hosted agent; running user-supplied scripts raises a session-pool warning. |
| `webhook-custom-protocol` | `hosted` | A webhook or non-conversational protocol points to the hosted Invocations protocol. |
| `heavy-processing-without-named-control` | `prompt` | Heavy processing alone is not a named control; the eligibility guard keeps the prompt agent. |
| `profile-rejects-unknown-field` | error | A misspelled field fails loudly instead of silently defaulting to `false`. |
| `profile-rejects-non-boolean` | error | Answers must be `true` or `false`, not strings such as `"yes"`. |
| `json-envelope-parses` | profiles load | The `{"profiles": [...]}` envelope, a bare list, and a single object all load, with defaults applied. |
| `production-sink-is-gated` | `NotImplementedError` | The production adapter stub cannot be used by accident. |

## Describe your workload

A profile is a JSON object. `name` is required; `description` is optional; every other field is a boolean that defaults to `false` (except `team_can_operate_containers`, which defaults to `true`). Unknown fields are rejected.

| Field | Group | Answer `true` when |
|---|---|---|
| `loop_must_be_code` | **Named control** | The whole job needs a fixed step order, compensation, or approval checkpoints that the model must not reorder. |
| `in_process_libraries` | **Named control** | Proprietary, native, or licensed libraries must run inside the agent loop, not behind a tool. |
| `custom_protocol` | **Named control** | Callers need a webhook, arbitrary JSON, real-time, or agent-to-agent protocol rather than a conversational API. |
| `custom_durable_state` | **Named control** | The agent needs its own durable state store or working files across turns, beyond platform conversations. |
| `custom_telemetry` | **Named control** | You need custom spans, metrics, or attributes emitted from inside the loop. |
| `gap_isolatable_behind_tool` | Gap shape | The missing capability can sit behind an OpenAPI, MCP, function, or A2A tool. |
| `deterministic_substeps` | Gap shape | A few steps (not the whole job) must be deterministic. |
| `user_facing_front_door` | Front door | Interactive users talk to the agent directly (chat, Microsoft 365, Teams, a web app). |
| `front_door_must_be_platform_governed` | Front door | The user-facing layer must keep platform-managed guardrails, tools, and conversation storage. |
| `heavy_backend_processing` | Experience | Heavy or long-running processing happens behind the conversation. |
| `latency_sensitive_first_turn` | Experience | The first response must feel immediate to an interactive user. |
| `gateway_must_be_enforced` | Governance | All model calls must go through governed deployments or an approved AI gateway. |
| `private_network_required` | Governance | The project must run with private networking and controlled egress. |
| `runs_untrusted_code` | Governance | The agent executes model-generated or user-supplied code. Neutral for scoring; always raises a session-pool warning. |
| `per_user_delegation` | Governance | Tools must act as the signed-in user. Neutral for scoring; always raises a delegation warning. |
| `team_can_operate_containers` | Operating model | The team can own a container supply chain: builds, scanning, patching, registry, egress rules, and on-call. Set `false` if not. |

The input file can hold a single profile object, a list of profiles, or an envelope:

```json
{
  "profiles": [
    {
      "name": "contoso-store-policy-assistant",
      "description": "Store associates ask about return and pricing policy in Teams; answers cite approved policy documents.",
      "user_facing_front_door": true,
      "latency_sensitive_first_turn": true,
      "gateway_must_be_enforced": true
    }
  ]
}
```

[`example-profiles.json`](example-profiles.json) contains three illustrative profiles (a policy assistant, a maintenance planner with a proprietary optimizer, and an advisor assistant with heavy analytics behind a Microsoft 365 front door). They use fictitious organization names. Copy one and edit it for your workload.

## Read the output

Abbreviated text output for the third example profile:

```text
Workload: northwind-advisor-assistant
Recommendation: MIXED
Scores: prompt=5 hosted=-2 mixed=10 (eligible: prompt, hosted, mixed)
Named controls: in_process_libraries
Rationale:
  - [in_process_libraries] Proprietary, native, or licensed libraries must run inside the loop, which only your own process can do.
  - [front_door_must_be_platform_governed] The user-facing layer must keep platform-managed guardrails, tools, and conversation storage.
  - ...
  - Recommended 'mixed': highest score among eligible runtimes (prompt, hosted, mixed).
Warnings:
  - Per-user access: use MCP or Toolbox OAuth, channel on-behalf-of support, or a server-side context store. ...
  - Hosted agent outbound traffic is open unless you restrict it. ...
  - In a mixed design, let exactly one layer plan and the other execute, and propagate trace context across the tool boundary.
```

With `--json`, each profile produces one line with `profile_name`, `runtime`, `scores`, `eligible`, `named_controls`, `rationale`, and `warnings`.

## How the selector decides

1. **Score.** Every runtime starts from a base score (prompt starts ahead because it is the default). Each criterion that applies adds its weights. Run `--list-criteria` to see them.
2. **Check eligibility.** These rules encode ADR 0001:
   - `prompt` is always eligible.
   - `hosted` is eligible only if at least one **named control** is `true`.
   - `mixed` is eligible only if a named control is `true` **and** a front-door field (`user_facing_front_door` or `front_door_must_be_platform_governed`) is `true`.
3. **Pick.** The highest-scoring eligible runtime wins. Ties go to the runtime with the smaller operational footprint: prompt, then mixed, then hosted.
4. **Warn.** The selector adds warnings when:
   - the highest raw score belonged to an ineligible runtime (for example, "a hosted agent that only wraps a prompt");
   - the top two eligible runtimes are within a small margin (a close call that needs human review);
   - a prompt agent is recommended while named controls or deterministic sub-steps are present;
   - a hosted or mixed design meets latency sensitivity, private networking, an enforced gateway, or a team that cannot operate containers;
   - the workload runs untrusted code (use a session pool in every runtime) or needs per-user delegation (never pass user tokens through prompts or tool parameters).

Warnings are as important as the recommendation. A `hosted` result with an egress warning is only correct if you also apply the egress control.

## Adapter seams

The selector separates *where profiles come from* and *where results go* from the scoring logic, so you can plug it into a review process without touching the rubric.

| Seam | Implementations | Notes |
|---|---|---|
| `ProfileSource` (Protocol with `load()`) | `InMemoryProfileSource`, `JsonFileProfileSource` | Add a source that reads intake forms from your architecture review tool or backlog. |
| `ResultSink` (Protocol with `emit()`) | `InMemorySink`, `StdoutSink(as_json)` | The in-memory sink backs the self-test. |
| `AdrRepositorySink` | Production stub; raises `NotImplementedError` | A real implementation would render an ADR draft and open a pull request or attach it to a review work item, authenticating with a managed or workload identity, never a personal token. |

`run(source, sink)` wires a source to a sink and returns the recommendations, so unit tests can assert on results without capturing standard output.

## Extend it

- **Add a scenario.** Append a `Scenario` to `SCENARIOS` with a profile, the expected runtime, and any warning substrings you expect. Re-run `--self-test`.
- **Tune weights.** Edit the `RUBRIC` entries. Keep the eligibility rules intact; they are the policy, and the weights are only the preference within that policy. Re-run `--self-test` after every change so the reference scenarios still hold.
- **Add a criterion.** Add a boolean field to `WorkloadProfile`, a `Criterion` to `RUBRIC`, and a row to the field table above. If the new field is a hard reason to own code, add it to `NAMED_CONTROLS` and to the decision matrix in `docs/architecture.md`.

## Limitations

- The rubric reflects the forces described in this asset. Your organization may weight them differently; record the reason when you diverge.
- Hosted agents, hosted-agent protocols, Toolbox, and some identity and networking features are in preview, and their capabilities change. Check current Microsoft Learn documentation before you treat a criterion as a hard constraint.
- The selector does not measure anything. Validate the chosen runtime with the evaluation scenarios and release gates in [`docs/evaluation-and-troubleshooting.md`](../docs/evaluation-and-troubleshooting.md).

## Related

- [Asset overview](../README.md)
- [Architecture and decision matrix](../docs/architecture.md)
- [Authentication and governance](../docs/authentication-and-governance.md)
- [ADR 0001: Default to prompt agents, escalate to hosted agents](../docs/decisions/0001-default-to-prompt-agents-escalate-to-hosted.md)
- [Production hosted agents on Foundry](../../production-hosted-agents-on-foundry/README.md)
- [Code execution sandbox with session pools](../../code-execution-sandbox-session-pools/README.md)
