"""Build an Azure OpenAI client for one team from that team's Foundry project connection.

The team never holds a model key or a gateway key in configuration. The flow is:

1. Authenticate to the team's Foundry project with Microsoft Entra ID
   (``DefaultAzureCredential``). Only members of that team have a role on the project.
2. Read the project connection ``ai-gateway-team-<team>`` with credentials. It holds
   the team's API Management subscription key and the gateway endpoint.
3. Create an ``AzureOpenAI`` client that targets the gateway with that key. The gateway
   enforces the team's token budget, strips the key and calls the model with its own
   managed identity.

The key lives only in memory. It is never logged, printed or written to disk.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass
from typing import Any, Mapping, Optional

from azure.ai.projects import AIProjectClient
from azure.core.credentials import TokenCredential
from azure.core.exceptions import ClientAuthenticationError, HttpResponseError, ResourceNotFoundError
from azure.identity import DefaultAzureCredential
from openai import AuthenticationError, AzureOpenAI

DEFAULT_API_VERSION = "2024-10-21"
DEFAULT_DEPLOYMENT = "chat"
GATEWAY_HEADERS = ("x-tokens-consumed", "x-remaining-tokens", "retry-after")


class TeamAccessError(RuntimeError):
    """The caller cannot read the team's gateway connection."""


class GatewayConnectionError(RuntimeError):
    """The connection exists but is not a usable gateway connection."""


@dataclass(frozen=True)
class TeamSettings:
    """Non-secret settings for one team. Every value can come from the environment."""

    team: str
    project_endpoint: str
    connection_name: str
    api_version: str = DEFAULT_API_VERSION
    deployment: str = DEFAULT_DEPLOYMENT

    @classmethod
    def from_env(cls, team: str, environ: Optional[Mapping[str, str]] = None) -> "TeamSettings":
        """Read ``TEAM_<TEAM>_PROJECT_ENDPOINT`` and ``TEAM_<TEAM>_CONNECTION``.

        The connection name defaults to ``ai-gateway-team-<team>``, which matches
        ``infra/modules/team.bicep``.
        """
        env = os.environ if environ is None else environ
        normalized = team.strip().lower()
        if not normalized:
            raise ValueError("team must not be empty")
        prefix = f"TEAM_{normalized.upper().replace('-', '_')}"
        endpoint = env.get(f"{prefix}_PROJECT_ENDPOINT", "").strip()
        if not endpoint:
            raise ValueError(
                f"{prefix}_PROJECT_ENDPOINT is not set. Use the projectEndpoint deployment "
                "output for this team (see src/.env.sample)."
            )
        return cls(
            team=normalized,
            project_endpoint=endpoint,
            connection_name=env.get(f"{prefix}_CONNECTION", f"ai-gateway-team-{normalized}").strip(),
            api_version=env.get("OPENAI_API_VERSION", DEFAULT_API_VERSION).strip(),
            deployment=env.get("MODEL_DEPLOYMENT", DEFAULT_DEPLOYMENT).strip(),
        )


@dataclass(frozen=True)
class GatewayEndpoint:
    """Gateway target and key read from a project connection. ``repr`` hides the key."""

    target: str
    api_key: str

    def __repr__(self) -> str:
        return f"GatewayEndpoint(target={self.target!r}, api_key='***')"


def read_gateway_endpoint(
    settings: TeamSettings, credential: Optional[TokenCredential] = None
) -> GatewayEndpoint:
    """Read the team's gateway connection, including its key, from the Foundry project."""
    credential = credential or DefaultAzureCredential()
    project = AIProjectClient(endpoint=settings.project_endpoint, credential=credential)
    try:
        connection = project.connections.get(settings.connection_name, include_credentials=True)
    except ResourceNotFoundError as exc:
        raise TeamAccessError(
            f"Connection '{settings.connection_name}' was not found in the team '{settings.team}' "
            "project. Check TEAM_<TEAM>_CONNECTION and the deployment outputs."
        ) from exc
    except ClientAuthenticationError as exc:
        raise TeamAccessError(
            "Could not get a Microsoft Entra token. Sign in with 'az login' or configure a "
            "managed identity."
        ) from exc
    except HttpResponseError as exc:
        if exc.status_code in (401, 403):
            raise TeamAccessError(
                f"Your identity has no access to the team '{settings.team}' project. Only members "
                "of that team (Foundry User on the project) can read its gateway connection."
            ) from exc
        raise

    credentials: Any = getattr(connection, "credentials", None)
    api_key = getattr(credentials, "api_key", None)
    target = (getattr(connection, "target", None) or "").rstrip("/")
    if not api_key:
        raise GatewayConnectionError(
            f"Connection '{settings.connection_name}' returned no API key. It must use the "
            "ApiKey auth type and you must be allowed to read its credentials."
        )
    if not target.startswith("https://"):
        raise GatewayConnectionError(
            f"Connection '{settings.connection_name}' has no HTTPS target."
        )
    return GatewayEndpoint(target=target, api_key=api_key)


