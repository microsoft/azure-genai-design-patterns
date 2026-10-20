#!/usr/bin/env python3
"""Delegated identity, end to end: validate -> exchange (OBO) -> cache -> call MCP.

This module is the reference for the identity path described in
``docs/authentication-and-identity.md``. It contains the pieces that every
"agent acts as the signed-in user against governed enterprise data" design
needs, independent of business domain:

* ``InboundTokenValidator``  - what an MCP server (or any middle tier) runs on
  every request: signature, issuer (v1 and v2), audience, tenant pinning,
  delegated scope / app role, and calling-client allow-list.
* ``OboTokenBroker``         - On-Behalf-Of exchange via MSAL, with an
  assertion-bound cache, single-flight de-duplication, CAE claims passthrough
  and a precise mapping from Entra ID error codes to remediation classes.
* ``StreamableHttpMcpClient`` - a small, dependency-light MCP Streamable HTTP
  client (initialize -> session -> tools/call, JSON or SSE responses) that
  re-exchanges once on 401, forwards ``insufficient_claims`` challenges, and
  re-initializes once on session expiry.
* ``remediation_activity``   - turns an ``AuthError`` into the Activity a Teams
  / Microsoft 365 Copilot user should see (OAuth card, consent guidance, or a
  reference id) instead of a stack trace.
* ``verify_two_user_isolation`` - the release gate proving that the data plane
  is filtered by the *user*, not by the service identity.

Where each piece runs::

    Teams / Microsoft 365 Copilot client
        |  Bot Connector JWT (iss=https://api.botframework.com, aud=<agent app id>)
        v                                              validated by the Agents SDK
    Custom Engine Agent
        |  SSO token   aud=api://botid-<agent app id>  scp=access_as_user
        |  OboTokenBroker.exchange(sso, ["api://<mcp app id>/Tools.Invoke"])   hop A
        |  StreamableHttpMcpClient  ->  Authorization: Bearer <token A>
        v
    MCP server
        |  InboundTokenValidator.validate(token A)
        |     aud=<mcp app id>  scp=Tools.Invoke  azp=<agent app id>  tid=<home tenant>
        |  OboTokenBroker.exchange(token A, [<downstream scope>])            hop B
        v
    Data platform (Unity Catalog, SQL, Snowflake, Graph, CRM) applies row and
    column security to the *user* identified by token B.

Run ``python obo_mcp_client.py`` for an offline demonstration. It generates an
RSA key locally, mints real RS256 JWTs from a fake identity platform, and talks
to an in-process MCP server through ``httpx.MockTransport``. Nothing leaves
the machine. Optional dependencies: ``msal`` (production exchange),
``PyJWT[crypto]`` (validation), ``httpx`` (MCP transport).

Production notes:
* MSAL Python's ``acquire_token_on_behalf_of`` always calls the token endpoint;
  it does not look up its own cache for OBO. Caching is therefore this
  module's responsibility, and the key MUST include a hash of the incoming
  assertion (see ``obo_cache_key``).
* The official ``mcp`` SDK client (``mcp.client.streamable_http``) is a fine
  alternative to ``StreamableHttpMcpClient``; the small client here exists to
  make the auth retry semantics explicit and testable.
"""

from __future__ import annotations

import asyncio
import base64
import collections
import hashlib
import json
import logging
import os
import re
import time
import uuid
from dataclasses import dataclass
from typing import Any, AsyncIterator, Awaitable, Callable, Mapping, Protocol, Sequence

try:  # production token exchange
    import msal  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional
    msal = None  # type: ignore[assignment]

try:  # inbound validation (PyJWT[crypto])
    import jwt  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional
    jwt = None  # type: ignore[assignment]

try:  # MCP Streamable HTTP transport
    import httpx  # type: ignore[import-not-found]
except ImportError:  # pragma: no cover - optional
    httpx = None  # type: ignore[assignment]


log = logging.getLogger("obo_mcp_client")

ENTRA_AUTHORITY_HOST = "https://login.microsoftonline.com"
FIC_AUDIENCE = "api://AzureADTokenExchange"
OAUTH_CARD_CONTENT_TYPE = "application/vnd.microsoft.card.oauth"
DEFAULT_MCP_PROTOCOL_VERSION = "2025-06-18"

# Representative downstream scopes. ``.default`` asks for every permission the
# middle tier was statically consented for on that resource; it cannot be
# mixed with named scopes for the same resource in one request.
DOWNSTREAM_SCOPE_EXAMPLES: Mapping[str, tuple[str, ...]] = {
    # Azure Databricks first-party resource id (public, identical in every tenant).
    "databricks": ("2ff814a6-3304-4ab8-85cb-cd0e6f879c1d/.default",),
    "azure_sql": ("https://database.windows.net/.default",),
    "microsoft_graph_mail": ("https://graph.microsoft.com/Mail.Read",),
    "dataverse_crm": ("https://{org}.crm.dynamics.com/user_impersonation",),
    # Snowflake External OAuth: the resource is an app registration you own.
    "snowflake": ("api://{snowflake-resource-app}/session:role-any",),
    "mcp_server": ("api://{mcp-server-app-id}/Tools.Invoke",),
}


# ---------------------------------------------------------------------------
# Errors. ``user_message`` is safe for end users; ``str(err)`` is for logs and
# never contains a token or the raw ``error_description`` (which can carry
# user principal names and internal trace data).
# ---------------------------------------------------------------------------


class AuthError(Exception):
    user_message = "I couldn't reach that data source with your credentials."
    retryable = False

    def __init__(
        self,
        detail: str = "",
        *,
        error_codes: Sequence[int] = (),
        correlation_id: str | None = None,
    ) -> None:
        super().__init__(detail or self.__class__.__name__)
        self.error_codes = tuple(error_codes)
        self.correlation_id = correlation_id


class InvalidInboundToken(AuthError):
    user_message = "Your session could not be verified. Please sign in again."


class ClaimsChallengeRequired(AuthError):
    """Conditional Access / CAE needs the user to satisfy more claims (MFA,
    compliant device, authentication context). Only an interactive sign-in can
    fix this; a silent retry cannot."""

    user_message = "Your organization requires an additional sign-in step for this data."

    def __init__(self, detail: str = "", *, claims: str | None = None, **kw: Any) -> None:
        super().__init__(detail, **kw)
        self.claims = claims


class ConsentRequired(AuthError):
    user_message = (
        "An administrator needs to approve this agent's access to that data source. "
        "Share the reference below with your IT team."
    )

    def __init__(self, detail: str = "", *, scopes: Sequence[str] = (), **kw: Any) -> None:
        super().__init__(detail, **kw)
        self.scopes = tuple(scopes)


class AssertionExpired(AuthError):
    user_message = "Your sign-in expired. Please sign in again."


class AccessBlockedByPolicy(AuthError):
    user_message = (
        "Your organization's access policy blocks this request from your current device "
        "or location. Retrying will not help; contact your IT team if this is unexpected."
    )


