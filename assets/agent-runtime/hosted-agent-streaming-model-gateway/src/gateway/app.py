"""Streaming model gateway: an OpenAI-compatible Chat Completions proxy.

ILLUSTRATIVE reference implementation. It mirrors the gateway core in
samples/streaming_gateway_sim.py, with real HTTP, Entra token validation and
managed identity in place of the simulator's fakes. Review, test and harden it
before production use. docs/architecture.md defines the contracts it implements
and docs/decisions/ records why.

Per request it:
1. Validates the caller's Entra access token: issuer, audience, tenant, app
   role, and the azp/appid allowlist taken from the policy.
2. Parses the request and enforces the caller's model allowlist, blocked terms,
   max-token cap and token budget.
3. Opens a streaming call to the first backend on the model alias's route that
   sends a data event. Failover happens only before the first data byte (ADR 0002).
4. Relays server-sent events one at a time with no buffering, sends keepalive
   comments while the backend is quiet, and closes the backend call as soon as
   the client goes away.
5. Settles the token budget and emits exactly one usage event.

Configuration comes only from environment variables (see src/README.md). Every
problem is collected at startup and the process refuses to start if there is
any, so a half-configured gateway never serves traffic.

Single replica as written: the token ledger and the idempotency store are in
memory. Several replicas need a shared store for both.

Foundry hosted agents and Foundry model gateway connections are observed in
preview; verify against current docs.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import os
import re
import secrets
import time
import uuid
from dataclasses import dataclass, field
from functools import partial
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping
from urllib.parse import urlsplit

import anyio
import httpx
import jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, Response, StreamingResponse
from starlette.routing import Route
from starlette.types import Receive, Scope, Send

log = logging.getLogger("gateway")
usage_log = logging.getLogger("gateway.usage")

FOUNDRY_SCOPE = "https://cognitiveservices.azure.com/.default"
KNOWN_KINDS = ("azure-openai", "openai-compatible")
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
PLACEHOLDER = re.compile(r"<[^<>]+>")
TRACEPARENT = re.compile(r"^[0-9a-f]{2}-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$")
KEEPALIVE_FRAME = b": keepalive\n\n"
DONE_FRAME = b"data: [DONE]\n\n"
SSE_HEADERS = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}

ERROR_TYPES = {
    400: "invalid_request_error",
    401: "authentication_error",
    403: "permission_error",
    409: "conflict_error",
    429: "rate_limit_error",
    502: "upstream_error",
    503: "service_unavailable",
}


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class GatewayError(Exception):
    """An error the client sees. Messages never echo tokens, prompts, URLs or backend text."""

    def __init__(self, status: int, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message
        self.retryable = retryable

    def envelope(self, request_id: str) -> dict[str, Any]:
        return {
            "error": {
                "message": self.message,
                "type": ERROR_TYPES.get(self.status, "gateway_error"),
                "code": self.code,
                "param": None,
                "request_id": request_id,
                "retryable": self.retryable,
            }
        }


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__(f"{len(problems)} configuration problem(s); see the log")
        self.problems = problems


class UpstreamStatus(Exception):
    """The backend answered with a non-200 status. Its body is never read or echoed."""

    def __init__(self, status: int) -> None:
        super().__init__(f"backend answered {status}")
        self.status = status

    @property
    def retryable(self) -> bool:
        # 400, 401, 403, 404 and 422 mean the request or the gateway's own setup is
        # wrong. Another backend would reject it the same way, so stop failing over.
        return self.status in (408, 429) or self.status >= 500


class UpstreamProtocolError(Exception):
    """The backend's stream ended or sent an error before its first data event."""


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Backend:
    name: str
    kind: str
    base_url: str
    upstream_model: str
    api_key: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class CallerPolicy:
    caller_id: str
    display_name: str
    allowed_models: frozenset[str]
    token_budget: int
    max_tokens_cap: int
    blocked_terms: tuple[str, ...]


