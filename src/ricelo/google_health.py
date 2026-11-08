"""Google Health API client (the successor of the Fitbit Web API).

Reads the user's exercise sessions, their TCX export and heart-rate samples.
Authentication is OAuth 2.0 with your own Google Cloud OAuth client: see
docs/fitbit-sync.md for the one-time setup.

Reference: https://developers.google.com/health
"""

from __future__ import annotations

import http.server
import json
import os
import secrets
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

BASE_URL = "https://health.googleapis.com/v4"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"

SCOPES = [
    # Exercise sessions.
    "https://www.googleapis.com/auth/googlehealth.activity_and_fitness.readonly",
    # GPS recorded during an exercise; the TCX export needs it for the route.
    "https://www.googleapis.com/auth/googlehealth.location.readonly",
    # Heart-rate samples.
    "https://www.googleapis.com/auth/googlehealth.health_metrics_and_measurements.readonly",
]

CONFIG_DIR = Path("~/.config/ricelo").expanduser()
DEFAULT_CLIENT_FILE = CONFIG_DIR / "google-oauth-client.json"
DEFAULT_TOKEN_FILE = CONFIG_DIR / "google-health-token.json"
DEFAULT_REDIRECT_PORT = 8765

# Exercise sessions: the API's default and maximum page size is 25.
EXERCISE_PAGE_SIZE = 25
SAMPLE_PAGE_SIZE = 10000


class GoogleHealthAuthError(RuntimeError):
    """Raised when Google Health credentials are missing or rejected."""


def write_private_json(path: Path, data: dict[str, Any]) -> None:
    """Write JSON readable only by the owner (it holds live credentials)."""
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    if hasattr(os, "fchmod"):
        os.fchmod(fd, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


@dataclass
class OAuthClient:
    client_id: str
    client_secret: str


def load_oauth_client(client_file: Path | str | None = None) -> OAuthClient:
    """Read the OAuth client from env vars or the JSON downloaded from Google Cloud."""
    env_id = os.environ.get("GOOGLE_HEALTH_CLIENT_ID")
    env_secret = os.environ.get("GOOGLE_HEALTH_CLIENT_SECRET")
    if env_id and env_secret:
        return OAuthClient(env_id.strip(), env_secret.strip())

    path = Path(client_file).expanduser() if client_file else DEFAULT_CLIENT_FILE
    if not path.exists():
        raise GoogleHealthAuthError(
            f"No Google OAuth client found. Save the client JSON from Google Cloud to {path}, "
            "or set GOOGLE_HEALTH_CLIENT_ID and GOOGLE_HEALTH_CLIENT_SECRET. See docs/fitbit-sync.md."
        )
    data = json.loads(path.read_text(encoding="utf-8"))
    # Google's download wraps the fields in "web" or "installed" by client type.
    section = data.get("web") or data.get("installed") or data
    try:
        return OAuthClient(section["client_id"], section["client_secret"])
    except KeyError as exc:
        raise GoogleHealthAuthError(f"{path} has no {exc.args[0]}; is it a Google OAuth client JSON?") from exc


def build_auth_url(client_id: str, redirect_uri: str, state: str) -> str:
    params = {
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        # A refresh token lets scheduled syncs run without a browser.
        "access_type": "offline",
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def parse_redirect(url: str, expected_state: str) -> str:
    """Return the authorization code from the URL Google redirected to."""
    query = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    if "error" in query:
        raise GoogleHealthAuthError(f"Google returned an error: {query['error'][0]}")
    if query.get("state", [None])[0] != expected_state:
        raise GoogleHealthAuthError("OAuth state mismatch; start the login again.")
    code = query.get("code", [None])[0]
    if not code:
        raise GoogleHealthAuthError("The redirect URL has no authorization code.")
    return code


def _wait_for_redirect(port: int, timeout: float = 300.0) -> str:
    """Serve one request on localhost and return its full URL."""
    captured: dict[str, str] = {}

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            captured["url"] = f"http://localhost:{port}{self.path}"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(b"ricelo: Google Health login received. You can close this tab.")

        def log_message(self, *args):
            pass

    server = http.server.HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 1.0
    deadline = time.time() + timeout
    try:
        while "url" not in captured and time.time() < deadline:
            server.handle_request()
    finally:
        server.server_close()
    if "url" not in captured:
        raise GoogleHealthAuthError("Timed out waiting for the Google login redirect.")
    return captured["url"]


def exchange_code(oauth: OAuthClient, code: str, redirect_uri: str, http: httpx.Client | None = None) -> dict[str, Any]:
    http = http or httpx.Client(timeout=30.0)
    resp = http.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": oauth.client_id,
            "client_secret": oauth.client_secret,
            "redirect_uri": redirect_uri,
        },
    )
    if resp.status_code != 200:
        raise GoogleHealthAuthError(f"Token exchange failed (HTTP {resp.status_code}): {resp.text}")
    return resp.json()


