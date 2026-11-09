"""Tests for the Google Health (Fitbit) to Garmin Connect sync."""

import json
import time
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest

from ricelo.client import GarminClient, parse_upload_result
from ricelo.fitbit_sync import (
    Exercise,
    SyncOptions,
    find_overlap,
    prepare_tcx,
    sync,
    tcx_sport,
)
from ricelo.google_health import (
    GoogleHealthAuthError,
    GoogleHealthClient,
    build_auth_url,
    parse_redirect,
    parse_rfc3339,
)



TCX = b"""<?xml version="1.0" encoding="UTF-8"?>
<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">
  <Activities>
    <Activity Sport="Other">
      <Id>2026-09-03T11:20:00.000Z</Id>
      <Lap StartTime="2026-09-03T11:20:00.000Z">
        <TotalTimeSeconds>1800</TotalTimeSeconds>
        <Track>
          <Trackpoint>
            <Time>2026-09-03T11:20:00.000Z</Time>
            <Position><LatitudeDegrees>35.0</LatitudeDegrees><LongitudeDegrees>139.0</LongitudeDegrees></Position>
            <DistanceMeters>0</DistanceMeters>
          </Trackpoint>
          <Trackpoint>
            <Time>2026-09-03T11:21:00.000Z</Time>
            <DistanceMeters>200</DistanceMeters>
          </Trackpoint>
        </Track>
      </Lap>
    </Activity>
  </Activities>
</TrainingCenterDatabase>
"""


def exercise_point(ex_id="111", start="2026-09-03T11:20:00Z", end="2026-09-03T11:50:00Z", kind="RUNNING"):
    return {
        "name": f"users/42/dataTypes/exercise/dataPoints/{ex_id}",
        "exercise": {
            "interval": {"startTime": start, "endTime": end},
            "exerciseType": kind,
            "displayName": kind.title(),
            "exerciseMetadata": {"hasGps": True},
        },
    }


# ------------------ parsing ------------------


def test_parse_rfc3339_handles_nanoseconds_and_offsets():
    assert parse_rfc3339("2026-09-03T11:20:00.123456789Z") == datetime(2026, 9, 3, 11, 20, 0, 123456, tzinfo=UTC)
    assert parse_rfc3339("2026-09-03T20:20:00+09:00") == datetime(2026, 9, 3, 11, 20, tzinfo=UTC)


def test_exercise_from_data_point():
    ex = Exercise.from_data_point(exercise_point())
    assert ex.id == "111"
    assert ex.exercise_type == "RUNNING"
    assert ex.start == datetime(2026, 9, 3, 11, 20, tzinfo=UTC)
    assert ex.has_gps


def test_tcx_sport_mapping():
    assert tcx_sport("TREADMILL") == "Running"
    assert tcx_sport("MOUNTAIN_BIKE") == "Biking"
    assert tcx_sport("WALKING") == "Other"


# ------------------ TCX ------------------


def test_prepare_tcx_sets_sport_fills_hr_and_notes():
    hr = [
        (datetime(2026, 9, 3, 11, 20, 3, tzinfo=UTC), 120),
        (datetime(2026, 9, 3, 11, 21, 30, tzinfo=UTC), 150),  # 30 s from the 2nd point: too far
    ]
    out, filled = prepare_tcx(TCX, sport="Running", heart_rate=hr, notes="Run (imported)")
    assert filled == 1
    text = out.decode()
    assert 'Sport="Running"' in text
    assert "<Value>120</Value>" in text and "150" not in text
    # HeartRateBpm must follow DistanceMeters, per the TCX schema order.
    assert text.index("<DistanceMeters>0") < text.index("<HeartRateBpm>")
    assert "<Notes>Run (imported)</Notes>" in text
    # The default namespace stays unprefixed so Garmin accepts the file.
    assert "<TrainingCenterDatabase xmlns=" in text
    assert text.index("</Lap>") < text.index("<Notes>")


def test_prepare_tcx_keeps_existing_heart_rate():
    tcx = TCX.replace(
        b"<DistanceMeters>0</DistanceMeters>",
        b"<DistanceMeters>0</DistanceMeters><HeartRateBpm><Value>99</Value></HeartRateBpm>",
    )
    hr = [(datetime(2026, 9, 3, 11, 20, tzinfo=UTC), 120)]
    out, filled = prepare_tcx(tcx, sport="Running", heart_rate=hr)
    assert filled == 0
    assert b"<Value>99</Value>" in out and b"<Value>120</Value>" not in out


# ------------------ overlap ------------------


def test_find_overlap_matches_same_workout_recorded_by_garmin():
    ex = Exercise.from_data_point(exercise_point())
    garmin = [
        {"activityId": 1, "startTimeGMT": "2026-09-03 08:00:00", "duration": 1800},
        {"activityId": 2, "startTimeGMT": "2026-09-03 11:21:10", "duration": 1750},
    ]
    assert find_overlap(ex, garmin)["activityId"] == 2


def test_find_overlap_ignores_brief_touch():
    ex = Exercise.from_data_point(exercise_point())
    garmin = [{"activityId": 3, "startTimeGMT": "2026-09-03 11:48:00", "duration": 3600}]
    assert find_overlap(ex, garmin) is None


