"""Terminal formatting and HTML report generation for Garmin Analytics."""

from __future__ import annotations

import csv
import html
import json
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from .models import Activity, format_seconds, speed_to_pace_str
from .performance import (
    BestEffort,
    ComparisonMetric,
    DailyFitnessRecord,
    VolumePeriod,
    calculate_acwr,
    find_best_efforts,
    find_best_empirical_vdot,
)
from .recovery import DailyRecoveryStatus

console = Console()


def _script_json(obj: Any) -> str:
    """Serialize obj for embedding in an inline <script> block.

    Escaping "<" stops a value containing "</script>" (such as an activity
    name) from ending the script element early.
    """
    return json.dumps(obj).replace("<", "\\u003c")


def print_activities_table(activities: list[Activity], title: str = "Garmin Activities") -> None:
    """Print a clean table of activities."""
    table = Table(title=title, show_lines=False, header_style="bold cyan")
    table.add_column("ID", style="dim", justify="right", no_wrap=True)
    table.add_column("Date", style="bold", no_wrap=True)
    table.add_column("Activity Name", style="white")
    table.add_column("Type", style="green", no_wrap=True)
    table.add_column("Distance", justify="right", style="yellow", no_wrap=True)
    table.add_column("Duration", justify="right", no_wrap=True)
    table.add_column("Pace / Speed", justify="right", style="magenta", no_wrap=True)
    table.add_column("Avg HR", justify="right", style="red", no_wrap=True)
    table.add_column("TE", justify="right", style="blue", no_wrap=True)

    for act in activities:
        dt_str = act.start_time_local[:16].replace("T", " ") if act.start_time_local else "N/A"
        speed_disp = act.pace_min_km if "run" in act.activity_type.lower() else f"{act.speed_kmh} km/h"
        hr_disp = f"{act.avg_hr:.0f} bpm" if act.avg_hr else "N/A"
        te_disp = f"{act.aerobic_training_effect:.1f}" if act.aerobic_training_effect else "N/A"

        table.add_row(
            str(act.activity_id),
            dt_str,
            act.name,
            act.activity_type,
            f"{act.distance_km:.2f} km",
            act.duration_str,
            speed_disp,
            hr_disp,
            te_disp,
        )

    console.print(table)


def print_activity_detail(act: Activity) -> None:
    """Print full detail card and splits for an activity."""
    summary_text = (
        f"[bold cyan]{act.name}[/bold cyan] ({act.activity_type})\n"
        f"Date: [bold]{act.start_time_local}[/bold] | ID: {act.activity_id}\n\n"
        f"Distance: [bold yellow]{act.distance_km:.2f} km[/bold yellow] ({act.distance_miles:.2f} mi)\n"
        f"Duration: [bold]{act.duration_str}[/bold] | Moving: {format_seconds(act.moving_duration_s) if act.moving_duration_s else 'N/A'} | Elapsed: {format_seconds(act.elapsed_duration_s)}\n"
        f"Average Pace: [bold magenta]{act.pace_min_km}[/bold magenta] ({act.speed_kmh} km/h)\n"
        f"Heart Rate: [bold red]Avg {act.avg_hr or 'N/A'} bpm[/bold red] | Max {act.max_hr or 'N/A'} bpm\n"
        f"Elevation: [bold green]+{act.elevation_gain_m or 0:.0f} m climb, {act.elevation_loss_m or 0:.0f} m loss[/bold green]\n"
        f"Cadence: {act.avg_cadence or 'N/A'} spm | Max: {act.max_cadence or 'N/A'} spm\n"
        f"Calories: {act.calories or 'N/A'} kcal\n"
        f"Training Effect: Aerobic [bold blue]{act.aerobic_training_effect or 'N/A'}[/bold blue] | Anaerobic [bold]{act.anaerobic_training_effect or 'N/A'}[/bold]"
    )

    if act.aerobic_decoupling_pct is not None:
        dec = act.aerobic_decoupling_pct
        dec_color = "green" if dec < 5.0 else ("yellow" if dec < 8.0 else "red")
        summary_text += f"\nAerobic Decoupling (Cardiac Drift): [{dec_color}]{dec:.1f}%[/{dec_color}]"

    console.print(Panel(summary_text, title="Activity Summary", border_style="cyan"))

    if act.splits:
        splits_table = Table(title="Laps / Splits Breakdown", header_style="bold magenta")
        splits_table.add_column("Split", justify="right")
        splits_table.add_column("Distance", justify="right")
        splits_table.add_column("Time", justify="right")
        splits_table.add_column("Pace", justify="right")
        splits_table.add_column("Avg HR", justify="right")
        splits_table.add_column("Cadence", justify="right")
        splits_table.add_column("Elevation", justify="right")

        for s in act.splits:
            splits_table.add_row(
                str(s.split_index),
                f"{s.distance_km:.2f} km",
                s.duration_str,
                s.pace_min_km,
                f"{s.avg_hr:.0f} bpm" if s.avg_hr else "N/A",
                f"{s.avg_cadence:.0f}" if s.avg_cadence else "N/A",
                f"+{s.elevation_gain_m or 0:.0f}m" if s.elevation_gain_m is not None else "N/A",
            )
        console.print(splits_table)


def print_volume_summary(periods: list[VolumePeriod], title: str = "Training Volume") -> None:
    """Print volume summary table."""
    table = Table(title=title, header_style="bold green")
    table.add_column("Period", style="bold")
    table.add_column("Dates", style="dim")
    table.add_column("Count", justify="right")
    table.add_column("Distance", justify="right", style="yellow")
    table.add_column("Time", justify="right", style="cyan")
    table.add_column("Elev Gain", justify="right", style="green")
    table.add_column("Calories", justify="right")
    table.add_column("Sports Breakdown", style="white")

    for p in periods:
        sports_str = ", ".join(f"{s.capitalize()}: {data['distance_km']}km ({data['count']})" for s, data in p.by_sport.items())
        table.add_row(
            p.period_label,
            f"{p.start_date} to {p.end_date}",
            str(p.total_activities),
            f"{p.total_distance_km:.1f} km",
            f"{p.total_duration_hours:.1f} h",
            f"{p.total_elevation_m:.0f} m",
            f"{p.total_calories:.0f} kcal",
            sports_str,
        )

    console.print(table)


def print_fitness_timeline(records: list[DailyFitnessRecord], title: str = "Fitness and Fatigue Progression (CTL / ATL / TSB)") -> None:
    """Print daily fitness and fatigue table."""
    table = Table(title=title, header_style="bold yellow")
    table.add_column("Date", style="bold")
    table.add_column("Daily Load", justify="right", style="dim")
    table.add_column("Fitness (CTL)", justify="right", style="cyan")
    table.add_column("Fatigue (ATL)", justify="right", style="magenta")
    table.add_column("Form (TSB)", justify="right", style="bold")
    table.add_column("Training Status", style="white")

    for r in records:
        tsb_color = "green" if r.tsb_form > 0 else ("yellow" if r.tsb_form > -20 else "red")
        table.add_row(
            r.date_str,
            f"{r.daily_load:.1f}",
            f"{r.ctl_fitness:.1f}",
            f"{r.atl_fatigue:.1f}",
            f"[{tsb_color}]{r.tsb_form:+.1f}[/{tsb_color}]",
            r.status,
        )

    console.print(table)


def print_comparison(act_a: Activity, act_b: Activity, metrics: list[ComparisonMetric]) -> None:
    """Print side by side comparison table."""
    table = Table(title=f"Head to Head Comparison: Activity {act_a.activity_id} vs {act_b.activity_id}", header_style="bold cyan")
    table.add_column("Metric", style="bold")
    table.add_column(f"Activity A ({act_a.start_time_local[:10]})", justify="right", style="yellow")
    table.add_column(f"Activity B ({act_b.start_time_local[:10]})", justify="right", style="magenta")
    table.add_column("Delta (B vs A)", justify="right", style="bold")
    table.add_column("Verdict", style="green")

    for m in metrics:
        table.add_row(m.metric_name, m.value_a, m.value_b, m.delta_str, m.verdict)

    console.print(table)


def print_readiness(status: DailyRecoveryStatus) -> None:
    """Print recovery and training readiness panel."""
    text = (
        f"Date: [bold]{status.date_str}[/bold]\n\n"
        f"Sleep Score: [bold cyan]{status.sleep_score or 'N/A'}[/bold cyan] ({status.sleep_duration_hours or 'N/A'} hrs total | Deep: {status.deep_sleep_hours or 'N/A'}h, REM: {status.rem_sleep_hours or 'N/A'}h)\n"
        f"HRV Status: [bold magenta]{status.hrv_status or 'N/A'}[/bold magenta] (Overnight Avg: {status.hrv_last_night_avg_ms or 'N/A'} ms | Weekly: {status.hrv_weekly_avg_ms or 'N/A'} ms)\n"
        f"Body Battery: [bold yellow]{status.body_battery_max or 'N/A'}[/bold yellow] (Charged: +{status.body_battery_charged or 'N/A'})\n"
        f"Resting HR: [bold red]{status.resting_hr_bpm or 'N/A'} bpm[/bold red] | Daily Stress: {status.avg_stress_score or 'N/A'}\n\n"
        f"Status: [bold green]{status.readiness_verdict}[/bold green]\n"
        f"Recommendation: {status.training_recommendation}"
    )
    console.print(Panel(text, title="Garmin Recovery and Readiness", border_style="green"))