class OboMisconfigured(AuthError):
    user_message = "The agent is not configured correctly for this data source. The issue has been logged."


class TransientAuthFailure(AuthError):
    retryable = True
    user_message = "The sign-in service is temporarily unavailable. Please try again shortly."


class DownstreamUnauthorized(AuthError):
    user_message = "The data service rejected your credentials even after a refresh. The issue has been logged."


class McpProtocolError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(f"MCP error {code}: {message}")
        self.code = code
        self.message = message


_CLAIMS_CODES = frozenset({50072, 50074, 50076, 50079, 50158})
_BLOCKED_CODES = frozenset({53000, 53001, 53002, 53003, 53004})
_CONSENT_CODES = frozenset({65001, 65004})
_EXPIRED_CODES = frozenset({500133, 50173})  # assertion expired / grant revoked (password reset, sign-out)
_MISCONFIG_CODES = frozenset({50013, 70011, 700016, 700027, 7000215, 7000222})


def classify_token_error(result: Mapping[str, Any], scopes: Sequence[str] = ()) -> AuthError:
    """Map an MSAL error dict to a remediation class.

    Precedence: an explicit ``claims`` member wins (it is the authoritative
    signal for step-up), then AADSTS codes, then the OAuth ``error`` string.
    """
    codes = tuple(int(c) for c in (result.get("error_codes") or ()))
    error = str(result.get("error") or "unknown_error")
    kw: dict[str, Any] = {"error_codes": codes, "correlation_id": result.get("correlation_id")}
    detail = f"{error} codes={list(codes)}"
    code_set = set(codes)
    if result.get("claims"):
        return ClaimsChallengeRequired(detail, claims=str(result["claims"]), **kw)
    if code_set & _CLAIMS_CODES:
        return ClaimsChallengeRequired(detail, claims=None, **kw)
    if code_set & _BLOCKED_CODES:
        return AccessBlockedByPolicy(detail, **kw)
    if code_set & _CONSENT_CODES:
        return ConsentRequired(detail, scopes=scopes, **kw)
    if code_set & _EXPIRED_CODES:
        return AssertionExpired(detail, **kw)
    if code_set & _MISCONFIG_CODES:
        return OboMisconfigured(detail, **kw)
    if error in {"temporarily_unavailable", "server_error"}:
        return TransientAuthFailure(detail, **kw)
    if error == "interaction_required":
        return ClaimsChallengeRequired(detail, claims=None, **kw)
    if error == "invalid_grant":
        return AssertionExpired(detail, **kw)
    return OboMisconfigured(detail, **kw)


# ---------------------------------------------------------------------------
# Small helpers: base64url, redaction, unverified routing claims.
# ---------------------------------------------------------------------------


def _b64url_decode(segment: str) -> bytes:
    return base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4))


def _sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def redact_token(token: str) -> str:
    """A stable, non-reversible handle that lets you correlate a token across
    log lines without ever logging it."""
    return f"<jwt sha256:{_sha256(token)[:12]}>"


_LOGGABLE_CLAIMS = ("iss", "aud", "tid", "ver", "scp", "roles", "azp", "appid", "idtyp", "acrs", "exp", "iat")


def loggable_claims(claims: Mapping[str, Any]) -> dict[str, Any]:
    """Allow-list projection of claims for diagnostics; ``oid`` is hashed,
    names / UPN / email / IP address are dropped."""
    out = {k: claims[k] for k in _LOGGABLE_CLAIMS if k in claims}
    if "oid" in claims:
        out["oid_hash"] = _sha256(str(claims["oid"]))[:16]
    return out


@dataclass(frozen=True)
class TokenIdentity:
    tenant_id: str
    object_id: str


def unverified_identity(token: str) -> TokenIdentity:
    """Read ``tid``/``oid`` WITHOUT verifying the signature.

    Use only for cache keying of an assertion that was already validated - by
    the channel (the SSO token arrives over an authenticated Bot Connector
    call) or by ``InboundTokenValidator``. Never authorize on this result.
    """
    parts = token.split(".")
    if len(parts) != 3:
        raise InvalidInboundToken("assertion is not a compact JWS")
    try:
        payload = json.loads(_b64url_decode(parts[1]))
    except (ValueError, json.JSONDecodeError) as exc:
        raise InvalidInboundToken("assertion payload is not JSON") from exc
    tid, oid = payload.get("tid"), payload.get("oid")
    if not tid or not oid:
        raise InvalidInboundToken("assertion lacks tid/oid (app-only tokens cannot be exchanged OBO)")
    return TokenIdentity(str(tid), str(oid))


# ---------------------------------------------------------------------------
# Inbound validation (MCP server side, or any middle tier).
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class InboundPolicy:
    tenant_id: str
    # v2 access tokens carry the resource's *client id* as ``aud``; v1 tokens
    # carry whatever the caller requested (often the App ID URI). Accept both.
    audiences: tuple[str, ...]
    required_scopes: frozenset[str] = frozenset()  # delegated: any-of in ``scp``
    required_app_roles: frozenset[str] = frozenset()  # app-only: any-of in ``roles``
    allowed_client_app_ids: frozenset[str] = frozenset()  # ``azp`` (v2) / ``appid`` (v1)
    allow_app_only: bool = False
    leeway_seconds: int = 120
    authority_host: str = ENTRA_AUTHORITY_HOST

    @property
    def issuers(self) -> list[str]:
        return [
            f"{self.authority_host}/{self.tenant_id}/v2.0",
            f"https://sts.windows.net/{self.tenant_id}/",
        ]

    @property
    def jwks_uri(self) -> str:
        return f"{self.authority_host}/{self.tenant_id}/discovery/v2.0/keys"


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    object_id: str
    client_app_id: str
    scopes: frozenset[str]
    roles: frozenset[str]
    auth_context: frozenset[str]  # ``acrs`` - Conditional Access authentication contexts satisfied
    delegated: bool
    token_version: str

    @property
    def key(self) -> str:
        return f"{self.tenant_id}:{self.object_id}"


