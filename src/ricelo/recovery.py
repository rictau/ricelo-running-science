"""Garmin biometric recovery, sleep, HRV, and training readiness analysis."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Any


@dataclass
class DailyRecoveryStatus:
    """Consolidated biometric recovery metrics for a single day."""
    date_str: str
    sleep_score: int | None
    sleep_duration_hours: float | None
    deep_sleep_hours: float | None
    rem_sleep_hours: float | None
    hrv_last_night_avg_ms: float | None
    hrv_weekly_avg_ms: float | None
    hrv_status: str | None
    resting_hr_bpm: int | None
    body_battery_max: int | None
    body_battery_charged: int | None
    avg_stress_score: int | None
    readiness_verdict: str
    training_recommendation: str


def latest_body_battery_record(records: list[dict[str, Any]] | None) -> dict[str, Any] | None:
    """Pick the most recent Body Battery record that actually has data.

    The Personal API returns one record per day, and the current day's
    record is present but empty until readings accumulate, so taking the
    last record unconditionally yields an all-null readiness assessment
    every morning.
    """
    if not records:
        return None
    for record in reversed(records):
        values = [
            row[1]
            for row in (record.get("bodyBatteryValuesArray") or [])
            if isinstance(row, (list, tuple)) and len(row) > 1 and isinstance(row[1], (int, float))
        ]
        # The current day's record can carry an array whose values are all
        # null, so an array alone is not evidence of data.
        if values or record.get("charged") is not None:
            return record
    return records[-1]


def evaluate_daily_readiness(
    sleep_data: dict[str, Any] | None = None,
    hrv_data: dict[str, Any] | None = None,
    body_battery_data: dict[str, Any] | None = None,
    daily_summary: dict[str, Any] | None = None,
    today: date | None = None,
) -> DailyRecoveryStatus:
    """
    Synthesize sleep, HRV, body battery, and stress into a holistic recovery status.

    A verdict needs last night's sleep score or HRV status. Body Battery
    alone is shown but does not produce a verdict: it is a derived figure
    that reflects the whole day, not overnight recovery.
    """
    sleep_score: int | None = None
    sleep_dur_h: float | None = None
    deep_sleep_h: float | None = None
    rem_sleep_h: float | None = None

    if sleep_data:
        # Sleep score
        sleep_score = sleep_data.get("sleepScores", {}).get("overall", {}).get("value")
        if sleep_score is None:
            sleep_score = sleep_data.get("overallScore")

        # Sleep durations
        sleep_seconds = sleep_data.get("sleepTimeSeconds") or sleep_data.get("durationInSeconds")
        if sleep_seconds:
            sleep_dur_h = round(float(sleep_seconds) / 3600.0, 1)

        deep_s = sleep_data.get("deepSleepSeconds")
        if deep_s:
            deep_sleep_h = round(float(deep_s) / 3600.0, 1)

        rem_s = sleep_data.get("remSleepSeconds")
        if rem_s:
            rem_sleep_h = round(float(rem_s) / 3600.0, 1)

    # HRV metrics
    hrv_last_night: float | None = None
    hrv_weekly: float | None = None
    hrv_status: str | None = None

    if hrv_data:
        hrv_summary = hrv_data.get("hrvSummary") or hrv_data
        hrv_last_night = hrv_summary.get("lastNightAvg")
        hrv_weekly = hrv_summary.get("weeklyAvg")
        hrv_status = hrv_summary.get("status")

    # Body Battery
    bb_max: int | None = None
    bb_charged: int | None = None

    if body_battery_data:
        if body_battery_data.get("charged") is not None:
            bb_charged = body_battery_data.get("charged")
        values = [
            row[1]
            for row in (body_battery_data.get("bodyBatteryValuesArray") or [])
            if isinstance(row, (list, tuple)) and len(row) > 1 and isinstance(row[1], (int, float))
        ]
        if values:
            bb_max = int(max(values))
        else:
            bb_max = body_battery_data.get("bodyBatteryHighestValue") or bb_charged

    # Daily summary & Resting HR
    rhr: int | None = None
    avg_stress: int | None = None

    if daily_summary:
        hr_dict = daily_summary.get("heartRate") or {}
        rhr = hr_dict.get("restingValue") or daily_summary.get("restingHeartRate") or daily_summary.get("restingHR")

        stress_dict = daily_summary.get("stress") or {}
        avg_stress = stress_dict.get("avgLevel") or daily_summary.get("averageStressLevel") or daily_summary.get("avgStress")

        if bb_max is None:
            bb_dict = daily_summary.get("bodyBattery") or {}
            bb_max = bb_dict.get("maxValue")
            bb_charged = bb_dict.get("chargedValue")

    # Determine readiness score & verdict
    score_components = []
    low_signals: list[str] = []
    if sleep_score is not None:
        score_components.append(sleep_score)
        if sleep_score < 60:
            low_signals.append(f"sleep score {sleep_score}")
    if hrv_status:
        # Check "unbalanced" before "balanced": the substring test for
        # "balanced" also matches "UNBALANCED".
        st = hrv_status.lower()
        if "unbalanced" in st:
            score_components.append(50)
            low_signals.append(f"HRV {hrv_status}")
        elif "balanced" in st:
            score_components.append(85)
        elif "low" in st or "poor" in st:
            score_components.append(30)
            low_signals.append(f"HRV {hrv_status}")
    has_overnight_data = bool(score_components)

    # Score Body Battery on its peak value, not on the day's charge total.
    # The Personal API reports a completed day's charge as 0 even when the
    # battery peaked normally, which would read as total exhaustion.
    bb_score = bb_max if bb_max is not None else bb_charged
    if bb_score is not None:
        score_components.append(min(100, bb_score))
        if bb_score < 50:
            low_signals.append(f"Body Battery peak {bb_score}")

    today_str = (
        (sleep_data and sleep_data.get("calendarDate"))
        or (daily_summary and daily_summary.get("calendarDate"))
        or (today or date.today()).isoformat()
    )

    composite_score = sum(score_components) / len(score_components) if score_components else None

    if not has_overnight_data:
        verdict = "Insufficient Data / No Verdict"
        rec = "No sleep or HRV data for last night, so readiness cannot be assessed."
        if bb_score is not None:
            rec += f" Body Battery peaked at {bb_score}."
    elif composite_score >= 80:
        verdict = "High Readiness / Prime for Peak Performance"
        rec = "Body is well-recovered. Excellent day for high-intensity intervals, tempo runs, or heavy threshold training."
    elif composite_score >= 60:
        verdict = "Moderate Readiness / Steady State"
        rec = "Recovery is adequate. Ideal for aerobic base miles (Zone 2), steady aerobic maintenance, or moderate effort."
    else:
        verdict = "Low Readiness / Recovery Priority"
        reasons = ", ".join(low_signals) or "low combined recovery score"
        rec = f"Elevated physiological strain detected ({reasons}). Prioritize rest, active recovery walk, gentle yoga, or early bedtime."

    return DailyRecoveryStatus(
        date_str=today_str,
        sleep_score=sleep_score,
        sleep_duration_hours=sleep_dur_h,
        deep_sleep_hours=deep_sleep_h,
        rem_sleep_hours=rem_sleep_h,
        hrv_last_night_avg_ms=hrv_last_night,
        hrv_weekly_avg_ms=hrv_weekly,
        hrv_status=hrv_status,
        resting_hr_bpm=rhr,
        body_battery_max=bb_max,
        body_battery_charged=bb_charged,
        avg_stress_score=avg_stress,
        readiness_verdict=verdict,
        training_recommendation=rec,
    )
