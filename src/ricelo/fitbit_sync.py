"""Copy exercises from Google Health (Fitbit) to Garmin Connect.

For each new Google Health exercise session:
1. skip it if it was synced before (local state file) or if a Garmin
   activity already covers the same time (e.g. you also wore a Garmin watch);
2. download it as TCX with the Google Health API's exportExerciseTcx;
3. set the TCX sport from the exercise type and fill in heart rate from the
   heart-rate samples where the export has none;
4. upload it to Garmin Connect and remember the result.
"""

from __future__ import annotations

import bisect
import json
import xml.etree.ElementTree as ET
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

from .google_health import CONFIG_DIR, parse_rfc3339, write_private_json

DEFAULT_STATE_FILE = CONFIG_DIR / "fitbit-sync-state.json"

TCX_NS = "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"
ET.register_namespace("", TCX_NS)
ET.register_namespace("ax", "http://www.garmin.com/xmlschemas/ActivityExtension/v2")
ET.register_namespace("xsi", "http://www.w3.org/2001/XMLSchema-instance")

# TCX only knows Running, Biking and Other. Garmin shows "Other" activities
# under a generic type that can be changed in Garmin Connect afterwards.
RUNNING_TYPES = {"RUNNING", "TRAIL_RUN", "TREADMILL", "INCLINE_RUN", "TRACK_AND_FIELD", "WATER_JOGGING"}
BIKING_TYPES = {"BIKING", "OUTDOOR_BIKE", "MOUNTAIN_BIKE", "ELECTRIC_BIKE", "STATIONARY_BIKE", "SPINNING", "HAND_CYCLING", "ASSAULT_BIKE"}

# A heart-rate sample counts for a trackpoint if it is at most this far away.
HR_MATCH_SECONDS = 10


def tcx_sport(exercise_type: str) -> str:
    if exercise_type in RUNNING_TYPES:
        return "Running"
    if exercise_type in BIKING_TYPES:
        return "Biking"
    return "Other"


@dataclass
class Exercise:
    name: str  # users/{user}/dataTypes/exercise/dataPoints/{id}
    id: str
    exercise_type: str
    display_name: str
    start: datetime
    end: datetime
    has_gps: bool

    @classmethod
    def from_data_point(cls, point: dict[str, Any]) -> Exercise:
        ex = point.get("exercise") or {}
        interval = ex.get("interval") or {}
        name = point["name"]
        return cls(
            name=name,
            id=name.rsplit("/", 1)[-1],
            exercise_type=ex.get("exerciseType", "OTHER"),
            display_name=ex.get("displayName") or ex.get("exerciseType", "Exercise"),
            start=parse_rfc3339(interval["startTime"]),
            end=parse_rfc3339(interval["endTime"]),
            has_gps=bool((ex.get("exerciseMetadata") or {}).get("hasGps")),
        )


def garmin_interval(activity: dict[str, Any]) -> tuple[datetime, datetime] | None:
    """UTC start and end of a Garmin activity from its search summary."""
    start = activity.get("startTimeGMT")
    if not start:
        return None
    begin = datetime.fromisoformat(start.replace(" ", "T")).replace(tzinfo=UTC)
    seconds = activity.get("elapsedDuration") or activity.get("duration") or 0
    return begin, begin + timedelta(seconds=float(seconds))


def find_overlap(exercise: Exercise, garmin_activities: Iterable[dict[str, Any]]) -> dict[str, Any] | None:
    """The Garmin activity that covers most of the same time, if any.

    Two recordings of one workout rarely start on the same second, so they
    match when they share at least half of the shorter one's duration.
    """
    for activity in garmin_activities:
        span = garmin_interval(activity)
        if not span:
            continue
        g_start, g_end = span
        shared = (min(exercise.end, g_end) - max(exercise.start, g_start)).total_seconds()
        shorter = min((exercise.end - exercise.start).total_seconds(), (g_end - g_start).total_seconds())
        if shared > 0 and shared >= 0.5 * max(shorter, 60):
            return activity
    return None


def _q(tag: str) -> str:
    return f"{{{TCX_NS}}}{tag}"


# Child order of a Trackpoint in the TCX schema; HeartRateBpm goes after these.
_BEFORE_HR = ("Time", "Position", "AltitudeMeters", "DistanceMeters")


def prepare_tcx(
    tcx: bytes,
    *,
    sport: str,
    heart_rate: list[tuple[datetime, int]] | None = None,
    notes: str | None = None,
) -> tuple[bytes, int]:
    """Set the sport, fill missing heart rate and add notes.

    Returns the new TCX and the number of trackpoints that got a heart rate.
    """
    root = ET.fromstring(tcx)
    times = [t for t, _ in heart_rate or []]
    filled = 0
    for activity in root.iter(_q("Activity")):
        activity.set("Sport", sport)
        if heart_rate:
            for point in activity.iter(_q("Trackpoint")):
                if point.find(_q("HeartRateBpm")) is not None:
                    continue
                time_el = point.find(_q("Time"))
                if time_el is None or not time_el.text:
                    continue
                bpm = _nearest(parse_rfc3339(time_el.text.strip()), times, heart_rate)
                if bpm is None:
                    continue
                hr_el = ET.Element(_q("HeartRateBpm"))
                ET.SubElement(hr_el, _q("Value")).text = str(bpm)
                index = max((i + 1 for i, c in enumerate(point) if c.tag in {_q(t) for t in _BEFORE_HR}), default=0)
                point.insert(index, hr_el)
                filled += 1
        if notes and activity.find(_q("Notes")) is None:
            # Notes follow the last Lap in the TCX schema.
            laps = activity.findall(_q("Lap"))
            index = list(activity).index(laps[-1]) + 1 if laps else len(activity)
            notes_el = ET.Element(_q("Notes"))
            notes_el.text = notes
            activity.insert(index, notes_el)
    return ET.tostring(root, encoding="utf-8", xml_declaration=True), filled


