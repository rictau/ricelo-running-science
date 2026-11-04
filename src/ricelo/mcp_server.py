"""FastMCP server exposing precision running performance and activity intelligence for ricelo running science."""

from __future__ import annotations

import os
import sys
from datetime import timedelta
from typing import Any, Optional
from urllib.parse import urlparse

from mcp.server.auth.settings import (
    AuthSettings,
    ClientRegistrationOptions,
    RevocationOptions,
)
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings

from .client import default_client
from .fit import fetch_activity_with_laps, measured_best_efforts
from .models import Activity, format_seconds
from .performance import (
    aggregate_volume,
    calculate_acwr,
    calculate_fitness_fatigue,
    calculate_vdot,
    compare_activities,
    find_best_empirical_vdot,
    get_vdot_race_predictions,
    get_vdot_training_paces,
)
from .recovery import evaluate_daily_readiness, latest_body_battery_record

SERVER_NAME = "ricelo-running-science"
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
_TOOLS: list[Any] = []


def tool(fn):
    """Mark a function as an MCP tool; build_server registers every marked tool."""
    _TOOLS.append(fn)
    return fn


def build_server(env: dict[str, str] | None = None) -> FastMCP:
    """
    Build the FastMCP server from environment settings.

    Default (RICELO_TRANSPORT unset or "stdio"): a local server the AI app
    starts itself; nothing here applies. With RICELO_TRANSPORT=http it serves
    streamable HTTP at /mcp for remote clients (claude.ai, Claude mobile):

      RICELO_PUBLIC_URL      public base URL, e.g. https://mcp.ricelo.com
      RICELO_AUTH_SECRET     random string (32+ chars) that signs OAuth tokens
      RICELO_OWNER_PASSWORD  password you type to approve a new connection
      RICELO_HOST / PORT     bind address (default 127.0.0.1 / 8000)

    Without the three auth settings the HTTP server only binds to loopback,
    for local testing.
    """
    server = _build_bare_server(dict(os.environ if env is None else env))
    for fn in _TOOLS:
        server.add_tool(fn)
    return server


def _build_bare_server(env: dict[str, str]) -> FastMCP:
    if env.get("RICELO_TRANSPORT", "stdio") != "http":
        return FastMCP(SERVER_NAME)

    host = env.get("RICELO_HOST", "127.0.0.1")
    port = int(env.get("PORT", "8000"))
    public_url = env.get("RICELO_PUBLIC_URL", "").rstrip("/")
    secret = env.get("RICELO_AUTH_SECRET", "")
    password = env.get("RICELO_OWNER_PASSWORD", "")
    common: dict[str, Any] = {"host": host, "port": port, "stateless_http": True, "json_response": True}

    if not (public_url or secret or password):
        if host not in LOOPBACK_HOSTS:
            raise SystemExit(
                "Refusing to serve without authentication on a non-loopback address. "
                "Set RICELO_PUBLIC_URL, RICELO_AUTH_SECRET and RICELO_OWNER_PASSWORD."
            )
        return FastMCP(SERVER_NAME, **common)

    missing = [n for n, v in (("RICELO_PUBLIC_URL", public_url), ("RICELO_AUTH_SECRET", secret),
                              ("RICELO_OWNER_PASSWORD", password)) if not v]
    if missing:
        raise SystemExit(f"Remote mode needs {', '.join(missing)}.")
    parsed = urlparse(public_url)
    if parsed.scheme != "https" and parsed.hostname not in LOOPBACK_HOSTS:
        raise SystemExit("RICELO_PUBLIC_URL must use https://")

    from .remote_auth import LOGIN_PATH, SCOPE, OwnerOAuthProvider

    redirect_hosts = tuple(h.strip() for h in env.get("RICELO_REDIRECT_HOSTS", "").split(",") if h.strip())
    provider = OwnerOAuthProvider(
        secret=secret,
        owner_password=password,
        public_url=public_url,
        **({"redirect_hosts": redirect_hosts} if redirect_hosts else {}),
    )
    netloc = parsed.netloc
    server = FastMCP(
        SERVER_NAME,
        auth_server_provider=provider,
        auth=AuthSettings(
            issuer_url=public_url,
            resource_server_url=f"{public_url}/mcp",
            validate_token_resource=True,
            required_scopes=[SCOPE],
            client_registration_options=ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            revocation_options=RevocationOptions(enabled=False),
        ),
        # Only answer requests addressed to the public host (DNS-rebinding guard).
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[netloc, f"{netloc}:*"],
            allowed_origins=[f"{parsed.scheme}://{netloc}", "https://claude.ai", "https://claude.com"],
        ),
        **common,
    )
    server.custom_route(LOGIN_PATH, methods=["GET", "POST"])(provider.login_page)
    return server