def generate_html_dashboard(
    activities: list[Activity],
    volume_periods: list[VolumePeriod],
    fitness_records: list[DailyFitnessRecord],
    output_path: Path | str = "./garmin_dashboard.html",
    best_efforts: list[BestEffort] | None = None,
    vdot_info: dict[str, Any] | None = None,
    vo2max_csv_path: Path | str | None = "./vo2max_2year_history.csv",
    athlete_name: str | None = None,
    max_hr: float | None = None,
    weeks_scope: int = 12,
    default_theme: str = "light",
) -> Path:
    """
    Generate an interactive, modern, high performance HTML performance dashboard.
    Uses Plus Jakarta Sans and JetBrains Mono typography, clean light theme default,
    theme switcher without emojis, no unnecessary decorative icons, and clean language
    without hyphens.
    """
    out_file = Path(output_path).expanduser()
    out_file.parent.mkdir(parents=True, exist_ok=True)

    # 1. Augment activities with local downloads if available (for full 1km splits)
    downloads_dir = Path("downloads")
    if downloads_dir.exists():
        try:
            from .fit import apply_fit_data, extract_and_parse_fit
            for act in activities:
                zip_p = downloads_dir / f"activity_{act.activity_id}.zip"
                if zip_p.exists() and (not act.splits or len(act.splits) < 5):
                    try:
                        apply_fit_data(act, extract_and_parse_fit(zip_p))
                    except Exception:
                        pass
        except Exception:
            pass

    # 2. Parse VO2 Max History
    vo2_history: list[dict[str, Any]] = []
    vo2_csv = Path(vo2max_csv_path) if vo2max_csv_path else Path("vo2max_2year_history.csv")
    if vo2_csv.exists():
        try:
            with open(vo2_csv, encoding="utf-8") as f:
                reader = csv.DictReader(f)
                for r in reader:
                    try:
                        vo2_history.append({
                            "date": r["date"],
                            "name": r.get("name", "Run"),
                            "distance_km": round(float(r.get("distance_km", 0.0)), 2),
                            "vo2_max": round(float(r["vo2_max"]), 2),
                            "vo2_display": int(float(r.get("vo2_display", r["vo2_max"]))),
                            "avg_hr": float(r["avg_hr"]) if r.get("avg_hr") and r["avg_hr"] != "None" else None,
                        })
                    except Exception:
                        continue
        except Exception:
            pass

    # 3. Best Efforts
    if best_efforts is None:
        best_efforts = find_best_efforts(activities)

    # 4. Compute Time Scopes and Date Ranges
    dates_act = [a.start_time_local[:10] for a in activities if a.start_time_local]
    act_start_date = min(dates_act) if dates_act else (date.today() - timedelta(weeks=weeks_scope)).isoformat()
    act_end_date = max(dates_act) if dates_act else date.today().isoformat()
    act_date_range = f"{act_start_date} to {act_end_date}"
    days_scope = weeks_scope * 7

    # Without a VO2 history file, show N/A rather than invented numbers.
    vo2_count = len(vo2_history)
    vo2_years_str = "N/A"
    vo2_date_range = "No VO2 max history file"
    if vo2_history:
        vo2_start = vo2_history[0]["date"]
        vo2_end = vo2_history[-1]["date"]
        vo2_date_range = f"{vo2_start} to {vo2_end}"
        try:
            vo2_days = (datetime.strptime(vo2_end, "%Y-%m-%d") - datetime.strptime(vo2_start, "%Y-%m-%d")).days
            vo2_years_str = f"{round(vo2_days / 365.25, 1)} Years ({vo2_days} Days)"
        except ValueError:
            pass

    # 5. Athlete KPIs
    latest_fitness = fitness_records[-1] if fitness_records else DailyFitnessRecord(
        date_str=date.today().isoformat(), daily_load=0, ctl_fitness=0.0, atl_fatigue=0.0, tsb_form=0.0, status="No data"
    )
    
    if vo2_history:
        current_vo2 = vo2_history[-1]["vo2_max"]
        vo2_current_disp = f"{current_vo2:.2f}"
        vo2_watch_disp = str(round(current_vo2))
        vo2_range_disp = (
            f"High: {max(r['vo2_max'] for r in vo2_history):.2f} / "
            f"Low: {min(r['vo2_max'] for r in vo2_history):.2f} / Latest: {vo2_current_disp}"
        )
    else:
        vo2_current_disp = vo2_watch_disp = vo2_range_disp = "N/A"
    vo2_delta_disp = (
        f"{vo2_history[-1]['vo2_max'] - vo2_history[-2]['vo2_max']:+.2f}" if len(vo2_history) >= 2 else "N/A"
    )

    # Heart rate zones as % of max HR; bpm only when max HR is known.
    def _zone_bpm(pct: float) -> str:
        return f"{round(max_hr * pct)}" if max_hr else "N/A"

    max_hr_disp = f"{max_hr:.0f} bpm" if max_hr else "N/A"
    z1_hr = f"&lt; {_zone_bpm(0.70)} bpm" if max_hr else "N/A"
    z2_hr = f"{_zone_bpm(0.70)} to {_zone_bpm(0.80)} bpm" if max_hr else "N/A"
    z3_hr = f"{_zone_bpm(0.80)} to {_zone_bpm(0.89)} bpm" if max_hr else "N/A"
    z4_hr = f"{_zone_bpm(0.89)} to {_zone_bpm(0.95)} bpm" if max_hr else "N/A"
    z5_hr = f"&gt; {_zone_bpm(0.95)} bpm" if max_hr else "N/A"

    athlete_disp = html.escape(athlete_name) if athlete_name else ""
    title_suffix = f" ({athlete_disp})" if athlete_disp else ""
    athlete_pill = f'<div class="pill">Athlete: <strong>{athlete_disp}</strong></div>' if athlete_disp else ""

    cutoff_30d = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    acts_30d = [a for a in activities if a.start_time_local and a.start_time_local[:10] >= cutoff_30d]
    dist_30d = sum(a.distance_km for a in acts_30d)
    time_30d_h = sum(a.duration_s for a in acts_30d) / 3600.0
    elev_30d_m = sum(a.elevation_gain_m or 0.0 for a in acts_30d)

    latest_run = next((a for a in activities if "run" in a.activity_type.lower()), None)
    latest_run_decoupling = latest_run.aerobic_decoupling_pct if latest_run else None
    if latest_run_decoupling is None:
        drift_value, drift_badge, drift_label = "N/A", "badge-fresh", "No split data"
    elif latest_run_decoupling < 5.0:
        drift_value, drift_badge, drift_label = f"{latest_run_decoupling:.2f}%", "badge-fresh", "Efficient"
    elif latest_run_decoupling <= 8.0:
        drift_value, drift_badge, drift_label = f"{latest_run_decoupling:.2f}%", "badge-fatigued", "Moderate drift"
    else:
        drift_value, drift_badge, drift_label = f"{latest_run_decoupling:.2f}%", "badge-risk", "High drift"
    drift_subtext = (
        html.escape(f"{latest_run.start_time_local[:10]} {latest_run.distance_km:.1f}km {latest_run.name}")
        if latest_run
        else "No recent run"
    )

    # 6. Prepare serialized data structures
    serialized_acts = []
    for a in activities:
        serialized_acts.append({
            "activity_id": a.activity_id,
            "name": a.name,
            "type": a.activity_type,
            "date": a.start_time_local[:10] if a.start_time_local else "",
            "full_date": a.start_time_local.replace("T", " ") if a.start_time_local else "",
            "distance_km": a.distance_km,
            "duration_s": a.duration_s,
            "duration_str": a.duration_str,
            "pace_min_km": a.pace_min_km,
            "speed_kmh": a.speed_kmh,
            "avg_hr": round(a.avg_hr) if a.avg_hr else None,
            "max_hr": round(a.max_hr) if a.max_hr else None,
            "avg_cadence": round(a.avg_cadence) if a.avg_cadence else None,
            "elevation_gain_m": round(a.elevation_gain_m) if a.elevation_gain_m else None,
            "aerobic_te": round(a.aerobic_training_effect, 1) if a.aerobic_training_effect else None,
            "anaerobic_te": round(a.anaerobic_training_effect, 1) if a.anaerobic_training_effect else None,
            "calories": round(a.calories) if a.calories else None,
            "decoupling_pct": a.aerobic_decoupling_pct,
            "splits": [
                {
                    "split": s.split_index,
                    "distance_km": s.distance_km,
                    "duration_str": s.duration_str,
                    "pace_min_km": s.pace_min_km,
                    "speed_kmh": s.speed_kmh,
                    "avg_hr": round(s.avg_hr) if s.avg_hr else None,
                    "avg_cadence": round(s.avg_cadence) if s.avg_cadence else None,
                    "elevation_gain_m": round(s.elevation_gain_m) if s.elevation_gain_m else None,
                }
                for s in a.splits
            ],
        })

    fitness_json = [
        {
            "date": r.date_str,
            "daily_load": r.daily_load,
            "ctl": r.ctl_fitness,
            "atl": r.atl_fatigue,
            "tsb": r.tsb_form,
            "status": r.status,
        }
        for r in fitness_records
    ]
    volume_json = [
        {
            "label": p.period_label.replace("Week ", "Wk "),
            "distance": p.total_distance_km,
            "hours": p.total_duration_hours,
            "elevation": p.total_elevation_m,
            "count": p.total_activities,
        }
        for p in volume_periods
    ]
    efforts_json = [
        {
            "category": e.category.replace("~", ""),
            "activity_name": e.activity_name,
            "activity_id": e.activity_id,
            "date": e.date_str,
            "value": e.value_display,
        }
        for e in best_efforts
    ]

    js_activities = _script_json(serialized_acts)
    js_fitness = _script_json(fitness_json)
    js_volume = _script_json(volume_json)
    js_vo2 = _script_json(vo2_history)
    js_efforts = _script_json(efforts_json)

    acwr_rec = calculate_acwr(fitness_records)
    if vdot_info is None:
        vdot_info = find_best_empirical_vdot(activities)

    # Build VDOT section HTML
    if vdot_info:
        v_val = vdot_info["vdot"]
        v_name = html.escape(str(vdot_info["benchmark_activity_name"]))
        v_dist = vdot_info["benchmark_distance"]
        v_time = vdot_info["benchmark_time"]
        v_date = vdot_info["benchmark_date"]
        v_paces = vdot_info["training_paces"]
        v_preds = vdot_info["race_predictions"]

        vdot_section_html = f"""
        <div class="panel-card" style="margin-bottom: 24px;">
            <div class="panel-header">
                <h2>Jack Daniels VDOT Training Paces (Empirical Benchmark: VDOT {v_val:.1f})</h2>
                <span class="badge badge-optimal">Source: {v_name} ({v_dist} in {v_time}) • {v_date}</span>
            </div>
            <p style="font-size: 13px; color: var(--text-secondary); margin-bottom: 16px;">
                Calculated strictly from your authentic maximal race benchmark using Daniels and Gilbert (1979). Prescribes target paces for your weekly training workouts without guesswork.
            </p>
            <table class="zone-table" style="margin-bottom: 20px;">
                <thead>
                    <tr>
                        <th>Pace Zone</th>
                        <th>Target Pace</th>
                        <th>Percent VO2 Max</th>
                        <th>Physiological Purpose and Workout Types</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><span class="badge badge-fresh">Easy (E-Pace)</span></td>
                        <td style="font-weight: 700; color: var(--accent-cyan);">{v_paces.get('easy_pace', 'N/A')}</td>
                        <td>62% to 70%</td>
                        <td>Aerobic base building, active recovery, conversational long runs (65% to 79% HRmax).</td>
                    </tr>
                    <tr>
                        <td><span class="badge badge-optimal">Marathon (M-Pace)</span></td>
                        <td style="font-weight: 700; color: var(--accent-emerald);">{v_paces.get('marathon_pace', 'N/A')}</td>
                        <td>~80%</td>
                        <td>Aerobic threshold tempo, steady-state marathon pacing stamina, glycogen sparing.</td>
                    </tr>
                    <tr>
                        <td><span class="badge badge-fatigued">Threshold (T-Pace)</span></td>
                        <td style="font-weight: 700; color: var(--accent-amber);">{v_paces.get('threshold_pace', 'N/A')}</td>
                        <td>~88%</td>
                        <td>Lactate clearance threshold, 20-30 min cruise intervals or 1-hour sustained race rhythm.</td>
                    </tr>
                    <tr>
                        <td><span class="badge badge-risk">Interval (I-Pace)</span></td>
                        <td style="font-weight: 700; color: var(--accent-rose);">{v_paces.get('interval_pace', 'N/A')}</td>
                        <td>97% to 100%</td>
                        <td>VO2 Max stimulation, 3 to 5 minute hard repeats (800m - 1200m) with equal work/rest ratio.</td>
                    </tr>
                    <tr>
                        <td><span class="badge" style="background: rgba(124, 58, 237, 0.15); color: #7c3aed;">Repetition (R-Pace)</span></td>
                        <td style="font-weight: 700; color: var(--accent-purple);">{v_paces.get('repetition_pace', 'N/A')}</td>
                        <td>&gt; 105%</td>
                        <td>Neuromuscular speed, running economy, 200m - 400m strides with full recovery.</td>
                    </tr>
                </tbody>
            </table>

            <div style="display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 12px; margin-top: 16px;">
                <div style="background: var(--bg-surface); padding: 12px 16px; border-radius: 8px; border: 1px solid var(--border-subtle);">
                    <div style="font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 700;">Equivalent 5K</div>
                    <div style="font-size: 18px; font-weight: 700; color: var(--text-primary); margin-top: 4px;">{v_preds.get('5k', 'N/A')}</div>
                </div>
                <div style="background: var(--bg-surface); padding: 12px 16px; border-radius: 8px; border: 1px solid var(--border-subtle);">
                    <div style="font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 700;">Equivalent 10K</div>
                    <div style="font-size: 18px; font-weight: 700; color: var(--text-primary); margin-top: 4px;">{v_preds.get('10k', 'N/A')}</div>
                </div>
                <div style="background: var(--bg-surface); padding: 12px 16px; border-radius: 8px; border: 1px solid var(--border-subtle);">
                    <div style="font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 700;">Equivalent Half Marathon</div>
                    <div style="font-size: 18px; font-weight: 700; color: var(--text-primary); margin-top: 4px;">{v_preds.get('half_marathon', 'N/A')}</div>
                </div>
                <div style="background: var(--bg-surface); padding: 12px 16px; border-radius: 8px; border: 1px solid var(--border-subtle);">
                    <div style="font-size: 11px; text-transform: uppercase; color: var(--text-muted); font-weight: 700;">Equivalent Marathon</div>
                    <div style="font-size: 18px; font-weight: 700; color: var(--text-primary); margin-top: 4px;">{v_preds.get('marathon', 'N/A')}</div>
                </div>
            </div>

            <div class="info-box" style="margin-top: 16px; margin-bottom: 0;">
                <strong>Scientific Note on VDOT vs Garmin Firstbeat VO2 Max:</strong>
                Garmin Firstbeat VO2 Max ({vo2_current_disp}) is a sub-maximal projection based on clean, steady-state heart-rate samples. True race VDOT ({v_val:.1f}) combines aerobic capacity with your real-world running economy, ground contact forces, and muscular fatigue resistance under race conditions.
            </div>
        </div>
        """
    else:
        vdot_section_html = """
        <div class="panel-card" style="margin-bottom: 24px;">
            <div class="panel-header">
                <h2>Jack Daniels VDOT Training Paces</h2>
                <span class="badge badge-fatigued">Benchmark Required</span>
            </div>
            <div class="info-box" style="margin-bottom: 0;">
                <strong>No Empirical Benchmark Run Found:</strong>
                In strict exercise physiology, Jack Daniels VDOT requires a genuine maximal race or time-trial effort (5K, 10K, Half Marathon, or Marathon). We do not fabricate a VDOT using sub-maximal jogs or heuristic multipliers. Log an all-out effort to unlock your personalized Daniels training paces.
            </div>
        </div>
        """

    html_content = f"""<!DOCTYPE html>
<html lang="en" data-theme="{default_theme}">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <meta name="color-scheme" content="light dark">
    <title>Ricelo Running Science | Performance Dashboard{title_suffix}</title>

    <!-- Professional Typography: Plus Jakarta Sans & JetBrains Mono -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600&display=swap" rel="stylesheet">

    <!-- Chart.js 4.4 CDN -->
    <script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.1/dist/chart.umd.min.js"></script>
    <style>
        :root, [data-theme="light"] {{
            color-scheme: light;
            --bg-primary: #f8fafc;
            --bg-surface: #ffffff;
            --bg-card: #ffffff;
            --bg-card-hover: #f1f5f9;
            --border-subtle: #e2e8f0;
            --border-accent: #cbd5e1;
            --accent-cyan: #0284c7;
            --accent-blue: #2563eb;
            --accent-emerald: #059669;
            --accent-amber: #d97706;
            --accent-purple: #7c3aed;
            --accent-rose: #e11d48;
            --text-primary: #0f172a;
            --text-secondary: #475569;
            --text-muted: #64748b;
            --card-shadow: 0 1px 3px 0 rgba(0, 0, 0, 0.05), 0 1px 2px -1px rgba(0, 0, 0, 0.04);
            --card-shadow-hover: 0 4px 6px -1px rgba(0, 0, 0, 0.07), 0 2px 4px -2px rgba(0, 0, 0, 0.05);
            --table-header-bg: #f1f5f9;
            --table-header-color: #334155;
            --chart-grid: #e2e8f0;
            --chart-tick: #64748b;
            --dialog-bg: #ffffff;
            --dialog-header-bg: #f8fafc;
            --dialog-backdrop: rgba(15, 23, 42, 0.45);
            --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }}

        [data-theme="dark"] {{
            color-scheme: dark;
            --bg-primary: #090d16;
            --bg-surface: #111827;
            --bg-card: rgba(17, 24, 39, 0.85);
            --bg-card-hover: rgba(30, 41, 59, 0.9);
            --border-subtle: #1e293b;
            --border-accent: #334155;
            --accent-cyan: #38bdf8;
            --accent-blue: #3b82f6;
            --accent-emerald: #10b981;
            --accent-amber: #f59e0b;
            --accent-purple: #a855f7;
            --accent-rose: #f43f5e;
            --text-primary: #f8fafc;
            --text-secondary: #94a3b8;
            --text-muted: #64748b;
            --card-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.3);
            --card-shadow-hover: 0 10px 15px -3px rgba(0, 0, 0, 0.4);
            --table-header-bg: #172136;
            --table-header-color: #94a3b8;
            --chart-grid: #1e293b;
            --chart-tick: #94a3b8;
            --dialog-bg: #0f172a;
            --dialog-header-bg: #131d33;
            --dialog-backdrop: rgba(0, 0, 0, 0.75);
            --font-sans: 'Plus Jakarta Sans', -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
            --font-mono: 'JetBrains Mono', ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace;
        }}

        * {{
            box-sizing: border-box;
            margin: 0;
            padding: 0;
        }}

        body {{
            font-family: var(--font-sans);
            background-color: var(--bg-primary);
            color: var(--text-primary);
            line-height: 1.5;
            padding: 24px 32px;
            min-height: 100vh;
            letter-spacing: -0.01em;
            -webkit-font-smoothing: antialiased;
            -moz-osx-font-smoothing: grayscale;
            transition: background-color 0.2s ease, color 0.2s ease;
        }}

        /* Monospace Tabular Numerics for Metrics and Data */
        .tabular, .kpi-value, .split-stat-item .val, .record-card .val, td {{
            font-family: var(--font-mono);
            font-feature-settings: "tnum";
            font-variant-numeric: tabular-nums;
        }}

        /* Scrollbars */
        ::-webkit-scrollbar {{
            width: 8px;
            height: 8px;
        }}
        ::-webkit-scrollbar-track {{
            background: var(--bg-primary);
        }}
        ::-webkit-scrollbar-thumb {{
            background: var(--border-accent);
            border-radius: 4px;
        }}

        /* Header Navigation */
        .top-nav {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 16px;
            padding-bottom: 20px;
            margin-bottom: 20px;
            border-bottom: 1px solid var(--border-subtle);
        }}

        .brand-titles h1 {{
            font-size: 24px;
            font-weight: 800;
            letter-spacing: -0.025em;
            color: var(--text-primary);
        }}

        .brand-titles p {{
            font-size: 13px;
            color: var(--text-secondary);
        }}

        .nav-right {{
            display: flex;
            align-items: center;
            gap: 12px;
            flex-wrap: wrap;
        }}

        .profile-chip {{
            display: flex;
            align-items: center;
            gap: 14px;
            background: var(--bg-surface);
            padding: 8px 16px;
            border-radius: 9999px;
            border: 1px solid var(--border-subtle);
            font-size: 13px;
            box-shadow: var(--card-shadow);
        }}

        .profile-chip .pill {{
            color: var(--text-secondary);
        }}
        .profile-chip strong {{
            color: var(--accent-cyan);
            font-weight: 700;
        }}

        .theme-toggle-btn {{
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            color: var(--text-primary);
            font-size: 13px;
            font-weight: 600;
            padding: 8px 14px;
            border-radius: 9999px;
            cursor: pointer;
            box-shadow: var(--card-shadow);
            transition: all 0.15s ease;
        }}
        .theme-toggle-btn:hover {{
            background: var(--bg-card-hover);
            border-color: var(--border-accent);
        }}

        /* Timeframe Scope Banner */
        .timeframe-banner {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 14px;
            margin-bottom: 24px;
        }}

        .timeframe-card {{
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 16px 20px;
            box-shadow: var(--card-shadow);
            transition: border-color 0.15s ease, box-shadow 0.15s ease;
        }}

        .timeframe-card:hover {{
            border-color: var(--border-accent);
            box-shadow: var(--card-shadow-hover);
        }}

        .timeframe-badge {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 4px;
        }}

        .timeframe-title {{
            font-size: 16px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 2px;
        }}

        .timeframe-sub {{
            font-size: 12px;
            color: var(--text-secondary);
        }}

        /* KPI Cards */
        .kpi-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
            gap: 16px;
            margin-bottom: 28px;
        }}

        .kpi-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 18px 20px;
            box-shadow: var(--card-shadow);
            transition: transform 0.15s ease, box-shadow 0.15s ease, border-color 0.15s ease;
        }}

        .kpi-card:hover {{
            border-color: var(--border-accent);
            transform: translateY(-2px);
            box-shadow: var(--card-shadow-hover);
        }}

        .kpi-title {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: var(--text-muted);
            margin-bottom: 6px;
        }}

        .kpi-value-row {{
            display: flex;
            align-items: baseline;
            gap: 8px;
            margin-bottom: 4px;
        }}

        .kpi-value {{
            font-size: 28px;
            font-weight: 700;
            letter-spacing: -0.02em;
            color: var(--text-primary);
        }}

        .kpi-subtext {{
            font-size: 12px;
            color: var(--text-secondary);
        }}

        .badge {{
            display: inline-flex;
            align-items: center;
            font-size: 11px;
            font-weight: 700;
            padding: 2px 7px;
            border-radius: 6px;
            text-transform: uppercase;
            letter-spacing: 0.03em;
        }}
        .badge-optimal {{ background: rgba(2, 132, 199, 0.1); color: #0284c7; border: 1px solid rgba(2, 132, 199, 0.2); }}
        .badge-fresh {{ background: rgba(5, 150, 105, 0.1); color: #059669; border: 1px solid rgba(5, 150, 105, 0.2); }}
        .badge-fatigued {{ background: rgba(217, 119, 6, 0.1); color: #d97706; border: 1px solid rgba(217, 119, 6, 0.2); }}
        .badge-risk {{ background: rgba(225, 29, 72, 0.1); color: #e11d48; border: 1px solid rgba(225, 29, 72, 0.2); }}

        /* Navigation Tab Header */
        .tabs-header {{
            display: flex;
            gap: 6px;
            background: var(--bg-surface);
            padding: 6px;
            border-radius: 10px;
            border: 1px solid var(--border-subtle);
            margin-bottom: 24px;
            overflow-x: auto;
            box-shadow: var(--card-shadow);
        }}

        .tab-btn {{
            background: transparent;
            border: 1px solid transparent;
            color: var(--text-secondary);
            font-size: 13px;
            font-weight: 600;
            padding: 8px 16px;
            border-radius: 7px;
            cursor: pointer;
            white-space: nowrap;
            transition: all 0.15s ease;
        }}

        .tab-btn:hover {{
            color: var(--text-primary);
            background: var(--bg-card-hover);
        }}

        .tab-btn.active {{
            color: var(--accent-cyan);
            background: var(--bg-primary);
            border-color: var(--border-subtle);
            font-weight: 700;
        }}

        .tab-content {{
            display: none;
        }}
        .tab-content.active {{
            display: block;
        }}

        /* Panels and Grids */
        .chart-grid {{
            display: grid;
            grid-template-columns: 2fr 1fr;
            gap: 20px;
            margin-bottom: 24px;
        }}
        @media (max-width: 1024px) {{
            .chart-grid {{
                grid-template-columns: 1fr;
            }}
        }}

        .panel-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 14px;
            padding: 22px;
            box-shadow: var(--card-shadow);
        }}

        .panel-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 8px;
            margin-bottom: 16px;
        }}

        .panel-header h2 {{
            font-size: 16px;
            font-weight: 700;
            color: var(--text-primary);
        }}

        .chart-wrapper {{
            position: relative;
            height: 330px;
            width: 100%;
        }}

        /* Table Controls and Responsive Table */
        .table-controls {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            flex-wrap: wrap;
            gap: 12px;
            margin-bottom: 16px;
        }}

        .search-box {{
            position: relative;
            min-width: 260px;
        }}

        .search-box input {{
            width: 100%;
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            border-radius: 8px;
            padding: 8px 14px;
            color: var(--text-primary);
            font-size: 13px;
            outline: none;
            font-family: var(--font-sans);
        }}
        .search-box input:focus {{
            border-color: var(--accent-cyan);
        }}

        .filter-group {{
            display: flex;
            gap: 6px;
        }}

        .filter-btn {{
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            color: var(--text-secondary);
            font-size: 12px;
            font-weight: 600;
            padding: 6px 12px;
            border-radius: 6px;
            cursor: pointer;
        }}
        .filter-btn.active {{
            background: rgba(2, 132, 199, 0.12);
            border-color: var(--accent-cyan);
            color: var(--accent-cyan);
        }}

        .table-wrapper {{
            overflow: auto;
            max-inline-size: 100%;
            max-block-size: min(650px, 75vh);
            container-type: inline-size;
            border-radius: 8px;
            border: 1px solid var(--border-subtle);
            background: var(--bg-surface);
            box-shadow: var(--card-shadow);
        }}

        table {{
            width: 100%;
            border-collapse: separate;
            border-spacing: 0;
            font-size: 13px;
        }}

        thead {{
            position: sticky;
            inset-block-start: 0;
            z-index: 5;
            background: var(--table-header-bg);
            backdrop-filter: blur(8px);
        }}

        thead th {{
            padding: 12px 14px;
            font-weight: 700;
            color: var(--table-header-color);
            border-bottom: 1px solid var(--border-accent);
            white-space: nowrap;
            text-align: left;
            user-select: none;
            cursor: pointer;
        }}
        thead th:hover {{
            color: var(--accent-cyan);
        }}

        tbody th {{
            position: sticky;
            inset-inline-start: 0;
            background: var(--bg-surface);
            z-index: 2;
            font-weight: 600;
            color: var(--text-primary);
            border-bottom: 1px solid var(--border-subtle);
            padding: 10px 14px;
            white-space: nowrap;
        }}

        tbody td {{
            padding: 10px 14px;
            border-bottom: 1px solid var(--border-subtle);
            color: var(--text-secondary);
            white-space: nowrap;
        }}

        tbody tr:hover td {{
            background: rgba(2, 132, 199, 0.04);
            color: var(--text-primary);
        }}
        tbody tr:hover th {{
            background: var(--bg-card-hover);
        }}

        .btn-inspect {{
            background: rgba(2, 132, 199, 0.08);
            color: var(--accent-cyan);
            border: 1px solid rgba(2, 132, 199, 0.2);
            padding: 4px 10px;
            border-radius: 6px;
            font-size: 12px;
            font-weight: 600;
            cursor: pointer;
            transition: all 0.15s ease;
        }}
        .btn-inspect:hover {{
            background: var(--accent-cyan);
            color: #ffffff;
        }}

        @container (width < 760px) {{
            th, td {{
                padding: 8px 10px;
                font-size: 12px;
            }}
        }}

        /* Native Modern <dialog> Modal */
        dialog.splits-dialog {{
            border: 1px solid var(--border-accent);
            border-radius: 16px;
            background: var(--dialog-bg);
            color: var(--text-primary);
            padding: 0;
            width: min(850px, 92vw);
            max-height: 88vh;
            margin: auto;
            box-shadow: 0 20px 40px -8px rgba(0, 0, 0, 0.25);
            outline: none;
        }}

        dialog.splits-dialog::backdrop {{
            background: var(--dialog-backdrop);
            backdrop-filter: blur(4px);
        }}

        .dialog-header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            padding: 18px 24px;
            border-bottom: 1px solid var(--border-subtle);
            background: var(--dialog-header-bg);
        }}

        .dialog-header h3 {{
            font-size: 18px;
            font-weight: 700;
            color: var(--text-primary);
        }}

        .dialog-close {{
            background: transparent;
            border: none;
            color: var(--text-muted);
            font-size: 24px;
            line-height: 1;
            cursor: pointer;
            padding: 4px;
        }}
        .dialog-close:hover {{
            color: var(--text-primary);
        }}

        .dialog-body {{
            padding: 24px;
            overflow-y: auto;
            max-height: calc(88vh - 80px);
        }}

        .split-stats-bar {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(130px, 1fr));
            gap: 12px;
            margin-bottom: 20px;
            background: var(--bg-primary);
            padding: 14px 18px;
            border-radius: 10px;
            border: 1px solid var(--border-subtle);
        }}

        .split-stat-item .label {{
            font-size: 11px;
            font-weight: 700;
            text-transform: uppercase;
            color: var(--text-muted);
        }}
        .split-stat-item .val {{
            font-size: 16px;
            font-weight: 700;
            color: var(--accent-cyan);
        }}

        .pace-bar-container {{
            width: 100%;
            height: 6px;
            background: var(--border-subtle);
            border-radius: 3px;
            overflow: hidden;
            margin-top: 4px;
        }}
        .pace-bar-fill {{
            height: 100%;
            background: var(--accent-cyan);
            border-radius: 3px;
        }}

        /* Records Grid */
        .records-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
            gap: 16px;
        }}

        .record-card {{
            background: var(--bg-card);
            border: 1px solid var(--border-subtle);
            border-radius: 12px;
            padding: 20px;
            box-shadow: var(--card-shadow);
            position: relative;
        }}
        .record-card h3 {{
            font-size: 13px;
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.04em;
            color: var(--accent-cyan);
            margin-bottom: 4px;
        }}
        .record-card .val {{
            font-size: 24px;
            font-weight: 700;
            color: var(--text-primary);
            margin-bottom: 8px;
        }}
        .record-card .meta {{
            font-size: 12px;
            color: var(--text-secondary);
        }}

        /* Info Boxes */
        .info-box {{
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            padding: 18px 22px;
            border-radius: 12px;
            margin-bottom: 20px;
            font-size: 13px;
            color: var(--text-secondary);
            box-shadow: var(--card-shadow);
        }}
        .info-box strong {{
            color: var(--text-primary);
        }}

        .scope-banner {{
            display: inline-flex;
            align-items: center;
            background: var(--bg-surface);
            border: 1px solid var(--border-subtle);
            color: var(--text-secondary);
            padding: 6px 14px;
            border-radius: 8px;
            font-size: 12px;
            font-weight: 600;
            margin-bottom: 16px;
            box-shadow: var(--card-shadow);
        }}

        .zone-table {{
            width: 100%;
            border-collapse: collapse;
            font-size: 13px;
            margin-top: 12px;
        }}
        .zone-table th, .zone-table td {{
            padding: 10px 14px;
            border-bottom: 1px solid var(--border-subtle);
            text-align: left;
        }}
        .zone-table th {{
            color: var(--text-muted);
            font-weight: 700;
        }}
    </style>
</head>
<body>

    <!-- Header Navigation -->
    <header class="top-nav">
        <div class="brand-titles">
            <h1>Ricelo Running Science</h1>
            <p>Precision Running Telemetry, Sub Integer VO2 Max Progression, and Training Load Engine</p>
        </div>

        <div class="nav-right">
            <div class="profile-chip">
                {athlete_pill}
                <div class="pill">Max HR: <strong>{max_hr_disp}</strong></div>
            </div>

            <!-- Theme Toggle Button -->
            <button id="themeToggleBtn" class="theme-toggle-btn" onclick="toggleTheme()" title="Toggle Light / Dark mode">
                Theme: Light
            </button>
        </div>
    </header>

    <!-- Prominent Timeframe Scopes Banner -->
    <section class="timeframe-banner">
        <div class="timeframe-card">
            <div class="timeframe-badge">Activities and Training Load Window</div>
            <div class="timeframe-title">Last {weeks_scope} Weeks ({days_scope} Days)</div>
            <div class="timeframe-sub">{act_date_range} • {len(activities)} activities recorded</div>
        </div>

        <div class="timeframe-card">
            <div class="timeframe-badge">Longitudinal VO2 Max Progression</div>
            <div class="timeframe-title">FIT Archive ({vo2_years_str})</div>
            <div class="timeframe-sub">{vo2_date_range} • {vo2_count} Firstbeat FIT runs</div>
        </div>

        <div class="timeframe-card">
            <div class="timeframe-badge">Personal Records and Best Efforts</div>
            <div class="timeframe-title">All Time Career Benchmarks</div>
            <div class="timeframe-sub">Evaluated across all historical sessions</div>
        </div>
    </section>

    <!-- Top KPI Cards -->
    <section class="kpi-grid">
        <div class="kpi-card">
            <div class="kpi-title">Current VO2 Max (FIT Archive)</div>
            <div class="kpi-value-row">
                <span class="kpi-value">{vo2_current_disp}</span>
                <span class="badge badge-fresh">{vo2_delta_disp}</span>
            </div>
            <div class="kpi-subtext">Garmin Watch: <strong>{vo2_watch_disp}</strong> • {vo2_count} runs • {vo2_years_str}</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-title">Fitness (CTL: 42 Day Base)</div>
            <div class="kpi-value-row">
                <span class="kpi-value">{latest_fitness.ctl_fitness:.1f}</span>
                <span class="badge badge-optimal">Productive</span>
            </div>
            <div class="kpi-subtext">Chronic Training Load rolling base</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-title">Fatigue (ATL: 7 Day Stress)</div>
            <div class="kpi-value-row">
                <span class="kpi-value">{latest_fitness.atl_fatigue:.1f}</span>
                <span class="badge badge-fatigued">Managed</span>
            </div>
            <div class="kpi-subtext">Acute physiological load accumulator</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-title">Form (TSB: Today)</div>
            <div class="kpi-value-row">
                <span class="kpi-value">{latest_fitness.tsb_form:+.1f}</span>
            </div>
            <div class="kpi-subtext">Training Stress Balance (CTL minus ATL)</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-title">Workload Ratio (ACWR)</div>
            <div class="kpi-value-row">
                <span class="kpi-value">{acwr_rec.acwr:.2f}</span>
                <span class="badge {acwr_rec.status_class}">{acwr_rec.status}</span>
            </div>
            <div class="kpi-subtext">Gabbett injury model: 7d acute vs 28d chronic</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-title">30 Day Training Volume</div>
            <div class="kpi-value-row">
                <span class="kpi-value">{dist_30d:.1f} km</span>
            </div>
            <div class="kpi-subtext">{len(acts_30d)} sessions • {time_30d_h:.1f} hrs • +{elev_30d_m:.0f}m elev</div>
        </div>

        <div class="kpi-card">
            <div class="kpi-title">Cardiac Drift (Latest Run)</div>
            <div class="kpi-value-row">
                <span class="kpi-value">{drift_value}</span>
                <span class="badge {drift_badge}">{drift_label}</span>
            </div>
            <div class="kpi-subtext">{drift_subtext}</div>
        </div>
    </section>

    <!-- Navigation Tabs -->
    <nav class="tabs-header">
        <button class="tab-btn active" onclick="switchTab('performanceTab', this)">Performance and Load Curves ({weeks_scope} Weeks)</button>
        <button class="tab-btn" onclick="switchTab('vo2Tab', this)">VO2 Max Progression ({vo2_count} Runs)</button>
        <button class="tab-btn" onclick="switchTab('activitiesTab', this)">Running Activities Explorer ({weeks_scope} Weeks)</button>
        <button class="tab-btn" onclick="switchTab('effortsTab', this)">All Time Running Personal Records</button>
        <button class="tab-btn" onclick="switchTab('zonesTab', this)">Physiological Zones and Telemetry</button>
    </nav>

    <!-- TAB 1: Performance and Load Curves (Last 12 Weeks) -->
    <main id="performanceTab" class="tab-content active">
        <div class="scope-banner">
            <span><strong>Data Scope:</strong> Last {weeks_scope} Weeks ({days_scope} Days: {act_date_range}) • Banister Daily Load Model</span>
        </div>

        <div class="chart-grid">
            <div class="panel-card">
                <div class="panel-header">
                    <h2>Banister Impulse Response Fitness and Fatigue (CTL, ATL, Form / TSB)</h2>
                    <span style="font-size: 12px; color: var(--text-muted);">Decay: ATL 7 Days, CTL 42 Days</span>
                </div>
                <div class="chart-wrapper">
                    <canvas id="fitnessChart"></canvas>
                </div>
            </div>

            <div class="panel-card">
                <div class="panel-header">
                    <h2>Weekly Volume (km)</h2>
                    <span style="font-size: 12px; color: var(--text-muted);">{weeks_scope} Weeks History</span>
                </div>
                <div class="chart-wrapper">
                    <canvas id="volumeChart"></canvas>
                </div>
            </div>
        </div>

        <div class="info-box">
            <strong>Training Stress Balance (TSB) Interpretation:</strong>
            Form &gt; +5 (Fresh / Peak Race Execution) | Form -10 to +5 (Neutral Maintenance) | Form -30 to -10 (Productive Aerobic Overload) | Form &lt; -30 (High Volume Accumulation).
        </div>
    </main>

    <!-- TAB 2: VO2 Max Longitudinal Progression -->
    <main id="vo2Tab" class="tab-content">
        <div class="scope-banner">
            <span><strong>Data Scope:</strong> FIT Archive ({vo2_years_str}: {vo2_date_range}) • {vo2_count} running sessions extracted from raw FIT telemetry</span>
        </div>

        <div class="panel-card" style="margin-bottom: 24px;">
            <div class="panel-header">
                <h2>Longitudinal Firstbeat Sub Integer VO2 Max Telemetry ({vo2_count} Runs)</h2>
                <span class="badge badge-optimal">{vo2_range_disp}</span>
            </div>
            <div class="chart-wrapper" style="height: 380px;">
                <canvas id="vo2Chart"></canvas>
            </div>
        </div>

        <div class="info-box">
            <strong>Firstbeat Sub Integer Telemetry:</strong>
            Garmin Connect web and watches round VO2 max to whole integers (such as 48.35 shown as 48; 47.71 shown as 48). This analytics system extracts raw unrounded sub integer oxygen consumption values directly from FIT Message 140, Field 7 using the formula <code>(raw_val * 3.5) / 65536.0</code>.
        </div>
    </main>

    <!-- TAB 3: Activities and Laps Explorer -->
    <main id="activitiesTab" class="tab-content">
        <div class="scope-banner">
            <span><strong>Data Scope:</strong> Last {weeks_scope} Weeks ({act_date_range}) • {len(activities)} Activities Recorded</span>
        </div>

        <div class="panel-card">
            <div class="table-controls">
                <div class="search-box">
                    <input type="search" id="activitySearch" placeholder="Search activities by name, date, pace..." oninput="handleActivitySearch()" />
                </div>
                <div class="filter-group">
                    <button class="filter-btn active" onclick="filterSport('all', this)">All</button>
                    <button class="filter-btn" onclick="filterSport('running', this)">Running</button>
                    <button class="filter-btn" onclick="filterSport('walking', this)">Walking</button>
                    <button class="filter-btn" onclick="filterSport('cycling', this)">Cycling</button>
                </div>
            </div>

            <!-- Table Wrapper per Modern Web Guidance -->
            <div class="table-wrapper" tabindex="0" role="region" aria-label="Activities Table">
                <table id="activitiesTable">
                    <thead>
                        <tr>
                            <th onclick="sortTable(0)">Date</th>
                            <th onclick="sortTable(1)">Activity Name</th>
                            <th onclick="sortTable(2)">Type</th>
                            <th onclick="sortTable(3)" style="text-align: right;">Distance</th>
                            <th onclick="sortTable(4)" style="text-align: right;">Duration</th>
                            <th onclick="sortTable(5)" style="text-align: right;">Pace / Speed</th>
                            <th onclick="sortTable(6)" style="text-align: right;">Avg HR</th>
                            <th onclick="sortTable(7)" style="text-align: right;">Aerobic TE</th>
                            <th onclick="sortTable(8)" style="text-align: right;">Cardiac Drift</th>
                            <th style="text-align: center;">Splits</th>
                        </tr>
                    </thead>
                    <tbody id="activitiesTbody">
                        <!-- Populated by JavaScript -->
                    </tbody>
                </table>
            </div>
        </div>
    </main>

    <!-- TAB 4: Personal Records and Best Efforts -->
    <main id="effortsTab" class="tab-content">
        <div class="scope-banner">
            <span><strong>Data Scope:</strong> All Time Career History • Evaluated across all historical running sessions</span>
        </div>

        <div class="records-grid" id="recordsGrid">
            <!-- Populated by JavaScript -->
        </div>
    </main>

    <!-- TAB 5: Physiological Zones and Telemetry Engine -->
    <main id="zonesTab" class="tab-content">
        <div class="panel-card" style="margin-bottom: 24px;">
            <div class="panel-header">
                <h2>Heart Rate Physiological Training Zones (Max HR: {max_hr_disp})</h2>
            </div>
            <table class="zone-table">
                <thead>
                    <tr>
                        <th>Zone</th>
                        <th>Name</th>
                        <th>HR Range</th>
                        <th>Percent Max HR</th>
                        <th>Physiological Purpose and Adaptation</th>
                    </tr>
                </thead>
                <tbody>
                    <tr>
                        <td><span class="badge" style="background: rgba(148, 163, 184, 0.2); color: #475569;">Zone 1</span></td>
                        <td>Active Recovery</td>
                        <td>{z1_hr}</td>
                        <td>&lt; 70%</td>
                        <td>Promotes blood flow, clears metabolic waste, recovery between hard days.</td>
                    </tr>
                    <tr>
                        <td><span class="badge" style="background: rgba(5, 150, 105, 0.15); color: #059669;">Zone 2</span></td>
                        <td>Aerobic Base Endurance</td>
                        <td>{z2_hr}</td>
                        <td>70% to 80%</td>
                        <td>Mitochondrial biogenesis, capillary density expansion, fatty acid oxidation efficiency.</td>
                    </tr>
                    <tr>
                        <td><span class="badge" style="background: rgba(2, 132, 199, 0.15); color: #0284c7;">Zone 3</span></td>
                        <td>Aerobic Tempo</td>
                        <td>{z3_hr}</td>
                        <td>80% to 89%</td>
                        <td>Sustained marathon pace rhythm, glycogen storage capacity, cardiac stroke volume.</td>
                    </tr>
                    <tr>
                        <td><span class="badge" style="background: rgba(217, 119, 6, 0.15); color: #d97706;">Zone 4</span></td>
                        <td>Lactate Threshold (Anaerobic)</td>
                        <td>{z4_hr}</td>
                        <td>89% to 95%</td>
                        <td>Lactate clearance capacity, high end 10K / Half Marathon racing speed stamina.</td>
                    </tr>
                    <tr>
                        <td><span class="badge" style="background: rgba(225, 29, 72, 0.15); color: #e11d48;">Zone 5</span></td>
                        <td>VO2 Max and Neuromuscular</td>
                        <td>{z5_hr}</td>
                        <td>&gt; 95%</td>
                        <td>Maximal oxygen uptake velocity, fast twitch motor unit recruitment.</td>
                    </tr>
                </tbody>
            </table>
        </div>

        {vdot_section_html}

        <div class="panel-card">
            <div class="panel-header">
                <h2>Aerobic Decoupling (Cardiac Drift / Efficiency Factor)</h2>
            </div>
            <div class="info-box">
                <strong>Efficiency Factor (EF):</strong> <code>EF = Speed (m/s) / Heart Rate (bpm)</code>
                <br>
                <strong>Decoupling Metric (% Drift):</strong> <code>((EF_first_half - EF_second_half) / EF_first_half) * 100%</code>
                <br><br>
                <strong>Scientific Benchmarks (Joe Friel / Firstbeat Analytics):</strong>
                <ul style="margin-left: 20px; margin-top: 8px;">
                    <li><strong>&lt; 3.5% (Exceptional Aerobic Stamina):</strong> Speed and heart rate are in near perfect equilibrium. Cardiovascular system is unstressed over the duration.</li>
                    <li><strong>3.5% to 5.0% (Well Conditioned):</strong> Standard healthy decoupling for long duration training without excessive cardiac fatigue.</li>
                    <li><strong>5.0% to 8.0% (Moderate Drift):</strong> Typical for endurance efforts exceeding 90+ minutes, warm weather conditions, or progressive pace builds.</li>
                    <li><strong>&gt; 8.0% (Significant Cardiac Decoupling):</strong> Glycogen depletion, plasma volume reduction (dehydration), or severe pacing overshoot.</li>
                </ul>
            </div>
        </div>
    </main>

    <!-- Native Modern <dialog> Modal for Activity Laps / Splits -->
    <dialog id="splitsModal" class="splits-dialog">
        <div class="dialog-header">
            <div>
                <h3 id="modalTitle">Activity Details</h3>
                <span id="modalSubtitle" style="font-size: 13px; color: var(--text-secondary);">Date and Time</span>
            </div>
            <button class="dialog-close" onclick="closeSplitsModal()">&times;</button>
        </div>
        <div class="dialog-body">
            <div class="split-stats-bar" id="modalStatsBar">
                <!-- Dynamically populated -->
            </div>

            <div class="table-wrapper" tabindex="0" role="region" aria-label="Splits Table">
                <table>
                    <thead>
                        <tr>
                            <th>Split</th>
                            <th>Distance</th>
                            <th>Time</th>
                            <th>Pace</th>
                            <th style="min-width: 140px;">Pace Profile</th>
                            <th>Avg HR</th>
                            <th>Cadence</th>
                            <th>Elevation</th>
                        </tr>
                    </thead>
                    <tbody id="modalSplitsTbody">
                        <!-- Dynamically populated -->
                    </tbody>
                </table>
            </div>
        </div>
    </dialog>

    <!-- Client-Side JavaScript Logic -->
    <script>
        const activitiesData = {js_activities};
        const fitnessData = {js_fitness};
        const volumeData = {js_volume};
        const vo2Data = {js_vo2};
        const effortsData = {js_efforts};

        let currentSportFilter = 'all';
        let searchQuery = '';
        let sortCol = 0;
        let sortAsc = false;
        let currentTheme = '{default_theme}';

        function escapeHtml(value) {{
            return String(value ?? '').replace(/[&<>"']/g, ch => ({{
                '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
            }})[ch]);
        }}

        let chartFitness = null;
        let chartVolume = null;
        let chartVo2 = null;

        function toggleTheme() {{
            const newTheme = currentTheme === 'light' ? 'dark' : 'light';
            setTheme(newTheme);
        }}

        function setTheme(theme) {{
            currentTheme = theme;
            document.documentElement.setAttribute('data-theme', theme);
            const btn = document.getElementById('themeToggleBtn');
            if (btn) {{
                btn.textContent = theme === 'light' ? 'Theme: Light' : 'Theme: Dark';
            }}
            updateChartsTheme(theme);
        }}

        function updateChartsTheme(theme) {{
            const gridColor = theme === 'light' ? '#e2e8f0' : '#1e293b';
            const tickColor = theme === 'light' ? '#64748b' : '#94a3b8';
            const labelColor = theme === 'light' ? '#0f172a' : '#f8fafc';

            [chartFitness, chartVolume, chartVo2].forEach(c => {{
                if (c) {{
                    if (c.options.scales.x) {{
                        c.options.scales.x.grid.color = gridColor;
                        c.options.scales.x.ticks.color = tickColor;
                    }}
                    if (c.options.scales.y) {{
                        c.options.scales.y.grid.color = gridColor;
                        c.options.scales.y.ticks.color = tickColor;
                    }}
                    if (c.options.plugins && c.options.plugins.legend) {{
                        c.options.plugins.legend.labels.color = labelColor;
                    }}
                    c.update();
                }}
            }});
        }}

        function switchTab(tabId, btn) {{
            document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
            document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
            document.getElementById(tabId).classList.add('active');
            btn.classList.add('active');
        }}

        function renderActivities() {{
            const tbody = document.getElementById('activitiesTbody');
            tbody.innerHTML = '';

            let filtered = activitiesData.filter(act => {{
                if (currentSportFilter !== 'all' && !act.type.toLowerCase().includes(currentSportFilter)) {{
                    return false;
                }}
                if (searchQuery) {{
                    const q = searchQuery.toLowerCase();
                    const matchName = act.name.toLowerCase().includes(q);
                    const matchDate = act.date.toLowerCase().includes(q);
                    const matchPace = act.pace_min_km.toLowerCase().includes(q);
                    if (!matchName && !matchDate && !matchPace) return false;
                }}
                return true;
            }});

            filtered.sort((a, b) => {{
                let valA, valB;
                switch (sortCol) {{
                    case 0: valA = a.date; valB = b.date; break;
                    case 1: valA = a.name; valB = b.name; break;
                    case 2: valA = a.type; valB = b.type; break;
                    case 3: valA = a.distance_km; valB = b.distance_km; break;
                    case 4: valA = a.duration_s; valB = b.duration_s; break;
                    case 5: valA = a.speed_kmh; valB = b.speed_kmh; break;
                    case 6: valA = a.avg_hr || 0; valB = b.avg_hr || 0; break;
                    case 7: valA = a.aerobic_te || 0; valB = b.aerobic_te || 0; break;
                    case 8: valA = a.decoupling_pct || 0; valB = b.decoupling_pct || 0; break;
                    default: valA = a.date; valB = b.date;
                }}
                if (valA < valB) return sortAsc ? -1 : 1;
                if (valA > valB) return sortAsc ? 1 : -1;
                return 0;
            }});

            filtered.forEach(act => {{
                const tr = document.createElement('tr');

                let decBadge = '<span style="color: var(--text-muted);">N/A</span>';
                if (act.decoupling_pct !== null && act.decoupling_pct !== undefined) {{
                    const d = act.decoupling_pct;
                    const badgeClass = d < 5.0 ? 'badge-fresh' : (d < 8.0 ? 'badge-fatigued' : 'badge-risk');
                    decBadge = `<span class="badge ${{badgeClass}}">${{d.toFixed(1)}}%</span>`;
                }}

                const speedDisp = act.type.toLowerCase().includes('run') ? act.pace_min_km : `${{act.speed_kmh.toFixed(1)}} km/h`;
                const hrDisp = act.avg_hr ? `${{act.avg_hr}} bpm` : 'N/A';
                const teDisp = act.aerobic_te ? `${{act.aerobic_te.toFixed(1)}}` : 'N/A';

                tr.innerHTML = `
                    <th>${{act.date}}</th>
                    <td><strong>${{escapeHtml(act.name)}}</strong></td>
                    <td><span class="badge" style="background: rgba(0,0,0,0.05); color: var(--text-secondary);">${{escapeHtml(act.type)}}</span></td>
                    <td style="text-align: right; color: var(--accent-amber); font-weight: 700;">${{act.distance_km.toFixed(2)}} km</td>
                    <td style="text-align: right;">${{act.duration_str}}</td>
                    <td style="text-align: right; color: var(--accent-cyan); font-weight: 700;">${{speedDisp}}</td>
                    <td style="text-align: right; color: var(--accent-rose); font-weight: 600;">${{hrDisp}}</td>
                    <td style="text-align: right; color: var(--accent-blue);">${{teDisp}}</td>
                    <td style="text-align: right;">${{decBadge}}</td>
                    <td style="text-align: center;">
                        <button class="btn-inspect" onclick="openSplitsModal(${{act.activity_id}})">Splits (${{act.splits.length}})</button>
                    </td>
                `;
                tbody.appendChild(tr);
            }});
        }}

        function handleActivitySearch() {{
            searchQuery = document.getElementById('activitySearch').value.trim();
            renderActivities();
        }}

        function filterSport(sport, btn) {{
            currentSportFilter = sport;
            document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
            btn.classList.add('active');
            renderActivities();
        }}

        function sortTable(colIndex) {{
            if (sortCol === colIndex) {{
                sortAsc = !sortAsc;
            }} else {{
                sortCol = colIndex;
                sortAsc = true;
            }}
            renderActivities();
        }}

        const splitsModal = document.getElementById('splitsModal');

        splitsModal.addEventListener('click', (e) => {{
            const rect = splitsModal.getBoundingClientRect();
            if (e.clientX < rect.left || e.clientX > rect.right || e.clientY < rect.top || e.clientY > rect.bottom) {{
                splitsModal.close();
            }}
        }});

        function closeSplitsModal() {{
            splitsModal.close();
        }}

        function openSplitsModal(activityId) {{
            const act = activitiesData.find(a => a.activity_id === activityId);
            if (!act) return;

            document.getElementById('modalTitle').textContent = act.name;
            document.getElementById('modalSubtitle').textContent = `${{act.full_date}} • ID: ${{act.activity_id}} • ${{act.type}}`;

            const statsBar = document.getElementById('modalStatsBar');
            statsBar.innerHTML = `
                <div class="split-stat-item">
                    <div class="label">Distance</div>
                    <div class="val">${{act.distance_km.toFixed(2)}} km</div>
                </div>
                <div class="split-stat-item">
                    <div class="label">Duration</div>
                    <div class="val">${{act.duration_str}}</div>
                </div>
                <div class="split-stat-item">
                    <div class="label">Average Pace</div>
                    <div class="val">${{act.pace_min_km}}</div>
                </div>
                <div class="split-stat-item">
                    <div class="label">Heart Rate</div>
                    <div class="val" style="color: var(--accent-rose);">${{act.avg_hr ? act.avg_hr + ' bpm' : 'N/A'}}</div>
                </div>
                <div class="split-stat-item">
                    <div class="label">Cardiac Drift</div>
                    <div class="val" style="color: var(--accent-emerald);">${{act.decoupling_pct !== null && act.decoupling_pct !== undefined ? act.decoupling_pct.toFixed(2) + '%' : 'N/A'}}</div>
                </div>
                <div class="split-stat-item">
                    <div class="label">Elevation Gain</div>
                    <div class="val" style="color: var(--accent-amber);">${{act.elevation_gain_m !== null ? '+' + act.elevation_gain_m + 'm' : 'N/A'}}</div>
                </div>
            `;

            const tbody = document.getElementById('modalSplitsTbody');
            tbody.innerHTML = '';

            if (!act.splits || act.splits.length === 0) {{
                tbody.innerHTML = `<tr><td colspan="8" style="text-align: center; padding: 24px; color: var(--text-muted);">No individual split intervals recorded for this session.</td></tr>`;
            }} else {{
                const speeds = act.splits.map(s => s.speed_kmh).filter(s => s > 0);
                const maxSpeed = Math.max(...speeds, 1.0);

                act.splits.forEach(s => {{
                    const tr = document.createElement('tr');
                    const barPct = Math.min(100, Math.max(10, (s.speed_kmh / maxSpeed) * 100));

                    let hrColor = 'var(--text-secondary)';
                    if (s.avg_hr) {{
                        if (s.avg_hr >= 165) hrColor = 'var(--accent-rose)';
                        else if (s.avg_hr >= 148) hrColor = 'var(--accent-cyan)';
                        else hrColor = 'var(--accent-emerald)';
                    }}

                    tr.innerHTML = `
                        <th>Split ${{s.split}}</th>
                        <td>${{s.distance_km.toFixed(2)}} km</td>
                        <td>${{s.duration_str}}</td>
                        <td style="color: var(--accent-cyan); font-weight: 700;">${{s.pace_min_km}}</td>
                        <td>
                            <div class="pace-bar-container">
                                <div class="pace-bar-fill" style="width: ${{barPct}}%;"></div>
                            </div>
                        </td>
                        <td style="color: ${{hrColor}}; font-weight: 700;">${{s.avg_hr ? s.avg_hr + ' bpm' : 'N/A'}}</td>
                        <td>${{s.avg_cadence ? s.avg_cadence + ' spm' : 'N/A'}}</td>
                        <td style="color: var(--accent-amber);">${{s.elevation_gain_m !== null ? '+' + s.elevation_gain_m + 'm' : 'N/A'}}</td>
                    `;
                    tbody.appendChild(tr);
                }});
            }}

            splitsModal.showModal();
        }}

        function renderBestEfforts() {{
            const grid = document.getElementById('recordsGrid');
            grid.innerHTML = '';

            effortsData.forEach(e => {{
                const card = document.createElement('div');
                card.className = 'record-card';
                card.innerHTML = `
                    <h3>${{e.category}}</h3>
                    <div class="val">${{e.value}}</div>
                    <div class="meta">${{escapeHtml(e.activity_name)}} • ${{e.date}}</div>
                `;
                grid.appendChild(card);
            }});
        }}

        window.addEventListener('DOMContentLoaded', () => {{
            renderActivities();
            renderBestEfforts();

            const isLight = currentTheme === 'light';
            const gridColor = isLight ? '#e2e8f0' : '#1e293b';
            const tickColor = isLight ? '#64748b' : '#94a3b8';
            const labelColor = isLight ? '#0f172a' : '#f8fafc';

            const ctxFitness = document.getElementById('fitnessChart').getContext('2d');
            const datesFit = fitnessData.map(d => d.date);
            const ctlFit = fitnessData.map(d => d.ctl);
            const atlFit = fitnessData.map(d => d.atl);
            const tsbFit = fitnessData.map(d => d.tsb);

            chartFitness = new Chart(ctxFitness, {{
                type: 'line',
                data: {{
                    labels: datesFit,
                    datasets: [
                        {{
                            label: 'Fitness (CTL)',
                            data: ctlFit,
                            borderColor: '#0284c7',
                            borderWidth: 2.5,
                            tension: 0.3,
                            pointRadius: 1,
                        }},
                        {{
                            label: 'Fatigue (ATL)',
                            data: atlFit,
                            borderColor: '#e11d48',
                            borderWidth: 2,
                            tension: 0.3,
                            pointRadius: 1,
                        }},
                        {{
                            label: 'Form (TSB)',
                            data: tsbFit,
                            borderColor: '#059669',
                            backgroundColor: 'rgba(5, 150, 105, 0.12)',
                            fill: true,
                            borderWidth: 2,
                            tension: 0.3,
                            pointRadius: 1,
                        }}
                    ]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    interaction: {{
                        mode: 'index',
                        intersect: false,
                    }},
                    scales: {{
                        x: {{
                            grid: {{ color: gridColor }},
                            ticks: {{ color: tickColor, maxTicksLimit: 10, font: {{ family: 'var(--font-mono)' }} }}
                        }},
                        y: {{
                            grid: {{ color: gridColor }},
                            ticks: {{ color: tickColor, font: {{ family: 'var(--font-mono)' }} }}
                        }}
                    }},
                    plugins: {{
                        legend: {{
                            labels: {{ color: labelColor, font: {{ family: 'var(--font-sans)', size: 12, weight: 600 }} }}
                        }}
                    }}
                }}
            }});

            const ctxVol = document.getElementById('volumeChart').getContext('2d');
            chartVolume = new Chart(ctxVol, {{
                type: 'bar',
                data: {{
                    labels: volumeData.map(v => v.label),
                    datasets: [{{
                        label: 'Distance (km)',
                        data: volumeData.map(v => v.distance),
                        backgroundColor: '#d97706',
                        borderRadius: 6,
                    }}]
                }},
                options: {{
                    responsive: true,
                    maintainAspectRatio: false,
                    scales: {{
                        x: {{
                            grid: {{ color: gridColor }},
                            ticks: {{ color: tickColor, maxRotation: 45, font: {{ family: 'var(--font-mono)' }} }}
                        }},
                        y: {{
                            grid: {{ color: gridColor }},
                            ticks: {{ color: tickColor, font: {{ family: 'var(--font-mono)' }} }}
                        }}
                    }},
                    plugins: {{
                        legend: {{ display: false }}
                    }}
                }}
            }});

            if (vo2Data.length > 0) {{
                const ctxVo2 = document.getElementById('vo2Chart').getContext('2d');
                chartVo2 = new Chart(ctxVo2, {{
                    type: 'line',
                    data: {{
                        labels: vo2Data.map(v => v.date),
                        datasets: [{{
                            label: 'Firstbeat Exact VO2 Max (mL/kg/min)',
                            data: vo2Data.map(v => v.vo2_max),
                            borderColor: '#059669',
                            backgroundColor: 'rgba(5, 150, 105, 0.08)',
                            fill: true,
                            borderWidth: 2.5,
                            tension: 0.35,
                            pointRadius: 2.5,
                            pointHoverRadius: 6,
                            pointBackgroundColor: '#059669',
                        }}]
                    }},
                    options: {{
                        responsive: true,
                        maintainAspectRatio: false,
                        scales: {{
                            x: {{
                                grid: {{ color: gridColor }},
                                ticks: {{ color: tickColor, maxTicksLimit: 14, font: {{ family: 'var(--font-mono)' }} }}
                            }},
                            y: {{
                                min: 41.5,
                                max: 46.0,
                                grid: {{ color: gridColor }},
                                ticks: {{ color: tickColor, font: {{ family: 'var(--font-mono)' }} }}
                            }}
                        }},
                        plugins: {{
                            tooltip: {{
                                callbacks: {{
                                    title: (items) => {{
                                        const idx = items[0].dataIndex;
                                        return `${{vo2Data[idx].date}} / ${{vo2Data[idx].name}}`;
                                    }},
                                    label: (item) => {{
                                        const idx = item.dataIndex;
                                        return `VO2 Max: ${{vo2Data[idx].vo2_max.toFixed(2)}} (Watch Display: ${{vo2Data[idx].vo2_display}}) | Dist: ${{vo2Data[idx].distance_km}} km`;
                                    }}
                                }}
                            }},
                            legend: {{
                                labels: {{ color: labelColor, font: {{ family: 'var(--font-sans)', size: 12, weight: 600 }} }}
                            }}
                        }}
                    }}
                }});
            }}
        }});
    </script>
</body>
</html>
"""
    out_file.write_text(html_content, encoding="utf-8")
    return out_file
