"""Server-side side-effect gate for agent tools (classes R, W1, W2, W3, W4).

This module is the reference implementation for the "Shared MCP Governance
Layer" role in the pattern. It runs on the MCP server, not in the agent, which
means the same rules apply whether the caller is a custom engine agent, a
declarative agent, Cowork, a scheduled workflow, or a script that bypassed every
user interface.

Why the gate is on the server
-----------------------------
Model instructions are not a control. Host confirmation dialogs are useful, but
you do not own them on every surface, and some surfaces suppress them when a tool
is marked read-only. The only control that holds across all callers is the one
that runs where the side effect happens, so the gate runs at the tool boundary.

Side-effect classes
-------------------
    R   Read-only. No state change anywhere.
    W1  Private and reversible. Writes only to the caller's own agent state, such
        as a note, a snooze, or a preference.
    W2  Shared but reversible, or staged. Creates a draft or a pending record in
        a system of record that nobody else acts on until promoted.
    W3  Externally visible. Sends a message, posts to a channel, or updates a
        shared record that other people or systems will act on.
    W4  Irreversible, destructive, financial, or bulk. Deletes, closes, pays,
        approves, or touches more than one record per call.

Check order, and why each step sits where it does
-------------------------------------------------
     1. Registry lookup. An unknown tool fails closed with `unregistered_tool`.
     2. R passes. It isn't counted, so a read-heavy session never consumes a
        write budget.
     3. Kill switch, read from the environment on every call (not cached at
        import), so an operator can stop all writes without a redeploy.
     4. Unattended ceiling. A scheduled or delegated run may not exceed W2.
     5. Recipient allow-list. Checked before anything stateful.
     6. Fan-out ceiling. Checked before anything stateful.
     7. Draft-before-send. W3 sends must reference a draft the same principal
        created for the same tool family.
     8. Approval presence. A missing token is refused before the counter is
        touched, and the response carries the payload hash that the approval UI
        must bind to.
     9. Daily-cap reservation. This is an atomic conditional increment against a
        durable store, so the cap holds across replicas and surfaces.
    10. Approval consumption. If it fails, the cap reservation is released.
        Reserving first means a cap refusal never burns a one-time approval. A
        refused approval then costs one compensating decrement, and the brief
        over-count it causes can only refuse, never allow.

Each decision, allow or deny, is written to the audit sink with the principal,
tool, class, payload hash, and correlation id. Allowed reads are left to
telemetry, so the write-audit log stays small enough to review.

Runtime dependencies: none (standard library only). The in-memory stores are
for tests and demos. A production deployment backs `CounterStore`,
`ApprovalStore`, and `DraftStore` with a durable store such as Azure Cosmos DB,
using ETag preconditions (see `cosmos_state_and_cache.py`).

Run the self-test:
    python side_effect_gate.py
"""

from __future__ import annotations

import asyncio
import enum
import hashlib
import json
import os
import secrets
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping, Protocol, Sequence

WRITES_ENABLED_ENV = "AGENT_MCP_WRITES_ENABLED"
DISABLED_TOOLS_ENV = "AGENT_MCP_DISABLED_TOOLS"
DEFAULT_APPROVAL_TTL_SECONDS = 900
UNATTENDED_CEILING_RANK = 2  # W2


class SideEffect(enum.Enum):
    R = ("R", 0, "read-only")
    W1 = ("W1", 1, "private, reversible (caller's own agent state)")
    W2 = ("W2", 2, "shared but staged or reversible (drafts, pending records)")
    W3 = ("W3", 3, "externally visible (send, post, update shared record)")
    W4 = ("W4", 4, "irreversible, destructive, financial, or bulk")

    def __init__(self, label: str, rank: int, meaning: str) -> None:
        self.label = label
        self.rank = rank
        self.meaning = meaning


_SAFE_CONTEXT_KEYS = frozenset({"payload_hash", "cap", "used", "max_fanout", "requested_fanout", "tool"})


