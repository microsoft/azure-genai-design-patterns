"""Illustrative entrypoint skeleton for a hosted agent container.

ILLUSTRATIVE. Not run in CI and not imported by any test.

Requires packages for the production paths (all imports are guarded, so the
file still loads without them):
  - azure-identity                    (managed identity token acquisition)
  - azure-monitor-opentelemetry       (OpenTelemetry export to Application Insights)
  - opentelemetry-api                 (tracer and trace context)
  - the hosting adapter for your agent framework (serves the agent protocol)

The stdlib HTTP server below exists only to keep this skeleton self-contained.
In production, register handle_turn() and handle_health() with the hosting
adapter your framework provides; it owns the protocol, the port and the paths.

What this file demonstrates:
  1. Configuration from AGENT_-prefixed environment variables, validated at startup.
  2. One credential per process and a per-audience token cache with refresh skew.
  3. OpenTelemetry wired from the platform-injected connection string.
  4. Correlation from the caller (x-client-correlation-id, traceparent) and the
     platform call identifier forwarded verbatim.
  5. Idempotent turns keyed by x-client-idempotency-key.
  6. Graceful shutdown on SIGTERM with a telemetry flush.
  7. A cheap liveness handler that touches no dependencies.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import signal
import sys
import threading
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Protocol

try:  # Production path. Absent locally is fine; token calls then fail clearly.
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
except ImportError:  # pragma: no cover - illustrative
    DefaultAzureCredential = None  # type: ignore[assignment]
    ManagedIdentityCredential = None  # type: ignore[assignment]

try:
    from azure.monitor.opentelemetry import configure_azure_monitor
except ImportError:  # pragma: no cover - illustrative
    configure_azure_monitor = None  # type: ignore[assignment]

try:
    from opentelemetry import trace as otel_trace
    from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator
except ImportError:  # pragma: no cover - illustrative
    otel_trace = None  # type: ignore[assignment]
    TraceContextTextMapPropagator = None  # type: ignore[assignment]


LOG = logging.getLogger("hosted_agent")

# Token audiences. Verify the Foundry audience against current documentation.
MODEL_SCOPE = "https://cognitiveservices.azure.com/.default"
FOUNDRY_SCOPE = "https://ai.azure.com/.default"
MONITOR_SCOPE = "https://monitor.azure.com/.default"


# ---------------------------------------------------------------------------
# 1. Configuration
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Settings:
    model_endpoint: str
    model_deployment: str
    cosmos_endpoint: str
    gateway_url: str
    agent_version: str
    log_level: str
    port: int
    token_refresh_skew_seconds: int

    @property
    def cosmos_scope(self) -> str:
        return self.cosmos_endpoint.rstrip("/") + "/.default"


REQUIRED = ("AGENT_MODEL_ENDPOINT", "AGENT_MODEL_DEPLOYMENT", "AGENT_COSMOS_ENDPOINT")


def load_settings(env: dict[str, str] | None = None) -> Settings:
    """Read AGENT_ variables, fail fast with every missing name listed."""
    env = dict(os.environ if env is None else env)
    missing = [name for name in REQUIRED if not env.get(name)]
    if missing:
        raise SystemExit("missing required configuration: " + ", ".join(missing))
    for name in env:
        upper = name.upper()
        if upper.startswith("AGENT_") and any(s in upper for s in ("KEY", "SECRET", "PASSWORD")):
            raise SystemExit(f"refusing secret-like variable {name}; use managed identity or Key Vault")
    return Settings(
        model_endpoint=env["AGENT_MODEL_ENDPOINT"],
        model_deployment=env["AGENT_MODEL_DEPLOYMENT"],
        cosmos_endpoint=env["AGENT_COSMOS_ENDPOINT"],
        gateway_url=env.get("AGENT_GATEWAY_URL", ""),
        agent_version=env.get("AGENT_VERSION_LABEL", "unknown"),
        log_level=env.get("AGENT_LOG_LEVEL", "INFO"),
        port=int(env.get("AGENT_PORT", "8088")),
        token_refresh_skew_seconds=int(env.get("AGENT_TOKEN_REFRESH_SKEW_SECONDS", "300")),
    )


# ---------------------------------------------------------------------------
# 2. One credential, per-audience token cache
# ---------------------------------------------------------------------------

def build_credential() -> Any:
    """Create exactly one credential for the process lifetime."""
    if ManagedIdentityCredential is None:
        raise RuntimeError("azure-identity is not installed")
    client_id = os.environ.get("AZURE_CLIENT_ID")
    if client_id:
        return ManagedIdentityCredential(client_id=client_id)
    return DefaultAzureCredential(exclude_interactive_browser_credential=True)


class TokenCache:
    """Caches one access token per audience and refreshes before expiry."""

    def __init__(self, credential: Any, skew_seconds: int) -> None:
        self._credential = credential
        self._skew = skew_seconds
        self._tokens: dict[str, tuple[str, float]] = {}
        self._lock = threading.Lock()

    def get(self, scope: str) -> str:
        now = time.time()
        with self._lock:
            cached = self._tokens.get(scope)
            if cached and cached[1] - self._skew > now:
                return cached[0]
            token = self._credential.get_token(scope)
            self._tokens[scope] = (token.token, float(token.expires_on))
            return token.token

    def bearer(self, scope: str) -> dict[str, str]:
        return {"Authorization": "Bearer " + self.get(scope)}


# ---------------------------------------------------------------------------
# 3. Telemetry
# ---------------------------------------------------------------------------

class _NoopSpan:
    def __enter__(self) -> "_NoopSpan":
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def set_attribute(self, key: str, value: Any) -> None:
        return None


def setup_telemetry(settings: Settings) -> None:
    logging.basicConfig(stream=sys.stdout, level=settings.log_level,
                        format="%(asctime)s %(levelname)s %(name)s %(message)s")
    if configure_azure_monitor is not None and os.environ.get("APPLICATIONINSIGHTS_CONNECTION_STRING"):
        # The connection string is injected by the platform (observed in preview; verify).
        configure_azure_monitor()
        LOG.info("telemetry: azure monitor exporter configured")
    else:
        LOG.info("telemetry: exporter not configured, spans are no-ops")


def start_span(name: str, headers: dict[str, str]) -> Any:
    if otel_trace is None or TraceContextTextMapPropagator is None:
        return _NoopSpan()
    parent = TraceContextTextMapPropagator().extract(carrier=headers)
    return otel_trace.get_tracer("hosted_agent").start_as_current_span(name, context=parent)


def flush_telemetry() -> None:
    if otel_trace is None:
        return
    provider = otel_trace.get_tracer_provider()
    flush = getattr(provider, "force_flush", None)
    if callable(flush):
        flush()


def short_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# 5. Idempotency
# ---------------------------------------------------------------------------

class IdempotencyStore(Protocol):
    def get(self, key: str, conversation_key: str) -> dict[str, Any] | None: ...
    def put(self, record: dict[str, Any]) -> None: ...


class LocalIdempotencyStore:
    """Local development only. Production uses the Cosmos DB 'idempotency'
    container with the runtime identity and the Cosmos DB data-plane role."""

    def __init__(self) -> None:
        self._items: dict[tuple[str, str], dict[str, Any]] = {}
        self._lock = threading.Lock()

    def get(self, key: str, conversation_key: str) -> dict[str, Any] | None:
        with self._lock:
            return self._items.get((conversation_key, key))

    def put(self, record: dict[str, Any]) -> None:
        with self._lock:
            self._items[(record["conversation_key"], record["key"])] = record


# ---------------------------------------------------------------------------
# Turn handling
# ---------------------------------------------------------------------------

class Agent:
    def __init__(self, settings: Settings, tokens: TokenCache | None, store: IdempotencyStore) -> None:
        self.settings = settings
        self.tokens = tokens
        self.store = store

    def handle_health(self) -> tuple[int, dict[str, Any]]:
        # Liveness only. Do not call models, Cosmos DB or tools here.
        return 200, {"status": "ok", "version": self.settings.agent_version}

    def handle_turn(self, body: dict[str, Any], headers: dict[str, str]) -> tuple[int, dict[str, Any]]:
        lower = {k.lower(): v for k, v in headers.items()}
        correlation_id = lower.get("x-client-correlation-id", "")
        idem_key = lower.get("x-client-idempotency-key", "")
        call_id = lower.get("x-agent-foundry-call-id", "")  # opaque; forward verbatim
        conversation_key = str((body.get("metadata") or {}).get("conversation_key", ""))
        if not idem_key or not conversation_key:
            return 400, {"error": "x-client-idempotency-key and metadata.conversation_key are required"}

        with start_span("agent.turn", lower) as span:
            span.set_attribute("agent.version", self.settings.agent_version)
            span.set_attribute("client.correlation_id", correlation_id)
            span.set_attribute("conversation.key_hash", short_hash(conversation_key))

            existing = self.store.get(idem_key, conversation_key)
            if existing and existing.get("status") == "completed":
                span.set_attribute("idempotency.replayed", True)
                return 200, {"output": existing["result"], "replayed": True}

            span.set_attribute("idempotency.replayed", False)
            self.store.put({"id": idem_key, "key": idem_key, "conversation_key": conversation_key,
                            "status": "in_progress", "agent_version": self.settings.agent_version})
            output = self.run_agent_turn(body, call_id)
            self.store.put({"id": idem_key, "key": idem_key, "conversation_key": conversation_key,
                            "status": "completed", "agent_version": self.settings.agent_version,
                            "result": output})
            # Log identifiers and sizes, never prompt or completion content.
            LOG.info("turn completed corr=%s conv=%s input_chars=%d",
                     correlation_id, short_hash(conversation_key), len(str(body.get("input", ""))))
            return 200, {"output": output, "replayed": False}

    def run_agent_turn(self, body: dict[str, Any], call_id: str) -> str:
        """Replace with your agent loop. Model calls use the model audience:

            headers = self.tokens.bearer(MODEL_SCOPE)
            headers["x-agent-foundry-call-id"] = call_id   # when calling Foundry services

        and target self.settings.model_endpoint with deployment
        self.settings.model_deployment. Reuse one HTTP client per process.
        """
        return "skeleton response; wire your agent framework here"


# ---------------------------------------------------------------------------
# Local HTTP surface (replace with the hosting adapter) and 6. shutdown
# ---------------------------------------------------------------------------

def make_handler(agent: Agent) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, payload: dict[str, Any]) -> None:
            data = json.dumps(payload).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            if self.path == "/healthz":
                self._send(*agent.handle_health())
            else:
                self._send(404, {"error": "not found"})

        def do_POST(self) -> None:  # noqa: N802
            if self.path != "/responses":
                self._send(404, {"error": "not found"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except json.JSONDecodeError:
                self._send(400, {"error": "invalid json"})
                return
            self._send(*agent.handle_turn(body, dict(self.headers.items())))

        def log_message(self, fmt: str, *args: Any) -> None:
            LOG.debug(fmt, *args)

    return Handler


def main() -> None:
    settings = load_settings()
    setup_telemetry(settings)
    tokens = TokenCache(build_credential(), settings.token_refresh_skew_seconds) \
        if ManagedIdentityCredential is not None else None
    agent = Agent(settings, tokens, LocalIdempotencyStore())
    server = ThreadingHTTPServer(("0.0.0.0", settings.port), make_handler(agent))

    def on_sigterm(signum: int, frame: Any) -> None:
        LOG.info("shutdown signal received; draining")
        threading.Thread(target=server.shutdown, daemon=True).start()

    signal.signal(signal.SIGTERM, on_sigterm)
    LOG.info("listening on port %d version=%s", settings.port, settings.agent_version)
    try:
        server.serve_forever()
    finally:
        server.server_close()
        flush_telemetry()
        LOG.info("shutdown complete")


if __name__ == "__main__":
    main()
