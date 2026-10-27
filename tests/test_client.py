"""Unit tests for Garmin client error handling."""

import httpx
import pytest

from ricelo.client import GarminClient


def _status_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "https://personal.connectapi.garmin.com/test")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("error", request=request, response=response)


def test_daily_summaries_404_returns_empty(monkeypatch):
    client = GarminClient(api_key="test-key")

    def fake_get(path, params=None):
        raise _status_error(404)

    monkeypatch.setattr(client, "get", fake_get)
    assert client.get_daily_summaries("2026-10-25", "2026-10-26") == []


def test_daily_summaries_other_errors_reraise(monkeypatch):
    client = GarminClient(api_key="test-key")

    def fake_get(path, params=None):
        raise _status_error(500)

    monkeypatch.setattr(client, "get", fake_get)
    with pytest.raises(httpx.HTTPStatusError):
        client.get_daily_summaries("2026-10-25", "2026-10-26")


def test_daily_summaries_passes_through_data(monkeypatch):
    client = GarminClient(api_key="test-key")
    monkeypatch.setattr(client, "get", lambda path, params=None: {"data": [{"calendarDate": "2026-10-25"}]})
    assert client.get_daily_summaries("2026-10-25", "2026-10-26") == [{"calendarDate": "2026-10-25"}]


def test_latest_body_battery_record_skips_empty_today():
    from ricelo.recovery import latest_body_battery_record

    yesterday = {"date": "2026-10-25", "charged": 0, "bodyBatteryValuesArray": [[0, 63], [1, 29]]}
    today = {"date": "2026-10-26", "charged": None, "bodyBatteryValuesArray": []}
    today_null_values = {"date": "2026-10-26", "charged": None, "bodyBatteryValuesArray": [[0, None], [1, None]]}
    assert latest_body_battery_record([yesterday, today]) is yesterday
    assert latest_body_battery_record([yesterday, today_null_values]) is yesterday
    assert latest_body_battery_record([]) is None
    assert latest_body_battery_record(None) is None


def test_readiness_body_battery_max_from_values_array():
    from ricelo.recovery import evaluate_daily_readiness

    bb = {"charged": 41, "bodyBatteryValuesArray": [[0, 55], [1, 91], [2, 63]]}
    status = evaluate_daily_readiness(body_battery_data=bb)
    assert status.body_battery_max == 91
    assert status.body_battery_charged == 41
    # The verdict scores the 91 peak, not the 41 charge total.
    assert "High Readiness" in status.readiness_verdict


def test_readiness_hrv_status_scoring():
    from ricelo.recovery import evaluate_daily_readiness

    def verdict(status):
        return evaluate_daily_readiness(hrv_data={"hrvSummary": {"status": status}}).readiness_verdict

    assert "High Readiness" in verdict("BALANCED")
    # "UNBALANCED" contains "balanced" and used to score as balanced.
    assert "Low Readiness" in verdict("UNBALANCED")
    assert "Low Readiness" in verdict("LOW")
    assert "Low Readiness" in verdict("POOR")


def test_search_all_activities_pages_until_short_page(monkeypatch):
    client = GarminClient(api_key="test-key")
    calls = []

    def fake_search(*, start_date=None, end_date=None, activity_type=None, limit=50, offset=0):
        calls.append(offset)
        return [{"activityId": i} for i in range(offset, min(offset + limit, 250))]

    monkeypatch.setattr(client, "search_activities", fake_search)
    result = client.search_all_activities(start_date="2026-01-01", page_size=100)
    assert [a["activityId"] for a in result] == list(range(250))
    assert calls == [0, 100, 200]


def test_search_all_activities_respects_max(monkeypatch):
    client = GarminClient(api_key="test-key")
    monkeypatch.setattr(
        client,
        "search_activities",
        lambda **kw: [{"activityId": i} for i in range(kw["offset"], kw["offset"] + kw["limit"])],
    )
    assert len(client.search_all_activities(page_size=100, max_activities=250)) == 250


def test_token_cache_is_owner_only(monkeypatch, tmp_path):
    import os
    import stat

    from ricelo import client as client_module

    cache = tmp_path / "garmin" / "bearer_token.json"
    monkeypatch.setattr(client_module, "DEFAULT_CACHE_FILE", cache)
    client = GarminClient(api_key="test-key")

    def fake_post(url, headers=None):
        return httpx.Response(200, text="jwt-token", request=httpx.Request("POST", url))

    monkeypatch.setattr(client._http, "post", fake_post)
    assert client.get_bearer_token(force_refresh=True) == "jwt-token"
    if os.name == "posix":
        assert stat.S_IMODE(cache.stat().st_mode) == 0o600
