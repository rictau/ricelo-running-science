"""Historical performance analytics, volume aggregation, and fitness/fatigue modeling."""

from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Optional

import pandas as pd

from .models import Activity, format_seconds, speed_to_pace_str


@dataclass
class VolumePeriod:
    """Summary of training volume for a single period (week or month)."""
    period_label: str
    start_date: str
    end_date: str
    total_activities: int
    total_distance_km: float
    total_duration_hours: float
    total_elevation_m: float
    total_calories: float
    by_sport: dict[str, dict[str, float]]  # sport -> {distance_km, duration_hours, count}


def aggregate_volume(
    activities: list[Activity],
    freq: str = "W",  # "W" for weekly, "M" for monthly
) -> list[VolumePeriod]:
    """
    Aggregate activity volume by week (Monday-Sunday) or month.
    freq: 'W' (ISO week) or 'M' (calendar month)
    """
    if not activities:
        return []

    # Sort activities by start time
    sorted_acts = sorted(
        activities,
        key=lambda a: a.start_time_local if a.start_time_local else "1970-01-01",
    )

    periods_dict: dict[str, list[Activity]] = defaultdict(list)

    for act in sorted_acts:
        if not act.start_time_local:
            continue
        # Extract date portion
        dt_str = act.start_time_local[:10]
        try:
            dt = datetime.strptime(dt_str, "%Y-%m-%d").date()
        except ValueError:
            continue

        if freq.upper() == "W":
            # Monday of the week
            start_of_week = dt - timedelta(days=dt.weekday())
            key = f"Week {start_of_week.strftime('%Y-%m-%d')}"
        else:
            key = dt.strftime("%Y-%m")

        periods_dict[key].append(act)

    results: list[VolumePeriod] = []
    for key, acts in periods_dict.items():
        total_dist_km = sum(a.distance_km for a in acts)
        total_dur_h = sum(a.duration_s for a in acts) / 3600.0
        total_elev_m = sum(a.elevation_gain_m or 0.0 for a in acts)
        total_cal = sum(a.calories or 0.0 for a in acts)

        by_sport: dict[str, dict[str, float]] = defaultdict(lambda: {"distance_km": 0.0, "duration_hours": 0.0, "count": 0})
        for a in acts:
            sport = a.activity_type.lower()
            by_sport[sport]["distance_km"] = round(by_sport[sport]["distance_km"] + a.distance_km, 2)
            by_sport[sport]["duration_hours"] = round(by_sport[sport]["duration_hours"] + (a.duration_s / 3600.0), 2)
            by_sport[sport]["count"] += 1

        dates = [a.start_time_local[:10] for a in acts if a.start_time_local]
        start_d = min(dates) if dates else ""
        end_d = max(dates) if dates else ""

        results.append(
            VolumePeriod(
                period_label=key,
                start_date=start_d,
                end_date=end_d,
                total_activities=len(acts),
                total_distance_km=round(total_dist_km, 2),
                total_duration_hours=round(total_dur_h, 2),
                total_elevation_m=round(total_elev_m, 1),
                total_calories=round(total_cal, 0),
                by_sport=dict(by_sport),
            )
        )

    return results


def calculate_activity_load(activity: Activity) -> float:
    """
    Estimate training load for an activity.
    Uses Garmin's Aerobic & Anaerobic Training Effect (EPOC-based) when present,
    or falls back to duration & HR intensity (TRIMP approximation).
    """
    # If training effect is available (Garmin TE ranges from 0.0 to 5.0)
    # Garmin training load roughly scales quadratically with TE and duration
    dur_min = activity.duration_s / 60.0
    if activity.aerobic_training_effect and activity.aerobic_training_effect > 0:
        te = activity.aerobic_training_effect
        ate = activity.anaerobic_training_effect or 0.0
        # Formula calibrated against Garmin EPOC / Training Load units
        load = (math.pow(te, 2.2) * 8.0) + (math.pow(ate, 1.8) * 12.0) + (dur_min * 0.4)
        return round(load, 1)

    # Fallback TRIMP-like load based on duration and HR or pace
    if activity.avg_hr and activity.avg_hr > 60:
        # HR reserve estimation assuming resting HR 50 and max HR 190
        hr_ratio = max(0.0, (activity.avg_hr - 50.0) / 140.0)
        return round(dur_min * hr_ratio * 1.5, 1)

    # Simple duration load fallback (1 hour easy = 50 load)
    return round(dur_min * 0.8, 1)


