"""Hosted agent side: point the agent's model client at the streaming model gateway.

ILLUSTRATIVE. It shows the parts that change when a Foundry hosted agent calls
its model through the gateway instead of a Foundry model deployment:

- The model client's base URL is the gateway, and the model name is a gateway
  model alias from the policy, not a deployment name.
- Every model call carries a fresh Entra access token for the gateway's app ID
  URI, obtained with the agent's own identity. No API key exists anywhere.
- Every model call carries a W3C traceparent and a new Idempotency-Key.
- SDK retries are off. If you add automatic retries, resend with a new key and
  backoff only when the gateway marks the error retryable. After a mid-stream
  error frame, re-running the turn is a deliberate agent decision
  (docs/decisions/0002-retry-only-before-first-byte.md).

The agent identity holds the gateway's Gateway.Invoke app role and no model role
on the Foundry account, so the gateway is the only way it can reach a model.

The agent loop and the hosting adapter are examples. Use your own framework;
keep GatewayAuth and the client settings. Foundry hosted agents and the
langchain-azure-ai hosting adapter are observed in preview; verify against
current docs before you depend on the adapter's module path, port or protocol.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import sys
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable, Iterator, Mapping
from urllib.parse import urlsplit

import httpx

log = logging.getLogger("agent")

REQUIRED = ("GATEWAY_BASE_URL", "GATEWAY_APP_ID_URI", "GATEWAY_MODEL_ALIAS")
PLACEHOLDER = re.compile(r"<[^<>]+>")
LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
SECRET_WORDS = ("KEY", "SECRET", "PASSWORD", "TOKEN")
# Variables that would let a client authenticate with a key or secret instead of the identity.
KEY_VARIABLES = ("OPENAI_API_KEY", "AZURE_OPENAI_API_KEY", "AZURE_CLIENT_SECRET")
DEFAULT_PORT = "8088"  # observed default for hosted agent containers in preview; verify

SYSTEM_PROMPT = (
    "You are the Contoso shopping assistant. Answer questions about orders. "
    "Use the order_status tool to look up an order; never guess a status."
)


class ConfigError(Exception):
    def __init__(self, problems: list[str]) -> None:
        super().__init__("; ".join(problems))
        self.problems = problems


@dataclass(frozen=True)
class Settings:
    gateway_base_url: str
    gateway_scope: str
    model_alias: str
    managed_identity_client_id: str | None
    port: int


def load_settings(environ: Mapping[str, str]) -> Settings:
    """Read configuration. Lists every problem at once and refuses to start on any."""
    problems: list[str] = []
    values = {name: (environ.get(name) or "").strip() for name in REQUIRED}
    for name, value in values.items():
        if not value:
            problems.append(f"{name} is not set")
        elif PLACEHOLDER.search(value):
            problems.append(f"{name} still contains a <placeholder>")

    base_url = values["GATEWAY_BASE_URL"].rstrip("/")
    if base_url and not PLACEHOLDER.search(base_url) and not _acceptable_url(base_url):
        problems.append("GATEWAY_BASE_URL must be an https URL (plain http only for loopback testing)")

    for name in sorted(environ):
        if name.startswith("GATEWAY_") and any(word in name for word in SECRET_WORDS):
            problems.append(f"{name} looks like a secret; the agent authenticates to the gateway with its identity only")
        elif name in KEY_VARIABLES:
            problems.append(f"{name} is set; remove it so no client can fall back to a key or secret")

    port_raw = (environ.get("PORT") or DEFAULT_PORT).strip()
    port = 0
    if not port_raw.isdigit() or not 0 < int(port_raw) < 65536:
        problems.append("PORT must be a TCP port number")
    else:
        port = int(port_raw)

    if problems:
        raise ConfigError(problems)
    return Settings(
        gateway_base_url=base_url,
        gateway_scope=values["GATEWAY_APP_ID_URI"].rstrip("/") + "/.default",
        model_alias=values["GATEWAY_MODEL_ALIAS"],
        managed_identity_client_id=(environ.get("AZURE_CLIENT_ID") or "").strip() or None,
        port=port,
    )


def _acceptable_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
    except ValueError:
        return False
    if not parts.hostname:
        return False
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname in LOOPBACK_HOSTS)


# ---------------------------------------------------------------------------
# Identity, trace context and idempotency on every model call
# ---------------------------------------------------------------------------

def build_token_provider(settings: Settings) -> Callable[[], str]:
    """Return a callable that yields a cached, auto-refreshed token for the gateway."""
    from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider

    if settings.managed_identity_client_id:
        credential: Any = ManagedIdentityCredential(client_id=settings.managed_identity_client_id)
    else:
        # In the hosted-agent runtime this resolves the identity the platform assigns
        # to the agent; locally it falls back to a developer sign-in. How the runtime
        # exposes the agent identity is observed in preview; verify against current docs.
        credential = DefaultAzureCredential()
    return get_bearer_token_provider(credential, settings.gateway_scope)


def inject_trace_context(headers: httpx.Headers) -> None:
    """Add traceparent (and tracestate) for the active span, if OpenTelemetry is present."""
    try:
        from opentelemetry.propagate import inject
    except ImportError:
        return
    carrier: dict[str, str] = {}
    inject(carrier)
    for name, value in carrier.items():
        # setdefault: if httpx instrumentation already added a client span's context, keep it.
        headers.setdefault(name, value)


class GatewayAuth(httpx.Auth):
    """Sets the gateway token, trace context and an Idempotency-Key on each request.

    The token provider caches tokens and refreshes them before expiry, so the
    per-call cost is normally a cache lookup. The async flow runs it in a worker
    thread because a refresh is a blocking network call.
    """

    def __init__(self, token_provider: Callable[[], str]) -> None:
        self._token_provider = token_provider

    def sync_auth_flow(self, request: httpx.Request) -> Iterator[httpx.Request]:
        self._apply(request, self._token_provider())
        yield request

    async def async_auth_flow(self, request: httpx.Request) -> AsyncIterator[httpx.Request]:
        token = await asyncio.to_thread(self._token_provider)
        self._apply(request, token)
        yield request

    @staticmethod
    def _apply(request: httpx.Request, token: str) -> None:
        request.headers.pop("api-key", None)
        # Replaces the placeholder bearer value the model SDK sets from api_key.
        request.headers["Authorization"] = f"Bearer {token}"
        # One key per HTTP request. With SDK retries off, a new request means a new
        # attempt by the agent. An intermediary that replays this exact request sends
        # the same key, and the gateway answers 409 instead of billing twice.
        request.headers.setdefault("Idempotency-Key", uuid.uuid4().hex)
        inject_trace_context(request.headers)


# ---------------------------------------------------------------------------
# Model client and agent (example framework)
# ---------------------------------------------------------------------------

def build_model(settings: Settings, auth: GatewayAuth) -> Any:
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=settings.model_alias,
        base_url=settings.gateway_base_url,
        # Required by the SDK and never sent: GatewayAuth overwrites the header.
        api_key="unused-entra-auth",
        streaming=True,
        stream_usage=True,
        use_responses_api=False,  # the gateway serves Chat Completions only
        max_retries=0,  # 0 = SDK retries off; the agent retries whole turns (ADR 0002)
        # The SDK's own request timeout applies. Set timeout= from your measurements if
        # you need a different one; keep it longer than the gateway's keepalive interval.
        http_client=httpx.Client(auth=auth),
        http_async_client=httpx.AsyncClient(auth=auth),
    )


def build_agent(model: Any) -> Any:
    from langchain.agents import create_agent
    from langchain_core.tools import tool

    @tool
    def order_status(order_id: str) -> str:
        """Look up the status of a Contoso order by its order ID."""
        # Example tool with canned data. A real tool calls your order service with
        # its own identity and checks that the end user may see this order.
        return f"Order {order_id} is packed and waiting for pickup."

    return create_agent(model, tools=[order_status], system_prompt=SYSTEM_PROMPT)


def main() -> int:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    logging.getLogger("azure").setLevel(logging.WARNING)
    try:
        settings = load_settings(os.environ)
    except ConfigError as err:
        for problem in err.problems:
            log.error("configuration problem: %s", problem)
        return 2

    auth = GatewayAuth(build_token_provider(settings))
    graph = build_agent(build_model(settings, auth))
    log.info("model calls go to the gateway as alias %s", settings.model_alias)

    # Hosting adapter: serves the agent over the protocol the hosted-agent runtime
    # invokes. Module path and behaviour observed in preview; verify against current docs.
    from langchain_azure_ai.agents.hosting import ResponsesHostServer

    ResponsesHostServer(graph).run(port=settings.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
