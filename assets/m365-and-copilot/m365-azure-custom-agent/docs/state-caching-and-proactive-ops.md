# State, caching, and proactive operations

> **Pattern scope:** a domain-agnostic state and caching design for a custom engine agent in
> Microsoft 365 (Teams, Microsoft 365 Copilot, Outlook) that runs on horizontally scaled Azure
> compute. The same design serves a CRM account assistant, an ITSM/SecOps incident copilot, or
> an HR/operations case agent. Only the `scope_id` vocabulary and the cached content change.
>
> **Runnable reference:** [`samples/cosmos_state_and_cache.py`](../samples/cosmos_state_and_cache.py)
> (8 offline scenario groups, standard library only).

## 1. The problems this design solves

A custom engine agent receives each user message as an independent HTTP request. It runs as
several stateless replicas, and the Microsoft 365 channel may redeliver the same activity. The
agent also has to *start* conversations, for example a morning briefing, at each user's own
local time. Naive designs fail in predictable ways:

| Requirement | Naive design | Failure it causes |
|---|---|---|
| Per-user isolation | One container keyed by conversation ID | A query bug or a guessed ID reads another user's history |
| Multi-replica concurrency | Read-modify-write with last-writer-wins | Two replicas handle two quick messages, and one turn's memory is silently lost |
| Idempotent redelivery | Process every inbound activity | A channel retry runs the turn, and its tool calls, twice |
| Bounded cost | Compute every briefing per user on request | Model spend grows linearly with the number of users opening the app at 09:00 |
| Freshness transparency | Serve whatever is in the cache | Day-old data is presented as current |
| Privacy retention | Keep transcripts indefinitely | Retention and deletion obligations cannot be met |
| Local-time proactive delivery | Cron at a fixed UTC hour | Briefings arrive at 03:00 for half the users and shift by an hour twice a year |

## 2. Dual-container architecture

The design separates **who the data belongs to** (one user) from **who may share it** (everyone
entitled to one scope). Each container gets a partition key and TTL policy that match its owner.

```text
                    ┌──────────────── Azure Cosmos DB (NoSQL API, one database) ───────────────┐
                    │                                                                          │
 interactive turn ─►│  agent_state            PK = ["/tenant_id", "/principal_oid"] (HPK)      │
                    │    session:<conv>          rolling summary, pinned entities, ledger      │
                    │    turn:<conv>:<activity>  idempotency + evaluation sample               │
                    │    convref:<conv>          Bot Framework conversation reference          │
                    │    delivery:<kind>:<conv>:<date>  exactly-once proactive claim           │
                    │                                                                          │
 scheduled wave  ──►│  precompute_cache       PK = "/scope_id"                                 │
                    │    <scope_id>:<kind>       briefing / digest / ranked list per scope     │
                    └──────────────────────────────────────────────────────────────────────────┘
```

| Property | `agent_state` | `precompute_cache` |
|---|---|---|
| Partition key | Hierarchical: `["/tenant_id", "/principal_oid"]` | `"/scope_id"`, for example territory, resolver group, region, or portfolio |
| Owner | Exactly one user in one tenant | Everyone entitled to the scope |
| Written by | The interactive turn (one batch per turn) and the dispatcher (claims) | The scheduled pre-compute wave and refresh-behind |
| Read by | The same user's turns and the dispatcher | Any entitled user's turn, after an entitlement check |
| TTL style | **Sliding** for sessions and references, **fixed** for turns and claims | **Fixed** from generation time |
| Contains model output? | Summaries only, never raw transcripts or raw tool rows | Generated briefing text plus evidence references |
| Isolation mechanism | A point read *requires* the full key `(tid, oid)`, which comes from the validated token | `scope_id` must appear in the caller's entitlement set before any read |

### Why a hierarchical partition key

- **Isolation by construction.** Every point read and every transactional batch needs the full
  partition key. The host takes `tenant_id` and `principal_oid` from the *validated* inbound
  token (see [authentication-and-identity.md](authentication-and-identity.md)), never from the
  message body. A forged conversation ID cannot cross users, because it lands in the wrong partition.
- **Atomic per-turn commits.** Cosmos DB transactional batches run within one logical partition.
  Session, turn record, and conversation reference share `(tid, oid)`, so a turn commits all of
  them atomically or none of them.
- **Tenant-scoped operations without fan-out.** Queries that filter only on `tenant_id`, such as
  tenant offboarding, a data-subject export, or a dispatcher sweep, are routed to the physical
  partitions that hold that tenant, not to every partition.