@dataclass
class DailyFitnessRecord:
    """Daily Fitness (CTL), Fatigue (ATL), and Form (TSB) metrics."""
    date_str: str
    daily_load: float
    ctl_fitness: float  # Chronic Training Load (42-day rolling)
    atl_fatigue: float  # Acute Training Load (7-day rolling)
    tsb_form: float     # Form = CTL - ATL
    status: str         # 'Fresh', 'Optimal', 'High Fatigue', 'Overtraining Risk'


def calculate_fitness_fatigue(
    activities: list[Activity],
    days: int = 90,
    end_date: date | None = None,
) -> list[DailyFitnessRecord]:
    """
    Calculate Chronic Training Load (CTL / Fitness), Acute Training Load (ATL / Fatigue),
    and Training Stress Balance (TSB / Form) across a daily timeline.
    Standard endurance model (Banister impulse-response, Coggan PMC form):
    - ATL time constant = 7 days, CTL time constant = 42 days.
    - Each is an exponentially weighted moving average with
      alpha = 1 - exp(-1 / tau), so a day's load decays with time constant
      tau: ATL halves about every 4.9 days, CTL about every 29 days.

    (Before 2026-10-27 this used the generic N-day EMA smoothing factor
    alpha = 2 / (N + 1), which decays roughly twice as fast as the
    Banister time constants and overstated how quickly fitness is lost
    during a taper or a rest block.)
    """
    if not end_date:
        end_date = date.today()

    # Warm up the model for 3 CTL time constants (126 days) before the
    # returned window, so the zero initial condition has decayed to
    # ~5% (exp(-3)) by the time the window starts. Callers should fetch
    # at least this much activity history.
    start_date = end_date - timedelta(days=days + 126)

    # Map daily load
    daily_loads: dict[str, float] = defaultdict(float)
    for act in activities:
        if not act.start_time_local:
            continue
        dt_str = act.start_time_local[:10]
        load = calculate_activity_load(act)
        daily_loads[dt_str] += load

    atl = 0.0
    ctl = 0.0
    atl_decay = 1.0 - math.exp(-1.0 / 7.0)
    ctl_decay = 1.0 - math.exp(-1.0 / 42.0)

    cur_dt = start_date
    all_records: list[DailyFitnessRecord] = []

    while cur_dt <= end_date:
        ds = cur_dt.strftime("%Y-%m-%d")
        load = daily_loads.get(ds, 0.0)

        # Exponential moving average
        atl = (load * atl_decay) + (atl * (1.0 - atl_decay))
        ctl = (load * ctl_decay) + (ctl * (1.0 - ctl_decay))

        # Round CTL/ATL first and derive TSB from the rounded values, so
        # every record is internally consistent: a consumer recomputing
        # CTL - ATL from the record's own fields always gets its TSB.
        # (Deriving TSB from the unrounded values could disagree with the
        # record's own CTL - ATL by 0.1 after rounding.)
        ctl_r = round(ctl, 1)
        atl_r = round(atl, 1)
        tsb = round(ctl_r - atl_r, 1)

        # Determine status
        if tsb > 15:
            status = "Very Fresh"
        elif tsb >= 5:
            status = "Fresh / Race Ready"
        elif tsb >= -10:
            status = "Optimal Base"
        elif tsb >= -30:
            status = "Productive Load"
        else:
            status = "High Volume Accumulation"

        record = DailyFitnessRecord(
            date_str=ds,
            daily_load=round(load, 1),
            ctl_fitness=ctl_r,
            atl_fatigue=atl_r,
            tsb_form=tsb,
            status=status,
        )
        all_records.append(record)
        cur_dt += timedelta(days=1)

    # Return only the requested window (last `days`)
    return all_records[-days:]


@dataclass
class BestEffort:
    category: str
    activity_id: int
    activity_name: str
    date_str: str
    value_display: str
    raw_value: float


