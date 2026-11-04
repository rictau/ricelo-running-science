"""End-to-end tests for remote (HTTP + OAuth) mode."""

import base64
import hashlib
import re
import secrets
from urllib.parse import parse_qs, urlparse

import pytest
from starlette.testclient import TestClient

from ricelo.mcp_server import build_server

PUBLIC = "https://mcp.example.test"
PASSWORD = "correct horse battery"
REDIRECT = "https://claude.ai/api/mcp/auth_callback"
ENV = {
    "RICELO_TRANSPORT": "http",
    "RICELO_PUBLIC_URL": PUBLIC,
    "RICELO_AUTH_SECRET": "s" * 40,
    "RICELO_OWNER_PASSWORD": PASSWORD,
}
MCP_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


@pytest.fixture
def client():
    app = build_server(ENV).streamable_http_app()
    with TestClient(app, base_url=PUBLIC) as c:
        yield c


def _register(client, redirect=REDIRECT):
    return client.post("/register", json={"redirect_uris": [redirect], "client_name": "test",
                                          "token_endpoint_auth_method": "none"})


def _sign_in(client, password=PASSWORD):
    reg = _register(client)
    assert reg.status_code == 201, reg.text
    client_id = reg.json()["client_id"]
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    auth = client.get("/authorize", params={
        "response_type": "code", "client_id": client_id, "redirect_uri": REDIRECT, "state": "xyz",
        "code_challenge": challenge, "code_challenge_method": "S256", "resource": f"{PUBLIC}/mcp",
    }, follow_redirects=False)
    assert auth.status_code == 302, auth.text
    login = client.get(auth.headers["location"].removeprefix(PUBLIC))
    assert login.status_code == 200 and "claude.ai" in login.text
    pending = re.search(r'name=request value="([^"]+)"', login.text).group(1)
    done = client.post("/login", data={"request": pending, "password": password}, follow_redirects=False)
    return client_id, verifier, done


def test_unauthenticated_mcp_is_refused(client):
    r = client.post("/mcp", headers=MCP_HEADERS, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401
    assert "oauth-protected-resource" in r.headers["www-authenticate"]


def test_metadata_is_published(client):
    meta = client.get("/.well-known/oauth-authorization-server").json()
    assert meta["registration_endpoint"] == f"{PUBLIC}/register"
    assert client.get("/.well-known/oauth-protected-resource/mcp").status_code == 200


def test_unknown_redirect_host_is_rejected(client):
    assert _register(client, "https://evil.example/cb").status_code == 400


def test_wrong_password_does_not_issue_a_code(client):
    _, _, done = _sign_in(client, password="nope")
    assert done.status_code == 401


def test_full_flow_lists_tools(client):
    client_id, verifier, done = _sign_in(client)
    assert done.status_code == 302
    back = urlparse(done.headers["location"])
    assert f"{back.scheme}://{back.netloc}{back.path}" == REDIRECT
    q = parse_qs(back.query)
    assert q["state"] == ["xyz"]

    tok = client.post("/token", data={
        "grant_type": "authorization_code", "code": q["code"][0], "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": verifier, "resource": f"{PUBLIC}/mcp",
    })
    assert tok.status_code == 200, tok.text
    tokens = tok.json()

    # Wrong PKCE verifier must fail.
    bad = client.post("/token", data={
        "grant_type": "authorization_code", "code": q["code"][0], "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": "x" * 50,
    })
    assert bad.status_code == 400

    auth = {**MCP_HEADERS, "Authorization": f"Bearer {tokens['access_token']}"}
    r = client.post("/mcp", headers=auth, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 200, r.text
    assert "list_activities" in {t["name"] for t in r.json()["result"]["tools"]}

    refreshed = client.post("/token", data={
        "grant_type": "refresh_token", "refresh_token": tokens["refresh_token"], "client_id": client_id,
    })
    assert refreshed.status_code == 200, refreshed.text


def test_tampered_token_is_refused(client):
    client_id, verifier, done = _sign_in(client)
    code = parse_qs(urlparse(done.headers["location"]).query)["code"][0]
    token = client.post("/token", data={
        "grant_type": "authorization_code", "code": code, "client_id": client_id,
        "redirect_uri": REDIRECT, "code_verifier": verifier,
    }).json()["access_token"]
    body, mac = token.split(".")
    forged = body[:-2] + ("AA" if body[-2:] != "AA" else "BB") + "." + mac
    r = client.post("/mcp", headers={**MCP_HEADERS, "Authorization": f"Bearer {forged}"},
                    json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    assert r.status_code == 401


def test_public_bind_without_auth_refuses_to_start():
    with pytest.raises(SystemExit):
        build_server({"RICELO_TRANSPORT": "http", "RICELO_HOST": "0.0.0.0"})


def test_default_is_stdio():
    assert build_server({}).settings.stateless_http is False