class InboundTokenValidator:
    """Validates bearer tokens presented to an MCP server.

    Every failure raises ``InvalidInboundToken`` with a *generic* message so
    the HTTP response never tells an attacker which check failed; the specific
    reason goes to the server log.
    """

    def __init__(self, policy: InboundPolicy, *, key_resolver: Callable[[str], Any] | None = None) -> None:
        if jwt is None:
            raise RuntimeError("pip install 'PyJWT[crypto]' to validate tokens")
        self._policy = policy
        if key_resolver is None:
            # PyJWKClient fetches with blocking urllib; keys are cached, so the
            # first call per process (or per rotation) is the only slow one.
            # Call ``validate`` via ``asyncio.to_thread`` on hot async paths.
            jwks = jwt.PyJWKClient(policy.jwks_uri, cache_keys=True, lifespan=3600)
            key_resolver = lambda token: jwks.get_signing_key_from_jwt(token).key  # noqa: E731
        self._resolve_key = key_resolver

    def validate(self, token: str) -> Principal:
        p = self._policy
        try:
            header = jwt.get_unverified_header(token)
            if header.get("alg") != "RS256":
                raise InvalidInboundToken(f"unexpected alg {header.get('alg')!r}")
            claims = jwt.decode(
                token,
                self._resolve_key(token),
                algorithms=["RS256"],
                audience=list(p.audiences),
                issuer=p.issuers,
                leeway=p.leeway_seconds,
                options={"require": ["exp", "iat", "iss", "aud"]},
            )
        except InvalidInboundToken:
            raise
        except Exception as exc:  # PyJWTError, key lookup failures, malformed input
            raise InvalidInboundToken(f"jwt rejected: {type(exc).__name__}") from exc

        if claims.get("tid") != p.tenant_id:
            raise InvalidInboundToken("tenant mismatch")
        client = str(claims.get("azp") or claims.get("appid") or "")
        if p.allowed_client_app_ids and client not in p.allowed_client_app_ids:
            raise InvalidInboundToken("calling client not allow-listed")

        scopes = frozenset(str(claims.get("scp", "")).split())
        roles = frozenset(claims.get("roles") or ())
        delegated = "scp" in claims
        if delegated:
            if p.required_scopes and not (scopes & p.required_scopes):
                raise InvalidInboundToken("required delegated scope missing")
            if not claims.get("oid"):
                raise InvalidInboundToken("delegated token without oid")
        else:
            if not p.allow_app_only:
                raise InvalidInboundToken("app-only token not accepted on a delegated endpoint")
            if p.required_app_roles and not (roles & p.required_app_roles):
                raise InvalidInboundToken("required app role missing")

        acrs = claims.get("acrs") or ()
        return Principal(
            tenant_id=str(claims["tid"]),
            object_id=str(claims.get("oid", "")),
            client_app_id=client,
            scopes=scopes,
            roles=roles,
            auth_context=frozenset([acrs] if isinstance(acrs, str) else acrs),
            delegated=delegated,
            token_version=str(claims.get("ver", "")),
        )


# ---------------------------------------------------------------------------
# OBO broker and its cache.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AccessToken:
    token: str
    expires_on: float
    scopes: tuple[str, ...]

    def __repr__(self) -> str:  # never print a bearer token by accident
        return f"AccessToken({redact_token(self.token)}, expires_on={int(self.expires_on)}, scopes={self.scopes})"


class TokenCache(Protocol):
    async def get(self, key: str) -> AccessToken | None: ...

    async def set(self, key: str, value: AccessToken, ttl_seconds: float) -> None: ...

    async def delete_prefix(self, prefix: str) -> int: ...


class MemoryTokenCache:
    """Bounded LRU with per-entry TTL. Per-replica only.

    For a distributed cache (Redis / Cosmos DB), keep the same key layout,
    encrypt values at rest with an envelope key from Key Vault, set the
    server-side TTL to the value passed to ``set``, and maintain a per-user
    index (``obo-index:{tid}:{oid}``) so ``delete_prefix`` does not need SCAN.
    Store access tokens only - never MSAL refresh tokens.
    """

    def __init__(self, max_entries: int = 10_000, clock: Callable[[], float] = time.time) -> None:
        self._data: collections.OrderedDict[str, tuple[float, AccessToken]] = collections.OrderedDict()
        self._max = max_entries
        self._clock = clock

    async def get(self, key: str) -> AccessToken | None:
        item = self._data.get(key)
        if item is None:
            return None
        deadline, value = item
        if deadline <= self._clock():
            self._data.pop(key, None)
            return None
        self._data.move_to_end(key)
        return value

    async def set(self, key: str, value: AccessToken, ttl_seconds: float) -> None:
        self._data[key] = (self._clock() + ttl_seconds, value)
        self._data.move_to_end(key)
        while len(self._data) > self._max:
            self._data.popitem(last=False)

    async def delete_prefix(self, prefix: str) -> int:
        doomed = [k for k in self._data if k.startswith(prefix)]
        for k in doomed:
            del self._data[k]
        return len(doomed)


def obo_cache_key(tenant_id: str, object_id: str, assertion: str, scopes: Sequence[str]) -> str:
    """``obo:{tid}:{oid}:{sha256(assertion)}:{scope-hash}``

    * The assertion hash binds the cached token to the exact incoming token.
      A token minted before a step-up (no MFA claim) is never served after it,
      and a revoked or signed-out session cannot keep reading from cache.
    * ``tid``/``oid`` stay in clear so sign-out and offboarding can purge all
      of a user's entries by prefix, and so a key-derivation bug cannot make
      two users collide.
    * Scopes are sorted so ``[a, b]`` and ``[b, a]`` share an entry.
    """
    scope_hash = _sha256(" ".join(sorted(scopes)))[:16]
    return f"obo:{tenant_id}:{object_id}:{_sha256(assertion)}:{scope_hash}"


def managed_identity_client_assertion(user_assigned_client_id: str | None = None) -> Callable[[], str]:
    """Federated identity credential (FIC): the app registration trusts a
    managed identity, so no client secret or certificate exists anywhere.

    MSAL invokes the returned callable whenever it needs a fresh client
    assertion; ``ManagedIdentityClient`` caches the MI token internally.
    """
    if msal is None:
        raise RuntimeError("pip install msal")
    import requests  # msal dependency

    identity = (
        msal.UserAssignedManagedIdentity(client_id=user_assigned_client_id)
        if user_assigned_client_id
        else msal.SystemAssignedManagedIdentity()
    )
    client = msal.ManagedIdentityClient(identity, http_client=requests.Session())

    def _assertion() -> str:
        result = client.acquire_token_for_client(resource=FIC_AUDIENCE)
        if "access_token" not in result:
            raise OboMisconfigured("managed identity could not mint FIC assertion")
        return str(result["access_token"])

    return _assertion


@dataclass(frozen=True)
class OboSettings:
    tenant_id: str
    client_id: str
    client_credential: Any
    authority_host: str = ENTRA_AUTHORITY_HOST
    # "CP1" declares the client can handle CAE claims challenges, which makes
    # Entra issue long-lived (CAE) tokens that are revoked by event instead.
    client_capabilities: tuple[str, ...] = ("CP1",)
    refresh_skew_seconds: int = 300

    @property
    def authority(self) -> str:
        return f"{self.authority_host}/{self.tenant_id}"

    @classmethod
    def from_env(cls, env: Mapping[str, str] = os.environ) -> "OboSettings":
        """Credential precedence: managed-identity FIC > certificate > secret."""
        if env.get("AGENT_MCP_USE_FIC") == "1":
            credential: Any = {
                "client_assertion": managed_identity_client_assertion(env.get("AGENT_MCP_FIC_MI_CLIENT_ID") or None)
            }
        elif env.get("AGENT_MCP_CERT_PFX_PATH"):
            credential = {
                "private_key_pfx_path": env["AGENT_MCP_CERT_PFX_PATH"],
                "passphrase": env.get("AGENT_MCP_CERT_PFX_PASSPHRASE"),
            }
        elif env.get("AGENT_MCP_CLIENT_SECRET"):
            log.warning("obo.credential client secret in use; acceptable for local development only")
            credential = env["AGENT_MCP_CLIENT_SECRET"]
        else:
            raise OboMisconfigured("no client credential configured")
        return cls(tenant_id=env["AGENT_MCP_TENANT_ID"], client_id=env["AGENT_MCP_CLIENT_ID"], client_credential=credential)