@tool
def list_activities(limit: int = 20, activity_type: Optional[str] = None) -> list[dict[str, Any]]:
    """
    List recent activities, newest first, with their Garmin activity IDs
    (needed by analyze_run and compare_runs). activity_type filters by
    Garmin type key, e.g. "running", "trail_running", "cycling".
    """
    raw_list = default_client.search_all_activities(activity_type=activity_type, max_activities=limit)
    return [
        {
            "activity_id": a.activity_id,
            "name": a.name,
            "type": a.activity_type,
            "date": a.start_time_local,
            "distance_km": a.distance_km,
            "duration": a.duration_str,
            "pace_min_km": a.pace_min_km,
            "avg_hr": a.avg_hr,
        }
        for a in (Activity.from_api_dict(r) for r in raw_list)
    ]


@tool
def analyze_run(activity_id: int) -> dict[str, Any]:
    """
    Perform deep running performance analysis on a specific activity.
    Returns distance, moving time, pace (MM:SS /km), HR, elevation, Training Effect,
    the watch's lap breakdown, and Aerobic Decoupling (Cardiac Drift %).
    Use list_activities to find activity IDs.
    """
    act = fetch_activity_with_laps(default_client, activity_id)

    splits_data = [
        {
            "split": s.split_index,
            "distance_km": s.distance_km,
            "duration": s.duration_str,
            "pace": s.pace_min_km,
            "avg_hr": s.avg_hr,
            "cadence": s.avg_cadence,
            "elevation_gain_m": s.elevation_gain_m,
        }
        for s in act.splits
    ]

    return {
        "activity_id": act.activity_id,
        "name": act.name,
        "type": act.activity_type,
        "date": act.start_time_local,
        "distance_km": act.distance_km,
        "distance_miles": act.distance_miles,
        "moving_duration": format_seconds(act.moving_duration_s) if act.moving_duration_s else None,
        "total_duration": act.duration_str,
        "pace_min_km": act.pace_min_km,
        "speed_kmh": act.speed_kmh,
        "avg_hr": act.avg_hr,
        "max_hr": act.max_hr,
        "avg_cadence": act.avg_cadence,
        "elevation_gain_m": act.elevation_gain_m,
        "elevation_loss_m": act.elevation_loss_m,
        "aerobic_training_effect": act.aerobic_training_effect,
        "anaerobic_training_effect": act.anaerobic_training_effect,
        "aerobic_decoupling_pct": act.aerobic_decoupling_pct,
        "cardiac_drift_assessment": (
            "Aerobically stable (<5% drift)"
            if act.aerobic_decoupling_pct is not None and act.aerobic_decoupling_pct < 5.0
            else ("Moderate cardiac drift (5-8%)" if act.aerobic_decoupling_pct is not None and act.aerobic_decoupling_pct <= 8.0
                  else ("High cardiac drift (>8%) - aerobic fatigue or dehydration" if act.aerobic_decoupling_pct is not None else "Not enough data (needs 20+ minutes of moving heart-rate data)"))
        ),
        "splits": splits_data,
    }


@tool
def compare_runs(activity_id_a: int, activity_id_b: int) -> dict[str, Any]:
    """
    Compare two running sessions head-to-head (e.g. recent 10k run vs previous 10k run).
    Compares pace deltas (s/km), heart rate, efficiency factor (Speed/HR), elevation, and decoupling.
    """
    act_a = fetch_activity_with_laps(default_client, activity_id_a)
    act_b = fetch_activity_with_laps(default_client, activity_id_b)

    metrics = compare_activities(act_a, act_b)

    return {
        "activity_a": {"id": act_a.activity_id, "name": act_a.name, "date": act_a.start_time_local},
        "activity_b": {"id": act_b.activity_id, "name": act_b.name, "date": act_b.start_time_local},
        "comparisons": [
            {
                "metric": m.metric_name,
                "activity_a": m.value_a,
                "activity_b": m.value_b,
                "delta": m.delta_str,
                "verdict": m.verdict,
            }
            for m in metrics
        ],
    }


