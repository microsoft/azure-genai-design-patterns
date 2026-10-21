# Samples: Code Execution Sandbox Simulator

`sandbox_session_sim.py` is a single-file, standard-library-only simulator of the control
plane described in [architecture.md](../docs/architecture.md). It models one fictional
Contoso analysis agent that must run model-generated code: the broker that sits between
the agent and the sandbox, a session pool with per-conversation sessions, the caller's
role assignment on the pool, the egress policy, file transfer, session lifecycle, pool
selection, output handling, approvals and audit.

Use it to read the logic, to argue about edge cases with your platform and security
teams, and as an executable specification for the tests you write against your own
deployment. Each scenario is a short function that asserts one behaviour from the docs
or an ADR.

> **Test only.** The simulator **never executes submitted code on the host**. "Code" is a
> tiny line-oriented mini-language that is parsed and walked by a deterministic
> interpreter with step, process and memory budgets. Budgets are abstract units, and
> every time value is a logical clock tick: ticks only order events and say nothing about
> real cold start, latency, capacity or cost. There is no network call anywhere in the
> file. Role names, API paths, field names, pool types, warm-instance behaviour, MCP
> availability and isolation properties model behaviour observed in preview. Verify them
> against current Microsoft Learn documentation before you rely on them.

## Prerequisites

- Python 3.10 or later. No packages to install.

## Run it

From the repository root:

```powershell
python assets/agent-runtime/code-execution-sandbox-session-pools/samples/sandbox_session_sim.py --self-test
python assets/agent-runtime/code-execution-sandbox-session-pools/samples/sandbox_session_sim.py --demo
```

- `--self-test` runs every scenario. It prints `PASS <scenario>` or
  `FAIL <scenario>: <reason>` for each one, then `N/34 scenarios passed`. It exits 0
  when all pass and 1 otherwise.
- `--demo` (also the default with no argument) prints a readable trace in 15 steps: pool
  type discovery, pool selection for four workloads, session identifier derivation,
  reuse within a conversation, isolation from another user, blocked egress and metadata
  probes, a rejected traversal upload, output truncation, a step-budget termination,
  state loss after cooldown, the MCP gap on a custom-container pool, an MCP tool
  descriptor on the built-in pool, an approval ticket, the in-process refusal with its
  exposure analysis, and a sample audit record.

The demo never prints a derivation key, a token or a code body.

## The fictional world

All names are placeholders.

| Constant | Value | Stands in for |
| --- | --- | --- |
| `TENANT_CONTOSO`, `TENANT_FABRIKAM` | `tenant-contoso`, `tenant-fabrikam` | Two customer tenants served by the same agent |
| `USER_A`, `USER_B` | `contoso-analyst-1`, `contoso-analyst-2` | Two end users (the same user IDs are reused in the Fabrikam tenant on purpose) |
| `CONV_SALES`, `CONV_FORECAST` | `conv-sales-review`, `conv-forecast` | Two conversations |
| `U1_SALES`, `U1_FORECAST`, `U2_SALES`, `U3_SALES`, `FAB_U1_SALES` | `EndUser` tuples | Tenant, user and conversation combinations used by scenarios |
| `BROKER_IDENTITY` | `mi-contoso-analysis-broker` | The managed identity that holds the session executor role on the pool |
| `OTHER_IDENTITY` | `mi-unrelated-workload` | A workload identity with no role on the pool |
| `ALLOWED_DATA_HOST` | `data.contoso.example` | The one host an allowlist variant may reach |
| `EXFIL_HOST` | `collector.attacker.example` | Where injected code tries to send data |
| `pool-contoso-analysis` | Pool name | A session pool; scope is built from placeholder subscription and resource group values |

Regions in `FakeResourceProvider`: `region-a` offers `PythonLTS`, `CustomContainer`,
`NodeLTS` and `Shell`; `region-b` offers `PythonLTS` and `CustomContainer`. The built-in
image has `numpy`, `pandas` and `matplotlib`; the custom image adds `polars` and
`pyarrow`. Token scope: `https://dynamicsessions.io/.default` (verify).