def _nearest(when: datetime, times: list[datetime], samples: list[tuple[datetime, int]]) -> int | None:
    i = bisect.bisect_left(times, when)
    best = None
    for j in (i - 1, i):
        if 0 <= j < len(times):
            gap = abs((times[j] - when).total_seconds())
            if gap <= HR_MATCH_SECONDS and (best is None or gap < best[0]):
                best = (gap, samples[j][1])
    return best[1] if best else None


def tcx_has_heart_rate(tcx: bytes) -> bool:
    return b"HeartRateBpm" in tcx


class SyncState:
    """Which Google Health exercises were already handled, kept on disk."""

    def __init__(self, path: Path | str = DEFAULT_STATE_FILE):
        self.path = Path(path).expanduser()
        self.synced: dict[str, dict[str, Any]] = {}
        if self.path.exists():
            self.synced = json.loads(self.path.read_text(encoding="utf-8")).get("synced", {})

    def __contains__(self, exercise_id: str) -> bool:
        return exercise_id in self.synced

    def record(self, exercise: Exercise, status: str, garmin_activity_id: Any = None) -> None:
        self.synced[exercise.id] = {
            "status": status,
            "garmin_activity_id": garmin_activity_id,
            "start": exercise.start.isoformat(),
            "type": exercise.exercise_type,
            "synced_at": datetime.now(UTC).isoformat(timespec="seconds"),
        }
        write_private_json(self.path, {"synced": self.synced})


class GoogleSource(Protocol):
    def list_exercises(self, start: date, end: date | None = None) -> list[dict[str, Any]]: ...
    def export_tcx(self, exercise_name: str, partial_data: bool = True) -> bytes: ...
    def heart_rate_samples(self, start: datetime, end: datetime) -> list[tuple[datetime, int]]: ...


class GarminTarget(Protocol):
    def search_all_activities(self, **kwargs) -> list[dict[str, Any]]: ...
    def upload_activity(self, filename: str, content: bytes) -> dict[str, Any]: ...


@dataclass
class SyncResult:
    exercise: Exercise
    action: str  # uploaded, duplicate, failed, would-upload, skipped-synced, skipped-overlap, skipped-type
    detail: str = ""
    garmin_activity_id: Any = None


@dataclass
class SyncOptions:
    start: date
    end: date | None = None
    types: set[str] | None = None
    skip_overlapping: bool = True
    fill_heart_rate: bool = True
    dry_run: bool = False
    save_dir: Path | None = None
    state_file: Path | str = DEFAULT_STATE_FILE
    progress: Callable[[str], None] = field(default=lambda msg: None)


def sync(google: GoogleSource, garmin: GarminTarget, options: SyncOptions) -> list[SyncResult]:
    state = SyncState(options.state_file)
    exercises = [Exercise.from_data_point(p) for p in google.list_exercises(options.start, options.end)]
    exercises.sort(key=lambda e: e.start)

    garmin_activities: list[dict[str, Any]] = []
    if options.skip_overlapping and exercises:
        # One day of margin on each side: Garmin searches by local date.
        garmin_activities = garmin.search_all_activities(
            start_date=(exercises[0].start.date() - timedelta(days=1)).isoformat(),
            end_date=(exercises[-1].end.date() + timedelta(days=1)).isoformat(),
        )

    results: list[SyncResult] = []
    for ex in exercises:
        if ex.id in state:
            results.append(SyncResult(ex, "skipped-synced", garmin_activity_id=state.synced[ex.id].get("garmin_activity_id")))
            continue
        if options.types and ex.exercise_type not in options.types:
            results.append(SyncResult(ex, "skipped-type"))
            continue
        overlap = find_overlap(ex, garmin_activities) if options.skip_overlapping else None
        if overlap:
            results.append(SyncResult(
                ex, "skipped-overlap",
                f"Garmin already has {overlap.get('activityName') or 'an activity'}",
                overlap.get("activityId"),
            ))
            continue

        try:
            options.progress(f"Exporting {ex.display_name} {ex.start:%Y-%m-%d %H:%M}")
            tcx = google.export_tcx(ex.name)
            heart_rate = None
            if options.fill_heart_rate and not tcx_has_heart_rate(tcx):
                heart_rate = google.heart_rate_samples(ex.start, ex.end)
            tcx, filled = prepare_tcx(
                tcx,
                sport=tcx_sport(ex.exercise_type),
                heart_rate=heart_rate,
                notes=f"{ex.display_name} (imported from Google Health / Fitbit)",
            )
            filename = f"google-health-{ex.start:%Y%m%d-%H%M}-{ex.id}.tcx"
            if options.save_dir:
                options.save_dir.mkdir(parents=True, exist_ok=True)
                (options.save_dir / filename).write_bytes(tcx)
            hr_note = f"heart rate added to {filled} points" if filled else ""
            if options.dry_run:
                results.append(SyncResult(ex, "would-upload", hr_note))
                continue

            options.progress(f"Uploading {filename}")
            outcome = garmin.upload_activity(filename, tcx)
        except Exception as exc:  # one bad exercise must not stop the rest
            results.append(SyncResult(ex, "failed", str(exc)))
            continue

        status = outcome["status"]
        if status in ("uploaded", "duplicate"):
            state.record(ex, status, outcome.get("activity_id"))
        results.append(SyncResult(ex, status, outcome.get("message") or hr_note, outcome.get("activity_id")))
    return results