@tool
def get_training_volume(weeks: int = 12, frequency: str = "W") -> list[dict[str, Any]]:
    """
    Calculate aggregated running and endurance volume by week or month.
    Returns total distance (km), duration (hours), elevation gain (m), calories,
    and sport distribution.
    """
    start_date = (default_client.today() - timedelta(weeks=weeks)).isoformat()
    raw_list = default_client.search_all_activities(start_date=start_date)
    activities = [Activity.from_api_dict(a) for a in raw_list]
    periods = aggregate_volume(activities, freq=frequency)

    return [
        {
            "period": p.period_label,
            "start_date": p.start_date,
            "end_date": p.end_date,
            "total_activities": p.total_activities,
            "total_distance_km": p.total_distance_km,
            "total_duration_hours": p.total_duration_hours,
            "total_elevation_m": p.total_elevation_m,
            "total_calories": p.total_calories,
            "sports": p.by_sport,
        }
        for p in periods
    ]


@tool
def get_fitness_and_freshness(days: int = 60) -> dict[str, Any]:
    """
    Model running training load progression using the Banister impulse-response model:
    - Chronic Training Load (CTL / Fitness: 42-day rolling base)
    - Acute Training Load (ATL / Fatigue: 7-day rolling stress)
    - Training Stress Balance (TSB / Form = CTL - ATL)
    Returns recent daily timeline and current athlete physiological training status.
    """
    # Fetch past the model's 126-day warm-up (see calculate_fitness_fatigue)
    today = default_client.today()
    start_date = (today - timedelta(days=days + 150)).isoformat()
    raw_list = default_client.search_all_activities(start_date=start_date)
    activities = [Activity.from_api_dict(a) for a in raw_list]
    timeline = calculate_fitness_fatigue(activities, days=days, end_date=today)

    current = timeline[-1] if timeline else None

    return {
        "current_status": {
            "date": current.date_str if current else "",
            "fitness_ctl": current.ctl_fitness if current else 0.0,
            "fatigue_atl": current.atl_fatigue if current else 0.0,
            "form_tsb": current.tsb_form if current else 0.0,
            "status_verdict": current.status if current else "No data",
        },
        "timeline": [
            {
                "date": r.date_str,
                "daily_load": r.daily_load,
                "ctl_fitness": r.ctl_fitness,
                "atl_fatigue": r.atl_fatigue,
                "tsb_form": r.tsb_form,
                "status": r.status,
            }
            for r in timeline[-14:]  # Last 14 days summary in response
        ],
    }


@tool
def get_personal_records() -> list[dict[str, Any]]:
    """
    Find athlete's career running personal records and landmark efforts across historical activities
    (Marathon 42.2K, Fastest Half Marathon, Fastest 10K, Fastest 5K, Longest Run, Max Elevation Gain).
    Race-distance times are the fastest continuous stretch measured from FIT files,
    checking the 3 runs with the fastest average pace for each distance.
    """
    raw_list = default_client.search_all_activities()
    activities = [Activity.from_api_dict(a) for a in raw_list]
    bests = measured_best_efforts(default_client, activities)

    return [
        {
            "category": b.category,
            "value": b.value_display,
            "activity_id": b.activity_id,
            "activity_name": b.activity_name,
            "date": b.date_str,
        }
        for b in bests
    ]


@tool
def get_readiness_assessment() -> dict[str, Any]:
    """
    Synthesize Garmin recovery biometrics (last night's sleep score, sleep stages,
    overnight HRV average & status, Body Battery, and resting HR) into an actionable daily readiness report.
    """
    today = default_client.today()
    today_str = today.isoformat()
    yesterday_str = (today - timedelta(days=1)).isoformat()

    sleep_records = default_client.get_sleep_summaries(yesterday_str, today_str)
    sleep_data = sleep_records[-1] if sleep_records else None

    hrv_records = default_client.get_hrv_summaries(yesterday_str, today_str)
    hrv_data = hrv_records[-1] if hrv_records else None

    bb_records = default_client.get_body_battery(yesterday_str, today_str)
    bb_data = latest_body_battery_record(bb_records)

    daily_records = default_client.get_daily_summaries(today_str, today_str)
    daily_data = daily_records[0] if daily_records else None

    status = evaluate_daily_readiness(
        sleep_data=sleep_data,
        hrv_data=hrv_data,
        body_battery_data=bb_data,
        daily_summary=daily_data,
        today=today,
    )

    return {
        "date": status.date_str,
        "sleep": {
            "score": status.sleep_score,
            "total_hours": status.sleep_duration_hours,
            "deep_sleep_hours": status.deep_sleep_hours,
            "rem_sleep_hours": status.rem_sleep_hours,
        },
        "hrv": {
            "last_night_avg_ms": status.hrv_last_night_avg_ms,
            "weekly_avg_ms": status.hrv_weekly_avg_ms,
            "status": status.hrv_status,
        },
        "body_battery": {
            "highest": status.body_battery_max,
            "charged": status.body_battery_charged,
        },
        "heart_rate": {
            "resting_bpm": status.resting_hr_bpm,
            "daily_stress_score": status.avg_stress_score,
        },
        "readiness_verdict": status.readiness_verdict,
        "recommendation": status.training_recommendation,
    }