Default limits, all in simulated units: step budget 200, eight processes, 64 memory
units, code up to 2048 bytes, stdout up to 512 bytes before truncation, cooldown five
ticks, maximum lifetime 20 ticks, four concurrent sessions. They are deliberately small
so the scenarios hit them quickly. They are not recommendations.

Mini-language operations: `set`, `add`, `mul`, `print` with `$var` expansion,
`repeat N` or `repeat forever` ... `end`, `fetch <url>`, `write <path> <text>`,
`read <path>`, `emit <path> <mime> <size>`, `spawn <n>`, `alloc <units>`,
`getenv <name>`, `sha256 <text>`, `import <package>` and `propose <action> <args>`.

## Scenario catalog

| # | Scenario | What it proves | Where it is documented |
| --- | --- | --- | --- |
| 1 | `same_user_and_conversation_reuse_one_session` | One conversation maps to one session and keeps state; another conversation starts clean. | [architecture](../docs/architecture.md) section 8.1, [ADR 0002](../docs/decisions/0002-one-session-per-user-conversation-egress-off-by-default.md) |
| 2 | `different_users_never_share_a_session` | Two users in the same conversation ID get different sessions and cannot read each other's files or variables. | [architecture](../docs/architecture.md) sections 3 and 8.1 |
| 3 | `tenants_are_isolated_even_with_identical_ids` | Same user and conversation IDs in another tenant derive a different identifier; separator smuggling is rejected; key rotation remaps every identifier. | [architecture](../docs/architecture.md) section 8.1, [governance](../docs/authentication-and-governance.md) |
| 4 | `client_supplied_session_id_is_rejected` | A caller or model cannot pass a session identifier, even a valid one. | [architecture](../docs/architecture.md) section 8.1 |
| 5 | `session_id_is_opaque_and_within_bounds` | Identifiers are stable, contain no raw identity values and respect observed length and charset bounds. | [architecture](../docs/architecture.md) section 8.1 |
| 6 | `caller_without_token_or_role_is_refused` | No role gives 403, a wrong audience gives 401, an unrelated identity gives 403 and an anonymous call is refused. | [governance](../docs/authentication-and-governance.md) |
| 7 | `egress_is_blocked_by_default` | Outbound calls fail when egress is disabled, even to trusted hosts, and are counted in audit. | [architecture](../docs/architecture.md) section 11, [ADR 0002](../docs/decisions/0002-one-session-per-user-conversation-egress-off-by-default.md) |
| 8 | `allowlist_permits_only_the_exact_listed_host` | Only the exact host over HTTPS passes; look-alike hosts, plain HTTP, userinfo and wildcard entries are rejected. | [architecture](../docs/architecture.md) section 11 |
| 9 | `metadata_and_local_addresses_always_blocked` | Metadata, wire server, loopback and encoded variants are denied in every mode and cannot be allowlisted. | [architecture](../docs/architecture.md) section 11, [evaluation](../docs/evaluation-and-troubleshooting.md) |
| 10 | `upload_path_traversal_is_rejected` | Traversal, absolute, encoded and drive paths are rejected before the pool; MIME and size limits apply. | [architecture](../docs/architecture.md) section 8.3 |
| 11 | `file_round_trip_stays_inside_the_session` | Uploads land under the session data root, outputs download back, and another user cannot fetch them. | [architecture](../docs/architecture.md) section 8.3 |
| 12 | `oversized_output_is_truncated_and_flagged` | Large stdout is capped with a visible marker and the truncation is audited. | [architecture](../docs/architecture.md) section 12 |
| 13 | `secrets_in_output_are_redacted` | Credential-shaped output is redacted before it reaches the model. | [architecture](../docs/architecture.md) section 12 |
| 14 | `disallowed_artifact_types_are_quarantined` | HTML and oversized artifacts are quarantined; an allowlisted PNG is delivered. | [architecture](../docs/architecture.md) section 12 |
| 15 | `step_budget_terminates_runaway_loop` | An infinite loop ends at the step budget with an explicit reason, and the session survives. | [architecture](../docs/architecture.md) section 3, [evaluation](../docs/evaluation-and-troubleshooting.md) |
| 16 | `process_and_memory_budgets_contain_fork_bomb` | Fork bombs and memory hogs hit their limits inside one session; other users keep working. | [evaluation](../docs/evaluation-and-troubleshooting.md) |
| 17 | `cooldown_expiry_recreates_session_and_surfaces_state_loss` | After cooldown the session is gone, the next call gets a new one, and state loss is reported to the model and audit. | [architecture](../docs/architecture.md) section 9 |
| 18 | `max_lifetime_expiry_and_explicit_delete` | Maximum lifetime recycles busy sessions; delete at conversation end is idempotent, audited and not reported as loss. | [architecture](../docs/architecture.md) section 9 |
| 19 | `capacity_exhaustion_fails_fast_then_recovers` | A full pool fails fast with a retryable 429; ending a conversation or cooldown frees a slot. | [architecture](../docs/architecture.md) section 10, [troubleshooting](../docs/evaluation-and-troubleshooting.md) |
| 20 | `unpinned_identifiers_mint_a_session_per_call` | Anti-pattern: a new identifier per call loses state and burns capacity; a pinned identifier uses one slot. | [architecture](../docs/architecture.md) section 16, [troubleshooting](../docs/evaluation-and-troubleshooting.md) |
| 21 | `custom_packages_select_custom_pool_and_mcp_is_rejected` | Custom packages select a custom-container pool; the MCP flag is dropped on read-back and an MCP request gets 409. | [architecture](../docs/architecture.md) section 10, [ADR 0003](../docs/decisions/0003-built-in-pool-for-mcp-custom-container-for-packages.md) |
| 22 | `mcp_need_selects_builtin_pool` | An MCP need selects the built-in pool; the descriptor pins the identifier and references a Key Vault secret. | [ADR 0003](../docs/decisions/0003-built-in-pool-for-mcp-custom-container-for-packages.md) |
| 23 | `packages_plus_mcp_need_own_tool_front` | Custom packages plus MCP means your own MCP or OpenAPI front over the data plane. | [architecture](../docs/architecture.md) section 10 |
| 24 | `warm_setting_ignored_on_managed_pool_and_zero_rejected_on_custom` | The warm setting does not stick on a managed pool; a custom pool rejects zero; a non-root image is flagged. | [architecture](../docs/architecture.md) sections 10 and 19 |
| 25 | `pool_types_come_from_the_resource_provider` | Pool types differ by region and the provider's error lists the valid ones. | [architecture](../docs/architecture.md) section 10 |
| 26 | `in_process_execution_is_refused` | The broker refuses an in-process backend; exposure analysis shows what in-process code would reach, without running it. | [ADR 0001](../docs/decisions/0001-never-execute-generated-code-in-process.md) |
| 27 | `secrets_in_code_are_flagged_before_execution` | Credential-shaped code is rejected before the pool and the value is not copied into audit. | [architecture](../docs/architecture.md) section 5, [governance](../docs/authentication-and-governance.md) |
| 28 | `oversized_code_is_rejected_before_execution` | Code over the size limit gets 413 and never reaches the pool. | [architecture](../docs/architecture.md) section 8.2 |
| 29 | `sandbox_has_no_agent_credentials` | Identity endpoints and agent settings do not exist inside the session; the broker requests only the sessions scope. | [ADR 0001](../docs/decisions/0001-never-execute-generated-code-in-process.md), [governance](../docs/authentication-and-governance.md) |
| 30 | `side_effecting_output_goes_to_approval` | A proposed change becomes an approval ticket correlated to the execution; nothing is applied. | [architecture](../docs/architecture.md) section 14 |
| 31 | `audit_records_have_correlation_ids_and_no_code_bodies` | Every call is audited with its own correlation ID, hashed identities and a code hash, never the code. | [architecture](../docs/architecture.md) section 18 |
| 32 | `answer_without_execution_evidence_fails_eval` | Numbers in an answer must appear in a succeeded execution's output. | [evaluation](../docs/evaluation-and-troubleshooting.md) |
| 33 | `live_adapter_stays_gated` | The live adapter refuses to construct without the opt-in and never pretends to work. | This page, "Gated live adapter" |
| 34 | `simulator_never_executes_submitted_code_on_host` | The interpreter only walks a parsed tree; host-level calls in submitted text are inert. | This page, "Test only" |

