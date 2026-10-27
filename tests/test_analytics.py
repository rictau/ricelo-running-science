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


def test_activity_from_api_dict_tolerates_null_fields():
    # Garmin sends null distance/speed for activities without GPS (e.g. strength).
    act = Activity.from_api_dict({
        "activityId": 1,
        "activityName": "Strength",
        "activityType": {"typeKey": "strength_training"},
        "startTimeLocal": "2026-10-20 07:00:00",
        "distance": None,
        "duration": 1800.0,
        "elapsedDuration": None,
        "averageSpeed": None,
        "splitSummaries": [{"distance": None, "duration": 600.0, "averageSpeed": None}],
    })
    assert act.distance_m == 0.0
    assert act.avg_speed_mps == 0.0
    assert act.elapsed_duration_s == 1800.0
    assert act.splits[0].distance_m == 0.0


def test_compare_activities_reports_equal():
    a = Activity(activity_id=1, distance_m=5000, duration_s=1500, avg_speed_mps=3.33, avg_hr=150, elevation_gain_m=20)
    b = Activity(activity_id=2, distance_m=5000, duration_s=1500, avg_speed_mps=3.33, avg_hr=150, elevation_gain_m=20)
    assert {m.metric_name: m.verdict for m in compare_activities(a, b)} == {
        "Distance": "Equal",
        "Duration": "Equal",
        "Average Pace": "Equal",
        "Average Heart Rate": "Equal",
        "Efficiency Factor (Speed/HR)": "Equal",
        "Elevation Gain": "Equal",
    }


def test_dashboard_escapes_activity_names(tmp_path):
    from ricelo.reporting import generate_html_dashboard

    evil = '</script><img src=x onerror=alert(1)>'
    act = Activity(
        activity_id=1,
        name=evil,
        activity_type="running",
        start_time_local="2026-10-20T07:00:00",
        distance_m=5000,
        duration_s=1500,
        avg_speed_mps=3.33,
    )
    out = generate_html_dashboard(
        [act],
        aggregate_volume([act]),
        calculate_fitness_fatigue([act], days=14),
        output_path=tmp_path / "dash.html",
        vo2max_csv_path=tmp_path / "missing.csv",
        vdot_info={
            "vdot": 40.0,
            "benchmark_activity_name": evil,
            "benchmark_distance": "5K",
            "benchmark_time": "25:00",
            "benchmark_date": "2026-10-20",
            "training_paces": {},
            "race_predictions": {},
        },
    )
    page = out.read_text(encoding="utf-8")
    assert evil not in page
    assert "escapeHtml(act.name)" in page


def test_dashboard_has_no_placeholder_athlete_data(tmp_path):
    from ricelo.reporting import generate_html_dashboard

    act = Activity(
        activity_id=1,
        name="Easy Run",
        activity_type="running",
        start_time_local="2026-10-20T07:00:00",
        distance_m=5000,
        duration_s=1500,
        avg_speed_mps=3.33,
    )
    args = ([act], aggregate_volume([act]), calculate_fitness_fatigue([act], days=14))
    page = generate_html_dashboard(
        *args, output_path=tmp_path / "a.html", vo2max_csv_path=tmp_path / "missing.csv"
    ).read_text(encoding="utf-8")
    for placeholder in ["44.35", "45.03", "42.26", "723 Days", "2024-11-01", "119 R", "rictau", "185 bpm", "Fitness Age", "Tempo phase"]:
        assert placeholder not in page, placeholder

    # With a known max HR the zones are derived from it.
    page = generate_html_dashboard(
        *args, output_path=tmp_path / "b.html", vo2max_csv_path=tmp_path / "missing.csv", max_hr=200
    ).read_text(encoding="utf-8")
    assert "140 to 160 bpm" in page  # Zone 2: 70-80% of 200
    assert "Max HR: <strong>200 bpm</strong>" in page


def test_movement_type_split_summaries_are_not_laps():
    # Garmin's splitSummaries group a run by movement type; this morning's
    # 5.4 km run came back as a 5.38 km "split" and a 24 m "split".
    act = Activity.from_api_dict({
        "activityId": 1,
        "activityType": {"typeKey": "running", "parentTypeId": 17},
        "startTimeLocal": "2026-10-27 08:30:58",
        "distance": 5404.0,
        "duration": 2136.0,
        "averageSpeed": 2.53,
        "aerobicTrainingEffect": 3.299999952316284,
        "splitSummaries": [
            {"splitType": "RWD_RUN", "distance": 5380.0, "duration": 2119.5, "averageSpeed": 2.54, "averageHR": 145},
            {"splitType": "RWD_STAND", "distance": 24.0, "duration": 16.5, "averageSpeed": 1.45, "averageHR": 145},
        ],
    })
    assert act.splits == []
    assert act.aerobic_training_effect == 3.3
    # parentTypeId is a numeric type ID, not a subtype name.
    assert act.activity_subtype is None