# ------------------ Garmin upload ------------------


def test_parse_upload_result_variants():
    ok = {"detailedImportResult": {"uploadId": 7, "successes": [{"internalId": 555}], "failures": []}}
    assert parse_upload_result(ok)["status"] == "uploaded"
    assert parse_upload_result(ok)["activity_id"] == 555

    dup = {"detailedImportResult": {"successes": [], "failures": [{"messages": [{"code": 202, "content": "Duplicate Activity."}]}]}}
    assert parse_upload_result(dup)["status"] == "duplicate"

    bad = {"detailedImportResult": {"successes": [], "failures": [{"messages": [{"content": "Invalid file"}]}]}}
    assert parse_upload_result(bad) == {"status": "failed", "activity_id": None, "message": "Invalid file", "raw": bad}

    pending = {"detailedImportResult": {"uploadId": 8, "successes": [], "failures": []}}
    assert parse_upload_result(pending)["status"] == "uploaded"


def test_upload_activity_409_is_duplicate(monkeypatch):
    client = GarminClient(api_key="k")
    request = httpx.Request("POST", "https://x/upload-service/upload")

    def fake_request(method, path, **kwargs):
        assert method == "POST" and path == "/upload-service/upload"
        assert kwargs["files"]["file"][0] == "a.tcx"
        raise httpx.HTTPStatusError("dup", request=request, response=httpx.Response(409, request=request))

    monkeypatch.setattr(client, "_request", fake_request)
    assert client.upload_activity("a.tcx", b"<x/>")["status"] == "duplicate"


# ------------------ sync flow ------------------


class FakeGoogle:
    def __init__(self, points, tcx=TCX):
        self.points = points
        self.tcx = tcx
        self.exported = []
        self.hr_calls = 0

    def list_exercises(self, start, end=None):
        return self.points

    def export_tcx(self, name, partial_data=True):
        self.exported.append(name)
        return self.tcx

    def heart_rate_samples(self, start, end):
        self.hr_calls += 1
        return [(start, 130)]


class FakeGarmin:
    def __init__(self, activities=(), outcome=None):
        self.activities = list(activities)
        self.uploads = []
        self.outcome = outcome or {"status": "uploaded", "activity_id": 999, "message": ""}

    def search_all_activities(self, **kwargs):
        return self.activities

    def upload_activity(self, filename, content):
        self.uploads.append((filename, content))
        return self.outcome


def test_sync_uploads_then_remembers(tmp_path):
    state = tmp_path / "state.json"
    google = FakeGoogle([exercise_point("1"), exercise_point("2", "2026-09-04T11:20:00Z", "2026-09-04T11:50:00Z", "WALKING")])
    garmin = FakeGarmin()
    options = SyncOptions(start=date(2026, 9, 1), state_file=state)

    results = sync(google, garmin, options)
    assert [r.action for r in results] == ["uploaded", "uploaded"]
    assert len(garmin.uploads) == 2
    assert b'Sport="Running"' in garmin.uploads[0][1]
    assert google.hr_calls == 2  # the export had no heart rate
    assert json.loads(state.read_text())["synced"]["1"]["garmin_activity_id"] == 999

    again = sync(google, FakeGarmin(), options)
    assert [r.action for r in again] == ["skipped-synced", "skipped-synced"]


def test_sync_skips_overlap_type_and_dry_run(tmp_path):
    points = [
        exercise_point("1"),
        exercise_point("2", "2026-09-04T11:20:00Z", "2026-09-04T11:50:00Z", "YOGA"),
        exercise_point("3", "2026-09-05T11:20:00Z", "2026-09-05T11:50:00Z", "RUNNING"),
    ]
    garmin = FakeGarmin([{"activityId": 77, "activityName": "Morning Run", "startTimeGMT": "2026-09-03 11:20:30", "duration": 1790}])
    options = SyncOptions(
        start=date(2026, 9, 1), types={"RUNNING"}, dry_run=True, save_dir=tmp_path / "tcx", state_file=tmp_path / "s.json"
    )
    results = sync(FakeGoogle(points), garmin, options)
    assert [r.action for r in results] == ["skipped-overlap", "skipped-type", "would-upload"]
    assert results[0].garmin_activity_id == 77
    assert garmin.uploads == []
    assert len(list((tmp_path / "tcx").glob("*.tcx"))) == 1
    assert not (tmp_path / "s.json").exists()


def test_sync_failure_is_retried_next_time(tmp_path):
    options = SyncOptions(start=date(2026, 9, 1), state_file=tmp_path / "s.json")
    garmin = FakeGarmin(outcome={"status": "failed", "activity_id": None, "message": "Invalid file"})
    results = sync(FakeGoogle([exercise_point("1")]), garmin, options)
    assert results[0].action == "failed"
    results = sync(FakeGoogle([exercise_point("1")]), FakeGarmin(), options)
    assert results[0].action == "uploaded"


# ------------------ Google Health client ------------------


