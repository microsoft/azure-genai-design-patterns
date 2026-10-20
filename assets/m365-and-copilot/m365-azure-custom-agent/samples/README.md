# Reference samples: custom engine agent runtime

These four Python modules make the patterns in [`../docs`](../docs) runnable. Each one is a
self-contained reference implementation of one runtime concern. Each ships with an offline
self-test that runs with no Azure subscription, no network, and no secrets.

They are **reference implementations, not a framework**. Copy the seams and contracts you need
into your own host (Microsoft 365 Agents SDK, Bot Framework, or Azure Container Apps worker),
then replace the in-memory fakes with the production adapters that already exist in each file.

| Module | Concern | Doc it implements | Self-test coverage |
|---|---|---|---|
| [`planner_specialist_router.py`](planner_specialist_router.py) | Planner → specialist routing, multi-intent decomposition, handoff contract, loop and budget guards, routing evaluation | [architecture.md](../docs/architecture.md), [evaluation-and-routing-quality.md](../docs/evaluation-and-routing-quality.md) | 13 checks × 4 domain packs |
| [`cosmos_state_and_cache.py`](cosmos_state_and_cache.py) | Per-user conversation state, ETag concurrency, transactional turn commit, pre-compute cache, time-zone-aware proactive dispatch | [state-caching-and-proactive-ops.md](../docs/state-caching-and-proactive-ops.md) | 8 scenario groups |
| [`obo_mcp_client.py`](obo_mcp_client.py) | Inbound token validation, On-Behalf-Of exchange with a federated credential, CAE claims challenges, Streamable HTTP MCP client, two-user isolation harness | [authentication-and-identity.md](../docs/authentication-and-identity.md) | 6 scenario groups |
| [`side_effect_gate.py`](side_effect_gate.py) | R/W1–W4 side-effect classification, approvals bound to payload hashes, fan-out caps, daily quotas, kill switch, audit trail | [architecture.md](../docs/architecture.md) § Safety | 26 scenarios |

