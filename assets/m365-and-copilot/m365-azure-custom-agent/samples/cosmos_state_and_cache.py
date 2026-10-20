"""Durable agent state, pre-compute cache, and proactive dispatch on Azure Cosmos DB.

This module is the reference implementation for the "Durable State and
Pre-Compute Store" role. It addresses four failure modes that appear once an agent
leaves a single developer laptop:

1. State held in memory is lost on restart, deploy, or scale-in, and a second
   replica never sees it. Every replica must load and save state per turn.
2. Two activities for the same conversation can reach two replicas at the same
   time (a double-click, a card submit racing a typed message, a channel retry).
   Last-writer-wins silently drops one turn's changes. This module uses ETag
   optimistic concurrency and replays the turn's mutations against the fresher
   document.
3. The channel can deliver the same activity twice. The turn document's id
   contains the activity id, so the second commit fails with 409 and is dropped.
4. Expensive intelligence (multi-source joins plus an LLM summary) is too slow
   to compute inside a chat turn. It is pre-computed on a schedule aligned to the
   user's working day, stored with freshness watermarks, and served with an
   explicit live-refresh and stale-fallback path.

Containers
----------
``agent_state``  hierarchical partition key ["/tenant_id", "/principal_oid"]
    Holds SessionState, TurnState, ConversationReferenceRecord, and delivery claims
    for one user in one logical partition. Co-location makes the transactional
    batch possible: a Cosmos DB transactional batch is limited to a single
    logical partition, and that is what gives "one atomic write per turn".
    Single-tenant deployments may use "/principal_oid" alone.

``precompute_cache``  partition key "/scope_id"
    Holds PrecomputedBriefingCache entries for a shared scope (an account, a
    service, a portfolio, a case queue). Many users read the same scope, so the
    key is the scope and not the user.

Create containers with infrastructure as code (Bicep or Terraform) rather than
from application code. With local (key) auth disabled, the data-plane RBAC
roles cannot create databases or containers, so a "create if not exists" call at
startup fails in a correctly locked-down account.

Per-turn cost model (in-memory demo counts the calls; RU figures are typical
for a document of about 4 KB with default indexing):
    begin_turn  2 point reads in parallel (turn existence, session)  ~2 RU
    commit      1 transactional batch (create turn, replace session,
                optionally upsert conversation reference)          ~12-20 RU
    conflict    +1 point read and +1 batch per retry (rare)

Dependencies: the demo is standard-library only. `CosmosDocumentStore` needs
`azure-cosmos`, `azure-identity`, and `aiohttp`. `zoneinfo` needs the `tzdata`
package on Windows.

Run the self-test:
    python cosmos_state_and_cache.py
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from typing import Any, Awaitable, Callable, Mapping, Protocol, Sequence
from zoneinfo import ZoneInfo

AGENT_STATE = "agent_state"
PRECOMPUTE = "precompute_cache"
SCHEMA_VERSION = 2

TTL_SECONDS: dict[str, int] = {
    "session": 30 * 86400,  # sliding: Cosmos DB TTL counts from the last write (_ts)
    "turn": 7 * 86400,  # long enough for a weekly evaluation sample, short for privacy
    "conversation_reference": 90 * 86400,  # refreshed on every interaction
    "delivery_claim": 3 * 86400,  # outlives any time zone's "today"
    "briefing": 36 * 3600,  # a missed pre-compute run never serves day-old data as fresh
}
MAX_SESSION_BYTES = 48 * 1024  # RU cost scales with document size; keep sessions small
MAX_RECENT_TURNS = 6
MAX_LEDGER_ENTRIES = 20

PartitionKey = tuple[str, ...]


# Errors


class StoreError(Exception):
    status = 0


class NotFound(StoreError):
    status = 404


class Conflict(StoreError):
    status = 409


class PreconditionFailed(StoreError):
    status = 412


class BatchFailed(StoreError):
    def __init__(self, index: int, status: int) -> None:
        super().__init__(f"batch operation {index} failed with {status}")
        self.index = index
        self.status = status


class DuplicateActivity(Exception):
    """The activity was already committed by this or another replica."""


class ConcurrentTurnError(Exception):
    """The session kept changing underneath this turn; give up after retries."""


class EntitlementError(Exception):
    """The caller is not entitled to the scope of a pre-computed artefact."""


class CacheUnavailable(Exception):
    """No fresh entry, live refresh failed, and nothing stale is safe to serve."""


class ConversationGone(Exception):
    """The proactive target no longer exists (403 or 404 from the channel)."""

    def __init__(self, status: int) -> None:
        super().__init__(f"conversation gone ({status})")
        self.status = status


# Document store abstraction


@dataclass(frozen=True)
class BatchOp:
    kind: str  # create | replace | upsert | delete
    doc: Mapping[str, Any] | None = None
    item_id: str | None = None
    if_match: str | None = None

    @staticmethod
    def create(doc: Mapping[str, Any]) -> "BatchOp":
        return BatchOp("create", doc=doc)

    @staticmethod
    def replace(doc: Mapping[str, Any], if_match: str) -> "BatchOp":
        return BatchOp("replace", doc=doc, if_match=if_match)

    @staticmethod
    def upsert(doc: Mapping[str, Any]) -> "BatchOp":
        return BatchOp("upsert", doc=doc)

    @staticmethod
    def delete(item_id: str) -> "BatchOp":
        return BatchOp("delete", item_id=item_id)


class DocumentStore(Protocol):
    async def read(self, container: str, pk: PartitionKey, item_id: str) -> dict[str, Any]: ...

    async def create(self, container: str, pk: PartitionKey, doc: Mapping[str, Any]) -> dict[str, Any]: ...

    async def replace(self, container: str, pk: PartitionKey, doc: Mapping[str, Any], *, if_match: str | None) -> dict[str, Any]: ...

    async def upsert(self, container: str, pk: PartitionKey, doc: Mapping[str, Any], *, if_match: str | None = None) -> dict[str, Any]: ...

    async def delete(self, container: str, pk: PartitionKey, item_id: str) -> None: ...

    async def query(self, container: str, sql: str, params: Mapping[str, Any]) -> list[Any]: ...

    async def batch(self, container: str, pk: PartitionKey, ops: Sequence[BatchOp]) -> None: ...


def _strip_system(doc: Mapping[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


class CosmosDocumentStore:
    """`azure-cosmos` async implementation using Microsoft Entra ID (no account keys).

    Grant the workload identity the "Cosmos DB Built-in Data Contributor" data-plane
    role, scoped to the database, and disable local auth on the account.
    """

    def __init__(self, endpoint: str, database: str, credential: Any | None = None) -> None:
        from azure.cosmos.aio import CosmosClient  # type: ignore[import-not-found]

        if credential is None:
            from azure.identity.aio import DefaultAzureCredential  # type: ignore[import-not-found]

            credential = DefaultAzureCredential()
        self._client = CosmosClient(endpoint, credential=credential)
        self._db = self._client.get_database_client(database)

    def _c(self, name: str) -> Any:
        return self._db.get_container_client(name)

    @staticmethod
    def _pk(pk: PartitionKey) -> Any:
        return list(pk) if len(pk) > 1 else pk[0]

    @staticmethod
    def _cond(if_match: str | None) -> dict[str, Any]:
        if not if_match:
            return {}
        from azure.core import MatchConditions  # type: ignore[import-not-found]

        return {"etag": if_match, "match_condition": MatchConditions.IfNotModified}

    async def read(self, container: str, pk: PartitionKey, item_id: str) -> dict[str, Any]:
        from azure.cosmos import exceptions as ex  # type: ignore[import-not-found]

        try:
            return await self._c(container).read_item(item=item_id, partition_key=self._pk(pk))
        except ex.CosmosResourceNotFoundError:
            raise NotFound(item_id) from None

    async def create(self, container: str, pk: PartitionKey, doc: Mapping[str, Any]) -> dict[str, Any]:
        from azure.cosmos import exceptions as ex  # type: ignore[import-not-found]

        try:
            return await self._c(container).create_item(body=_strip_system(doc))
        except ex.CosmosResourceExistsError:
            raise Conflict(str(doc.get("id"))) from None

    async def replace(self, container: str, pk: PartitionKey, doc: Mapping[str, Any], *, if_match: str | None) -> dict[str, Any]:
        from azure.cosmos import exceptions as ex  # type: ignore[import-not-found]

        try:
            return await self._c(container).replace_item(item=doc["id"], body=_strip_system(doc), **self._cond(if_match))
        except ex.CosmosAccessConditionFailedError:
            raise PreconditionFailed(str(doc["id"])) from None
        except ex.CosmosResourceNotFoundError:
            raise NotFound(str(doc["id"])) from None

    async def upsert(self, container: str, pk: PartitionKey, doc: Mapping[str, Any], *, if_match: str | None = None) -> dict[str, Any]:
        from azure.cosmos import exceptions as ex  # type: ignore[import-not-found]

        try:
            return await self._c(container).upsert_item(body=_strip_system(doc), **self._cond(if_match))
        except ex.CosmosAccessConditionFailedError:
            raise PreconditionFailed(str(doc["id"])) from None

    async def delete(self, container: str, pk: PartitionKey, item_id: str) -> None:
        from azure.cosmos import exceptions as ex  # type: ignore[import-not-found]

        try:
            await self._c(container).delete_item(item=item_id, partition_key=self._pk(pk))
        except ex.CosmosResourceNotFoundError:
            return

    async def query(self, container: str, sql: str, params: Mapping[str, Any]) -> list[Any]:
        parameters = [{"name": k, "value": v} for k, v in params.items()]
        return [item async for item in self._c(container).query_items(query=sql, parameters=parameters)]

    async def batch(self, container: str, pk: PartitionKey, ops: Sequence[BatchOp]) -> None:
        from azure.cosmos.exceptions import CosmosBatchOperationError  # type: ignore[import-not-found]

        wire: list[tuple[Any, ...]] = []
        for op in ops:
            if op.kind == "create":
                wire.append(("create", (_strip_system(op.doc or {}),)))
            elif op.kind == "upsert":
                wire.append(("upsert", (_strip_system(op.doc or {}),)))
            elif op.kind == "replace":
                body = _strip_system(op.doc or {})
                wire.append(("replace", (body["id"], body), {"if_match_etag": op.if_match}))
            elif op.kind == "delete":
                wire.append(("delete", (op.item_id,)))
            else:
                raise ValueError(op.kind)
        try:
            await self._c(container).execute_item_batch(batch_operations=wire, partition_key=self._pk(pk))
        except CosmosBatchOperationError as err:
            index = err.error_index if err.error_index is not None else -1
            responses = err.operation_responses or []
            status = int(responses[index].get("statusCode", 0)) if 0 <= index < len(responses) else int(err.status_code or 0)
            raise BatchFailed(index, status) from None

    async def close(self) -> None:
        await self._client.close()


_SELECT = re.compile(r"^\s*SELECT\s+(?P<proj>\*|DISTINCT VALUE c\.(?P<field>\w+))\s+FROM c(?:\s+WHERE\s+(?P<where>.+))?\s*$", re.I | re.S)
_CMP = re.compile(r"^c\.(\w+)\s*(=|!=)\s*(@\w+)$")
_IN = re.compile(r"^ARRAY_CONTAINS\((@\w+),\s*c\.(\w+)\)$", re.I)


class InMemoryDocumentStore:
    """Emulates the Cosmos DB behaviours the pattern depends on.

    It emulates ETags, 404/409/412 status codes, TTL expiry from `_ts`, all-or-nothing
    transactional batches inside one partition, and a tiny SQL subset (`=`, `!=`,
    `ARRAY_CONTAINS`, `DISTINCT VALUE`). Several "replicas" can share one instance
    to reproduce races deterministically.
    """

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._items: dict[tuple[str, PartitionKey, str], dict[str, Any]] = {}
        self._seq = 0
        self._clock = clock
        self._lock = asyncio.Lock()
        self.calls: Counter[str] = Counter()

    def _stamp(self, doc: Mapping[str, Any]) -> dict[str, Any]:
        self._seq += 1
        stamped = copy.deepcopy(_strip_system(doc))
        stamped["_etag"] = f'"{self._seq:08x}-etag"'
        stamped["_ts"] = int(self._clock())
        return stamped

    def _live(self, key: tuple[str, PartitionKey, str], items: dict[tuple[str, PartitionKey, str], dict[str, Any]]) -> dict[str, Any] | None:
        doc = items.get(key)
        if doc is None:
            return None
        ttl = doc.get("ttl")
        if isinstance(ttl, int) and ttl > 0 and doc["_ts"] + ttl <= self._clock():
            items.pop(key, None)
            return None
        return doc

    def _apply(self, items: dict[tuple[str, PartitionKey, str], dict[str, Any]], container: str, pk: PartitionKey, op: BatchOp) -> None:
        item_id = op.item_id if op.kind == "delete" else str((op.doc or {})["id"])
        key = (container, pk, item_id)
        existing = self._live(key, items)
        if op.kind == "create":
            if existing is not None:
                raise Conflict(item_id)
            items[key] = self._stamp(op.doc or {})
        elif op.kind == "replace":
            if existing is None:
                raise NotFound(item_id)
            if op.if_match and existing["_etag"] != op.if_match:
                raise PreconditionFailed(item_id)
            items[key] = self._stamp(op.doc or {})
        elif op.kind == "upsert":
            if op.if_match and (existing is None or existing["_etag"] != op.if_match):
                raise PreconditionFailed(item_id)
            items[key] = self._stamp(op.doc or {})
        elif op.kind == "delete":
            if existing is None:
                raise NotFound(item_id)
            items.pop(key)

    async def read(self, container: str, pk: PartitionKey, item_id: str) -> dict[str, Any]:
        async with self._lock:
            self.calls["read"] += 1
            doc = self._live((container, pk, item_id), self._items)
            if doc is None:
                raise NotFound(item_id)
            return copy.deepcopy(doc)

    async def create(self, container: str, pk: PartitionKey, doc: Mapping[str, Any]) -> dict[str, Any]:
        return await self._single(container, pk, BatchOp.create(doc), "create")

    async def replace(self, container: str, pk: PartitionKey, doc: Mapping[str, Any], *, if_match: str | None) -> dict[str, Any]:
        return await self._single(container, pk, BatchOp("replace", doc=doc, if_match=if_match), "replace")

    async def upsert(self, container: str, pk: PartitionKey, doc: Mapping[str, Any], *, if_match: str | None = None) -> dict[str, Any]:
        return await self._single(container, pk, BatchOp("upsert", doc=doc, if_match=if_match), "upsert")

    async def delete(self, container: str, pk: PartitionKey, item_id: str) -> None:
        async with self._lock:
            self.calls["delete"] += 1
            try:
                self._apply(self._items, container, pk, BatchOp.delete(item_id))
            except NotFound:
                return

    async def _single(self, container: str, pk: PartitionKey, op: BatchOp, name: str) -> dict[str, Any]:
        async with self._lock:
            self.calls[name] += 1
            self._apply(self._items, container, pk, op)
            return copy.deepcopy(self._items[(container, pk, str((op.doc or {})["id"]))])

    async def batch(self, container: str, pk: PartitionKey, ops: Sequence[BatchOp]) -> None:
        async with self._lock:
            self.calls["batch"] += 1
            shadow = dict(self._items)
            for index, op in enumerate(ops):
                try:
                    self._apply(shadow, container, pk, op)
                except StoreError as err:
                    raise BatchFailed(index, err.status) from None
            self._items = shadow

    async def query(self, container: str, sql: str, params: Mapping[str, Any]) -> list[Any]:
        async with self._lock:
            self.calls["query"] += 1
            match = _SELECT.match(sql)
            if not match:
                raise ValueError(f"unsupported query: {sql}")
            clauses = [c.strip() for c in re.split(r"\s+AND\s+", match.group("where") or "", flags=re.I) if c.strip()]
            rows: list[Any] = []
            for (cont, _pk, _id), doc in list(self._items.items()):
                if cont != container or self._live((cont, _pk, _id), self._items) is None:
                    continue
                if all(self._matches(doc, clause, params) for clause in clauses):
                    rows.append(copy.deepcopy(doc))
            if match.group("field"):
                fld = match.group("field")
                return sorted({r[fld] for r in rows if fld in r})
            return rows

    @staticmethod
    def _matches(doc: Mapping[str, Any], clause: str, params: Mapping[str, Any]) -> bool:
        if m := _CMP.match(clause):
            fld, op, param = m.groups()
            if fld not in doc:
                return False  # Cosmos DB: comparing an undefined property yields undefined, which filters the row out
            return (doc[fld] == params[param]) if op == "=" else (doc[fld] != params[param])
        if m := _IN.match(clause):
            param, fld = m.groups()
            return fld in doc and doc[fld] in params[param]
        raise ValueError(f"unsupported clause: {clause}")


# Document schemas


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat()


def session_doc_id(conversation_id: str) -> str:
    return f"session:{conversation_id}"


def turn_doc_id(conversation_id: str, activity_id: str) -> str:
    return f"turn:{conversation_id}:{activity_id}"


def convref_doc_id(conversation_id: str) -> str:
    return f"convref:{conversation_id}"


def new_session_doc(tenant_id: str, principal_oid: str, conversation_id: str, channel_id: str, now: float) -> dict[str, Any]:
    return {
        "id": session_doc_id(conversation_id),
        "doc_type": "session",
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "principal_oid": principal_oid,
        "conversation_id": conversation_id,
        "channel_id": channel_id,
        "active_specialist": None,
        "active_skills": [],
        "planner_summary": "",
        "facts": {},
        "recent_turns": [],
        "handoff_ledger": [],
        "pending_approvals": [],
        "turn_count": 0,
        "last_activity_id": None,
        "created_at": _iso(now),
        "updated_at": _iso(now),
        "ttl": TTL_SECONDS["session"],
    }


def upgrade_session(doc: dict[str, Any]) -> dict[str, Any]:
    """Lazy schema migration on read; the upgraded shape is written by the next commit.

    Version 1 stored a single `specialist` string and no skills list. Never run a
    bulk migration job against live sessions; the next turn upgrades each one.
    """
    version = int(doc.get("schema_version", 1))
    if version < 2:
        doc["active_specialist"] = doc.pop("specialist", None)
        doc.setdefault("active_skills", [])
        doc.setdefault("recent_turns", [])
        doc["schema_version"] = 2
    return doc


def new_conversation_reference_doc(
    tenant_id: str,
    principal_oid: str,
    reference: Mapping[str, Any],
    *,
    time_zone: str,
    delivery_local: str = "07:30",
    opted_in: bool = True,
    now: float,
) -> dict[str, Any]:
    ZoneInfo(time_zone)  # reject invalid IANA names at write time, not at 07:30
    conversation_id = str(reference["conversation"]["id"])
    return {
        "id": convref_doc_id(conversation_id),
        "doc_type": "conversation_reference",
        "schema_version": SCHEMA_VERSION,
        "tenant_id": tenant_id,
        "principal_oid": principal_oid,
        "reference": dict(reference),
        "time_zone": time_zone,
        "delivery_local": delivery_local,
        "opted_in": opted_in,
        "last_delivery_local_date": "",  # an empty string, not null, so `!=` compares same-typed values
        "updated_at": _iso(now),
        "ttl": TTL_SECONDS["conversation_reference"],
    }


# Per-turn state with a single write


Mutation = Callable[[dict[str, Any]], None]


@dataclass
class TurnHandle:
    pk: PartitionKey
    conversation_id: str
    activity_id: str
    session: dict[str, Any]
    etag: str | None
    started: float
    duplicate: bool = False
    mutations: list[Mutation] = field(default_factory=list)
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    route: dict[str, Any] = field(default_factory=dict)
    reference_doc: dict[str, Any] | None = None
    conflicts: int = 0

    def mutate(self, fn: Mutation) -> None:
        """Apply a change now and remember it so it can be replayed after a 412.

        Mutations must be expressed as functions of the session, not as absolute
        values computed from a stale read. "Increment turn_count" replays
        correctly; "set turn_count to 7" does not.
        """
        fn(self.session)
        self.mutations.append(fn)

    def record_tool_call(self, tool: str, side_effect: str, status: str, latency_ms: int, evidence_ref: str | None = None) -> None:
        self.tool_calls.append(
            {"tool": tool, "side_effect": side_effect, "status": status, "latency_ms": latency_ms, "evidence_ref": evidence_ref}
        )


class StateManager:
    def __init__(self, store: DocumentStore, *, replica: str = "replica-0", clock: Callable[[], float] = time.time) -> None:
        self._store = store
        self._replica = replica
        self._clock = clock

    async def _load_session(self, pk: PartitionKey, conversation_id: str, channel_id: str) -> tuple[dict[str, Any], str | None]:
        try:
            doc = await self._store.read(AGENT_STATE, pk, session_doc_id(conversation_id))
            return upgrade_session(_strip_system(doc)), doc["_etag"]
        except NotFound:
            return new_session_doc(pk[0], pk[1], conversation_id, channel_id, self._clock()), None

    async def begin_turn(self, tenant_id: str, principal_oid: str, conversation_id: str, activity_id: str, channel_id: str = "msteams") -> TurnHandle:
        """Two parallel point reads. The duplicate check here is a cheap early exit;
        the authoritative de-duplication is the `create` inside the commit batch."""
        pk = (tenant_id, principal_oid)

        async def turn_exists() -> bool:
            try:
                await self._store.read(AGENT_STATE, pk, turn_doc_id(conversation_id, activity_id))
                return True
            except NotFound:
                return False

        exists, (session, etag) = await asyncio.gather(turn_exists(), self._load_session(pk, conversation_id, channel_id))
        return TurnHandle(pk, conversation_id, activity_id, session, etag, started=self._clock(), duplicate=exists)

    def attach_reference(self, handle: TurnHandle, reference: Mapping[str, Any], *, time_zone: str, existing: Mapping[str, Any] | None = None) -> None:
        """Refresh the proactive reference inside the same batch, only when needed.

        The channel's `serviceUrl` can change, so the stored copy must track the
        latest inbound activity. Rewriting it every turn wastes RU, so refresh it
        only when a routing field changed or the record is older than a week (which
        also keeps its TTL alive).
        """
        now = self._clock()
        if existing is not None:
            same_route = existing.get("reference", {}).get("serviceUrl") == reference.get("serviceUrl") and existing.get("time_zone") == time_zone
            fresh = (now - float(existing.get("_ts", 0))) < 7 * 86400
            if same_route and fresh:
                return
        doc = new_conversation_reference_doc(handle.pk[0], handle.pk[1], reference, time_zone=time_zone, now=now)
        if existing is not None:
            doc["last_delivery_local_date"] = existing.get("last_delivery_local_date", "")
        handle.reference_doc = doc

    def _turn_doc(self, handle: TurnHandle, outcome: str, tokens: Mapping[str, int]) -> dict[str, Any]:
        now = self._clock()
        return {
            "id": turn_doc_id(handle.conversation_id, handle.activity_id),
            "doc_type": "turn",
            "schema_version": SCHEMA_VERSION,
            "tenant_id": handle.pk[0],
            "principal_oid": handle.pk[1],
            "conversation_id": handle.conversation_id,
            "activity_id": handle.activity_id,
            "turn_index": handle.session["turn_count"],
            "route": handle.route,
            "tool_calls": handle.tool_calls,
            "tokens": dict(tokens),
            "latency_ms": int((now - handle.started) * 1000),
            "outcome": outcome,
            "replica": self._replica,
            "session_conflicts": handle.conflicts,
            "created_at": _iso(now),
            "ttl": TTL_SECONDS["turn"],
        }

    @staticmethod
    def _enforce_budget(session: dict[str, Any]) -> None:
        session["recent_turns"] = session["recent_turns"][-MAX_RECENT_TURNS:]
        session["handoff_ledger"] = session["handoff_ledger"][-MAX_LEDGER_ENTRIES:]
        while len(json.dumps(session, default=str).encode()) > MAX_SESSION_BYTES and session["recent_turns"]:
            session["recent_turns"].pop(0)

    async def commit(self, handle: TurnHandle, *, outcome: str, tokens: Mapping[str, int] | None = None, max_attempts: int = 3) -> None:
        """One transactional batch per turn: create the turn, then replace the session.

        Raises DuplicateActivity if another replica already committed this
        activity (409 on the turn create), and ConcurrentTurnError after
        `max_attempts` ETag conflicts on the session.
        """
        if handle.duplicate:
            raise DuplicateActivity(handle.activity_id)

        def finalize(session: dict[str, Any]) -> None:
            session["turn_count"] += 1
            session["last_activity_id"] = handle.activity_id
            session["updated_at"] = _iso(self._clock())

        handle.mutate(finalize)
        for _attempt in range(max_attempts):
            self._enforce_budget(handle.session)
            session_op = BatchOp.replace(handle.session, handle.etag) if handle.etag else BatchOp.create(handle.session)
            ops = [BatchOp.create(self._turn_doc(handle, outcome, tokens or {})), session_op]
            if handle.reference_doc is not None:
                ops.append(BatchOp.upsert(handle.reference_doc))
            try:
                await self._store.batch(AGENT_STATE, handle.pk, ops)
                return
            except BatchFailed as failure:
                if failure.index == 0 and failure.status == 409:
                    raise DuplicateActivity(handle.activity_id) from None
                if failure.index == 1 and failure.status in (409, 412, 404):
                    fresh, etag = await self._load_session(handle.pk, handle.conversation_id, handle.session["channel_id"])
                    for fn in handle.mutations:
                        fn(fresh)
                    handle.session, handle.etag = fresh, etag
                    handle.conflicts += 1
                    continue
                raise
        raise ConcurrentTurnError(handle.conversation_id)


# Pre-computed briefing cache with freshness watermarks


@dataclass(frozen=True)
class RefreshResult:
    payload: Mapping[str, Any]
    source_watermark: str
    input_hash: str


@dataclass(frozen=True)
class CacheResult:
    payload: Mapping[str, Any]
    freshness: str  # fresh | live | stale
    as_of: str
    banner: str | None = None


def briefing_doc_id(scope_id: str, kind: str) -> str:
    return f"briefing:{kind}:{scope_id}"


def content_hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()


class BriefingCache:
    """Serve pre-computed artefacts; refresh live under a budget; fall back to stale.

    Entitlement equivalence: the pre-compute job runs as the application identity,
    which usually sees more than any single user. The serving path must therefore
    check that the caller's token-derived entitlements include `scope_id` before
    returning anything. Without that check the cache becomes a row-level-security
    bypass.
    """

    def __init__(
        self,
        store: DocumentStore,
        *,
        generator_version: str,
        validity: timedelta = timedelta(hours=20),
        max_stale: timedelta = timedelta(hours=36),
        refresh_budget_s: float = 6.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._store = store
        self._version = generator_version
        self._validity = validity
        self._max_stale = max_stale
        self._budget = refresh_budget_s
        self._clock = clock
        self._inflight: dict[tuple[str, str], asyncio.Task[RefreshResult]] = {}
        self.live_refreshes = 0

    @property
    def generator_version(self) -> str:
        return self._version

    def _doc(self, scope_id: str, kind: str, result: RefreshResult, prompt_hash: str) -> dict[str, Any]:
        now = self._clock()
        return {
            "id": briefing_doc_id(scope_id, kind),
            "doc_type": "briefing",
            "schema_version": SCHEMA_VERSION,
            "scope_id": scope_id,
            "kind": kind,
            "payload": dict(result.payload),
            "source_watermark": result.source_watermark,
            "input_hash": result.input_hash,
            "prompt_hash": prompt_hash,
            "generator_version": self._version,
            "computed_at": _iso(now),
            "valid_until": _iso(now + self._validity.total_seconds()),
            "ttl": TTL_SECONDS["briefing"],
        }

    async def put_precomputed(self, scope_id: str, kind: str, result: RefreshResult, *, prompt_hash: str) -> bool:
        """Write from the scheduled job. Returns False when nothing changed.

        If the inputs and generator are unchanged, the expensive LLM step was
        already skipped by the caller and the write is skipped here, which saves
        RU and keeps `computed_at` honest about when content last changed.
        """
        try:
            existing = await self._store.read(PRECOMPUTE, (scope_id,), briefing_doc_id(scope_id, kind))
        except NotFound:
            existing = None
        if existing and existing.get("input_hash") == result.input_hash and existing.get("generator_version") == self._version:
            return False
        doc = self._doc(scope_id, kind, result, prompt_hash)
        await self._store.upsert(PRECOMPUTE, (scope_id,), doc)
        return True

    async def get(
        self,
        scope_id: str,
        kind: str,
        *,
        entitled_scopes: frozenset[str],
        live_refresh: Callable[[], Awaitable[RefreshResult]],
        prompt_hash: str = "",
    ) -> CacheResult:
        if scope_id not in entitled_scopes:
            raise EntitlementError(scope_id)
        now = self._clock()
        try:
            entry: dict[str, Any] | None = await self._store.read(PRECOMPUTE, (scope_id,), briefing_doc_id(scope_id, kind))
        except NotFound:
            entry = None

        if entry and entry["generator_version"] == self._version and datetime.fromisoformat(entry["valid_until"]).timestamp() > now:
            return CacheResult(entry["payload"], "fresh", entry["computed_at"])

        key = (scope_id, kind)
        task = self._inflight.get(key)
        if task is None:
            self.live_refreshes += 1
            task = asyncio.ensure_future(self._refresh_and_store(scope_id, kind, live_refresh, entry, prompt_hash))
            self._inflight[key] = task
            task.add_done_callback(lambda _t, k=key: self._inflight.pop(k, None))
        try:
            # shield: a waiter that times out must not cancel the shared refresh,
            # which keeps running and warms the cache for the next request
            result = await asyncio.wait_for(asyncio.shield(task), timeout=self._budget)
            return CacheResult(result.payload, "live", _iso(self._clock()))
        except Exception:
            if entry is not None:
                computed = datetime.fromisoformat(entry["computed_at"])
                if now - computed.timestamp() <= self._max_stale.total_seconds():
                    banner = f"Showing results as of {computed.strftime('%Y-%m-%d %H:%M')} UTC; a live refresh did not complete."
                    return CacheResult(entry["payload"], "stale", entry["computed_at"], banner)
            raise CacheUnavailable(scope_id) from None

    async def _refresh_and_store(
        self,
        scope_id: str,
        kind: str,
        live_refresh: Callable[[], Awaitable[RefreshResult]],
        entry: Mapping[str, Any] | None,
        prompt_hash: str,
    ) -> RefreshResult:
        result = await live_refresh()
        doc = self._doc(scope_id, kind, result, prompt_hash)
        try:
            if entry is not None:
                await self._store.upsert(PRECOMPUTE, (scope_id,), doc, if_match=entry["_etag"])
            else:
                await self._store.create(PRECOMPUTE, (scope_id,), doc)
        except (PreconditionFailed, Conflict):
            pass  # another writer stored a newer entry first; keep theirs
        return result


async def run_precompute_wave(
    cache: BriefingCache,
    scopes: Sequence[str],
    *,
    kind: str,
    read_watermark: Callable[[str], Awaitable[tuple[str, Mapping[str, Any]]]],
    generate: Callable[[str, Mapping[str, Any]], Awaitable[Mapping[str, Any]]],
    prompt_hash: str,
    concurrency: int = 8,
    store: DocumentStore,
) -> Counter[str]:
    """Pre-compute one wave (one time-zone band) with bounded concurrency.

    For each scope, read the cheap source watermark and inputs first. If the input
    hash and generator version match the cached entry, skip the LLM call
    entirely. This is where most of the token budget is saved: on a typical
    day most scopes have not changed overnight.
    """
    stats: Counter[str] = Counter()
    gate = asyncio.Semaphore(concurrency)

    async def one(scope_id: str) -> None:
        async with gate:
            watermark, inputs = await read_watermark(scope_id)
            input_hash = content_hash(inputs)
            try:
                existing = await store.read(PRECOMPUTE, (scope_id,), briefing_doc_id(scope_id, kind))
                if existing.get("input_hash") == input_hash and existing.get("generator_version") == cache.generator_version:
                    stats["unchanged_skipped_llm"] += 1
                    return
            except NotFound:
                pass
            try:
                payload = await generate(scope_id, inputs)
            except Exception:
                stats["failed"] += 1  # the old entry keeps serving; its TTL bounds staleness
                return
            written = await cache.put_precomputed(scope_id, kind, RefreshResult(payload, watermark, input_hash), prompt_hash=prompt_hash)
            stats["written" if written else "unchanged"] += 1

    await asyncio.gather(*(one(s) for s in scopes))
    return stats


# Time-zone aligned scheduling and proactive delivery


def local_window_open(time_zone: str, now_utc: datetime, start_local: dtime, catch_up: timedelta) -> tuple[bool, str]:
    """Is `now` inside [start_local, start_local + catch_up) in that zone, and what is the local date?

    The window comes from converting UTC to local through zoneinfo, so daylight
    saving transitions are handled by the tz database rather than by fixed offsets.
    A catch-up span (rather than an exact 15-minute slot) lets a tick missed
    during a deploy deliver late instead of never; the delivery claim keeps it
    exactly-once.
    """
    local = now_utc.astimezone(ZoneInfo(time_zone))
    start = local.replace(hour=start_local.hour, minute=start_local.minute, second=0, microsecond=0)
    return (start <= local < start + catch_up), local.date().isoformat()


class ProactiveSender(Protocol):
    async def continue_conversation(self, reference: Mapping[str, Any], activity: Mapping[str, Any]) -> None:
        """Send `activity` into the referenced conversation as the bot.

        Raise ConversationGone(403|404) when the user removed the app, left the
        team, or the conversation no longer exists.
        """


class BotConnectorSender:
    """Proactive send via the Bot Connector REST API (what the SDK adapters call).

    POST {serviceUrl}/v3/conversations/{conversationId}/activities with a token
    for the bot's own app identity (scope https://api.botframework.com/.default).
    No user token exists on this path, so anything the message needs from
    user-scoped systems must already be in the pre-compute cache or must be
    fetched after the user clicks into the conversation.
    """

    def __init__(self, get_bot_token: Callable[[], Awaitable[str]], http: Any) -> None:
        self._token = get_bot_token
        self._http = http  # an httpx.AsyncClient

    async def continue_conversation(self, reference: Mapping[str, Any], activity: Mapping[str, Any]) -> None:
        url = f"{str(reference['serviceUrl']).rstrip('/')}/v3/conversations/{reference['conversation']['id']}/activities"
        response = await self._http.post(url, json=dict(activity), headers={"Authorization": f"Bearer {await self._token()}"})
        if response.status_code in (403, 404):
            raise ConversationGone(response.status_code)
        response.raise_for_status()


@dataclass
class DispatchReport:
    due_zones: list[str] = field(default_factory=list)
    delivered: int = 0
    already_claimed: int = 0
    removed_references: int = 0
    failed: int = 0


class ProactiveDispatcher:
    """Runs every 15 minutes on every replica; delivery claims make it exactly-once."""

    def __init__(
        self,
        store: DocumentStore,
        sender: ProactiveSender,
        *,
        tenant_id: str,
        compose: Callable[[Mapping[str, Any]], Awaitable[Mapping[str, Any]]],
        kind: str = "daily_briefing",
        start_local: dtime = dtime(7, 30),
        catch_up: timedelta = timedelta(hours=3),
    ) -> None:
        self._store = store
        self._sender = sender
        self._tenant = tenant_id
        self._compose = compose
        self._kind = kind
        self._start = start_local
        self._catch_up = catch_up

    async def tick(self, now_utc: datetime) -> DispatchReport:
        report = DispatchReport()
        zones = await self._store.query(
            AGENT_STATE,
            "SELECT DISTINCT VALUE c.time_zone FROM c WHERE c.doc_type = @t AND c.tenant_id = @tid",
            {"@t": "conversation_reference", "@tid": self._tenant},
        )
        due: dict[str, str] = {}
        for tz in zones:
            is_open, local_date = local_window_open(tz, now_utc, self._start, self._catch_up)
            if is_open:
                due[tz] = local_date
        report.due_zones = sorted(due)
        if not due:
            return report

        refs = await self._store.query(
            AGENT_STATE,
            "SELECT * FROM c WHERE c.doc_type = @t AND c.tenant_id = @tid AND c.opted_in = @yes AND ARRAY_CONTAINS(@zones, c.time_zone)",
            {"@t": "conversation_reference", "@tid": self._tenant, "@yes": True, "@zones": sorted(due)},
        )
        for ref in refs:
            local_date = due[ref["time_zone"]]
            if ref.get("last_delivery_local_date") == local_date:
                continue
            await self._deliver(ref, local_date, report)
        return report

    async def _deliver(self, ref: Mapping[str, Any], local_date: str, report: DispatchReport) -> None:
        pk = (ref["tenant_id"], ref["principal_oid"])
        claim_id = f"delivery:{self._kind}:{ref['reference']['conversation']['id']}:{local_date}"
        try:
            await self._store.create(
                AGENT_STATE,
                pk,
                {
                    "id": claim_id,
                    "doc_type": "delivery_claim",
                    "tenant_id": pk[0],
                    "principal_oid": pk[1],
                    "ttl": TTL_SECONDS["delivery_claim"],
                },
            )
        except Conflict:
            report.already_claimed += 1
            return
        try:
            activity = await self._compose(ref)
            await self._sender.continue_conversation(ref["reference"], activity)
        except ConversationGone:
            await self._store.delete(AGENT_STATE, pk, ref["id"])
            report.removed_references += 1
            return
        except Exception:
            await self._store.delete(AGENT_STATE, pk, claim_id)  # release so a later tick retries
            report.failed += 1
            return
        updated = dict(ref, last_delivery_local_date=local_date)
        try:
            await self._store.replace(AGENT_STATE, pk, updated, if_match=ref["_etag"])
        except (PreconditionFailed, NotFound):
            pass  # a concurrent turn refreshed the reference; the claim still prevents a resend
        report.delivered += 1


# Self-test


class _FakeClock:
    def __init__(self, start: float) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


class _RecordingSender:
    def __init__(self, gone: set[str]) -> None:
        self.sent: list[str] = []
        self._gone = gone

    async def continue_conversation(self, reference: Mapping[str, Any], activity: Mapping[str, Any]) -> None:
        conversation_id = reference["conversation"]["id"]
        if conversation_id in self._gone:
            raise ConversationGone(403)
        await asyncio.sleep(0)
        self.sent.append(conversation_id)


def _reference(conversation_id: str, user_oid: str) -> dict[str, Any]:
    return {
        "channelId": "msteams",
        "serviceUrl": "https://smba.trafficmanager.net/amer/",
        "conversation": {"id": conversation_id, "conversationType": "personal", "tenantId": "tenant-0001"},
        "user": {"id": f"29:{user_oid}", "aadObjectId": user_oid},
        "bot": {"id": "28:bot-app-id"},
    }


async def _self_test() -> None:
    clock = _FakeClock(datetime(2026, 3, 9, 12, 40, tzinfo=timezone.utc).timestamp())
    store = InMemoryDocumentStore(clock=clock)
    replica_1 = StateManager(store, replica="replica-1", clock=clock)
    replica_2 = StateManager(store, replica="replica-2", clock=clock)
    tenant = "tenant-0001"
    log: list[str] = []

    # 1. first turn creates the session in one batch
    t0 = await replica_1.begin_turn(tenant, "oid-user-a", "conv-a", "act-0")
    t0.mutate(lambda s: s.__setitem__("active_specialist", "status"))
    replica_1.attach_reference(t0, _reference("conv-a", "oid-user-a"), time_zone="America/Chicago")
    store.calls.clear()
    await replica_1.commit(t0, outcome="answered", tokens={"input": 1800, "output": 220})
    assert store.calls == Counter({"batch": 1}), store.calls
    log.append("first turn: session + turn + conversation reference written in 1 batch")

    # 2. two replicas race on the same conversation; ETag conflict, replay, both survive
    ta = await replica_1.begin_turn(tenant, "oid-user-a", "conv-a", "act-1")
    tb = await replica_2.begin_turn(tenant, "oid-user-a", "conv-a", "act-2")
    ta.mutate(lambda s: s["facts"].__setitem__("focus_entity", "E-100"))
    tb.mutate(lambda s: s["pending_approvals"].append({"tool": "send_message_draft", "payload_hash": "sha256:abc"}))
    await replica_1.commit(ta, outcome="answered")
    await replica_2.commit(tb, outcome="answered")
    final = await store.read(AGENT_STATE, (tenant, "oid-user-a"), session_doc_id("conv-a"))
    assert final["turn_count"] == 3, final["turn_count"]
    assert final["facts"]["focus_entity"] == "E-100" and len(final["pending_approvals"]) == 1
    assert tb.conflicts == 1
    log.append("concurrent turns on 2 replicas: 1 ETag conflict, mutations replayed, no lost update")

    # 3. duplicate delivery, sequential (early exit) and racing (409 in batch)
    dup = await replica_2.begin_turn(tenant, "oid-user-a", "conv-a", "act-1")
    assert dup.duplicate
    r1 = await replica_1.begin_turn(tenant, "oid-user-a", "conv-a", "act-3")
    r2 = await replica_2.begin_turn(tenant, "oid-user-a", "conv-a", "act-3")
    await replica_1.commit(r1, outcome="answered")
    try:
        await replica_2.commit(r2, outcome="answered")
        raise AssertionError("racing duplicate must be rejected")
    except DuplicateActivity:
        pass
    final = await store.read(AGENT_STATE, (tenant, "oid-user-a"), session_doc_id("conv-a"))
    assert final["turn_count"] == 4
    log.append("duplicate activity: early exit on read, 409 on racing commit, counted once")

    # 4. lazy schema upgrade
    legacy = new_session_doc(tenant, "oid-user-l", "conv-l", "msteams", clock())
    legacy.pop("active_skills")
    legacy["schema_version"] = 1
    legacy["specialist"] = legacy.pop("active_specialist")
    await store.create(AGENT_STATE, (tenant, "oid-user-l"), legacy)
    tl = await replica_1.begin_turn(tenant, "oid-user-l", "conv-l", "act-l1")
    assert tl.session["schema_version"] == 2 and tl.session["active_skills"] == []
    log.append("schema v1 session upgraded lazily on read")

    # 5. pre-compute cache: entitlement, fresh, singleflight live refresh, stale fallback
    cache = BriefingCache(store, generator_version="briefing-v3", refresh_budget_s=0.05, clock=clock)
    calls = Counter[str]()

    async def fast_refresh() -> RefreshResult:
        calls["fast"] += 1
        await asyncio.sleep(0.01)
        return RefreshResult({"headline": "live"}, "wm-2", content_hash({"v": 2}))

    async def slow_refresh() -> RefreshResult:
        calls["slow"] += 1
        await asyncio.sleep(0.2)
        return RefreshResult({"headline": "late"}, "wm-3", content_hash({"v": 3}))

    async def broken_refresh() -> RefreshResult:
        raise RuntimeError("source unavailable")

    entitled = frozenset({"scope-1", "scope-2"})
    try:
        await cache.get("scope-9", "daily", entitled_scopes=entitled, live_refresh=fast_refresh)
        raise AssertionError("must refuse an unentitled scope")
    except EntitlementError:
        pass
    await cache.put_precomputed("scope-1", "daily", RefreshResult({"headline": "precomputed"}, "wm-1", content_hash({"v": 1})), prompt_hash="p1")
    hit = await cache.get("scope-1", "daily", entitled_scopes=entitled, live_refresh=fast_refresh)
    assert hit.freshness == "fresh" and calls["fast"] == 0

    results = await asyncio.gather(*(cache.get("scope-2", "daily", entitled_scopes=entitled, live_refresh=fast_refresh) for _ in range(5)))
    assert {r.freshness for r in results} == {"live"} and calls["fast"] == 1
    log.append("cache: unentitled scope refused, fresh hit, 5 concurrent misses -> 1 live refresh")

    clock.now += 21 * 3600  # entry for scope-1 is now past valid_until but within max_stale
    stale = await cache.get("scope-1", "daily", entitled_scopes=entitled, live_refresh=slow_refresh)
    assert stale.freshness == "stale" and stale.banner and calls["slow"] == 1
    await asyncio.sleep(0.25)  # the shielded refresh finished in the background and warmed the cache
    warmed = await cache.get("scope-1", "daily", entitled_scopes=entitled, live_refresh=broken_refresh)
    assert warmed.freshness == "fresh" and warmed.payload["headline"] == "late"
    clock.now += 40 * 3600
    try:
        await cache.get("scope-1", "daily", entitled_scopes=entitled, live_refresh=broken_refresh)
        raise AssertionError("expired entry with failed refresh must not be served")
    except CacheUnavailable:
        pass
    log.append("cache: stale served with banner, refresh-behind warmed it, expired + failed -> unavailable")

    # 6. pre-compute wave skips unchanged inputs
    async def read_watermark(scope_id: str) -> tuple[str, Mapping[str, Any]]:
        return "wm-x", {"scope": scope_id, "open_items": 3}

    async def generate(scope_id: str, inputs: Mapping[str, Any]) -> Mapping[str, Any]:
        calls["generate"] += 1
        return {"headline": f"{scope_id}: {inputs['open_items']} open items"}

    first = await run_precompute_wave(cache, ["scope-3", "scope-4"], kind="daily", read_watermark=read_watermark, generate=generate, prompt_hash="p1", store=store)
    second = await run_precompute_wave(cache, ["scope-3", "scope-4"], kind="daily", read_watermark=read_watermark, generate=generate, prompt_hash="p1", store=store)
    assert first["written"] == 2 and second["unchanged_skipped_llm"] == 2 and calls["generate"] == 2
    log.append("pre-compute: second wave skipped the LLM for unchanged inputs")

    # 7. proactive dispatch across time zones, two replicas, uninstalled app
    clock.now = datetime(2026, 3, 9, 12, 40, tzinfo=timezone.utc).timestamp()  # US on DST since Mar 8, Europe not yet
    for oid, conv, tz in [
        ("oid-user-b", "conv-b", "America/New_York"),
        ("oid-user-c", "conv-c", "Asia/Tokyo"),
        ("oid-user-d", "conv-d", "America/Los_Angeles"),
        ("oid-user-e", "conv-e", "Europe/London"),
    ]:
        await store.create(AGENT_STATE, (tenant, oid), new_conversation_reference_doc(tenant, oid, _reference(conv, oid), time_zone=tz, now=clock()))
    sender = _RecordingSender(gone={"conv-b"})

    async def compose(ref: Mapping[str, Any]) -> Mapping[str, Any]:
        return {"type": "message", "text": "Your briefing is ready."}

    d1 = ProactiveDispatcher(store, sender, tenant_id=tenant, compose=compose)
    d2 = ProactiveDispatcher(store, sender, tenant_id=tenant, compose=compose)
    now_utc = datetime.fromtimestamp(clock(), tz=timezone.utc)
    rep1, rep2 = await asyncio.gather(d1.tick(now_utc), d2.tick(now_utc))
    assert rep1.due_zones == ["America/Chicago", "America/New_York"], rep1.due_zones
    assert sender.sent == ["conv-a"], sender.sent
    assert rep1.delivered + rep2.delivered == 1 and rep1.already_claimed + rep2.already_claimed == 2
    assert rep1.removed_references + rep2.removed_references == 1
    rep3 = await d1.tick(now_utc)
    assert rep3.delivered == 0 and sender.sent == ["conv-a"]
    try:
        await store.read(AGENT_STATE, (tenant, "oid-user-b"), convref_doc_id("conv-b"))
        raise AssertionError("reference for an uninstalled app must be deleted")
    except NotFound:
        pass
    log.append("dispatch: DST-correct zones, 2 replicas -> 1 send, uninstalled app reference removed, re-tick no-op")

    for line in log:
        print(f"  ok  {line}")
    print(f"\n{len(log)} scenario groups passed; store calls: {dict(store.calls)}")


if __name__ == "__main__":
    asyncio.run(_self_test())