class OboTokenBroker:
    """Exchanges an incoming user assertion for a downstream token.

    * One ``ConfidentialClientApplication`` per exchange with a throwaway
      token cache (OBO results are cached here, keyed by assertion), sharing
      ``http_cache`` so authority metadata is fetched once per process.
    * MSAL is synchronous; the call runs in a worker thread so one slow
      token endpoint round-trip never stalls the event loop for other turns.
    * Concurrent requests for the same key share one in-flight exchange.
    """

    def __init__(
        self,
        settings: OboSettings,
        *,
        cache: TokenCache | None = None,
        app_factory: Callable[[], Any] | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._settings = settings
        self._clock = clock
        self._cache = cache or MemoryTokenCache(clock=clock)
        self._http_cache: dict[Any, Any] = {}
        self._app_factory = app_factory or self._default_app
        self._inflight: dict[str, asyncio.Future[AccessToken]] = {}
        self.idp_calls = 0
        self.cache_hits = 0

    def _default_app(self) -> Any:
        if msal is None:
            raise RuntimeError("pip install msal")
        return msal.ConfidentialClientApplication(
            self._settings.client_id,
            authority=self._settings.authority,
            client_credential=self._settings.client_credential,
            client_capabilities=list(self._settings.client_capabilities),
            http_cache=self._http_cache,
            token_cache=msal.TokenCache(),
        )

    async def exchange(
        self,
        assertion: str,
        scopes: Sequence[str],
        *,
        claims_challenge: str | None = None,
        force_refresh: bool = False,
    ) -> AccessToken:
        ident = unverified_identity(assertion)
        key = obo_cache_key(ident.tenant_id, ident.object_id, assertion, scopes)
        if claims_challenge or force_refresh:
            # Never satisfy a challenge or a rejected token from cache.
            return await self._exchange_now(key, ident, assertion, scopes, claims_challenge)
        hit = await self._cache.get(key)
        if hit is not None and hit.expires_on - self._settings.refresh_skew_seconds > self._clock():
            self.cache_hits += 1
            return hit
        pending = self._inflight.get(key)
        if pending is None:
            pending = asyncio.ensure_future(self._exchange_now(key, ident, assertion, scopes, None))
            self._inflight[key] = pending
            pending.add_done_callback(lambda _f, k=key: self._inflight.pop(k, None))
        return await asyncio.shield(pending)

    async def _exchange_now(
        self,
        key: str,
        ident: TokenIdentity,
        assertion: str,
        scopes: Sequence[str],
        claims_challenge: str | None,
    ) -> AccessToken:
        app = self._app_factory()
        self.idp_calls += 1
        started = self._clock()  # expiry is computed from *before* the call: conservative
        result = await asyncio.to_thread(
            app.acquire_token_on_behalf_of, assertion, list(scopes), claims_challenge=claims_challenge
        )
        oid_hash = _sha256(ident.object_id)[:16]
        if "access_token" not in result:
            err = classify_token_error(result, scopes)
            log.warning(
                "obo.exchange.failed class=%s codes=%s correlation_id=%s oid_hash=%s scopes=%s",
                type(err).__name__, list(err.error_codes), err.correlation_id, oid_hash, list(scopes),
            )
            raise err
        expires_in = int(result.get("expires_in", 3599))
        token = AccessToken(str(result["access_token"]), expires_on=started + expires_in, scopes=tuple(scopes))
        ttl = expires_in - self._settings.refresh_skew_seconds
        if ttl > 0:
            await self._cache.set(key, token, ttl)
        log.info(
            "obo.exchange.ok oid_hash=%s scopes=%s token=%s challenged=%s",
            oid_hash, list(scopes), redact_token(token.token), bool(claims_challenge),
        )
        return token

    async def purge_user(self, tenant_id: str, object_id: str) -> int:
        """Call on sign-out, on offboarding and on a CAE ``user_risk`` event."""
        return await self._cache.delete_prefix(f"obo:{tenant_id}:{object_id}:")


# A token provider gets (claims_challenge, force_refresh) and returns a bearer.
TokenProvider = Callable[[str | None, bool], Awaitable[str]]


def obo_token_provider(broker: OboTokenBroker, assertion: str, scopes: Sequence[str]) -> TokenProvider:
    async def provide(claims: str | None, force: bool) -> str:
        return (await broker.exchange(assertion, scopes, claims_challenge=claims, force_refresh=force)).token

    return provide


# ---------------------------------------------------------------------------
# WWW-Authenticate / CAE helpers.
# ---------------------------------------------------------------------------

_AUTH_PARAM = re.compile(r'([A-Za-z_][A-Za-z0-9_-]*)\s*=\s*"([^"]*)"')


def parse_www_authenticate(header: str) -> dict[str, str]:
    scheme, _, rest = header.strip().partition(" ")
    if scheme.lower() != "bearer":
        return {}
    return {k.lower(): v for k, v in _AUTH_PARAM.findall(rest)}


def decode_claims_challenge(value: str) -> str:
    """CAE sends the claims JSON base64-encoded (padding optional)."""
    raw = value.replace("-", "+").replace("_", "/")
    decoded = base64.b64decode(raw + "=" * (-len(raw) % 4)).decode("utf-8")
    json.loads(decoded)  # must be JSON; raises otherwise
    return decoded


def insufficient_claims_header(claims_json: str) -> str:
    """What an MCP server returns when *its* downstream OBO hits a claims
    challenge, so the agent can propagate step-up to the user. Only possible
    before the response body starts; once an SSE stream is open, return a
    structured tool error ``{"error": "step_up_required", "claims": ...}``."""
    encoded = base64.b64encode(claims_json.encode("utf-8")).decode("ascii")
    return f'Bearer realm="", error="insufficient_claims", claims="{encoded}"'


# ---------------------------------------------------------------------------
# MCP Streamable HTTP client.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ToolResult:
    content: list[dict[str, Any]]
    structured: dict[str, Any] | None
    is_error: bool


async def _aiter_sse(resp: Any) -> AsyncIterator[dict[str, Any]]:
    data: list[str] = []
    async for line in resp.aiter_lines():
        if line == "":
            if data:
                yield json.loads("\n".join(data))
                data = []
            continue
        if line.startswith(":"):
            continue  # comment / keep-alive
        name, _, value = line.partition(":")
        if value.startswith(" "):
            value = value[1:]
        if name == "data":
            data.append(value)
        # ``event``/``id``/``retry`` are not needed without stream resumption.
    if data:
        yield json.loads("\n".join(data))


class StreamableHttpMcpClient:
    """Minimal MCP client over Streamable HTTP with explicit auth semantics.

    Retry policy (each at most once per request, so a broken server can never
    cause a token-endpoint storm):
      * 401 ``invalid_token``      -> force a fresh OBO exchange, retry.
      * 401 ``insufficient_claims`` -> exchange WITH the decoded claims, retry.
        If Entra needs the user, ``ClaimsChallengeRequired`` propagates to the
        turn handler, which sends an OAuth card (``remediation_activity``).
      * 404 with a session id     -> session expired; re-initialize, retry.
    """

    def __init__(
        self,
        endpoint: str,
        token_provider: TokenProvider,
        *,
        http: Any = None,
        protocol_version: str = DEFAULT_MCP_PROTOCOL_VERSION,
        client_name: str = "enterprise-agent",
        client_version: str = "1.0.0",
        on_notification: Callable[[dict[str, Any]], None] | None = None,
        timeout_seconds: float = 30.0,
    ) -> None:
        if httpx is None and http is None:
            raise RuntimeError("pip install httpx")
        self._endpoint = endpoint
        self._provide = token_provider
        self._http = http or httpx.AsyncClient(timeout=timeout_seconds)
        self._requested_version = protocol_version
        self._client_info = {"name": client_name, "version": client_version}
        self._on_notification = on_notification or (lambda _m: None)
        self._next_id = 0
        self.session_id: str | None = None
        self.protocol_version: str | None = None
        self.server_info: dict[str, Any] = {}
        self.reinitializations = 0
        self.auth_retries = 0
        self.trace_id = uuid.uuid4().hex

    async def __aenter__(self) -> "StreamableHttpMcpClient":
        await self.initialize()
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    async def initialize(self) -> dict[str, Any]:
        self.session_id = None
        self.protocol_version = None
        result, headers = await self._send(
            self._request(
                "initialize",
                {"protocolVersion": self._requested_version, "capabilities": {}, "clientInfo": self._client_info},
            )
        )
        self.session_id = headers.get("mcp-session-id")
        self.protocol_version = str(result.get("protocolVersion") or self._requested_version)
        self.server_info = dict(result.get("serverInfo") or {})
        await self._send({"jsonrpc": "2.0", "method": "notifications/initialized"})
        return result

    async def list_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            result, _ = await self._send(self._request("tools/list", {"cursor": cursor} if cursor else {}))
            tools.extend(result.get("tools", []))
            cursor = result.get("nextCursor")
            if not cursor:
                return tools

    async def call_tool(self, name: str, arguments: Mapping[str, Any] | None = None) -> ToolResult:
        params = {"name": name, "arguments": dict(arguments or {}), "_meta": {"progressToken": uuid.uuid4().hex}}
        result, _ = await self._send(self._request("tools/call", params))
        return ToolResult(
            content=list(result.get("content") or []),
            structured=result.get("structuredContent"),
            is_error=bool(result.get("isError")),
        )

    async def aclose(self) -> None:
        if self.session_id:
            try:
                token = await self._provide(None, False)
                await self._http.delete(self._endpoint, headers=self._headers(token))
            except Exception:  # best effort; the server will expire the session
                pass
        await self._http.aclose()

    # -- internals ---------------------------------------------------------

    def _request(self, method: str, params: Mapping[str, Any]) -> dict[str, Any]:
        self._next_id += 1
        return {"jsonrpc": "2.0", "id": self._next_id, "method": method, "params": dict(params)}

    def _headers(self, token: str) -> dict[str, str]:
        h = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "traceparent": f"00-{self.trace_id}-{uuid.uuid4().hex[:16]}-01",
        }
        if self.protocol_version:
            h["MCP-Protocol-Version"] = self.protocol_version
        if self.session_id:
            h["Mcp-Session-Id"] = self.session_id
        return h

    async def _send(self, message: dict[str, Any]) -> tuple[dict[str, Any], dict[str, str]]:
        claims: str | None = None
        auth_retry = session_retry = False
        while True:
            token = await self._provide(claims, auth_retry)
            kind, payload, headers = await self._attempt(message, token)
            if kind == "ok":
                return payload, headers
            if kind == "unauthorized":
                if auth_retry:
                    raise DownstreamUnauthorized("MCP server rejected a freshly exchanged token")
                auth_retry = True
                self.auth_retries += 1
                claims = payload.get("claims")
                continue
            if kind == "session_expired":
                if session_retry or message.get("method") == "initialize":
                    raise McpProtocolError(-32001, "session could not be re-established")
                session_retry = True
                self.reinitializations += 1
                await self.initialize()
                continue
            raise McpProtocolError(int(payload.get("code", -32603)), str(payload.get("message", "error")))

    async def _attempt(self, message: dict[str, Any], token: str) -> tuple[str, dict[str, Any], dict[str, str]]:
        body = json.dumps(message).encode("utf-8")
        async with self._http.stream("POST", self._endpoint, content=body, headers=self._headers(token)) as resp:
            headers = {k.lower(): v for k, v in resp.headers.items()}
            status = resp.status_code
            if status == 401:
                await resp.aread()
                challenge = parse_www_authenticate(headers.get("www-authenticate", ""))
                claims = None
                if challenge.get("error") == "insufficient_claims" and challenge.get("claims"):
                    claims = decode_claims_challenge(challenge["claims"])
                return "unauthorized", {"claims": claims}, headers
            if status == 404 and self.session_id:
                await resp.aread()
                return "session_expired", {}, headers
            if status == 202 and "id" not in message:
                return "ok", {}, headers
            if status >= 400:
                await resp.aread()
                return "error", {"code": -32000 - status, "message": f"HTTP {status}"}, headers

            content_type = headers.get("content-type", "").split(";")[0].strip().lower()
            if content_type == "text/event-stream":
                async for msg in _aiter_sse(resp):
                    if msg.get("id") == message.get("id") and ("result" in msg or "error" in msg):
                        return self._unwrap(msg, headers)
                    if "method" in msg and "id" not in msg:
                        self._on_notification(msg)  # progress / logging -> e.g. Teams informative update
                    elif "method" in msg:
                        # Server->client requests (sampling, elicitation) are not
                        # supported by this client; production code must answer
                        # them with a JSON-RPC error instead of leaving them hanging.
                        log.info("mcp.server_request.ignored method=%s", msg.get("method"))
                return "error", {"code": -32603, "message": "stream ended without a response"}, headers
            raw = await resp.aread()
            return self._unwrap(json.loads(raw), headers)

    @staticmethod
    def _unwrap(msg: Mapping[str, Any], headers: dict[str, str]) -> tuple[str, dict[str, Any], dict[str, str]]:
        if "error" in msg:
            return "error", dict(msg["error"]), headers
        return "ok", dict(msg.get("result") or {}), headers


