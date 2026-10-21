#!/usr/bin/env python3
"""Hosted agent operations simulator (stdlib only, Python 3.10+).

Companion to the "Production Hosted Agents on Microsoft Foundry" reference
architecture. It models the operational behaviours described in
docs/architecture.md with in-memory fakes, so you can read, run and extend
them without an Azure subscription:

* network gating: data-plane calls from outside the virtual network get 403
* role-based authorization per data-plane operation, with clear messages
  (control-plane roles such as Contributor grant no data-plane actions)
* a per-audience token cache with expiry and refresh skew
* the session lifecycle (CREATED, ACTIVE, IDLE, EXPIRED, CLOSED, plus FAILED)
  with version pinning, heartbeats that extend the idle timeout, and expiry
* invoke with bounded retry, error classification and an idempotency key,
  so a retried turn never repeats a side effect
* orphan session cleanup (dry run, keep list, apply)
* a release pipeline (build, scan, sign, push, verify roles, create version,
  smoke test inside the VNet, evaluation gate, promote) with rollback
* OpenTelemetry-like span records that carry the caller's correlation ID
* a keep-warm anti-pattern detector

TEST ONLY. All times are logical clock ticks, not seconds, and say nothing
about real latency, cold start, capacity or cost. Nothing here makes a
network call. Status codes, header names, session states and role names
model behaviour observed in preview; verify them against Microsoft Learn
before relying on them. FoundryHostedAgentAdapter is a gated stub: it
refuses to construct unless you opt in, and even then it is not implemented.

Usage:
    python hosted_agent_ops_sim.py --self-test
    python hosted_agent_ops_sim.py --demo
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from dataclasses import dataclass, field, replace
from typing import Any, Callable, Protocol

# ---------------------------------------------------------------------------
# Fictional world. Every value is a placeholder.
# ---------------------------------------------------------------------------

AGENT_NAME = "contoso-research-agent"
PROJECT_ENDPOINT = "https://<foundry-account>.services.ai.azure.com/api/projects/<project>"
APPROVED_BASE = "<registry>.azurecr.io/approved/python:3.12-slim@sha256:<digest>"

AUD_FOUNDRY = "https://ai.azure.com/.default"
AUD_MODELS = "https://cognitiveservices.azure.com/.default"
AUD_COSMOS = "https://<cosmos-account>.documents.azure.com/.default"
AUD_MONITOR = "https://monitor.azure.com/.default"
AUD_GATEWAY = "api://<gateway-app-id>/.default"

# Principal names stand in for managed identity object IDs.
PIPELINE = "contoso-release-pipeline"
RUNTIME = "contoso-research-agent-runtime"
PROJECT_IDENTITY = "contoso-project-identity"
CALLER = "contoso-research-gateway"
OPERATOR = "contoso-agent-operator"
DEVELOPER = "contoso-developer"

SCOPE_PROJECT = "foundry-project"
SCOPE_ACCOUNT = "foundry-account"
SCOPE_REGISTRY = "container-registry"
SCOPE_COSMOS = "cosmos-account"
SCOPE_GATEWAY = "tool-gateway"

# Public built-in role names; verify the current names on Microsoft Learn.
ROLE_AI_USER = "Foundry User"
ROLE_OPENAI_USER = "Cognitive Services OpenAI User"
ROLE_COSMOS_DATA = "Cosmos DB Built-in Data Contributor"
ROLE_ACR_PUSH = "AcrPush"
ROLE_ACR_PULL = "AcrPull"
ROLE_CONTRIBUTOR = "Contributor"
CONTROL_PLANE_ROLES = frozenset({ROLE_CONTRIBUTOR, "Owner", "Reader"})

# Arbitrary logical ticks. They only order events; they are not seconds.
PROVISION_TICKS = 1
SERVER_WARMUP_TICKS = 2
IDLE_AFTER_TICKS = 3
IDLE_TIMEOUT_TICKS = 8
TOKEN_LIFETIME_TICKS = 30
REFRESH_SKEW_TICKS = 5
SWEEP_IDLE_WINDOW_TICKS = 5
TELEMETRY_BATCH_SIZE = 4

CREATED = "CREATED"
ACTIVE = "ACTIVE"
IDLE = "IDLE"
EXPIRED = "EXPIRED"
CLOSED = "CLOSED"
FAILED = "FAILED"
LIVE_STATES = frozenset({CREATED, ACTIVE, IDLE})
TERMINAL_STATES = frozenset({EXPIRED, CLOSED, FAILED})

DEFAULT_ENV = {
    "AGENT_MODEL_DEPLOYMENT": "<model-deployment>",
    "AGENT_COSMOS_ENDPOINT": "https://<cosmos-account>.documents.azure.com:443/",
    "AGENT_GATEWAY_URL": "https://<gateway-host>/mcp",
}


@dataclass(frozen=True)
class Operation:
    role: str | None
    scope: str
    audience: str | None
    destination: str


_FOUNDRY_OP = Operation(ROLE_AI_USER, SCOPE_PROJECT, AUD_FOUNDRY, "foundry")

OPERATIONS: dict[str, Operation] = {
    "agents.create_version": _FOUNDRY_OP,
    "agents.set_active_version": _FOUNDRY_OP,
    "agents.read": _FOUNDRY_OP,
    "sessions.create": _FOUNDRY_OP,
    "sessions.get": _FOUNDRY_OP,
    "sessions.list": _FOUNDRY_OP,
    "sessions.end": _FOUNDRY_OP,
    "sessions.heartbeat": _FOUNDRY_OP,
    "responses.invoke": _FOUNDRY_OP,
    "registry.push": Operation(ROLE_ACR_PUSH, SCOPE_REGISTRY, None, "registry"),
    "registry.pull": Operation(ROLE_ACR_PULL, SCOPE_REGISTRY, None, "registry"),
    "models.chat": Operation(ROLE_OPENAI_USER, SCOPE_ACCOUNT, AUD_MODELS, "models"),
    "cosmos.read": Operation(ROLE_COSMOS_DATA, SCOPE_COSMOS, AUD_COSMOS, "cosmos"),
    "cosmos.write": Operation(ROLE_COSMOS_DATA, SCOPE_COSMOS, AUD_COSMOS, "cosmos"),
    "gateway.tool": Operation(None, SCOPE_GATEWAY, AUD_GATEWAY, "gateway"),
}


# ---------------------------------------------------------------------------
# Errors and retry classification
# ---------------------------------------------------------------------------


class DataPlaneError(Exception):
    """A data-plane failure. Status 0 means the connection was reset."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(f"{status} {code}: {message}")
        self.status = status
        self.code = code
        self.message = message


class PipelineFailure(Exception):
    """A release stage failed for a reason that is not a data-plane error."""


RETRYABLE_STATUS = frozenset({424, 429, 500, 502, 503, 504})
NON_RETRYABLE_CODES = frozenset(
    {"configuration_error", "dependency_authorization_failed", "egress_blocked", "session_failed"}
)


def classify(exc: DataPlaneError) -> str:
    """Return 'retry', 'recover' or 'fail' for an invoke error."""
    if exc.code in NON_RETRYABLE_CODES:
        return "fail"
    if exc.status == 404 and exc.code == "session_not_found":
        return "recover"
    if exc.status == 0 or exc.status in RETRYABLE_STATUS:
        return "retry"
    return "fail"


def backoff(attempt: int) -> int:
    # Deterministic exponential backoff in ticks. Real clients add jitter.
    return min(2 ** (attempt - 1), 8)


# ---------------------------------------------------------------------------
# Protocol seams. Swap the fakes for real adapters behind these.
# ---------------------------------------------------------------------------


class Clock(Protocol):
    def now(self) -> int: ...

    def advance(self, ticks: int = 1) -> int: ...


class TokenProvider(Protocol):
    def get_token(self, audience: str) -> "AccessToken": ...


class NetworkContext(Protocol):
    name: str

    def inside_vnet(self) -> bool: ...

    def can_reach(self, destination: str) -> bool: ...


class SessionStore(Protocol):
    def get_record(self, key: str) -> dict | None: ...

    def put_record(self, key: str, record: dict) -> None: ...

    def load_state(self, conversation_key: str) -> dict: ...

    def save_state(self, conversation_key: str, state: dict) -> None: ...

    def get_result(self, ref: str) -> str | None: ...

    def put_result(self, ref: str, output: str) -> None: ...


class TelemetrySink(Protocol):
    def export(self, span: dict) -> None: ...


class AgentRuntime(Protocol):
    def create_version(self, ctx: "CallContext", image_digest: str, env: dict) -> str: ...

    def set_active_version(self, ctx: "CallContext", version: str) -> None: ...

    def get_active_version(self, ctx: "CallContext") -> str | None: ...

    def create_session(
        self, ctx: "CallContext", version: str | None = None, session_id: str | None = None
    ) -> dict: ...

    def get_session(self, ctx: "CallContext", session_id: str) -> dict: ...

    def list_sessions(self, ctx: "CallContext") -> list[dict]: ...

    def end_session(self, ctx: "CallContext", session_id: str, reason: str = "client_end") -> dict: ...

    def heartbeat(self, ctx: "CallContext", session_id: str) -> dict: ...

    def invoke(
        self, ctx: "CallContext", session_id: str | None, request: dict, headers: dict
    ) -> dict: ...


# ---------------------------------------------------------------------------
# Clock, tokens, network, role assignments
# ---------------------------------------------------------------------------


class LogicalClock:
    def __init__(self, start: int = 0) -> None:
        self._t = start

    def now(self) -> int:
        return self._t

    def advance(self, ticks: int = 1) -> int:
        self._t += ticks
        return self._t


@dataclass(frozen=True)
class AccessToken:
    principal: str
    audience: str
    expires_at: int
    serial: int

    def __repr__(self) -> str:
        return (
            f"AccessToken(principal={self.principal!r}, audience={self.audience!r}, "
            f"expires_at={self.expires_at}, serial={self.serial}, value=<redacted>)"
        )


class FakeEntraIssuer:
    """Issues opaque tokens for a principal and audience. Records every issuance."""

    def __init__(self, clock: Clock, lifetime: int = TOKEN_LIFETIME_TICKS) -> None:
        self.clock = clock
        self.lifetime = lifetime
        self.issued: list[tuple[str, str]] = []

    def issue(self, principal: str, audience: str) -> AccessToken:
        self.issued.append((principal, audience))
        return AccessToken(principal, audience, self.clock.now() + self.lifetime, len(self.issued))

    def count(self, principal: str | None = None, audience: str | None = None) -> int:
        return sum(
            1
            for p, a in self.issued
            if (principal is None or p == principal) and (audience is None or a == audience)
        )


class CachedTokenProvider:
    """One cache entry per audience; refresh when within the skew of expiry."""

    def __init__(
        self, principal: str, issuer: FakeEntraIssuer, clock: Clock, skew: int = REFRESH_SKEW_TICKS
    ) -> None:
        self.principal = principal
        self.issuer = issuer
        self.clock = clock
        self.skew = skew
        self._cache: dict[str, AccessToken] = {}
        self.fetches = 0
        self.hits = 0

    def get_token(self, audience: str) -> AccessToken:
        cached = self._cache.get(audience)
        if cached is not None and self.clock.now() < cached.expires_at - self.skew:
            self.hits += 1
            return cached
        token = self.issuer.issue(self.principal, audience)
        self._cache[audience] = token
        self.fetches += 1
        return token

    def cached_audiences(self) -> list[str]:
        return sorted(self._cache)


@dataclass(frozen=True)
class Placement:
    """Where a caller runs. Implements NetworkContext."""

    name: str
    in_vnet: bool
    egress_allow: frozenset = frozenset()

    def inside_vnet(self) -> bool:
        return self.in_vnet

    def can_reach(self, destination: str) -> bool:
        return destination in self.egress_allow


INSIDE = Placement("ci-runner-in-vnet", True, frozenset({"entra", "foundry", "registry"}))
INTERNET = Placement("public-hosted-runner", False, frozenset({"entra", "foundry", "registry"}))
SANDBOX = Placement(
    "agent-sandbox", True, frozenset({"entra", "models", "cosmos", "gateway", "monitor"})
)
PLATFORM_PULL = Placement("foundry-platform-pull", True, frozenset({"registry"}))


class RoleAssignments:
    def __init__(self) -> None:
        self._grants: set[tuple[str, str, str]] = set()

    def grant(self, principal: str, role: str, scope: str) -> None:
        self._grants.add((principal, role, scope))

    def revoke(self, principal: str, role: str, scope: str) -> None:
        self._grants.discard((principal, role, scope))

    def has(self, principal: str, role: str, scope: str) -> bool:
        return (principal, role, scope) in self._grants

    def roles_of(self, principal: str, scope: str | None = None) -> list[str]:
        return sorted({r for p, r, s in self._grants if p == principal and (scope is None or s == scope)})