@tool
def get_acwr_injury_risk() -> dict[str, Any]:
    """
    Calculate Acute-to-Chronic Workload Ratio (Gabbett 2016 sports science model)
    to assess soft-tissue injury risk and workload progression rate.
    Returns acute load (last 7 days), chronic load (28-day baseline), ratio, zone verdict, and recommendation.
    """
    # Fetch past the fitness model's 126-day warm-up (see calculate_fitness_fatigue)
    today = default_client.today()
    start_date = (today - timedelta(days=60 + 150)).isoformat()
    raw_list = default_client.search_all_activities(start_date=start_date)
    acts = [Activity.from_api_dict(a) for a in raw_list]
    timeline = calculate_fitness_fatigue(acts, days=60, end_date=today)
    rec = calculate_acwr(timeline)

    return {
        "acute_load_7d": rec.acute_load,
        "chronic_load_28d_weekly_avg": rec.chronic_load,
        "acwr_ratio": rec.acwr,
        "status_zone": rec.status,
        "status_class": rec.status_class,
        "sweet_spot_range": "0.80 - 1.30",
        "danger_zone_threshold": "> 1.50",
        "recommendation": rec.recommendation,
    }


@tool
def get_vdot_paces(distance_m: Optional[float] = None, duration_s: Optional[float] = None) -> dict[str, Any]:
    """
    Calculate Jack Daniels VDOT, equivalent race predictions, and prescribed training paces
    (Easy E-Pace, Marathon M-Pace, Threshold T-Pace, Interval I-Pace, Repetition R-Pace).
    If distance and duration are omitted, uses the best race-distance effort of the
    last 365 days, preferring runs tagged as races in Garmin Connect.
    Does NOT use uncalibrated sub-maximal jogs or fudge factors.
    """
    if distance_m and duration_s:
        v = calculate_vdot(distance_m, duration_s)
        return {
            "vdot": v,
            "source": f"Manual Input ({distance_m:.0f}m in {duration_s:.0f}s)",
            "benchmark_date": "N/A",
            "training_paces": get_vdot_training_paces(v),
            "race_predictions": get_vdot_race_predictions(v),
        }

    raw_list = default_client.search_all_activities()
    acts = [Activity.from_api_dict(a) for a in raw_list]
    vdot_data = find_best_empirical_vdot(acts, today=default_client.today())

    if not vdot_data:
        return {
            "vdot": None,
            "message": "No empirical benchmark race efforts (5K, 10K, Half, or Marathon) found in history. VDOT requires an authentic maximal effort.",
            "recommendation": "Provide distance_m and duration_s to calculate VDOT from a specific time-trial.",
        }

    result = {
        "vdot": vdot_data["vdot"],
        "benchmark_activity_name": vdot_data["benchmark_activity_name"],
        "benchmark_activity_id": vdot_data["benchmark_activity_id"],
        "benchmark_date": vdot_data["benchmark_date"],
        "benchmark_distance": vdot_data["benchmark_distance"],
        "benchmark_time": vdot_data["benchmark_time"],
        "benchmark_pace": vdot_data["benchmark_pace"],
        "benchmark_is_race": vdot_data["benchmark_is_race"],
        "lookback_days": vdot_data["lookback_days"],
        "training_paces": vdot_data["training_paces"],
        "race_predictions": vdot_data["race_predictions"],
    }
    if "note" in vdot_data:
        result["note"] = vdot_data["note"]
    return result


mcp = build_server()


def main():
    if mcp.settings.stateless_http:  # built for RICELO_TRANSPORT=http
        print(f"ricelo MCP over HTTP on {mcp.settings.host}:{mcp.settings.port}/mcp", file=sys.stderr)
        mcp.run(transport="streamable-http")
    else:
        mcp.run()


if __name__ == "__main__":
    main()