def build_openai_client(
    settings: TeamSettings, endpoint: GatewayEndpoint, max_retries: int = 2
) -> AzureOpenAI:
    """Create an ``AzureOpenAI`` client that talks to the gateway with the team key.

    ``x-team`` is a diagnostic header only. The gateway counts tokens by product
    (that is, by key) and removes ``x-team`` before calling the model.
    """
    return AzureOpenAI(
        azure_endpoint=endpoint.target,
        api_key=endpoint.api_key,
        api_version=settings.api_version,
        default_headers={"x-team": settings.team},
        max_retries=max_retries,
    )


def gateway_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Return the gateway's budget headers that are present on a response."""
    lowered = {k.lower(): v for k, v in headers.items()}
    return {name: lowered[name] for name in GATEWAY_HEADERS if name in lowered}


@dataclass(frozen=True)
class ChatResult:
    text: str
    status_code: int
    gateway_headers: dict[str, str]


class TeamGatewayClient:
    """A team-scoped chat client that re-reads its connection once after a 401.

    A 401 from the gateway usually means the key was rotated. The new key is in the
    project connection after the platform team redeploys, so the client fetches it
    once and retries. A second 401 is raised to the caller.
    """

    def __init__(
        self,
        settings: TeamSettings,
        credential: Optional[TokenCredential] = None,
        max_retries: int = 2,
    ) -> None:
        self.settings = settings
        self._credential = credential or DefaultAzureCredential()
        self._max_retries = max_retries
        self._lock = threading.Lock()
        self._client: Optional[AzureOpenAI] = None

    @property
    def openai(self) -> AzureOpenAI:
        with self._lock:
            if self._client is None:
                self._client = self._new_client()
            return self._client

    def _new_client(self) -> AzureOpenAI:
        endpoint = read_gateway_endpoint(self.settings, self._credential)
        return build_openai_client(self.settings, endpoint, self._max_retries)

    def refresh(self) -> None:
        with self._lock:
            self._client = self._new_client()

    def chat(self, prompt: str, max_tokens: int = 200) -> ChatResult:
        try:
            return self._chat_once(prompt, max_tokens)
        except AuthenticationError:
            self.refresh()
            return self._chat_once(prompt, max_tokens)

    def _chat_once(self, prompt: str, max_tokens: int) -> ChatResult:
        raw = self.openai.chat.completions.with_raw_response.create(
            model=self.settings.deployment,
            messages=[{"role": "user", "content": prompt}],
            max_tokens=max_tokens,
        )
        completion = raw.parse()
        text = completion.choices[0].message.content if completion.choices else ""
        return ChatResult(
            text=text or "",
            status_code=raw.status_code,
            gateway_headers=gateway_headers(raw.headers),
        )


def list_project_connections(
    settings: TeamSettings, credential: Optional[TokenCredential] = None
) -> list[dict[str, str]]:
    """List connections in the team's project without reading any credentials."""
    credential = credential or DefaultAzureCredential()
    project = AIProjectClient(endpoint=settings.project_endpoint, credential=credential)
    rows = []
    for connection in project.connections.list():
        rows.append(
            {
                "name": str(getattr(connection, "name", "")),
                "type": str(getattr(connection, "type", "")),
                "target": str(getattr(connection, "target", "")),
            }
        )
    return rows