def check_data_plane(
    op_name: str,
    principal: str,
    token: AccessToken | None,
    network: NetworkContext,
    rbac: RoleAssignments,
    clock: Clock,
) -> None:
    """Order matters: placement, then token, then role. Mirrors what callers observe."""
    op = OPERATIONS[op_name]
    if not network.inside_vnet():
        raise DataPlaneError(
            403,
            "public_access_disabled",
            f"{op_name} from '{network.name}' rejected: public network access is disabled; "
            "call from inside the virtual network through the private endpoint",
        )
    if op.audience is not None:
        if token is None:
            raise DataPlaneError(401, "missing_token", f"{op_name} requires a token for {op.audience}")
        if token.audience != op.audience:
            raise DataPlaneError(
                401,
                "invalid_audience",
                f"{op_name} requires audience '{op.audience}' but the token was issued for "
                f"'{token.audience}'",
            )
        if clock.now() >= token.expires_at:
            raise DataPlaneError(
                401, "token_expired", f"{op_name}: token expired at tick {token.expires_at}"
            )
    if op.role is not None and not rbac.has(principal, op.role, op.scope):
        message = (
            f"principal '{principal}' lacks role '{op.role}' on scope '{op.scope}' "
            f"required for {op_name}"
        )
        held = [r for r in rbac.roles_of(principal) if r in CONTROL_PLANE_ROLES]
        if held:
            message += (
                f"; it holds control-plane role(s) {', '.join(held)}, "
                "which grant no data-plane actions"
            )
        raise DataPlaneError(403, "authorization_failed", message)


@dataclass
class CallContext:
    """Who is calling, from where, and which token provider they use."""

    principal: str
    network: Placement
    tokens: TokenProvider
    audience: str = AUD_FOUNDRY

    def token(self) -> AccessToken:
        return self.tokens.get_token(self.audience)


# ---------------------------------------------------------------------------
# Trace helpers
# ---------------------------------------------------------------------------


def _hex(seed: str, length: int) -> str:
    out = ""
    i = 0
    while len(out) < length:
        out += hashlib.sha256(f"{seed}:{i}".encode()).hexdigest()
        i += 1
    return out[:length]


def make_traceparent(trace_id: str, span_id: str) -> str:
    return f"00-{trace_id}-{span_id}-01"


def parse_traceparent(value: str) -> tuple[str, str] | None:
    parts = value.split("-")
    if len(parts) != 4 or len(parts[1]) != 32 or len(parts[2]) != 16:
        return None
    return parts[1], parts[2]


# ---------------------------------------------------------------------------
# Registry, state store, side-effect ledger, telemetry
# ---------------------------------------------------------------------------


class PrivateRegistry:
    """Private container registry reachable only through its private endpoint."""

    def __init__(self, rbac: RoleAssignments, clock: Clock) -> None:
        self.rbac = rbac
        self.clock = clock
        self.images: dict[str, dict] = {}
        self.deleted: set[str] = set()
        self.pulls: list[tuple[str, str]] = []

    def push(self, principal: str, network: NetworkContext, image: dict) -> str:
        check_data_plane("registry.push", principal, None, network, self.rbac, self.clock)
        digest = "sha256:" + hashlib.sha256(json.dumps(image, sort_keys=True).encode()).hexdigest()
        self.images[digest] = dict(image)
        self.deleted.discard(digest)
        return digest

    def pull(self, principal: str, network: NetworkContext, digest: str) -> dict:
        check_data_plane("registry.pull", principal, None, network, self.rbac, self.clock)
        if digest not in self.images:
            hint = " (deleted; check registry retention and purge policies)" if digest in self.deleted else ""
            raise DataPlaneError(
                404, "manifest_unknown", f"image {digest[:19]}... not found in the private registry{hint}"
            )
        self.pulls.append((principal, digest))
        return self.images[digest]

    def delete(self, digest: str) -> None:
        self.images.pop(digest, None)
        self.deleted.add(digest)


class InMemoryStateStore:
    """Stand-in for the Cosmos DB containers: idempotency records, state, results."""

    def __init__(self) -> None:
        self.records: dict[str, dict] = {}
        self.states: dict[str, dict] = {}
        self.results: dict[str, str] = {}
        self.writes = 0

    def get_record(self, key: str) -> dict | None:
        record = self.records.get(key)
        return dict(record) if record else None

    def put_record(self, key: str, record: dict) -> None:
        self.records[key] = dict(record)
        self.writes += 1

    def load_state(self, conversation_key: str) -> dict:
        return dict(self.states.get(conversation_key, {"turns": 0, "notes": 0}))

    def save_state(self, conversation_key: str, state: dict) -> None:
        self.states[conversation_key] = dict(state)
        self.writes += 1

    def get_result(self, ref: str) -> str | None:
        return self.results.get(ref)

    def put_result(self, ref: str, output: str) -> None:
        self.results[ref] = output
        self.writes += 1


class ToolLedger:
    """An external side effect (a ticket, a write to a system of record).

    It deliberately does not deduplicate, so the tests prove that
    idempotency lives in the agent, not in the downstream system.
    """

    def __init__(self) -> None:
        self.entries: list[dict] = []

    def apply(self, conversation_key: str, action: str, idempotency_key: str) -> None:
        self.entries.append({"conversation_key": conversation_key, "action": action, "key": idempotency_key})

    def count(self, conversation_key: str | None = None) -> int:
        return sum(1 for e in self.entries if conversation_key is None or e["conversation_key"] == conversation_key)


class InMemoryTelemetrySink:
    """Stand-in for Application Insights behind an OTLP or Azure Monitor exporter."""

    def __init__(self) -> None:
        self.spans: list[dict] = []

    def export(self, span: dict) -> None:
        self.spans.append(dict(span))

    def find(self, **attributes: Any) -> list[dict]:
        return [
            s for s in self.spans if all(s["attributes"].get(k) == v for k, v in attributes.items())
        ]


class BatchingExporter:
    """In-container span buffer. Flushes on batch size and on shutdown.

    If egress to the monitoring endpoint is blocked the batch is dropped
    and a warning is recorded. The agent turn itself still succeeds, which
    is exactly why silent telemetry loss needs its own alert.
    """

    def __init__(
        self, sink: TelemetrySink, network: NetworkContext, batch_size: int = TELEMETRY_BATCH_SIZE
    ) -> None:
        self.sink = sink
        self.network = network
        self.batch_size = batch_size
        self.buffer: list[dict] = []
        self.exported = 0
        self.dropped = 0
        self.warnings: list[str] = []

    def record(self, span: dict) -> None:
        self.buffer.append(span)
        if len(self.buffer) >= self.batch_size:
            self.flush("batch_full")

    def flush(self, reason: str = "shutdown") -> None:
        if not self.buffer:
            return
        batch, self.buffer = self.buffer, []
        if not self.network.can_reach("monitor"):
            self.dropped += len(batch)
            self.warnings.append(
                f"telemetry export blocked by egress ({reason}): dropped {len(batch)} span(s); "
                "allow the Azure Monitor ingestion endpoint or route through an approved collector"
            )
            return
        for span in batch:
            self.sink.export(span)
        self.exported += len(batch)


# ---------------------------------------------------------------------------
# The agent code that runs inside the container (one instance per session)
# ---------------------------------------------------------------------------


class HostedAgentApp:
    """What your image runs. Uses only its own managed identity for dependencies."""

    def __init__(
        self,
        version: str,
        principal: str,
        issuer: FakeEntraIssuer,
        clock: Clock,
        store: SessionStore,
        ledger: ToolLedger,
        exporter: BatchingExporter,
        rbac: RoleAssignments,
        network: Placement,
        env: dict,
        session_id: str,
    ) -> None:
        self.version = version
        self.principal = principal
        self.tokens = CachedTokenProvider(principal, issuer, clock)
        self.clock = clock
        self.store = store
        self.ledger = ledger
        self.exporter = exporter
        self.rbac = rbac
        self.network = network
        self.env = dict(env)
        self.session_id = session_id
        self.turns_handled = 0
        self.dependency_calls: list[str] = []
        self._span_seq = 0

    def handle(self, request: dict, headers: dict) -> dict:
        kind = request.get("kind", "turn")
        conversation_key = request.get("metadata", {}).get("conversation_key", "")
        first_turn = self.turns_handled == 0
        try:
            replayed = False
            if kind == "readiness":
                output = "READY"
            elif kind == "smoke":
                output = self._smoke()
            elif kind == "turn":
                output, replayed = self._turn(request, headers)
                self.turns_handled += 1
            else:
                raise DataPlaneError(400, "bad_request", f"unknown request kind '{kind}'")
        except DataPlaneError as exc:
            self._emit_span(kind, headers, conversation_key, False, first_turn, error=exc.code)
            raise
        self._emit_span(kind, headers, conversation_key, replayed, first_turn)
        return {"output": output, "agent_version": self.version, "replayed": replayed}

    def shutdown(self) -> None:
        self.exporter.flush("shutdown")

    def _turn(self, request: dict, headers: dict) -> tuple[str, bool]:
        metadata = request.get("metadata", {})
        key = headers.get("x-client-idempotency-key") or metadata.get("idempotency_key")
        conversation_key = metadata.get("conversation_key")
        if not key or not conversation_key:
            raise DataPlaneError(
                400,
                "bad_request",
                "a turn needs an idempotency key (x-client-idempotency-key) and metadata.conversation_key",
            )
        self._dependency("cosmos.read")
        record = self.store.get_record(key)
        if record and record.get("status") == "completed":
            stored = self.store.get_result(record["result_ref"])
            return stored or "", True
        state = self.store.load_state(conversation_key)
        reply = self._model(request.get("input", ""))
        tool = request.get("tool")
        if tool:
            self._dependency("gateway.tool")
            self.ledger.apply(conversation_key, tool, key)
            state["notes"] += 1
        state["turns"] += 1
        self._dependency("cosmos.write")
        ref = f"results/{conversation_key}/{state['turns']:04d}"
        output = f"{reply} turn={state['turns']} notes={state['notes']} version={self.version}"
        self.store.put_result(ref, output)
        self.store.save_state(conversation_key, state)
        self.store.put_record(
            key,
            {
                "id": key,
                "key": key,
                "conversation_key": conversation_key,
                "status": "completed",
                "agent_version": self.version,
                "result_ref": ref,
                "ttl": "<retention-policy>",
            },
        )
        return output, False

    def _smoke(self) -> str:
        self._model("smoke")
        self._dependency("cosmos.write")
        self.store.put_result(f"smoke/{self.session_id}", "ok")
        self._dependency("cosmos.read")
        if self.store.get_result(f"smoke/{self.session_id}") != "ok":
            raise DataPlaneError(500, "state_roundtrip_failed", "smoke state roundtrip failed")
        self._dependency("gateway.tool")
        return f"MODEL_OK STATE_OK TOOL_OK version={self.version}"

    def _model(self, text: str) -> str:
        deployment = self.env.get("AGENT_MODEL_DEPLOYMENT")
        if not deployment:
            raise DataPlaneError(
                500,
                "configuration_error",
                "AGENT_MODEL_DEPLOYMENT is not set for this agent version; configuration is per "
                "version, so create a new version with the setting",
            )
        self._dependency("models.chat")
        return f"answer[{hashlib.sha256(text.encode()).hexdigest()[:8]}] via {deployment}"

    def _dependency(self, op_name: str) -> None:
        op = OPERATIONS[op_name]
        if not self.network.can_reach(op.destination):
            raise DataPlaneError(
                502,
                "egress_blocked",
                f"{op_name}: egress from the agent sandbox to '{op.destination}' is not allowed",
            )
        token = self.tokens.get_token(op.audience) if op.audience else None
        try:
            check_data_plane(op_name, self.principal, token, self.network, self.rbac, self.clock)
        except DataPlaneError as exc:
            raise DataPlaneError(
                500,
                "dependency_authorization_failed",
                f"agent runtime identity '{self.principal}' was refused by {op.destination}: "
                f"{exc.status} {exc.code}: {exc.message}",
            ) from exc
        self.dependency_calls.append(op_name)

    def _emit_span(
        self,
        kind: str,
        headers: dict,
        conversation_key: str,
        replayed: bool,
        first_turn: bool,
        error: str | None = None,
    ) -> None:
        self._span_seq += 1
        parsed = parse_traceparent(headers.get("traceparent", ""))
        if parsed:
            trace_id, parent = parsed
        else:
            trace_id, parent = _hex(f"local-{self.session_id}-{self._span_seq}", 32), None
        attributes: dict[str, Any] = {
            "gen_ai.operation.name": "invoke_agent",
            "gen_ai.agent.name": AGENT_NAME,
            "agent.version": self.version,
            "agent.session_id": self.session_id,
            "client.correlation_id": headers.get("x-client-correlation-id", ""),
            "idempotency.replayed": replayed,
            "session.first_turn": first_turn,
            "conversation.key_hash": (
                hashlib.sha256(conversation_key.encode()).hexdigest()[:12] if conversation_key else ""
            ),
            "platform.call_id": headers.get("x-agent-foundry-call-id", ""),
        }
        if error:
            attributes["error.type"] = error
        self.exporter.record(
            {
                "name": f"agent.{kind}",
                "trace_id": trace_id,
                "parent_span_id": parent,
                "span_id": _hex(f"{self.session_id}-span-{self._span_seq}", 16),
                "tick": self.clock.now(),
                "status": "error" if error else "ok",
                "attributes": attributes,
            }
        )