- **No hot tenant ceiling.** A single-level `/tenant_id` key caps a large tenant at one logical
  partition (20 GB). The second level spreads users across physical partitions.

> Hierarchical partition keys are fixed when the container is created. Decide them before your
> first deployment. Changing them later means a container migration.

### Why `/scope_id` for the cache

Most proactive and "what changed" content is identical for everyone who shares an entitlement
scope, such as every agent on one resolver group or every seller in one territory. Computing it
once per scope instead of once per user turns model spend from O(users) into O(scopes). The cache
**never** holds per-user content. If a briefing needs personalization (greeting, the user's own
tasks), compose it at read time from the shared scope document plus the user's `agent_state`.

## 3. Document model

| Document | `id` | Container | TTL (sample default) | Purpose |
|---|---|---|---|---|
| Session | `session:<conversation_id>` | `agent_state` | 30 days, **sliding** | Rolling summary, pinned entities, last N turns, tool ledger |
| Turn record | `turn:<conversation_id>:<activity_id>` | `agent_state` | 7 days, fixed | Idempotency key for redelivery, plus a weekly evaluation sample |
| Conversation reference | `convref:<conversation_id>` | `agent_state` | 90 days, sliding (refreshed every interaction) | Target for proactive `ContinueConversation` |
| Delivery claim | `delivery:<kind>:<conversation_id>:<local_date>` | `agent_state` | 3 days, fixed | Exactly-once proactive delivery across replicas |
| Briefing | `<scope_id>:<kind>` | `precompute_cache` | 36 hours, fixed | Pre-computed content, content hash, generator version |

A session document stays small and structured. It holds no transcript:

```json
{
  "id": "session:19:meeting_abc@thread.v2",
  "tenant_id": "11111111-1111-1111-1111-111111111111",
  "principal_oid": "aaaaaaaa-0000-0000-0000-00000000000a",
  "type": "session",
  "schema_version": 2,
  "channel_id": "msteams",
  "summary": "Discussed the at-risk item; user asked for a draft update to stakeholders.",
  "pinned_entities": [{ "kind": "case", "id": "CS-88412" }],
  "recent_turns": [{ "turn_id": "t-41", "capabilities": ["status", "communicate"], "outcome": "ok" }],
  "tool_ledger": [{ "tool": "create_draft", "side_effect": "W2", "status": "ok",
                    "evidence_ref": "ev:create_draft:1f2e3d4c5b6a" }],
  "updated_at": "2026-03-09T08:01:12Z",
  "ttl": 2592000
}
```

**Size discipline.** Request-unit (RU) cost scales with document size, and every turn reads and
replaces the session. The sample enforces a 48 KB session cap, keeps at most 6 recent turns and
20 ledger entries, and compacts oldest-first. Compaction runs *before* the batch is built, so an
oversized session never fails a turn. **Schema evolution** is lazy: `schema_version` is checked
on read, and older documents are upgraded in memory and persisted on the next commit. No
migration job is needed.

## 4. Turn lifecycle: read once, mutate in memory, commit once

```text
 begin_turn(tid, oid, conversation_id, activity_id)
   ├─ point read  turn:<conv>:<activity>     exists? → duplicate redelivery → early exit (no LLM, no tools)
   ├─ point read  session:<conv>             404 → new session document
   └─ TurnHandle(session, etag, mutations=[])

 planner / specialists run
   └─ handle.mutate(fn) …                    every state change is a recorded pure function

 commit(handle, outcome)                     ONE transactional batch in partition (tid, oid)
   ├─ replace  session:<conv>   If-Match: <etag>
   ├─ create   turn:<conv>:<activity>        409 → racing duplicate → counted once
   └─ upsert   convref:<conv>                keeps the proactive target fresh
```

Three rules keep this correct under concurrency:

1. **No intermediate writes.** Specialists never write to Cosmos DB directly. They call
   `handle.mutate(fn)`, and the planner owns the single commit. One turn costs one point read
   for the session, one for the duplicate check, and one batch.
2. **Mutations are pure and replayable.** Each mutation is a function of the session document
   alone. It does not depend on the tool results captured in a closure that would change on retry.
   That property is what makes `412` replay safe.
3. **Side effects come before the commit, not inside it.** External writes go through the side-effect
   gate and are recorded in the ledger by a mutation. A replayed commit re-applies the *record*,
   not the side effect itself.

## 5. ETag optimistic concurrency and `412` replay

Concurrent turns for the same user are normal. Users send two messages within a second, the
channel retries, two replicas pick up adjacent activities, and an interactive turn can overlap
a proactive delivery. The session replace carries `If-Match: <etag from begin_turn>`. If another
writer committed first, Cosmos DB rejects the whole batch with **412 Precondition Failed**.