All four modules are domain-agnostic. The router ships with four **domain packs** (CRM/sales,
IT and security operations, financial risk, and customer service case management) that reuse
one planner, one handoff contract, and one tool-invocation path. Only vocabulary, entity names,
and specialist cards change between packs. See [Adapting to a new domain](#adapting-to-a-new-domain).

## How the modules compose into one runtime

```text
 Teams / Microsoft 365 Copilot / Outlook
            │  Activity + SSO token (Bot Framework / Agents SDK channel)
            ▼
 ┌───────────────────────────────────────────────────────────────────────────┐
 │ Host turn handler (your code: Agents SDK app, Container Apps revision)     │
 │                                                                           │
 │  1. InboundTokenValidator.validate()           obo_mcp_client.py          │
 │       → Principal(tenant_id, object_id, scopes)                           │
 │  2. StateManager.begin_turn(tid, oid, conv, activity_id)                  │
 │       → TurnHandle (session doc + ETag; duplicate activity → no-op)       │
 │                                                cosmos_state_and_cache.py  │
 │  3. Planner.handle(request)                    planner_specialist_router  │
 │       RoutingModel → RoutingDecision → Specialist → HandoffEnvelope       │
 │       ToolInvoker (allow-list, call budget, evidence refs)                │
 │            │                                                              │
 │            ▼                                                              │
 │  4. SideEffectGate.check(ActionRequest)        side_effect_gate.py        │
 │       R / W1 / W2 (staged drafts) pass policy · W3+ sends need an owned   │
 │       draft + approval token bound to principal, tool, payload hash       │
 │            │                                                              │
 │            ▼                                                              │
 │  5. OboTokenBroker.exchange() → StreamableHttpMcpClient.call_tool()       │
 │       user-delegated token per downstream MCP server   obo_mcp_client.py  │
 │                                                                           │
 │  6. StateManager.commit(handle, outcome=…)     one transactional batch:   │
 │       session replace (If-Match) + turn record + conversation reference   │
 └───────────────────────────────────────────────────────────────────────────┘

 Scheduled worker (Container Apps job / Functions timer)
   run_precompute_wave() → BriefingCache.put_precomputed()   per scope_id
   ProactiveDispatcher.tick(now_utc) → local_window_open(tz) → ContinueConversation
```

**Data-flow guarantees the composition relies on:**

- **Identity enters once.** Steps 1–5 use the same `tenant_id` + `object_id`. The state
  partition key, the OBO cache key, and the gate's quota counters all derive from it, so one
  user's turn never reads or writes under another user's key.
- **Raw tool rows never reach prompts.** `ToolInvoker` keeps only the per-tool contract fields
  plus an evidence reference (`ev:<tool>:<sha12>`). The self-test asserts that the 250 raw rows
  each fake tool returns never appear in any prompt.
- **Writes are deny-by-default.** The router can only *request* a write. The gate decides, and the
  kill switch can stop every write (or one tool) without a redeploy of the planner.
- **State commits once per turn.** Specialists mutate an in-memory `TurnHandle`. The single
  `commit()` either lands all documents atomically or raises `ConcurrentTurnError`. The host
  then replays the turn against the fresh session, not the stale one.

## Interfaces you implement (the seams)

| Seam (Protocol / callable) | Module | In-memory fake (tests) | Production adapter (in file) |
|---|---|---|---|
| `RoutingModel.route()` | router | `KeywordRoutingModel`, `MisroutingModel` | `AzureOpenAIRoutingModel` (structured outputs, `strict` JSON schema) |
| Toolbox (`call(tool, args)`) | router | `SemanticToolbox` | Wrap `StreamableHttpMcpClient.call_tool()` |
| `DocumentStore` | cosmos | `InMemoryDocumentStore` (ETag, TTL, batch semantics) | `CosmosDocumentStore` (`azure-cosmos` async, `DefaultAzureCredential`) |
| `ProactiveSender` | cosmos | `_RecordingSender` | `BotConnectorSender` (`ContinueConversation`) |
| `TokenCache` | obo | `MemoryTokenCache` | Distributed cache keyed by `obo_cache_key()` (hashes only) |
| Client assertion | obo | Fake identity platform | `managed_identity_client_assertion()` (federated identity credential, no secret) |
| `CounterStore`, `ApprovalStore`, `DraftStore`, `AuditSink` | gate | `InMemory*`, `ListAuditSink` | Cosmos DB containers or Table storage; audit to Log Analytics |

## Configuration and environment variables

The samples take configuration through **constructor arguments**, which keeps the self-tests
hermetic. Two variables are read directly. Everything else is read by **your host's composition
root** and passed in. Use managed identity everywhere. None of the settings below is a secret.

| Variable | Read by | Purpose |
|---|---|---|
| `AGENT_MCP_WRITES_ENABLED` | `side_effect_gate.KillSwitch` (every call) | Global write switch. **Fail-closed:** writes are refused unless set to `1`/`true`/`yes`/`on`. |
| `AGENT_MCP_DISABLED_TOOLS` | `side_effect_gate.KillSwitch` (every call) | Comma-separated tool names to disable without stopping all writes. |
| `AZURE_OPENAI_ENDPOINT` | host → `AzureOpenAIRoutingModel(endpoint=…)` | Routing and synthesis model endpoint. |
| `AZURE_OPENAI_DEPLOYMENT` | host → `AzureOpenAIRoutingModel(deployment=…)` | Small, fast deployment for routing. Use a separate deployment for synthesis. |
| `AZURE_OPENAI_API_VERSION` | host → `AzureOpenAIRoutingModel(api_version=…)` | Defaults to `2024-10-21`. |
| `COSMOS_ENDPOINT`, `COSMOS_DATABASE` | host → `CosmosDocumentStore` | Account endpoint and database that hold `agent_state` and `precompute_cache`. |
| `ENTRA_TENANT_ID` | host → `InboundPolicy`, `OboSettings.tenant_id` | Home tenant, used as the issuer check and the OBO authority. |
| `AGENT_APP_CLIENT_ID` | host → `OboSettings.client_id`, `InboundPolicy` audience | The agent's Entra app registration (the bot ID). |
| `AZURE_CLIENT_ID` | host → `managed_identity_client_assertion()` | User-assigned managed identity that backs the federated identity credential. |
| `MCP_SERVER_URL`, `MCP_SCOPE` | host → `StreamableHttpMcpClient`, `OboTokenBroker.exchange(scopes=…)` | Downstream MCP endpoint and its delegated scope, for example `api://<mcp-app-id>/Tools.Invoke`. |
| `OAUTH_CONNECTION_NAME` | host → `build_oauth_card_activity()` | Azure Bot OAuth connection used for Teams SSO and consent cards. |
| `APPLICATIONINSIGHTS_CONNECTION_STRING` | host → OpenTelemetry distro | Exports the GenAI spans the planner emits (`invoke_agent`, `execute_tool`, `handoff`). |

`OboSettings` also requests the `CP1` client capability by default. That opts the agent into
Continuous Access Evaluation, so a revoked session surfaces as a claims challenge, not a silent
hour-long token. See [authentication-and-identity.md](../docs/authentication-and-identity.md).

## Running the self-tests

Python 3.11+ is required. The offline self-tests use the standard library only. On Windows,
`cosmos_state_and_cache.py` needs `pip install tzdata` for `zoneinfo`.

```powershell
cd assets/m365-and-copilot/m365-azure-custom-agent/samples

python planner_specialist_router.py                 # sales pack, 13 checks
python planner_specialist_router.py --all-domains   # every domain pack
python planner_specialist_router.py --domain secops --trace   # print the span tree
python planner_specialist_router.py --otel          # also export spans via OpenTelemetry console exporter

python cosmos_state_and_cache.py                    # 8 scenario groups + store-call counts
python obo_mcp_client.py                            # 6 scenario groups + identity-platform call count
python side_effect_gate.py                          # 26 scenarios + audit summary
```

Each module exits non-zero on the first failed assertion (the router prints `FAIL` per check
and exits `1`), so you can wire them straight into CI as a smoke stage. Delete the generated
`__pycache__/` folder before you publish a copy.

### What each self-test proves

| Module | Scenarios asserted |
|---|---|
| Router | Single-intent routing. Multi-intent decomposition into an ordered path. Misroute → `HandoffEnvelope(out_of_domain)` → re-route. Ping-pong loop detection. Hop budget. Permission refusal is **not** re-routed and leaks no entity name. Cross-specialist scrubbing of raw rows. Honest partial failure. Deadline → follow-up. Clarify on out-of-scope and ambiguous input. Strict handoff schema (bad enum and extra field rejected). Telemetry carries hashes, not user text. Routing precision/recall ≥ 0.9 per capability. |
| State and cache | First turn writes session + turn + conversation reference in **one** transactional batch. Two replicas racing on one session → exactly one ETag conflict (`412`), mutations replayed, no lost update. Duplicate activity → early exit on read, `409` on a racing commit, counted once. Lazy v1 → v2 schema upgrade on read. Cache: unentitled scope refused, single-flight (5 concurrent misses → 1 live refresh), stale served with a banner while refresh-behind re-warms it, expired + failed refresh → explicit "unavailable". Pre-compute skips the LLM when the input content hash is unchanged. Dispatch: DST-correct local windows, 2 replicas → 1 send, uninstalled-app reference removed, re-tick is a no-op. |
| OBO and MCP | Inbound validation accepts v1 and v2 issuers with clock leeway and rejects 7 attack shapes. OBO cache bound to the inbound assertion (20 concurrent misses → 1 exchange, per-user purge). 7 Entra failure shapes mapped to a remediation, and step-up cards are sent only on interactive turns. MCP client: SSE with progress, a revoked token re-exchanged once, CAE step-up, session re-initialization. Two-user isolation with the cross-probe denied. Zero raw tokens across 15 log lines. |
| Side-effect gate | Reads pass, and unknown tools fail closed. A send needs a draft that exists, is owned by the caller, and matches the tool family. The approval token is bound to principal, tool, and payload hash, expires, and is single-use (replay refused). Recipient allow-list. Unattended W3 refused, unattended W2 allowed, and unattended principals cannot mint approvals. Fan-out cap and daily cap. Per-tool disable, and the global kill switch still lets reads through. 18 refusals audited with safe context keys only. |

## Adapting to a new domain

1. **Add a `DomainPack`** in `planner_specialist_router.py`. Supply the entity kind, an example
   entity, a restricted entity (for the permission probe), the stakeholder noun, keywords per
   capability, and four `SpecialistCard`s. Keep the four capabilities (`status`, `recommend`,
   `communicate`, `brief`) unless your domain truly needs another one. Every new capability adds
   a routing class you must evaluate.
2. **Map tools to capabilities** in `CAPABILITY_TOOLS` and give each tool a contract (the fields
   allowed into the prompt). Classify each tool's side effect in
   `side_effect_gate.default_policies()`.
3. **Add labeled routing cases** (`labeled_routing_cases`) and run `--domain <yours>`. Precision
   and recall must hold ≥ 0.9 per capability before the pack ships. See
   [evaluation-and-routing-quality.md](../docs/evaluation-and-routing-quality.md).
4. **Choose `scope_id`s** for pre-computed content, such as a team, queue, region, or portfolio.
   Never use a single user. Per-user data belongs in `agent_state`, not the shared cache.

| Pattern element | CRM / Sales | ITSM / SecOps | HR / Operations |
|---|---|---|---|
| Entity | Account, opportunity | Incident, change, asset | Case, requisition, site |
| `status` specialist | Account health | Incident triage | Case status |
| `recommend` specialist | Next best action | Remediation planner | Policy or resolution advisor |
| `communicate` specialist (W2 draft) | Outreach drafter | Stakeholder comms | Employee reply drafter |
| `brief` specialist | Meeting prep | Shift handover | Daily operations brief |
| Pre-compute `scope_id` | Sales territory | Resolver group / on-call rota | HR region / site |
| Typical W3+ action (gated) | Send email, update stage | Close incident, isolate host | Update record, notify employee |

## Production hardening checklist (beyond the samples)

- Replace every `InMemory*` store with its durable adapter, and run the same self-test scenarios
  against the Cosmos DB emulator in CI.
- Pin the routing model deployment and version. Re-run the routing evaluation on any model, prompt,
  or domain-pack change (see the release gates in the evaluation doc).
- Send the gate's audit events and the planner's spans to the same Log Analytics workspace, and
  correlate them by `turn_id`.
- Load-test the proactive dispatcher against the Bot Connector rate limits, and honor `Retry-After`
  on `429`.