# ---------------------------------------------------------------------------
# Fake Foundry Agent Service (hosted agents). Implements AgentRuntime.
# ---------------------------------------------------------------------------


DRAIN_REASONS = frozenset({"drain", "rollback_drain"})


class FakeFoundryRuntime:
    """In-memory model of versions, sessions and invocation for one agent.

    Session state is evaluated lazily against the logical clock, so reading
    a session after a gap gives the state it would have reached.
    """

    def __init__(
        self,
        clock: Clock,
        rbac: RoleAssignments,
        registry: PrivateRegistry,
        issuer: FakeEntraIssuer,
        store: SessionStore,
        ledger: ToolLedger,
        sink: TelemetrySink,
        sandbox_network: Placement = SANDBOX,
        runtime_principal: str = RUNTIME,
        pull_principal: str = PROJECT_IDENTITY,
    ) -> None:
        self.clock = clock
        self.rbac = rbac
        self.registry = registry
        self.issuer = issuer
        self.store = store
        self.ledger = ledger
        self.sink = sink
        self.sandbox_network = sandbox_network
        self.runtime_principal = runtime_principal
        self.pull_principal = pull_principal
        self.versions: dict[str, dict] = {}
        self._active: str | None = None
        self.sessions: dict[str, dict] = {}
        self.apps: dict[str, HostedAgentApp] = {}
        self.exporters: list[BatchingExporter] = []
        self.activity: list[tuple[int, str, str]] = []
        self.faults: list[tuple[str, int]] = []
        self.last_forwarded: dict[str, str] = {}
        self._version_seq = 0
        self._session_seq = 0
        self._call_seq = 0

    # -- versions ----------------------------------------------------------

    def _authorize(self, op_name: str, ctx: CallContext) -> None:
        check_data_plane(op_name, ctx.principal, ctx.token(), ctx.network, self.rbac, self.clock)

    def create_version(self, ctx: CallContext, image_digest: str, env: dict) -> str:
        self._authorize("agents.create_version", ctx)
        if not image_digest.startswith("sha256:"):
            raise DataPlaneError(
                400, "invalid_image_reference", "reference the image by digest (sha256:...), not by a mutable tag"
            )
        self._version_seq += 1
        version = str(self._version_seq)
        self.versions[version] = {"version": version, "image": image_digest, "env": dict(env)}
        return version

    def set_active_version(self, ctx: CallContext, version: str) -> None:
        self._authorize("agents.set_active_version", ctx)
        if version not in self.versions:
            raise DataPlaneError(404, "version_not_found", f"agent version '{version}' does not exist")
        self._active = version

    def get_active_version(self, ctx: CallContext) -> str | None:
        self._authorize("agents.read", ctx)
        return self._active

    # -- sessions ----------------------------------------------------------

    def create_session(
        self, ctx: CallContext, version: str | None = None, session_id: str | None = None
    ) -> dict:
        self._authorize("sessions.create", ctx)
        if session_id is not None and session_id in self.sessions:
            existing = self._refresh(session_id)
            if existing["state"] in LIVE_STATES:
                return self._view(existing)
            raise DataPlaneError(
                409, "session_exists", f"session '{session_id}' is {existing['state']}; use a new session id"
            )
        return self._view(self._new_session(version, session_id))

    def get_session(self, ctx: CallContext, session_id: str) -> dict:
        self._authorize("sessions.get", ctx)
        return self._view(self._require(session_id))

    def list_sessions(self, ctx: CallContext) -> list[dict]:
        self._authorize("sessions.list", ctx)
        return [self._view(self._refresh(sid)) for sid in sorted(self.sessions)]

    def end_session(self, ctx: CallContext, session_id: str, reason: str = "client_end") -> dict:
        self._authorize("sessions.end", ctx)
        session = self._require(session_id)
        if session["state"] in LIVE_STATES or session["state"] == FAILED:
            self._set_state(session, CLOSED)
            session["end_reason"] = reason
            self._shutdown_app(session_id)
        return self._view(session)

    def heartbeat(self, ctx: CallContext, session_id: str) -> dict:
        self._authorize("sessions.heartbeat", ctx)
        session = self._require(session_id)
        if session["state"] in TERMINAL_STATES:
            raise DataPlaneError(
                404, "session_not_found", f"session '{session_id}' is {session['state']}; heartbeat rejected"
            )
        self._touch(session)
        self.activity.append((self.clock.now(), session_id, "heartbeat"))
        return self._view(session)

    def invoke(self, ctx: CallContext, session_id: str | None, request: dict, headers: dict) -> dict:
        self._authorize("responses.invoke", ctx)
        if session_id is None:
            leaked = self._new_session(None, None)
            raise DataPlaneError(
                424,
                "dependency_not_ready",
                f"no session id supplied; the platform started unpinned session '{leaked['id']}', "
                "which is not ready yet",
            )
        if session_id not in self.sessions:
            raise DataPlaneError(404, "session_not_found", f"session '{session_id}' does not exist")
        session = self._refresh(session_id)
        state = session["state"]
        if state in (EXPIRED, CLOSED):
            target = (
                "on the active version"
                if session["end_reason"] in DRAIN_REASONS
                else f"pinned to version {session['agent_version']}"
            )
            raise DataPlaneError(
                404,
                "session_not_found",
                f"session '{session_id}' is {state} ({session['end_reason']}); open a new session {target} "
                "and resend with the same idempotency key",
            )
        if state == FAILED:
            raise DataPlaneError(409, "session_failed", f"session '{session_id}' failed: {session['failure']}")
        now = self.clock.now()
        if state == CREATED or now < session["ready_at"]:
            raise DataPlaneError(
                424,
                "dependency_not_ready",
                f"session '{session_id}' container is starting but the agent server is not ready; "
                "retry with backoff",
            )
        fault = self.faults.pop(0) if self.faults else None
        if fault is not None and fault[0] == "status":
            raise DataPlaneError(fault[1], "injected_fault", f"transient platform error {fault[1]}")
        self._touch(session)
        kind = request.get("kind", "turn")
        self.activity.append((now, session_id, "user_turn" if kind == "turn" else "probe"))
        forwarded = self._ingress(ctx, headers)
        result = self.apps[session_id].handle(request, forwarded)
        session["turns_served"] += 1
        if fault is not None and fault[0] == "lose_response":
            raise DataPlaneError(0, "connection_reset", "connection reset after the turn was processed")
        return {**result, "session_id": session_id, "status": 200}

    # -- internals ---------------------------------------------------------

    def _require(self, session_id: str) -> dict:
        if session_id not in self.sessions:
            raise DataPlaneError(404, "session_not_found", f"session '{session_id}' does not exist")
        return self._refresh(session_id)

    def _new_session(self, version: str | None, session_id: str | None) -> dict:
        pinned = version is not None
        resolved = version if pinned else self._active
        if resolved is None or resolved not in self.versions:
            raise DataPlaneError(404, "version_not_found", f"agent version '{resolved}' does not exist")
        self._session_seq += 1
        sid = session_id or f"sess-{self._session_seq:04d}"
        now = self.clock.now()
        session = {
            "id": sid,
            "agent_version": resolved,
            "pinned": pinned,
            "state": CREATED,
            "history": [CREATED],
            "created_at": now,
            "active_at": None,
            "ready_at": None,
            "last_activity": now,
            "turns_served": 0,
            "failure": None,
            "end_reason": None,
        }
        self.sessions[sid] = session
        return session

    def _set_state(self, session: dict, state: str) -> None:
        if session["state"] != state:
            session["state"] = state
            session["history"].append(state)

    def _touch(self, session: dict) -> None:
        if session["state"] in (ACTIVE, IDLE):
            session["last_activity"] = self.clock.now()
            self._set_state(session, ACTIVE)

    def _refresh(self, session_id: str) -> dict:
        session = self.sessions[session_id]
        now = self.clock.now()
        if session["state"] == CREATED and now >= session["created_at"] + PROVISION_TICKS:
            image = self.versions[session["agent_version"]]["image"]
            try:
                self.registry.pull(self.pull_principal, PLATFORM_PULL, image)
            except DataPlaneError as exc:
                session["failure"] = f"ImagePullFailed: {exc.status} {exc.code}: {exc.message}"
                self._set_state(session, FAILED)
                return session
            active_at = session["created_at"] + PROVISION_TICKS
            session["active_at"] = active_at
            session["ready_at"] = active_at + SERVER_WARMUP_TICKS
            session["last_activity"] = active_at
            self._set_state(session, ACTIVE)
            exporter = BatchingExporter(self.sink, self.sandbox_network)
            self.exporters.append(exporter)
            self.apps[session_id] = HostedAgentApp(
                version=session["agent_version"],
                principal=self.runtime_principal,
                issuer=self.issuer,
                clock=self.clock,
                store=self.store,
                ledger=self.ledger,
                exporter=exporter,
                rbac=self.rbac,
                network=self.sandbox_network,
                env=self.versions[session["agent_version"]]["env"],
                session_id=session_id,
            )
        if session["state"] in (ACTIVE, IDLE):
            # A session that is still warming up is not idle yet.
            quiet = now - max(session["last_activity"], session["ready_at"])
            if quiet >= IDLE_TIMEOUT_TICKS:
                self._set_state(session, EXPIRED)
                session["end_reason"] = "idle_timeout"
                self._shutdown_app(session_id)
            elif quiet >= IDLE_AFTER_TICKS:
                self._set_state(session, IDLE)
        return session

    def _shutdown_app(self, session_id: str) -> None:
        app = self.apps.pop(session_id, None)
        if app is not None:
            app.shutdown()

    def _ingress(self, ctx: CallContext, headers: dict) -> dict:
        """Forward only trace context and x-client-* headers; add platform headers."""
        self._call_seq += 1
        forwarded: dict[str, str] = {}
        for name, value in headers.items():
            lowered = name.lower()
            if lowered in ("traceparent", "tracestate") or lowered.startswith("x-client-"):
                forwarded[lowered] = value
        parsed = parse_traceparent(forwarded.get("traceparent", ""))
        if parsed:
            forwarded["traceparent"] = make_traceparent(parsed[0], _hex(f"platform-hop-{self._call_seq}", 16))
        forwarded["x-agent-user-id"] = ctx.principal
        forwarded["x-agent-foundry-call-id"] = f"call-{self._call_seq:06d}"
        self.last_forwarded = dict(forwarded)
        return forwarded

    def _view(self, session: dict) -> dict:
        keys = ("id", "state", "agent_version", "pinned", "last_activity", "failure", "end_reason", "turns_served")
        view = {k: session[k] for k in keys}
        view["history"] = list(session["history"])
        return view


# ---------------------------------------------------------------------------
# Callers
# ---------------------------------------------------------------------------


@dataclass
class TurnResult:
    status: int
    output: str = ""
    agent_version: str | None = None
    replayed: bool = False
    attempts: int = 0
    session_id: str | None = None
    recovered_sessions: list[str] = field(default_factory=list)
    error: str | None = None
    trace_id: str = ""