```python
async def commit_with_replay(state, handle, outcome, max_replays=3):
    for attempt in range(max_replays + 1):
        try:
            return await state.commit(handle, outcome=outcome)
        except PreconditionFailed:
            if attempt == max_replays:
                raise ConcurrentTurnError(handle.conversation_id)
            fresh = await state.reload(handle)           # new session doc + new ETag
            for mutation in handle.mutations:            # replay the recorded pure functions
                mutation(fresh.session)
            handle = fresh                               # never re-run the LLM or the tools
```

**Replay the mutations, not the turn.** Re-running the planner on `412` doubles model cost,
can produce a different answer than the one already streamed to the user, and can repeat a
side effect. The sample's self-test races two replicas on one session and asserts exactly one
ETag conflict and no lost update.

| Status | Where | Meaning | Action |
|---|---|---|---|
| `404 Not Found` | Session read | First turn in this conversation | Create a new session document in the batch |
| `409 Conflict` | Turn-record create | Another replica already committed this activity | Treat as a duplicate. Count it once and send no second reply. |
| `412 Precondition Failed` | Session replace | Another turn committed first | Reload, replay the mutations, and retry within the bound. Then raise `ConcurrentTurnError` with a friendly "still working on your last message". |
| `413 Request Entity Too Large` | Batch | The session grew past the limit | Prevented by compaction before the batch. Treat any occurrence as a bug. |
| `429 Too Many Requests` | Any | RU budget exhausted | The SDK retries using `x-ms-retry-after-ms`. Alert on sustained 429s and do not raise the retry count. |

## 6. TTL strategy: sliding vs fixed

Cosmos DB measures an item's TTL from its **last modification time** (`_ts`). Every write
restarts the clock. That one rule gives you both behaviors without a cleanup job:

| Style | How you get it | Use it for | Sample value |
|---|---|---|---|
| **Sliding** | The document is rewritten on every interaction, so each write resets `_ts` | Sessions and conversation references. Active users never expire, and inactive users age out automatically. | Session 30 d, reference 90 d |
| **Fixed** | The document is written once and never updated | Turn records and delivery claims. They expire on schedule regardless of activity. | Turn 7 d, claim 3 d |
| **Fixed with read-time validity** | TTL bounds physical retention, and an explicit `valid_until` field bounds *serving* | Briefings. A briefing is rewritten every wave, so TTL alone would slide, and serving needs its own clock. | TTL 36 h, `max_stale` 36 h |

Implementation notes:

- Set the container's `DefaultTimeToLive` to `-1`. That turns TTL on but sets no default, so
  each document chooses its own `ttl`. A document without a `ttl` field never expires, so the
  sample sets `ttl` on every document type.
- Expired items stop appearing in reads and queries immediately. Physical deletion happens in the
  background and uses spare RUs only.
- Align retention with your privacy commitments. The turn-record TTL is your maximum retention
  for per-turn metadata. Pick it deliberately (for example, long enough for a weekly evaluation
  sample and no longer) and document it in your data-handling notes.
- The delivery-claim TTL must exceed the longest possible "today" across time zones (more than
  26 hours, plus catch-up). Otherwise a claim can expire while some zone is still on the same
  local date.

## 7. Pre-compute cache and stale-while-revalidate

Interactive reads of shared content go through one function, `BriefingCache.get(scope_id, kind,
entitled_scopes, live_refresh)`. It returns the payload plus an explicit **freshness** label
that the response must show:

```text
             entitled?  ── no ──► EntitlementError (never reveal whether the scope exists)
                │ yes
       fresh entry (now < valid_until and same generator_version)? ── yes ──► freshness = "fresh"
                │ no
       live refresh within refresh_budget (single-flight per scope+kind)?
                ├─ succeeded ──► write entry, freshness = "live"
                └─ failed / timed out
                       │
            stale entry within max_stale? ── yes ──► freshness = "stale" + banner, refresh-behind
                       │ no
                       └──► CacheUnavailable  → "I can't get current data right now" (no guessing)
```

