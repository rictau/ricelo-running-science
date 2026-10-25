"""Native Garmin Connect Personal API client with automatic token management.

100% self-contained with no external third-party Garmin SDK dependencies.
"""

from __future__ import annotations

import json
import os
import time
from datetime import date, datetime
from pathlib import Path
from typing import Any

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
                access_token = data.get("access_token") or data.get("token") or resp.text.strip()
                expires_in = data.get("expires_in", 86400) if isinstance(data, dict) else 86400
            else:
                access_token = resp.text.strip()
                expires_in = 86400

            self._cached_token = access_token
            self._token_expires_at = now + float(expires_in)

            # Persist to disk cache
            try:
                DEFAULT_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
                DEFAULT_CACHE_FILE.write_text(
                    json.dumps({"access_token": access_token, "expires_at": self._token_expires_at}),
                    encoding="utf-8",
                )
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

    def get_activity(self, activity_id: int | str) -> dict[str, Any]:
        """Fetch full activity details including splits and metadata."""
        result = self.get(f"/activity-service/activity/{activity_id}")
        if not result:
            raise ValueError(f"Activity {activity_id} not found.")
        return result

    def get_activity_workouts(self, activity_id: int | str) -> list[dict[str, Any]]:
        """Fetch structured workouts associated with an activity."""
        result = self.get(f"/activity-service/activity/{activity_id}/workouts")
        return result or []

    def download_activity(self, activity_id: int | str, output_dir: Path | str = "./downloads") -> Path:
        """Download original activity zip/.fit file."""
        resp = self._request(
            "GET",
            f"/download-service/files/activity/{activity_id}",
            headers={"Accept": "application/zip, application/octet-stream"},
        )
        out_path = Path(output_dir).expanduser()
        out_path.mkdir(parents=True, exist_ok=True)
        zip_file = out_path / f"activity_{activity_id}.zip"
        zip_file.write_bytes(resp.content)
        return zip_file

    def get_daily_summaries(self, start_date: str | date, end_date: str | date) -> list[dict[str, Any]]:
        """Fetch daily wellness summaries across a date range."""
        s = start_date if isinstance(start_date, str) else start_date.isoformat()
        e = end_date if isinstance(end_date, str) else end_date.isoformat()
        result = self.get(f"/usersummary-service/usersummary/daily/{s}/{e}")
        return result.get("data", []) if isinstance(result, dict) else (result or [])

    def get_sleep_summaries(self, start_date: str | date, end_date: str | date) -> list[dict[str, Any]]:
        """Fetch nightly sleep records across a date range."""
        s = start_date if isinstance(start_date, str) else start_date.isoformat()
        e = end_date if isinstance(end_date, str) else end_date.isoformat()
        result = self.get(
            "/sleep-service/sleep/dailySleepSummariesByDate",
            params={"startDate": s, "endDate": e},
        )
        return result or []

    def get_hrv_summaries(self, start_date: str | date, end_date: str | date) -> list[dict[str, Any]]:
        """Fetch overnight HRV records across a date range."""
        s = start_date if isinstance(start_date, str) else start_date.isoformat()
        e = end_date if isinstance(end_date, str) else end_date.isoformat()
        result = self.get(f"/hrv-service/hrv/daily/{s}/{e}")
        return result or []

    def get_body_battery(self, start_date: str | date, end_date: str | date) -> list[dict[str, Any]]:
        """Fetch daily Body Battery data across a date range."""
        s = start_date if isinstance(start_date, str) else start_date.isoformat()
        e = end_date if isinstance(end_date, str) else end_date.isoformat()
        result = self.get(
            "/wellness-service/wellness/bodyBattery/reports/daily",
            params={"startDate": s, "endDate": e},
        )
        return result or []

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


# Singleton instance
default_client = GarminClient()
