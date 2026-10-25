"""Command-line interface for ricelo running science."""

from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Optional

import typer
from rich.console import Console

from .client import GarminAuthError, default_client
from .fit import extract_and_parse_fit
from .models import Activity
from .performance import (
    aggregate_volume,
    calculate_acwr,
    calculate_fitness_fatigue,
    calculate_vdot,
    compare_activities,
    find_best_efforts,
    find_best_empirical_vdot,
    get_vdot_race_predictions,
    get_vdot_training_paces,
)
from .recovery import evaluate_daily_readiness
from .reporting import (
    generate_html_dashboard,
    print_activities_table,
    print_activity_detail,
    print_comparison,
    print_fitness_timeline,
    print_readiness,
    print_volume_summary,
)

app = typer.Typer(
    name="ricelo",
    help="ricelo running science - Precision running telemetry, sub-integer VO2 max, and physiological load intelligence",
    add_completion=False,
)
console = Console()


@app.command("auth")
def check_auth() -> None:
    """Verify Garmin Personal API key and connection."""
    try:
        profile = default_client.get_profile()
        name = profile.get("displayName") or "Garmin User"
        console.print(f"[bold green]✓ Successfully connected to Garmin Connect![/bold green] User: [cyan]{name}[/cyan]")
    except GarminAuthError as exc:
        console.print(f"[bold red]Authentication Error:[/bold red] {exc}")
        sys.exit(1)
    except Exception as exc:
        console.print(f"[bold red]Connection Failed:[/bold red] {exc}")
        sys.exit(1)


@app.command("activities")
def list_activities(
    limit: int = typer.Option(20, "--limit", "-n", help="Number of activities to fetch"),
    days: Optional[int] = typer.Option(None, "--days", "-d", help="Filter by last N days"),
    activity_type: Optional[str] = typer.Option(None, "--type", "-t", help="Activity type (e.g. running, cycling)"),
) -> None:
    """List recent Garmin activities with pace, distance, and training effect."""
    start_date = (date.today() - timedelta(days=days)).isoformat() if days else None
    try:
        raw_list = default_client.search_activities(limit=limit, start_date=start_date, activity_type=activity_type)
        activities = [Activity.from_api_dict(a) for a in raw_list]
        if not activities:
            console.print("[yellow]No activities found matching criteria.[/yellow]")
            return
        print_activities_table(activities, title=f"Garmin Activities (Last {len(activities)})")
    except Exception as exc:
        console.print(f"[bold red]Error fetching activities:[/bold red] {exc}")
        sys.exit(1)


@app.command("activity")
def get_activity_detail(
    activity_id: int = typer.Argument(..., help="Garmin activity ID"),
) -> None:
    """View full performance details, splits, and cardiac drift for an activity."""
    try:
        raw = default_client.get_activity(activity_id)
        act = Activity.from_api_dict(raw)
        print_activity_detail(act)
    except Exception as exc:
        console.print(f"[bold red]Error fetching activity {activity_id}:[/bold red] {exc}")
        sys.exit(1)


@app.command("volume")
def volume_summary(
    weeks: int = typer.Option(12, "--weeks", "-w", help="Number of weeks to analyze"),
    freq: str = typer.Option("W", "--freq", "-f", help="'W' for weekly, 'M' for monthly"),
) -> None:
    """Analyze weekly or monthly training volume, distance, time, and elevation."""
    start_date = (date.today() - timedelta(weeks=weeks)).isoformat()
    try:
        raw_list = default_client.search_activities(start_date=start_date, limit=200)
        activities = [Activity.from_api_dict(a) for a in raw_list]
        periods = aggregate_volume(activities, freq=freq)
        print_volume_summary(periods, title=f"Training Volume (Last {weeks} Weeks)")
    except Exception as exc:
        console.print(f"[bold red]Error calculating volume:[/bold red] {exc}")
        sys.exit(1)


@app.command("fitness")
def fitness_trends(
    days: int = typer.Option(60, "--days", "-d", help="Number of days to show fitness timeline"),
) -> None:
    """Calculate Fitness (CTL), Fatigue (ATL), and Form (TSB) training load curves."""
    start_date = (date.today() - timedelta(days=days + 60)).isoformat()
    try:
        raw_list = default_client.search_activities(start_date=start_date, limit=300)
        activities = [Activity.from_api_dict(a) for a in raw_list]
        timeline = calculate_fitness_fatigue(activities, days=days)
        print_fitness_timeline(timeline)
    except Exception as exc:
        console.print(f"[bold red]Error modeling fitness/fatigue:[/bold red] {exc}")
        sys.exit(1)