class AgentClient:
    """A well-behaved caller: pinned sessions, bounded retries, one key per turn."""

    def __init__(
        self,
        runtime: AgentRuntime,
        ctx: CallContext,
        clock: Clock,
        max_attempts: int = 4,
        poll_budget: int = 6,
        readiness_budget: int = 5,
    ) -> None:
        self.runtime = runtime
        self.ctx = ctx
        self.clock = clock
        self.max_attempts = max_attempts
        self.poll_budget = poll_budget
        self.readiness_budget = readiness_budget
        self.pins: dict[str, str] = {}
        self.attempt_log: list[tuple[int, str | None, str]] = []
        self.readiness_attempts = 0
        self._recoveries = 0

    def open_session(
        self, version: str | None = None, session_id: str | None = None, wait_ready: bool = True
    ) -> dict:
        """Create (or re-open) a session, poll until it leaves CREATED, then wait for readiness."""
        view = self.runtime.create_session(self.ctx, version=version, session_id=session_id)
        polls = 0
        while view["state"] == CREATED:
            if polls >= self.poll_budget:
                raise DataPlaneError(504, "session_start_timeout", f"session '{view['id']}' did not start")
            self.clock.advance(1)
            polls += 1
            view = self.runtime.get_session(self.ctx, view["id"])
        if view["state"] == FAILED:
            raise DataPlaneError(409, "session_failed", f"session '{view['id']}' failed: {view['failure']}")
        self.pins[view["id"]] = view["agent_version"]
        if wait_ready:
            self.wait_ready(view["id"])
        return self.runtime.get_session(self.ctx, view["id"])

    def wait_ready(self, session_id: str) -> int:
        """ACTIVE is not the same as ready: retry a cheap probe on 424 only, with backoff."""
        attempt = 0
        while True:
            attempt += 1
            self.readiness_attempts = attempt
            try:
                self.runtime.invoke(self.ctx, session_id, {"kind": "readiness"}, {})
                return attempt
            except DataPlaneError as exc:
                if exc.status != 424 or attempt >= self.readiness_budget:
                    raise
                self.clock.advance(backoff(attempt))

    def heartbeat(self, session_id: str) -> dict:
        return self.runtime.heartbeat(self.ctx, session_id)

    def end(self, session_id: str, reason: str = "client_end") -> dict:
        return self.runtime.end_session(self.ctx, session_id, reason)

    def send_turn(
        self,
        session_id: str,
        conversation_key: str,
        text: str,
        idempotency_key: str,
        correlation_id: str,
        tool: str | None = None,
        kind: str = "turn",
        key_in_header: bool = True,
    ) -> TurnResult:
        """Send one logical turn. Every attempt reuses the same idempotency key."""
        trace_id = _hex("trace-" + correlation_id, 32)
        result = TurnResult(status=0, session_id=session_id, trace_id=trace_id)
        metadata: dict[str, str] = {"conversation_key": conversation_key}
        if not key_in_header:
            metadata["idempotency_key"] = idempotency_key
        request: dict[str, Any] = {"kind": kind, "input": text, "metadata": metadata}
        if tool:
            request["tool"] = tool
        sid = session_id
        attempt = 0
        while attempt < self.max_attempts:
            attempt += 1
            headers = {
                "traceparent": make_traceparent(trace_id, _hex(f"{correlation_id}-attempt-{attempt}", 16)),
                "x-client-correlation-id": correlation_id,
                "Authorization": "<caller access token placeholder>",
                "x-tenant-hint": "fabrikam",
            }
            if key_in_header:
                headers["x-client-idempotency-key"] = idempotency_key
            try:
                response = self.runtime.invoke(self.ctx, sid, request, headers)
            except DataPlaneError as exc:
                action = classify(exc)
                self.attempt_log.append((self.clock.now(), sid, f"{exc.status} {exc.code} -> {action}"))
                result.status = exc.status
                result.error = f"{exc.status} {exc.code}: {exc.message}"
                if action == "fail" or attempt >= self.max_attempts:
                    break
                if action == "recover":
                    pinned = self.pins.get(sid)
                    if pinned is None:
                        break
                    try:
                        drained = self.runtime.get_session(self.ctx, sid)["end_reason"] in DRAIN_REASONS
                    except DataPlaneError:
                        drained = False
                    # Expired: keep the conversation on its version. Drained: move to the active one.
                    target = None if drained else pinned
                    self._recoveries += 1
                    try:
                        view = self.open_session(version=target, session_id=f"{conversation_key}-r{self._recoveries}")
                    except DataPlaneError as reopen:
                        result.status = reopen.status
                        result.error = f"recovery failed: {reopen}"
                        break
                    sid = view["id"]
                    result.recovered_sessions.append(sid)
                    continue
                self.clock.advance(backoff(attempt))
                continue
            self.attempt_log.append((self.clock.now(), sid, "200 ok"))
            result.status = 200
            result.output = response["output"]
            result.agent_version = response["agent_version"]
            result.replayed = response["replayed"]
            result.error = None
            break
        result.attempts = attempt
        result.session_id = sid
        return result


class UnpinnedRetryClient:
    """Anti-pattern: no session id and blind retries. Each attempt can start a session."""

    def __init__(self, runtime: AgentRuntime, ctx: CallContext, clock: Clock, max_attempts: int = 4) -> None:
        self.runtime = runtime
        self.ctx = ctx
        self.clock = clock
        self.max_attempts = max_attempts

    def send(self, text: str, correlation_id: str) -> TurnResult:
        result = TurnResult(status=0)
        for attempt in range(1, self.max_attempts + 1):
            result.attempts = attempt
            try:
                response = self.runtime.invoke(
                    self.ctx, None, {"kind": "turn", "input": text}, {"x-client-correlation-id": correlation_id}
                )
            except DataPlaneError as exc:
                result.status = exc.status
                result.error = f"{exc.status} {exc.code}: {exc.message}"
                if classify(exc) != "retry":
                    break
                self.clock.advance(backoff(attempt))
                continue
            result.status = 200
            result.output = response["output"]
            break
        return result


# ---------------------------------------------------------------------------
# Release pipeline (runs on a runner inside the VNet)
# ---------------------------------------------------------------------------


@dataclass
class ReleaseRecord:
    build_id: str
    stages: list[tuple[str, str, str]] = field(default_factory=list)
    digest: str | None = None
    version: str | None = None
    rollback_target: str | None = None
    drain_pending: list[str] = field(default_factory=list)
    outcome: str = "pending"

    def stage_status(self) -> dict[str, str]:
        return {name: status for name, status, _ in self.stages}

    def stage_names(self) -> list[str]:
        return [name for name, _, _ in self.stages]

    def detail(self, stage: str) -> str:
        for name, _, text in self.stages:
            if name == stage:
                return text
        return ""


class ReleasePipeline:
    """build -> scan -> sign -> push -> verify roles -> create version -> smoke -> eval -> promote.

    Failures before the smoke test fail fast with no retry: a 403 from a
    runner outside the network will not fix itself. A smoke or evaluation
    failure leaves (or puts) the recorded version active and drains the bad one.
    """

    def __init__(
        self,
        runtime: FakeFoundryRuntime,
        registry: PrivateRegistry,
        rbac: RoleAssignments,
        ctx: CallContext,
        clock: Clock,
        sign: bool = True,
        eval_gate: Callable[[ReleaseRecord], tuple[bool, str]] | None = None,
    ) -> None:
        self.runtime = runtime
        self.registry = registry
        self.rbac = rbac
        self.ctx = ctx
        self.clock = clock
        self.sign = sign
        self.eval_gate = eval_gate

    def release(self, build_id: str, source: dict, env: dict | None = None) -> ReleaseRecord:
        rec = ReleaseRecord(build_id)
        env = dict(DEFAULT_ENV) if env is None else dict(env)
        stage = "build"
        try:
            image = self._build(source)
            self._ok(rec, "build", f"linux/amd64 image from {image['base'].split('@')[0]}, non-root")
            stage = "scan"
            self._scan(source)
            self._ok(rec, "scan", "no secret or critical findings")
            if self.sign:
                stage = "sign"
                self._ok(rec, "sign", "signature attached to the digest (optional; tooling varies)")
            stage = "push"
            rec.digest = self.registry.push(self.ctx.principal, self.ctx.network, image)
            self._ok(rec, "push", f"pushed by digest {rec.digest[:19]}...")
            stage = "verify_roles"
            self._verify_roles()
            self._ok(rec, "verify_roles", "runtime and pull identities hold their data-plane roles")
            stage = "create_version"
            rec.rollback_target = self.runtime.get_active_version(self.ctx)
            rec.version = self.runtime.create_version(self.ctx, rec.digest, env)
            self._ok(rec, "create_version", f"version {rec.version} (rollback target {rec.rollback_target})")
        except (DataPlaneError, PipelineFailure) as exc:
            rec.stages.append((stage, "failed", str(exc)))
            rec.outcome = "failed"
            return rec

        passed, why = self._smoke(rec)
        rec.stages.append(("smoke", "passed" if passed else "failed", why))
        if passed and self.eval_gate is not None:
            passed, why = self.eval_gate(rec)
            rec.stages.append(("eval_gate", "passed" if passed else "failed", why))
        if not passed:
            drained = self._restore_and_drain(rec.rollback_target, rec.version)
            rec.stages.append(
                ("rollback", "ok", f"active version {rec.rollback_target}; drained {len(drained)} session(s)")
            )
            rec.outcome = "rolled_back"
            return rec
        self.runtime.set_active_version(self.ctx, rec.version)
        rec.drain_pending = [
            s["id"]
            for s in self.runtime.list_sessions(self.ctx)
            if s["state"] in LIVE_STATES and s["agent_version"] != rec.version
        ]
        self._ok(
            rec,
            "promote",
            f"active version {rec.version}; {len(rec.drain_pending)} session(s) on older versions pending drain",
        )
        rec.outcome = "promoted"
        return rec

    def rollback(self, rec: ReleaseRecord) -> list[str]:
        """Post-promote rollback: re-activate the recorded version and drain the bad one now."""
        if rec.rollback_target is None:
            raise PipelineFailure("no rollback target recorded; hotfix forward with a new version")
        drained = self._restore_and_drain(rec.rollback_target, rec.version)
        rec.stages.append(("rollback", "ok", f"active version {rec.rollback_target}; drained {len(drained)}"))
        rec.outcome = "rolled_back"
        return drained

    def drain(self, version: str, reason: str = "drain") -> list[str]:
        drained = []
        for view in self.runtime.list_sessions(self.ctx):
            if view["agent_version"] == version and view["state"] in LIVE_STATES:
                self.runtime.end_session(self.ctx, view["id"], reason)
                drained.append(view["id"])
        return drained

    def _ok(self, rec: ReleaseRecord, stage: str, detail: str) -> None:
        rec.stages.append((stage, "ok", detail))

    def _build(self, source: dict) -> dict:
        base = source.get("base", APPROVED_BASE)
        if base != APPROVED_BASE:
            raise PipelineFailure(f"base image '{base}' is not on the approved list")
        return {
            "base": base,
            "source": source.get("name", AGENT_NAME),
            "revision": source.get("revision", "1"),
            "platform": "linux/amd64",
            "user": "non-root",
        }

    def _scan(self, source: dict) -> None:
        blocking = [f for f in source.get("findings", []) if f in ("secret", "critical")]
        if blocking:
            raise PipelineFailure(f"image scan blocked the release: {', '.join(blocking)} finding(s)")

    def _verify_roles(self) -> None:
        required = [
            (self.runtime.runtime_principal, ROLE_OPENAI_USER, SCOPE_ACCOUNT),
            (self.runtime.runtime_principal, ROLE_COSMOS_DATA, SCOPE_COSMOS),
            (self.runtime.pull_principal, ROLE_ACR_PULL, SCOPE_REGISTRY),
        ]
        missing = [f"{p} needs '{r}' on {s}" for p, r, s in required if not self.rbac.has(p, r, s)]
        if missing:
            raise PipelineFailure("missing role assignments: " + "; ".join(missing))

    def _smoke(self, rec: ReleaseRecord) -> tuple[bool, str]:
        client = AgentClient(self.runtime, self.ctx, self.clock)
        sid = f"smoke-{rec.build_id}"
        try:
            client.open_session(version=rec.version, session_id=sid)
            res = client.send_turn(
                sid, sid, "smoke", f"{sid}-1", f"smoke-{rec.build_id}", kind="smoke"
            )
        except DataPlaneError as exc:
            return False, f"session could not start: {exc}"
        finally:
            try:
                self.runtime.end_session(self.ctx, sid, "smoke_complete")
            except DataPlaneError:
                pass
        if res.status != 200:
            return False, res.error or f"status {res.status}"
        expected = ["MODEL_OK", "STATE_OK", "TOOL_OK", f"version={rec.version}"]
        missing = [m for m in expected if m not in res.output]
        if missing:
            return False, f"smoke output missing {', '.join(missing)}"
        return True, res.output

    def _restore_and_drain(self, target: str | None, bad_version: str | None) -> list[str]:
        if target is not None:
            self.runtime.set_active_version(self.ctx, target)
        return self.drain(bad_version, "rollback_drain") if bad_version else []


# ---------------------------------------------------------------------------
# Session hygiene: orphan sweeper and keep-warm detector
# ---------------------------------------------------------------------------


class KeepWarmDetector:
    """Flags sessions held open by heartbeats with no user turns behind them."""

    def __init__(self, max_heartbeats_without_user: int = 3, user_quiet_window: int = 6) -> None:
        self.max_heartbeats = max_heartbeats_without_user
        self.quiet_window = user_quiet_window

    def scan(
        self, activity: list[tuple[int, str, str]], now: int, live: set[str] | None = None
    ) -> list[dict]:
        per_session: dict[str, dict] = {}
        for tick, sid, kind in activity:
            entry = per_session.setdefault(sid, {"last_user": None, "heartbeats": 0})
            if kind == "user_turn":
                entry["last_user"] = tick
                entry["heartbeats"] = 0
            elif kind == "heartbeat":
                entry["heartbeats"] += 1
        suspects = []
        for sid, entry in sorted(per_session.items()):
            if live is not None and sid not in live:
                continue
            last_user = entry["last_user"]
            quiet = now - last_user if last_user is not None else None
            if entry["heartbeats"] > self.max_heartbeats and (quiet is None or quiet >= self.quiet_window):
                suspects.append(
                    {
                        "session_id": sid,
                        "heartbeats_since_user_turn": entry["heartbeats"],
                        "ticks_since_user_turn": quiet,
                        "finding": "keep-warm: heartbeats without user activity hide cold start and cost",
                    }
                )
        return suspects