def test_race_predictions_never_show_sixty_seconds():
    from ricelo.performance import get_vdot_race_predictions

    for tenth in range(300, 801):
        for name, time_str in get_vdot_race_predictions(tenth / 10).items():
            assert not time_str.endswith(":60"), (tenth / 10, name, time_str)
            assert ":60:" not in time_str, (tenth / 10, name, time_str)


def _run(i, distance_m, duration_s, day="2026-10-15", **kw):
    return Activity(
        activity_id=i,
        name=f"Run {i}",
        activity_type="running",
        start_time_local=f"{day} 08:00:00",
        distance_m=distance_m,
        duration_s=duration_s,
        avg_speed_mps=distance_m / duration_s,
        **kw,
    )


def test_half_marathon_record_uses_race_distance_not_whole_run():
    # A 22 km run in 2:13:02 used to be reported as a 2:13:02 half marathon.
    efforts = {e.category: e for e in find_best_efforts([_run(1, 22000, 7982)])}
    half = efforts["Fastest Half Marathon"].value_display
    assert half.startswith("06:03 /km (02:07:3")
    assert "whole run 22.00 km in 02:13:02" in half

    # A run that is the race distance shows its own time.
    efforts = {e.category: e for e in find_best_efforts([_run(2, 5000, 1481)])}
    assert efforts["Fastest 5K"].value_display == "04:56 /km (24:41)"


def test_runs_shorter_than_race_distance_do_not_count():
    # 20.5 km used to count as a half marathon and 40 km as a marathon.
    efforts = {e.category for e in find_best_efforts([_run(1, 20600, 6000), _run(2, 40500, 13000)])}
    assert "Fastest Half Marathon" not in efforts
    assert "Marathon (42.2K)" not in efforts


def test_vdot_ignores_efforts_older_than_lookback():
    from datetime import date
    from ricelo.performance import find_best_empirical_vdot

    old_fast = _run(1, 5000, 1200, day="2019-10-01")  # 20:00 5K
    recent = _run(2, 5000, 1481, day="2026-10-15")    # 24:41 5K
    result = find_best_empirical_vdot([old_fast, recent], today=date(2026, 10, 27))
    assert result["benchmark_activity_id"] == 2
    assert result["lookback_days"] == 365
    assert "all history" not in result["note"]
    assert "not a run tagged as a race" in result["note"]

    # With nothing recent, fall back to history but say so.
    result = find_best_empirical_vdot([old_fast], today=date(2026, 10, 27))
    assert result["benchmark_activity_id"] == 1
    assert "2019-10-01" in result["note"]


def test_vdot_prefers_runs_tagged_as_races():
    from datetime import date
    from ricelo.performance import find_best_empirical_vdot

    fast_training = _run(1, 5000, 1400)
    race = _run(2, 10000, 3100, raw_data={"eventType": {"typeKey": "race"}})
    result = find_best_empirical_vdot([fast_training, race], today=date(2026, 10, 27))
    assert result["benchmark_activity_id"] == 2
    assert result["benchmark_is_race"] is True
    assert "note" not in result


def test_compare_uses_moving_duration_when_available():
    a = _run(1, 5000, 1500, moving_duration_s=1480)
    b = _run(2, 5000, 1500, moving_duration_s=1490)
    metric = next(m for m in compare_activities(a, b) if "Duration" in m.metric_name)
    assert metric.metric_name == "Moving Duration"
    assert (metric.value_a, metric.value_b) == ("24:40", "24:50")


def _records(minutes, hr_start, hr_end, speed=3.0, step_s=5):
    import pandas as pd

    n = int(minutes * 60 / step_s)
    start = pd.Timestamp("2026-10-27 08:30:00")
    return pd.DataFrame({
        "timestamp": [start + pd.Timedelta(seconds=i * step_s) for i in range(n)],
        "enhanced_speed": [speed] * n,
        "heart_rate": [hr_start + (hr_end - hr_start) * i / max(n - 1, 1) for i in range(n)],
    })


def test_decoupling_from_records():
    from ricelo.fit import decoupling_from_records

    # Constant pace with HR rising 140 -> 160: efficiency drops in the second half.
    drift = decoupling_from_records(_records(40, 140, 160))
    assert drift is not None and 6.0 < drift < 8.0
    assert decoupling_from_records(_records(40, 150, 150)) == 0.0
    # Too short to judge.
    assert decoupling_from_records(_records(15, 140, 160)) is None


def test_decoupling_ignores_pauses():
    import pandas as pd
    from ricelo.fit import decoupling_from_records

    df = _records(40, 150, 150)
    # A 10-minute stop at a traffic light: the gap and the standing samples
    # must not count as moving time or shift the halves.
    paused = df.copy()
    paused.loc[paused.index >= 240, "timestamp"] += pd.Timedelta(minutes=10)
    standing = pd.DataFrame({"timestamp": [pd.Timestamp("2026-10-27 08:50:05")], "enhanced_speed": [0.0], "heart_rate": [100]})
    assert decoupling_from_records(pd.concat([paused, standing])) == 0.0