@app.command("compare")
def compare(
    activity_a_id: int = typer.Argument(..., help="ID of first activity"),
    activity_b_id: int = typer.Argument(..., help="ID of second activity"),
) -> None:
    """Compare two activities side-by-side (pace, HR efficiency, splits, decoupling)."""
    try:
        raw_a = default_client.get_activity(activity_a_id)
        raw_b = default_client.get_activity(activity_b_id)
        act_a = Activity.from_api_dict(raw_a)
        act_b = Activity.from_api_dict(raw_b)
        metrics = compare_activities(act_a, act_b)
        print_comparison(act_a, act_b, metrics)
    except Exception as exc:
        console.print(f"[bold red]Error comparing activities:[/bold red] {exc}")
        sys.exit(1)


@app.command("best-efforts")
def best_efforts(
    limit: int = typer.Option(100, "--limit", "-n", help="Number of recent activities to scan"),
) -> None:
    """Find historical personal bests (fastest 5k, 10k, longest distance, max elevation)."""
    try:
        raw_list = default_client.search_activities(limit=limit)
        activities = [Activity.from_api_dict(a) for a in raw_list]
        bests = find_best_efforts(activities)
        if not bests:
            console.print("[yellow]No qualifying benchmark activities found.[/yellow]")
            return
        table = typer.rich_utils.rich_format_error if False else None  # dummy
        from rich.table import Table
        t = Table(title="Personal Bests & Landmark Efforts", header_style="bold yellow")
        t.add_column("Category", style="bold")
        t.add_column("Value", style="cyan")
        t.add_column("Activity Name")
        t.add_column("Date", style="dim")
        t.add_column("ID", style="dim")
        for b in bests:
            t.add_row(b.category, b.value_display, b.activity_name, b.date_str, str(b.activity_id))
        console.print(t)
    except Exception as exc:
        console.print(f"[bold red]Error finding best efforts:[/bold red] {exc}")
        sys.exit(1)


@app.command("readiness")
def recovery_readiness() -> None:
    """Evaluate today's recovery and training readiness (Sleep + HRV + Body Battery)."""
    today_str = date.today().isoformat()
    yesterday_str = (date.today() - timedelta(days=1)).isoformat()
    try:
        sleep_records = default_client.get_sleep_summaries(yesterday_str, today_str)
        sleep_data = sleep_records[-1] if sleep_records else None

        hrv_records = default_client.get_hrv_summaries(yesterday_str, today_str)
        hrv_data = hrv_records[-1] if hrv_records else None

        bb_records = default_client.get_body_battery(yesterday_str, today_str)
        bb_data = bb_records[-1] if bb_records else None

        daily_records = default_client.get_daily_summaries(today_str, today_str)
        daily_data = daily_records[0] if daily_records else None

        status = evaluate_daily_readiness(
            sleep_data=sleep_data,
            hrv_data=hrv_data,
            body_battery_data=bb_data,
            daily_summary=daily_data,
        )
        print_readiness(status)
    except Exception as exc:
        console.print(f"[bold red]Error checking readiness:[/bold red] {exc}")
        sys.exit(1)


@app.command("vo2max")
def get_vo2max_history(
    limit: int = typer.Option(10, "--limit", "-n", help="Number of recent activities to scan"),
) -> None:
    """Extract precise VO2 max calculation from Garmin device FIT sensor streams."""
    from rich.table import Table
    try:
        raw_list = default_client.search_activities(limit=limit, activity_type="running")
        if not raw_list:
            console.print("[yellow]No running activities found.[/yellow]")
            return

        table = Table(title="Garmin VO2 Max Progression (Sub-integer Precision)", header_style="bold cyan")
        table.add_column("Date", style="bold")
        table.add_column("Activity Name", style="white")
        table.add_column("VO2 Max (Exact)", justify="right", style="bold green")
        table.add_column("Garmin Connect Display", justify="right", style="cyan")
        table.add_column("Pace", justify="right", style="magenta")
        table.add_column("Avg HR", justify="right", style="red")

        for act in raw_list:
            act_id = act.get("activityId")
            date_str = act.get("startTimeLocal", "")[:10]
            name = act.get("activityName", "Run")
            speed = float(act.get("averageSpeed", 0.0))
            hr = act.get("averageHR") or act.get("averageHeartRate")
            pace_str = "--:--"
            if speed > 0.1:
                p_sec = int(1000.0 / speed)
                pace_str = f"{p_sec // 60:02d}:{p_sec % 60:02d} /km"

            try:
                zip_path = default_client.download_activity(act_id)
                fit_data = extract_and_parse_fit(zip_path)
                if fit_data.vo2_max_exact:
                    table.add_row(
                        date_str,
                        name,
                        f"{fit_data.vo2_max_exact:.2f} mL/kg/min",
                        f"{round(fit_data.vo2_max_exact)}",
                        pace_str,
                        f"{hr:.0f} bpm" if hr else "--",
                    )
            except Exception:
                pass

        console.print(table)
    except Exception as exc:
        console.print(f"[bold red]Error querying VO2 max:[/bold red] {exc}")
        sys.exit(1)


