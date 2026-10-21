#!/usr/bin/env python3
"""Lightweight OTLP/HTTP trace receiver reference for Foundry hosted agents.

Offline, standard-library-only (Python 3.10+) reference for the
``hosted-agent-otel-trace-receiver`` asset. It shows what a team-owned trace
receiver must do when a hosted agent exports OpenTelemetry traces over
OTLP/HTTP *in addition to* the platform's built-in tracing:

* authenticate the exporter (fake Entra token validator; dev-only shared header)
* bound the payload (body size, decompressed size, span count, attribute
  count and value length)
* decode OTLP/JSON (protobuf decoding is a production seam)
* validate spans and answer OTLP partial success for rejected spans
* redact attributes against an allowlist (content off by default,
  identifiers hashed with a keyed HMAC)
* stamp trusted routing from the authenticated principal, never from span or
  resource attributes
* fan out to sinks and return OTLP-shaped responses

Modes::

    --self-test   run named scenarios, print PASS/FAIL, exit non-zero on failure
    --domain X    run Domain Pack checks (contoso-retail, fabrikam-health,
                  northwind-logistics, all)
    --demo        narrate one export batch end to end, including a 429 retry
    --serve       listen on 127.0.0.1 only for local verification (fake tokens)
    --send        send one fake export batch to a local --serve (use --port)

Everything here is illustrative. Bounds are configurable parameters, not
recommendations. Tenants, audiences, client ids, and hosts are fictional.
"""
from __future__ import annotations

import argparse
import base64
import copy
import gzip
import hashlib
import hmac
import http.client
import json
import math
import re
import socket
import sys
import threading
import time
import zlib
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable, ClassVar, Iterable, Protocol

# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

TRACES_PATH = "/v1/traces"
LOGS_PATH = "/v1/logs"
METRICS_PATH = "/v1/metrics"
SIGNAL_BY_PATH = {TRACES_PATH: "traces", LOGS_PATH: "logs", METRICS_PATH: "metrics"}
JSON_TYPE = "application/json"
PROTOBUF_TYPE = "application/x-protobuf"

# OTLP/HTTP: only these statuses are retryable; other 4xx/5xx make the exporter drop data.
RETRYABLE_STATUSES = frozenset({429, 502, 503, 504})

# google.rpc.Code values carried in OTLP error bodies (google.rpc.Status).
GRPC_CODE = {
    400: 3,   # INVALID_ARGUMENT
    401: 16,  # UNAUTHENTICATED
    403: 7,   # PERMISSION_DENIED
    404: 5,   # NOT_FOUND
    405: 12,  # UNIMPLEMENTED
    411: 3,
    413: 3,
    415: 3,
    429: 8,   # RESOURCE_EXHAUSTED
    503: 14,  # UNAVAILABLE
}

TRACE_ID_RE = re.compile(r"^[0-9a-f]{32}$")
SPAN_ID_RE = re.compile(r"^[0-9a-f]{16}$")

# Resource attributes in this namespace are written only by the receiver.
RECEIVER_NAMESPACE = "trace_receiver."

# Demo-only HMAC key. Production keys come from a secret store and are rotated deliberately.
DEMO_HMAC_KEY = b"demo-only-key-not-a-secret"

# Illustrative W3C Trace Context ids (from the W3C specification examples).
DEMO_TRACE_ID = "4bf92f3577b34da6a3ce929d0e0e4736"
GATEWAY_SPAN_ID = "00f067aa0ba902b7"
AGENT_SPAN_ID = "1111111111111111"
CHAT_SPAN_ID = "2222222222222222"
TOOL_SPAN_ID = "3333333333333333"

# ---------------------------------------------------------------------------
# Errors, clock, principal
# ---------------------------------------------------------------------------


class ReceiverError(Exception):
    """An HTTP-level rejection with an OTLP-shaped error body."""

    def __init__(self, status: int, message: str, headers: dict[str, str] | None = None):
        super().__init__(message)
        self.status = status
        self.message = message
        self.headers = dict(headers or {})


class SinkUnavailable(Exception):
    """Raised by a sink when its destination is temporarily unavailable."""


class Clock(Protocol):
    def now(self) -> float: ...


class SystemClock:
    def now(self) -> float:
        return time.time()