# ---------------------------------------------------------------------------
# User-facing remediation (Teams / Microsoft 365 Copilot).
# ---------------------------------------------------------------------------


def build_oauth_card_activity(
    connection_name: str,
    *,
    text: str,
    sign_in_link: str | None,
    token_exchange_uri: str | None,
) -> dict[str, Any]:
    """OAuth card attachment. With ``tokenExchangeResource`` Teams attempts
    silent SSO first; without it, the user gets an interactive sign-in button.
    Step-up MUST omit it, otherwise Teams silently returns the same SSO token
    that just failed the policy check."""
    content: dict[str, Any] = {
        "text": text,
        "connectionName": connection_name,
        "buttons": [{"type": "signin", "title": "Sign in", "value": sign_in_link or ""}],
    }
    if token_exchange_uri:
        content["tokenExchangeResource"] = {"id": uuid.uuid4().hex, "uri": token_exchange_uri}
    return {
        "type": "message",
        "inputHint": "acceptingInput",
        "attachments": [{"contentType": OAUTH_CARD_CONTENT_TYPE, "content": content}],
    }


def token_exchange_failed_response(exchange_id: str, connection_name: str, detail: str) -> dict[str, Any]:
    """Invoke response for ``signin/tokenExchange`` when the SSO token cannot
    be exchanged. HTTP 412 tells Teams to fall back to the sign-in button."""
    return {
        "status": 412,
        "body": {"id": exchange_id, "connectionName": connection_name, "failureDetail": detail},
    }