def login(
    *,
    client_file: Path | str | None = None,
    token_file: Path | str | None = None,
    port: int = DEFAULT_REDIRECT_PORT,
    manual: bool = False,
    prompt=input,
    echo=print,
) -> Path:
    """Run the OAuth consent flow once and store a refresh token.

    With `manual`, nothing listens on localhost: the user opens the URL on any
    device, then pastes back the URL the browser was redirected to (it fails to
    load, which is expected). This suits headless machines.
    """
    oauth = load_oauth_client(client_file)
    redirect_uri = f"http://localhost:{port}/"
    state = secrets.token_urlsafe(16)
    url = build_auth_url(oauth.client_id, redirect_uri, state)

    echo("Open this URL and allow access to your Google Health data:\n")
    echo(url + "\n")
    if manual:
        redirected = prompt("Paste the full URL your browser was redirected to: ").strip()
    else:
        threading.Thread(target=webbrowser.open, args=(url,), daemon=True).start()
        redirected = _wait_for_redirect(port)

    code = parse_redirect(redirected, state)
    tokens = exchange_code(oauth, code, redirect_uri)
    if not tokens.get("refresh_token"):
        raise GoogleHealthAuthError("Google returned no refresh token; revoke ricelo's access and log in again.")
    path = Path(token_file).expanduser() if token_file else DEFAULT_TOKEN_FILE
    write_private_json(path, _token_record(tokens))
    return path


def _token_record(tokens: dict[str, Any], previous: dict[str, Any] | None = None) -> dict[str, Any]:
    record = dict(previous or {})
    record["access_token"] = tokens["access_token"]
    record["expires_at"] = time.time() + float(tokens.get("expires_in", 3600))
    # A refresh response usually omits the refresh token: keep the old one.
    if tokens.get("refresh_token"):
        record["refresh_token"] = tokens["refresh_token"]
    if tokens.get("scope"):
        record["scope"] = tokens["scope"]
    return record


def parse_rfc3339(value: str) -> datetime:
    """Parse an RFC 3339 timestamp, including Google's nanosecond fractions."""
    value = value.replace("Z", "+00:00")
    if "." in value:
        head, rest = value.split(".", 1)
        n = 0
        while n < len(rest) and rest[n].isdigit():
            n += 1
        digits, tz = rest[:n], rest[n:]
        value = f"{head}.{digits[:6]}{tz}"
    return datetime.fromisoformat(value)


