"""Data models for Garmin activities, splits, and health metrics."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from pydantic import BaseModel, Field


def format_seconds(seconds: float | None) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    if seconds is None or seconds < 0:
        return "--:--"
    total_sec = int(round(seconds))
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def speed_to_pace_str(speed_mps: float | None) -> str:
    """Convert speed (meters/sec) to running pace (min:sec /km)."""
    if speed_mps is None or speed_mps <= 0.1:
        return "--:--"
    pace_sec = 1000.0 / speed_mps
    return f"{format_seconds(pace_sec)} /km"


class Split(BaseModel):
    """Represents a single split or lap within an activity."""
    split_index: int = 1
    distance_m: float = 0.0
    duration_s: float = 0.0
    avg_speed_mps: float = 0.0
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_cadence: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None

    @property
    def distance_km(self) -> float:
        return round(self.distance_m / 1000.0, 2)

    @property
    def pace_min_km(self) -> str:
        return speed_to_pace_str(self.avg_speed_mps)

    @property
    def speed_kmh(self) -> float:
        return round(self.avg_speed_mps * 3.6, 2)

    @property
    def duration_str(self) -> str:
        return format_seconds(self.duration_s)


class Activity(BaseModel):
    """Parsed Garmin Connect activity with rich performance metrics."""
    activity_id: int
    name: str = "Untitled Activity"
    activity_type: str = "other"
    activity_subtype: str | None = None
    start_time_local: str = ""
    distance_m: float = 0.0
    duration_s: float = 0.0
    elapsed_duration_s: float = 0.0
    moving_duration_s: float | None = None
    avg_speed_mps: float = 0.0
    max_speed_mps: float | None = None
    avg_hr: float | None = None
    max_hr: float | None = None
    avg_cadence: float | None = None
    max_cadence: float | None = None
    avg_power_w: float | None = None
    max_power_w: float | None = None
    elevation_gain_m: float | None = None
    elevation_loss_m: float | None = None
    aerobic_training_effect: float | None = None
    anaerobic_training_effect: float | None = None
    calories: float | None = None
    splits: list[Split] = Field(default_factory=list)
    raw_data: dict[str, Any] = Field(default_factory=dict, repr=False)

    @property
    def distance_km(self) -> float:
        return round(self.distance_m / 1000.0, 2)

    @property
    def distance_miles(self) -> float:
        return round(self.distance_m / 1609.34, 2)

    @property
    def pace_min_km(self) -> str:
        return speed_to_pace_str(self.avg_speed_mps)

    @property
    def speed_kmh(self) -> float:
        return round(self.avg_speed_mps * 3.6, 2)

    @property
    def duration_str(self) -> str:
        return format_seconds(self.duration_s)

    @property
    def aerobic_decoupling_pct(self) -> float | None:
        """
        Calculate Aerobic Decoupling (Cardiac Drift).
        Measures efficiency (Speed / HR) difference between 1st half and 2nd half.
        A positive value > 5% indicates cardiac drift / fatigue.
        """
        if not self.splits or len(self.splits) < 2:
            return None

        # Filter valid splits with both distance and HR
        valid_splits = [s for s in self.splits if s.distance_m > 100 and s.avg_hr and s.avg_hr > 40]
        if len(valid_splits) < 2:
            return None

        mid = len(valid_splits) // 2
        first_half = valid_splits[:mid]
        second_half = valid_splits[mid:]

        speed1 = sum(s.avg_speed_mps * s.duration_s for s in first_half) / max(sum(s.duration_s for s in first_half), 1.0)
        hr1 = sum(s.avg_hr * s.duration_s for s in first_half) / max(sum(s.duration_s for s in first_half), 1.0)

        speed2 = sum(s.avg_speed_mps * s.duration_s for s in second_half) / max(sum(s.duration_s for s in second_half), 1.0)
        hr2 = sum(s.avg_hr * s.duration_s for s in second_half) / max(sum(s.duration_s for s in second_half), 1.0)

        if hr1 <= 0 or hr2 <= 0 or speed1 <= 0:
            return None

        eff1 = speed1 / hr1
        eff2 = speed2 / hr2

        decoupling = ((eff1 - eff2) / eff1) * 100.0
        return round(decoupling, 2)

    @classmethod
    def from_api_dict(cls, data: dict[str, Any]) -> Activity:
        """Parse raw Garmin API activity dictionary into an Activity model."""
        summary = data.get("summaryDTO") or data

        # Extract splits / laps
        splits: list[Split] = []
        raw_splits = data.get("splitSummaries") or data.get("lapDTOs") or []
        for idx, s in enumerate(raw_splits, start=1):
            splits.append(
                Split(
                    split_index=s.get("splitIndex") or s.get("lapIndex") or idx,
                    distance_m=float(s.get("distance", 0.0)),
                    duration_s=float(s.get("duration", 0.0)),
                    avg_speed_mps=float(s.get("averageSpeed", 0.0)),
                    avg_hr=s.get("averageHR") or s.get("averageHeartRate"),
                    max_hr=s.get("maxHR") or s.get("maxHeartRate"),
                    avg_cadence=s.get("averageRunCadence") or s.get("averageBikeCadence") or s.get("averageCadence"),
                    elevation_gain_m=s.get("elevationGain"),
                    elevation_loss_m=s.get("elevationLoss"),
                )
            )

        # Activity type
        act_type_obj = data.get("activityTypeDTO") or summary.get("activityTypeDTO") or data.get("activityType") or summary.get("activityType") or {}
        if isinstance(act_type_obj, dict):
            act_type = act_type_obj.get("typeKey") or "other"
            act_subtype = act_type_obj.get("parentTypeId") or summary.get("activitySubType")
        else:
            act_type = str(act_type_obj)
            act_subtype = summary.get("activitySubType")

        return cls(
            activity_id=data.get("activityId") or summary.get("activityId", 0),
            name=data.get("activityName") or summary.get("activityName") or "Activity",
            activity_type=str(act_type),
            activity_subtype=str(act_subtype) if act_subtype else None,
            start_time_local=summary.get("startTimeLocal") or summary.get("startTimeGMT") or data.get("startTimeLocal") or "",
            distance_m=float(summary.get("distance", data.get("distance", 0.0))),
            duration_s=float(summary.get("duration", data.get("duration", 0.0))),
            elapsed_duration_s=float(summary.get("elapsedDuration", data.get("elapsedDuration", summary.get("duration", 0.0)))),
            moving_duration_s=summary.get("movingDuration") or data.get("movingDuration"),
            avg_speed_mps=float(summary.get("averageSpeed", data.get("averageSpeed", 0.0))),
            max_speed_mps=summary.get("maxSpeed") or data.get("maxSpeed"),
            avg_hr=summary.get("averageHR") or summary.get("averageHeartRate") or data.get("averageHR"),
            max_hr=summary.get("maxHR") or summary.get("maxHeartRate") or data.get("maxHR"),
            avg_cadence=summary.get("averageRunCadence") or summary.get("averageBikeCadence") or summary.get("averageRunningCadenceInStepsPerMinute") or summary.get("averageCadence") or data.get("averageRunningCadenceInStepsPerMinute"),
            max_cadence=summary.get("maxRunCadence") or summary.get("maxBikeCadence") or summary.get("maxRunningCadenceInStepsPerMinute") or summary.get("maxCadence") or data.get("maxRunningCadenceInStepsPerMinute"),
            avg_power_w=summary.get("avgPower") or summary.get("averagePower") or data.get("avgPower"),
            max_power_w=summary.get("maxPower") or data.get("maxPower"),
            elevation_gain_m=summary.get("elevationGain") or data.get("elevationGain"),
            elevation_loss_m=summary.get("elevationLoss") or data.get("elevationLoss"),
            aerobic_training_effect=summary.get("aerobicTrainingEffect") or summary.get("trainingEffect") or data.get("aerobicTrainingEffect"),
            anaerobic_training_effect=summary.get("anaerobicTrainingEffect") or data.get("anaerobicTrainingEffect"),
            calories=summary.get("calories") or data.get("calories"),
            splits=splits,
            raw_data=data,
        )
