"""Unit tests for Garmin performance analysis and models."""

from ricelo.models import Activity, Split
from ricelo.performance import (
    aggregate_volume,
    calculate_activity_load,
    calculate_fitness_fatigue,
    compare_activities,
    find_best_efforts,
)
from ricelo.recovery import evaluate_daily_readiness


def test_activity_model_and_decoupling():
    splits = [
        # First half: fast and low HR
        Split(split_index=1, distance_m=1000, duration_s=300, avg_speed_mps=3.33, avg_hr=140),
        Split(split_index=2, distance_m=1000, duration_s=300, avg_speed_mps=3.33, avg_hr=142),
        # Second half: same speed but HR drifted up (cardiac drift)
        Split(split_index=3, distance_m=1000, duration_s=300, avg_speed_mps=3.33, avg_hr=155),
        Split(split_index=4, distance_m=1000, duration_s=300, avg_speed_mps=3.33, avg_hr=158),
    ]

    act = Activity(
        activity_id=12345,
        name="Morning Run",
        activity_type="running",
        start_time_local="2026-10-20T07:00:00",
        distance_m=4000,
        duration_s=1200,
        elapsed_duration_s=1200,
        avg_speed_mps=3.33,
        avg_hr=149,
        splits=splits,
    )

    assert act.distance_km == 4.0
    assert act.duration_str == "20:00"
    assert "05:00" in act.pace_min_km
    # Cardiac drift should be positive (>5%)
    assert act.aerobic_decoupling_pct is not None
    assert act.aerobic_decoupling_pct > 5.0


def test_volume_aggregation():
    acts = [
        Activity(
            activity_id=1,
            name="Run 1",
            activity_type="running",
            start_time_local="2026-10-12T07:00:00",
            distance_m=5000,
            duration_s=1500,
            elapsed_duration_s=1500,
            avg_speed_mps=3.33,
        ),
        Activity(
            activity_id=2,
            name="Run 2",
            activity_type="running",
            start_time_local="2026-10-14T07:00:00",
            distance_m=10000,
            duration_s=3000,
            elapsed_duration_s=3000,
            avg_speed_mps=3.33,
        ),
        Activity(
            activity_id=3,
            name="Ride 1",
            activity_type="cycling",
            start_time_local="2026-10-19T07:00:00",  # Next week
            distance_m=30000,
            duration_s=3600,
            elapsed_duration_s=3600,
            avg_speed_mps=8.33,
        ),
    ]

    periods = aggregate_volume(acts, freq="W")
    assert len(periods) == 2
    assert periods[0].total_activities == 2
    assert periods[0].total_distance_km == 15.0
    assert periods[1].total_activities == 1
    assert periods[1].total_distance_km == 30.0


def test_fitness_fatigue_tsb():
    acts = [
        Activity(
            activity_id=i,
            name=f"Run {i}",
            activity_type="running",
            start_time_local=f"2026-10-{10+i:02d}T07:00:00",
            distance_m=10000,
            duration_s=3000,
            elapsed_duration_s=3000,
            avg_speed_mps=3.33,
            aerobic_training_effect=3.5,
        )
        for i in range(5)
    ]

    timeline = calculate_fitness_fatigue(acts, days=14)
    assert len(timeline) == 14
    latest = timeline[-1]
    assert latest.ctl_fitness >= 0.0
    assert latest.atl_fatigue >= 0.0
    assert round(latest.tsb_form, 1) == round(latest.ctl_fitness - latest.atl_fatigue, 1)

    # Validate activity load calculation & best efforts
    load = calculate_activity_load(acts[0])
    assert load > 0.0
    efforts = find_best_efforts(acts)
    assert any("10K" in e.category or "Longest" in e.category for e in efforts)


def test_fitness_decay_uses_banister_time_constants():
    # Regression test for the pre-2026-10-27 decay factors: the generic
    # N-day EMA alpha = 2 / (N + 1) decayed CTL with a ~14-day half-life,
    # about twice as fast as the Banister 42-day time constant (half-life
    # ~29 days). Train daily, then stop, and check the decay rates.
    from datetime import date, timedelta

    start = date(2026, 1, 1)
    train_days = 120
    rest_days = 29
    acts = [
        Activity(
            activity_id=i,
            name=f"Daily Run {i}",
            activity_type="running",
            start_time_local=f"{start + timedelta(days=i)}T07:00:00",
            distance_m=10000,
            duration_s=3000,
            elapsed_duration_s=3000,
            avg_speed_mps=3.33,
            aerobic_training_effect=3.5,
        )
        for i in range(train_days)
    ]
    end = start + timedelta(days=train_days + rest_days - 1)
    timeline = calculate_fitness_fatigue(acts, days=train_days + rest_days, end_date=end)
    assert len(timeline) == train_days + rest_days

    last_train = timeline[train_days - 1]
    after_rest = timeline[-1]
    ctl_ratio = after_rest.ctl_fitness / last_train.ctl_fitness
    # exp(-29 / 42) ~= 0.50; the old alpha gave ~0.25
    assert 0.42 < ctl_ratio < 0.58
    # ATL (7-day time constant) is almost fully gone after 29 rest days
    assert after_rest.atl_fatigue < 0.05 * last_train.atl_fatigue
    # Every record is internally consistent: TSB == CTL - ATL as stored
    for r in timeline:
        assert round(r.tsb_form, 1) == round(r.ctl_fitness - r.atl_fatigue, 1)