class SideEffectDenied(Exception):
    """Raised for every refusal. `code` is stable and machine-readable."""

    def __init__(self, code: str, message: str, **context: Any) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.context = context

    def as_payload(self) -> dict[str, Any]:
        """Shape returned to the MCP client as a tool result, not a transport error.

        Returning a structured tool result, rather than a JSON-RPC error, lets the
        model read the refusal and explain it to the user ("I drafted it, but
        sending needs your approval") instead of retrying blindly. Only allow-listed
        context keys are echoed, so internal identifiers never leak to the model.
        """
        payload: dict[str, Any] = {
            "error": self.code,
            "detail": self.message,
            "refused_by": "side_effect_gate",
        }
        payload.update({k: v for k, v in self.context.items() if k in _SAFE_CONTEXT_KEYS})
        return payload


@dataclass(frozen=True)
class ToolPolicy:
    name: str
    side_effect: SideEffect
    description: str
    daily_cap: int | None = None
    requires_draft: bool = False
    requires_approval: bool = False
    max_fanout: int | None = None
    recipient_domains: frozenset[str] | None = None
    open_world: bool = False
    draft_family: str | None = None

    def __post_init__(self) -> None:
        rank = self.side_effect.rank
        if rank >= 3 and not self.requires_approval:
            raise ValueError(f"{self.name}: W3/W4 tools must require approval")
        if rank == 4 and not self.daily_cap:
            raise ValueError(f"{self.name}: W4 tools must declare a daily cap")
        if self.daily_cap is not None and self.daily_cap <= 0:
            raise ValueError(f"{self.name}: daily_cap must be positive")
        if self.max_fanout is not None and self.max_fanout <= 0:
            raise ValueError(f"{self.name}: max_fanout must be positive")
        if self.requires_draft and not self.draft_family:
            raise ValueError(f"{self.name}: requires_draft needs a draft_family")
        if rank == 0 and (self.requires_approval or self.daily_cap):
            raise ValueError(f"{self.name}: R tools are never gated or counted")

    def as_mcp_annotations(self) -> dict[str, Any]:
        """MCP tool annotations, using the camelCase wire keys the spec defines.

        Pitfall: some SDK annotation models accept unknown fields. Snake-case keys
        such as `read_only_hint` validate without error and serialise as extra
        fields, so the host sees no hint at all. A host that decides whether to
        show a confirmation from `readOnlyHint` and `destructiveHint` then
        behaves as if the hints were absent. Emit the exact wire names.
        """
        rank = self.side_effect.rank
        return {
            "title": self.description,
            "readOnlyHint": rank == 0,
            "destructiveHint": rank >= 3,
            "idempotentHint": rank <= 2,
            "openWorldHint": self.open_world,
        }


@dataclass(frozen=True)
class Principal:
    """Who is acting, derived from a validated token and never from tool arguments."""

    tenant_id: str
    object_id: str
    upn: str
    surface: str
    unattended: bool = False
    delegated_ceiling: SideEffect | None = None

    @property
    def key(self) -> str:
        return f"{self.tenant_id}:{self.object_id}"


@dataclass(frozen=True)
class ActionRequest:
    tool: str
    principal: Principal
    payload: Mapping[str, Any]
    recipients: Sequence[str] = ()
    draft_id: str | None = None
    approval_token: str | None = None
    fanout: int = 1
    correlation_id: str = ""


@dataclass(frozen=True)
class GateReceipt:
    tool: str
    side_effect: SideEffect
    principal_key: str
    payload_hash: str
    counter_value: int | None
    approval_token: str | None
    decided_at: str


def canonical_payload_hash(payload: Mapping[str, Any]) -> str:
    """Stable hash of the arguments an approval is bound to.

    Control fields (the approval token itself, correlation ids) are excluded, so
    attaching the token doesn't change the hash it was minted against.
    """
    material = {k: v for k, v in payload.items() if k not in {"approval_token", "correlation_id"}}
    blob = json.dumps(material, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str)
    return "sha256:" + hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _utc_day(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc).strftime("%Y-%m-%d")


def _iso(now: float | None = None) -> str:
    return datetime.fromtimestamp(now if now is not None else time.time(), tz=timezone.utc).isoformat()


# Store protocols. Implement these against a durable, replica-shared store.