class GoogleHealthClient:
    """Read-only client for the Google Health API v4."""

    def __init__(
        self,
        *,
        client_file: Path | str | None = None,
        token_file: Path | str | None = None,
        http: httpx.Client | None = None,
        base_url: str = BASE_URL,
    ):
        self._client_file = client_file
        self._token_file = Path(token_file).expanduser() if token_file else DEFAULT_TOKEN_FILE
        self._http = http or httpx.Client(timeout=60.0)
        self.base_url = base_url.rstrip("/")
        self._token: dict[str, Any] | None = None

    # ------------------ auth ------------------

    def _load_token(self) -> dict[str, Any]:
        if self._token is None:
            if not self._token_file.exists():
                raise GoogleHealthAuthError("Not logged in to Google Health. Run `ricelo google-login` first.")
            self._token = json.loads(self._token_file.read_text(encoding="utf-8"))
        return self._token

    def access_token(self, force_refresh: bool = False) -> str:
        token = self._load_token()
        if not force_refresh and token.get("access_token") and time.time() < token.get("expires_at", 0) - 60:
            return token["access_token"]

        oauth = load_oauth_client(self._client_file)
        resp = self._http.post(
            TOKEN_URL,
            data={
                "grant_type": "refresh_token",
                "refresh_token": token.get("refresh_token", ""),
                "client_id": oauth.client_id,
                "client_secret": oauth.client_secret,
            },
        )
        if resp.status_code in (400, 401):
            raise GoogleHealthAuthError(
                "Google rejected the refresh token (it expires after 7 days while the OAuth app is in "
                "Testing mode). Run `ricelo google-login` again."
            )
        resp.raise_for_status()
        self._token = _token_record(resp.json(), token)
        write_private_json(self._token_file, self._token)
        return self._token["access_token"]

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        url = path if path.startswith("http") else f"{self.base_url}/{path.lstrip('/')}"
        headers = kwargs.pop("headers", {})
        refreshed = False
        for attempt in range(5):
            headers["Authorization"] = f"Bearer {self.access_token()}"
            resp = self._http.request(method, url, headers=headers, **kwargs)
            if resp.status_code == 401 and not refreshed:
                self.access_token(force_refresh=True)
                refreshed = True
                continue
            # Google asks clients to retry these with exponential backoff.
            if resp.status_code in (429, 500, 503, 504) and attempt < 4:
                time.sleep(2 ** attempt)
                continue
            break
        resp.raise_for_status()
        return resp

    # ------------------ data ------------------

    def _list(self, data_type: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        params = dict(params)
        while True:
            body = self._request("GET", f"users/me/dataTypes/{data_type}/dataPoints", params=params).json()
            yield from body.get("dataPoints", [])
            page_token = body.get("nextPageToken")
            if not page_token:
                return
            params["pageToken"] = page_token

    def list_exercises(self, start: date, end: date | None = None) -> list[dict[str, Any]]:
        """Exercise sessions starting on or after `start` (local date), up to `end` inclusive."""
        clauses = [f'exercise.interval.civil_start_time >= "{start.isoformat()}"']
        if end:
            clauses.append(f'exercise.interval.civil_start_time < "{(end + timedelta(days=1)).isoformat()}"')
        params = {"filter": " AND ".join(clauses), "pageSize": EXERCISE_PAGE_SIZE}
        return list(self._list("exercise", params))

    def export_tcx(self, exercise_name: str, partial_data: bool = True) -> bytes:
        """Download an exercise as TCX.

        `exercise_name` is the data point's resource name
        (users/{user}/dataTypes/exercise/dataPoints/{id}). With `partial_data`
        an exercise without GPS (treadmill, gym) still exports its samples.
        """
        params = {"alt": "media", "partialData": "true" if partial_data else "false"}
        return self._request("GET", f"{exercise_name}:exportExerciseTcx", params=params).content

    def heart_rate_samples(self, start: datetime, end: datetime) -> list[tuple[datetime, int]]:
        """Heart-rate samples in [start, end), sorted by time."""
        fmt = lambda dt: dt.isoformat().replace("+00:00", "Z")
        params = {
            "filter": (
                f'heart_rate.sample_time.physical_time >= "{fmt(start)}" AND '
                f'heart_rate.sample_time.physical_time < "{fmt(end)}"'
            ),
            "pageSize": SAMPLE_PAGE_SIZE,
        }
        samples = []
        for point in self._list("heart-rate", params):
            hr = point.get("heartRate") or {}
            when = (hr.get("sampleTime") or {}).get("physicalTime")
            bpm = hr.get("beatsPerMinute")
            if when and bpm:
                samples.append((parse_rfc3339(when), int(bpm)))
        samples.sort()
        return samples