@app.command("download")
def download_activity(
    activity_id: int = typer.Argument(..., help="Activity ID to download"),
    output_dir: str = typer.Option("./downloads", "--output-dir", "-o", help="Directory to save original .fit file"),
) -> None:
    """Download raw original .FIT file and parse its second-by-second telemetry streams."""
    try:
        zip_path = default_client.download_activity(activity_id, output_dir=output_dir)
        console.print(f"[bold green]✓ Downloaded archive to:[/bold green] {zip_path}")
        fit_data = extract_and_parse_fit(zip_path)
        summary = fit_data.get_summary()
        console.print(f"[cyan]FIT File Parsed Successfully:[/cyan] {summary}")
    except Exception as exc:
        console.print(f"[bold red]Error downloading/parsing FIT file:[/bold red] {exc}")
        sys.exit(1)


@app.command("dashboard")
def export_dashboard(
    weeks: int = typer.Option(12, "--weeks", "-w", help="Number of weeks to visualize"),
    output: str = typer.Option("./garmin_dashboard.html", "--output", "-o", help="Output HTML path"),
) -> None:
    """Generate an interactive HTML performance dashboard with volume and fitness charts."""
    start_date = (date.today() - timedelta(weeks=weeks)).isoformat()
    try:
        raw_list = default_client.search_activities(start_date=start_date, limit=150)
        activities = [Activity.from_api_dict(a) for a in raw_list]
        volume = aggregate_volume(activities, freq="W")

        # Query all-time history for authentic career PRs, VDOT benchmark, and CTL model warm-up
        try:
            all_history_raw = default_client.search_activities(start_date="2023-01-01", limit=150)
            all_history_acts = [Activity.from_api_dict(a) for a in all_history_raw]
            best_efforts = find_best_efforts(all_history_acts)
            fitness = calculate_fitness_fatigue(all_history_acts, days=weeks * 7)
            vdot_info = find_best_empirical_vdot(all_history_acts)
        except Exception:
            best_efforts = find_best_efforts(activities)
            fitness = calculate_fitness_fatigue(activities, days=weeks * 7)
            vdot_info = find_best_empirical_vdot(activities)

        saved_path = generate_html_dashboard(
            activities,
            volume,
            fitness,
            output_path=output,
            best_efforts=best_efforts,
            vdot_info=vdot_info,
            weeks_scope=weeks,
        )
        console.print(f"[bold green]✓ Dashboard generated at:[/bold green] [cyan]{saved_path.resolve()}[/cyan]")
    except Exception as exc:
        console.print(f"[bold red]Error generating dashboard:[/bold red] {exc}")
        sys.exit(1)


@app.command("acwr")
def get_acwr() -> None:
    """Calculate Acute-to-Chronic Workload Ratio (Gabbett 2016 injury prevention model)."""
    try:
        raw_list = default_client.search_activities(start_date="2023-01-01", limit=150)
        acts = [Activity.from_api_dict(a) for a in raw_list]
        timeline = calculate_fitness_fatigue(acts, days=60)
        rec = calculate_acwr(timeline)

        from rich.table import Table
        t = Table(title="Acute-to-Chronic Workload Ratio (ACWR - Gabbett Model)", show_header=True)
        t.add_column("Metric", style="bold cyan")
        t.add_column("Value", style="bold")
        t.add_column("Reference / Range", style="dim")

        color = "green" if rec.acwr <= 1.30 and rec.acwr >= 0.80 else ("yellow" if rec.acwr <= 1.50 else "red")
        t.add_row("Acute Workload (Last 7 Days)", f"{rec.acute_load:.1f}", "Short-term cumulative training fatigue")
        t.add_row("Chronic Workload (28d Weekly Base)", f"{rec.chronic_load:.1f}", "Rolling baseline fitness conditioning")
        t.add_row("ACWR Ratio", f"[{color}]{rec.acwr:.2f}[/{color}]", "Sweet Spot: 0.80 - 1.30 | Danger: > 1.50")
        t.add_row("Status Zone", f"[{color}]{rec.status}[/{color}]", rec.recommendation)

        console.print(t)
    except Exception as exc:
        console.print(f"[bold red]Error calculating ACWR:[/bold red] {exc}")
        sys.exit(1)