def test_laps_to_splits_and_fit_cadence():
    import pandas as pd
    from ricelo.fit import FitActivityData, apply_fit_data, laps_to_splits

    laps = pd.DataFrame([
        {"total_distance": 1000.0, "total_timer_time": 395.0, "enhanced_avg_speed": 2.53, "avg_heart_rate": 140,
         "avg_running_cadence": 85, "avg_fractional_cadence": 0.5, "total_ascent": 10},
        {"total_distance": 403.0, "total_timer_time": 160.0, "avg_heart_rate": 150},
    ])
    splits = laps_to_splits(laps)
    assert [s.distance_m for s in splits] == [1000.0, 403.0]
    assert splits[0].avg_cadence == 171.0
    assert splits[1].avg_speed_mps == 403.0 / 160.0

    records = _records(30, 150, 150).assign(cadence=[86] * 359 + [0], fractional_cadence=0.0)
    fit = FitActivityData(records_df=records, laps_df=laps, session_info={"sport": "running"})
    # Per-leg cadence 86 is 172 steps per minute; the stopped zero is ignored.
    assert fit.get_summary()["avg_cadence"] == 172.0

    act = apply_fit_data(_run(1, 1403, 555), fit)
    assert len(act.splits) == 2
    assert act.aerobic_decoupling_pct == 0.0


def _distance_records(splits, step_s=5):
    """Records at step_s intervals; splits is [(seconds, speed_mps), ...]."""
    import pandas as pd

    rows, t, d = [], 0, 0.0
    start = pd.Timestamp("2026-10-27 08:00:00")
    for seconds, speed in splits:
        for _ in range(int(seconds / step_s)):
            rows.append({"timestamp": start + pd.Timedelta(seconds=t), "distance": d})
            t += step_s
            d += speed * step_s
    rows.append({"timestamp": start + pd.Timedelta(seconds=t), "distance": d})
    return pd.DataFrame(rows)


def test_best_segment_finds_fastest_stretch():
    from ricelo.fit import best_segment_seconds

    # 10 min easy at 2.5 m/s, then 5 km at 4 m/s (1250 s), then easy again.
    df = _distance_records([(600, 2.5), (1250, 4.0), (600, 2.5)])
    assert abs(best_segment_seconds(df, 5000) - 1250) < 1
    assert best_segment_seconds(df, 50000) is None


def test_best_segment_excludes_pauses():
    import pandas as pd
    from ricelo.fit import best_segment_seconds

    # 5 km at 4 m/s with a 2-minute auto-pause in the middle: no records,
    # no distance gained, so the timer time stays 1250 s.
    df = _distance_records([(1250, 4.0)])
    df.loc[df.index > 125, "timestamp"] += pd.Timedelta(minutes=2)
    assert abs(best_segment_seconds(df, 5000) - 1250) < 1


def test_measured_best_efforts_uses_fit_segments():
    from ricelo import fit

    long_run = _run(1, 22000, 7982)      # 22 km at 6:03 /km overall
    gps_short_5k = _run(2, 4980, 1481)   # the watch read 4.98 km in a 5K

    class FakeClient:
        downloads = []

        def download_activity_bytes(self, activity_id):
            self.downloads.append(activity_id)
            return b"fit"

    # The 22 km run: 2 km easy, then a 21.1 km stretch in 1:55:00, then easy.
    records = _distance_records([(800, 2.5), (6900, 21097.5 / 6900), (300, 2.5)])
    original = fit.parse_fit_download
    fit.parse_fit_download = lambda data: fit.FitActivityData(records_df=records, laps_df=records.iloc[0:0], session_info={})
    try:
        client = FakeClient()
        efforts = {e.category: e for e in fit.measured_best_efforts(client, [long_run, gps_short_5k])}
    finally:
        fit.parse_fit_download = original

    half = efforts["Fastest Half Marathon"]
    assert half.value_display.startswith("05:27 /km (01:55:00; fastest stretch of a 22.00 km run")
    # A GPS-short 5K counts in its own time, without a download.
    assert efforts["Fastest 5K"].value_display == "04:56 /km (24:41)"
    assert client.downloads == [1]


def test_best_segment_rejects_gps_jumps():
    from ricelo.fit import best_segment_seconds

    # Speed sensor says 2.5 m/s throughout, but GPS distance runs at 4 m/s:
    # the "fast" stretch is GPS error, not running.
    df = _distance_records([(1500, 4.0)]).assign(enhanced_speed=2.5)
    assert best_segment_seconds(df, 5000) is None
    # When the two agree, the segment counts.
    df = _distance_records([(1500, 4.0)]).assign(enhanced_speed=4.0)
    assert abs(best_segment_seconds(df, 5000) - 1250) < 1
