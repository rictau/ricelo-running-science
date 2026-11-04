"""Single-owner OAuth for running ricelo as a remote MCP server.

When ricelo is hosted (e.g. on Cloud Run) so that claude.ai and the Claude
mobile apps can reach it, the URL must not be open to anyone: the server holds
the owner's Garmin key. This module makes the server its own OAuth
authorization server with exactly one user, the owner, who signs in with a
password set in ``RICELO_OWNER_PASSWORD``.

Everything is stateless. Registered clients, authorization codes, access and
refresh tokens are self-contained values signed with HMAC-SHA256 using
``RICELO_AUTH_SECRET``, so they survive restarts and scale-to-zero without a
database. Rotating ``RICELO_AUTH_SECRET`` signs everything out.

Because nothing is stored, an authorization code cannot be marked as used; it
is instead short-lived (5 minutes) and bound to the client's PKCE challenge,
which only the client that started the sign-in knows.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import html
import json
import secrets
import time
from typing import Any
from urllib.parse import urlencode, urlparse

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from pydantic import AnyUrl
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

SCOPE = "ricelo"
LOGIN_PATH = "/login"

CODE_TTL = 5 * 60
LOGIN_TTL = 10 * 60
ACCESS_TTL = 60 * 60
REFRESH_TTL = 30 * 24 * 60 * 60

# Redirect targets a sign-in may send the owner back to. Claude's web and mobile
# connectors return to claude.ai; desktop clients (Claude Code etc.) to localhost.
DEFAULT_REDIRECT_HOSTS = ("claude.ai", "claude.com", "localhost", "127.0.0.1")

# Failed password attempts allowed per window, per server instance.
MAX_FAILED_LOGINS = 10
FAILED_LOGIN_WINDOW = 15 * 60


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class Signer:
    """Signs and verifies small JSON payloads with a type tag and expiry."""

    def __init__(self, secret: str):
        if len(secret) < 32:
            raise ValueError("RICELO_AUTH_SECRET must be at least 32 characters")
        self._key = hashlib.sha256(secret.encode("utf-8")).digest()

    def _mac(self, kind: str, body: str) -> str:
        return _b64(hmac.new(self._key, f"{kind}.{body}".encode("ascii"), hashlib.sha256).digest())

    def sign(self, kind: str, payload: dict[str, Any], ttl: int | None) -> str:
        data = dict(payload)
        data["n"] = secrets.token_hex(8)  # make every value unique
        if ttl is not None:
            data["exp"] = int(time.time()) + ttl
        body = _b64(json.dumps(data, separators=(",", ":"), sort_keys=True).encode("utf-8"))
        return f"{body}.{self._mac(kind, body)}"

    def verify(self, kind: str, value: str) -> dict[str, Any] | None:
        try:
            body, mac = value.split(".", 1)
            if not hmac.compare_digest(mac, self._mac(kind, body)):
                return None
            data = json.loads(_unb64(body))
        except (ValueError, UnicodeError):
            return None
        if not isinstance(data, dict):
            return None
        exp = data.get("exp")
        if exp is not None and time.time() >= exp:
            return None
        return data

    def derive(self, label: str, value: str) -> str:
        """Deterministic secret derived from a value (used for client secrets)."""
        return _b64(hmac.new(self._key, f"{label}:{value}".encode(), hashlib.sha256).digest())


class OwnerOAuthProvider:
    """OAuthAuthorizationServerProvider for a single owner, with no storage."""

    def __init__(
        self,
        *,
        secret: str,
        owner_password: str,
        public_url: str,
        redirect_hosts: tuple[str, ...] = DEFAULT_REDIRECT_HOSTS,
    ):
        if len(owner_password) < 12:
            raise ValueError("RICELO_OWNER_PASSWORD must be at least 12 characters")
        self._signer = Signer(secret)
        self._password = owner_password
        self._public_url = public_url.rstrip("/")
        self._resource = f"{self._public_url}/mcp"
        self._redirect_hosts = tuple(h.lower() for h in redirect_hosts)
        self._failed: list[float] = []

    # -- clients (dynamic registration) ------------------------------------

    def _redirect_allowed(self, uri: str) -> bool:
        parsed = urlparse(uri)
        host = (parsed.hostname or "").lower()
        if host in ("localhost", "127.0.0.1"):
            return host in self._redirect_hosts and parsed.scheme in ("http", "https")
        return parsed.scheme == "https" and any(
            host == allowed or host.endswith("." + allowed) for allowed in self._redirect_hosts
        )

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        uris = [str(u) for u in client_info.redirect_uris or []]
        bad = [u for u in uris if not self._redirect_allowed(u)]
        if not uris or bad:
            raise RegistrationError(
                "invalid_redirect_uri",
                f"redirect URI not allowed by this server: {', '.join(bad) or '(none)'}",
            )
        # The client ID carries the client's metadata, signed, so get_client
        # needs no storage. The handler returns client_info after this call.
        client_info.client_id = self._signer.sign(
            "client",
            {
                "redirect_uris": uris,
                "auth": client_info.token_endpoint_auth_method,
                "name": client_info.client_name,
                "iat": client_info.client_id_issued_at,
            },
            ttl=None,
        )
        if client_info.client_secret is not None:
            client_info.client_secret = self._signer.derive("client-secret", client_info.client_id)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        data = self._signer.verify("client", client_id)
        if data is None:
            return None
        auth = data.get("auth") or "none"
        return OAuthClientInformationFull(
            client_id=client_id,
            client_secret=None if auth == "none" else self._signer.derive("client-secret", client_id),
            client_id_issued_at=data.get("iat"),
            redirect_uris=[AnyUrl(u) for u in data["redirect_uris"]],
            token_endpoint_auth_method=auth,
            grant_types=["authorization_code", "refresh_token"],
            response_types=["code"],
            client_name=data.get("name"),
            scope=SCOPE,
        )

    # -- authorization: owner signs in on /login ---------------------------

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        pending = self._signer.sign(
            "login",
            {
                "client_id": client.client_id,
                "redirect_uri": str(params.redirect_uri),
                "explicit": params.redirect_uri_provided_explicitly,
                "challenge": params.code_challenge,
                "state": params.state,
                "scopes": params.scopes or [SCOPE],
                "resource": params.resource,
            },
            ttl=LOGIN_TTL,
        )
        return f"{self._public_url}{LOGIN_PATH}?{urlencode({"request": pending})}"

    def _too_many_failures(self) -> bool:
        cutoff = time.time() - FAILED_LOGIN_WINDOW
        self._failed = [t for t in self._failed if t > cutoff]
        return len(self._failed) >= MAX_FAILED_LOGINS

    async def login_page(self, request: Request) -> Response:
        """GET shows the password form; POST checks it and finishes sign-in."""
        if request.method == "GET":
            token = request.query_params.get("request", "")
            pending = self._signer.verify("login", token)
            if pending is None:
                return _page("This sign-in link has expired. Start again from your AI app.", status=400)
            return _login_form(token, pending, error=None)

        form = await request.form()
        token = str(form.get("request", ""))
        pending = self._signer.verify("login", token)
        if pending is None:
            return _page("This sign-in link has expired. Start again from your AI app.", status=400)
        if self._too_many_failures():
            return _page("Too many failed attempts. Try again in 15 minutes.", status=429)

        password = str(form.get("password", ""))
        if not hmac.compare_digest(password.encode("utf-8"), self._password.encode("utf-8")):
            self._failed.append(time.time())
            await asyncio.sleep(1)  # slow down guessing
            return _login_form(token, pending, error="Wrong password.", status=401)

        code = self._signer.sign("code", {k: pending[k] for k in pending if k not in ("n", "exp")}, ttl=CODE_TTL)
        return RedirectResponse(
            construct_redirect_uri(pending["redirect_uri"], code=code, state=pending.get("state")),
            status_code=302,
        )

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        data = self._signer.verify("code", authorization_code)
        if data is None or data.get("client_id") != client.client_id:
            return None
        return AuthorizationCode(
            code=authorization_code,
            scopes=data["scopes"],
            expires_at=float(data["exp"]),
            client_id=data["client_id"],
            code_challenge=data["challenge"],
            redirect_uri=AnyUrl(data["redirect_uri"]),
            redirect_uri_provided_explicitly=data["explicit"],
            resource=data.get("resource"),
            subject="owner",
        )

    # -- tokens -------------------------------------------------------------

    def _issue(self, client_id: str, scopes: list[str], resource: str | None) -> OAuthToken:
        claims = {"client_id": client_id, "scopes": scopes, "resource": resource or self._resource}
        return OAuthToken(
            access_token=self._signer.sign("access", claims, ttl=ACCESS_TTL),
            expires_in=ACCESS_TTL,
            scope=" ".join(scopes),
            refresh_token=self._signer.sign("refresh", claims, ttl=REFRESH_TTL),
        )

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        return self._issue(authorization_code.client_id, authorization_code.scopes, authorization_code.resource)

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        data = self._signer.verify("refresh", refresh_token)
        if data is None or data.get("client_id") != client.client_id:
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=data["client_id"],
            scopes=data["scopes"],
            expires_at=data["exp"],
            resource=data.get("resource"),
            subject="owner",
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        if scopes and not set(scopes).issubset(refresh_token.scopes):
            raise TokenError("invalid_scope", "cannot widen scopes on refresh")
        return self._issue(refresh_token.client_id, scopes or refresh_token.scopes, refresh_token.resource)

    async def load_access_token(self, token: str) -> AccessToken | None:
        data = self._signer.verify("access", token)
        if data is None:
            return None
        return AccessToken(
            token=token,
            client_id=data["client_id"],
            scopes=data["scopes"],
            expires_at=data["exp"],
            resource=data.get("resource"),
            subject="owner",
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # Stateless tokens can't be revoked one by one; rotate RICELO_AUTH_SECRET
        # to sign every client out.
        return None


_STYLE = """
:root{color-scheme:light dark;--bg:#f6f7f9;--card:#fff;--fg:#1b1f24;--muted:#5b6470;--line:#d6dae0;--accent:#2b6de8;--err:#c62828}
@media (prefers-color-scheme:dark){:root{--bg:#111418;--card:#1b2027;--fg:#e8ebef;--muted:#9aa4b0;--line:#323a45;--accent:#6b9cff;--err:#ff8a80}}
*{box-sizing:border-box}body{margin:0;min-height:100vh;display:grid;place-items:center;background:var(--bg);color:var(--fg);
font:16px/1.5 system-ui,-apple-system,"Segoe UI",sans-serif;padding:16px}
main{width:100%;max-width:380px;background:var(--card);border:1px solid var(--line);border-radius:12px;padding:24px}
h1{font-size:20px;margin:0 0 8px}p{margin:0 0 16px;color:var(--muted)}b{color:var(--fg)}
input{width:100%;padding:10px 12px;font:inherit;border:1px solid var(--line);border-radius:8px;background:transparent;color:inherit}
button{margin-top:12px;width:100%;padding:10px;font:inherit;font-weight:600;border:0;border-radius:8px;background:var(--accent);color:#fff;cursor:pointer}
.err{color:var(--err);margin:8px 0 0}
"""


def _page(message: str, status: int = 200) -> HTMLResponse:
    return HTMLResponse(
        f"<!doctype html><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
        f"<title>ricelo sign-in</title><style>{_STYLE}</style><main><h1>ricelo</h1><p>{html.escape(message)}</p></main>",
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


def _login_form(token: str, pending: dict[str, Any], error: str | None, status: int = 200) -> HTMLResponse:
    target = urlparse(pending["redirect_uri"]).hostname or "?"
    err = f"<p class=err role=alert>{html.escape(error)}</p>" if error else ""
    return HTMLResponse(
        f"""<!doctype html><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>ricelo sign-in</title><style>{_STYLE}</style>
<main><h1>Connect ricelo</h1>
<p>An app at <b>{html.escape(target)}</b> wants to read your Garmin data through ricelo. Only continue if you started this.</p>
<form method=post action="{LOGIN_PATH}">
<input type=hidden name=request value="{html.escape(token)}">
<label for=pw>Owner password</label>
<input id=pw name=password type=password autocomplete=current-password required autofocus>
{err}<button type=submit>Allow</button></form></main>""",
        status_code=status,
        headers={"Cache-Control": "no-store", "X-Frame-Options": "DENY"},
    )


__all__ = ["LOGIN_PATH", "SCOPE", "OwnerOAuthProvider"]