class CounterStore(Protocol):
    async def try_reserve(self, key: str, limit: int, ttl_seconds: int) -> tuple[bool, int]:
        """Atomically increment `key` if the current value is below `limit`.

        Returns (reserved, value_after_or_current). Must be atomic across replicas,
        for example a Cosmos DB conditional patch with a filter predicate
        (`FROM c WHERE c.count < @limit`), or a replace with an If-Match ETag
        inside a bounded retry loop.
        """

    async def release(self, key: str) -> None:
        """Compensating decrement after a reservation whose call was refused."""


class ApprovalStore(Protocol):
    async def put(self, record: "ApprovalRecord") -> None: ...

    async def get(self, token: str) -> "ApprovalRecord | None": ...

    async def mark_used(self, token: str, expected_version: int) -> bool:
        """Conditional update: succeed only if nobody consumed it first."""


class DraftStore(Protocol):
    async def owner_of(self, draft_id: str) -> tuple[str, str] | None:
        """Return (principal_key, draft_family) for a draft, or None."""


class AuditSink(Protocol):
    async def record(self, event: Mapping[str, Any]) -> None: ...


@dataclass
class ApprovalRecord:
    token: str
    principal_key: str
    tool: str
    payload_hash: str
    expires_at: float
    used: bool = False
    version: int = 0


class ApprovalService:
    """Mints and consumes single-use approvals bound to principal, tool, and payload.

    An approval is consent to one specific payload. A change to the recipient,
    the amount, or the record id after approval produces a different hash and
    the token no longer matches. That is the property that makes "approve then
    send" safe against a model that rewrites the message between the two steps.

    Minting happens on a path the model cannot reach: an Adaptive Card
    `Action.Execute` handled by the bot, a host confirmation callback, or an
    approvals workflow. If the model could call `mint`, approval would be theatre.
    """

    def __init__(self, store: ApprovalStore, ttl_seconds: int = DEFAULT_APPROVAL_TTL_SECONDS) -> None:
        self._store = store
        self._ttl = ttl_seconds

    async def mint(self, principal: Principal, tool: str, payload: Mapping[str, Any], *, now: float | None = None) -> str:
        if principal.unattended:
            raise SideEffectDenied("approval_requires_attended_principal", "Only a signed-in user can approve an action.")
        token = "apr_" + secrets.token_hex(16)
        record = ApprovalRecord(
            token=token,
            principal_key=principal.key,
            tool=tool,
            payload_hash=canonical_payload_hash(payload),
            expires_at=(now if now is not None else time.time()) + self._ttl,
        )
        await self._store.put(record)
        return token

    async def consume(self, token: str, principal: Principal, tool: str, payload_hash: str, *, now: float | None = None) -> str | None:
        """Return None on success, or a stable refusal reason."""
        record = await self._store.get(token)
        current = now if now is not None else time.time()
        if record is None:
            return "approval_not_found"
        if record.used:
            return "approval_already_used"
        if record.principal_key != principal.key:
            return "approval_belongs_to_another_principal"
        if record.tool != tool:
            return "approval_minted_for_a_different_tool"
        if record.payload_hash != payload_hash:
            return "approval_does_not_match_this_payload"
        if current >= record.expires_at:
            return "approval_expired"
        if not await self._store.mark_used(token, expected_version=record.version):
            return "approval_already_used"
        return None


class KillSwitch:
    """Environment-driven write switch, read on every call.

    The default is fail-closed: writes are refused unless an operator explicitly
    enables them. A per-tool deny list lets on-call engineers disable one bad tool
    without stopping every write. In Azure Container Apps, changing an environment
    variable creates a new revision; if you need a sub-minute stop, read the same
    keys from Azure App Configuration with a short refresh interval instead.
    """

    def __init__(self, environ: Mapping[str, str] | None = None) -> None:
        self._environ = environ

    def _env(self) -> Mapping[str, str]:
        return self._environ if self._environ is not None else os.environ

    def writes_enabled(self) -> bool:
        return self._env().get(WRITES_ENABLED_ENV, "false").strip().lower() in {"1", "true", "yes", "on"}

    def tool_disabled(self, tool: str) -> bool:
        raw = self._env().get(DISABLED_TOOLS_ENV, "")
        return tool in {t.strip() for t in raw.split(",") if t.strip()}