def find_best_efforts(activities: list[Activity]) -> list[BestEffort]:
    """Find athlete's best historical performances across distance and speed benchmarks."""
    best: dict[str, BestEffort] = {}

    for a in activities:
        if not a.start_time_local or a.distance_km <= 0:
            continue
        d_km = a.distance_km
        dt_str = a.start_time_local[:10]
        is_run = "run" in a.activity_type.lower()

        # Marathon (40.0 - 45.0 km)
        if is_run and 40.0 <= d_km <= 45.0 and a.avg_speed_mps > 0:
            if "Marathon (42.2K)" not in best or a.avg_speed_mps > best["Marathon (42.2K)"].raw_value:
                best["Marathon (42.2K)"] = BestEffort(
                    category="Marathon (42.2K)",
                    activity_id=a.activity_id,
                    activity_name=a.name,
                    date_str=dt_str,
                    value_display=f"{a.pace_min_km} ({a.duration_str})",
                    raw_value=a.avg_speed_mps,
                )

        # Longest Run
        if is_run:
            if "Longest Run" not in best or d_km > best["Longest Run"].raw_value:
                best["Longest Run"] = BestEffort(
                    category="Longest Run",
                    activity_id=a.activity_id,
                    activity_name=a.name,
                    date_str=dt_str,
                    value_display=f"{d_km:.2f} km ({a.duration_str})",
                    raw_value=d_km,
                )

        # Fastest Half Marathon (20.5 - 22.5 km)
        if is_run and 20.5 <= d_km <= 22.5 and a.avg_speed_mps > 0:
            if "Fastest Half Marathon" not in best or a.avg_speed_mps > best["Fastest Half Marathon"].raw_value:
                best["Fastest Half Marathon"] = BestEffort(
                    category="Fastest Half Marathon",
                    activity_id=a.activity_id,
                    activity_name=a.name,
                    date_str=dt_str,
                    value_display=f"{a.pace_min_km} ({a.duration_str})",
                    raw_value=a.avg_speed_mps,
                )

        # Fastest 10K (9.5 - 10.5 km)
        if is_run and 9.5 <= d_km <= 10.5 and a.avg_speed_mps > 0:
            if "Fastest 10K" not in best or a.avg_speed_mps > best["Fastest 10K"].raw_value:
                best["Fastest 10K"] = BestEffort(
                    category="Fastest 10K",
                    activity_id=a.activity_id,
                    activity_name=a.name,
                    date_str=dt_str,
                    value_display=f"{a.pace_min_km} ({a.duration_str})",
                    raw_value=a.avg_speed_mps,
                )

        # Fastest 5K (4.8 - 5.5 km)
        if is_run and 4.8 <= d_km <= 5.5 and a.avg_speed_mps > 0:
            if "Fastest 5K" not in best or a.avg_speed_mps > best["Fastest 5K"].raw_value:
                best["Fastest 5K"] = BestEffort(
                    category="Fastest 5K",
                    activity_id=a.activity_id,
                    activity_name=a.name,
                    date_str=dt_str,
                    value_display=f"{a.pace_min_km} ({a.duration_str})",
                    raw_value=a.avg_speed_mps,
                )

        # Elevation gain (Running vert)
        if is_run and a.elevation_gain_m and ("Max Elevation Gain" not in best or a.elevation_gain_m > best["Max Elevation Gain"].raw_value):
            best["Max Elevation Gain"] = BestEffort(
                category="Max Elevation Gain",
                activity_id=a.activity_id,
                activity_name=a.name,
                date_str=dt_str,
                value_display=f"{a.elevation_gain_m:.0f} m",
                raw_value=a.elevation_gain_m,
            )

    return list(best.values())


@dataclass
class ComparisonMetric:
    metric_name: str
    value_a: str
    value_b: str
    delta_str: str
    verdict: str  # e.g. "Activity A was 12s/km faster"