def remediation_activity(
    err: AuthError,
    *,
    connection_name: str,
    sign_in_link: str | None = None,
    token_exchange_uri: str | None = None,
    tenant_id: str | None = None,
    client_id: str | None = None,
) -> dict[str, Any]:
    reference = err.correlation_id or "n/a"
    if isinstance(err, ClaimsChallengeRequired):
        return build_oauth_card_activity(
            connection_name, text=err.user_message, sign_in_link=sign_in_link, token_exchange_uri=None
        )
    if isinstance(err, (AssertionExpired, InvalidInboundToken)):
        return build_oauth_card_activity(
            connection_name, text=err.user_message, sign_in_link=sign_in_link, token_exchange_uri=token_exchange_uri
        )
    if isinstance(err, ConsentRequired):
        text = f"{err.user_message} Reference: {reference}."
        if tenant_id and client_id:
            text += f" Admin consent: {ENTRA_AUTHORITY_HOST}/{tenant_id}/adminconsent?client_id={client_id}"
        return {"type": "message", "text": text}
    return {"type": "message", "text": f"{err.user_message} Reference: {reference}."}


# ---------------------------------------------------------------------------
# Release gate: two-user row-level isolation.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IsolationReport:
    user_a_rows: frozenset[str]
    user_b_rows: frozenset[str]
    overlap: frozenset[str]
    distinct_principals: bool
    cross_probe_denied: bool

    @property
    def passed(self) -> bool:
        return (
            bool(self.user_a_rows)
            and bool(self.user_b_rows)
            and not self.overlap
            and self.distinct_principals
            and self.cross_probe_denied
        )


async def verify_two_user_isolation(
    client_a: StreamableHttpMcpClient,
    client_b: StreamableHttpMcpClient,
    *,
    list_tool: str,
    probe_tool: str,
    probe_argument: str,
    row_key: str = "id",
    principal_field: str = "principal",
) -> IsolationReport:
    """Two synthetic users with provably *disjoint* entitlements call the same
    tools through the full chain. Identical results for different users is
    the signature of a service identity leaking into the data plane."""
    ra = await client_a.call_tool(list_tool, {})
    rb = await client_b.call_tool(list_tool, {})
    sa, sb = ra.structured or {}, rb.structured or {}
    rows_a = frozenset(str(r[row_key]) for r in sa.get("rows", []))
    rows_b = frozenset(str(r[row_key]) for r in sb.get("rows", []))
    denied = True
    if rows_a:
        probe = await client_b.call_tool(probe_tool, {probe_argument: sorted(rows_a)[0]})
        denied = probe.is_error or not (probe.structured or {}).get("rows")
    return IsolationReport(
        user_a_rows=rows_a,
        user_b_rows=rows_b,
        overlap=rows_a & rows_b,
        distinct_principals=sa.get(principal_field) != sb.get(principal_field),
        cross_probe_denied=bool(denied),
    )


# ---------------------------------------------------------------------------
# Offline demonstration: fake identity platform + in-process MCP server.
# All identifiers are placeholders.
# ---------------------------------------------------------------------------

_TENANT = "11111111-1111-1111-1111-111111111111"
_OTHER_TENANT = "99999999-9999-9999-9999-999999999999"
_AGENT_APP = "22222222-2222-2222-2222-222222222222"
_MCP_APP = "33333333-3333-3333-3333-333333333333"
_TEAMS_CLIENT = "44444444-4444-4444-4444-444444444444"
_USER_A = "aaaaaaaa-0000-0000-0000-00000000000a"
_USER_B = "bbbbbbbb-0000-0000-0000-00000000000b"
_MCP_URI = f"api://{_MCP_APP}"
_MCP_SCOPE = f"{_MCP_URI}/Tools.Invoke"
_SSO_AUD = f"api://botid-{_AGENT_APP}"
_STEP_UP_CLAIMS = json.dumps({"access_token": {"acrs": {"essential": True, "value": "c1"}}})


class _FakeIdentityPlatform:
    """Mints genuine RS256 JWTs so the server-side validator runs unmodified."""

    def __init__(self, private_key: Any) -> None:
        self._key = private_key
        self.calls = 0
        self.fail_next: dict[str, Any] | None = None
        self.mfa_completed: set[str] = set()

    def mint(
        self,
        *,
        aud: str,
        oid: str | None,
        scp: str | None = None,
        azp: str = _AGENT_APP,
        tid: str = _TENANT,
        ver: str = "2.0",
        ttl: int = 3600,
        extra: Mapping[str, Any] | None = None,
    ) -> str:
        now = int(time.time())
        iss = f"{ENTRA_AUTHORITY_HOST}/{tid}/v2.0" if ver == "2.0" else f"https://sts.windows.net/{tid}/"
        claims: dict[str, Any] = {
            "iss": iss, "aud": aud, "tid": tid, "ver": ver,
            "iat": now - 5, "nbf": now - 5, "exp": now + ttl, "jti": uuid.uuid4().hex,
        }
        if oid:
            claims["oid"] = oid
        if scp is not None:
            claims["scp"] = scp
        claims["azp" if ver == "2.0" else "appid"] = azp
        claims.update(extra or {})
        return jwt.encode(claims, self._key, algorithm="RS256", headers={"kid": "local-test"})

    def app_factory(self) -> Any:
        platform = self

        class _FakeConfidentialClient:
            def acquire_token_on_behalf_of(
                self, user_assertion: str, scopes: list[str], claims_challenge: str | None = None
            ) -> dict[str, Any]:
                platform.calls += 1
                time.sleep(0.01)  # widen the race window so single-flight is exercised
                if platform.fail_next is not None:
                    failure, platform.fail_next = platform.fail_next, None
                    return failure
                inbound = jwt.decode(user_assertion, options={"verify_signature": False})
                oid = inbound["oid"]
                extra: dict[str, Any] = {}
                if claims_challenge:
                    if oid not in platform.mfa_completed:
                        return {
                            "error": "interaction_required",
                            "error_codes": [50076],
                            "claims": claims_challenge,
                            "correlation_id": "corr-stepup",
                        }
                    extra["acrs"] = ["c1"]
                resource, _, scope = scopes[0].rpartition("/")
                aud = _MCP_APP if resource == _MCP_URI else resource
                token = platform.mint(aud=aud, oid=oid, scp=scope, azp=_AGENT_APP, extra=extra)
                return {"access_token": token, "expires_in": 3599, "token_type": "Bearer"}

        return _FakeConfidentialClient()