class FakeClock:
    """Deterministic clock for tests and the demo."""

    def __init__(self, start: float = 1_800_000_000.0):
        self._now = start

    def now(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


@dataclass(frozen=True)
class Principal:
    """Who is exporting, as established by authentication (never by payload)."""

    client_id: str
    tenant_id: str
    route: str
    auth_method: str


# ---------------------------------------------------------------------------
# Attribute policy
# ---------------------------------------------------------------------------

# GenAI semantic conventions are in Development status; names can change.
CONTENT_ATTRIBUTES = frozenset({
    "gen_ai.input.messages",
    "gen_ai.output.messages",
    "gen_ai.system_instructions",
    "gen_ai.tool.definitions",
    "gen_ai.tool.call.arguments",
    "gen_ai.tool.call.result",
    # Older names still emitted by some instrumentations.
    "gen_ai.prompt",
    "gen_ai.completion",
})

DEFAULT_SPAN_ALLOWLIST = frozenset({
    "gen_ai.operation.name",
    "gen_ai.provider.name",
    "gen_ai.agent.id",
    "gen_ai.agent.name",
    "gen_ai.agent.version",
    "gen_ai.conversation.id",
    "gen_ai.request.model",
    "gen_ai.response.model",
    "gen_ai.response.id",
    "gen_ai.response.finish_reasons",
    "gen_ai.usage.input_tokens",
    "gen_ai.usage.output_tokens",
    "gen_ai.tool.name",
    "gen_ai.tool.type",
    "gen_ai.tool.call.id",
    "error.type",
    "server.address",
    "server.port",
    # Event attribute: keep the exception type, never the message or stack trace.
    "exception.type",
})

DEFAULT_HASHED_ATTRIBUTES = frozenset({"user.id", "enduser.id"})

DEFAULT_RESOURCE_ALLOWLIST = frozenset({
    "service.name",
    "service.version",
    "telemetry.sdk.*",
    "deployment.environment.name",
    "cloud.provider",
    "cloud.region",
})


def _allowed(key: str, allowlist: Iterable[str]) -> bool:
    for entry in allowlist:
        if entry.endswith(".*"):
            if key.startswith(entry[:-1]):
                return True
        elif key == entry:
            return True
    return False


@dataclass
class ReceiverPolicy:
    """Per-environment receiver policy. Bound values are illustrative, not recommendations."""

    name: str = "default"
    environment: str = "dev"
    max_body_bytes: int = 256 * 1024
    max_decompressed_bytes: int = 1024 * 1024
    max_spans_per_request: int = 64
    max_attributes_per_span: int = 32
    max_value_chars: int = 256
    max_events_per_span: int = 8
    span_allowlist: frozenset[str] = DEFAULT_SPAN_ALLOWLIST
    resource_allowlist: frozenset[str] = DEFAULT_RESOURCE_ALLOWLIST
    content_attributes: frozenset[str] = CONTENT_ATTRIBUTES
    hashed_attributes: frozenset[str] = DEFAULT_HASHED_ATTRIBUTES
    capture_content: bool = False
    content_capture_allowed: bool = True
    hmac_key: bytes = DEMO_HMAC_KEY
    other_signals: str = "discard"  # "discard" (200 + partialSuccess) or "reject" (404)
    requests_per_window: int = 100
    window_seconds: int = 60
    retry_after_seconds: int = 5

    def __post_init__(self) -> None:
        if self.capture_content and not self.content_capture_allowed:
            raise ValueError(f"policy {self.name!r} forbids content capture")
        if self.other_signals not in ("discard", "reject"):
            raise ValueError("other_signals must be 'discard' or 'reject'")
        bounds = (
            self.max_body_bytes, self.max_decompressed_bytes, self.max_spans_per_request,
            self.max_attributes_per_span, self.max_value_chars, self.max_events_per_span,
            self.requests_per_window, self.window_seconds, self.retry_after_seconds,
        )
        if any(value <= 0 for value in bounds):
            raise ValueError("all bounds must be positive")
        if not self.hmac_key:
            raise ValueError("hmac_key must be set")


@dataclass
class RedactionReport:
    """What the receiver changed. Holds counts and attribute keys, never values."""

    MAX_REPORTED_KEYS: ClassVar[int] = 50

    spans_received: int = 0
    spans_accepted: int = 0
    spans_rejected: dict[str, int] = field(default_factory=dict)
    content_dropped: int = 0
    hashed: int = 0
    not_allowlisted: int = 0
    unsupported_value: int = 0
    truncated: int = 0
    over_attribute_limit: int = 0
    namespace_stripped: int = 0
    status_messages_dropped: int = 0
    events_dropped: int = 0
    dropped_keys: set[str] = field(default_factory=set)

    def note_key(self, key: str) -> None:
        if len(self.dropped_keys) < self.MAX_REPORTED_KEYS:
            self.dropped_keys.add(key[:128])

    def reject(self, reason: str) -> None:
        self.spans_rejected[reason] = self.spans_rejected.get(reason, 0) + 1

    @property
    def rejected_total(self) -> int:
        return sum(self.spans_rejected.values())

    def to_dict(self) -> dict[str, Any]:
        return {
            "spansReceived": self.spans_received,
            "spansAccepted": self.spans_accepted,
            "spansRejected": dict(sorted(self.spans_rejected.items())),
            "attributes": {
                "contentDropped": self.content_dropped,
                "hashed": self.hashed,
                "notAllowlisted": self.not_allowlisted,
                "unsupportedValue": self.unsupported_value,
                "truncated": self.truncated,
                "overAttributeLimit": self.over_attribute_limit,
                "namespaceStripped": self.namespace_stripped,
            },
            "statusMessagesDropped": self.status_messages_dropped,
            "eventsDropped": self.events_dropped,
            "droppedKeys": sorted(self.dropped_keys),
        }


# ---------------------------------------------------------------------------
# Redaction and span validation
# ---------------------------------------------------------------------------

_SCALAR_KINDS = ("stringValue", "boolValue", "intValue", "doubleValue")


def _scalar_text(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    for kind in _SCALAR_KINDS:
        if kind in value:
            raw = value[kind]
            if isinstance(raw, (str, int, float, bool)):
                return str(raw)
    return None


class AttributeRedactor:
    """Allowlist-first attribute redaction.

    Order: strip receiver namespace -> drop content (unless captured) -> hash
    identifiers -> drop anything not allowlisted -> drop unsupported value types
    -> truncate strings -> cap attribute count.
    """

    def __init__(self, policy: ReceiverPolicy):
        self.policy = policy

    def hash_identifier(self, text: str) -> str:
        digest = hmac.new(self.policy.hmac_key, text.encode("utf-8"), hashlib.sha256)
        return "hmac-sha256:" + digest.hexdigest()[:32]

    def redact(self, attributes: Any, allowlist: Iterable[str], report: RedactionReport) -> list[dict]:
        policy = self.policy
        out: list[dict] = []
        if not isinstance(attributes, list):
            return out
        for attr in attributes:
            if not isinstance(attr, dict) or not isinstance(attr.get("key"), str):
                report.unsupported_value += 1
                continue
            key = attr["key"]
            value = attr.get("value")
            if key.startswith(RECEIVER_NAMESPACE):
                report.namespace_stripped += 1
                report.note_key(key)
                continue
            if key in policy.content_attributes:
                if not policy.capture_content:
                    report.content_dropped += 1
                    report.note_key(key)
                    continue
            elif key in policy.hashed_attributes:
                text = _scalar_text(value)
                if text is None:
                    report.unsupported_value += 1
                    report.note_key(key)
                    continue
                out.append({"key": key, "value": {"stringValue": self.hash_identifier(text)}})
                report.hashed += 1
                continue
            elif not _allowed(key, allowlist):
                report.not_allowlisted += 1
                report.note_key(key)
                continue
            bounded = self._bound_value(value, report)
            if bounded is None:
                report.unsupported_value += 1
                report.note_key(key)
                continue
            out.append({"key": key, "value": bounded})
        if len(out) > policy.max_attributes_per_span:
            for extra in out[policy.max_attributes_per_span:]:
                report.over_attribute_limit += 1
                report.note_key(extra["key"])
            out = out[: policy.max_attributes_per_span]
        return out

    def _bound_scalar(self, value: Any, report: RedactionReport) -> dict | None:
        if not isinstance(value, dict):
            return None
        if "stringValue" in value and isinstance(value["stringValue"], str):
            text = value["stringValue"]
            if len(text) > self.policy.max_value_chars:
                report.truncated += 1
                text = text[: self.policy.max_value_chars]
            return {"stringValue": text}
        if "boolValue" in value and isinstance(value["boolValue"], bool):
            return {"boolValue": value["boolValue"]}
        if "intValue" in value:
            raw = value["intValue"]
            if isinstance(raw, bool):
                return None
            if isinstance(raw, int) or (isinstance(raw, str) and re.fullmatch(r"-?\d{1,19}", raw)):
                return {"intValue": str(int(raw))}
            return None
        if "doubleValue" in value and isinstance(value["doubleValue"], (int, float)) \
                and not isinstance(value["doubleValue"], bool):
            return {"doubleValue": float(value["doubleValue"])}
        return None

    def _bound_value(self, value: Any, report: RedactionReport) -> dict | None:
        if isinstance(value, dict) and "arrayValue" in value:
            items = value["arrayValue"].get("values", []) if isinstance(value["arrayValue"], dict) else []
            if not isinstance(items, list):
                return None
            bounded_items = []
            for item in items[: self.policy.max_attributes_per_span]:
                bounded = self._bound_scalar(item, report)
                if bounded is not None:
                    bounded_items.append(bounded)
            return {"arrayValue": {"values": bounded_items}}
        # kvlistValue and bytesValue are dropped: they are a common path for smuggling payloads.
        return self._bound_scalar(value, report)


def _parse_nanos(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value >= 0 else None
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _valid_id(value: Any, pattern: re.Pattern[str]) -> str | None:
    if not isinstance(value, str):
        return None
    lowered = value.lower()
    if not pattern.match(lowered) or set(lowered) == {"0"}:
        return None
    return lowered


def validate_span(span: Any) -> str | None:
    """Return a rejection reason, or None when the span is structurally valid."""
    if not isinstance(span, dict):
        return "not_an_object"
    if _valid_id(span.get("traceId"), TRACE_ID_RE) is None:
        return "invalid_trace_id"
    if _valid_id(span.get("spanId"), SPAN_ID_RE) is None:
        return "invalid_span_id"
    parent = span.get("parentSpanId") or ""
    if parent and _valid_id(parent, SPAN_ID_RE) is None:
        return "invalid_parent_span_id"
    name = span.get("name")
    if not isinstance(name, str) or not name.strip():
        return "missing_name"
    start = _parse_nanos(span.get("startTimeUnixNano"))
    end = _parse_nanos(span.get("endTimeUnixNano"))
    if start is None or end is None or end < start:
        return "invalid_timestamps"
    return None


# ---------------------------------------------------------------------------
# Exporter authentication
# ---------------------------------------------------------------------------


class Authenticator(Protocol):
    def authenticate(self, headers: dict[str, str], now: float) -> Principal: ...


@dataclass(frozen=True)
class TrustConfig:
    """Which exporters may send, and where their spans go.

    ``audiences`` holds both the receiver's application ID URI (v1 tokens) and its
    client id (v2 tokens). ``clients`` maps the exporter's ``azp``/``appid`` (the
    hosted agent's managed identity) to a destination route.
    """

    tenant_ids: frozenset[str]
    audiences: frozenset[str]
    clients: dict[str, str]
    required_role: str = "Traces.Write"
    clock_skew_seconds: int = 120

    def issuers(self) -> frozenset[str]:
        found: set[str] = set()
        for tid in self.tenant_ids:
            found.add(f"https://sts.windows.net/{tid}/")
            found.add(f"https://login.microsoftonline.com/{tid}/v2.0")
        return frozenset(found)


def _unauthorized(message: str, missing: bool = False) -> ReceiverError:
    challenge = 'Bearer realm="trace-receiver"' if missing else \
        f'Bearer realm="trace-receiver", error="invalid_token", error_description="{message}"'
    return ReceiverError(401, message, {"WWW-Authenticate": challenge})


def make_fake_token(claims: dict[str, Any]) -> str:
    """Build an UNSIGNED fake token. Only FakeEntraTokenValidator accepts these."""
    raw = json.dumps(claims, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return "fake." + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _decode_fake_token(token: str) -> dict[str, Any]:
    if not token.startswith("fake."):
        raise _unauthorized("malformed token")
    payload = token[len("fake."):]
    try:
        claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except ValueError:
        raise _unauthorized("malformed token") from None
    if not isinstance(claims, dict):
        raise _unauthorized("malformed token")
    return claims


class FakeEntraTokenValidator:
    """Offline stand-in for Entra access-token validation: claim checks only, no signature."""

    def __init__(self, trust: TrustConfig):
        self.trust = trust

    def authenticate(self, headers: dict[str, str], now: float) -> Principal:
        scheme, _, token = headers.get("authorization", "").partition(" ")
        if scheme.lower() != "bearer" or not token.strip():
            raise _unauthorized("missing bearer token", missing=True)
        claims = _decode_fake_token(token.strip())
        trust = self.trust
        if claims.get("tid") not in trust.tenant_ids:
            raise _unauthorized("untrusted tenant")
        if claims.get("iss") not in trust.issuers():
            raise _unauthorized("untrusted issuer")
        if claims.get("aud") not in trust.audiences:
            raise _unauthorized("invalid audience")
        exp, nbf = claims.get("exp"), claims.get("nbf", 0)
        if not isinstance(exp, (int, float)) or now > exp + trust.clock_skew_seconds:
            raise _unauthorized("token expired")
        if isinstance(nbf, (int, float)) and now + trust.clock_skew_seconds < nbf:
            raise _unauthorized("token not yet valid")
        client = claims.get("azp") or claims.get("appid")
        if client not in trust.clients:
            raise ReceiverError(403, "client is not allowed to export to this receiver")
        roles = claims.get("roles") or []
        if not isinstance(roles, list) or trust.required_role not in roles:
            raise ReceiverError(403, f"token lacks the {trust.required_role} app role")
        return Principal(client, claims["tid"], trust.clients[client], "entra-bearer")


class SharedHeaderAuthenticator:
    """Development-only shared-secret header. Refuses to run outside ``dev``."""

    def __init__(self, header_name: str, secret: str, environment: str, route: str = "dev"):
        if environment != "dev":
            raise ValueError("shared-header authentication is restricted to the dev environment")
        if not secret:
            raise ValueError("secret must be non-empty")
        self.header = header_name.lower()
        self._secret = secret.encode("utf-8")
        self.route = route

    def authenticate(self, headers: dict[str, str], now: float) -> Principal:
        supplied = headers.get(self.header, "").encode("utf-8")
        if not supplied or not hmac.compare_digest(supplied, self._secret):
            raise ReceiverError(401, "missing or invalid shared header")
        return Principal("dev-shared-header", "dev", self.route, "shared-header")


class EntraTokenValidator:
    """Production seam: validate *signed* Entra access tokens.

    Use a maintained JWT/OIDC library to fetch the tenant's OpenID configuration
    and signing keys (JWKS, cached and refreshed on key rollover), verify the
    signature and algorithm, then apply the same claim checks as
    FakeEntraTokenValidator (tid, iss, aud, exp/nbf, azp/appid allowlist, app
    role). Built-in authentication on the hosting platform can perform the
    signature step; the claim and allowlist checks still belong to the receiver.
    """

    def __init__(self, trust: TrustConfig):
        self.trust = trust

    def authenticate(self, headers: dict[str, str], now: float) -> Principal:
        raise NotImplementedError("wire a maintained JWT library; see class docstring")


# ---------------------------------------------------------------------------
# Decoding and payload bounds
# ---------------------------------------------------------------------------


class TraceDecoder(Protocol):
    content_type: str

    def decode(self, body: bytes, signal: str) -> dict[str, Any]: ...


class JsonTraceDecoder:
    content_type = JSON_TYPE

    def decode(self, body: bytes, signal: str) -> dict[str, Any]:
        try:
            payload = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReceiverError(400, f"malformed OTLP/JSON ({exc.__class__.__name__})") from None
        if not isinstance(payload, dict):
            raise ReceiverError(400, "OTLP/JSON body must be a JSON object")
        return payload


class ProtobufTraceDecoder:
    """Production seam: decode ``application/x-protobuf`` export requests.

    Use the opentelemetry-proto generated classes. Convert trace and span ids
    from bytes to lowercase hex (protobuf-to-dict helpers emit base64), keep
    int64 values as decimal strings, and encode responses as protobuf when the
    request was protobuf. Alternatively, terminate OTLP at an OpenTelemetry
    Collector and forward OTLP/JSON to this receiver.
    """

    content_type = PROTOBUF_TYPE

    def decode(self, body: bytes, signal: str) -> dict[str, Any]:
        raise NotImplementedError("protobuf decoding needs opentelemetry-proto; see class docstring")


def decompress_bounded(body: bytes, encoding: str, limit: int) -> bytes:
    """Decompress with a hard ceiling so a small gzip body cannot expand without bound."""
    encoding = (encoding or "").strip().lower()
    if encoding in ("", "identity"):
        if len(body) > limit:
            raise ReceiverError(413, "body exceeds the decompressed size limit")
        return body
    if encoding != "gzip":
        raise ReceiverError(415, f"unsupported Content-Encoding {encoding!r}; use gzip or none")
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        out = inflater.decompress(body, limit + 1)
    except zlib.error:
        raise ReceiverError(400, "invalid gzip body") from None
    if len(out) > limit or inflater.unconsumed_tail:
        raise ReceiverError(413, "decompressed body exceeds the configured limit")
    if not inflater.eof:
        raise ReceiverError(400, "truncated gzip body")
    return out


class FixedWindowRateLimiter:
    """Per-client fixed-window limiter. Answers 429 with Retry-After."""

    def __init__(self, limit: int, window_seconds: int, clock: Clock):
        self.limit = limit
        self.window = window_seconds
        self.clock = clock
        self._state: dict[str, tuple[float, int]] = {}
        self._lock = threading.Lock()

    def check(self, client_id: str) -> None:
        now = self.clock.now()
        window_start = math.floor(now / self.window) * self.window
        with self._lock:
            start, count = self._state.get(client_id, (window_start, 0))
            if start != window_start:
                start, count = window_start, 0
            if count >= self.limit:
                retry_after = max(1, math.ceil(start + self.window - now))
                raise ReceiverError(429, "export rate exceeded for this client",
                                    {"Retry-After": str(retry_after)})
            self._state[client_id] = (start, count + 1)


# ---------------------------------------------------------------------------
# Sinks
# ---------------------------------------------------------------------------


class TraceSink(Protocol):
    name: str

    def write(self, batch: dict[str, Any], principal: Principal) -> None: ...


class InMemorySink:
    """Test sink. Real sinks must be idempotent on (traceId, spanId): exporters retry."""

    def __init__(self, name: str):
        self.name = name
        self.available = True
        self.batches: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def write(self, batch: dict[str, Any], principal: Principal) -> None:
        if not self.available:
            raise SinkUnavailable(f"sink {self.name} unavailable")
        with self._lock:
            self.batches.append(copy.deepcopy(batch))

    def spans(self) -> list[dict[str, Any]]:
        found = []
        for batch in self.batches:
            for rs in batch.get("resourceSpans", []):
                for ss in rs.get("scopeSpans", []):
                    found.extend(ss.get("spans", []))
        return found

    def resource_attrs(self) -> list[dict[str, Any]]:
        found = []
        for batch in self.batches:
            for rs in batch.get("resourceSpans", []):
                found.append({a["key"]: a["value"] for a in rs.get("resource", {}).get("attributes", [])})
        return found

    def dumps(self) -> str:
        return json.dumps(self.batches, sort_keys=True)


class BlobSink:
    """Production seam: write accepted batches to a storage container.

    Authenticate with the receiver's managed identity (data-plane role scoped to
    the one container), name blobs by route/date/traceId/spanId so retries
    overwrite rather than duplicate, and set lifecycle rules for retention.
    """

    name = "blob"

    def write(self, batch: dict[str, Any], principal: Principal) -> None:
        raise NotImplementedError("use the storage SDK with a managed identity credential")


class LogAnalyticsSink:
    """Production seam: send rows through the Logs Ingestion API (data collection rule)."""

    name = "log-analytics"

    def write(self, batch: dict[str, Any], principal: Principal) -> None:
        raise NotImplementedError("use the monitor ingestion SDK with a managed identity credential")


# ---------------------------------------------------------------------------
# HTTP-shaped request/response (transport independent)
# ---------------------------------------------------------------------------


@dataclass
class HttpRequest:
    method: str
    path: str
    headers: dict[str, str]
    body: bytes

    def __post_init__(self) -> None:
        self.headers = {k.lower(): v for k, v in self.headers.items()}

    def header(self, name: str, default: str = "") -> str:
        return self.headers.get(name.lower(), default)


@dataclass
class HttpResponse:
    status: int
    body: dict[str, Any]
    headers: dict[str, str] = field(default_factory=dict)
    content_type: str = JSON_TYPE

    def encoded(self) -> bytes:
        return json.dumps(self.body, separators=(",", ":")).encode("utf-8")


def error_response(err: ReceiverError) -> HttpResponse:
    body = {"code": GRPC_CODE.get(err.status, 2), "message": err.message}
    return HttpResponse(err.status, body, dict(err.headers))


# ---------------------------------------------------------------------------
# Receiver
# ---------------------------------------------------------------------------


class TraceReceiver:
    """Transport-independent OTLP/HTTP trace receiver core."""

    def __init__(
        self,
        policy: ReceiverPolicy,
        authenticator: Authenticator,
        routes: dict[str, list[TraceSink]],
        clock: Clock | None = None,
        decoders: Iterable[TraceDecoder] | None = None,
    ):
        self.policy = policy
        self.authenticator = authenticator
        self.routes = routes
        self.clock = clock or SystemClock()
        self.redactor = AttributeRedactor(policy)
        self.limiter = FixedWindowRateLimiter(policy.requests_per_window, policy.window_seconds, self.clock)
        self.decoders = {d.content_type: d for d in (decoders or [JsonTraceDecoder()])}
        self.last_report: RedactionReport | None = None

    def handle(self, request: HttpRequest) -> HttpResponse:
        try:
            return self._handle(request)
        except ReceiverError as err:
            return error_response(err)

    def _handle(self, request: HttpRequest) -> HttpResponse:
        policy = self.policy
        signal = SIGNAL_BY_PATH.get(request.path.split("?", 1)[0])
        if signal is None:
            raise ReceiverError(404, f"unknown path; OTLP/HTTP traces are POSTed to {TRACES_PATH}")
        if request.method.upper() != "POST":
            raise ReceiverError(405, "OTLP/HTTP export requires POST", {"Allow": "POST"})
        principal = self.authenticator.authenticate(request.headers, self.clock.now())
        self.limiter.check(principal.client_id)
        if len(request.body) > policy.max_body_bytes:
            raise ReceiverError(413, "request body exceeds the configured limit")
        content_type = request.header("content-type").split(";", 1)[0].strip().lower()
        decoder = self.decoders.get(content_type)
        if decoder is None:
            hint = ("register a protobuf decoder or terminate OTLP at a Collector"
                    if content_type == PROTOBUF_TYPE else f"send {JSON_TYPE}")
            raise ReceiverError(415, f"unsupported Content-Type {content_type!r}; {hint}")
        body = decompress_bounded(request.body, request.header("content-encoding"),
                                  policy.max_decompressed_bytes)
        payload = decoder.decode(body, signal)
        if signal != "traces":
            return self._other_signal(signal, payload)
        batch, report = self.process_traces(payload, principal)
        self.last_report = report
        sinks = self.routes.get(principal.route)
        if not sinks:
            raise ReceiverError(403, "no destination is configured for this client")
        if report.spans_accepted:
            try:
                for sink in sinks:
                    sink.write(batch, principal)
            except SinkUnavailable:
                raise ReceiverError(503, "destination temporarily unavailable",
                                    {"Retry-After": str(policy.retry_after_seconds)}) from None
        if report.rejected_total:
            reasons = ", ".join(f"{k}={v}" for k, v in sorted(report.spans_rejected.items()))
            return HttpResponse(200, {"partialSuccess": {
                "rejectedSpans": str(report.rejected_total),
                "errorMessage": f"rejected spans: {reasons}",
            }})
        return HttpResponse(200, {})

    def _other_signal(self, signal: str, payload: dict[str, Any]) -> HttpResponse:
        if self.policy.other_signals == "reject":
            raise ReceiverError(404, f"this receiver accepts traces only; {signal} are not accepted")
        count = 0
        if signal == "logs":
            for rl in _list_field(payload, "resourceLogs"):
                for sl in _list_field(rl, "scopeLogs"):
                    count += len(_list_field(sl, "logRecords"))
            field_name = "rejectedLogRecords"
        else:
            for rm in _list_field(payload, "resourceMetrics"):
                for sm in _list_field(rm, "scopeMetrics"):
                    for metric in _list_field(sm, "metrics"):
                        for kind in ("gauge", "sum", "histogram", "exponentialHistogram", "summary"):
                            count += len(_list_field(metric.get(kind) if isinstance(metric, dict) else None,
                                                     "dataPoints"))
            field_name = "rejectedDataPoints"
        if not count:
            return HttpResponse(200, {})
        return HttpResponse(200, {"partialSuccess": {
            field_name: str(count),
            "errorMessage": f"{signal} are discarded by receiver policy; export traces only",
        }})

    def process_traces(self, payload: dict[str, Any], principal: Principal) -> tuple[dict, RedactionReport]:
        policy = self.policy
        report = RedactionReport()
        resource_spans = payload.get("resourceSpans", [])
        if resource_spans is None:
            resource_spans = []
        if not isinstance(resource_spans, list):
            raise ReceiverError(400, "resourceSpans must be an array")
        out_resource_spans = []
        for rs in resource_spans:
            if not isinstance(rs, dict):
                continue
            out_scopes = []
            for ss in _list_field(rs, "scopeSpans"):
                if not isinstance(ss, dict):
                    continue
                kept = []
                for span in _list_field(ss, "spans"):
                    report.spans_received += 1
                    if report.spans_received > policy.max_spans_per_request:
                        report.reject("over_span_limit")
                        continue
                    reason = validate_span(span)
                    if reason:
                        report.reject(reason)
                        continue
                    kept.append(self._clean_span(span, report))
                    report.spans_accepted += 1
                if kept:
                    scope = ss.get("scope") if isinstance(ss.get("scope"), dict) else {}
                    out_scopes.append({
                        "scope": {k: str(scope[k])[: policy.max_value_chars]
                                  for k in ("name", "version") if scope.get(k)},
                        "spans": kept,
                    })
            if out_scopes:
                resource = rs.get("resource") if isinstance(rs.get("resource"), dict) else {}
                attrs = self.redactor.redact(resource.get("attributes", []), policy.resource_allowlist, report)
                attrs.extend(_trusted_attributes(principal, policy))
                out_resource_spans.append({"resource": {"attributes": attrs}, "scopeSpans": out_scopes})
        return {"resourceSpans": out_resource_spans}, report

    def _clean_span(self, span: dict[str, Any], report: RedactionReport) -> dict[str, Any]:
        policy = self.policy
        allow = policy.span_allowlist
        clean: dict[str, Any] = {
            "traceId": span["traceId"].lower(),
            "spanId": span["spanId"].lower(),
            "name": span["name"][: policy.max_value_chars],
            "kind": span.get("kind") if span.get("kind") in range(0, 6) else 0,
            "startTimeUnixNano": str(_parse_nanos(span["startTimeUnixNano"])),
            "endTimeUnixNano": str(_parse_nanos(span["endTimeUnixNano"])),
            "attributes": self.redactor.redact(span.get("attributes", []), allow, report),
        }
        if span.get("parentSpanId"):
            clean["parentSpanId"] = span["parentSpanId"].lower()
        status = span.get("status") if isinstance(span.get("status"), dict) else {}
        if status:
            if status.get("message"):
                report.status_messages_dropped += 1
            code = status.get("code", 0)
            clean["status"] = {"code": code if code in (0, 1, 2) else 0}
        events = span.get("events", []) or []
        if isinstance(events, list) and events:
            kept_events = []
            for event in events[: policy.max_events_per_span]:
                if not isinstance(event, dict):
                    continue
                kept_events.append({
                    "name": str(event.get("name", ""))[: policy.max_value_chars],
                    "timeUnixNano": str(_parse_nanos(event.get("timeUnixNano")) or 0),
                    "attributes": self.redactor.redact(event.get("attributes", []), allow, report),
                })
            report.events_dropped += max(0, len(events) - policy.max_events_per_span)
            clean["events"] = kept_events
        links = span.get("links", []) or []
        if isinstance(links, list) and links:
            clean["links"] = [
                {"traceId": link["traceId"].lower(), "spanId": link["spanId"].lower()}
                for link in links
                if isinstance(link, dict)
                and _valid_id(link.get("traceId"), TRACE_ID_RE)
                and _valid_id(link.get("spanId"), SPAN_ID_RE)
            ][: policy.max_events_per_span]
        return clean


def _list_field(container: Any, key: str) -> list[Any]:
    """Return container[key] as a list; absent/null is empty, any other shape is a 400."""
    if not isinstance(container, dict):
        return []
    value = container.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise ReceiverError(400, f"{key} must be an array")
    return value


def _trusted_attributes(principal: Principal, policy: ReceiverPolicy) -> list[dict[str, Any]]:
    """Routing facts the receiver stamps itself, from the authenticated principal."""
    return [
        {"key": RECEIVER_NAMESPACE + "route", "value": {"stringValue": principal.route}},
        {"key": RECEIVER_NAMESPACE + "client_id", "value": {"stringValue": principal.client_id}},
        {"key": RECEIVER_NAMESPACE + "auth_method", "value": {"stringValue": principal.auth_method}},
        {"key": RECEIVER_NAMESPACE + "policy", "value": {"stringValue": policy.name}},
    ]


# ---------------------------------------------------------------------------
# Exporter-side retry semantics (what a compliant OTLP exporter does)
# ---------------------------------------------------------------------------


def exporter_should_retry(status: int) -> bool:
    return status in RETRYABLE_STATUSES


def exporter_backoff_seconds(status: int, headers: dict[str, str], attempt: int,
                             base: float = 1.0, cap: float = 30.0) -> float | None:
    """Delay before the next attempt, or None when the exporter must drop the batch."""
    if not exporter_should_retry(status):
        return None
    for key, value in headers.items():
        if key.lower() == "retry-after" and value.strip().isdigit():
            return float(value.strip())
    return min(cap, base * (2 ** max(0, attempt - 1)))


def export_with_retry(receiver: TraceReceiver, payload: dict[str, Any],
                      headers_factory: Callable[[], dict[str, str]], clock: FakeClock,
                      max_attempts: int = 3, log: Callable[[str], None] | None = None) -> HttpResponse:
    response = HttpResponse(0, {})
    for attempt in range(1, max_attempts + 1):
        headers = {"Content-Type": JSON_TYPE, **headers_factory()}
        response = receiver.handle(HttpRequest("POST", TRACES_PATH, headers,
                                               json.dumps(payload).encode("utf-8")))
        delay = exporter_backoff_seconds(response.status, response.headers, attempt)
        if log:
            log(f"attempt {attempt}: HTTP {response.status}"
                + (f" -> retry after {delay:g}s" if delay is not None and attempt < max_attempts else ""))
        if delay is None or attempt == max_attempts:
            return response
        clock.advance(delay)
    return response


# ---------------------------------------------------------------------------
# Fake exporter: builds an OTLP/JSON ExportTraceServiceRequest with GenAI spans
# ---------------------------------------------------------------------------


def s(key: str, value: str) -> dict[str, Any]:
    return {"key": key, "value": {"stringValue": value}}


def i(key: str, value: int) -> dict[str, Any]:
    return {"key": key, "value": {"intValue": str(value)}}


def arr(key: str, values: list[str]) -> dict[str, Any]:
    return {"key": key, "value": {"arrayValue": {"values": [{"stringValue": v} for v in values]}}}


def attr_map(attributes: list[dict[str, Any]]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for attr in attributes:
        value = attr["value"]
        if "arrayValue" in value:
            out[attr["key"]] = [next(iter(v.values())) for v in value["arrayValue"]["values"]]
        else:
            out[attr["key"]] = next(iter(value.values()))
    return out


BASE_NANOS = 1_800_000_000_000_000_000


def _nanos(offset_ms: int) -> str:
    return str(BASE_NANOS + offset_ms * 1_000_000)


def build_export_request(
    agent_name: str = "demo-hosted-agent",
    *,
    conversation_id: str = "conv-demo-0001",
    user_id: str | None = None,
    include_content: bool = True,
    extra_span_attrs: list[dict[str, Any]] | None = None,
    extra_resource_attrs: list[dict[str, Any]] | None = None,
    trace_id: str = DEMO_TRACE_ID,
    parent_span_id: str = GATEWAY_SPAN_ID,
) -> dict[str, Any]:
    """One agent turn: invoke_agent -> chat -> execute_tool, as a hosted agent would export it."""
    agent_attrs = [
        s("gen_ai.operation.name", "invoke_agent"),
        s("gen_ai.agent.name", agent_name),
        s("gen_ai.agent.id", f"{agent_name}:3"),
        s("gen_ai.conversation.id", conversation_id),
    ]
    if user_id:
        agent_attrs.append(s("enduser.id", user_id))
    if include_content:
        agent_attrs += [
            s("gen_ai.input.messages", '[{"role":"user","parts":[{"type":"text","content":"Where is order 1234?"}]}]'),
            s("gen_ai.system_instructions", '[{"type":"text","content":"You are a helpful order assistant."}]'),
        ]
    agent_attrs += extra_span_attrs or []
    chat_attrs = [
        s("gen_ai.operation.name", "chat"),
        s("gen_ai.provider.name", "azure.ai.openai"),
        s("gen_ai.request.model", "example-chat-model"),
        s("gen_ai.response.model", "example-chat-model"),
        s("gen_ai.response.id", "resp-demo-0001"),
        i("gen_ai.usage.input_tokens", 42),
        i("gen_ai.usage.output_tokens", 17),
        arr("gen_ai.response.finish_reasons", ["tool_calls"]),
        s("server.address", "models.example.com"),
    ]
    if include_content:
        chat_attrs.append(s("gen_ai.output.messages", '[{"role":"assistant","parts":[{"type":"tool_call"}]}]'))
    tool_attrs = [
        s("gen_ai.operation.name", "execute_tool"),
        s("gen_ai.tool.name", "lookup_order"),
        s("gen_ai.tool.type", "function"),
        s("gen_ai.tool.call.id", "call-demo-0001"),
    ]
    if include_content:
        tool_attrs += [s("gen_ai.tool.call.arguments", '{"order_id":"1234"}'),
                       s("gen_ai.tool.call.result", '{"status":"shipped"}')]
    resource_attrs = [
        s("service.name", agent_name),
        s("service.version", "3"),
        s("telemetry.sdk.name", "opentelemetry"),
        s("telemetry.sdk.language", "python"),
        s("deployment.environment.name", "dev"),
        s("cloud.provider", "azure"),
    ] + (extra_resource_attrs or [])
    spans = [
        {"traceId": trace_id, "spanId": AGENT_SPAN_ID, "parentSpanId": parent_span_id,
         "name": f"invoke_agent {agent_name}", "kind": 2,
         "startTimeUnixNano": _nanos(0), "endTimeUnixNano": _nanos(900),
         "attributes": agent_attrs, "status": {"code": 1}},
        {"traceId": trace_id, "spanId": CHAT_SPAN_ID, "parentSpanId": AGENT_SPAN_ID,
         "name": "chat example-chat-model", "kind": 3,
         "startTimeUnixNano": _nanos(10), "endTimeUnixNano": _nanos(400),
         "attributes": chat_attrs, "status": {}},
        {"traceId": trace_id, "spanId": TOOL_SPAN_ID, "parentSpanId": AGENT_SPAN_ID,
         "name": "execute_tool lookup_order", "kind": 1,
         "startTimeUnixNano": _nanos(410), "endTimeUnixNano": _nanos(500),
         "attributes": tool_attrs, "status": {}},
    ]
    return {"resourceSpans": [{
        "resource": {"attributes": resource_attrs},
        "scopeSpans": [{"scope": {"name": "demo.agent.instrumentation", "version": "0.1.0"},
                        "spans": spans}],
    }]}


def make_span(span_id: str, name: str = "extra span", **overrides: Any) -> dict[str, Any]:
    span = {"traceId": DEMO_TRACE_ID, "spanId": span_id, "parentSpanId": AGENT_SPAN_ID, "name": name,
            "kind": 1, "startTimeUnixNano": _nanos(0), "endTimeUnixNano": _nanos(1), "attributes": []}
    span.update(overrides)
    return span


# ---------------------------------------------------------------------------
# Test harness (fictional tenant, audience, and clients)
# ---------------------------------------------------------------------------

TENANT = "00000000-0000-0000-0000-000000000001"
OTHER_TENANT = "00000000-0000-0000-0000-000000000002"
AUDIENCE_ID = "00000000-0000-0000-0000-0000000000aa"
AUDIENCE_URI = "api://" + AUDIENCE_ID
DEMO_CLIENT = "demo-agent-mi"


def fake_claims(client: str = DEMO_CLIENT, *, tid: str = TENANT, aud: str = AUDIENCE_URI,
                version: str = "1.0", roles: list[str] | None = None, now: float = 1_800_000_000.0,
                lifetime: int = 3600) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "tid": tid, "aud": aud, "ver": version, "nbf": int(now) - 60, "iat": int(now) - 60,
        "exp": int(now) + lifetime, "roles": ["Traces.Write"] if roles is None else roles,
    }
    if version == "1.0":
        claims["iss"] = f"https://sts.windows.net/{tid}/"
        claims["appid"] = client
    else:
        claims["iss"] = f"https://login.microsoftonline.com/{tid}/v2.0"
        claims["azp"] = client
    return claims


def bearer(claims: dict[str, Any]) -> dict[str, str]:
    return {"Authorization": "Bearer " + make_fake_token(claims)}


def build_receiver(policy: ReceiverPolicy | None = None, clients: dict[str, str] | None = None,
                   clock: FakeClock | None = None) -> tuple[TraceReceiver, dict[str, InMemorySink], FakeClock]:
    policy = policy or ReceiverPolicy(name="test")
    clock = clock or FakeClock()
    clients = clients or {DEMO_CLIENT: "default"}
    trust = TrustConfig(frozenset({TENANT}), frozenset({AUDIENCE_URI, AUDIENCE_ID}), dict(clients))
    sinks = {route: InMemorySink(route) for route in sorted(set(clients.values()))}
    receiver = TraceReceiver(policy, FakeEntraTokenValidator(trust),
                             {route: [sink] for route, sink in sinks.items()}, clock)
    return receiver, sinks, clock


def post_json(receiver: TraceReceiver, payload: Any, headers: dict[str, str] | None = None, *,
              path: str = TRACES_PATH, gzip_body: bool = False,
              content_type: str = JSON_TYPE, raw: bytes | None = None) -> HttpResponse:
    body = raw if raw is not None else json.dumps(payload).encode("utf-8")
    all_headers = {"Content-Type": content_type}
    if gzip_body:
        body = gzip.compress(body)
        all_headers["Content-Encoding"] = "gzip"
    all_headers.update(headers if headers is not None else bearer(fake_claims()))
    return receiver.handle(HttpRequest("POST", path, all_headers, body))


# ---------------------------------------------------------------------------
# Loopback-only HTTP server for local verification
# ---------------------------------------------------------------------------

LOOPBACK_HOST = "127.0.0.1"


def make_server(receiver: TraceReceiver, host: str = LOOPBACK_HOST, port: int = 4318) -> ThreadingHTTPServer:
    if host != LOOPBACK_HOST:
        raise ValueError("this reference server binds to loopback only; deploy a hardened receiver instead")

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        server_version = "otlp-trace-receiver-reference"
        sys_version = ""

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - stdlib signature
            return

        def _send(self, response: HttpResponse) -> None:
            body = response.encoded()
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(body)))
            for key, value in response.headers.items():
                self.send_header(key, value)
            self.end_headers()
            self.wfile.write(body)

        def _dispatch(self) -> None:
            length_header = self.headers.get("Content-Length")
            if self.command == "POST":
                if length_header is None or not length_header.strip().isdigit():
                    self._send(error_response(ReceiverError(411, "Content-Length is required")))
                    return
                length = int(length_header)
                if length > receiver.policy.max_body_bytes:
                    # Refuse before reading so an oversized body never enters memory.
                    self._send(error_response(ReceiverError(413, "request body exceeds the configured limit")))
                    return
                body = self.rfile.read(length)
            else:
                body = b""
            headers = {k: v for k, v in self.headers.items()}
            self._send(receiver.handle(HttpRequest(self.command, self.path, headers, body)))

        do_POST = _dispatch
        do_GET = _dispatch
        do_PUT = _dispatch

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    return server


# ---------------------------------------------------------------------------
# Self-test scenarios
# ---------------------------------------------------------------------------

SCENARIOS: list[tuple[str, Callable[[], None]]] = []


def scenario(fn: Callable[[], None]) -> Callable[[], None]:
    SCENARIOS.append((fn.__name__, fn))
    return fn


def check(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _stored(sink: InMemorySink, span_id: str) -> dict[str, Any]:
    for span in sink.spans():
        if span["spanId"] == span_id:
            return span
    raise AssertionError(f"span {span_id} not stored")


@scenario
def full_success_json() -> None:
    receiver, sinks, _ = build_receiver()
    resp = post_json(receiver, build_export_request())
    check(resp.status == 200 and resp.body == {}, f"expected 200 {{}}, got {resp.status} {resp.body}")
    names = sorted(sp["name"] for sp in sinks["default"].spans())
    check(names == ["chat example-chat-model", "execute_tool lookup_order", "invoke_agent demo-hosted-agent"],
          f"unexpected spans {names}")
    chat = attr_map(_stored(sinks["default"], CHAT_SPAN_ID)["attributes"])
    check(chat.get("gen_ai.usage.input_tokens") == "42", "usage attribute lost")
    check(chat.get("gen_ai.response.finish_reasons") == ["tool_calls"], "array attribute lost")


@scenario
def empty_request_success() -> None:
    receiver, sinks, _ = build_receiver()
    for payload in ({}, {"resourceSpans": []}, {"resourceSpans": None}):
        resp = post_json(receiver, payload)
        check(resp.status == 200 and resp.body == {}, f"empty request should succeed: {resp.status}")
    check(not sinks["default"].batches, "nothing should reach the sink")


@scenario
def partial_success_rejects_invalid_span() -> None:
    receiver, sinks, _ = build_receiver()
    payload = build_export_request()
    spans = payload["resourceSpans"][0]["scopeSpans"][0]["spans"]
    spans += [
        make_span("4444444444444444", traceId="not-a-trace-id"),
        make_span("0000000000000000"),
        make_span("5555555555555555", startTimeUnixNano=_nanos(5), endTimeUnixNano=_nanos(1)),
        make_span("6666666666666666", name=""),
    ]
    resp = post_json(receiver, payload)
    partial = resp.body.get("partialSuccess", {})
    check(resp.status == 200, f"partial success is HTTP 200, got {resp.status}")
    check(partial.get("rejectedSpans") == "4", f"rejectedSpans should be '4': {partial}")
    for reason in ("invalid_trace_id", "invalid_span_id", "invalid_timestamps", "missing_name"):
        check(reason in partial.get("errorMessage", ""), f"errorMessage should name {reason}")
    check(len(sinks["default"].spans()) == 3, "valid spans must still be stored")


@scenario
def missing_token_401() -> None:
    receiver, sinks, _ = build_receiver()
    for headers in ({}, {"Authorization": "Basic ZGVtbzpkZW1v"}, {"Authorization": "Bearer "},
                    {"Authorization": "Bearer not-a-token"}):
        resp = post_json(receiver, build_export_request(), headers)
        check(resp.status == 401, f"expected 401 for {list(headers)}, got {resp.status}")
        check(resp.body.get("code") == 16, "error body should carry UNAUTHENTICATED")
        check("WWW-Authenticate" in resp.headers, "401 must include a WWW-Authenticate challenge")
    check(not sinks["default"].batches, "unauthenticated spans must not be stored")


@scenario
def expired_token_401() -> None:
    receiver, _, clock = build_receiver()
    expired = fake_claims(now=clock.now() - 7200, lifetime=3600)
    resp = post_json(receiver, build_export_request(), bearer(expired))
    check(resp.status == 401 and "expired" in resp.body["message"], f"expected expiry 401: {resp.body}")
    within_skew = fake_claims(now=clock.now() - 3660, lifetime=3600)
    check(post_json(receiver, build_export_request(), bearer(within_skew)).status == 200,
          "small clock skew should be tolerated")
    future = fake_claims(now=clock.now() + 3600)
    check(post_json(receiver, {}, bearer(future)).status == 401, "nbf in the future must fail")


@scenario
def wrong_audience_401() -> None:
    receiver, _, _ = build_receiver()
    claims = fake_claims(aud="api://00000000-0000-0000-0000-0000000000bb")
    resp = post_json(receiver, build_export_request(), bearer(claims))
    check(resp.status == 401 and "audience" in resp.body["message"], f"expected audience 401: {resp.body}")
    graph = fake_claims(aud="https://graph.microsoft.com")
    check(post_json(receiver, {}, bearer(graph)).status == 401, "tokens for other APIs must be refused")


@scenario
def untrusted_tenant_401() -> None:
    receiver, _, _ = build_receiver()
    resp = post_json(receiver, build_export_request(), bearer(fake_claims(tid=OTHER_TENANT)))
    check(resp.status == 401 and "tenant" in resp.body["message"], f"expected tenant 401: {resp.body}")
    forged = fake_claims()
    forged["iss"] = "https://issuer.example.com/"
    resp = post_json(receiver, build_export_request(), bearer(forged))
    check(resp.status == 401 and "issuer" in resp.body["message"], f"expected issuer 401: {resp.body}")


@scenario
def v2_token_with_azp_accepted() -> None:
    receiver, sinks, _ = build_receiver()
    claims = fake_claims(version="2.0", aud=AUDIENCE_ID)
    resp = post_json(receiver, build_export_request(), bearer(claims))
    check(resp.status == 200, f"v2 token should be accepted: {resp.status} {resp.body}")
    attrs = sinks["default"].resource_attrs()[0]
    check(attrs["trace_receiver.client_id"] == {"stringValue": DEMO_CLIENT}, "client id should be stamped")


@scenario
def unlisted_client_403() -> None:
    receiver, sinks, _ = build_receiver()
    resp = post_json(receiver, build_export_request(), bearer(fake_claims("someone-else-mi")))
    check(resp.status == 403 and resp.body.get("code") == 7, f"expected 403: {resp.status} {resp.body}")
    check(not sinks["default"].batches, "unlisted clients must not write")


@scenario
def missing_app_role_403() -> None:
    receiver, _, _ = build_receiver()
    for roles in ([], ["Traces.Read"]):
        resp = post_json(receiver, build_export_request(), bearer(fake_claims(roles=roles)))
        check(resp.status == 403 and "Traces.Write" in resp.body["message"], f"expected role 403 for {roles}")


@scenario
def shared_header_dev_only() -> None:
    try:
        SharedHeaderAuthenticator("x-dev-trace-key", "local-dev-only", environment="prod")
        raise AssertionError("shared header must be refused outside dev")
    except ValueError:
        pass
    sink = InMemorySink("dev")
    receiver = TraceReceiver(ReceiverPolicy(name="dev"),
                             SharedHeaderAuthenticator("x-dev-trace-key", "local-dev-only", "dev"),
                             {"dev": [sink]}, FakeClock())
    ok = post_json(receiver, build_export_request(), {"X-Dev-Trace-Key": "local-dev-only"})
    bad = post_json(receiver, build_export_request(), {"X-Dev-Trace-Key": "guess"})
    check(ok.status == 200 and bad.status == 401, f"dev header: ok={ok.status} bad={bad.status}")
    stamped = sink.resource_attrs()[0]["trace_receiver.auth_method"]
    check(stamped == {"stringValue": "shared-header"}, "auth method must be visible downstream")


@scenario
def wrong_path_404() -> None:
    receiver, _, _ = build_receiver()
    for path in ("/v1/traces/v1/traces", "/", "/v1/trace", "/api/traces"):
        resp = post_json(receiver, build_export_request(), path=path)
        check(resp.status == 404 and TRACES_PATH in resp.body["message"], f"expected 404 for {path}")


@scenario
def method_not_allowed_405() -> None:
    receiver, _, _ = build_receiver()
    resp = receiver.handle(HttpRequest("GET", TRACES_PATH, bearer(fake_claims()), b""))
    check(resp.status == 405 and resp.headers.get("Allow") == "POST", f"expected 405: {resp.status}")


@scenario
def protobuf_needs_decoder_415() -> None:
    receiver, _, _ = build_receiver()
    resp = post_json(receiver, None, raw=b"\x0a\x00", content_type=PROTOBUF_TYPE)
    check(resp.status == 415, f"expected 415 for protobuf without a decoder, got {resp.status}")
    check("Collector" in resp.body["message"], "415 should explain the protobuf options")


@scenario
def unsupported_content_type_415() -> None:
    receiver, _, _ = build_receiver()
    check(post_json(receiver, {}, content_type="text/plain").status == 415, "text/plain must be 415")
    resp = post_json(receiver, {}, {**bearer(fake_claims()), "Content-Encoding": "br"})
    check(resp.status == 415, f"unsupported Content-Encoding must be 415, got {resp.status}")
    resp = post_json(receiver, build_export_request(), content_type="application/json; charset=utf-8")
    check(resp.status == 200, "media type parameters should be tolerated")


@scenario
def payload_too_large_413() -> None:
    receiver, sinks, _ = build_receiver(ReceiverPolicy(name="small", max_body_bytes=2048))
    payload = build_export_request(extra_span_attrs=[s("gen_ai.agent.version", "x" * 4096)])
    resp = post_json(receiver, payload)
    check(resp.status == 413 and not exporter_should_retry(413), f"expected non-retryable 413, got {resp.status}")
    check(not sinks["default"].batches, "oversized body must not be stored")


@scenario
def gzip_bomb_413() -> None:
    receiver, _, _ = build_receiver(ReceiverPolicy(name="bounded", max_decompressed_bytes=64 * 1024))
    bomb = b"{" + b" " * (4 * 1024 * 1024) + b"}"
    check(len(gzip.compress(bomb)) < receiver.policy.max_body_bytes, "bomb must pass the wire-size check")
    resp = post_json(receiver, None, raw=bomb, gzip_body=True)
    check(resp.status == 413, f"decompressed size must be bounded, got {resp.status}")


@scenario
def gzip_roundtrip_success() -> None:
    receiver, sinks, _ = build_receiver()
    check(post_json(receiver, build_export_request(), gzip_body=True).status == 200, "gzip body should work")
    check(len(sinks["default"].spans()) == 3, "gzip spans should be stored")
    headers = {**bearer(fake_claims()), "Content-Encoding": "gzip"}
    check(post_json(receiver, None, headers, raw=b"not gzip").status == 400, "invalid gzip must be 400")
    truncated = gzip.compress(json.dumps(build_export_request()).encode("utf-8"))[:-12]
    check(post_json(receiver, None, headers, raw=truncated).status == 400, "truncated gzip must be 400")


@scenario
def malformed_json_400() -> None:
    receiver, _, _ = build_receiver()
    bad_bodies = [b"{not json", b"[1, 2]", b"\xff\xfe", json.dumps({"resourceSpans": {}}).encode(),
                  json.dumps({"resourceSpans": [{"scopeSpans": "x"}]}).encode()]
    for raw in bad_bodies:
        resp = post_json(receiver, None, raw=raw)
        check(resp.status == 400 and resp.body.get("code") == 3, f"expected 400 for {raw[:24]!r}, got {resp.status}")


@scenario
def span_limit_partial_success() -> None:
    receiver, sinks, _ = build_receiver(ReceiverPolicy(name="tight", max_spans_per_request=2))
    resp = post_json(receiver, build_export_request())
    partial = resp.body.get("partialSuccess", {})
    check(resp.status == 200 and partial.get("rejectedSpans") == "1", f"expected 1 rejected: {resp.body}")
    check("over_span_limit" in partial.get("errorMessage", ""), "errorMessage should name the bound")
    check(len(sinks["default"].spans()) == 2, "spans within the bound should be stored")


@scenario
def content_dropped_by_default() -> None:
    receiver, sinks, _ = build_receiver()
    post_json(receiver, build_export_request())
    stored = sinks["default"].dumps()
    for marker in ("Where is order", "helpful order assistant", "order_id", "shipped"):
        check(marker not in stored, f"content leaked into the sink: {marker!r}")
    check(receiver.last_report is not None and receiver.last_report.content_dropped == 5,
          "five content attributes should be dropped")
    receiver, sinks, _ = build_receiver(ReceiverPolicy(name="debug", capture_content=True, max_value_chars=24))
    post_json(receiver, build_export_request())
    kept = attr_map(_stored(sinks["default"], AGENT_SPAN_ID)["attributes"]).get("gen_ai.input.messages", "")
    check(len(kept) == 24, "captured content must still be length-bounded")


@scenario
def identifiers_hashed_stably() -> None:
    receiver, sinks, _ = build_receiver()
    post_json(receiver, build_export_request(user_id="user-4711"))
    post_json(receiver, build_export_request(user_id="user-4711"))
    hashes = [attr_map(sp["attributes"]).get("enduser.id") for sp in sinks["default"].spans()
              if sp["spanId"] == AGENT_SPAN_ID]
    check(len(hashes) == 2 and hashes[0] == hashes[1], "same identifier must hash the same way")
    check(str(hashes[0]).startswith("hmac-sha256:"), "identifier should be keyed-hashed")
    check("user-4711" not in sinks["default"].dumps(), "raw identifier must not be stored")
    other = AttributeRedactor(ReceiverPolicy(name="other", hmac_key=b"another-demo-key"))
    check(other.hash_identifier("user-4711") != hashes[0], "a different key must give a different hash")


@scenario
def unknown_attribute_dropped_and_reported() -> None:
    receiver, sinks, _ = build_receiver()
    post_json(receiver, build_export_request(extra_span_attrs=[s("app.debug.dump", "raw request headers")]))
    check("raw request headers" not in sinks["default"].dumps(), "non-allowlisted value leaked")
    report = receiver.last_report
    check(report is not None and "app.debug.dump" in report.dropped_keys, "dropped key should be reported")


@scenario
def attribute_value_truncated() -> None:
    receiver, sinks, _ = build_receiver(ReceiverPolicy(name="short", max_value_chars=16))
    post_json(receiver, build_export_request(agent_name="agent-with-a-very-long-name"))
    attrs = attr_map(_stored(sinks["default"], AGENT_SPAN_ID)["attributes"])
    check(attrs["gen_ai.agent.name"] == "agent-with-a-ver", f"unexpected truncation {attrs['gen_ai.agent.name']}")
    check(receiver.last_report is not None and receiver.last_report.truncated >= 1, "truncation should be counted")


@scenario
def attribute_count_bounded() -> None:
    receiver, sinks, _ = build_receiver(ReceiverPolicy(name="few", max_attributes_per_span=3))
    post_json(receiver, build_export_request())
    check(all(len(sp["attributes"]) <= 3 for sp in sinks["default"].spans()), "attribute count not bounded")
    check(receiver.last_report is not None and receiver.last_report.over_attribute_limit > 0, "overflow not counted")


@scenario
def nested_and_bytes_values_dropped() -> None:
    receiver, sinks, _ = build_receiver()
    extra = [{"key": "gen_ai.agent.version", "value": {"kvlistValue": {"values": [s("k", "hidden")]}}},
             {"key": "server.port", "value": {"bytesValue": "aGlkZGVu"}},
             {"key": "gen_ai.tool.type", "value": {"intValue": "not-a-number"}},
             {"value": {"stringValue": "no key"}}]
    post_json(receiver, build_export_request(extra_span_attrs=extra))
    stored = sinks["default"].dumps()
    check("hidden" not in stored and "aGlkZGVu" not in stored, "nested or bytes values leaked")
    check(receiver.last_report is not None and receiver.last_report.unsupported_value == 4,
          f"expected 4 unsupported values: {receiver.last_report and receiver.last_report.to_dict()}")


@scenario
def status_and_exception_messages_dropped() -> None:
    receiver, sinks, _ = build_receiver()
    payload = build_export_request()
    tool = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][2]
    tool["status"] = {"code": 2, "message": "lookup failed for customer C-1001"}
    tool["events"] = [{"name": "exception", "timeUnixNano": _nanos(450), "attributes": [
        s("exception.type", "LookupError"),
        s("exception.message", "customer C-1001 not found"),
        s("exception.stacktrace", "Traceback ... C-1001"),
    ]}]
    post_json(receiver, payload)
    stored = _stored(sinks["default"], TOOL_SPAN_ID)
    check(stored["status"] == {"code": 2}, f"status should keep only the code: {stored['status']}")
    check(attr_map(stored["events"][0]["attributes"]) == {"exception.type": "LookupError"}, "exception detail kept")
    check("C-1001" not in sinks["default"].dumps(), "free-text error detail leaked")
    check(receiver.last_report is not None and receiver.last_report.status_messages_dropped == 1,
          "status message drop should be counted")


@scenario
def resource_attributes_not_used_for_routing() -> None:
    receiver, sinks, _ = build_receiver(clients={"team-a-agent-mi": "team-a", "team-b-agent-mi": "team-b"})
    spoof = [s("trace_receiver.route", "team-b"), s("tenant.id", "team-b")]
    payload = build_export_request("team-b-agent", extra_resource_attrs=spoof)
    resp = post_json(receiver, payload, bearer(fake_claims("team-a-agent-mi")))
    check(resp.status == 200, f"expected 200, got {resp.status}")
    check(not sinks["team-b"].batches, "payload attributes must never select the destination")
    attrs = sinks["team-a"].resource_attrs()[0]
    check(attrs["trace_receiver.route"] == {"stringValue": "team-a"}, "route must come from the token")
    check("tenant.id" not in attrs, "non-allowlisted resource attribute should be dropped")
    routes = [a for rs in sinks["team-a"].batches[0]["resourceSpans"]
              for a in rs["resource"]["attributes"] if a["key"] == "trace_receiver.route"]
    check(len(routes) == 1, "spoofed receiver-namespace attributes must be stripped")


@scenario
def trace_context_preserved() -> None:
    receiver, sinks, _ = build_receiver()
    payload = build_export_request(trace_id=DEMO_TRACE_ID.upper())
    agent = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][0]
    agent["links"] = [{"traceId": "0af7651916cd43dd8448eb211c80319c", "spanId": "b7ad6b7169203331"}]
    post_json(receiver, payload)
    stored = _stored(sinks["default"], AGENT_SPAN_ID)
    check(stored["traceId"] == DEMO_TRACE_ID, "trace id should be normalized to lowercase, never hashed")
    check(stored["parentSpanId"] == GATEWAY_SPAN_ID, "gateway parent span id must survive")
    check(stored["links"][0]["spanId"] == "b7ad6b7169203331", "span links should survive")
    conv = attr_map(stored["attributes"]).get("gen_ai.conversation.id")
    check(conv == "conv-demo-0001", "default policy keeps the conversation id for correlation")


@scenario
def rate_limited_429_retry_after() -> None:
    clients = {DEMO_CLIENT: "default", "second-agent-mi": "default"}
    receiver, _, clock = build_receiver(ReceiverPolicy(name="rl", requests_per_window=2), clients)
    for _ in range(2):
        check(post_json(receiver, {}).status == 200, "requests within the window should pass")
    resp = post_json(receiver, {})
    retry_after = resp.headers.get("Retry-After", "")
    check(resp.status == 429 and retry_after.isdigit() and resp.body.get("code") == 8, f"expected 429: {resp}")
    check(post_json(receiver, {}, bearer(fake_claims("second-agent-mi"))).status == 200, "limits are per client")
    clock.advance(int(retry_after))
    check(post_json(receiver, {}).status == 200, "request after Retry-After should pass")


@scenario
def sink_unavailable_503_retry_after() -> None:
    receiver, sinks, _ = build_receiver()
    sinks["default"].available = False
    resp = post_json(receiver, build_export_request())
    check(resp.status == 503 and resp.headers.get("Retry-After") == "5", f"expected 503 + Retry-After: {resp}")
    check(exporter_should_retry(resp.status), "503 must be retryable")
    sinks["default"].available = True
    check(post_json(receiver, build_export_request()).status == 200, "retry should succeed once the sink recovers")


@scenario
def logs_and_metrics_discarded_by_policy() -> None:
    receiver, sinks, _ = build_receiver()
    logs = {"resourceLogs": [{"scopeLogs": [{"logRecords": [{"body": {"stringValue": "x"}}, {}]}]}]}
    resp = post_json(receiver, logs, path=LOGS_PATH)
    check(resp.status == 200 and resp.body["partialSuccess"]["rejectedLogRecords"] == "2", f"logs: {resp.body}")
    metrics = {"resourceMetrics": [{"scopeMetrics": [{"metrics": [{"name": "m", "gauge": {
        "dataPoints": [{"asInt": "1"}, {"asInt": "2"}, {"asInt": "3"}]}}]}]}]}
    resp = post_json(receiver, metrics, path=METRICS_PATH)
    check(resp.body["partialSuccess"]["rejectedDataPoints"] == "3", f"metrics: {resp.body}")
    check(not sinks["default"].batches, "discarded signals must not reach trace sinks")
    strict, _, _ = build_receiver(ReceiverPolicy(name="strict", other_signals="reject"))
    check(post_json(strict, logs, path=LOGS_PATH).status == 404, "reject policy should answer 404")


@scenario
def exporter_retry_semantics() -> None:
    check(exporter_backoff_seconds(429, {"retry-after": "7"}, 1) == 7.0, "Retry-After must win")
    check(exporter_backoff_seconds(503, {}, 1) == 1.0, "first backoff should use the base")
    check(exporter_backoff_seconds(503, {}, 3) == 4.0, "backoff should grow exponentially")
    check(exporter_backoff_seconds(504, {}, 20) == 30.0, "backoff should be capped")
    for status in (200, 400, 401, 403, 404, 413, 415, 500):
        check(exporter_backoff_seconds(status, {}, 1) is None, f"{status} must not be retried")


@scenario
def redaction_report_has_no_values() -> None:
    receiver, _, _ = build_receiver()
    post_json(receiver, build_export_request(user_id="user-4711",
                                             extra_span_attrs=[s("app.secret.note", "do-not-store")]))
    report = json.dumps(receiver.last_report.to_dict() if receiver.last_report else {})
    for value in ("Where is order", "user-4711", "do-not-store"):
        check(value not in report, f"report leaked a value: {value!r}")
    for key in ("gen_ai.input.messages", "app.secret.note"):
        check(key in report, f"report should list dropped key {key}")


@scenario
def production_stubs_raise() -> None:
    trust = TrustConfig(frozenset({TENANT}), frozenset({AUDIENCE_URI}), {DEMO_CLIENT: "default"})
    principal = Principal(DEMO_CLIENT, TENANT, "default", "entra-bearer")
    stubs: list[Callable[[], Any]] = [
        lambda: EntraTokenValidator(trust).authenticate({}, 0.0),
        lambda: ProtobufTraceDecoder().decode(b"", "traces"),
        lambda: BlobSink().write({}, principal),
        lambda: LogAnalyticsSink().write({}, principal),
    ]
    for stub in stubs:
        try:
            stub()
            raise AssertionError("production stub should raise NotImplementedError")
        except NotImplementedError:
            pass
    try:
        make_server(build_receiver()[0], host="0.0.0.0", port=0)
        raise AssertionError("non-loopback bind must be refused")
    except ValueError:
        pass


@scenario
def policy_guards() -> None:
    bad_policies = [dict(max_spans_per_request=0), dict(other_signals="keep"),
                    dict(capture_content=True, content_capture_allowed=False), dict(hmac_key=b"")]
    for kwargs in bad_policies:
        try:
            ReceiverPolicy(name="bad", **kwargs)
            raise AssertionError(f"policy {kwargs} should be rejected")
        except ValueError:
            pass


@scenario
def serve_roundtrip_loopback() -> None:
    receiver, sinks, _ = build_receiver()
    server = make_server(receiver, port=0)
    host, port = server.server_address[0], server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
    thread.start()

    def raw_request(method: str, headers: dict[str, str], body: bytes | None) -> tuple[int, dict[str, str]]:
        conn = http.client.HTTPConnection(host, port, timeout=5)
        try:
            conn.putrequest(method, TRACES_PATH)
            for key, value in headers.items():
                conn.putheader(key, value)
            conn.endheaders(body)
            resp = conn.getresponse()
            payload = json.loads(resp.read() or b"{}")
            return resp.status, payload
        finally:
            conn.close()

    try:
        auth = bearer(fake_claims())
        body = json.dumps(build_export_request()).encode("utf-8")
        status, payload = raw_request("POST", {"Content-Type": JSON_TYPE, "Content-Length": str(len(body)), **auth},
                                      body)
        check(status == 200 and payload == {}, f"loopback JSON export failed: {status} {payload}")
        check(len(sinks["default"].spans()) == 3, "loopback spans should be stored")
        proto = b"\x0a\x00"
        status, _ = raw_request("POST", {"Content-Type": PROTOBUF_TYPE, "Content-Length": str(len(proto)), **auth},
                                proto)
        check(status == 415, f"loopback protobuf should be 415, got {status}")
        too_big = str(receiver.policy.max_body_bytes + 1)
        status, _ = raw_request("POST", {"Content-Type": JSON_TYPE, "Content-Length": too_big, **auth}, None)
        check(status == 413, f"declared oversize should be refused before reading, got {status}")
        status, _ = raw_request("POST", {"Content-Type": JSON_TYPE, **auth}, None)
        check(status == 411, f"missing Content-Length should be 411, got {status}")
        status, _ = raw_request("GET", auth, None)
        check(status == 405, f"GET should be 405, got {status}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)
    check(not thread.is_alive(), "server thread should stop")
    try:
        socket.create_connection((host, port), timeout=1).close()
        still_listening = True
    except OSError:
        still_listening = False
    check(not still_listening, "listener should be closed after shutdown")


def run_self_test() -> int:
    failures = 0
    for name, fn in SCENARIOS:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as exc:  # noqa: BLE001 - report every scenario
            failures += 1
            print(f"FAIL  {name}: {exc.__class__.__name__}: {exc}")
    print(f"\n{len(SCENARIOS) - failures}/{len(SCENARIOS)} scenarios passed")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Domain Packs (fictional organizations)
# ---------------------------------------------------------------------------


def contoso_policy(**overrides: Any) -> ReceiverPolicy:
    kwargs: dict[str, Any] = dict(
        name="contoso-retail", environment="prod",
        span_allowlist=DEFAULT_SPAN_ALLOWLIST | {"contoso.store.region"},
        hashed_attributes=DEFAULT_HASHED_ATTRIBUTES | {"contoso.customer.id", "contoso.order.id"},
    )
    kwargs.update(overrides)
    return ReceiverPolicy(**kwargs)


def fabrikam_policy(**overrides: Any) -> ReceiverPolicy:
    kwargs: dict[str, Any] = dict(
        name="fabrikam-health", environment="prod", content_capture_allowed=False,
        hmac_key=b"fabrikam-demo-key-not-a-secret", max_value_chars=128,
        hashed_attributes=DEFAULT_HASHED_ATTRIBUTES
        | {"gen_ai.conversation.id", "fabrikam.patient.id", "fabrikam.mrn"},
    )
    kwargs.update(overrides)
    return ReceiverPolicy(**kwargs)


def northwind_policy(**overrides: Any) -> ReceiverPolicy:
    kwargs: dict[str, Any] = dict(
        name="northwind-logistics", environment="prod",
        span_allowlist=DEFAULT_SPAN_ALLOWLIST | {"northwind.shipment.id", "northwind.depot"},
    )
    kwargs.update(overrides)
    return ReceiverPolicy(**kwargs)


def _is_hashed(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("hmac-sha256:")


def domain_contoso_retail() -> list[tuple[str, bool]]:
    client = "contoso-shopping-assistant-mi"
    receiver, sinks, _ = build_receiver(contoso_policy(), {client: "contoso-retail"})
    extra = [s("contoso.customer.id", "CUST-0042"), s("contoso.order.id", "ORD-1234"),
             s("contoso.store.region", "west"), s("contoso.debug.payload", '{"cart":"full basket"}')]
    resp = post_json(receiver, build_export_request("contoso-shopping-assistant", extra_span_attrs=extra),
                     bearer(fake_claims(client)))
    sink = sinks["contoso-retail"]
    attrs = attr_map(_stored(sink, AGENT_SPAN_ID)["attributes"])
    stored = sink.dumps()
    return [
        ("export accepted", resp.status == 200),
        ("customer and order ids hashed",
         _is_hashed(attrs.get("contoso.customer.id")) and _is_hashed(attrs.get("contoso.order.id"))),
        ("raw customer and order ids absent", "CUST-0042" not in stored and "ORD-1234" not in stored),
        ("store region kept for reporting", attrs.get("contoso.store.region") == "west"),
        ("debug payload dropped", "contoso.debug.payload" not in attrs and "full basket" not in stored),
        ("conversation id kept for correlation", attrs.get("gen_ai.conversation.id") == "conv-demo-0001"),
        ("prompt and completion content dropped", "Where is order" not in stored),
    ]


def domain_fabrikam_health() -> list[tuple[str, bool]]:
    try:
        fabrikam_policy(capture_content=True)
        capture_locked = False
    except ValueError:
        capture_locked = True
    client = "fabrikam-care-navigator-mi"
    receiver, sinks, _ = build_receiver(fabrikam_policy(), {client: "fabrikam-health"})
    extra = [s("fabrikam.patient.id", "patient-0001"), s("fabrikam.mrn", "mrn-000123"),
             s("fabrikam.ward", "ward-cardiology")]
    payload = build_export_request("fabrikam-care-navigator", conversation_id="conv-fab-0001",
                                   user_id="clinician-0007", extra_span_attrs=extra)
    tool = payload["resourceSpans"][0]["scopeSpans"][0]["spans"][2]
    tool["status"] = {"code": 2, "message": "no appointment for patient-0001"}
    tool["events"] = [{"name": "exception", "timeUnixNano": _nanos(450), "attributes": [
        s("exception.type", "LookupError"), s("exception.message", "patient-0001 has no record")]}]
    resp = post_json(receiver, payload, bearer(fake_claims(client)))
    sink = sinks["fabrikam-health"]
    agent = attr_map(_stored(sink, AGENT_SPAN_ID)["attributes"])
    tool_stored = _stored(sink, TOOL_SPAN_ID)
    stored = sink.dumps()
    report = json.dumps(receiver.last_report.to_dict() if receiver.last_report else {})
    raw_values = ("patient-0001", "mrn-000123", "clinician-0007", "conv-fab-0001", "ward-cardiology")
    return [
        ("content capture cannot be enabled", capture_locked),
        ("export accepted", resp.status == 200),
        ("patient identifiers hashed",
         _is_hashed(agent.get("fabrikam.patient.id")) and _is_hashed(agent.get("fabrikam.mrn"))),
        ("conversation and user ids hashed",
         _is_hashed(agent.get("gen_ai.conversation.id")) and _is_hashed(agent.get("enduser.id"))),
        ("ward attribute dropped", "fabrikam.ward" not in agent),
        ("no raw sensitive value in sink", not any(v in stored for v in raw_values)),
        ("no raw sensitive value in redaction report", not any(v in report for v in raw_values)),
        ("prompt and tool content dropped", "Where is order" not in stored and "order_id" not in stored),
        ("error kept as type and status code only",
         tool_stored["status"] == {"code": 2}
         and attr_map(tool_stored["events"][0]["attributes"]) == {"exception.type": "LookupError"}),
        ("domain hash key differs from the default",
         agent.get("enduser.id") != AttributeRedactor(ReceiverPolicy()).hash_identifier("clinician-0007")),
    ]


def domain_northwind_logistics() -> list[tuple[str, bool]]:
    clients = {"dispatch-agent-mi": "northwind-emea", "planner-agent-mi": "northwind-amer"}
    receiver, sinks, _ = build_receiver(northwind_policy(), clients)
    spoof = [s("northwind.tenant", "amer"), s("trace_receiver.route", "northwind-amer")]
    extra = [s("northwind.shipment.id", "SHP-0001"), s("northwind.depot", "depot-7")]
    payload = build_export_request("planner-agent", extra_span_attrs=extra, extra_resource_attrs=spoof)
    resp = post_json(receiver, payload, bearer(fake_claims("dispatch-agent-mi")))
    emea, amer = sinks["northwind-emea"], sinks["northwind-amer"]
    resource = emea.resource_attrs()[0] if emea.batches else {}
    routes = [a for b in emea.batches for rs in b["resourceSpans"]
              for a in rs["resource"]["attributes"] if a["key"] == "trace_receiver.route"]
    agent = attr_map(_stored(emea, AGENT_SPAN_ID)["attributes"]) if emea.batches else {}
    amer_before = len(amer.batches)
    planner = post_json(receiver, build_export_request("planner-agent"), bearer(fake_claims("planner-agent-mi")))
    return [
        ("export accepted", resp.status == 200),
        ("spoofed service.name and tenant ignored for routing",
         len(emea.batches) == 1 and amer_before == 0),
        ("route stamped from the token", resource.get("trace_receiver.route") == {"stringValue": "northwind-emea"}),
        ("spoofed receiver attribute stripped", len(routes) == 1),
        ("tenant resource attribute dropped", "northwind.tenant" not in resource),
        ("operational ids kept", agent.get("northwind.shipment.id") == "SHP-0001"
         and agent.get("northwind.depot") == "depot-7"),
        ("planner identity routes to its own destination", planner.status == 200 and len(amer.batches) == 1),
    ]


DOMAINS: dict[str, Callable[[], list[tuple[str, bool]]]] = {
    "contoso-retail": domain_contoso_retail,
    "fabrikam-health": domain_fabrikam_health,
    "northwind-logistics": domain_northwind_logistics,
}


def run_domains(selected: str) -> int:
    names = list(DOMAINS) if selected == "all" else [selected]
    failures = 0
    for name in names:
        print(f"== {name} ==")
        for check_name, ok in DOMAINS[name]():
            failures += 0 if ok else 1
            print(f"  {'PASS' if ok else 'FAIL'}  {check_name}")
    print(f"\n{'all domain checks passed' if not failures else f'{failures} domain check(s) failed'}")
    return 1 if failures else 0


# ---------------------------------------------------------------------------
# Demo
# ---------------------------------------------------------------------------


def _span_names(batch: dict[str, Any]) -> list[str]:
    return [sp.get("name", "") for rs in batch.get("resourceSpans", [])
            for ss in rs.get("scopeSpans", []) for sp in ss.get("spans", [])]


def run_demo() -> int:
    client = "contoso-shopping-assistant-mi"
    receiver, sinks, clock = build_receiver(contoso_policy(requests_per_window=1), {client: "contoso-retail"})
    sink = sinks["contoso-retail"]

    def headers() -> dict[str, str]:
        return bearer(fake_claims(client, now=clock.now()))

    extra = [s("contoso.customer.id", "CUST-0042"), s("contoso.store.region", "west"),
             s("contoso.debug.payload", '{"cart":"full basket"}')]
    payload = build_export_request("contoso-shopping-assistant", user_id="shopper-0042", extra_span_attrs=extra)
    sent_attrs = sum(len(sp["attributes"]) for sp in payload["resourceSpans"][0]["scopeSpans"][0]["spans"])

    print("1) The hosted agent exports one turn as OTLP/JSON over HTTP")
    print(f"   POST {TRACES_PATH}  spans={_span_names(payload)}  span attributes sent={sent_attrs}")
    print(f"   traceId={DEMO_TRACE_ID} (same trace as the gateway span {GATEWAY_SPAN_ID})")

    first = post_json(receiver, payload, headers())
    print(f"\n2) Receiver authenticated the exporter, bounded, redacted and stored the batch -> HTTP {first.status}"
          f" {json.dumps(first.body)}")
    report = receiver.last_report.to_dict() if receiver.last_report else {}
    print("   Redaction report (counts and keys only, never values):")
    print("\n".join("   " + line for line in json.dumps(report, indent=2).splitlines()))
    stored = attr_map(_stored(sink, AGENT_SPAN_ID)["attributes"])
    print("   Stored invoke_agent attributes:")
    for key in sorted(stored):
        print(f"     {key} = {stored[key]}")
    trusted = {k: v.get("stringValue") for k, v in sink.resource_attrs()[0].items()
               if k.startswith(RECEIVER_NAMESPACE)}
    print(f"   Receiver-stamped resource attributes (from the token, not the payload): {trusted}")

    print("\n3) A second export inside the same rate window is throttled; the exporter honours Retry-After")
    retried = export_with_retry(receiver, payload, headers, clock, log=lambda m: print("   " + m))
    print(f"   Sink now holds {len(sink.batches)} batches with the same span ids; a real sink upserts on"
          " (traceId, spanId) so the retry does not duplicate spans.")

    print("\n4) A batch with one malformed span gets OTLP partial success; the exporter must not retry it")
    clock.advance(receiver.policy.window_seconds)
    mixed = build_export_request("contoso-shopping-assistant")
    mixed["resourceSpans"][0]["scopeSpans"][0]["spans"].append(make_span("4444444444444444", traceId="not-hex"))
    partial = post_json(receiver, mixed, headers())
    print(f"   HTTP {partial.status} {json.dumps(partial.body)}"
          f"  retry={exporter_should_retry(partial.status)}")

    ok = (first.status == 200 and retried.status == 200 and partial.status == 200
          and "partialSuccess" in partial.body)
    print(f"\ndemo {'completed' if ok else 'FAILED'}")
    return 0 if ok else 1


# ---------------------------------------------------------------------------
# Local serve / send (loopback only, fake tokens)
# ---------------------------------------------------------------------------


class ConsoleSink:
    """Prints a one-line summary per accepted batch; stores nothing."""

    def __init__(self, name: str = "console"):
        self.name = name

    def write(self, batch: dict[str, Any], principal: Principal) -> None:
        names = _span_names(batch)
        print(f"[{principal.route}] accepted {len(names)} span(s) from {principal.client_id}: "
              + ", ".join(names[:5]), flush=True)


def run_serve(port: int) -> int:
    trust = TrustConfig(frozenset({TENANT}), frozenset({AUDIENCE_URI, AUDIENCE_ID}), {DEMO_CLIENT: "default"})
    receiver = TraceReceiver(ReceiverPolicy(name="local-serve"), FakeEntraTokenValidator(trust),
                             {"default": [ConsoleSink()]}, SystemClock())
    server = make_server(receiver, port=port)
    bound_port = server.server_address[1]
    print(f"listening on http://{LOOPBACK_HOST}:{bound_port}{TRACES_PATH} (loopback only; accepts fake tokens)")
    print(f"send a fake export batch from another terminal:  python {sys.argv[0]} --send --port {bound_port}")
    print("press Ctrl+C to stop", flush=True)
    try:
        server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
    return 0


def run_send(port: int) -> int:
    body = json.dumps(build_export_request()).encode("utf-8")
    headers = {"Content-Type": JSON_TYPE, "Content-Length": str(len(body)),
               **bearer(fake_claims(now=time.time()))}
    conn = http.client.HTTPConnection(LOOPBACK_HOST, port, timeout=10)
    try:
        conn.request("POST", TRACES_PATH, body=body, headers=headers)
        resp = conn.getresponse()
        print(f"HTTP {resp.status} {resp.read().decode('utf-8', 'replace')}")
        return 0 if resp.status == 200 else 1
    except OSError as exc:
        print(f"could not reach http://{LOOPBACK_HOST}:{port}{TRACES_PATH}: {exc}")
        return 1
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Lightweight OTLP/HTTP trace receiver reference (offline).")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--self-test", action="store_true", help="run named scenarios and print PASS/FAIL")
    mode.add_argument("--domain", choices=[*DOMAINS, "all"], help="run Domain Pack checks")
    mode.add_argument("--demo", action="store_true", help="narrate one export batch end to end")
    mode.add_argument("--serve", action="store_true", help="listen on 127.0.0.1 for local verification")
    mode.add_argument("--send", action="store_true", help="send one fake export batch to a local --serve")
    parser.add_argument("--port", type=int, default=4318, help="loopback port for --serve/--send")
    args = parser.parse_args(argv)
    if args.self_test:
        return run_self_test()
    if args.domain:
        return run_domains(args.domain)
    if args.demo:
        return run_demo()
    if args.serve:
        return run_serve(args.port)
    return run_send(args.port)


if __name__ == "__main__":
    sys.exit(main())