class OrphanSweeper:
    """Runs inside the network on a schedule. Dry run by default; honours a keep list."""

    def __init__(
        self,
        runtime: AgentRuntime,
        ctx: CallContext,
        clock: Clock,
        idle_window: int = SWEEP_IDLE_WINDOW_TICKS,
        keep: set[str] | None = None,
        detector: KeepWarmDetector | None = None,
    ) -> None:
        self.runtime = runtime
        self.ctx = ctx
        self.clock = clock
        self.idle_window = idle_window
        self.keep = set(keep or ())
        self.detector = detector

    def run(self, apply: bool = False, activity: list[tuple[int, str, str]] | None = None) -> dict:
        now = self.clock.now()
        views = self.runtime.list_sessions(self.ctx)
        stale = [
            v["id"]
            for v in views
            if (v["state"] in LIVE_STATES or v["state"] == FAILED) and now - v["last_activity"] >= self.idle_window
        ]
        kept = [sid for sid in stale if sid in self.keep]
        candidates = [sid for sid in stale if sid not in self.keep]
        ended = []
        if apply:
            for sid in candidates:
                self.runtime.end_session(self.ctx, sid, "orphan_sweep")
                ended.append(sid)
        suspects: list[dict] = []
        if self.detector is not None and activity is not None:
            live = {v["id"] for v in views if v["state"] in LIVE_STATES}
            suspects = self.detector.scan(activity, now, live)
        return {
            "dry_run": not apply,
            "candidates": candidates,
            "kept": kept,
            "ended": ended,
            "keep_warm_suspects": suspects,
        }


# ---------------------------------------------------------------------------
# Gated production stub
# ---------------------------------------------------------------------------


LIVE_OPT_IN_ENV = "HOSTED_AGENT_SIM_ENABLE_LIVE"


class FoundryHostedAgentAdapter:
    """Where a real AgentRuntime adapter would go. Deliberately not implemented.

    A real adapter would call the Foundry Agent Service data plane with a
    managed identity token for the Foundry audience, from inside the network.
    Request shapes are preview; build it against current Microsoft Learn docs
    and the SDK version you pin. This stub never makes a network call.
    """

    def __init__(self, project_endpoint: str = PROJECT_ENDPOINT) -> None:
        if os.environ.get(LIVE_OPT_IN_ENV) != "1":
            raise NotImplementedError(
                f"FoundryHostedAgentAdapter is a stub. Set {LIVE_OPT_IN_ENV}=1 only after you implement "
                "it against the current preview API; the simulator never calls Azure"
            )
        self.project_endpoint = project_endpoint

    def _not_implemented(self, *_: Any, **__: Any) -> Any:
        raise NotImplementedError(
            "implement against the Foundry Agent Service data plane (preview); verify shapes on Microsoft Learn"
        )

    create_version = _not_implemented
    set_active_version = _not_implemented
    get_active_version = _not_implemented
    create_session = _not_implemented
    get_session = _not_implemented
    list_sessions = _not_implemented
    end_session = _not_implemented
    heartbeat = _not_implemented
    invoke = _not_implemented


# ---------------------------------------------------------------------------
# Test world
# ---------------------------------------------------------------------------


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def expect_error(fn: Callable[[], Any], status: int, code: str | None = None) -> DataPlaneError:
    try:
        fn()
    except DataPlaneError as exc:
        check(exc.status == status, f"expected status {status}, got {exc}")
        if code is not None:
            check(exc.code == code, f"expected code {code}, got {exc}")
        return exc
    raise AssertionError(f"expected DataPlaneError {status} {code or ''}, but the call succeeded")


DEFAULT_GRANTS = (
    (PIPELINE, ROLE_ACR_PUSH, SCOPE_REGISTRY),
    (PIPELINE, ROLE_AI_USER, SCOPE_PROJECT),
    (RUNTIME, ROLE_OPENAI_USER, SCOPE_ACCOUNT),
    (RUNTIME, ROLE_COSMOS_DATA, SCOPE_COSMOS),
    (PROJECT_IDENTITY, ROLE_ACR_PULL, SCOPE_REGISTRY),
    (CALLER, ROLE_AI_USER, SCOPE_PROJECT),
    (OPERATOR, ROLE_AI_USER, SCOPE_PROJECT),
    # Control plane only: can manage the resource, cannot call its data plane.
    (DEVELOPER, ROLE_CONTRIBUTOR, SCOPE_PROJECT),
)


class World:
    """One fictional Contoso environment with least-privilege grants in place."""

    def __init__(self, sandbox: Placement = SANDBOX) -> None:
        self.clock = LogicalClock()
        self.rbac = RoleAssignments()
        for grant in DEFAULT_GRANTS:
            self.rbac.grant(*grant)
        self.issuer = FakeEntraIssuer(self.clock)
        self.registry = PrivateRegistry(self.rbac, self.clock)
        self.store = InMemoryStateStore()
        self.ledger = ToolLedger()
        self.sink = InMemoryTelemetrySink()
        self.runtime = FakeFoundryRuntime(
            self.clock,
            self.rbac,
            self.registry,
            self.issuer,
            self.store,
            self.ledger,
            self.sink,
            sandbox_network=sandbox,
        )

    def ctx(
        self,
        principal: str = CALLER,
        placement: Placement = INSIDE,
        audience: str = AUD_FOUNDRY,
        skew: int = REFRESH_SKEW_TICKS,
    ) -> CallContext:
        provider = CachedTokenProvider(principal, self.issuer, self.clock, skew)
        return CallContext(principal, placement, provider, audience)

    def client(self, principal: str = CALLER, placement: Placement = INSIDE, **kwargs: Any) -> AgentClient:
        return AgentClient(self.runtime, self.ctx(principal, placement), self.clock, **kwargs)

    def pipeline(self, placement: Placement = INSIDE, **kwargs: Any) -> ReleasePipeline:
        return ReleasePipeline(
            self.runtime, self.registry, self.rbac, self.ctx(PIPELINE, placement), self.clock, **kwargs
        )

    @staticmethod
    def source(revision: str = "1", **overrides: Any) -> dict:
        src: dict[str, Any] = {"name": AGENT_NAME, "revision": revision, "base": APPROVED_BASE, "findings": []}
        src.update(overrides)
        return src

    def deploy(self, revision: str = "1") -> str:
        rec = self.pipeline().release(f"b{revision}", self.source(revision))
        check(rec.outcome == "promoted", f"baseline release failed: {rec.stages}")
        return rec.version or ""

    def session(self, session_id: str) -> dict:
        return self.runtime.get_session(self.ctx(OPERATOR), session_id)

    def active_version(self) -> str | None:
        return self.runtime.get_active_version(self.ctx(OPERATOR))

    def flush_telemetry(self) -> None:
        for exporter in self.runtime.exporters:
            exporter.flush("test")


# ---------------------------------------------------------------------------
# Scenarios 1-12: network, identity, tokens, sessions, invoke
# ---------------------------------------------------------------------------


def scenario_outside_vnet_403() -> None:
    w = World()
    rec = w.pipeline(placement=INTERNET).release("b-public", w.source())
    check(rec.outcome == "failed", f"a release from a public runner must fail, got {rec.outcome}")
    check(rec.stage_status().get("push") == "failed", f"push should be the failing stage: {rec.stages}")
    check("public_access_disabled" in rec.detail("push"), rec.detail("push"))
    check(not w.registry.images and not w.runtime.versions, "nothing may be pushed or created")
    w.deploy()
    outside = w.client(placement=INTERNET)
    exc = expect_error(lambda: outside.open_session(version="1"), 403, "public_access_disabled")
    check(classify(exc) == "fail", "a network 403 will not fix itself; do not retry it")
    view = w.client().open_session(version="1")
    check(view["state"] == ACTIVE, f"the same call from inside the VNet works: {view}")


def scenario_missing_role_403_clear_message() -> None:
    w = World()
    w.deploy()
    developer = w.client(principal=DEVELOPER)
    exc = expect_error(lambda: developer.open_session(version="1"), 403, "authorization_failed")
    check(ROLE_AI_USER in exc.message and ROLE_CONTRIBUTOR in exc.message, exc.message)
    check("grant no data-plane actions" in exc.message, exc.message)
    w.rbac.revoke(RUNTIME, ROLE_OPENAI_USER, SCOPE_ACCOUNT)
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-roles")["id"]
    res = client.send_turn(sid, sid, "hello", "k-roles-1", "corr-roles")
    check(res.status == 500 and "dependency_authorization_failed" in (res.error or ""), str(res))
    check(ROLE_OPENAI_USER in (res.error or "") and RUNTIME in (res.error or ""), res.error or "")
    check(res.attempts == 1, f"a missing role is not transient; attempts={res.attempts}")


def scenario_token_cache_per_audience() -> None:
    w = World()
    provider = CachedTokenProvider(RUNTIME, w.issuer, w.clock)
    first = provider.get_token(AUD_MODELS)
    second = provider.get_token(AUD_MODELS)
    provider.get_token(AUD_COSMOS)
    provider.get_token(AUD_GATEWAY)
    check(first is second, "the same audience within its lifetime must hit the cache")
    check(provider.fetches == 3 and provider.hits == 1, f"fetches={provider.fetches} hits={provider.hits}")
    check(
        provider.cached_audiences() == sorted([AUD_MODELS, AUD_COSMOS, AUD_GATEWAY]),
        str(provider.cached_audiences()),
    )
    expect_error(
        lambda: check_data_plane("cosmos.read", RUNTIME, first, SANDBOX, w.rbac, w.clock), 401, "invalid_audience"
    )
    check("<redacted>" in repr(first), "a token repr must never show a value")
    w.deploy()
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-cache")["id"]
    for n in range(1, 4):
        res = client.send_turn(sid, sid, f"question {n}", f"k-cache-{n}", "corr-cache")
        check(res.status == 200, str(res))
    app = w.runtime.apps[sid]
    check(app.tokens.fetches == 2, f"one fetch per audience (models, cosmos), got {app.tokens.fetches}")
    check(app.tokens.hits >= 6, f"later dependency calls should hit the cache, hits={app.tokens.hits}")


def scenario_token_refresh_skew() -> None:
    w = World()
    no_skew = CachedTokenProvider(RUNTIME, w.issuer, w.clock, skew=0)
    with_skew = CachedTokenProvider(RUNTIME, w.issuer, w.clock, skew=REFRESH_SKEW_TICKS)
    plain = no_skew.get_token(AUD_MODELS)
    skewed = with_skew.get_token(AUD_MODELS)
    w.clock.advance(TOKEN_LIFETIME_TICKS - REFRESH_SKEW_TICKS)
    refreshed = with_skew.get_token(AUD_MODELS)
    check(refreshed is not skewed and with_skew.fetches == 2, "the skewed provider must refresh early")
    w.clock.advance(REFRESH_SKEW_TICKS - 1)
    late = no_skew.get_token(AUD_MODELS)
    check(late is plain, "without skew a nearly expired token is still handed out")
    w.clock.advance(2)  # the request is still in flight when the token expires
    expect_error(
        lambda: check_data_plane("models.chat", RUNTIME, late, SANDBOX, w.rbac, w.clock), 401, "token_expired"
    )
    check_data_plane("models.chat", RUNTIME, refreshed, SANDBOX, w.rbac, w.clock)


def scenario_session_state_machine() -> None:
    w = World()
    w.deploy()
    client = w.client()
    created = w.runtime.create_session(client.ctx, version="1", session_id="conv-sm")
    check(created["state"] == CREATED and created["pinned"], str(created))
    view = client.open_session(version="1", session_id="conv-sm")  # create is idempotent for a live id
    check(view["state"] == ACTIVE and view["agent_version"] == "1", str(view))
    check(client.send_turn("conv-sm", "conv-sm", "hi", "k-sm-1", "corr-sm-1").status == 200, "first turn")
    w.clock.advance(IDLE_AFTER_TICKS)
    check(w.session("conv-sm")["state"] == IDLE, "quiet sessions become IDLE")
    check(client.send_turn("conv-sm", "conv-sm", "again", "k-sm-2", "corr-sm-2").status == 200, "second turn")
    check(w.session("conv-sm")["state"] == ACTIVE, "a turn re-activates an IDLE session")
    client.end("conv-sm")
    client.end("conv-sm")  # ending twice is harmless
    view = w.session("conv-sm")
    check(view["history"] == [CREATED, ACTIVE, IDLE, ACTIVE, CLOSED], str(view["history"]))
    check(view["end_reason"] == "client_end", str(view))
    expect_error(lambda: w.runtime.invoke(client.ctx, "conv-sm", {"kind": "turn"}, {}), 404, "session_not_found")
    expect_error(lambda: client.heartbeat("conv-sm"), 404, "session_not_found")
    expect_error(
        lambda: w.runtime.create_session(client.ctx, version="1", session_id="conv-sm"), 409, "session_exists"
    )