@dataclass(frozen=True)
class Settings:
    tenant_id: str
    audiences: frozenset[str]
    required_role: str
    managed_identity_client_id: str | None
    keepalive_seconds: float
    first_byte_timeout_seconds: float
    max_attempts: int
    ingress_idle_timeout_seconds: float
    early_commit: bool
    budget_window_seconds: int
    callers: Mapping[str, CallerPolicy]
    routes: Mapping[str, tuple[Backend, ...]]


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Read and check all configuration. Collects every problem, then fails closed."""
    problems: list[str] = []

    def text(name: str) -> str:
        value = (environ.get(name) or "").strip()
        if not value:
            problems.append(f"{name} is not set")
        elif PLACEHOLDER.search(value):
            problems.append(f"{name} still contains a <placeholder>")
        return value

    def number(name: str, *, integer: bool = False, allow_zero: bool = False) -> Any:
        raw = (environ.get(name) or "").strip()
        if not raw:
            problems.append(f"{name} is not set; choose a value from your own measurements")
            return None
        try:
            value = int(raw) if integer else float(raw)
        except ValueError:
            problems.append(f"{name} must be {'an integer' if integer else 'a number'}")
            return None
        if not math.isfinite(value) or value < 0 or (value == 0 and not allow_zero):
            problems.append(f"{name} must be {'zero or more' if allow_zero else 'greater than zero'}")
            return None
        return value

    tenant_id = text("GATEWAY_TENANT_ID")
    audiences = frozenset(a.strip() for a in text("GATEWAY_AUDIENCES").split(",") if a.strip())
    required_role = text("GATEWAY_REQUIRED_ROLE")
    keepalive = number("GATEWAY_KEEPALIVE_SECONDS", allow_zero=True)
    first_byte = number("GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS")
    max_attempts = number("GATEWAY_MAX_ATTEMPTS", integer=True)
    idle = number("GATEWAY_INGRESS_IDLE_TIMEOUT_SECONDS")

    early_raw = (environ.get("GATEWAY_EARLY_COMMIT") or "").strip().lower()
    if early_raw not in ("", "true", "false"):
        problems.append("GATEWAY_EARLY_COMMIT must be true or false")
    early_commit = early_raw == "true"

    if keepalive and idle is not None and keepalive >= idle:
        problems.append(
            "GATEWAY_KEEPALIVE_SECONDS must be shorter than GATEWAY_INGRESS_IDLE_TIMEOUT_SECONDS"
        )
    if early_commit and keepalive == 0:
        problems.append(
            "GATEWAY_EARLY_COMMIT=true needs GATEWAY_KEEPALIVE_SECONDS above zero, otherwise "
            "the ingress idle timer runs while backends are tried"
        )
    if not early_commit and None not in (first_byte, max_attempts, idle) and first_byte * max_attempts >= idle:
        problems.append(
            "in hold mode, GATEWAY_FIRST_BYTE_TIMEOUT_SECONDS x GATEWAY_MAX_ATTEMPTS must be "
            "below GATEWAY_INGRESS_IDLE_TIMEOUT_SECONDS; lower them or set GATEWAY_EARLY_COMMIT=true"
        )

    budget_window = None
    callers: dict[str, CallerPolicy] = {}
    routes: dict[str, tuple[Backend, ...]] = {}
    policy = _load_policy(environ.get("GATEWAY_POLICY_JSON"), problems)
    if policy is not None:
        budget_window = _positive_int(
            policy.get("budget_window_seconds"), "policy.budget_window_seconds", problems
        )
        callers = _load_callers(policy.get("callers"), problems)
        routes = _load_routes(policy.get("routes"), callers, environ, problems)

    if problems:
        raise ConfigError(problems)
    return Settings(
        tenant_id=tenant_id,
        audiences=audiences,
        required_role=required_role,
        managed_identity_client_id=(environ.get("AZURE_CLIENT_ID") or "").strip() or None,
        keepalive_seconds=keepalive,
        first_byte_timeout_seconds=first_byte,
        max_attempts=max_attempts,
        ingress_idle_timeout_seconds=idle,
        early_commit=early_commit,
        budget_window_seconds=budget_window,
        callers=callers,
        routes=routes,
    )


def _load_policy(raw: str | None, problems: list[str]) -> dict[str, Any] | None:
    if not raw or not raw.strip():
        problems.append("GATEWAY_POLICY_JSON is not set")
        return None
    try:
        value = json.loads(raw)
    except ValueError:
        problems.append("GATEWAY_POLICY_JSON is not valid JSON")
        return None
    if not isinstance(value, dict):
        problems.append("GATEWAY_POLICY_JSON must be a JSON object")
        return None
    value = _without_comments(value)
    problems.extend(
        f"policy still contains a <placeholder> at {path}" for path in _placeholder_paths(value, "policy")
    )
    return value


def _without_comments(value: Any) -> Any:
    """Drop keys that start with an underscore, at every level."""
    if isinstance(value, dict):
        return {k: _without_comments(v) for k, v in value.items() if not k.startswith("_")}
    if isinstance(value, list):
        return [_without_comments(v) for v in value]
    return value


def _placeholder_paths(value: Any, path: str):
    if isinstance(value, dict):
        for key, item in value.items():
            if PLACEHOLDER.search(key):
                yield f"{path} (a key)"
            yield from _placeholder_paths(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _placeholder_paths(item, f"{path}[{index}]")
    elif isinstance(value, str) and PLACEHOLDER.search(value):
        yield path


def _positive_int(value: Any, where: str, problems: list[str]) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        if not (isinstance(value, str) and PLACEHOLDER.search(value)):  # already reported
            problems.append(f"{where} must be a positive integer")
        return None
    return value


def _load_callers(raw: Any, problems: list[str]) -> dict[str, CallerPolicy]:
    callers: dict[str, CallerPolicy] = {}
    if not isinstance(raw, dict) or not raw:
        problems.append("policy.callers must be a non-empty object keyed by caller client ID")
        return callers
    for caller_id, entry in raw.items():
        where = f"policy.callers.{caller_id}"
        if not isinstance(entry, dict):
            problems.append(f"{where} must be an object")
            continue
        models = entry.get("allowed_models")
        models_ok = (
            isinstance(models, list)
            and bool(models)
            and all(isinstance(m, str) and m.strip() for m in models)
        )
        if not models_ok:
            problems.append(f"{where}.allowed_models must be a non-empty list of model aliases")
        budget = _positive_int(entry.get("token_budget_per_window"), f"{where}.token_budget_per_window", problems)
        cap = _positive_int(entry.get("max_tokens_cap"), f"{where}.max_tokens_cap", problems)
        if budget is not None and cap is not None and cap > budget:
            problems.append(f"{where}.max_tokens_cap is larger than token_budget_per_window")
        terms = entry.get("blocked_terms", [])
        terms_ok = isinstance(terms, list) and all(isinstance(t, str) and t.strip() for t in terms)
        if not terms_ok:
            problems.append(f"{where}.blocked_terms must be a list of non-empty strings")
        if models_ok and terms_ok and budget is not None and cap is not None:
            callers[caller_id] = CallerPolicy(
                caller_id=caller_id,
                display_name=str(entry.get("display_name") or caller_id),
                allowed_models=frozenset(m.strip() for m in models),
                token_budget=budget,
                max_tokens_cap=cap,
                blocked_terms=tuple(t.strip().lower() for t in terms),
            )
    return callers


def _load_routes(
    raw: Any, callers: Mapping[str, CallerPolicy], environ: Mapping[str, str], problems: list[str]
) -> dict[str, tuple[Backend, ...]]:
    routes: dict[str, tuple[Backend, ...]] = {}
    if not isinstance(raw, dict):
        problems.append("policy.routes must be an object keyed by model alias")
        return routes
    referenced = sorted({alias for caller in callers.values() for alias in caller.allowed_models})
    for alias in referenced:
        entries = raw.get(alias)
        if not isinstance(entries, list) or not entries:
            problems.append(f"model alias '{alias}' is allowed for a caller but has no route")
            continue
        backends = []
        for index, entry in enumerate(entries):
            backend = _load_backend(entry, f"policy.routes.{alias}[{index}]", environ, problems)
            if backend is not None:
                backends.append(backend)
        routes[alias] = tuple(backends)
    for alias in sorted(set(raw) - set(referenced)):
        log.info("route '%s' is not allowed for any caller; ignored", alias)
    return routes


def _load_backend(entry: Any, where: str, environ: Mapping[str, str], problems: list[str]) -> Backend | None:
    if not isinstance(entry, dict):
        problems.append(f"{where} must be an object")
        return None
    before = len(problems)
    name = entry.get("name")
    kind = entry.get("kind")
    upstream_model = entry.get("upstream_model")
    url_env = entry.get("base_url_env")
    key_env = entry.get("api_key_env")
    if not isinstance(name, str) or not name.strip():
        problems.append(f"{where}.name is required")
    if kind not in KNOWN_KINDS:
        problems.append(f"{where}.kind must be one of {', '.join(KNOWN_KINDS)}")
    if not isinstance(upstream_model, str) or not upstream_model.strip():
        problems.append(f"{where}.upstream_model is required")
    base_url = ""
    if not isinstance(url_env, str) or not url_env.strip():
        problems.append(f"{where}.base_url_env must name an environment variable")
    else:
        base_url = (environ.get(url_env) or "").strip()
        if not base_url:
            problems.append(f"{where}.base_url_env names {url_env}, which is not set")
        elif not _acceptable_url(base_url):
            problems.append(f"{url_env} must be an https URL (plain http only for loopback testing)")
    api_key = None
    if kind == "azure-openai" and key_env is not None:
        problems.append(f"{where} is azure-openai, which uses the gateway's managed identity; remove api_key_env")
    if kind == "openai-compatible":
        if not isinstance(key_env, str) or not key_env.strip():
            problems.append(f"{where} is openai-compatible and needs api_key_env (a Key Vault-backed secret)")
        else:
            api_key = environ.get(key_env) or ""
            if not api_key.strip():
                problems.append(f"{where}.api_key_env names {key_env}, which is not set")
    if len(problems) != before or PLACEHOLDER.search(str(upstream_model)):
        return None
    return Backend(name=name, kind=kind, base_url=base_url, upstream_model=upstream_model, api_key=api_key)


def _acceptable_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if not parts.hostname:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS)


# ---------------------------------------------------------------------------
# Request parsing and helpers
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ChatRequest:
    model: str
    messages: list[dict[str, Any]]
    max_tokens: int | None
    uses_completion_field: bool
    include_usage: bool


def parse_chat_request(body: Any) -> ChatRequest:
    if not isinstance(body, dict):
        raise GatewayError(400, "invalid_request", "request body must be a JSON object")
    model = body.get("model")
    if not isinstance(model, str) or not model.strip():
        raise GatewayError(400, "invalid_request", "model is required")
    messages = body.get("messages")
    if not isinstance(messages, list) or not messages or not all(isinstance(m, dict) for m in messages):
        raise GatewayError(400, "invalid_request", "messages must be a non-empty list of objects")
    if body.get("stream") is not True:
        raise GatewayError(400, "invalid_request", "this gateway serves streaming requests only; set stream to true")
    uses_completion_field = body.get("max_completion_tokens") is not None
    raw_max = body.get("max_completion_tokens") if uses_completion_field else body.get("max_tokens")
    if raw_max is not None and (isinstance(raw_max, bool) or not isinstance(raw_max, int) or raw_max <= 0):
        raise GatewayError(400, "invalid_request", "max_completion_tokens or max_tokens must be a positive integer")
    options = body.get("stream_options")
    include_usage = isinstance(options, dict) and options.get("include_usage") is True
    return ChatRequest(model, messages, raw_max, uses_completion_field, include_usage)


def message_text(message: Mapping[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return " ".join(
            part["text"] for part in content if isinstance(part, dict) and isinstance(part.get("text"), str)
        )
    return ""


def estimate_tokens(text: str) -> int:
    """Rough estimate used only when the backend did not report usage."""
    return len(text) // 4 + 1


def estimate_prompt_tokens(messages: list[dict[str, Any]]) -> int:
    return sum(estimate_tokens(message_text(m)) for m in messages)


def child_traceparent(incoming: str | None) -> tuple[str, str]:
    """Return (trace_id, outgoing traceparent). Keeps a valid incoming trace, else starts one."""
    match = TRACEPARENT.match(incoming or "")
    if match and not incoming.startswith("ff") and int(match.group(1), 16) and int(match.group(2), 16):
        trace_id, flags = match.group(1), match.group(3)
    else:
        trace_id, flags = secrets.token_hex(16), "01"
    return trace_id, f"00-{trace_id}-{secrets.token_hex(8)}-{flags}"


def error_response(err: GatewayError, request_id: str) -> JSONResponse:
    headers = {"X-Request-Id": request_id}
    if err.status == 401:
        headers["WWW-Authenticate"] = "Bearer"
    return JSONResponse(err.envelope(request_id), status_code=err.status, headers=headers)


def sse_frame(payload: Mapping[str, Any]) -> bytes:
    return b"data: " + json.dumps(payload, separators=(",", ":")).encode() + b"\n\n"


async def next_data(lines: AsyncIterator[str]) -> str | None:
    """Return the next SSE data payload, skipping comments, event names and blank lines.

    Assumes one `data:` line per event, which is what OpenAI-compatible backends send.
    """
    async for line in lines:
        if line.startswith("data:"):
            return line[5:].strip()
    return None


def _count(value: Any) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Caller:
    caller_id: str
    tenant_id: str
    policy: CallerPolicy


class TokenValidator:
    """Validates Entra access tokens. Identity comes from the token, never from headers."""

    def __init__(self, settings: Settings) -> None:
        tenant = settings.tenant_id
        self._tenant_id = tenant
        self._audiences = settings.audiences
        self._role = settings.required_role
        self._callers = settings.callers
        self._issuers = frozenset({
            f"https://sts.windows.net/{tenant}/",
            f"https://login.microsoftonline.com/{tenant}/v2.0",
        })
        self._jwks = jwt.PyJWKClient(f"https://login.microsoftonline.com/{tenant}/discovery/v2.0/keys")

    async def validate(self, authorization: str | None) -> Caller:
        scheme, _, token = (authorization or "").partition(" ")
        token = token.strip()
        if scheme.lower() != "bearer" or not token:
            raise GatewayError(401, "missing_token", "bearer token required")
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError:
            raise GatewayError(401, "invalid_token", "malformed token") from None
        if header.get("alg") != "RS256":
            raise GatewayError(401, "invalid_token", "unsupported token algorithm")
        try:
            signing_key = await asyncio.to_thread(self._jwks.get_signing_key_from_jwt, token)
        except (jwt.PyJWTError, OSError) as exc:
            log.warning("token signing key not resolved (%s)", type(exc).__name__)
            raise GatewayError(401, "invalid_token", "token signing key not recognised") from None
        try:
            claims = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                # Issuer and audience are checked below so each failure has its own error code.
                options={"verify_aud": False, "verify_iss": False, "require": ["exp", "iss", "aud"]},
            )
        except (jwt.ExpiredSignatureError, jwt.ImmatureSignatureError):
            raise GatewayError(401, "token_expired", "token outside its validity window") from None
        except jwt.PyJWTError:
            raise GatewayError(401, "invalid_token", "token signature or claims are invalid") from None
        if claims.get("iss") not in self._issuers:
            raise GatewayError(401, "invalid_issuer", "issuer not trusted")
        audience = claims.get("aud")
        if not isinstance(audience, str) or audience not in self._audiences:
            raise GatewayError(401, "invalid_audience", "token audience does not match this gateway")
        if claims.get("tid") != self._tenant_id:
            raise GatewayError(401, "invalid_tenant", "token tenant not trusted")
        roles = claims.get("roles")
        if not isinstance(roles, list) or self._role not in roles:
            raise GatewayError(403, "missing_app_role", "caller lacks the gateway app role")
        caller_id = claims.get("azp") or claims.get("appid")
        policy = self._callers.get(caller_id) if isinstance(caller_id, str) else None
        if policy is None:
            raise GatewayError(403, "caller_not_allowed", "caller is not on the gateway allowlist")
        return Caller(caller_id=caller_id, tenant_id=self._tenant_id, policy=policy)


# ---------------------------------------------------------------------------
# Budgets and idempotency (in memory: one replica only)
# ---------------------------------------------------------------------------

class TokenLedger:
    """Per-caller token budget. Reserve before the call, settle on actual usage.

    In memory and per replica. Several replicas need a shared store, otherwise each
    replica grants the full budget.
    """

    def __init__(self, window_seconds: int) -> None:
        self._window = window_seconds
        self._window_start = time.monotonic()
        self.used: dict[str, int] = {}
        self.reserved: dict[str, int] = {}

    def _roll(self) -> None:
        now = time.monotonic()
        if now - self._window_start >= self._window:
            self._window_start = now
            self.used.clear()  # reservations belong to in-flight calls and carry over

    def reserve(self, policy: CallerPolicy, amount: int) -> None:
        self._roll()
        used = self.used.get(policy.caller_id, 0)
        held = self.reserved.get(policy.caller_id, 0)
        if used + held + amount > policy.token_budget:
            raise GatewayError(
                429, "token_budget_exceeded", "caller token budget exhausted for this window", retryable=True
            )
        self.reserved[policy.caller_id] = held + amount

    def settle(self, caller_id: str, reserved: int, actual: int) -> None:
        self._roll()
        self.reserved[caller_id] = self.reserved.get(caller_id, 0) - reserved
        self.used[caller_id] = self.used.get(caller_id, 0) + actual


class IdempotencyStore:
    """Remembers keys that produced output, plus keys still in flight, per caller."""

    def __init__(self, retention_seconds: int) -> None:
        self._retention = retention_seconds
        self._completed: dict[str, float] = {}
        self._in_flight: set[str] = set()

    def check(self, key: str) -> None:
        now = time.monotonic()
        self._completed = {k: t for k, t in self._completed.items() if now - t < self._retention}
        if key in self._completed or key in self._in_flight:
            raise GatewayError(409, "duplicate_request", "this idempotency key already produced output")

    def begin(self, key: str) -> None:
        self._in_flight.add(key)

    def finish(self, key: str, *, produced_output: bool) -> None:
        self._in_flight.discard(key)
        if produced_output:
            self._completed[key] = time.monotonic()


# ---------------------------------------------------------------------------
# Streaming
# ---------------------------------------------------------------------------

@dataclass
class Exchange:
    """Per-request state shared by the handler, the stream and the finaliser."""

    request_id: str
    trace_id: str
    traceparent: str
    model: Any = None
    caller_id: str | None = None
    tenant_id: str | None = None
    request: ChatRequest | None = None
    body: dict[str, Any] = field(default_factory=dict)
    max_tokens: int = 0
    prompt_estimate: int = 0
    reservation: int = 0
    idempotency_key: str | None = None
    attempts: list[list[Any]] = field(default_factory=list)
    connect_failure: str = "no_backend"
    rejected: bool = False
    upstream: httpx.Response | None = None
    lines: Any = None
    first_chunk: dict[str, Any] | None = None
    tasks: set[asyncio.Task[Any]] = field(default_factory=set)
    result: str | None = None
    status: int = 200
    data_frames: int = 0
    delivered_tokens: int = 0
    usage: dict[str, Any] | None = None
    saw_finish: bool = False
    closed: bool = False


class GuardedStream(StreamingResponse):
    """A StreamingResponse that always watches for client disconnect and always cleans up.

    Starlette watches receive() for disconnects only on servers reporting ASGI spec
    below 2.4. On newer servers it relies on send() raising, and some servers drop
    writes to a closed connection silently, which would keep the backend call (and
    its billing) running. This class watches receive() on every server, cancels the
    stream when the client goes away, and runs the finaliser even if the body
    iterator never started.
    """

    def __init__(self, content: AsyncIterator[bytes], *, on_close: Callable[[], Awaitable[None]], **kwargs: Any) -> None:
        super().__init__(content, **kwargs)
        self._on_close = on_close

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        streaming = asyncio.create_task(self.stream_response(send))
        watching = asyncio.create_task(self.listen_for_disconnect(receive))
        try:
            await asyncio.wait({streaming, watching}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            watching.cancel()
            streaming.cancel()
            with anyio.CancelScope(shield=True):
                await asyncio.gather(streaming, watching, return_exceptions=True)
                with contextlib.suppress(Exception):
                    await self.body_iterator.aclose()
                await self._on_close()
        if not streaming.cancelled():
            exc = streaming.exception()
            if exc is not None and not isinstance(exc, OSError):  # OSError: client went away mid-write
                raise exc


class Gateway:
    def __init__(
        self,
        settings: Settings,
        *,
        http_client: httpx.AsyncClient,
        validator: TokenValidator,
        credential: Any = None,
    ) -> None:
        self.settings = settings
        self.http = http_client
        self.validator = validator
        self.credential = credential
        self.ledger = TokenLedger(settings.budget_window_seconds)
        self.idempotency = IdempotencyStore(settings.budget_window_seconds)

    # -- endpoints -------------------------------------------------------------
    async def list_models(self, request: Request) -> Response:
        request_id = "req-" + uuid.uuid4().hex
        try:
            caller = await self.validator.validate(request.headers.get("authorization"))
        except GatewayError as err:
            return error_response(err, request_id)
        data = [{"id": alias, "object": "model", "owned_by": "gateway"} for alias in sorted(caller.policy.allowed_models)]
        return JSONResponse({"object": "list", "data": data}, headers={"X-Request-Id": request_id})

    async def chat_completions(self, request: Request) -> Response:
        trace_id, traceparent = child_traceparent(request.headers.get("traceparent"))
        ex = Exchange(request_id="req-" + uuid.uuid4().hex, trace_id=trace_id, traceparent=traceparent)
        correlation = request.headers.get("x-ms-client-request-id")
        if correlation:
            # Correlation only. Never use forwarded headers to decide policy.
            log.debug("request %s received", ex.request_id, extra={"client_request_id": correlation})
        try:
            caller = await self.validator.validate(request.headers.get("authorization"))
            ex.caller_id, ex.tenant_id = caller.caller_id, caller.tenant_id
            body = await _read_json(request)
            ex.model = body.get("model") if isinstance(body, dict) else None
            chat = parse_chat_request(body)
            policy = caller.policy
            key = request.headers.get("idempotency-key")
            scoped_key = f"{caller.caller_id}:{key}" if key else None
            if scoped_key:
                self.idempotency.check(scoped_key)
            if chat.model not in policy.allowed_models:
                raise GatewayError(403, "model_not_allowed", f"model {chat.model!r} is not allowed for this caller")
            if policy.blocked_terms and _has_blocked_term(chat.messages, policy.blocked_terms):
                raise GatewayError(400, "content_filtered", "input blocked by the content-safety hook")
            ex.max_tokens = min(chat.max_tokens or policy.max_tokens_cap, policy.max_tokens_cap)
            ex.prompt_estimate = estimate_prompt_tokens(chat.messages)
            reservation = ex.prompt_estimate + ex.max_tokens
            self.ledger.reserve(policy, reservation)
        except GatewayError as err:
            self._emit(ex, status=err.status, outcome=err.code)
            return error_response(err, ex.request_id)

        # No await between the idempotency check above and here, so this is race-free.
        ex.reservation, ex.request, ex.body = reservation, chat, body
        if scoped_key:
            self.idempotency.begin(scoped_key)
            ex.idempotency_key = scoped_key

        if not self.settings.early_commit:
            # Hold mode: no status is sent until a backend produces its first data event,
            # so an all-backends failure is still a real 502.
            try:
                connected = await self._connect(ex)
            except BaseException:
                await self._close(ex)
                raise
            if not connected:
                ex.result, ex.status = ex.connect_failure, 502
                await self._close(ex)
                err = GatewayError(502, "upstream_failed", "model backend failed", retryable=not ex.rejected)
                return error_response(err, ex.request_id)

        return GuardedStream(
            self._stream(ex),
            on_close=partial(self._close, ex),
            media_type="text/event-stream",
            headers={**SSE_HEADERS, "X-Request-Id": ex.request_id},
        )

    # -- backends ----------------------------------------------------------------
    async def _authorization(self, backend: Backend) -> str:
        if backend.kind == "azure-openai":
            # The gateway's own managed identity. The caller's token is never forwarded.
            token = await asyncio.to_thread(self.credential.get_token, FOUNDRY_SCOPE)
            return f"Bearer {token.token}"
        return f"Bearer {backend.api_key}"

    def _upstream_body(self, backend: Backend, ex: Exchange) -> dict[str, Any]:
        body = dict(ex.body)
        body["model"] = backend.upstream_model
        body["stream"] = True
        options = body.get("stream_options")
        # Always ask for usage so budgets settle on real numbers; the client only sees
        # the usage chunk if it asked for it.
        body["stream_options"] = {**(options if isinstance(options, dict) else {}), "include_usage": True}
        body.pop("max_tokens", None)
        body.pop("max_completion_tokens", None)
        use_completion_field = ex.request.uses_completion_field or backend.kind == "azure-openai"
        body["max_completion_tokens" if use_completion_field else "max_tokens"] = ex.max_tokens
        return body

    async def _open(self, backend: Backend, ex: Exchange) -> tuple[httpx.Response, Any, dict[str, Any]]:
        request = self.http.build_request(
            "POST",
            backend.base_url.rstrip("/") + "/chat/completions",
            json=self._upstream_body(backend, ex),
            headers={
                "Authorization": await self._authorization(backend),
                "Accept": "text/event-stream",
                "Accept-Encoding": "identity",
                # If you enable OpenTelemetry httpx instrumentation, let it own traceparent.
                "traceparent": ex.traceparent,
            },
        )
        response = await self.http.send(request, stream=True)
        lines = None
        try:
            if response.status_code != 200:
                raise UpstreamStatus(response.status_code)
            lines = response.aiter_lines()
            payload = await next_data(lines)
            if payload is None or payload == "[DONE]":
                raise UpstreamProtocolError("stream ended before the first data event")
            chunk = json.loads(payload)
            if not isinstance(chunk, dict) or "error" in chunk:
                raise UpstreamProtocolError("backend sent an error before the first data event")
            return response, lines, chunk
        except BaseException:
            if lines is not None:
                with contextlib.suppress(Exception):
                    await lines.aclose()
            await response.aclose()
            raise

    async def _connect(self, ex: Exchange) -> bool:
        """Try backends in route order until one sends its first data event (ADR 0002)."""
        for backend in self.settings.routes[ex.request.model][: self.settings.max_attempts]:
            attempt: list[Any] = [backend.name, None]  # outcome is filled in at close
            ex.attempts.append(attempt)
            try:
                # The deadline covers token acquisition, connect and the first data event.
                # On expiry _open closes its own response, so nothing leaks.
                async with asyncio.timeout(self.settings.first_byte_timeout_seconds):
                    ex.upstream, ex.lines, ex.first_chunk = await self._open(backend, ex)
                return True
            except (TimeoutError, httpx.TimeoutException):
                attempt[1] = "first_byte_timeout"
            except UpstreamStatus as exc:
                attempt[1] = "failed_before_first_byte"
                log.warning("request %s: backend %s answered %s", ex.request_id, backend.name, exc.status)
                if not exc.retryable:
                    ex.rejected = True
                    ex.connect_failure = attempt[1]
                    return False
            except Exception as exc:
                attempt[1] = "failed_before_first_byte"
                log.warning(
                    "request %s: backend %s failed before the first byte (%s)",
                    ex.request_id, backend.name, type(exc).__name__,
                )
            ex.connect_failure = attempt[1]
        return False

    # -- relay -------------------------------------------------------------------
    async def _stream(self, ex: Exchange) -> AsyncIterator[bytes]:
        keepalive = self.settings.keepalive_seconds
        try:
            if ex.upstream is None:
                # Early-commit mode: headers are already sent, so keep the connection
                # alive while backends are tried.
                connecting = asyncio.create_task(self._connect(ex))
                ex.tasks.add(connecting)
                while not connecting.done():
                    done, _ = await asyncio.wait({connecting}, timeout=keepalive)
                    if not done:
                        yield KEEPALIVE_FRAME
                ex.tasks.discard(connecting)
                if not connecting.result():
                    ex.result = ex.connect_failure
                    err = GatewayError(502, "upstream_failed", "model backend failed", retryable=not ex.rejected)
                    yield sse_frame(err.envelope(ex.request_id))
                    return

            chunk, ex.first_chunk = ex.first_chunk, None
            while True:
                if chunk is None:
                    reading = asyncio.create_task(next_data(ex.lines))
                    ex.tasks.add(reading)
                    try:
                        while keepalive and not reading.done():
                            done, _ = await asyncio.wait({reading}, timeout=keepalive)
                            if not done:
                                # Comment frame: resets idle timers on every hop. The read
                                # is not cancelled; the client write also detects disconnects.
                                yield KEEPALIVE_FRAME
                        payload = await reading
                    except (httpx.HTTPError, OSError) as exc:
                        # Includes the upstream read timeout, so a silent backend cannot be
                        # held open forever by keepalives.
                        log.warning("request %s: backend stream broke (%s)", ex.request_id, type(exc).__name__)
                        yield self._mid_stream_error(ex)
                        return
                    finally:
                        if reading.done():
                            ex.tasks.discard(reading)  # otherwise _close cancels it
                    if payload is None:
                        if ex.saw_finish:
                            ex.result = "completed"
                            yield DONE_FRAME
                        else:
                            yield self._mid_stream_error(ex)
                        return
                    if payload == "[DONE]":
                        ex.result = "completed"
                        yield DONE_FRAME
                        return
                    try:
                        chunk = json.loads(payload)
                    except ValueError:
                        chunk = None
                    if not isinstance(chunk, dict) or "error" in chunk:
                        yield self._mid_stream_error(ex)
                        return
                frame = self._translate(ex, chunk)
                chunk = None
                if frame is not None:
                    ex.data_frames += 1
                    yield frame
        finally:
            await self._close(ex)

    def _mid_stream_error(self, ex: Exchange) -> bytes:
        # Status is already 200: send the error as a frame and end WITHOUT [DONE].
        ex.result = "failed_after_first_byte"
        err = GatewayError(502, "upstream_failed_mid_stream", "model backend failed after streaming began")
        return sse_frame(err.envelope(ex.request_id))

    def _translate(self, ex: Exchange, chunk: dict[str, Any]) -> bytes | None:
        choices = chunk.get("choices")
        usage = chunk.get("usage")
        if isinstance(usage, dict):
            ex.usage = usage
        if not ex.request.include_usage:
            if isinstance(usage, dict) and not choices:
                return None  # usage-only chunk the client did not ask for
            chunk.pop("usage", None)
        for choice in choices if isinstance(choices, list) else []:
            if not isinstance(choice, dict):
                continue
            if choice.get("finish_reason"):
                ex.saw_finish = True
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            content = delta.get("content")
            if isinstance(content, str) and content:
                ex.delivered_tokens += estimate_tokens(content)
            for call in delta.get("tool_calls") or []:
                arguments = (call.get("function") or {}).get("arguments") if isinstance(call, dict) else None
                if isinstance(arguments, str) and arguments:
                    ex.delivered_tokens += estimate_tokens(arguments)
        if "model" in chunk:
            chunk["model"] = ex.request.model  # the alias, never the backend deployment name
        return sse_frame(chunk)

    # -- finalise ----------------------------------------------------------------
    async def _close(self, ex: Exchange) -> None:
        """Idempotent. Stops the backend call, settles the budget, emits the usage event."""
        if ex.closed:
            return
        ex.closed = True
        with anyio.CancelScope(shield=True):
            for task in list(ex.tasks):
                task.cancel()
            for task in list(ex.tasks):
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
            ex.tasks.clear()
            if ex.lines is not None:
                with contextlib.suppress(Exception):
                    await ex.lines.aclose()
            if ex.upstream is not None:
                with contextlib.suppress(Exception):
                    await ex.upstream.aclose()
        outcome = ex.result or "client_cancelled"
        if ex.attempts and ex.attempts[-1][1] is None:
            ex.attempts[-1][1] = outcome
        if ex.result == "completed" and ex.usage is not None:
            prompt, completion = _count(ex.usage.get("prompt_tokens")), _count(ex.usage.get("completion_tokens"))
            source = "upstream"
        else:
            prompt = ex.prompt_estimate if ex.data_frames else 0
            completion, source = ex.delivered_tokens, "estimated"
        self.ledger.settle(ex.caller_id, ex.reservation, prompt + completion)
        if ex.idempotency_key:
            self.idempotency.finish(ex.idempotency_key, produced_output=ex.data_frames > 0)
        self._emit(ex, status=ex.status, outcome=outcome, prompt=prompt, completion=completion, source=source)

    def _emit(
        self, ex: Exchange, *, status: int, outcome: str, prompt: int = 0, completion: int = 0, source: str = "none"
    ) -> None:
        """One usage event per request, including denials. Never contains prompt or completion text."""
        event = {
            "event": "gateway.usage",
            "request_id": ex.request_id,
            "trace_id": ex.trace_id,
            "caller_id": ex.caller_id,
            "tenant_id": ex.tenant_id,
            "model": ex.model if isinstance(ex.model, str) else None,
            "backend": ex.attempts[-1][0] if ex.attempts else None,
            "attempts": ex.attempts,
            "status": status,
            "outcome": outcome,
            "prompt_tokens": prompt,
            "completion_tokens": completion,
            "usage_source": source,
        }
        usage_log.info(json.dumps(event, separators=(",", ":")))


async def _read_json(request: Request) -> Any:
    try:
        return json.loads(await request.body())
    except ValueError:
        raise GatewayError(400, "invalid_request", "request body must be JSON") from None


def _has_blocked_term(messages: list[dict[str, Any]], terms: tuple[str, ...]) -> bool:
    text = " ".join(message_text(m) for m in messages).lower()
    return any(term in text for term in terms)


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------

def configure_logging() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    # httpx logs every request URL at INFO and azure-identity logs token activity.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("azure").setLevel(logging.WARNING)
    if os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        try:
            from azure.monitor.opentelemetry import configure_azure_monitor
        except ImportError:
            log.warning("APPLICATIONINSIGHTS_CONNECTION_STRING is set but azure-monitor-opentelemetry is not installed")
            return
        configure_azure_monitor(logger_name="gateway")


def build_credential(settings: Settings) -> Any:
    if not any(b.kind == "azure-openai" for backends in settings.routes.values() for b in backends):
        return None
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential

    if settings.managed_identity_client_id:
        # Container Apps exposes managed identity through IDENTITY_ENDPOINT. With a
        # user-assigned identity the client ID selects which identity to use.
        return ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    log.warning("AZURE_CLIENT_ID is not set; using DefaultAzureCredential (local development only)")
    return DefaultAzureCredential()


@contextlib.asynccontextmanager
async def lifespan(app: Starlette) -> AsyncIterator[None]:
    try:
        settings = load_settings(os.environ)
    except ConfigError as err:
        for problem in err.problems:
            log.error("configuration problem: %s", problem)
        raise
    credential = build_credential(settings)
    # One client for all backends. Its timeout reuses the first-byte deadline, which
    # also bounds how long a backend may stay silent mid-stream.
    async with httpx.AsyncClient(timeout=httpx.Timeout(settings.first_byte_timeout_seconds)) as client:
        app.state.gateway = Gateway(
            settings, http_client=client, validator=TokenValidator(settings), credential=credential
        )
        try:
            yield
        finally:
            app.state.gateway = None
            if credential is not None and hasattr(credential, "close"):
                credential.close()


def _gateway(request: Request) -> Gateway | None:
    return getattr(request.app.state, "gateway", None)


def _not_ready(request_id: str) -> JSONResponse:
    return error_response(GatewayError(503, "gateway_not_ready", "gateway is not ready", retryable=True), request_id)


async def healthz(request: Request) -> Response:
    ready = _gateway(request) is not None
    return JSONResponse({"status": "ok" if ready else "starting"}, status_code=200 if ready else 503)


async def list_models(request: Request) -> Response:
    gateway = _gateway(request)
    if gateway is None:
        return _not_ready("req-" + uuid.uuid4().hex)
    return await gateway.list_models(request)


async def chat_completions(request: Request) -> Response:
    gateway = _gateway(request)
    if gateway is None:
        return _not_ready("req-" + uuid.uuid4().hex)
    return await gateway.chat_completions(request)


def create_app() -> Starlette:
    configure_logging()
    # No compression middleware: gzip buffers the body and breaks streaming.
    return Starlette(
        routes=[
            Route("/healthz", healthz, methods=["GET"]),
            Route("/v1/models", list_models, methods=["GET"]),
            Route("/v1/chat/completions", chat_completions, methods=["POST"]),
        ],
        lifespan=lifespan,
    )


app = create_app()