| Mechanism | Why it matters |
|---|---|
| **Entitlement before read** | `scope_id` must be in the caller's entitlement set, which is resolved from their identity and not from the request text. Shared content never crosses an entitlement boundary. |
| **Single-flight** | Five users opening the app at once for the same cold scope trigger **one** live refresh, not five. The self-test asserts 5 concurrent misses → 1 refresh. |
| **Refresh budget** | A live refresh gets a hard time budget (seconds, not the full turn deadline). Past it, a stale answer with a banner beats a timed-out turn. |
| **Banner on stale** | The response states its age, for example "Showing results as of 2026-03-09 06:00 UTC; a live refresh did not complete." Users can tell current data from cached data. |
| **Refresh-behind** | Serving stale also schedules a background refresh, so the next reader gets fresh data. |
| **`generator_version`** | Bump it when you change the prompt, model, or output schema. Every older entry is then treated as a miss, which avoids serving content in a format the new code cannot render. |
| **Explicit unavailable** | When nothing safe exists, say so. Never fall through to the model "from memory". |

## 8. The pre-compute wave

A scheduled job (an Azure Container Apps job or an Azure Functions timer) generates shared
content per scope before users need it:

```text
for scope_id in scopes_due():                          # bounded concurrency (semaphore)
    inputs      = await fetch_inputs(scope_id)          # read-only tools, scope-level identity
    input_hash  = content_hash(inputs)                  # canonical JSON → SHA-256
    existing    = await cache.read(scope_id, kind)
    if existing and existing.input_hash == input_hash \
            and existing.generator_version == cache.generator_version:
        skip()                                          # nothing changed → no LLM call
        continue
    result = await generate(inputs)                     # LLM synthesis with evidence refs
    await cache.put_precomputed(scope_id, kind, result) # failure → old entry keeps serving
```

- **Content-hash skip.** Most scopes do not change between waves. Hashing the canonical inputs
  and skipping unchanged scopes typically removes most model calls. The self-test's second wave
  makes zero LLM calls.
- **Failure isolation.** One scope's failure does not abort the wave. The old entry keeps
  serving, and its TTL bounds how stale it can get.
- **Identity.** The wave runs as the application (a managed identity) against scope-level data
  sources. It never impersonates users, and it never writes to `agent_state`. Per-user
  personalization happens at read time.

## 9. DST-safe local-time scheduling

Users expect a morning briefing at, say, 08:00 *their* time, every day, across daylight-saving
transitions. The pattern:

1. **Capture the IANA time zone** (for example `Europe/Berlin`) from the inbound activity's
   `localTimezone` on each interaction, and store it on the conversation reference. Never store
   a UTC offset. Offsets change twice a year, and IANA rules encode that.
2. **Evaluate a window, not an instant.** `local_window_open(tz, now_utc, start_local, catch_up)`
   converts `now_utc` to local wall time with `zoneinfo`. It returns whether the local time is
   in `[start_local, start_local + catch_up)`, plus the **local date**.
3. **Tick frequently on every replica.** The dispatcher runs every 15 minutes. No leader election
   is needed, because the delivery claim makes delivery exactly-once.
4. **Key the claim on the local date**: `delivery:<kind>:<conversation_id>:<local_date>`.

| Edge case | Why a fixed UTC cron fails | Why the window + claim design works |
|---|---|---|
| Spring forward (02:00 → 03:00 skipped) | A job at a nonexistent local time never fires | The window is checked against wall time on each tick, so the first tick after the gap delivers |
| Fall back (01:00–02:00 repeats) | The job fires twice | The second occurrence finds the claim for that local date (`409`) and does nothing |
| Zones with 30- or 45-minute offsets | An hourly UTC cron is off by 30–45 minutes | A 15-minute tick with a multi-hour window covers every offset |
| Deploy or outage at 08:00 | The briefing is lost | The catch-up window (sample: 3 h) delivers late rather than never |
| Two replicas tick together | Duplicate messages | Only one `create` of the claim succeeds. The self-test asserts 2 replicas → 1 send. |

## 10. Proactive dispatch with `ContinueConversation`

Proactive messages reuse the conversation reference the agent saved on the user's last
interaction. In the Microsoft 365 Agents SDK or Bot Framework, `continue_conversation(reference,
callback)` posts to `{serviceUrl}/v3/conversations/{conversationId}/activities` with an app
token. That is why the reference, including its `serviceUrl`, is refreshed on every turn.

```text
tick(now_utc):
  for zone in DISTINCT VALUE c.time_zone of conversation references:
      open, local_date = local_window_open(zone, now_utc, start_local, catch_up)
      if not open: continue
      for ref in references(zone):
          create claim delivery:<kind>:<conv>:<local_date>     409 → already_claimed, skip
          payload = compose(cache.get(ref.scope_id, kind, …), user's agent_state)
          send(ref, payload) ─┬─ 2xx           → delivered (keep claim)
                              ├─ 403 / 404     → ConversationGone: delete the reference, keep claim
                              ├─ 429           → honor Retry-After; on budget exhaustion release claim
                              └─ 5xx / timeout → release claim so a later tick in the window retries
```