def scenario_heartbeat_extends_idle() -> None:
    w = World()
    w.deploy()
    client = w.client()
    beating = client.open_session(version="1", session_id="conv-beat")["id"]
    quiet = client.open_session(version="1", session_id="conv-quiet")["id"]
    for sid in (beating, quiet):
        client.heartbeat(sid)
    for _ in range(5):
        w.clock.advance(2)
        check(client.heartbeat(beating)["state"] == ACTIVE, "a heartbeat keeps an in-use session active")
    check(w.session(beating)["state"] == ACTIVE, "heartbeats extended the idle timeout")
    expired = w.session(quiet)
    check(expired["state"] == EXPIRED and expired["end_reason"] == "idle_timeout", str(expired))
    w.clock.advance(IDLE_TIMEOUT_TICKS)
    check(w.session(beating)["state"] == EXPIRED, "once heartbeats stop the idle timeout applies again")


def scenario_idle_expiry() -> None:
    w = World()
    w.deploy()
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-exp")["id"]
    check(client.send_turn(sid, sid, "hi", "k-exp-1", "corr-exp").status == 200, "turn")
    check(not w.sink.find(**{"agent.session_id": sid}), "spans are buffered inside the container")
    w.clock.advance(IDLE_AFTER_TICKS)
    check(w.session(sid)["state"] == IDLE, "IDLE first")
    w.clock.advance(IDLE_TIMEOUT_TICKS - IDLE_AFTER_TICKS)
    view = w.session(sid)
    check(view["state"] == EXPIRED and view["end_reason"] == "idle_timeout", str(view))
    check(sid not in w.runtime.apps, "the container stops when the session expires")
    check(len(w.sink.find(**{"agent.session_id": sid})) == 2, "graceful shutdown flushed the buffered spans")
    exc = expect_error(lambda: w.runtime.invoke(client.ctx, sid, {"kind": "turn"}, {}), 404, "session_not_found")
    check("pinned to version 1" in exc.message and "same idempotency key" in exc.message, exc.message)
    check(classify(exc) == "recover", "an expired session is recovered, not retried")


def scenario_invoke_retry_idempotent() -> None:
    w = World()
    w.deploy()
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-idem")["id"]
    w.runtime.faults.append(("lose_response", 0))
    res = client.send_turn(sid, sid, "open a maintenance ticket", "k-idem-1", "corr-idem-1", tool="create_ticket")
    check(res.status == 200 and res.attempts == 2 and res.replayed, str(res))
    check(w.ledger.count(sid) == 1, f"the side effect must happen once, ledger={w.ledger.count(sid)}")
    check("turn=1 notes=1" in res.output, res.output)
    w.runtime.faults.append(("status", 503))
    res2 = client.send_turn(sid, sid, "add a note", "k-idem-2", "corr-idem-2", tool="add_note")
    check(res2.status == 200 and res2.attempts == 2 and not res2.replayed, str(res2))
    check(w.ledger.count(sid) == 2 and "turn=2 notes=2" in res2.output, res2.output)
    res3 = client.send_turn(
        sid, sid, "open a maintenance ticket", "k-idem-1", "corr-idem-3", tool="create_ticket", key_in_header=False
    )
    check(res3.replayed and res3.output == res.output and w.ledger.count(sid) == 2, str(res3))
    expect_error(
        lambda: w.runtime.invoke(
            client.ctx, sid, {"kind": "turn", "input": "x", "metadata": {"conversation_key": sid}}, {}
        ),
        400,
        "bad_request",
    )


def scenario_active_not_ready_424_bounded_retry() -> None:
    w = World()
    w.deploy()
    client = w.client()
    view = client.open_session(version="1", session_id="conv-ready", wait_ready=False)
    check(view["state"] == ACTIVE, f"ACTIVE before the agent server is ready: {view}")
    exc = expect_error(
        lambda: w.runtime.invoke(client.ctx, "conv-ready", {"kind": "readiness"}, {}), 424, "dependency_not_ready"
    )
    check(classify(exc) == "retry", "424 while starting is retryable")
    check(client.wait_ready("conv-ready") == 3, f"readiness attempts={client.readiness_attempts}")
    impatient = w.client(readiness_budget=2)
    expect_error(lambda: impatient.open_session(version="1", session_id="conv-impatient"), 424, "dependency_not_ready")
    check(impatient.readiness_attempts == 2, "the readiness budget bounds the retries")
    check(w.session("conv-impatient")["state"] in LIVE_STATES, "the session keeps starting; only the client gave up")
    direct = client.open_session(version="1", session_id="conv-direct", wait_ready=False)["id"]
    res = client.send_turn(direct, direct, "hi", "k-direct-1", "corr-direct")
    check(res.status == 200 and res.attempts == 3, f"send_turn absorbs 424 with backoff: {res}")


def scenario_retry_classification() -> None:
    table = [
        (403, "public_access_disabled", "fail"),
        (403, "authorization_failed", "fail"),
        (401, "invalid_audience", "fail"),
        (400, "bad_request", "fail"),
        (404, "session_not_found", "recover"),
        (409, "session_failed", "fail"),
        (424, "dependency_not_ready", "retry"),
        (429, "rate_limited", "retry"),
        (503, "injected_fault", "retry"),
        (0, "connection_reset", "retry"),
        (500, "configuration_error", "fail"),
        (500, "dependency_authorization_failed", "fail"),
        (502, "egress_blocked", "fail"),
    ]
    for status, code, expected in table:
        got = classify(DataPlaneError(status, code, ""))
        check(got == expected, f"{status} {code}: expected {expected}, got {got}")
    check([backoff(n) for n in range(1, 6)] == [1, 2, 4, 8, 8], "capped exponential backoff")
    w = World()
    w.deploy()
    client = w.client(max_attempts=3)
    sid = client.open_session(version="1", session_id="conv-budget")["id"]
    w.runtime.faults.extend([("status", 503)] * 5)
    res = client.send_turn(sid, sid, "hi", "k-budget-1", "corr-budget")
    check(res.status == 503 and res.attempts == 3, f"retries stop at the attempt budget: {res}")
    check(w.store.get_record("k-budget-1") is None, "no record exists when no attempt reached the agent")


def scenario_wrong_audience_401() -> None:
    w = World()
    w.deploy()
    wrong = w.ctx(CALLER, INSIDE, audience=AUD_MODELS)
    exc = expect_error(lambda: w.runtime.create_session(wrong), 401, "invalid_audience")
    check(AUD_FOUNDRY in exc.message, exc.message)
    check(classify(exc) == "fail", "fix the token scope; do not retry")
    foundry_token = w.ctx(RUNTIME, SANDBOX).token()
    expect_error(
        lambda: check_data_plane("models.chat", RUNTIME, foundry_token, SANDBOX, w.rbac, w.clock),
        401,
        "invalid_audience",
    )
    expect_error(
        lambda: check_data_plane("cosmos.write", RUNTIME, None, SANDBOX, w.rbac, w.clock), 401, "missing_token"
    )
    check(w.runtime.get_active_version(w.ctx(CALLER, INSIDE)) == "1", "the right audience works")


def scenario_ingress_drops_unprefixed_headers() -> None:
    w = World()
    w.deploy()
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-hdr")["id"]
    res = client.send_turn(sid, sid, "hi", "k-hdr-1", "corr-hdr")
    check(res.status == 200, str(res))
    fwd = w.runtime.last_forwarded
    check("authorization" not in fwd and "x-tenant-hint" not in fwd, f"unprefixed headers reached the agent: {sorted(fwd)}")
    check(fwd.get("x-client-correlation-id") == "corr-hdr", str(fwd))
    check(fwd.get("x-client-idempotency-key") == "k-hdr-1", str(fwd))
    check(fwd.get("x-agent-user-id") == CALLER, "the caller id is the calling principal, not the end user")
    check(fwd.get("x-agent-foundry-call-id", "").startswith("call-"), str(fwd))
    parsed = parse_traceparent(fwd.get("traceparent", ""))
    check(parsed is not None, "trace context is forwarded")
    trace_id, hop_span = parsed  # type: ignore[misc]
    check(trace_id == res.trace_id, "the trace id survives the platform hop")
    check(hop_span != _hex("corr-hdr-attempt-1", 16), "the platform adds its own span")
    w.flush_telemetry()
    spans = w.sink.find(**{"client.correlation_id": "corr-hdr"})
    check(len(spans) == 1, f"one turn span expected, got {len(spans)}")
    check(spans[0]["trace_id"] == res.trace_id and spans[0]["parent_span_id"] == hop_span, str(spans[0]))


# ---------------------------------------------------------------------------
# Scenarios 13-16: session hygiene, rehydration, versions, image pulls
# ---------------------------------------------------------------------------


def scenario_unpinned_retry_leaks_sessions() -> None:
    w = World()
    w.deploy()
    naive = UnpinnedRetryClient(w.runtime, w.ctx(), w.clock)
    res = naive.send("hello", "corr-naive")
    check(res.status == 424 and res.attempts == 4, f"the blind retry loop never got an answer: {res}")
    leaked = [sid for sid in sorted(w.runtime.sessions) if sid.startswith("sess-")]
    check(len(leaked) == 4, f"every unpinned attempt started a session, leaked={leaked}")
    check(all(not w.runtime.sessions[sid]["pinned"] for sid in leaked), "leaked sessions are unpinned")
    states = [w.session(sid)["state"] for sid in leaked]
    check(states.count(EXPIRED) == 3 and states[-1] == IDLE, f"leaked sessions linger until expiry: {states}")
    report = OrphanSweeper(w.runtime, w.ctx(OPERATOR), w.clock).run(apply=True)
    check(report["ended"] == [leaked[-1]], f"the sweeper ends the leaked session still alive: {report}")
    check(w.session(leaked[-1])["end_reason"] == "orphan_sweep", "ended by the sweep")
    before = len(w.runtime.sessions)
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-pinned")["id"]
    res2 = client.send_turn(sid, sid, "hello", "k-pinned-1", "corr-pinned")
    check(res2.status == 200 and res2.attempts == 1, f"the pinned client gets an answer: {res2}")
    check(len(w.runtime.sessions) == before + 1, "a pinned client starts exactly one session")
    client.end(sid)
    check(w.session(sid)["state"] == CLOSED, "and ends it explicitly")


def scenario_expired_session_rehydrates_same_key() -> None:
    w = World()
    w.deploy()
    client = w.client()
    key = "conv-rehydrate"
    sid = client.open_session(version="1", session_id=key)["id"]
    first = client.send_turn(sid, key, "open a case", "k-reh-1", "corr-reh-1", tool="open_case")
    check(first.status == 200 and "turn=1 notes=1" in first.output, str(first))
    w.clock.advance(IDLE_TIMEOUT_TICKS)
    check(w.session(sid)["state"] == EXPIRED, "the session expired while the user was away")
    second = client.send_turn(sid, key, "any update?", "k-reh-2", "corr-reh-2")
    check(second.status == 200 and second.recovered_sessions == [f"{key}-r1"], str(second))
    check(second.session_id == f"{key}-r1" and second.agent_version == "1", str(second))
    check("turn=2 notes=1" in second.output, f"state rehydrated from the store: {second.output}")
    check(w.session(f"{key}-r1")["pinned"], "recovery after expiry stays pinned to the same version")
    check(w.ledger.count(key) == 1, "no side effect repeated during recovery")
    again = client.send_turn(f"{key}-r1", key, "open a case", "k-reh-1", "corr-reh-3", tool="open_case")
    check(again.replayed and again.output == first.output, f"same key on the new session replays: {again}")
    check(w.ledger.count(key) == 1, "idempotency records live in the store, not in the container")