class _FakeMcpServer:
    """In-process MCP server with per-user row filtering, token revocation,
    an authentication-context (CAE) requirement and session expiry."""

    def __init__(self, validator: InboundTokenValidator) -> None:
        self._validator = validator
        self.sessions: set[str] = set()
        self.revoked: set[str] = set()
        self.require_auth_context: str | None = None
        self.rows = [
            {"id": "REC-A1", "owner": _USER_A, "title": "Item visible to user A"},
            {"id": "REC-A2", "owner": _USER_A, "title": "Second item for user A"},
            {"id": "REC-B1", "owner": _USER_B, "title": "Item visible to user B"},
        ]

    @staticmethod
    def _unauthorized(www: str) -> Any:
        return httpx.Response(401, headers={"WWW-Authenticate": www})

    async def __call__(self, request: Any) -> Any:
        token = request.headers.get("authorization", "").removeprefix("Bearer ").strip()
        if _sha256(token) in self.revoked:
            return self._unauthorized('Bearer error="invalid_token"')
        try:
            principal = self._validator.validate(token)
        except InvalidInboundToken:
            return self._unauthorized('Bearer error="invalid_token"')
        if self.require_auth_context and self.require_auth_context not in principal.auth_context:
            return self._unauthorized(insufficient_claims_header(_STEP_UP_CLAIMS))
        if request.method == "DELETE":
            self.sessions.discard(request.headers.get("mcp-session-id", ""))
            return httpx.Response(200)

        msg = json.loads(request.content)
        if msg.get("method") == "initialize":
            sid = uuid.uuid4().hex
            self.sessions.add(sid)
            body = {"jsonrpc": "2.0", "id": msg["id"], "result": {
                "protocolVersion": msg["params"]["protocolVersion"],
                "capabilities": {"tools": {}},
                "serverInfo": {"name": "governed-data-mcp", "version": "1.0.0"},
            }}
            return httpx.Response(200, json=body, headers={"Mcp-Session-Id": sid})
        if request.headers.get("mcp-session-id") not in self.sessions:
            return httpx.Response(404)
        if "id" not in msg:
            return httpx.Response(202)

        visible = [r for r in self.rows if r["owner"] == principal.object_id]  # row-level security
        params = msg.get("params", {})
        if params.get("name") == "list_my_records":
            rows, is_error = visible, False
        elif params.get("name") == "get_record":
            rows = [r for r in visible if r["id"] == params.get("arguments", {}).get("id")]
            is_error = not rows  # "not found" whether absent or forbidden: no existence oracle
        else:
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": msg["id"],
                                             "error": {"code": -32602, "message": "unknown tool"}})
        result = {
            "content": [{"type": "text", "text": f"{len(rows)} record(s)" if rows else "not found"}],
            "structuredContent": {
                "rows": [{"id": r["id"], "title": r["title"]} for r in rows],
                "principal": _sha256(principal.key)[:16],
            },
            "isError": is_error,
        }
        token_meta = params.get("_meta", {}).get("progressToken")
        events = [
            {"jsonrpc": "2.0", "method": "notifications/progress",
             "params": {"progressToken": token_meta, "progress": 1, "total": 2, "message": "Querying governed data"}},
            {"jsonrpc": "2.0", "id": msg["id"], "result": result},
        ]
        sse = "".join(f"event: message\ndata: {json.dumps(e)}\n\n" for e in events)
        return httpx.Response(200, content=sse.encode("utf-8"), headers={"Content-Type": "text/event-stream"})


class _CaptureLogs(logging.Handler):
    def __init__(self) -> None:
        super().__init__(logging.DEBUG)
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(record.getMessage())


async def _expect(exc_type: type[BaseException], awaitable: Awaitable[Any]) -> BaseException:
    try:
        await awaitable
    except exc_type as exc:
        return exc
    raise AssertionError(f"expected {exc_type.__name__}")