def test_parse_redirect_checks_state():
    assert parse_redirect("http://localhost:8765/?state=abc&code=XYZ", "abc") == "XYZ"
    with pytest.raises(GoogleHealthAuthError):
        parse_redirect("http://localhost:8765/?state=evil&code=XYZ", "abc")
    with pytest.raises(GoogleHealthAuthError):
        parse_redirect("http://localhost:8765/?error=access_denied&state=abc", "abc")


def test_build_auth_url_requests_offline_access():
    url = build_auth_url("cid", "http://localhost:8765/", "s")
    assert "access_type=offline" in url and "googlehealth.location.readonly" in url


def _client(tmp_path, handler):
    token = tmp_path / "token.json"
    token.write_text(json.dumps({"access_token": "t", "refresh_token": "r", "expires_at": time.time() + 3600}))
    http = httpx.Client(transport=httpx.MockTransport(handler))
    return GoogleHealthClient(token_file=token, http=http)


def test_list_exercises_pages_and_filters(tmp_path):
    seen = []

    def handler(request):
        seen.append(request)
        assert request.headers["Authorization"] == "Bearer t"
        if "pageToken" not in request.url.params:
            return httpx.Response(200, json={"dataPoints": [exercise_point("1")], "nextPageToken": "p2"})
        return httpx.Response(200, json={"dataPoints": [exercise_point("2")]})

    client = _client(tmp_path, handler)
    points = client.list_exercises(date(2026, 9, 1), date(2026, 9, 30))
    assert [p["name"][-1] for p in points] == ["1", "2"]
    flt = seen[0].url.params["filter"]
    assert 'civil_start_time >= "2026-09-01"' in flt and 'civil_start_time < "2026-10-01"' in flt
    assert seen[0].url.path == "/v4/users/me/dataTypes/exercise/dataPoints"


def test_export_tcx_and_heart_rate(tmp_path):
    def handler(request):
        if request.url.path.endswith(":exportExerciseTcx"):
            assert request.url.params["alt"] == "media"
            return httpx.Response(200, content=TCX)
        assert request.url.path.endswith("/dataTypes/heart-rate/dataPoints")
        return httpx.Response(200, json={"dataPoints": [
            {"heartRate": {"sampleTime": {"physicalTime": "2026-09-03T11:20:05Z"}, "beatsPerMinute": "140"}},
            {"heartRate": {"sampleTime": {"physicalTime": "2026-09-03T11:20:01Z"}, "beatsPerMinute": "138"}},
        ]})

    client = _client(tmp_path, handler)
    assert client.export_tcx("users/me/dataTypes/exercise/dataPoints/1") == TCX
    start = datetime(2026, 9, 3, 11, 20, tzinfo=UTC)
    samples = client.heart_rate_samples(start, start + timedelta(minutes=30))
    assert [bpm for _, bpm in samples] == [138, 140]


def test_expired_token_is_refreshed_and_saved(tmp_path, monkeypatch):
    monkeypatch.setenv("GOOGLE_HEALTH_CLIENT_ID", "cid")
    monkeypatch.setenv("GOOGLE_HEALTH_CLIENT_SECRET", "sec")

    def handler(request):
        if request.url.host == "oauth2.googleapis.com":
            return httpx.Response(200, json={"access_token": "new", "expires_in": 3599})
        assert request.headers["Authorization"] == "Bearer new"
        return httpx.Response(200, json={"dataPoints": []})

    client = _client(tmp_path, handler)
    saved = json.loads((tmp_path / "token.json").read_text())
    saved["expires_at"] = 0
    (tmp_path / "token.json").write_text(json.dumps(saved))
    assert client.list_exercises(date(2026, 9, 1)) == []
    saved = json.loads((tmp_path / "token.json").read_text())
    assert saved["access_token"] == "new" and saved["refresh_token"] == "r"


@pytest.mark.parametrize("status", [401, 403, 404, 405])
def test_upload_refused_raises_not_allowed(monkeypatch, status):
    from ricelo.client import GarminUploadNotAllowed

    client = GarminClient(api_key="k")
    request = httpx.Request("POST", "https://x/upload-service/upload")

    def fake_request(method, path, **kwargs):
        raise httpx.HTTPStatusError("no", request=request, response=httpx.Response(status, request=request))

    monkeypatch.setattr(client, "_request", fake_request)
    with pytest.raises(GarminUploadNotAllowed, match="read-only"):
        client.upload_activity("a.tcx", b"<x/>")


def test_sync_stops_when_uploads_not_allowed(tmp_path):
    from ricelo.client import GarminUploadNotAllowed

    class RefusingGarmin(FakeGarmin):
        def upload_activity(self, filename, content):
            self.uploads.append(filename)
            raise GarminUploadNotAllowed("read-only")

    garmin = RefusingGarmin()
    points = [exercise_point("1"), exercise_point("2", "2026-09-04T11:20:00Z", "2026-09-04T11:50:00Z")]
    with pytest.raises(GarminUploadNotAllowed):
        sync(FakeGoogle(points), garmin, SyncOptions(start=date(2026, 9, 1), state_file=tmp_path / "s.json"))
    assert len(garmin.uploads) == 1
    assert not (tmp_path / "s.json").exists()