def scenario_new_version_does_not_evict() -> None:
    w = World()
    w.deploy()
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-v1")["id"]
    check(client.send_turn(sid, sid, "hi", "k-v1-1", "corr-v1-1").status == 200, "turn on v1")
    rec = w.pipeline().release("b2", w.source("2"))
    check(rec.outcome == "promoted" and rec.version == "2" and rec.rollback_target == "1", str(rec.stages))
    check(rec.drain_pending == ["conv-v1"], f"promote reports sessions still on old versions: {rec.drain_pending}")
    check(w.session(sid)["state"] in LIVE_STATES, "a new active version does not evict live sessions")
    old = client.send_turn(sid, sid, "still there?", "k-v1-2", "corr-v1-2")
    check(old.status == 200 and "version=1" in old.output and "turn=2" in old.output, old.output)
    fresh = client.open_session(session_id="conv-fresh")
    check(fresh["agent_version"] == "2" and not fresh["pinned"], f"new sessions get the active version: {fresh}")
    drained = w.pipeline().drain("1")
    check(drained == ["conv-v1"], f"drain ends the old version's sessions: {drained}")
    check(w.session(sid)["end_reason"] == "drain", "ended with reason drain")
    moved = client.send_turn(sid, sid, "after the drain", "k-v1-3", "corr-v1-3")
    check(moved.status == 200 and moved.session_id == "conv-v1-r1", str(moved))
    check(moved.agent_version == "2" and "turn=3" in moved.output, f"drained sessions move on: {moved.output}")
    check(not w.session("conv-v1-r1")["pinned"], "after a drain the client follows the active version")


def scenario_image_pull_failure_session_failed() -> None:
    w = World()
    w.deploy()
    client = w.client()
    alive = client.open_session(version="1", session_id="conv-alive")["id"]
    w.rbac.revoke(PROJECT_IDENTITY, ROLE_ACR_PULL, SCOPE_REGISTRY)
    expect_error(lambda: client.open_session(version="1", session_id="conv-pull"), 409, "session_failed")
    failure = w.session("conv-pull")["failure"] or ""
    check(failure.startswith("ImagePullFailed: 403"), failure)
    check(PROJECT_IDENTITY in failure and ROLE_ACR_PULL in failure, f"the message names who lacks what: {failure}")
    exc = expect_error(lambda: w.runtime.invoke(client.ctx, "conv-pull", {"kind": "turn"}, {}), 409, "session_failed")
    check(classify(exc) == "fail", "a failed session is not retried")
    client.end("conv-pull")
    check(w.session("conv-pull")["history"] == [CREATED, FAILED, CLOSED], str(w.session("conv-pull")["history"]))
    w.rbac.grant(PROJECT_IDENTITY, ROLE_ACR_PULL, SCOPE_REGISTRY)
    w.registry.delete(w.runtime.versions["1"]["image"])
    expect_error(lambda: client.open_session(version="1", session_id="conv-purged"), 409, "session_failed")
    purged = w.session("conv-purged")["failure"] or ""
    check("manifest_unknown" in purged and "retention" in purged, purged)
    res = client.send_turn(alive, alive, "still working?", "k-alive-1", "corr-alive")
    check(res.status == 200, f"a running session keeps the image it already pulled: {res}")


# ---------------------------------------------------------------------------
# Scenarios 17-20: release pipeline and telemetry
# ---------------------------------------------------------------------------


def scenario_pipeline_promote() -> None:
    w = World()
    rec = w.pipeline().release("b1", w.source("1"))
    check(rec.outcome == "promoted" and rec.version == "1", str(rec.stages))
    expected = ["build", "scan", "sign", "push", "verify_roles", "create_version", "smoke", "promote"]
    check(rec.stage_names() == expected, f"stage order: {rec.stage_names()}")
    check(rec.digest is not None and rec.digest == w.runtime.versions["1"]["image"], "versions reference the digest")
    check(rec.rollback_target is None, "the first release has nothing to roll back to")
    check(rec.detail("smoke").startswith("MODEL_OK"), rec.detail("smoke"))
    smoke = w.session("smoke-b1")
    check(smoke["state"] == CLOSED and smoke["end_reason"] == "smoke_complete", "the smoke session is ended")
    gate_calls: list[str | None] = []

    def eval_gate(r: ReleaseRecord) -> tuple[bool, str]:
        gate_calls.append(r.version)
        return True, "offline evaluation set within the agreed thresholds"

    rec2 = w.pipeline(sign=False, eval_gate=eval_gate).release("b2", w.source("2"))
    check(rec2.outcome == "promoted" and rec2.version == "2" and rec2.rollback_target == "1", str(rec2.stages))
    check("sign" not in rec2.stage_names() and "eval_gate" in rec2.stage_names(), str(rec2.stage_names()))
    check(gate_calls == ["2"], f"the evaluation gate ran once against the new version: {gate_calls}")
    check(w.active_version() == "2", "promote moved the active version")
    check(len(w.registry.images) == 2, "one immutable image per build")
    expect_error(
        lambda: w.runtime.create_version(w.ctx(PIPELINE), "<registry>.azurecr.io/agents/research:latest", DEFAULT_ENV),
        400,
        "invalid_image_reference",
    )


def scenario_pipeline_rollback() -> None:
    w = World()
    w.deploy()
    bad_env = {k: v for k, v in DEFAULT_ENV.items() if k != "AGENT_MODEL_DEPLOYMENT"}
    rec2 = w.pipeline().release("b2", w.source("2"), env=bad_env)
    check(rec2.outcome == "rolled_back", str(rec2.stages))
    check(rec2.stage_status().get("smoke") == "failed", str(rec2.stages))
    check("configuration_error" in rec2.detail("smoke"), rec2.detail("smoke"))
    check("promote" not in rec2.stage_names() and w.active_version() == "1", "the bad version never went live")
    rec3 = w.pipeline(eval_gate=lambda r: (False, "groundedness below the release threshold")).release(
        "b3", w.source("3")
    )
    check(rec3.outcome == "rolled_back" and rec3.stage_status().get("eval_gate") == "failed", str(rec3.stages))
    check(w.active_version() == "1", "a failed evaluation gate keeps the previous version")
    rec4 = w.pipeline().release("b4", w.source("4"))
    check(rec4.outcome == "promoted" and rec4.version == "4" and rec4.rollback_target == "1", str(rec4.stages))
    client = w.client()
    sid = client.open_session(session_id="conv-on-v4")["id"]
    first = client.send_turn(sid, sid, "hi", "k-v4-1", "corr-v4-1")
    check(first.agent_version == "4", str(first))
    drained = w.pipeline().rollback(rec4)
    check(drained == ["conv-on-v4"], f"rollback drains the bad version now: {drained}")
    check(w.active_version() == "1" and rec4.outcome == "rolled_back", "the recorded target is active again")
    check(w.session(sid)["end_reason"] == "rollback_drain", str(w.session(sid)))
    nxt = client.send_turn(sid, sid, "and now?", "k-v4-2", "corr-v4-2")
    check(nxt.status == 200 and nxt.agent_version == "1" and "turn=2" in nxt.output, str(nxt))
    check(not w.session("conv-on-v4-r1")["pinned"], "the drained conversation followed the active version")
    fresh = World()
    first_rec = fresh.pipeline().release("b1", fresh.source())
    try:
        fresh.pipeline().rollback(first_rec)
    except PipelineFailure as exc:
        check("no rollback target" in str(exc), str(exc))
    else:
        raise AssertionError("rolling back the first release must fail loudly")


def scenario_pipeline_fail_fast_gates() -> None:
    w = World()
    rec = w.pipeline().release("b-secret", w.source(findings=["secret"]))
    check(rec.outcome == "failed" and rec.stage_names()[-1] == "scan", str(rec.stages))
    check(not w.registry.images, "a blocked scan pushes nothing")
    rec = w.pipeline().release("b-base", w.source(base="public-hub/python:latest"))
    check(rec.stage_status().get("build") == "failed" and "approved list" in rec.detail("build"), str(rec.stages))
    w.rbac.revoke(PIPELINE, ROLE_ACR_PUSH, SCOPE_REGISTRY)
    rec = w.pipeline().release("b-push", w.source())
    check(rec.stage_status().get("push") == "failed", str(rec.stages))
    check("authorization_failed" in rec.detail("push") and ROLE_ACR_PUSH in rec.detail("push"), rec.detail("push"))
    w.rbac.grant(PIPELINE, ROLE_ACR_PUSH, SCOPE_REGISTRY)
    w.rbac.revoke(RUNTIME, ROLE_COSMOS_DATA, SCOPE_COSMOS)
    rec = w.pipeline().release("b-roles", w.source())
    detail = rec.detail("verify_roles")
    check(rec.stage_status().get("verify_roles") == "failed", str(rec.stages))
    check(ROLE_COSMOS_DATA in detail and RUNTIME in detail, detail)
    check(len(w.registry.images) == 1 and not w.runtime.versions, "pushed, but no version was created")
    w.rbac.grant(RUNTIME, ROLE_COSMOS_DATA, SCOPE_COSMOS)
    rec = w.pipeline().release("b-medium", w.source(findings=["medium"]))
    check(rec.outcome == "promoted", f"non-blocking findings do not stop the release: {rec.stages}")


def scenario_spans_carry_correlation_and_version() -> None:
    w = World()
    w.deploy()
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-otel")["id"]
    key = "northwind-case-118"
    a = client.send_turn(sid, key, "summarize the case file", "k-otel-1", "corr-otel-1")
    b = client.send_turn(sid, key, "draft the reply", "k-otel-2", "corr-otel-2")
    check(a.status == 200 and b.status == 200, f"{a} {b}")
    client.end(sid)
    turns = [s for s in w.sink.spans if s["name"] == "agent.turn" and s["attributes"]["agent.session_id"] == sid]
    check(len(turns) == 2, f"one span per turn after shutdown flush, got {len(turns)}")
    for span, res, corr in zip(turns, (a, b), ("corr-otel-1", "corr-otel-2")):
        attrs = span["attributes"]
        check(attrs["client.correlation_id"] == corr, str(attrs))
        check(span["trace_id"] == res.trace_id, "the caller's trace id is preserved")
        check(attrs["agent.version"] == "1" and attrs["gen_ai.operation.name"] == "invoke_agent", str(attrs))
        check(attrs["platform.call_id"].startswith("call-"), str(attrs))
        check(attrs["conversation.key_hash"] == hashlib.sha256(key.encode()).hexdigest()[:12], str(attrs))
    check([s["attributes"]["session.first_turn"] for s in turns] == [True, False], "first-turn flag")
    dump = json.dumps(w.sink.spans)
    for forbidden in ("summarize the case file", "draft the reply", "k-otel-1", key):
        check(forbidden not in dump, f"telemetry must not contain '{forbidden}'")


# ---------------------------------------------------------------------------
# Scenarios 21-24: egress, hygiene, keep-warm, production seam
# ---------------------------------------------------------------------------


def scenario_telemetry_flush_and_blocked_egress() -> None:
    w = World()
    w.deploy()
    client = w.client()
    sid = client.open_session(version="1", session_id="conv-batch")["id"]
    for i in range(1, 4):
        check(client.send_turn(sid, sid, f"q{i}", f"k-batch-{i}", f"corr-batch-{i}").status == 200, f"turn {i}")
    batch = w.sink.find(**{"agent.session_id": sid})
    check(len(batch) == TELEMETRY_BATCH_SIZE, f"readiness + 3 turns fill one batch, exported {len(batch)}")
    client.send_turn(sid, sid, "q4", "k-batch-4", "corr-batch-4")
    check(len(w.sink.find(**{"agent.session_id": sid})) == TELEMETRY_BATCH_SIZE, "a partial batch stays buffered")
    client.end(sid)
    check(len(w.sink.find(**{"agent.session_id": sid})) == TELEMETRY_BATCH_SIZE + 1, "shutdown flushed the rest")

    no_monitor = Placement("agent-sandbox-no-monitor", True, frozenset({"entra", "models", "cosmos", "gateway"}))
    quiet = World(sandbox=no_monitor)
    quiet.deploy()
    qc = quiet.client()
    qsid = qc.open_session(version="1", session_id="conv-dark")["id"]
    check(qc.send_turn(qsid, qsid, "hello", "k-dark-1", "corr-dark-1").status == 200, "the agent still works")
    qc.end(qsid)
    check(not quiet.sink.spans, "nothing reached the monitoring endpoint")
    dropped = sum(e.dropped for e in quiet.runtime.exporters)
    warnings = [m for e in quiet.runtime.exporters for m in e.warnings]
    check(dropped > 0, "spans were dropped, not silently queued forever")
    check(any(m.startswith("telemetry export blocked by egress") for m in warnings), str(warnings))

    w.runtime.sandbox_network = Placement(
        "agent-sandbox-no-gateway", True, frozenset({"entra", "models", "cosmos", "monitor"})
    )
    gsid = client.open_session(version="1", session_id="conv-no-gateway")["id"]
    res = client.send_turn(gsid, gsid, "file it", "k-gw-1", "corr-gw-1", tool="open_case")
    check(res.status == 502 and "egress_blocked" in (res.error or "") and res.attempts == 1, str(res))
    check(w.ledger.count(gsid) == 0, "a blocked tool call has no side effect")
    client.end(gsid)
    errored = [s for s in w.sink.find(**{"error.type": "egress_blocked"}) if s["attributes"]["agent.session_id"] == gsid]
    check(len(errored) == 1, f"the blocked call is visible in telemetry: {len(errored)}")
    rec = w.pipeline().release("b2", w.source("2"))
    check(rec.outcome == "rolled_back" and w.active_version() == "1", f"smoke catches the egress gap: {rec.stages}")