| Response | Meaning in Microsoft 365 channels | Action |
|---|---|---|
| `403 Forbidden` | The app was uninstalled or blocked, or the bot is no longer in the conversation | Delete the conversation reference. Do not retry. The user re-engages by reinstalling. |
| `404 Not Found` | The conversation no longer exists | Delete the reference. Do not retry. |
| `429 Too Many Requests` | Channel rate limit (per conversation and per app) | Wait for `Retry-After`, reduce dispatcher concurrency, and release the claim if the tick's time budget runs out. Never retry in a tight loop. |
| `5xx` or timeout | Transient channel failure | Release the claim (delete it), so a later tick inside the catch-up window retries. |

Also delete the reference when the agent receives an `installationUpdate` *remove* activity.
Do not wait for a `403`. For an ambiguous timeout where the send may have succeeded, prefer
**keeping** the claim for low-stakes content such as briefings. Missing one briefing is better
than sending two.

## 11. Cost and RU model

| Operation | Cosmos DB calls | Notes |
|---|---|---|
| Interactive turn | 2 point reads + 1 transactional batch (3 ops) | Keep sessions small. Exclude `summary`, `recent_turns`, and `tool_ledger` from the indexing policy, and index only the fields you query (`type`, `time_zone`, `doc_type`). |
| `412` replay (rare) | +1 point read + 1 batch per replay | Bounded by `max_replays`. Alert if the replay rate exceeds 1%. |
| Duplicate redelivery | 1 point read, then exit | No model or tool cost |
| Cached read | 1 point read in `precompute_cache` | One read serves every user in the scope |
| Pre-compute wave | 1 read per scope, plus 1 write only when inputs changed | Model calls ≈ changed scopes × kinds |
| Dispatch tick | 1 distinct-zone query + per-zone reference queries + 1 claim create per delivery | Runs every 15 min, and most ticks find no open window |

## 12. Observability

Emit these as OpenTelemetry metrics or span attributes, with no user text, and correlate them by
`turn_id`:

- `state.replay_count`, `state.duplicate_activity`, `state.session_bytes` (p95),
  `state.ru_per_turn`
- `cache.freshness` ∈ {fresh, live, stale, unavailable}, `cache.refresh_ms`,
  `cache.singleflight_waiters`
- `precompute.scopes_skipped`, `precompute.scopes_generated`, `precompute.scope_failures`
- `dispatch.delivered`, `dispatch.already_claimed`, `dispatch.gone`, `dispatch.throttled`,
  `dispatch.lateness_minutes` (actual send time minus local start)

Suggested alerts: a stale + unavailable ratio above 5% for 30 minutes, a `412` replay rate above
1%, `dispatch.gone` spiking after a release (it can signal a broken reference refresh), and p95
dispatch lateness above 60 minutes.

## 13. Adapting across domains

| Element | CRM / Sales | ITSM / SecOps | HR / Operations |
|---|---|---|---|
| `scope_id` | Territory or account team | Resolver group or on-call rota | Region, site, or HR service team |
| Pre-computed `kind` | Daily pipeline digest, at-risk accounts | Overnight incident digest, SLA-breach watchlist | Open-case digest, upcoming onboarding starts |
| Local start / catch-up | 08:00 / 3 h | Shift start / 1 h | 08:30 / 3 h |
| Session pinned entities | Accounts, opportunities | Incidents, changes, assets | Cases, requisitions |
| Stale banner tolerance | Hours | Minutes. Prefer "unavailable" over stale for active incidents. | Hours |

## 14. Checklist

- [ ] `agent_state` is created with the hierarchical key `["/tenant_id", "/principal_oid"]` and
      `DefaultTimeToLive = -1`. `precompute_cache` is keyed on `/scope_id`.
- [ ] `tenant_id` and `principal_oid` come only from the validated token.
- [ ] Each turn commits in one transactional batch with `If-Match`. `412` replays the recorded
      mutations, never the LLM.
- [ ] Turn-record IDs derive from the activity ID, so redelivery is a no-op.
- [ ] Every document type sets an explicit `ttl`, and sliding vs fixed is deliberate per type.
- [ ] Cache reads check entitlement first, use single-flight, have a refresh budget, label
      freshness, and show a stale banner.
- [ ] `generator_version` is bumped on every prompt, model, or schema change.
- [ ] Schedules use IANA zones, local-time windows with catch-up, and claims keyed on the local date.
- [ ] Dispatch deletes references on `403`/`404` and `installationUpdate` remove, honors
      `Retry-After` on `429`, and releases the claim on transient failure.
