#!/usr/bin/env python3
"""Offline simulation of a streaming model gateway in front of a hosted agent.

TEST ONLY. Standard library only (Python 3.10+). No network calls, no real
tokens, no real clocks: every delay is a logical tick, so every run is
deterministic. Names, callers and policies are fictional.

What the simulation models
  - Caller authentication: a fake HMAC-signed JWT stands in for an Entra token.
    The gateway checks signature, issuer, audience, tenant, expiry, the
    required app role and the azp/appid allowlist, and fails closed.
  - Per-caller policy: model allowlist, max_tokens clamp, token budget
    (reserve before the call, settle on real usage) and a content-safety hook.
  - Routing with failover that is only allowed BEFORE the first data frame
    reaches the client. After the first data frame a failure becomes an SSE
    error frame and the stream closes without [DONE]; nothing is replayed.
  - SSE framing in OpenAI Chat Completions chunk shape, keepalive comments,
    an ingress layer with an idle timeout and an optional buffering defect.
  - Client disconnect propagates to the upstream (the backend generator is
    closed), and usage is still attributed.
  - W3C traceparent propagation and a per-request usage event with no prompt
    or completion text in it.

Modes
  --self-test                         named scenarios, PASS/FAIL, exit 1 on any failure
  --domain contoso-retail|fabrikam-health|northwind-logistics|all
  --demo                              narrated walkthrough printing SSE frames

Production stubs at the bottom raise NotImplementedError and point at the
deployable code in ../src/gateway/app.py.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import hmac
import json
import sys
from dataclasses import dataclass, field
from typing import Callable, Iterator, Protocol

# ---------------------------------------------------------------------------
# Constants for the fictional tenant. None of these are real identifiers.
# ---------------------------------------------------------------------------

SIM_SIGNING_SECRET = b"simulation-only-hmac-secret-not-used-anywhere-else"
SIM_TENANT = "tenant-contoso-sim"
SIM_ISSUER = f"https://login.example.com/{SIM_TENANT}/v2.0"
SIM_AUDIENCE = "api://model-gateway-sim"
SIM_APP_ROLE = "Gateway.Invoke"
SIM_EPOCH = 1_000_000  # logical "now" for token checks

SSE_HEADERS = {
    "content-type": "text/event-stream",
    "cache-control": "no-cache",
    "x-accel-buffering": "no",
}


# ---------------------------------------------------------------------------
# Fake token issuer (stands in for Microsoft Entra ID)
# ---------------------------------------------------------------------------

def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _b64url_decode(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def issue_token(
    *,
    azp: str,
    aud: str = SIM_AUDIENCE,
    iss: str = SIM_ISSUER,
    tid: str = SIM_TENANT,
    roles: tuple[str, ...] = (SIM_APP_ROLE,),
    exp_offset: int = 600,
    use_appid_claim: bool = False,
    secret: bytes = SIM_SIGNING_SECRET,
) -> str:
    header = {"alg": "HS256", "typ": "JWT"}
    claims: dict[str, object] = {
        "iss": iss,
        "aud": aud,
        "tid": tid,
        "oid": f"oid-{azp}",
        "roles": list(roles),
        "nbf": SIM_EPOCH - 60,
        "exp": SIM_EPOCH + exp_offset,
    }
    claims["appid" if use_appid_claim else "azp"] = azp
    signing_input = _b64url(json.dumps(header).encode()) + "." + _b64url(json.dumps(claims).encode())
    signature = hmac.new(secret, signing_input.encode(), hashlib.sha256).digest()
    return signing_input + "." + _b64url(signature)


# ---------------------------------------------------------------------------
# Errors and wire shapes
# ---------------------------------------------------------------------------

class GatewayError(Exception):
    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    def envelope(self, request_id: str) -> dict[str, object]:
        return {
            "error": {
                "message": self.message,
                "type": _ERROR_TYPES.get(self.status, "gateway_error"),
                "code": self.code,
                "param": None,
                "request_id": request_id,
                "retryable": self.retryable,
            }
        }


_ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    409: "conflict_error",
    429: "rate_limit_error",
    502: "upstream_error",
    503: "service_unavailable",
}


class ConfigError(Exception):
    pass


class BackendError(Exception):
    """Upstream failure (connection reset, 5xx, protocol error)."""


class ClientGone(Exception):
    """The client disconnected; stop work and cancel the upstream."""


class IngressCut(Exception):
    """An ingress layer closed the connection after an idle gap."""


class FirstByteTimeout(Exception):
    """The upstream did not produce a first event within the deadline."""


@dataclass(frozen=True)
class Claims:
    caller_id: str
    tenant_id: str
    object_id: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: tuple[dict[str, str], ...]
    max_tokens: int | None
    include_usage: bool


@dataclass(frozen=True)
class UpstreamRequest:
    model: str
    messages: tuple[dict[str, str], ...]
    max_tokens: int
    traceparent: str
    request_id: str


# Upstream events. Idle advances the logical clock without output.
@dataclass(frozen=True)
class Idle:
    ticks: int


@dataclass(frozen=True)
class Delta:
    text: str
    tokens: int = 1


@dataclass(frozen=True)
class Finish:
    reason: str = "stop"


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int
    completion_tokens: int

    @property
    def total(self) -> int:
        return self.prompt_tokens + self.completion_tokens


BackendEvent = Idle | Delta | Finish | Usage


class ModelBackend(Protocol):
    name: str

    def stream(self, request: UpstreamRequest) -> Iterator[BackendEvent]: ...


class ContentSafetyHook(Protocol):
    def check_input(self, messages: tuple[dict[str, str], ...]) -> str | None: ...


class UsageSink(Protocol):
    def emit(self, event: dict[str, object]) -> None: ...


# ---------------------------------------------------------------------------
# Fake backends
# ---------------------------------------------------------------------------

class FakeBackend:
    """Scripted upstream. Records calls, cancellations and generated tokens."""

    def __init__(
        self,
        name: str,
        *,
        deltas: tuple[str, ...] = ("Hello", ",", " world", "."),
        first_byte_idle: int = 0,
        gap_idle: int = 0,
        fail_before_first_byte: bool = False,
        fail_after_deltas: int | None = None,
        prompt_tokens: int = 12,
    ) -> None:
        self.name = name
        self.deltas = deltas
        self.first_byte_idle = first_byte_idle
        self.gap_idle = gap_idle
        self.fail_before_first_byte = fail_before_first_byte
        self.fail_after_deltas = fail_after_deltas
        self.prompt_tokens = prompt_tokens
        self.calls = 0
        self.cancelled = 0
        self.completed = 0
        self.generated_tokens = 0  # what this provider would bill for output
        self.seen_traceparents: list[str] = []
        self.seen_max_tokens: list[int] = []

    def stream(self, request: UpstreamRequest) -> Iterator[BackendEvent]:
        self.calls += 1
        self.seen_traceparents.append(request.traceparent)
        self.seen_max_tokens.append(request.max_tokens)
        return self._generate()

    def _generate(self) -> Iterator[BackendEvent]:
        try:
            if self.first_byte_idle:
                yield Idle(self.first_byte_idle)
            if self.fail_before_first_byte:
                raise BackendError(f"{self.name}: upstream unavailable before first byte")
            for index, text in enumerate(self.deltas):
                if self.fail_after_deltas is not None and index == self.fail_after_deltas:
                    raise BackendError(f"{self.name}: connection reset mid-stream")
                if index and self.gap_idle:
                    yield Idle(self.gap_idle)
                self.generated_tokens += 1
                yield Delta(text)
            yield Finish("stop")
            yield Usage(self.prompt_tokens, len(self.deltas))
            self.completed += 1
        except GeneratorExit:
            self.cancelled += 1
            raise


class KeywordSafetyHook:
    """Stand-in for a content-safety service: blocks inputs containing terms."""

    def __init__(self, blocked_terms: tuple[str, ...]) -> None:
        self.blocked_terms = tuple(t.lower() for t in blocked_terms)

    def check_input(self, messages: tuple[dict[str, str], ...]) -> str | None:
        text = " ".join(m.get("content", "") for m in messages).lower()
        for term in self.blocked_terms:
            if term in text:
                return "blocked_term"
        return None


class MemoryUsageSink:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    def emit(self, event: dict[str, object]) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Ingress and client model
# ---------------------------------------------------------------------------

@dataclass
class ClientConnection:
    """The hosted agent's HTTP connection, seen through the ingress chain.

    ingress_idle_timeout: the shortest idle timeout of every proxy between the
    agent and the gateway. A gap longer than this closes the connection.
    buffering: models a proxy that buffers the response until it completes.
    disconnect_after_data_frames: the client goes away after N data frames.
    """

    ingress_idle_timeout: int
    buffering: bool = False
    disconnect_after_data_frames: int | None = None
    status: int | None = None
    headers: dict[str, str] = field(default_factory=dict)
    json_body: dict[str, object] | None = None
    frames: list[tuple[int, str]] = field(default_factory=list)
    connected: bool = True
    cut_reason: str | None = None
    _buffer: list[str] = field(default_factory=list)
    _last_activity: int = 0
    _data_frames_seen: int = 0

    def start_stream(self, headers: dict[str, str], now: int) -> None:
        self._check_idle(now)
        self.status = 200
        self.headers = dict(headers)
        self._last_activity = now

    def write(self, frame: str, now: int) -> None:
        if not self.connected:
            raise ClientGone()
        self._check_idle(now)
        self._last_activity = now
        if self.buffering:
            self._buffer.append(frame)
        else:
            self.frames.append((now, frame))
        if frame.startswith("data: "):
            self._data_frames_seen += 1
            if (
                self.disconnect_after_data_frames is not None
                and self._data_frames_seen >= self.disconnect_after_data_frames
            ):
                self.connected = False

    def respond_json(self, status: int, body: dict[str, object], now: int) -> None:
        self._check_idle(now)
        self.status = status
        self.headers = {"content-type": "application/json"}
        self.json_body = body

    def close(self, now: int) -> None:
        if self._buffer:
            self.frames.extend((now, f) for f in self._buffer)
            self._buffer.clear()

    def _check_idle(self, now: int) -> None:
        if now - self._last_activity > self.ingress_idle_timeout:
            self.connected = False
            self.cut_reason = "ingress_idle_timeout"
            raise IngressCut()

    # Helpers used by tests and the demo -------------------------------------
    def data_payloads(self) -> list[object]:
        out: list[object] = []
        for _, frame in self.frames:
            if frame.startswith("data: "):
                raw = frame[len("data: "):].strip()
                out.append(raw if raw == "[DONE]" else json.loads(raw))
        return out

    def content_text(self) -> str:
        parts = []
        for payload in self.data_payloads():
            if isinstance(payload, dict):
                for choice in payload.get("choices", []):
                    parts.append(choice.get("delta", {}).get("content") or "")
        return "".join(parts)

    def keepalives(self) -> int:
        return sum(1 for _, f in self.frames if f.startswith(":"))


def looks_buffered(client: ClientConnection) -> bool:
    """Streaming check used in troubleshooting: did data frames arrive spread out?

    A buffered response delivers every data frame at the same instant, at the
    end. A streamed response delivers the first frame well before the last.
    """
    ticks = [t for t, f in client.frames if f.startswith("data: ")]
    return len(ticks) > 2 and ticks[0] == ticks[-1]


# ---------------------------------------------------------------------------
# Gateway configuration, policy and state
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class CallerPolicy:
    caller_id: str
    display_name: str
    allowed_models: frozenset[str]
    token_budget: int
    max_tokens_cap: int
    blocked_terms: tuple[str, ...] = ()


@dataclass(frozen=True)
class GatewayConfig:
    issuer: str
    audiences: tuple[str, ...]
    tenant_id: str
    required_role: str
    callers: tuple[CallerPolicy, ...]
    routes: dict[str, tuple[str, ...]]
    ingress_idle_timeout: int
    keepalive_every: int
    first_byte_deadline: int
    max_attempts: int
    early_commit: bool = False
    allow_retry_after_first_byte: bool = False  # anti-pattern switch, tests only

    def problems(self) -> list[str]:
        found = []
        if not self.issuer:
            found.append("issuer is empty")
        if not self.audiences or any(not a for a in self.audiences):
            found.append("audiences are empty")
        if not self.tenant_id:
            found.append("tenant_id is empty")
        if not self.required_role:
            found.append("required_role is empty")
        if not self.callers:
            found.append("no callers are allowlisted")
        for caller in self.callers:
            for model in caller.allowed_models:
                if not self.routes.get(model):
                    found.append(f"model {model!r} allowed for {caller.caller_id} has no route")
        if self.keepalive_every and self.keepalive_every >= self.ingress_idle_timeout:
            found.append("keepalive interval must be shorter than the ingress idle timeout")
        if self.early_commit and not self.keepalive_every:
            found.append("early-commit mode requires keepalives")
        if not self.early_commit and self.first_byte_deadline * self.max_attempts >= self.ingress_idle_timeout:
            found.append(
                "hold-headers mode: first_byte_deadline x max_attempts must stay under the ingress idle timeout"
            )
        return found


class TokenLedger:
    """Per-caller token budget. Reserve before the call, settle on usage.

    In-memory only. A multi-replica gateway needs a shared store; see
    docs/architecture.md (policy enforcement points).
    """

    def __init__(self) -> None:
        self.used: dict[str, int] = {}
        self.reserved: dict[str, int] = {}

    def reserve(self, policy: CallerPolicy, amount: int) -> None:
        used = self.used.get(policy.caller_id, 0)
        held = self.reserved.get(policy.caller_id, 0)
        if used + held + amount > policy.token_budget:
            raise GatewayError(
                429, "token_budget_exceeded", "caller token budget exhausted for this window", retryable=True
            )
        self.reserved[policy.caller_id] = held + amount

    def settle(self, caller_id: str, reserved: int, actual: int) -> None:
        self.reserved[caller_id] = self.reserved.get(caller_id, 0) - reserved
        self.used[caller_id] = self.used.get(caller_id, 0) + actual


@dataclass
class StreamState:
    request_id: str
    model: str
    include_usage: bool
    now: int = 0
    headers_committed: bool = False
    data_frames: int = 0
    delivered_tokens: int = 0
    usage: Usage | None = None
    last_write: int = 0


def _estimate_prompt_tokens(messages: tuple[dict[str, str], ...]) -> int:
    return sum(len(m.get("content", "")) // 4 + 1 for m in messages)


def _child_traceparent(incoming: str | None, request_id: str) -> tuple[str, str]:
    """Return (trace_id, outgoing traceparent). Starts a trace if none is valid."""
    trace_id = ""
    if incoming:
        parts = incoming.split("-")
        if len(parts) == 4 and len(parts[1]) == 32 and len(parts[2]) == 16:
            trace_id = parts[1]
    if not trace_id:
        trace_id = hashlib.sha256(("trace:" + request_id).encode()).hexdigest()[:32]
    span_id = hashlib.sha256(("span:" + request_id).encode()).hexdigest()[:16]
    return trace_id, f"00-{trace_id}-{span_id}-01"


def _sse(payload: object) -> str:
    return "data: " + json.dumps(payload, separators=(",", ":")) + "\n\n"


class StreamingGateway:
    def __init__(
        self,
        config: GatewayConfig,
        backends: dict[str, ModelBackend],
        *,
        ledger: TokenLedger | None = None,
        usage_sink: UsageSink | None = None,
        idempotency: dict[tuple[str, str], str] | None = None,
        secret: bytes = SIM_SIGNING_SECRET,
    ) -> None:
        problems = config.problems()
        for model, names in config.routes.items():
            for name in names:
                if name not in backends:
                    problems.append(f"route {model!r} references unknown backend {name!r}")
        if problems:
            raise ConfigError("; ".join(problems))
        self.config = config
        self.backends = backends
        self.policies = {c.caller_id: c for c in config.callers}
        self.ledger = ledger or TokenLedger()
        self.usage_sink = usage_sink or MemoryUsageSink()
        self.idempotency = idempotency if idempotency is not None else {}
        self._secret = secret
        self._counter = 0

    # -- authentication ----------------------------------------------------
    def authenticate(self, authorization: str | None) -> Claims:
        if not authorization or not authorization.startswith("Bearer "):
            raise GatewayError(401, "missing_token", "bearer token required")
        token = authorization[len("Bearer "):]
        try:
            head, body, sig = token.split(".")
        except ValueError:
            raise GatewayError(401, "invalid_token", "malformed token") from None
        expected = hmac.new(self._secret, f"{head}.{body}".encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _b64url_decode(sig)):
            raise GatewayError(401, "invalid_token", "signature check failed")
        claims = json.loads(_b64url_decode(body))
        if claims.get("iss") != self.config.issuer:
            raise GatewayError(401, "invalid_issuer", "issuer not trusted")
        if claims.get("aud") not in self.config.audiences:
            raise GatewayError(401, "invalid_audience", "token audience does not match this gateway")
        if claims.get("tid") != self.config.tenant_id:
            raise GatewayError(401, "invalid_tenant", "token tenant not trusted")
        if not (claims.get("nbf", SIM_EPOCH + 1) <= SIM_EPOCH < claims.get("exp", 0)):
            raise GatewayError(401, "token_expired", "token outside its validity window")
        roles = tuple(claims.get("roles", ()))
        if self.config.required_role not in roles:
            raise GatewayError(403, "missing_app_role", "caller lacks the gateway app role")
        caller_id = claims.get("azp") or claims.get("appid") or ""
        if caller_id not in self.policies:
            raise GatewayError(403, "caller_not_allowed", "caller is not on the gateway allowlist")
        return Claims(caller_id, claims["tid"], claims.get("oid", ""), roles)

    # -- request handling -----------------------------------------------------
    def handle(
        self,
        *,
        authorization: str | None,
        body: dict[str, object],
        client: ClientConnection,
        traceparent: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, object]:
        self._counter += 1
        request_id = f"req-{self._counter:04d}"
        trace_id, outgoing_tp = _child_traceparent(traceparent, request_id)
        event: dict[str, object] = {
            "event": "gateway.usage",
            "request_id": request_id,
            "trace_id": trace_id,
            "caller_id": None,
            "tenant_id": None,
            "model": body.get("model"),
            "backend": None,
            "attempts": [],
            "status": None,
            "outcome": None,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "usage_source": "none",
        }
        try:
            claims = self.authenticate(authorization)
            event["caller_id"], event["tenant_id"] = claims.caller_id, claims.tenant_id
            policy = self.policies[claims.caller_id]
            request = self._parse(body)
            if idempotency_key and (claims.caller_id, idempotency_key) in self.idempotency:
                raise GatewayError(409, "duplicate_request", "this idempotency key already produced output")
            if request.model not in policy.allowed_models:
                raise GatewayError(403, "model_not_allowed", f"model {request.model!r} is not allowed for this caller")
            if policy.blocked_terms and KeywordSafetyHook(policy.blocked_terms).check_input(request.messages):
                raise GatewayError(400, "content_filtered", "input blocked by the content-safety hook")
            max_tokens = min(request.max_tokens or policy.max_tokens_cap, policy.max_tokens_cap)
            reservation = _estimate_prompt_tokens(request.messages) + max_tokens
            self.ledger.reserve(policy, reservation)
        except GatewayError as err:
            client.respond_json(err.status, err.envelope(request_id), 0)
            event.update(status=err.status, outcome=err.code)
            self.usage_sink.emit(event)
            return event

        state = StreamState(request_id=request_id, model=request.model, include_usage=request.include_usage)
        upstream = UpstreamRequest(request.model, request.messages, max_tokens, outgoing_tp, request_id)
        attempts: list[list[str]] = []
        result = "no_backend"
        backend_name = None
        try:
            if self.config.early_commit:
                self._commit(client, state)
            for name in self.config.routes[request.model][: self.config.max_attempts]:
                backend_name = name
                result = self._pump(self.backends[name], upstream, client, state)
                attempts.append([name, result])
                if result in ("failed_before_first_byte", "first_byte_timeout") and state.data_frames == 0:
                    continue  # safe: nothing has been delivered yet
                if result == "failed_after_first_byte" and self.config.allow_retry_after_first_byte:
                    continue  # anti-pattern: replays output and double-bills
                break
            self._finalise(client, state, result)
        except IngressCut:
            result = "ingress_idle_cut"
            if not attempts or attempts[-1][1] != result:
                attempts.append([backend_name or "-", result])
        client.close(state.now)

        if state.usage and result == "completed":
            prompt, completion, source = state.usage.prompt_tokens, state.usage.completion_tokens, "upstream"
        else:
            prompt = _estimate_prompt_tokens(request.messages) if state.data_frames else 0
            completion, source = state.delivered_tokens, "estimated"
        self.ledger.settle(policy.caller_id, reservation, prompt + completion)
        if idempotency_key and state.data_frames:
            self.idempotency[(policy.caller_id, idempotency_key)] = request_id
        event.update(
            backend=attempts[-1][0] if attempts else None,
            attempts=attempts,
            status=client.status,
            outcome=result,
            prompt_tokens=prompt,
            completion_tokens=completion,
            usage_source=source,
        )
        self.usage_sink.emit(event)
        return event

    def _parse(self, body: dict[str, object]) -> ChatRequest:
        model = body.get("model")
        messages = body.get("messages")
        if not isinstance(model, str) or not model:
            raise GatewayError(400, "invalid_request", "model is required")
        if not isinstance(messages, list) or not messages:
            raise GatewayError(400, "invalid_request", "messages must be a non-empty list")
        raw_max = body.get("max_completion_tokens", body.get("max_tokens"))
        options = body.get("stream_options") or {}
        include_usage = bool(isinstance(options, dict) and options.get("include_usage"))
        return ChatRequest(model, tuple(messages), int(raw_max) if raw_max else None, include_usage)

    # -- streaming core -------------------------------------------------------
    def _commit(self, client: ClientConnection, st: StreamState) -> None:
        if not st.headers_committed:
            client.start_stream(SSE_HEADERS, st.now)
            st.headers_committed = True
            st.last_write = st.now

    def _write(self, client: ClientConnection, st: StreamState, frame: str) -> None:
        self._commit(client, st)
        client.write(frame, st.now)
        st.last_write = st.now
        if frame.startswith("data: "):
            st.data_frames += 1

    def _chunk(self, st: StreamState, delta: dict[str, object], finish: str | None = None) -> dict[str, object]:
        return {
            "id": f"chatcmpl-{st.request_id}",
            "object": "chat.completion.chunk",
            "created": st.now,
            "model": st.model,
            "choices": [{"index": 0, "delta": delta, "finish_reason": finish}],
        }

    def _idle(self, client: ClientConnection, st: StreamState, ticks: int, attempt_started: int | None) -> None:
        for _ in range(ticks):
            st.now += 1
            if attempt_started is not None and st.now - attempt_started > self.config.first_byte_deadline:
                raise FirstByteTimeout()
            if not client.connected:
                raise ClientGone()
            ka = self.config.keepalive_every
            if ka and st.headers_committed and st.now - st.last_write >= ka:
                self._write(client, st, ": keepalive\n\n")

    def _pump(self, backend: ModelBackend, req: UpstreamRequest, client: ClientConnection, st: StreamState) -> str:
        events = backend.stream(req)
        started = st.now
        first = False
        try:
            for ev in events:
                if isinstance(ev, Idle):
                    self._idle(client, st, ev.ticks, None if first else started)
                elif isinstance(ev, Delta):
                    st.now += 1
                    if not first:
                        first = True
                        self._write(client, st, _sse(self._chunk(st, {"role": "assistant", "content": ""})))
                    self._write(client, st, _sse(self._chunk(st, {"content": ev.text})))
                    st.delivered_tokens += ev.tokens
                elif isinstance(ev, Finish):
                    self._write(client, st, _sse(self._chunk(st, {}, ev.reason)))
                elif isinstance(ev, Usage):
                    st.usage = ev
            if st.include_usage and st.usage:
                usage = {
                    "prompt_tokens": st.usage.prompt_tokens,
                    "completion_tokens": st.usage.completion_tokens,
                    "total_tokens": st.usage.total,
                }
                payload = self._chunk(st, {})
                payload["choices"] = []
                payload["usage"] = usage
                self._write(client, st, _sse(payload))
            self._write(client, st, "data: [DONE]\n\n")
            return "completed"
        except BackendError:
            return "failed_after_first_byte" if first else "failed_before_first_byte"
        except FirstByteTimeout:
            events.close()
            return "first_byte_timeout"
        except ClientGone:
            events.close()
            return "client_cancelled"
        except IngressCut:
            events.close()
            raise

    def _finalise(self, client: ClientConnection, st: StreamState, result: str) -> None:
        if result in ("completed", "client_cancelled"):
            return
        err = GatewayError(502, "upstream_failed", "model backend failed", retryable=st.data_frames == 0)
        if result == "failed_after_first_byte":
            err = GatewayError(502, "upstream_failed_mid_stream", "model backend failed after streaming began")
        if st.headers_committed:
            # Status is already 200. Send an error frame and close WITHOUT [DONE].
            try:
                self._write(client, st, _sse(err.envelope(st.request_id)))
            except ClientGone:
                pass
        else:
            client.respond_json(err.status, err.envelope(st.request_id), st.now)


# ---------------------------------------------------------------------------
# Scenario helpers
# ---------------------------------------------------------------------------

DEFAULT_CALLER = CallerPolicy(
    caller_id="agent-contoso-assistant",
    display_name="Contoso assistant (hosted agent)",
    allowed_models=frozenset({"gpt-4o", "gpt-4o-mini"}),
    token_budget=500,
    max_tokens_cap=64,
    blocked_terms=("card number",),
)
SECOND_CALLER = CallerPolicy(
    caller_id="agent-contoso-reporting",
    display_name="Contoso reporting (hosted agent)",
    allowed_models=frozenset({"gpt-4o-mini"}),
    token_budget=500,
    max_tokens_cap=32,
)


def make_config(**overrides: object) -> GatewayConfig:
    base: dict[str, object] = dict(
        issuer=SIM_ISSUER,
        audiences=(SIM_AUDIENCE,),
        tenant_id=SIM_TENANT,
        required_role=SIM_APP_ROLE,
        callers=(DEFAULT_CALLER, SECOND_CALLER),
        routes={"gpt-4o": ("primary", "secondary"), "gpt-4o-mini": ("primary", "secondary")},
        ingress_idle_timeout=30,
        keepalive_every=10,
        first_byte_deadline=8,
        max_attempts=3,
    )
    base.update(overrides)
    return GatewayConfig(**base)  # type: ignore[arg-type]


def make_body(model: str = "gpt-4o", content: str = "Where is order 1042?", **extra: object) -> dict[str, object]:
    body: dict[str, object] = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "stream": True,
        "stream_options": {"include_usage": True},
    }
    body.update(extra)
    return body


def bearer(caller: str = DEFAULT_CALLER.caller_id, **kw: object) -> str:
    return "Bearer " + issue_token(azp=caller, **kw)  # type: ignore[arg-type]


@dataclass
class Rig:
    gateway: StreamingGateway
    primary: FakeBackend
    secondary: FakeBackend
    sink: MemoryUsageSink

    def call(self, client: ClientConnection | None = None, **kw: object) -> tuple[dict[str, object], ClientConnection]:
        client = client or ClientConnection(ingress_idle_timeout=self.gateway.config.ingress_idle_timeout)
        kw.setdefault("authorization", bearer())
        kw.setdefault("body", make_body())
        event = self.gateway.handle(client=client, **kw)  # type: ignore[arg-type]
        return event, client


def make_rig(primary: FakeBackend | None = None, secondary: FakeBackend | None = None, **config: object) -> Rig:
    p = primary or FakeBackend("primary")
    s = secondary or FakeBackend("secondary", deltas=("Order", " 1042", " shipped", "."))
    sink = MemoryUsageSink()
    gw = StreamingGateway(make_config(**config), {"primary": p, "secondary": s}, usage_sink=sink)
    return Rig(gw, p, s, sink)


class Check:
    def __init__(self) -> None:
        self.failures: list[str] = []

    def that(self, condition: bool, message: str) -> None:
        if not condition:
            self.failures.append(message)


# ---------------------------------------------------------------------------
# Self-test scenarios
# ---------------------------------------------------------------------------

def sc_happy_path(c: Check) -> None:
    rig = make_rig()
    event, client = rig.call()
    payloads = client.data_payloads()
    c.that(client.status == 200, f"status {client.status}")
    c.that(client.headers.get("content-type") == "text/event-stream", "content-type is not text/event-stream")
    c.that(payloads and payloads[0]["choices"][0]["delta"].get("role") == "assistant", "first chunk lacks role")
    c.that(client.content_text() == "Hello, world.", f"content {client.content_text()!r}")
    finish = [p for p in payloads if isinstance(p, dict) and p["choices"] and p["choices"][0]["finish_reason"]]
    c.that(len(finish) == 1 and finish[0]["choices"][0]["finish_reason"] == "stop", "missing finish chunk")
    usage = [p for p in payloads if isinstance(p, dict) and "usage" in p]
    c.that(len(usage) == 1 and usage[0]["choices"] == [], "usage chunk must have empty choices")
    c.that(payloads[-1] == "[DONE]", "stream must end with [DONE]")
    c.that(event["outcome"] == "completed" and event["usage_source"] == "upstream", f"event {event['outcome']}")


def sc_sse_framing(c: Check) -> None:
    rig = make_rig()
    _, client = rig.call()
    for _, frame in client.frames:
        c.that(frame.endswith("\n\n"), f"frame not terminated by blank line: {frame!r}")
        c.that(frame.startswith("data: ") or frame.startswith(":"), f"unexpected frame prefix: {frame!r}")
        c.that("\n" not in frame[:-2], "an SSE event must not contain a raw newline inside data")
    c.that(client.headers.get("cache-control") == "no-cache", "cache-control must be no-cache")
    c.that(client.headers.get("x-accel-buffering") == "no", "x-accel-buffering must be no")


def sc_usage_omitted_when_not_requested(c: Check) -> None:
    rig = make_rig()
    body = make_body()
    body.pop("stream_options")
    event, client = rig.call(body=body)
    c.that(not any(isinstance(p, dict) and "usage" in p for p in client.data_payloads()), "usage chunk leaked")
    c.that(event["usage_source"] == "upstream" and event["completion_tokens"] == 4, "gateway must still account usage")


def sc_auth_failures(c: Check) -> None:
    cases = {
        "missing_token": None,
        "invalid_token": "Bearer " + issue_token(azp=DEFAULT_CALLER.caller_id, secret=b"forged"),
        "invalid_audience": bearer(aud="api://some-other-api"),
        "invalid_issuer": bearer(iss="https://login.example.com/other/v2.0"),
        "invalid_tenant": bearer(tid="tenant-other"),
        "token_expired": bearer(exp_offset=-1),
        "missing_app_role": bearer(roles=()),
        "caller_not_allowed": bearer(caller="agent-unknown"),
    }
    for code, auth in cases.items():
        rig = make_rig()
        event, client = rig.call(authorization=auth)
        expected_status = 403 if code in ("missing_app_role", "caller_not_allowed") else 401
        c.that(client.status == expected_status, f"{code}: status {client.status}")
        c.that(event["outcome"] == code, f"{code}: outcome {event['outcome']}")
        c.that(rig.primary.calls == 0, f"{code}: backend must not be called")


def sc_appid_claim_accepted(c: Check) -> None:
    rig = make_rig()
    auth = "Bearer " + issue_token(azp=DEFAULT_CALLER.caller_id, use_appid_claim=True)
    event, client = rig.call(authorization=auth)
    c.that(client.status == 200 and event["caller_id"] == DEFAULT_CALLER.caller_id, "v1 appid claim not honoured")


def sc_model_not_allowed(c: Check) -> None:
    rig = make_rig()
    event, client = rig.call(authorization=bearer(SECOND_CALLER.caller_id), body=make_body("gpt-4o"))
    c.that(client.status == 403 and event["outcome"] == "model_not_allowed", f"got {client.status}")
    c.that(rig.primary.calls == 0, "backend must not be called on policy deny")
    body = client.json_body or {}
    c.that(isinstance(body.get("error"), dict) and body["error"].get("request_id"), "error envelope lacks request_id")


def sc_budget_exceeded(c: Check) -> None:
    rig = make_rig()
    statuses = []
    denied = None
    for _ in range(40):
        _, client = rig.call()
        statuses.append(client.status)
        if client.status == 429 and denied is None:
            denied = client
    c.that(statuses[0] == 200, "first call should pass")
    c.that(429 in statuses, f"budget never enforced: {statuses}")
    err = ((denied.json_body if denied else None) or {}).get("error", {})
    c.that(err.get("code") == "token_budget_exceeded" and err.get("retryable") is True,
           "a budget denial is retryable once the window moves on")
    first_429 = statuses.index(429) if 429 in statuses else len(statuses)
    c.that(all(s == 429 for s in statuses[first_429:]), "budget must stay exhausted within the window")
    used = rig.gateway.ledger.used[DEFAULT_CALLER.caller_id]
    c.that(used <= DEFAULT_CALLER.token_budget, f"ledger overspent: {used}")
    c.that(rig.gateway.ledger.reserved[DEFAULT_CALLER.caller_id] == 0, "reservations leaked")


def sc_max_tokens_clamped(c: Check) -> None:
    rig = make_rig()
    rig.call(body=make_body(max_completion_tokens=100000))
    c.that(rig.primary.seen_max_tokens == [DEFAULT_CALLER.max_tokens_cap], f"sent {rig.primary.seen_max_tokens}")


def sc_content_safety_block(c: Check) -> None:
    rig = make_rig()
    event, client = rig.call(body=make_body(content="Please save my card number for later"))
    c.that(client.status == 400 and event["outcome"] == "content_filtered", f"got {client.status}")
    c.that(rig.primary.calls == 0, "backend must not see blocked input")


def sc_failover_before_first_byte(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", fail_before_first_byte=True))
    event, client = rig.call()
    c.that(client.status == 200, f"status {client.status}")
    c.that(client.content_text() == "Order 1042 shipped.", f"content {client.content_text()!r}")
    c.that(event["attempts"] == [["primary", "failed_before_first_byte"], ["secondary", "completed"]], str(event["attempts"]))
    c.that(rig.primary.generated_tokens == 0, "primary must not bill anything")


def sc_first_byte_timeout_failover(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", first_byte_idle=20))
    event, client = rig.call()
    c.that(client.status == 200 and event["backend"] == "secondary", f"backend {event['backend']}")
    c.that(rig.primary.cancelled == 1, "slow upstream must be cancelled, not abandoned")
    c.that(event["attempts"][0] == ["primary", "first_byte_timeout"], str(event["attempts"]))


def sc_all_backends_fail_clean_json(c: Check) -> None:
    rig = make_rig(
        primary=FakeBackend("primary", fail_before_first_byte=True),
        secondary=FakeBackend("secondary", fail_before_first_byte=True),
    )
    event, client = rig.call()
    c.that(client.status == 502 and client.frames == [], "hold-headers mode must return a JSON 502, not SSE")
    err = (client.json_body or {}).get("error", {})
    c.that(isinstance(err, dict) and err.get("retryable") is True, "nothing delivered, so the client may retry")
    c.that(event["prompt_tokens"] == 0 and event["completion_tokens"] == 0, "nothing should be billed")


def sc_mid_stream_failure_no_retry(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", fail_after_deltas=2))
    event, client = rig.call()
    payloads = client.data_payloads()
    c.that(client.status == 200, "status already committed")
    c.that(rig.secondary.calls == 0, "must not fail over after the first data frame")
    c.that("[DONE]" not in payloads, "a truncated stream must not end with [DONE]")
    last = payloads[-1] if payloads else {}
    c.that(isinstance(last, dict) and last.get("error", {}).get("code") == "upstream_failed_mid_stream", "no error frame")
    c.that(isinstance(last, dict) and last["error"]["retryable"] is False, "mid-stream failure is not blindly retryable")
    c.that(event["outcome"] == "failed_after_first_byte" and event["usage_source"] == "estimated", str(event["outcome"]))


def sc_anti_pattern_retry_after_first_byte(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", fail_after_deltas=2), allow_retry_after_first_byte=True)
    _, client = rig.call()
    text = client.content_text()
    billed = rig.primary.generated_tokens + rig.secondary.generated_tokens
    c.that(text.startswith("Hello,Order"), f"expected spliced output to prove the harm, got {text!r}")
    c.that(billed > rig.secondary.generated_tokens, "replay should show double generation (billing)")


def sc_client_disconnect_cancels_upstream(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", gap_idle=2, deltas=tuple("abcdefgh")))
    client = ClientConnection(ingress_idle_timeout=30, disconnect_after_data_frames=3)
    event, client = rig.call(client=client)
    c.that(event["outcome"] == "client_cancelled", f"outcome {event['outcome']}")
    c.that(rig.primary.cancelled == 1, "upstream must be cancelled on disconnect")
    c.that(rig.primary.generated_tokens < 8, "upstream kept generating after the client left")
    c.that(event["completion_tokens"] == 2 and event["usage_source"] == "estimated",
           f"delivered tokens must still be attributed, got {event['completion_tokens']}")
    c.that(rig.secondary.calls == 0, "a cancelled request must not fail over")


def sc_keepalive_survives_idle_gaps(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", gap_idle=25))
    event, client = rig.call()
    c.that(event["outcome"] == "completed", f"outcome {event['outcome']}")
    c.that(client.keepalives() >= 3, f"expected keepalives, saw {client.keepalives()}")


def sc_no_keepalive_ingress_cuts(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", gap_idle=40), keepalive_every=0, first_byte_deadline=8)
    event, client = rig.call()
    c.that(event["outcome"] == "ingress_idle_cut", f"outcome {event['outcome']}")
    c.that(client.cut_reason == "ingress_idle_timeout", "ingress should have closed the idle stream")
    c.that("[DONE]" not in client.data_payloads(), "cut stream cannot have [DONE]")


def sc_early_commit_failover_still_allowed(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", first_byte_idle=6, fail_before_first_byte=True),
                   early_commit=True, keepalive_every=5, first_byte_deadline=20)
    event, client = rig.call()
    c.that(client.status == 200 and client.keepalives() >= 1, "early commit should send keepalives before content")
    c.that(event["backend"] == "secondary" and event["outcome"] == "completed", str(event["attempts"]))
    c.that(client.content_text() == "Order 1042 shipped.", "comments do not count as the first byte")


def sc_early_commit_exhausted_is_sse_error(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", fail_before_first_byte=True),
                   secondary=FakeBackend("secondary", fail_before_first_byte=True),
                   early_commit=True, keepalive_every=5, first_byte_deadline=20)
    _, client = rig.call()
    payloads = client.data_payloads()
    c.that(client.status == 200 and payloads and isinstance(payloads[-1], dict) and "error" in payloads[-1],
           "after headers are committed, failure must be an SSE error frame")
    c.that(payloads[-1]["error"]["retryable"] is True, "no content delivered, so retry is safe")


def sc_buffering_detector(c: Check) -> None:
    rig = make_rig(primary=FakeBackend("primary", gap_idle=2))
    _, streamed = rig.call()
    rig2 = make_rig(primary=FakeBackend("primary", gap_idle=2))
    _, buffered = rig2.call(client=ClientConnection(ingress_idle_timeout=30, buffering=True))
    c.that(not looks_buffered(streamed), "direct stream wrongly flagged as buffered")
    c.that(looks_buffered(buffered), "buffering proxy not detected")


def sc_idempotency(c: Check) -> None:
    rig = make_rig()
    _, first = rig.call(idempotency_key="turn-7")
    event, second = rig.call(idempotency_key="turn-7")
    c.that(first.status == 200 and second.status == 409, f"statuses {first.status}, {second.status}")
    c.that(rig.primary.calls == 1, "duplicate must not reach the backend")
    rig2 = make_rig(primary=FakeBackend("primary", fail_before_first_byte=True),
                    secondary=FakeBackend("secondary", fail_before_first_byte=True))
    rig2.call(idempotency_key="turn-8")
    rig2.primary.fail_before_first_byte = False
    _, retry = rig2.call(idempotency_key="turn-8")
    c.that(retry.status == 200, "a key whose first attempt delivered nothing may be retried")


def sc_traceparent(c: Check) -> None:
    rig = make_rig()
    incoming = "00-" + "ab" * 16 + "-" + "cd" * 8 + "-01"
    event, _ = rig.call(traceparent=incoming)
    sent = rig.primary.seen_traceparents[0]
    c.that(sent.split("-")[1] == "ab" * 16, "trace id must be preserved")
    c.that(sent.split("-")[2] != "cd" * 8, "gateway must create its own span id")
    c.that(event["trace_id"] == "ab" * 16, "usage event must carry the trace id")
    _, _ = rig.call(traceparent="garbage")
    c.that(len(rig.primary.seen_traceparents[1].split("-")[1]) == 32, "invalid traceparent must start a new trace")


def sc_usage_attribution_and_privacy(c: Check) -> None:
    rig = make_rig()
    secret_prompt = "ship to 221B Example Street"
    rig.call(body=make_body(content=secret_prompt))
    rig.call(authorization=bearer(SECOND_CALLER.caller_id), body=make_body("gpt-4o-mini"))
    callers = [e["caller_id"] for e in rig.sink.events]
    c.that(callers == [DEFAULT_CALLER.caller_id, SECOND_CALLER.caller_id], f"callers {callers}")
    ledger = rig.gateway.ledger.used
    c.that(ledger[DEFAULT_CALLER.caller_id] > 0 and ledger[SECOND_CALLER.caller_id] > 0, "usage not attributed")
    dumped = json.dumps(rig.sink.events)
    c.that(secret_prompt not in dumped and "Hello" not in dumped, "telemetry must not contain prompt or output text")


def sc_fail_closed_config(c: Check) -> None:
    bad = [
        dict(audiences=("",)),
        dict(tenant_id=""),
        dict(callers=()),
        dict(routes={"gpt-4o": ("primary",)}),  # gpt-4o-mini allowed but unrouted
        dict(keepalive_every=40),
        dict(first_byte_deadline=12),  # 12 x 3 attempts >= idle timeout in hold mode
    ]
    for overrides in bad:
        try:
            make_rig(**overrides)
            c.that(False, f"config accepted but should fail closed: {overrides}")
        except ConfigError:
            pass


def sc_production_stubs(c: Check) -> None:
    stubs: list[Callable[[], object]] = [
        lambda: EntraTokenValidator().validate("Bearer x"),
        lambda: FoundryChatBackend().stream(None),  # type: ignore[arg-type]
        lambda: KeyVaultBackendSecret().get(),
        lambda: AzureContentSafetyHook().check_input(()),
        lambda: AppInsightsUsageSink().emit({}),
        lambda: SharedTokenLedger().reserve(DEFAULT_CALLER, 1),
    ]
    for stub in stubs:
        try:
            stub()
            c.that(False, "production stub did not raise")
        except NotImplementedError:
            pass


SCENARIOS: list[tuple[str, Callable[[Check], None]]] = [
    ("happy_path_role_deltas_finish_usage_done", sc_happy_path),
    ("sse_framing_and_streaming_headers", sc_sse_framing),
    ("usage_chunk_only_when_requested", sc_usage_omitted_when_not_requested),
    ("auth_rejects_bad_tokens_fail_closed", sc_auth_failures),
    ("auth_accepts_v1_appid_claim", sc_appid_claim_accepted),
    ("policy_denies_model_not_allowed", sc_model_not_allowed),
    ("policy_enforces_token_budget", sc_budget_exceeded),
    ("policy_clamps_max_tokens", sc_max_tokens_clamped),
    ("content_safety_hook_blocks_input", sc_content_safety_block),
    ("failover_before_first_byte", sc_failover_before_first_byte),
    ("first_byte_deadline_cancels_and_fails_over", sc_first_byte_timeout_failover),
    ("all_backends_down_returns_json_502", sc_all_backends_fail_clean_json),
    ("mid_stream_failure_error_frame_no_retry", sc_mid_stream_failure_no_retry),
    ("anti_pattern_retry_after_first_byte_shows_harm", sc_anti_pattern_retry_after_first_byte),
    ("client_disconnect_cancels_upstream", sc_client_disconnect_cancels_upstream),
    ("keepalive_survives_idle_gaps", sc_keepalive_survives_idle_gaps),
    ("no_keepalive_ingress_cuts_stream", sc_no_keepalive_ingress_cuts),
    ("early_commit_keepalive_then_failover", sc_early_commit_failover_still_allowed),
    ("early_commit_exhausted_is_sse_error", sc_early_commit_exhausted_is_sse_error),
    ("buffering_detector", sc_buffering_detector),
    ("idempotency_key_blocks_double_billing", sc_idempotency),
    ("traceparent_propagated", sc_traceparent),
    ("usage_attributed_per_caller_without_content", sc_usage_attribution_and_privacy),
    ("config_fails_closed", sc_fail_closed_config),
    ("production_stubs_raise", sc_production_stubs),
]


def run_self_test() -> int:
    passed = 0
    for name, fn in SCENARIOS:
        check = Check()
        try:
            fn(check)
        except Exception as exc:  # noqa: BLE001 - report, do not crash the suite
            check.failures.append(f"raised {type(exc).__name__}: {exc}")
        if check.failures:
            print(f"FAIL {name}: {check.failures[0]}")
        else:
            passed += 1
            print(f"PASS {name}")
    print(f"{passed}/{len(SCENARIOS)} scenarios passed")
    return 0 if passed == len(SCENARIOS) else 1


# ---------------------------------------------------------------------------
# Domain packs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DomainRequest:
    label: str
    caller: str
    model: str
    prompt: str
    profile: str  # backend behaviour for this request
    expect_status: int
    expect_outcome: str
    disconnect_after: int | None = None


@dataclass(frozen=True)
class DomainPack:
    name: str
    summary: str
    callers: tuple[CallerPolicy, ...]
    routes: dict[str, tuple[str, ...]]
    early_commit: bool
    requests: tuple[DomainRequest, ...]


def _profile(profile: str) -> dict[str, FakeBackend]:
    healthy = dict(deltas=("Draft", " answer", " ready", "."))
    table: dict[str, dict[str, FakeBackend]] = {
        "healthy": {
            "foundry-primary": FakeBackend("foundry-primary", **healthy),
            "foundry-secondary": FakeBackend("foundry-secondary", **healthy),
            "byo-partner": FakeBackend("byo-partner", **healthy),
        },
        "primary-down": {
            "foundry-primary": FakeBackend("foundry-primary", fail_before_first_byte=True),
            "foundry-secondary": FakeBackend("foundry-secondary", **healthy),
            "byo-partner": FakeBackend("byo-partner", **healthy),
        },
        "primary-slow": {
            "foundry-primary": FakeBackend("foundry-primary", first_byte_idle=20),
            "foundry-secondary": FakeBackend("foundry-secondary", **healthy),
            "byo-partner": FakeBackend("byo-partner", **healthy),
        },
        "byo-slow-gaps": {
            "foundry-primary": FakeBackend("foundry-primary", **healthy),
            "foundry-secondary": FakeBackend("foundry-secondary", **healthy),
            "byo-partner": FakeBackend("byo-partner", first_byte_idle=12, gap_idle=18, **healthy),
        },
        "byo-mid-stream-fail": {
            "foundry-primary": FakeBackend("foundry-primary", **healthy),
            "foundry-secondary": FakeBackend("foundry-secondary", **healthy),
            "byo-partner": FakeBackend("byo-partner", fail_after_deltas=2, **healthy),
        },
        "byo-long": {
            "foundry-primary": FakeBackend("foundry-primary", **healthy),
            "foundry-secondary": FakeBackend("foundry-secondary", **healthy),
            "byo-partner": FakeBackend("byo-partner", gap_idle=3, deltas=tuple("abcdefghij")),
        },
    }
    return table[profile]


DOMAIN_PACKS: dict[str, DomainPack] = {
    "contoso-retail": DomainPack(
        name="contoso-retail",
        summary="Shopping assistant on two Foundry deployments with failover; payment data blocked at the gateway.",
        callers=(
            CallerPolicy("agent-contoso-shopper", "Contoso shopping assistant",
                         frozenset({"gpt-4o-mini", "gpt-4o"}), 400, 48, ("card number", "cvv")),
        ),
        routes={"gpt-4o-mini": ("foundry-primary", "foundry-secondary"),
                "gpt-4o": ("foundry-primary", "foundry-secondary")},
        early_commit=False,
        requests=(
            DomainRequest("order status", "agent-contoso-shopper", "gpt-4o-mini",
                          "Where is my order?", "healthy", 200, "completed"),
            DomainRequest("primary region down", "agent-contoso-shopper", "gpt-4o-mini",
                          "Recommend a rain jacket", "primary-down", 200, "completed"),
            DomainRequest("payment data in prompt", "agent-contoso-shopper", "gpt-4o",
                          "Store my card number please", "healthy", 400, "content_filtered"),
            DomainRequest("unlisted BYO model", "agent-contoso-shopper", "byo-chat",
                          "Use the partner model", "healthy", 403, "model_not_allowed"),
        ),
    ),
    "fabrikam-health": DomainPack(
        name="fabrikam-health",
        summary="Care-navigation agent restricted to Foundry deployments; no BYO backend for data-boundary reasons.",
        callers=(
            CallerPolicy("agent-fabrikam-care", "Fabrikam care navigator",
                         frozenset({"gpt-4o"}), 400, 48, ("medical record number",)),
        ),
        routes={"gpt-4o": ("foundry-primary", "foundry-secondary")},
        early_commit=False,
        requests=(
            DomainRequest("visit preparation summary", "agent-fabrikam-care", "gpt-4o",
                          "Summarise how to prepare for a clinic visit", "healthy", 200, "completed"),
            DomainRequest("primary slow to first token", "agent-fabrikam-care", "gpt-4o",
                          "List questions to ask a pharmacist", "primary-slow", 200, "completed"),
            DomainRequest("identifier in prompt", "agent-fabrikam-care", "gpt-4o",
                          "My medical record number is on file", "healthy", 400, "content_filtered"),
            DomainRequest("BYO model requested", "agent-fabrikam-care", "byo-chat",
                          "Use the partner model", "healthy", 403, "model_not_allowed"),
        ),
    ),
    "northwind-logistics": DomainPack(
        name="northwind-logistics",
        summary="Dispatch agent on a bring-your-own model with long pauses; early-commit mode with keepalives.",
        callers=(
            CallerPolicy("agent-northwind-dispatch", "Northwind dispatch planner",
                         frozenset({"byo-chat", "gpt-4o-mini"}), 70, 32),
        ),
        routes={"byo-chat": ("byo-partner",), "gpt-4o-mini": ("foundry-primary", "foundry-secondary")},
        early_commit=True,
        requests=(
            DomainRequest("route plan with long pauses", "agent-northwind-dispatch", "byo-chat",
                          "Plan tomorrow's delivery waves", "byo-slow-gaps", 200, "completed"),
            DomainRequest("BYO backend drops mid-stream", "agent-northwind-dispatch", "byo-chat",
                          "Re-plan after a road closure", "byo-mid-stream-fail", 200, "failed_after_first_byte"),
            DomainRequest("dispatcher closes the tab", "agent-northwind-dispatch", "byo-chat",
                          "Explain the full manifest", "byo-long", 200, "client_cancelled", disconnect_after=4),
            DomainRequest("budget spent", "agent-northwind-dispatch", "gpt-4o-mini",
                          "One more plan please", "healthy", 429, "token_budget_exceeded"),
        ),
    ),
}


def run_domain(name: str) -> int:
    pack = DOMAIN_PACKS[name]
    print(f"== {pack.name}: {pack.summary}")
    print(f"   mode: {'early-commit (headers + keepalives first)' if pack.early_commit else 'hold-headers'}")
    ledger = TokenLedger()
    sink = MemoryUsageSink()
    failures = 0
    for req in pack.requests:
        config = make_config(callers=pack.callers, routes=pack.routes, early_commit=pack.early_commit,
                             first_byte_deadline=8 if not pack.early_commit else 20)
        gateway = StreamingGateway(config, dict(_profile(req.profile)), ledger=ledger, usage_sink=sink)
        client = ClientConnection(ingress_idle_timeout=config.ingress_idle_timeout,
                                  disconnect_after_data_frames=req.disconnect_after)
        event = gateway.handle(authorization=bearer(req.caller), body=make_body(req.model, req.prompt), client=client)
        ok = client.status == req.expect_status and event["outcome"] == req.expect_outcome
        failures += 0 if ok else 1
        path = " -> ".join(f"{a[0]}:{a[1]}" for a in event["attempts"]) or "denied at gateway"
        print(f"   {'PASS' if ok else 'FAIL'} {req.label:<30} status={client.status} outcome={event['outcome']}"
              f" keepalives={client.keepalives()} path={path}")
    for caller in pack.callers:
        print(f"   ledger {caller.caller_id}: used {ledger.used.get(caller.caller_id, 0)}"
              f" of {caller.token_budget} tokens")
    return failures


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------

def _print_frames(client: ClientConnection) -> None:
    if client.json_body is not None:
        print(f"   HTTP {client.status} {json.dumps(client.json_body)}")
        return
    print(f"   HTTP {client.status} {client.headers}")
    for tick, frame in client.frames:
        print(f"   t={tick:<3} {frame.strip()}")


def run_demo() -> int:
    print("1) Happy path: role chunk, content deltas, finish chunk, usage chunk, [DONE].")
    rig = make_rig()
    event, client = rig.call(traceparent="00-" + "ab" * 16 + "-" + "cd" * 8 + "-01")
    _print_frames(client)
    print(f"   usage event: {json.dumps(event)}\n")

    print("2) Primary fails before the first byte: the gateway fails over invisibly.")
    rig = make_rig(primary=FakeBackend("primary", fail_before_first_byte=True))
    event, client = rig.call()
    print(f"   attempts: {event['attempts']}  content: {client.content_text()!r}\n")

    print("3) Primary fails mid-stream: error frame, no [DONE], no replay to the secondary.")
    rig = make_rig(primary=FakeBackend("primary", fail_after_deltas=2))
    event, client = rig.call()
    _print_frames(client)
    print(f"   secondary calls: {rig.secondary.calls}\n")

    print("4) Long pauses: keepalive comments hold the connection open through the ingress.")
    rig = make_rig(primary=FakeBackend("primary", gap_idle=25, deltas=("Plan", " ready")))
    event, client = rig.call()
    _print_frames(client)
    print()

    print("5) Policy deny: model not on the caller's allowlist; the backend is never called.")
    rig = make_rig()
    event, client = rig.call(authorization=bearer(SECOND_CALLER.caller_id), body=make_body("gpt-4o"))
    _print_frames(client)
    print(f"   backend calls: {rig.primary.calls}")
    return 0


# ---------------------------------------------------------------------------
# Production stubs (see ../src/gateway/app.py for the deployable version)
# ---------------------------------------------------------------------------

class EntraTokenValidator:
    """Validate an Entra access token: RS256 signature against the tenant JWKS,
    iss, aud, tid, exp/nbf, required app role, azp/appid allowlist."""

    def validate(self, authorization: str) -> Claims:
        raise NotImplementedError("Use PyJWT with the tenant JWKS; see src/gateway/app.py")


class FoundryChatBackend:
    """Stream Chat Completions from a Foundry deployment with a managed identity token."""

    name = "foundry"

    def stream(self, request: UpstreamRequest) -> Iterator[BackendEvent]:
        raise NotImplementedError("Use httpx streaming with a Microsoft Entra token; see src/gateway/app.py")


class KeyVaultBackendSecret:
    """Resolve a bring-your-own backend credential from Key Vault via managed identity."""

    def get(self) -> str:
        raise NotImplementedError("Use a Container Apps Key Vault secret reference; see infra/main.bicep")


class AzureContentSafetyHook:
    """Call a content-safety service for input screening."""

    def check_input(self, messages: tuple[dict[str, str], ...]) -> str | None:
        raise NotImplementedError("Call your content-safety service; see docs/architecture.md")


class AppInsightsUsageSink:
    """Emit the usage event through OpenTelemetry to Application Insights."""

    def emit(self, event: dict[str, object]) -> None:
        raise NotImplementedError("Log through azure-monitor-opentelemetry; see src/gateway/app.py")


class SharedTokenLedger:
    """Budget store shared by every gateway replica."""

    def reserve(self, policy: CallerPolicy, amount: int) -> None:
        raise NotImplementedError("Back the ledger with a shared store before scaling out")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--self-test", action="store_true", help="run named scenarios and exit non-zero on failure")
    group.add_argument("--domain", choices=[*DOMAIN_PACKS, "all"], help="run a fictional domain pack")
    group.add_argument("--demo", action="store_true", help="narrated walkthrough printing SSE frames")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.demo:
        return run_demo()
    names = list(DOMAIN_PACKS) if args.domain == "all" else [args.domain]
    failures = sum(run_domain(n) for n in names)
    print(f"domain run: {'all expectations met' if failures == 0 else f'{failures} expectation(s) failed'}")
    return 0 if failures == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