## Adapter seams

The fakes sit behind `typing.Protocol` seams so you can see where a real adapter plugs in.

| Protocol | Fake in the simulator | Production counterpart |
| --- | --- | --- |
| `SessionPool` | `InMemorySessionPool` (and the refused `InProcessExecutor`) | `AcaSessionPoolAdapter`, a gated stub you implement against the current session pool data plane |
| `TokenProvider` | `StaticTokenProvider` | Managed identity credential for the broker, scoped to the sessions audience only |
| `DerivationKeyProvider` | `StaticDerivationKeyProvider` | A Key Vault secret holding the HMAC key, with versioned rotation |
| `ApprovalGate` | `InMemoryApprovalGate` | Your approval workflow, for example [Verify Before Approve](../../../orchestration-and-workflows/verify-before-approve/README.md) |
| `AuditSink` | `AuditLog` | Structured logs or an append-only store with retention |

`FakeResourceProvider`, `LogicalClock` and `RoleAssignments` stand in for the resource
provider, the wall clock and Azure RBAC.

## Gated live adapter

`AcaSessionPoolAdapter` raises `LiveAdapterGated` (a `NotImplementedError`) unless
`SANDBOX_SIM_LIVE=1` is set, and every method raises `NotImplementedError` even then. It
makes no network calls. It marks where real data-plane calls belong: token acquisition for
the sessions scope, the execute call with the pinned identifier, the files API, delete and
the MCP endpoint lookup. Build a real one from the Azure Container Apps dynamic sessions
documentation and the API version you pin, keep it behind the same `SessionPool` seam,
and keep it out of unit tests.