class SideEffectGate:
    def __init__(
        self,
        *,
        counters: CounterStore,
        approvals: ApprovalService,
        drafts: DraftStore,
        audit: AuditSink,
        kill_switch: KillSwitch | None = None,
    ) -> None:
        self._policies: dict[str, ToolPolicy] = {}
        self._counters = counters
        self._approvals = approvals
        self._drafts = drafts
        self._audit = audit
        self._kill = kill_switch or KillSwitch()

    def register(self, policy: ToolPolicy) -> None:
        if policy.name in self._policies:
            raise ValueError(f"duplicate tool registration: {policy.name}")
        self._policies[policy.name] = policy

    def policy(self, tool: str) -> ToolPolicy:
        try:
            return self._policies[tool]
        except KeyError:
            raise SideEffectDenied("unregistered_tool", "This tool has no side-effect policy.", tool=tool) from None

    def tool_annotations(self) -> dict[str, dict[str, Any]]:
        return {name: p.as_mcp_annotations() for name, p in sorted(self._policies.items())}

    async def check(self, request: ActionRequest, *, now: float | None = None) -> GateReceipt:
        payload_hash = canonical_payload_hash(request.payload)
        try:
            receipt = await self._check(request, payload_hash, now)
        except SideEffectDenied as denied:
            await self._audit_decision(request, payload_hash, allowed=False, code=denied.code, now=now)
            raise
        await self._audit_decision(request, payload_hash, allowed=True, code="allowed", now=now, receipt=receipt)
        return receipt

    async def _check(self, req: ActionRequest, payload_hash: str, now: float | None) -> GateReceipt:
        policy = self.policy(req.tool)
        rank = policy.side_effect.rank
        principal = req.principal

        if rank == 0:
            return self._receipt(policy, principal, payload_hash, None, None, now)

        if not self._kill.writes_enabled():
            raise SideEffectDenied("writes_disabled", "Writes are disabled by the operator kill switch.")
        if self._kill.tool_disabled(policy.name):
            raise SideEffectDenied("tool_disabled", "This tool is temporarily disabled by the operator.", tool=policy.name)

        ceiling = principal.delegated_ceiling.rank if principal.delegated_ceiling else UNATTENDED_CEILING_RANK
        if principal.unattended and rank > min(ceiling, UNATTENDED_CEILING_RANK):
            raise SideEffectDenied(
                "unattended_side_effect_refused",
                f"Unattended runs may prepare work (up to W2) but may not perform {policy.side_effect.label} actions.",
            )

        if policy.recipient_domains is not None:
            for recipient in req.recipients:
                domain = recipient.rsplit("@", 1)[-1].lower() if "@" in recipient else ""
                if domain not in policy.recipient_domains:
                    raise SideEffectDenied("recipient_not_allowed", "A recipient is outside the allowed domains for this tool.")

        if policy.max_fanout is not None and req.fanout > policy.max_fanout:
            raise SideEffectDenied(
                "fanout_exceeded",
                "This call touches more records or recipients than the tool allows.",
                max_fanout=policy.max_fanout,
                requested_fanout=req.fanout,
            )

        if policy.requires_draft:
            if not req.draft_id:
                raise SideEffectDenied("draft_required", "Create a draft first, then send that draft.")
            owner = await self._drafts.owner_of(req.draft_id)
            if owner is None:
                raise SideEffectDenied("draft_not_found", "The referenced draft does not exist.")
            owner_key, family = owner
            if owner_key != principal.key:
                raise SideEffectDenied("draft_belongs_to_another_principal", "That draft was created by someone else.")
            if family != policy.draft_family:
                raise SideEffectDenied("draft_family_mismatch", "That draft was created for a different kind of action.")

        if policy.requires_approval and not req.approval_token:
            raise SideEffectDenied(
                "approval_required",
                "This action needs explicit approval for this exact payload.",
                payload_hash=payload_hash,
                tool=policy.name,
            )

        counter_value: int | None = None
        counter_key = f"{principal.key}|{policy.name}|{_utc_day(now)}"
        if policy.daily_cap is not None:
            reserved, value = await self._counters.try_reserve(counter_key, policy.daily_cap, ttl_seconds=2 * 86400)
            if not reserved:
                raise SideEffectDenied("daily_cap_reached", "Daily limit reached for this action.", cap=policy.daily_cap, used=value)
            counter_value = value

        if policy.requires_approval:
            assert req.approval_token is not None
            reason = await self._approvals.consume(req.approval_token, principal, policy.name, payload_hash, now=now)
            if reason is not None:
                if policy.daily_cap is not None:
                    await self._counters.release(counter_key)
                raise SideEffectDenied(reason, "The approval token was not accepted for this call.", payload_hash=payload_hash)

        return self._receipt(policy, principal, payload_hash, counter_value, req.approval_token, now)

    @staticmethod
    def _receipt(policy: ToolPolicy, principal: Principal, payload_hash: str, counter: int | None, token: str | None, now: float | None) -> GateReceipt:
        return GateReceipt(
            tool=policy.name,
            side_effect=policy.side_effect,
            principal_key=principal.key,
            payload_hash=payload_hash,
            counter_value=counter,
            approval_token=token,
            decided_at=_iso(now),
        )

    async def _audit_decision(
        self,
        req: ActionRequest,
        payload_hash: str,
        *,
        allowed: bool,
        code: str,
        now: float | None,
        receipt: GateReceipt | None = None,
    ) -> None:
        policy = self._policies.get(req.tool)
        if policy is not None and policy.side_effect is SideEffect.R and allowed:
            return
        await self._audit.record(
            {
                "event": "side_effect_decision",
                "at": _iso(now),
                "allowed": allowed,
                "code": code,
                "tool": req.tool,
                "side_effect": policy.side_effect.label if policy else None,
                "principal_key": req.principal.key,
                "surface": req.principal.surface,
                "unattended": req.principal.unattended,
                "payload_hash": payload_hash,
                "fanout": req.fanout,
                "counter_value": receipt.counter_value if receipt else None,
                "correlation_id": req.correlation_id,
            }
        )


