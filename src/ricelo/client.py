"""Native Garmin Connect Personal API client with automatic token management.

100% self-contained with no external third-party Garmin SDK dependencies.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

import httpx

DEFAULT_BASE_URL = "https://personal.connectapi.garmin.com"
DEFAULT_KEY_FILE = Path("~/.config/garmin/connect-personal-api-key").expanduser()
DEFAULT_CACHE_FILE = Path("~/.cache/garmin/bearer_token.json").expanduser()


class GarminAuthError(RuntimeError):
    """Raised when authentication with Garmin Connect fails."""
    pass


class GarminClient:
    """Native HTTP client for Garmin Connect Personal API."""

    def __init__(
        self,
        api_key: str | None = None,
        key_file: Path | str | None = None,
        base_url: str = DEFAULT_BASE_URL,
    ):
        self.base_url = base_url.rstrip("/")
        self._explicit_key = api_key
        self._key_file = Path(key_file).expanduser() if key_file else DEFAULT_KEY_FILE
        self._cached_token: str | None = None
        self._token_expires_at: float = 0.0
        self._timezone: ZoneInfo | None = None
        self._http = httpx.Client(timeout=30.0)

    def get_api_key(self) -> str:
        """Resolve the API key from explicit key, env var, or config file."""
        if self._explicit_key:
            return self._explicit_key.strip()

        env_val = os.environ.get("GARMIN_PERSONAL_API_KEY")
        if env_val:
            return env_val.strip()

        if self._key_file.exists():
            key = self._key_file.read_text(encoding="utf-8").strip()
            if key:
                return key

        raise GarminAuthError(
            f"No usable Garmin Personal API key found.\n"
            f"Save your key to {self._key_file} or set GARMIN_PERSONAL_API_KEY."
        )

    def get_bearer_token(self, force_refresh: bool = False) -> str:
        """Obtain a Bearer token, using cache when valid or exchanging API key."""
        now = time.time()
        # In-memory check
        if not force_refresh and self._cached_token and now < self._token_expires_at - 60:
            return self._cached_token

        # Disk cache check
        if not force_refresh and DEFAULT_CACHE_FILE.exists():
            try:
                data = json.loads(DEFAULT_CACHE_FILE.read_text(encoding="utf-8"))
                cached = data.get("access_token")
                expires = data.get("expires_at", 0)
                if cached and now < expires - 60:
                    self._cached_token = cached
                    self._token_expires_at = expires
                    return cached
            except Exception:
                pass

        # Exchange API Key for fresh token
        api_key = self.get_api_key()
        token_url = f"{self.base_url}/personalapitoken-service/personal-api/personal-api-key/token"
        try:
            resp = self._http.post(token_url, headers={"Authorization": f"ApiKey {api_key}"})
            if resp.status_code in (401, 403):
                raise GarminAuthError(f"Garmin rejected the API key (HTTP {resp.status_code}). Check that your key is valid.")
            resp.raise_for_status()

            # Garmin returns raw JWT string (text/plain) or JSON
            content_type = resp.headers.get("content-type", "")
            if "application/json" in content_type:
                data = resp.json()
                if isinstance(data, dict):
                    access_token = data.get("access_token") or data.get("token") or resp.text.strip()
                    expires_in = data.get("expires_in", 86400)
                else:
                    access_token = data if isinstance(data, str) else resp.text.strip()
                    expires_in = 86400
            else:
                access_token = resp.text.strip()
                expires_in = 86400

            self._cached_token = access_token
            self._token_expires_at = now + float(expires_in)

            # Persist to disk cache, readable only by the owner: the file
            # holds a live bearer token for the account.
            try:
                DEFAULT_CACHE_FILE.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                fd = os.open(DEFAULT_CACHE_FILE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                if hasattr(os, "fchmod"):  # not available on Windows before Python 3.13
                    os.fchmod(fd, 0o600)  # also tighten a cache file left by older versions
                with os.fdopen(fd, "w", encoding="utf-8") as f:
                    json.dump({"access_token": access_token, "expires_at": self._token_expires_at}, f)
            except Exception:
                pass

            return access_token
        except httpx.HTTPError as exc:
            raise GarminAuthError(f"Failed to obtain token from Garmin: {exc}") from exc

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        """Make an authenticated request with retry on 401."""
        url = f"{self.base_url}{path}" if path.startswith("/") else f"{self.base_url}/{path}"
        token = self.get_bearer_token()
        headers = kwargs.pop("headers", {})
        headers["Authorization"] = f"Bearer {token}"
        headers.setdefault("Accept", "application/json")

        resp = self._http.request(method, url, headers=headers, **kwargs)
        if resp.status_code == 401:
            # Refresh token and retry once
            token = self.get_bearer_token(force_refresh=True)
            headers["Authorization"] = f"Bearer {token}"
            resp = self._http.request(method, url, headers=headers, **kwargs)

        resp.raise_for_status()
        return resp

    def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        """GET JSON endpoint."""
        resp = self._request("GET", path, params=params)
        if not resp.content:
            return None
        return resp.json()

    # ------------------ High-Level Operations ------------------

    def search_activities(
        self,
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        activity_type: str | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """Search activities with optional date range and activity type filtering."""
        params: dict[str, Any] = {"start": offset, "limit": limit}
        if start_date:
            params["startDate"] = start_date if isinstance(start_date, str) else start_date.isoformat()
        if end_date:
            params["endDate"] = end_date if isinstance(end_date, str) else end_date.isoformat()
        if activity_type:
            params["activityType"] = activity_type
        result = self.get("/activitylist-service/activities/search/activities", params=params)
        return result or []

    def search_all_activities(
        self,
        *,
        start_date: str | date | None = None,
        end_date: str | date | None = None,
        activity_type: str | None = None,
        page_size: int = 100,
        max_activities: int = 10000,
    ) -> list[dict[str, Any]]:
        """Fetch every activity in the date range by paging through search results.

        A single search call returns at most `limit` activities, newest first,
        so a capped call silently drops the oldest part of a long range.
        `max_activities` is a safety stop, not an expected limit.
        """
        activities: list[dict[str, Any]] = []
        while len(activities) < max_activities:
            page = self.search_activities(
                start_date=start_date,
                end_date=end_date,
                activity_type=activity_type,
                limit=page_size,
                offset=len(activities),
            )
            activities.extend(page)
            if len(page) < page_size:
                break
        return activities[:max_activities]

    def get_activity(self, activity_id: int | str) -> dict[str, Any]:
        """Fetch full activity details including splits and metadata."""
        try:
            result = self.get(f"/activity-service/activity/{activity_id}")
        except httpx.HTTPStatusError as exc:
            # Garmin answers 403 for an ID that belongs to another account or
            # does not exist, e.g. a Strava activity ID.
            if exc.response.status_code in (403, 404):
                raise ValueError(
                    f"Activity {activity_id} not found or not accessible with this account "
                    f"(HTTP {exc.response.status_code}). Is this a Garmin Connect activity ID?"
                ) from exc
            raise
        if not result:
            raise ValueError(f"Activity {activity_id} not found.")
        return result

    def get_activity_workouts(self, activity_id: int | str) -> list[dict[str, Any]]:
        """Fetch structured workouts associated with an activity."""
        result = self.get(f"/activity-service/activity/{activity_id}/workouts")
        return result or []

    def download_activity_bytes(self, activity_id: int | str) -> bytes:
        """Download the original activity file (a zip holding the .fit) into memory."""
        resp = self._request(
            "GET",
            f"/download-service/files/activity/{activity_id}",
            # The Personal API download endpoint answers 406 to a zip-only
            # Accept header and 200 to */* (verified 2026-10-26).
            headers={"Accept": "*/*"},
        )
        return resp.content

    def download_activity(self, activity_id: int | str, output_dir: Path | str = "./downloads") -> Path:
        """Download original activity zip/.fit file."""
        out_path = Path(output_dir).expanduser()
        out_path.mkdir(parents=True, exist_ok=True)
        zip_file = out_path / f"activity_{activity_id}.zip"
        zip_file.write_bytes(self.download_activity_bytes(activity_id))
        return zip_file

    def get_daily_summaries(self, start_date: str | date, end_date: str | date) -> list[dict[str, Any]]:
        """Fetch daily wellness summaries across a date range.

        The Personal API does not expose this usersummary endpoint (it
        answers 404 for every range, verified 2026-10-26), while the sleep,
        HRV and Body Battery services do respond. Treat a 404 as "no daily
        summaries available" so callers such as the readiness assessment
        degrade gracefully instead of failing on the last of their calls.
        """
        s = start_date if isinstance(start_date, str) else start_date.isoformat()
        e = end_date if isinstance(end_date, str) else end_date.isoformat()
        try:
            result = self.get(f"/usersummary-service/usersummary/daily/{s}/{e}")
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return []
            raise
        return result.get("data", []) if isinstance(result, dict) else (result or [])

    def get_sleep_summaries(self, start_date: str | date, end_date: str | date) -> list[dict[str, Any]]:
        """Fetch nightly sleep records across a date range."""

        def fetch(s: str, e: str) -> list[dict[str, Any]]:
            result = self.get(
                "/sleep-service/sleep/dailySleepSummariesByDate",
                params={"startDate": s, "endDate": e},
            )
            return result or []

        return _fetch_in_windows(fetch, start_date, end_date)

    def get_hrv_summaries(self, start_date: str | date, end_date: str | date) -> list[dict[str, Any]]:
        """Fetch overnight HRV records across a date range."""
        s = start_date if isinstance(start_date, str) else start_date.isoformat()
        e = end_date if isinstance(end_date, str) else end_date.isoformat()
        result = self.get(f"/hrv-service/hrv/daily/{s}/{e}")
        return result or []

    def get_body_battery(self, start_date: str | date, end_date: str | date) -> list[dict[str, Any]]:
        """Fetch daily Body Battery data across a date range."""

        def fetch(s: str, e: str) -> list[dict[str, Any]]:
            result = self.get(
                "/wellness-service/wellness/bodyBattery/reports/daily",
                params={"startDate": s, "endDate": e},
            )
            return result or []

        return _fetch_in_windows(fetch, start_date, end_date)

    def get_heart_rate_zones(self) -> list[dict[str, Any]]:
        """Fetch heart rate zones."""
        result = self.get("/biometric-service/heartRateZones/v2")
        return result or []

    def get_fitness_age(self, calendar_date: str | date) -> dict[str, Any]:
        """Fetch fitness age evaluation."""
        d = calendar_date if isinstance(calendar_date, str) else calendar_date.isoformat()
        result = self.get(f"/fitnessage-service/fitnessage/v2/{d}")
        return result or {}

    def get_profile(self) -> dict[str, Any]:
        """Fetch profile settings."""
        result = self.get("/userprofile-service/userprofile/settings")
        return result or {}

    def today(self) -> date:
        """Today's date in the athlete's Garmin profile timezone.

        Activity start times are in the athlete's local time, so "today"
        must be too: on a UTC host, date.today() is still yesterday for an
        athlete in Tokyo until 09:00 local time. Falls back to the host's
        date if the profile has no usable timezone.
        """
        if self._timezone is None:
            try:
                self._timezone = ZoneInfo(self.get_profile()["timeZone"])
            except Exception:
                return date.today()
        return datetime.now(self._timezone).date()


# The Personal API rejects longer ranges with HTTP 400: sleep accepts up to
# 32 days and Body Battery up to 29 (verified 2026-10-27).
WELLNESS_WINDOW_DAYS = 28


def _fetch_in_windows(
    fetch: Callable[[str, str], list[dict[str, Any]]],
    start_date: str | date,
    end_date: str | date,
) -> list[dict[str, Any]]:
    """Call fetch(start, end) over consecutive windows small enough for the API."""
    start = date.fromisoformat(start_date) if isinstance(start_date, str) else start_date
    end = date.fromisoformat(end_date) if isinstance(end_date, str) else end_date
    records: list[dict[str, Any]] = []
    while start <= end:
        window_end = min(end, start + timedelta(days=WELLNESS_WINDOW_DAYS - 1))
        records.extend(fetch(start.isoformat(), window_end.isoformat()))
        start = window_end + timedelta(days=1)
    return records


# Singleton instance
default_client = GarminClient()