## Extend it

- **Add a scenario.** Write a `scenario_<name>()` function that calls `build_world(...)`,
  drives `w.broker`, and asserts with `check(...)` and `expect_error(...)`. The runner
  discovers every function whose name starts with `scenario_`.
- **Change the policy.** Pass `egress=EgressPolicy(mode="allowlist", allow_hosts=(...))`,
  `policy=BrokerPolicy(...)` or `limits=ExecutionLimits(...)` to `build_world` to rehearse
  a policy change before you make it.
- **Change the grants.** Use `build_world(grant_role=False)` or `principal=...` to rehearse
  a missing or wrong role assignment.
- **Port a scenario to a live test.** Keep the assertion, swap the fake pool for your
  adapter, and run it against a non-production pool with egress disabled.

## Limitations

- It does not model Hyper-V isolation, container images, networking, Microsoft Entra ID
  or the session pool APIs. It models the decisions and contracts around them.
- The mini-language is not Python. Real generated code has far more ways to reach the
  network, the filesystem and the process table; the sandbox, not the pre-screen, is the
  control.
- Request, response and error shapes are simplified and partly hypothetical. The preview
  API may differ in field names, paths, status codes and behaviour.
- Role checks are exact matches on principal, role and scope. Real Azure RBAC inherits
  down the scope hierarchy and supports custom roles and conditions.

## Related

- [Asset README](../README.md)
- [Architecture](../docs/architecture.md)
- [Authentication and governance](../docs/authentication-and-governance.md)
- [Evaluation and troubleshooting](../docs/evaluation-and-troubleshooting.md)
- [Production Hosted Agents on Microsoft Foundry](../../production-hosted-agents-on-foundry/README.md)
- [Verify Before Approve](../../../orchestration-and-workflows/verify-before-approve/README.md)