# In-memory reference stores (tests and demos only).


class InMemoryCounterStore:
    def __init__(self) -> None:
        self._values: dict[str, int] = {}
        self._lock = asyncio.Lock()

    async def try_reserve(self, key: str, limit: int, ttl_seconds: int) -> tuple[bool, int]:
        async with self._lock:
            current = self._values.get(key, 0)
            if current >= limit:
                return False, current
            self._values[key] = current + 1
            return True, current + 1

    async def release(self, key: str) -> None:
        async with self._lock:
            self._values[key] = max(0, self._values.get(key, 0) - 1)

    def value(self, key: str) -> int:
        return self._values.get(key, 0)


class InMemoryApprovalStore:
    def __init__(self) -> None:
        self._records: dict[str, ApprovalRecord] = {}
        self._lock = asyncio.Lock()

    async def put(self, record: ApprovalRecord) -> None:
        async with self._lock:
            self._records[record.token] = record

    async def get(self, token: str) -> ApprovalRecord | None:
        async with self._lock:
            rec = self._records.get(token)
            return None if rec is None else ApprovalRecord(**rec.__dict__)

    async def mark_used(self, token: str, expected_version: int) -> bool:
        async with self._lock:
            rec = self._records.get(token)
            if rec is None or rec.used or rec.version != expected_version:
                return False
            rec.used = True
            rec.version += 1
            return True


class InMemoryDraftStore:
    def __init__(self) -> None:
        self._drafts: dict[str, tuple[str, str]] = {}

    def add(self, draft_id: str, principal: Principal, family: str) -> None:
        self._drafts[draft_id] = (principal.key, family)

    async def owner_of(self, draft_id: str) -> tuple[str, str] | None:
        return self._drafts.get(draft_id)


@dataclass
class ListAuditSink:
    events: list[dict[str, Any]] = field(default_factory=list)

    async def record(self, event: Mapping[str, Any]) -> None:
        self.events.append(dict(event))