def compare_activities(act_a: Activity, act_b: Activity) -> list[ComparisonMetric]:
    """Provide a comprehensive side-by-side comparison of two activities."""
    metrics: list[ComparisonMetric] = []

    # Distance
    dist_diff = round(act_b.distance_km - act_a.distance_km, 2)
    metrics.append(
        ComparisonMetric(
            metric_name="Distance",
            value_a=f"{act_a.distance_km} km",
            value_b=f"{act_b.distance_km} km",
            delta_str=f"{dist_diff:+.2f} km",
            verdict="Equal" if abs(dist_diff) < 0.05 else ("B longer" if dist_diff > 0 else "A longer"),
        )
    )

    # Duration
    dur_diff = act_b.duration_s - act_a.duration_s
    metrics.append(
        ComparisonMetric(
            metric_name="Moving Duration",
            value_a=act_a.duration_str,
            value_b=act_b.duration_str,
            delta_str=format_seconds(abs(dur_diff)),
            verdict="Equal" if abs(dur_diff) < 1 else ("B was longer" if dur_diff > 0 else "A was longer"),
        )
    )

    # Pace / Speed
    if act_a.avg_speed_mps > 0 and act_b.avg_speed_mps > 0:
        pace_a_sec = 1000.0 / act_a.avg_speed_mps
        pace_b_sec = 1000.0 / act_b.avg_speed_mps
        pace_diff_sec = round(pace_b_sec - pace_a_sec, 1)
        metrics.append(
            ComparisonMetric(
                metric_name="Average Pace",
                value_a=act_a.pace_min_km,
                value_b=act_b.pace_min_km,
                delta_str=f"{pace_diff_sec:+.1f} s/km",
                verdict="Equal" if pace_diff_sec == 0 else ("A was faster" if pace_diff_sec > 0 else "B was faster"),
            )
        )

    # Average Heart Rate
    if act_a.avg_hr and act_b.avg_hr:
        hr_diff = round(act_b.avg_hr - act_a.avg_hr, 1)
        metrics.append(
            ComparisonMetric(
                metric_name="Average Heart Rate",
                value_a=f"{act_a.avg_hr:.0f} bpm",
                value_b=f"{act_b.avg_hr:.0f} bpm",
                delta_str=f"{hr_diff:+.1f} bpm",
                verdict="Equal" if hr_diff == 0 else ("A had lower HR" if hr_diff > 0 else "B had lower HR"),
            )
        )

    # Aerobic Efficiency Factor (Speed m/s / HR)
    if act_a.avg_hr and act_b.avg_hr and act_a.avg_speed_mps > 0 and act_b.avg_speed_mps > 0:
        eff_a = (act_a.avg_speed_mps / act_a.avg_hr) * 1000.0
        eff_b = (act_b.avg_speed_mps / act_b.avg_hr) * 1000.0
        eff_diff = round(eff_b - eff_a, 2)
        metrics.append(
            ComparisonMetric(
                metric_name="Efficiency Factor (Speed/HR)",
                value_a=f"{eff_a:.2f}",
                value_b=f"{eff_b:.2f}",
                delta_str=f"{eff_diff:+.2f}",
                verdict="Equal" if eff_diff == 0 else ("B was more aerobic-efficient" if eff_diff > 0 else "A was more aerobic-efficient"),
            )
        )

    # Elevation Gain
    elev_a = act_a.elevation_gain_m or 0.0
    elev_b = act_b.elevation_gain_m or 0.0
    elev_diff = round(elev_b - elev_a, 1)
    metrics.append(
        ComparisonMetric(
            metric_name="Elevation Gain",
            value_a=f"{elev_a:.0f} m",
            value_b=f"{elev_b:.0f} m",
            delta_str=f"{elev_diff:+.0f} m",
            verdict="Equal" if elev_diff == 0 else ("B had more elevation" if elev_diff > 0 else "A had more elevation"),
        )
    )

    # Aerobic Decoupling (Drift)
    dec_a = act_a.aerobic_decoupling_pct
    dec_b = act_b.aerobic_decoupling_pct
    if dec_a is not None and dec_b is not None:
        dec_diff = round(dec_b - dec_a, 2)
        metrics.append(
            ComparisonMetric(
                metric_name="Cardiac Drift (Decoupling)",
                value_a=f"{dec_a:.1f}%",
                value_b=f"{dec_b:.1f}%",
                delta_str=f"{dec_diff:+.1f}%",
                verdict="Equal" if dec_diff == 0 else ("A had better cardiac stability" if dec_diff > 0 else "B had better cardiac stability"),
            )
        )

    return metrics