def scenario_orphan_sweep() -> None:
    w = World()
    w.deploy()
    client = w.client()
    ids = [client.open_session(version="1", session_id=s, wait_ready=False)["id"] for s in ("conv-a", "conv-b", "conv-c")]
    for sid in ids:
        client.heartbeat(sid)
    w.clock.advance(SWEEP_IDLE_WINDOW_TICKS + 1)
    check(client.send_turn("conv-c", "conv-c", "status?", "k-c-1", "corr-c-1").status == 200, "c is in use")
    sweeper = OrphanSweeper(w.runtime, w.ctx(OPERATOR), w.clock, keep={"conv-b"})
    dry = sweeper.run()
    check(dry["dry_run"] and dry["candidates"] == ["conv-a"] and dry["kept"] == ["conv-b"], str(dry))
    check(dry["ended"] == [] and w.session("conv-a")["state"] in LIVE_STATES, "a dry run changes nothing")
    applied = sweeper.run(apply=True)
    check(applied["ended"] == ["conv-a"], str(applied))
    check(w.session("conv-a")["end_reason"] == "orphan_sweep", str(w.session("conv-a")))
    check(w.session("conv-b")["state"] in LIVE_STATES, "the keep list is honoured")
    expect_error(lambda: OrphanSweeper(w.runtime, w.ctx(OPERATOR, INTERNET), w.clock).run(), 403, "public_access_disabled")
    expect_error(lambda: OrphanSweeper(w.runtime, w.ctx(DEVELOPER), w.clock).run(), 403, "authorization_failed")


def scenario_keep_warm_detector() -> None:
    w = World()
    w.deploy()
    client = w.client()
    names = ("conv-warm", "conv-used", "conv-ghost")
    for name in names:
        client.open_session(version="1", session_id=name, wait_ready=False)
    w.clock.advance(3)
    check(client.send_turn("conv-warm", "conv-warm", "one question", "k-w-1", "corr-w-1").status == 200, "warm")
    for i in range(5):
        w.clock.advance(2)
        for name in names:
            client.heartbeat(name)
        res = client.send_turn("conv-used", "conv-used", f"q{i}", f"k-u-{i}", f"corr-u-{i}")
        check(res.status == 200, str(res))
    found = KeepWarmDetector().scan(w.runtime.activity, w.clock.now())
    by_id = {f["session_id"]: f for f in found}
    check(sorted(by_id) == ["conv-ghost", "conv-warm"], f"flagged: {sorted(by_id)}")
    check(by_id["conv-warm"]["heartbeats_since_user_turn"] == 5, str(by_id["conv-warm"]))
    check(by_id["conv-warm"]["ticks_since_user_turn"] == 10, str(by_id["conv-warm"]))
    check(by_id["conv-ghost"]["ticks_since_user_turn"] is None, "never used at all")
    report = OrphanSweeper(w.runtime, w.ctx(OPERATOR), w.clock, detector=KeepWarmDetector()).run(
        activity=w.runtime.activity
    )
    check(report["candidates"] == [], "heartbeats hide these sessions from an idle-based sweep")
    suspects = sorted(s["session_id"] for s in report["keep_warm_suspects"])
    check(suspects == ["conv-ghost", "conv-warm"], f"the detector still sees them: {suspects}")
    w.clock.advance(IDLE_TIMEOUT_TICKS)
    live = {v["id"] for v in w.runtime.list_sessions(w.ctx(OPERATOR)) if v["state"] in LIVE_STATES}
    check(not live, f"once the pings stop the platform expires them: {live}")
    check(KeepWarmDetector().scan(w.runtime.activity, w.clock.now(), live) == [], "nothing live to flag")


def scenario_production_adapter_gated() -> None:
    saved = os.environ.pop(LIVE_OPT_IN_ENV, None)
    try:
        try:
            FoundryHostedAgentAdapter()
        except NotImplementedError as exc:
            check(LIVE_OPT_IN_ENV in str(exc), str(exc))
        else:
            raise AssertionError("the adapter must refuse to construct without an explicit opt-in")
        os.environ[LIVE_OPT_IN_ENV] = "1"
        adapter = FoundryHostedAgentAdapter()
        check(adapter.project_endpoint.startswith("https://<foundry-account>"), adapter.project_endpoint)
        for call in (
            lambda: adapter.create_session(None),
            lambda: adapter.invoke(None, "s", {}, {}),
            lambda: adapter.list_sessions(None),
        ):
            try:
                call()
            except NotImplementedError:
                continue
            raise AssertionError("adapter methods must not pretend to work")
    finally:
        if saved is None:
            os.environ.pop(LIVE_OPT_IN_ENV, None)
        else:
            os.environ[LIVE_OPT_IN_ENV] = saved
    seams: list[tuple[type, tuple[type, ...]]] = [
        (Clock, (LogicalClock,)),
        (TokenProvider, (CachedTokenProvider,)),
        (NetworkContext, (Placement,)),
        (SessionStore, (InMemoryStateStore,)),
        (TelemetrySink, (InMemoryTelemetrySink,)),
        (AgentRuntime, (FakeFoundryRuntime, FoundryHostedAgentAdapter)),
    ]
    for proto, impls in seams:
        members = [n for n, v in vars(proto).items() if not n.startswith("_") and callable(v)]
        check(bool(members), f"{proto.__name__} declares no members")
        for impl in impls:
            missing = [n for n in members if not callable(getattr(impl, n, None))]
            check(not missing, f"{impl.__name__} does not satisfy {proto.__name__}: missing {missing}")
    check("name" in Placement.__dataclass_fields__, "NetworkContext.name is provided by Placement")


SCENARIOS: list[tuple[str, Callable[[], None]]] = [
    ("outside-vnet-403", scenario_outside_vnet_403),
    ("missing-role-403-clear-message", scenario_missing_role_403_clear_message),
    ("token-cache-per-audience", scenario_token_cache_per_audience),
    ("token-refresh-skew", scenario_token_refresh_skew),
    ("session-state-machine", scenario_session_state_machine),
    ("heartbeat-extends-idle", scenario_heartbeat_extends_idle),
    ("idle-expiry", scenario_idle_expiry),
    ("invoke-retry-idempotent", scenario_invoke_retry_idempotent),
    ("active-not-ready-424-bounded-retry", scenario_active_not_ready_424_bounded_retry),
    ("retry-classification", scenario_retry_classification),
    ("wrong-audience-401", scenario_wrong_audience_401),
    ("ingress-drops-unprefixed-headers", scenario_ingress_drops_unprefixed_headers),
    ("unpinned-retry-leaks-sessions", scenario_unpinned_retry_leaks_sessions),
    ("expired-session-rehydrates-same-key", scenario_expired_session_rehydrates_same_key),
    ("new-version-does-not-evict", scenario_new_version_does_not_evict),
    ("image-pull-failure-session-failed", scenario_image_pull_failure_session_failed),
    ("pipeline-promote", scenario_pipeline_promote),
    ("pipeline-rollback", scenario_pipeline_rollback),
    ("pipeline-fail-fast-gates", scenario_pipeline_fail_fast_gates),
    ("spans-carry-correlation-and-version", scenario_spans_carry_correlation_and_version),
    ("telemetry-flush-and-blocked-egress", scenario_telemetry_flush_and_blocked_egress),
    ("orphan-sweep", scenario_orphan_sweep),
    ("keep-warm-detector", scenario_keep_warm_detector),
    ("production-adapter-gated", scenario_production_adapter_gated),
]


# ---------------------------------------------------------------------------
# Entry points
# ---------------------------------------------------------------------------


def _say(clock: Clock, text: str) -> None:
    print(f"[t={clock.now():03d}] {text}")


def _print_release(clock: Clock, rec: ReleaseRecord) -> None:
    _say(
        clock,
        f"release {rec.build_id}: outcome={rec.outcome} version={rec.version} "
        f"rollback_target={rec.rollback_target}",
    )
    for name, status, detail in rec.stages:
        print(f"          {name:<15} {status:<8} {detail}")


def run_self_test() -> int:
    passed = 0
    for name, fn in SCENARIOS:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001 - report every failure, keep going
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            continue
        passed += 1
        print(f"PASS {name}")
    total = len(SCENARIOS)
    print(f"{passed}/{total} scenarios passed")
    return 0 if passed == total else 1


def run_demo() -> int:
    w = World()
    clock = w.clock
    print("Hosted agent operations simulator: fictional Contoso world, logical clock ticks only")
    print("")

    print("1. Release from a public runner, then from a runner inside the VNet")
    _print_release(clock, w.pipeline(placement=INTERNET).release("b-public", w.source("1")))
    _print_release(clock, w.pipeline().release("b1", w.source("1")))
    print("")

    print("2. Open a pinned session and wait until it is ready, not just ACTIVE")
    client = w.client()
    view = client.open_session(version="1", session_id="fabrikam-case-1")
    sid = view["id"]
    _say(
        clock,
        f"session {sid} state={view['state']} version={view['agent_version']} "
        f"pinned={view['pinned']} readiness_attempts={client.readiness_attempts}",
    )
    print("")

    print("3. The first response is lost; the retry reuses the idempotency key")
    w.runtime.faults.append(("lose_response", 0))
    res = client.send_turn(sid, sid, "open a maintenance ticket", "k-demo-1", "corr-demo-1", tool="create_ticket")
    _say(
        clock,
        f"status={res.status} attempts={res.attempts} replayed={res.replayed} "
        f"side_effects={w.ledger.count(sid)} output='{res.output}'",
    )
    print("")

    print("4. The session expires while the user is away; the client recovers on the pinned version")
    clock.advance(IDLE_TIMEOUT_TICKS + 1)
    _say(clock, f"session {sid} state={w.session(sid)['state']}")
    res = client.send_turn(sid, sid, "any update on the ticket?", "k-demo-2", "corr-demo-2")
    _say(
        clock,
        f"status={res.status} recovered={res.recovered_sessions} version={res.agent_version} "
        f"output='{res.output}'",
    )
    ended = client.end(res.session_id or sid)
    _say(clock, f"session {ended['id']} ended explicitly: state={ended['state']} reason={ended['end_reason']}")
    print("")

    print("5. A release with a missing setting fails its smoke test and is rolled back")
    bad_env = {k: v for k, v in DEFAULT_ENV.items() if k != "AGENT_MODEL_DEPLOYMENT"}
    _print_release(clock, w.pipeline().release("b2", w.source("2"), env=bad_env))
    _say(clock, f"active version is still {w.active_version()}")
    print("")

    print("6. Orphan sweep and keep-warm detection")
    orphan = client.open_session(version="1", session_id="northwind-orphan", wait_ready=False)["id"]
    warm = client.open_session(version="1", session_id="northwind-keepwarm", wait_ready=False)["id"]
    client.heartbeat(warm)
    for _ in range(3):
        clock.advance(2)
        client.heartbeat(warm)
    sweeper = OrphanSweeper(w.runtime, w.ctx(OPERATOR), clock, detector=KeepWarmDetector())
    report = sweeper.run(activity=w.runtime.activity)
    _say(clock, f"dry run: candidates={report['candidates']} kept={report['kept']}")
    for suspect in report["keep_warm_suspects"]:
        _say(
            clock,
            f"keep-warm suspect {suspect['session_id']}: "
            f"heartbeats_since_user_turn={suspect['heartbeats_since_user_turn']} ({suspect['finding']})",
        )
    applied = sweeper.run(apply=True)
    _say(clock, f"apply: ended={applied['ended']}; {orphan} reason={w.session(orphan)['end_reason']}")
    clock.advance(IDLE_TIMEOUT_TICKS)
    _say(clock, f"pings stopped: {warm} state={w.session(warm)['state']}")
    print("")

    print("7. Spans exported by the container (no prompts, no keys, hashed conversation key)")
    w.flush_telemetry()
    for span in w.sink.spans:
        attrs = span["attributes"]
        if span["name"] != "agent.turn":
            continue
        print(
            f"          {span['name']:<12} session={attrs.get('agent.session_id')} "
            f"version={attrs.get('agent.version')} correlation={attrs.get('client.correlation_id')} "
            f"replayed={attrs.get('idempotency.replayed')} error={attrs.get('error.type')}"
        )
    print("")
    print("Run with --self-test to check every behaviour above (and more) as assertions.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hosted agent operations simulator (stdlib only, test only).")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--self-test", action="store_true", help="run every scenario as an assertion")
    mode.add_argument("--demo", action="store_true", help="print a readable trace (the default)")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    return run_demo()


if __name__ == "__main__":
    sys.dont_write_bytecode = True
    sys.exit(main())