def default_policies(allowed_domains: frozenset[str]) -> list[ToolPolicy]:
    """A domain-neutral tool set. Rename the tools for your domain; keep the classes.

    Examples of the same shape in other domains:
      * IT and security operations: `get_incident_summary` (R),
        `add_triage_note` (W1), `draft_containment_change` (W2),
        `submit_change_request` (W3), `isolate_hosts` (W4).
      * Financial risk and compliance: `get_exposure` (R), `flag_for_review` (W1),
        `draft_control_exception` (W2), `file_exception` (W3), `post_adjustment` (W4).
      * Customer service: `get_case` (R), `snooze_case` (W1), `draft_reply` (W2),
        `send_reply` (W3), `bulk_close_cases` (W4).
    """
    return [
        ToolPolicy("get_entity_summary", SideEffect.R, "Read a governed summary of one entity"),
        ToolPolicy("save_private_note", SideEffect.W1, "Save a note visible only to the caller", daily_cap=200),
        ToolPolicy("snooze_item", SideEffect.W1, "Hide an item from the caller's queue until a date", daily_cap=200),
        ToolPolicy("create_message_draft", SideEffect.W2, "Create an unsent message draft", daily_cap=50),
        ToolPolicy(
            "send_message_draft",
            SideEffect.W3,
            "Send a previously created draft",
            daily_cap=20,
            requires_draft=True,
            draft_family="message",
            requires_approval=True,
            max_fanout=10,
            recipient_domains=allowed_domains,
            open_world=True,
        ),
        ToolPolicy(
            "bulk_close_records",
            SideEffect.W4,
            "Close several records in the system of record",
            daily_cap=3,
            requires_approval=True,
            max_fanout=25,
        ),
    ]