# ---------------------------------------------------------------------------
# Acute-to-Chronic Workload Ratio (ACWR) - Gabbett Model (2016)
# ---------------------------------------------------------------------------

@dataclass
class ACWRRecord:
    """Acute-to-Chronic Workload Ratio for soft-tissue injury risk monitoring."""
    acute_load: float          # 7-day cumulative load
    chronic_load: float        # 28-day rolling weekly average load
    acwr: float                # acute / chronic
    status: str                # "Sweet Spot", "Underload", "Caution", "Danger Zone"
    status_class: str          # "badge-optimal", "badge-fresh", "badge-fatigued", "badge-risk"
    recommendation: str


def calculate_acwr(timeline: list[DailyFitnessRecord]) -> ACWRRecord:
    """
    Calculate Acute-to-Chronic Workload Ratio (Gabbett, BJSM 2016).
    Acute: Sum of training load over the last 7 days.
    Chronic: 28-day rolling average of weekly training load.
    """
    if not timeline or len(timeline) < 7:
        return ACWRRecord(
            acute_load=0.0,
            chronic_load=0.0,
            acwr=1.0,
            status="Insufficient History (<7 days)",
            status_class="badge-fresh",
            recommendation="Record at least 7 days of training load to establish a workload ratio.",
        )

    loads = [r.daily_load for r in timeline]
    acute = sum(loads[-7:])
    chronic_slice = loads[-28:] if len(loads) >= 28 else loads
    chronic_weeks = len(chronic_slice) / 7.0
    chronic = sum(chronic_slice) / chronic_weeks if chronic_weeks > 0 else acute

    ratio = round(acute / chronic, 2) if chronic > 0 else 1.0

    if ratio < 0.80:
        status = "Underload"
        status_class = "badge-fresh"
        rec = "Workload has declined. Safe to gradually progress mileage without injury risk."
    elif ratio <= 1.30:
        status = "Sweet Spot"
        status_class = "badge-optimal"
        rec = "Optimal training progression (lowest relative injury risk per Gabbett 2016)."
    elif ratio <= 1.50:
        status = "Caution"
        status_class = "badge-fatigued"
        rec = "Accelerating training load. Prioritize recovery and sleep, and avoid sudden volume jumps."
    else:
        status = "Danger Zone"
        status_class = "badge-risk"
        rec = "High soft-tissue injury risk (>1.5x spike). Consider recovery day or cross-training."

    return ACWRRecord(
        acute_load=round(acute, 1),
        chronic_load=round(chronic, 1),
        acwr=ratio,
        status=status,
        status_class=status_class,
        recommendation=rec,
    )


# ---------------------------------------------------------------------------
# Jack Daniels VDOT & Pacing Formulas (Daniels & Gilbert 1979)
# ---------------------------------------------------------------------------

def calculate_vdot(distance_m: float, duration_s: float) -> float:
    """
    Calculate Jack Daniels VDOT from race/benchmark performance using Daniels & Gilbert (1979).
    Requires a maximal or near-maximal effort.
    """
    if distance_m <= 0 or duration_s <= 0:
        return 0.0
    t_min = duration_s / 60.0
    v_mpm = distance_m / t_min
    vo2 = -4.60 + 0.182258 * v_mpm + 0.000104 * (v_mpm ** 2)
    pct_max = 0.8 + 0.1894393 * math.exp(-0.012778 * t_min) + 0.2989558 * math.exp(-0.1932605 * t_min)
    if pct_max <= 0:
        return 0.0
    return round(vo2 / pct_max, 2)


def solve_vdot_velocity(target_vo2: float) -> float:
    """Solve quadratic equation for velocity (m/min) given target VO2."""
    a = 0.000104
    b = 0.182258
    c = -(target_vo2 + 4.60)
    discriminant = b**2 - 4 * a * c
    if discriminant < 0:
        return 0.0
    return (-b + math.sqrt(discriminant)) / (2 * a)