def test_compare_activities():
    act_a = Activity(
        activity_id=101,
        name="Baseline 5k",
        activity_type="running",
        start_time_local="2026-09-01T07:00:00",
        distance_m=5000,
        duration_s=1500,
        elapsed_duration_s=1500,
        avg_speed_mps=3.33,  # 5:00 /km
        avg_hr=150,
    )
    act_b = Activity(
        activity_id=102,
        name="Recent 5k",
        activity_type="running",
        start_time_local="2026-10-01T07:00:00",
        distance_m=5000,
        duration_s=1350,
        elapsed_duration_s=1350,
        avg_speed_mps=3.70,  # 4:30 /km (faster)
        avg_hr=148,          # lower HR!
    )

    metrics = compare_activities(act_a, act_b)
    metric_map = {m.metric_name: m for m in metrics}

    assert "Average Pace" in metric_map
    assert "B was faster" in metric_map["Average Pace"].verdict
    assert "Efficiency Factor (Speed/HR)" in metric_map
    assert "B was more aerobic-efficient" in metric_map["Efficiency Factor (Speed/HR)"].verdict


def test_readiness_synthesis():
    sleep = {"sleepScores": {"overall": {"value": 85}}, "durationInSeconds": 28800}
    hrv = {"hrvSummary": {"lastNightAvg": 55, "weeklyAvg": 53, "status": "balanced"}}
    bb = {"charged": 88}
    daily = {"restingHeartRate": 48, "averageStressLevel": 22}

    status = evaluate_daily_readiness(sleep, hrv, bb, daily)
    assert status.sleep_score == 85
    assert status.hrv_status == "balanced"
    assert status.resting_hr_bpm == 48
    assert "High Readiness" in status.readiness_verdict


def test_html_dashboard_generation(tmp_path):
    from ricelo.reporting import generate_html_dashboard
    acts = [
        Activity(
            activity_id=999,
            name="Test 10K Run",
            activity_type="running",
            start_time_local="2026-10-25T07:00:00",
            distance_m=10000,
            duration_s=3000,
            elapsed_duration_s=3000,
            avg_speed_mps=3.33,
            avg_hr=150,
            splits=[
                Split(split_index=1, distance_m=1000, duration_s=300, avg_speed_mps=3.33, avg_hr=145),
                Split(split_index=2, distance_m=1000, duration_s=300, avg_speed_mps=3.33, avg_hr=150),
            ]
        )
    ]
    volume = aggregate_volume(acts, freq="W")
    fitness = calculate_fitness_fatigue(acts, days=14)
    out_file = tmp_path / "test_dash.html"
    res_path = generate_html_dashboard(acts, volume, fitness, output_path=out_file)

    assert res_path.exists()
    content = res_path.read_text(encoding="utf-8")
    assert "Ricelo Running Science" in content
    assert 'data-theme="light"' in content
    assert "Banister Impulse Response Fitness and Fatigue" in content
    assert "table-wrapper" in content
    assert "splitsModal" in content
    assert "Test 10K Run" in content


def test_acwr_and_vdot():
    from ricelo.performance import (
        DailyFitnessRecord,
        calculate_acwr,
        calculate_vdot,
        get_vdot_race_predictions,
        get_vdot_training_paces,
    )

    # ACWR test: 14 days of constant load 100
    timeline = [
        DailyFitnessRecord(
            date_str=f"2026-10-{i+1:02d}",
            daily_load=100.0,
            ctl_fitness=100.0,
            atl_fatigue=100.0,
            tsb_form=0.0,
            status="Optimal Base",
        )
        for i in range(14)
    ]
    acwr = calculate_acwr(timeline)
    assert acwr.acute_load == 700.0
    assert acwr.acwr == 1.0
    assert acwr.status == "Sweet Spot"

    # VDOT test: 5000m in 24:41 (1481s) -> ~38.9
    vdot = calculate_vdot(5000.0, 1481.0)
    assert 38.5 <= vdot <= 39.5

    paces = get_vdot_training_paces(vdot)
    assert "easy_pace" in paces
    assert "threshold_pace" in paces
    assert "05:" in paces["threshold_pace"]

    preds = get_vdot_race_predictions(vdot)
    assert "5k" in preds
    assert "marathon" in preds