@app.command("vdot")
def get_vdot(
    distance_m: Optional[float] = typer.Option(None, "--distance", "-d", help="Distance in meters (e.g. 5000)"),
    time_str: Optional[str] = typer.Option(None, "--time", "-t", help="Time MM:SS or HH:MM:SS (e.g. 24:41)"),
) -> None:
    """Calculate Jack Daniels VDOT, race equivalents, and prescribed training paces."""
    try:
        from rich.table import Table

        if distance_m and time_str:
            parts = [float(p) for p in time_str.split(":")]
            if len(parts) == 2:
                tot_sec = parts[0] * 60 + parts[1]
            elif len(parts) == 3:
                tot_sec = parts[0] * 3600 + parts[1] * 60 + parts[2]
            else:
                tot_sec = float(parts[0])

            vdot_val = calculate_vdot(distance_m, tot_sec)
            vdot_data = {
                "vdot": vdot_val,
                "benchmark_activity_name": "Manual Input",
                "benchmark_date": "N/A",
                "benchmark_distance": f"{distance_m:.0f} m",
                "benchmark_time": time_str,
                "benchmark_pace": "N/A",
                "training_paces": get_vdot_training_paces(vdot_val),
                "race_predictions": get_vdot_race_predictions(vdot_val),
            }
        else:
            raw_list = default_client.search_activities(start_date="2023-01-01", limit=150)
            acts = [Activity.from_api_dict(a) for a in raw_list]
            vdot_data = find_best_empirical_vdot(acts)

        if not vdot_data:
            console.print("[bold yellow]No empirical benchmark race efforts found.[/bold yellow]")
            console.print("VDOT requires an authentic maximal race or time-trial (5K, 10K, Half, or Marathon).")
            console.print("You can also calculate VDOT manually: [cyan]uv run ricelo vdot -d 5000 -t 24:41[/cyan]")
            return

        v = vdot_data["vdot"]
        console.print(f"\n[bold green]Jack Daniels VDOT Engine: [bold cyan]{v:.2f}[/bold cyan][/bold green]")
        console.print(f"Benchmark Source: [bold]{vdot_data['benchmark_activity_name']}[/bold] ({vdot_data['benchmark_distance']} in {vdot_data['benchmark_time']}) • {vdot_data['benchmark_date']}\n")

        # Table 1: Daniels Training Paces
        t_paces = Table(title="Jack Daniels Prescribed Training Paces", show_header=True)
        t_paces.add_column("Pace Zone", style="bold cyan")
        t_paces.add_column("Target Pace", style="bold green")
        t_paces.add_column("Physiological Purpose", style="dim")

        paces = vdot_data["training_paces"]
        t_paces.add_row("Easy (E-Pace)", paces.get("easy_pace", "N/A"), "Aerobic base building, active recovery, long runs")
        t_paces.add_row("Marathon (M-Pace)", paces.get("marathon_pace", "N/A"), "Aerobic threshold & marathon goal pace")
        t_paces.add_row("Threshold (T-Pace)", paces.get("threshold_pace", "N/A"), "Lactate clearance (1-hour sustainable tempo)")
        t_paces.add_row("Interval (I-Pace)", paces.get("interval_pace", "N/A"), "VO2 Max stimulation (800m - 1200m repeats)")
        t_paces.add_row("Repetition (R-Pace)", paces.get("repetition_pace", "N/A"), "Neuromuscular speed & mechanics (200m - 400m strides)")
        console.print(t_paces)

        # Table 2: Equivalent Race Predictions
        t_race = Table(title=f"Equivalent Race Predictions (at VDOT {v:.1f})", show_header=True)
        t_race.add_column("Distance", style="bold cyan")
        t_race.add_column("Predicted Time", style="bold green")

        preds = vdot_data["race_predictions"]
        t_race.add_row("5K", preds.get("5k", "N/A"))
        t_race.add_row("10K", preds.get("10k", "N/A"))
        t_race.add_row("Half Marathon", preds.get("half_marathon", "N/A"))
        t_race.add_row("Marathon", preds.get("marathon", "N/A"))
        console.print(t_race)
        console.print()

    except Exception as exc:
        console.print(f"[bold red]Error calculating VDOT:[/bold red] {exc}")
        sys.exit(1)


if __name__ == "__main__":
    app()