async def _selftest() -> int:
    missing = [name for name, mod in (("PyJWT", jwt), ("httpx", httpx)) if mod is None]
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
    except ImportError:
        missing.append("cryptography")
    if missing:
        print("skipped: install " + ", ".join(missing))
        return 0

    capture = _CaptureLogs()
    log.addHandler(capture)
    log.setLevel(logging.DEBUG)
    log.propagate = False

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    idp = _FakeIdentityPlatform(key)
    resolve = lambda _token: key.public_key()  # noqa: E731
    groups = 0

    # 1. Inbound validation ------------------------------------------------
    mcp_policy = InboundPolicy(
        tenant_id=_TENANT,
        audiences=(_MCP_APP, _MCP_URI),
        required_scopes=frozenset({"Tools.Invoke"}),
        allowed_client_app_ids=frozenset({_AGENT_APP}),
    )
    validator = InboundTokenValidator(mcp_policy, key_resolver=resolve)
    p = validator.validate(idp.mint(aud=_MCP_APP, oid=_USER_A, scp="Tools.Invoke"))
    assert p.delegated and p.object_id == _USER_A and p.client_app_id == _AGENT_APP
    assert validator.validate(idp.mint(aud=_MCP_URI, oid=_USER_A, scp="Tools.Invoke", ver="1.0")).token_version == "1.0"
    assert validator.validate(idp.mint(aud=_MCP_APP, oid=_USER_A, scp="Tools.Invoke", ttl=-60)).object_id == _USER_A
    rejected = {
        "wrong audience": idp.mint(aud=_AGENT_APP, oid=_USER_A, scp="Tools.Invoke"),
        "foreign tenant": idp.mint(aud=_MCP_APP, oid=_USER_A, scp="Tools.Invoke", tid=_OTHER_TENANT),
        "expired beyond leeway": idp.mint(aud=_MCP_APP, oid=_USER_A, scp="Tools.Invoke", ttl=-600),
        "missing scope": idp.mint(aud=_MCP_APP, oid=_USER_A, scp="User.Read"),
        "unknown client": idp.mint(aud=_MCP_APP, oid=_USER_A, scp="Tools.Invoke", azp=_TEAMS_CLIENT),
        "app-only": idp.mint(aud=_MCP_APP, oid=None, extra={"roles": ["Tools.Invoke.All"], "idtyp": "app"}),
        "HS256 confusion": jwt.encode({"aud": _MCP_APP, "tid": _TENANT}, "shared-secret-key-for-test-only", algorithm="HS256"),
    }
    for label, bad in rejected.items():
        try:
            validator.validate(bad)
        except InvalidInboundToken as exc:
            assert exc.user_message == InvalidInboundToken.user_message, label  # generic outward message
        else:
            raise AssertionError(f"accepted: {label}")
    groups += 1
    print(f"  ok  inbound validation: v2 + v1 issuers and leeway accepted; {len(rejected)} attack shapes rejected")

    # 2. OBO broker: cache binding, single-flight, purge ---------------------
    settings = OboSettings(tenant_id=_TENANT, client_id=_AGENT_APP, client_credential="unused-in-demo")
    broker = OboTokenBroker(settings, app_factory=idp.app_factory)
    sso = lambda oid: idp.mint(aud=_SSO_AUD, oid=oid, scp="access_as_user", azp=_TEAMS_CLIENT)  # noqa: E731
    sso_a, sso_b = sso(_USER_A), sso(_USER_B)
    t1 = await broker.exchange(sso_a, [_MCP_SCOPE])
    t2 = await broker.exchange(sso_a, [_MCP_SCOPE])
    assert t1.token == t2.token and broker.idp_calls == 1 and broker.cache_hits == 1
    await broker.exchange(sso(_USER_A), [_MCP_SCOPE])  # new assertion, same user -> new exchange
    tb = await broker.exchange(sso_b, [_MCP_SCOPE])
    assert broker.idp_calls == 3 and unverified_identity(tb.token).object_id == _USER_B
    sso_burst = sso(_USER_A)
    burst = await asyncio.gather(*(broker.exchange(sso_burst, [_MCP_SCOPE]) for _ in range(20)))
    assert broker.idp_calls == 4 and len({t.token for t in burst}) == 1
    assert "eyJ" not in repr(t1)
    purged = await broker.purge_user(_TENANT, _USER_A)
    assert purged == 3
    await broker.exchange(sso_a, [_MCP_SCOPE])
    assert broker.idp_calls == 5
    groups += 1
    print("  ok  OBO cache: bound to assertion, 20 concurrent misses -> 1 exchange, per-user purge")

    # 3. Error classification -----------------------------------------------
    cases: list[tuple[dict[str, Any], type[AuthError]]] = [
        ({"error": "invalid_grant", "error_codes": [65001]}, ConsentRequired),
        ({"error": "invalid_grant", "error_codes": [500133]}, AssertionExpired),
        ({"error": "invalid_grant", "error_codes": [50013]}, OboMisconfigured),
        ({"error": "invalid_grant", "error_codes": [53003]}, AccessBlockedByPolicy),
        ({"error": "invalid_grant", "error_codes": [50076]}, ClaimsChallengeRequired),
        ({"error": "temporarily_unavailable", "error_codes": []}, TransientAuthFailure),
    ]
    for failure, expected in cases:
        idp.fail_next = dict(failure, correlation_id="corr-1", error_description="contains user@contoso.com")
        err = await _expect(expected, broker.exchange(sso(_USER_A), [_MCP_SCOPE]))
        assert "contoso.com" not in str(err) and err.correlation_id == "corr-1"
    stepup = await _expect(ClaimsChallengeRequired, broker.exchange(sso_a, [_MCP_SCOPE], claims_challenge=_STEP_UP_CLAIMS))
    card = remediation_activity(stepup, connection_name="agent-sso", sign_in_link="https://token.example/signin")
    oauth = card["attachments"][0]["content"]
    assert card["attachments"][0]["contentType"] == OAUTH_CARD_CONTENT_TYPE and "tokenExchangeResource" not in oauth
    expired_card = remediation_activity(AssertionExpired(), connection_name="agent-sso", token_exchange_uri=_SSO_AUD)
    assert expired_card["attachments"][0]["content"]["tokenExchangeResource"]["uri"] == _SSO_AUD
    assert token_exchange_failed_response("x", "agent-sso", "step-up")["status"] == 412
    groups += 1
    print(f"  ok  error mapping: {len(cases) + 1} Entra failure shapes -> remediation; step-up card is interactive-only")

    # 4. MCP client: SSE, revocation retry, CAE step-up, session expiry -----
    server = _FakeMcpServer(validator)
    notes: list[dict[str, Any]] = []

    def make_client(assertion: str) -> StreamableHttpMcpClient:
        return StreamableHttpMcpClient(
            "https://mcp.example.invalid/mcp",
            obo_token_provider(broker, assertion, [_MCP_SCOPE]),
            http=httpx.AsyncClient(transport=httpx.MockTransport(server)),
            on_notification=notes.append,
        )

    client_a = make_client(sso_a)
    await client_a.initialize()
    assert client_a.session_id in server.sessions and client_a.protocol_version == DEFAULT_MCP_PROTOCOL_VERSION
    first = await client_a.call_tool("list_my_records")
    assert {r["id"] for r in first.structured["rows"]} == {"REC-A1", "REC-A2"}
    assert notes and notes[0]["method"] == "notifications/progress"

    current = await broker.exchange(sso_a, [_MCP_SCOPE])
    server.revoked.add(_sha256(current.token))
    calls_before = broker.idp_calls
    again = await client_a.call_tool("list_my_records")
    assert not again.is_error and broker.idp_calls == calls_before + 1 and client_a.auth_retries == 1

    server.require_auth_context = "c1"
    await _expect(ClaimsChallengeRequired, client_a.call_tool("list_my_records"))
    idp.mfa_completed.add(_USER_A)  # user completes the interactive sign-in from the OAuth card
    stepped = await client_a.call_tool("list_my_records")
    assert not stepped.is_error
    server.require_auth_context = None

    server.sessions.clear()
    renewed = await client_a.call_tool("list_my_records")
    assert not renewed.is_error and client_a.reinitializations == 1
    groups += 1
    print("  ok  MCP client: SSE + progress, revoked token re-exchanged once, CAE step-up, session re-init")

    # 5. Two-user isolation release gate ------------------------------------
    client_b = make_client(sso_b)
    await client_b.initialize()
    report = await verify_two_user_isolation(
        client_a, client_b, list_tool="list_my_records", probe_tool="get_record", probe_argument="id"
    )
    assert report.passed, report
    await client_a.aclose()
    await client_b.aclose()
    groups += 1
    print(f"  ok  isolation: A={sorted(report.user_a_rows)} B={sorted(report.user_b_rows)}, cross-probe denied")

    # 6. No bearer token ever reached a log line ----------------------------
    assert capture.lines and not any("eyJ" in line for line in capture.lines)
    groups += 1
    print(f"  ok  redaction: {len(capture.lines)} log lines, zero raw tokens")

    print(f"\n{groups} scenario groups passed; identity platform calls: {idp.calls}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(_selftest()))