def get_vdot_training_paces(vdot: float) -> dict[str, str]:
    """Calculate Jack Daniels prescribed training paces from VDOT."""
    if vdot <= 10:
        return {}

    def pace_str(v_mpm: float) -> str:
        if v_mpm <= 0:
            return "N/A"
        p = 1000.0 / v_mpm
        m = int(p)
        s = int(round((p - m) * 60))
        if s == 60:
            m += 1
            s = 0
        return f"{m:02d}:{s:02d} /km"

    easy_low = pace_str(solve_vdot_velocity(vdot * 0.62))
    easy_high = pace_str(solve_vdot_velocity(vdot * 0.70))
    marathon = pace_str(solve_vdot_velocity(vdot * 0.80))
    threshold = pace_str(solve_vdot_velocity(vdot * 0.88))
    interval = pace_str(solve_vdot_velocity(vdot * 0.975))
    repetition = pace_str(solve_vdot_velocity(vdot * 1.05))

    return {
        "easy_pace": f"{easy_high} - {easy_low}",
        "marathon_pace": marathon,
        "threshold_pace": threshold,
        "interval_pace": interval,
        "repetition_pace": repetition,
    }


def get_vdot_race_predictions(vdot: float) -> dict[str, str]:
    """Predict equivalent race times for standard distances using bisection search."""
    if vdot <= 10:
        return {}

    distances = {
        "5k": 5000.0,
        "10k": 10000.0,
        "half_marathon": 21097.5,
        "marathon": 42195.0,
    }

    results = {}
    for name, dist in distances.items():
        low, high = 2.0, 720.0
        for _ in range(50):
            mid = (low + high) / 2.0
            v = dist / mid
            vo2_req = -4.60 + 0.182258 * v + 0.000104 * (v ** 2)
            pct = 0.8 + 0.1894393 * math.exp(-0.012778 * mid) + 0.2989558 * math.exp(-0.1932605 * mid)
            if (vo2_req / pct) > vdot:
                low = mid
            else:
                high = mid
        tot_secs = mid * 60.0
        h = int(tot_secs // 3600)
        m = int((tot_secs % 3600) // 60)
        s = int(round(tot_secs % 60))
        if h > 0:
            results[name] = f"{h:02d}:{m:02d}:{s:02d}"
        else:
            results[name] = f"{m:02d}:{s:02d}"

    return results


def find_best_empirical_vdot(activities: list[Activity]) -> Optional[dict[str, Any]]:
    """
    Scan historical activities to find the athlete's peak demonstrated VDOT
    strictly from genuine race/benchmark efforts (5K, 10K, Half, Marathon).
    Does NOT use sub-maximal jogs or arbitrary fudge factors.
    """
    best_vdot = 0.0
    best_act = None
    best_dist_type = ""

    for a in activities:
        if not a.start_time_local or a.distance_km <= 0 or a.duration_s <= 0:
            continue
        if "run" not in a.activity_type.lower():
            continue

        d_m = a.distance_m
        t_s = a.duration_s

        dist_label = None
        if 4800 <= d_m <= 5500:
            dist_label = "5K"
        elif 9500 <= d_m <= 10500:
            dist_label = "10K"
        elif 20500 <= d_m <= 22500:
            dist_label = "Half Marathon"
        elif 40000 <= d_m <= 45000:
            dist_label = "Marathon"

        if dist_label:
            v = calculate_vdot(d_m, t_s)
            if v > best_vdot:
                best_vdot = v
                best_act = a
                best_dist_type = dist_label

    if best_vdot <= 0 or best_act is None:
        return None

    return {
        "vdot": best_vdot,
        "benchmark_activity_id": best_act.activity_id,
        "benchmark_activity_name": best_act.name,
        "benchmark_date": best_act.start_time_local[:10],
        "benchmark_distance": best_dist_type,
        "benchmark_time": best_act.duration_str,
        "benchmark_pace": best_act.pace_min_km,
        "training_paces": get_vdot_training_paces(best_vdot),
        "race_predictions": get_vdot_race_predictions(best_vdot),
    }