async def _self_test() -> None:
    env: dict[str, str] = {WRITES_ENABLED_ENV: "true"}
    approvals_store = InMemoryApprovalStore()
    approvals = ApprovalService(approvals_store)
    drafts = InMemoryDraftStore()
    counters = InMemoryCounterStore()
    audit = ListAuditSink()
    gate = SideEffectGate(counters=counters, approvals=approvals, drafts=drafts, audit=audit, kill_switch=KillSwitch(env))
    for policy in default_policies(frozenset({"contoso.com"})):
        gate.register(policy)

    try:
        ToolPolicy("unsafe_send", SideEffect.W3, "W3 without approval")
        raise AssertionError("W3 without approval must be rejected at registration")
    except ValueError:
        pass

    user_a = Principal("tenant-0001", "oid-user-a", "user.a@contoso.com", surface="custom-engine-agent")
    user_b = Principal("tenant-0001", "oid-user-b", "user.b@contoso.com", surface="declarative-agent")
    scheduled_a = Principal("tenant-0001", "oid-user-a", "user.a@contoso.com", surface="scheduler", unattended=True)

    results: list[tuple[str, str]] = []

    async def attempt(label: str, request: ActionRequest, expect: str) -> None:
        try:
            await gate.check(request)
            outcome = "allowed"
        except SideEffectDenied as denied:
            outcome = denied.code
            assert denied.as_payload()["refused_by"] == "side_effect_gate"
        assert outcome == expect, f"{label}: expected {expect}, got {outcome}"
        results.append((label, outcome))

    def send(principal: Principal, payload: dict[str, Any], *, draft: str | None = "drf_a", token: str | None = None) -> ActionRequest:
        to = payload.get("to", [])
        return ActionRequest("send_message_draft", principal, payload, recipients=to, draft_id=draft, approval_token=token, fanout=len(to) or 1)

    drafts.add("drf_a", user_a, "message")
    drafts.add("drf_b", user_b, "message")
    drafts.add("drf_note", user_a, "note")
    payload_a = {"draft_id": "drf_a", "to": ["owner@contoso.com"], "subject": "Status update"}
    payload_b = {"draft_id": "drf_b", "to": ["owner@contoso.com"], "subject": "Status update"}

    await attempt("read passes", ActionRequest("get_entity_summary", user_a, {"entity_id": "E-1"}), "allowed")
    await attempt("unknown tool fails closed", ActionRequest("drop_table", user_a, {}), "unregistered_tool")
    await attempt("send without draft", send(user_a, {"to": ["x@contoso.com"]}, draft=None), "draft_required")
    await attempt("send unknown draft", send(user_a, payload_a, draft="drf_missing"), "draft_not_found")
    await attempt("send someone else's draft", send(user_a, payload_a, draft="drf_b"), "draft_belongs_to_another_principal")
    await attempt("send a non-message draft", send(user_a, payload_a, draft="drf_note"), "draft_family_mismatch")
    await attempt("send without approval", send(user_a, payload_a), "approval_required")
    await attempt("unknown approval token", send(user_a, payload_a, token="apr_doesnotexist"), "approval_not_found")

    token_a = await approvals.mint(user_a, "send_message_draft", payload_a)
    await attempt("user B uses user A's token", send(user_b, payload_b, draft="drf_b", token=token_a), "approval_belongs_to_another_principal")
    tampered = dict(payload_a, to=["owner@contoso.com", "lead@contoso.com"])
    await attempt("payload changed after approval", send(user_a, tampered, token=token_a), "approval_does_not_match_this_payload")
    wrong_tool = await approvals.mint(user_a, "bulk_close_records", payload_a)
    await attempt("token minted for another tool", send(user_a, payload_a, token=wrong_tool), "approval_minted_for_a_different_tool")
    stale = await approvals.mint(user_a, "send_message_draft", payload_a, now=time.time() - 3600)
    await attempt("expired approval", send(user_a, payload_a, token=stale), "approval_expired")

    send_key = f"{user_a.key}|send_message_draft|{_utc_day()}"
    assert counters.value(send_key) == 0, "refused approvals must release their cap reservation"

    await attempt("approved send", send(user_a, payload_a, token=token_a), "allowed")
    await attempt("token replay", send(user_a, payload_a, token=token_a), "approval_already_used")
    assert counters.value(send_key) == 1

    external = dict(payload_a, to=["someone@external.example"])
    await attempt("external recipient", send(user_a, external, token=token_a), "recipient_not_allowed")
    await attempt("unattended W3", send(scheduled_a, payload_a), "unattended_side_effect_refused")
    await attempt("unattended W2 allowed", ActionRequest("create_message_draft", scheduled_a, {"subject": "prep"}), "allowed")
    try:
        await approvals.mint(scheduled_a, "send_message_draft", payload_a)
        raise AssertionError("unattended principals must not mint approvals")
    except SideEffectDenied as denied:
        results.append(("unattended approval mint", denied.code))

    await attempt("bulk fan-out", ActionRequest("bulk_close_records", user_a, {"ids": list(range(40))}, fanout=40), "fanout_exceeded")
    for i in range(3):
        payload = {"ids": [i], "reason": "duplicate"}
        tok = await approvals.mint(user_a, "bulk_close_records", payload)
        await attempt(f"bulk close #{i + 1}", ActionRequest("bulk_close_records", user_a, payload, approval_token=tok), "allowed")
    payload = {"ids": [99], "reason": "duplicate"}
    tok = await approvals.mint(user_a, "bulk_close_records", payload)
    await attempt("cap reached", ActionRequest("bulk_close_records", user_a, payload, approval_token=tok), "daily_cap_reached")
    unburned = await approvals_store.get(tok)
    assert unburned is not None and not unburned.used, "a cap refusal must not consume the approval"

    env[DISABLED_TOOLS_ENV] = "save_private_note"
    await attempt("per-tool disable", ActionRequest("save_private_note", user_a, {"text": "x"}), "tool_disabled")
    env[WRITES_ENABLED_ENV] = "false"
    await attempt("global kill switch", ActionRequest("snooze_item", user_a, {"item": "I-1"}), "writes_disabled")
    await attempt("kill switch spares reads", ActionRequest("get_entity_summary", user_a, {"entity_id": "E-1"}), "allowed")

    annotations = gate.tool_annotations()
    assert annotations["send_message_draft"]["destructiveHint"] is True
    assert annotations["get_entity_summary"]["readOnlyHint"] is True
    assert all("read_only_hint" not in a for a in annotations.values())

    width = max(len(label) for label, _ in results)
    for label, outcome in results:
        print(f"  {label.ljust(width)}  ->  {outcome}")
    denied = sum(1 for e in audit.events if not e["allowed"])
    print(f"\n{len(results)} scenarios passed; {len(audit.events)} audit events ({denied} refusals).")


if __name__ == "__main__":
    asyncio.run(_self_test())
