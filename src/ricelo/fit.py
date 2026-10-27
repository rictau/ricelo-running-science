"""Parser for Garmin raw .FIT activity files."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitparse
import pandas as pd

from .models import Activity, Split

# Cardiac drift needs a sustained effort to mean anything.
MIN_DECOUPLING_MOVING_S = 20 * 60
# Gaps longer than this between samples are pauses, not moving time.
MAX_SAMPLE_GAP_S = 10.0
# Below this speed (m/s, about 30 min/km) the athlete is standing.
MIN_MOVING_SPEED_MPS = 0.5


@dataclass
class FitActivityData:
    """Parsed time-series streams and lap records from a .FIT file."""
    records_df: pd.DataFrame
    laps_df: pd.DataFrame
    session_info: dict[str, Any]
    vo2_max_exact: float | None = None

    def get_summary(self) -> dict[str, Any]:
        """Summarize key metrics from the .FIT data streams."""
        res: dict[str, Any] = {}
        if self.vo2_max_exact is not None:
            res["vo2_max_exact"] = round(self.vo2_max_exact, 2)
            res["vo2_max_display"] = round(self.vo2_max_exact)
        if not self.records_df.empty:
            res["total_records"] = len(self.records_df)
            if "heart_rate" in self.records_df:
                res["min_hr"] = float(self.records_df["heart_rate"].min())
                res["avg_hr"] = round(float(self.records_df["heart_rate"].mean()), 1)
                res["max_hr"] = float(self.records_df["heart_rate"].max())
            if "speed" in self.records_df:
                res["avg_speed_mps"] = round(float(self.records_df["speed"].mean()), 2)
                res["max_speed_mps"] = round(float(self.records_df["speed"].max()), 2)
            if "power" in self.records_df:
                res["avg_power_w"] = round(float(self.records_df["power"].mean()), 1)
                res["max_power_w"] = float(self.records_df["power"].max())
            if "cadence" in self.records_df:
                cadence = self.records_df["cadence"].astype(float)
                if "fractional_cadence" in self.records_df:
                    cadence = cadence + self.records_df["fractional_cadence"].fillna(0.0).astype(float)
                cadence = cadence[cadence > 0]  # zeros are stopped periods
                if self.session_info.get("sport") == "running":
                    cadence = cadence * 2  # FIT stores running cadence per leg
                if not cadence.empty:
                    res["avg_cadence"] = round(float(cadence.mean()), 1)
            if "altitude" in self.records_df:
                res["min_altitude_m"] = round(float(self.records_df["altitude"].min()), 1)
                res["max_altitude_m"] = round(float(self.records_df["altitude"].max()), 1)
        res["laps_count"] = len(self.laps_df)
        return res


def parse_fit_bytes(fit_bytes: bytes) -> FitActivityData:
    """Parse raw bytes of a .FIT file into structured DataFrames."""
    fitfile = fitparse.FitFile(io.BytesIO(fit_bytes))

    records: list[dict[str, Any]] = []
    laps: list[dict[str, Any]] = []
    session_info: dict[str, Any] = {}
    vo2_max: float | None = None

    for record in fitfile.get_messages():
        if record.name == "record":
            row: dict[str, Any] = {}
            for data in record:
                if data.value is not None:
                    row[data.name] = data.value
            if row:
                records.append(row)
        elif record.name == "lap":
            row = {}
            for data in record:
                if data.value is not None:
                    row[data.name] = data.value
            if row:
                laps.append(row)
        elif record.name == "session":
            for data in record:
                if data.value is not None:
                    session_info[data.name] = data.value
        elif record.name in ("unknown_140", "max_met_data", "max_met"):
            for data in record:
                if data.name in ("unknown_7", "vo2_max", "max_met") and data.value:
                    try:
                        vo2_max = (float(data.value) * 3.5) / 65536.0
                    except (ValueError, TypeError):
                        pass

    records_df = pd.DataFrame(records)
    laps_df = pd.DataFrame(laps)

    return FitActivityData(
        records_df=records_df,
        laps_df=laps_df,
        session_info=session_info,
        vo2_max_exact=vo2_max,
    )


def parse_fit_download(data: bytes) -> FitActivityData:
    """Parse a Garmin activity download, either a zip archive or a bare .fit file."""
    if not data.startswith(b"PK"):
        return parse_fit_bytes(data)
    with zipfile.ZipFile(io.BytesIO(data), "r") as archive:
        fit_names = [n for n in archive.namelist() if n.lower().endswith(".fit")]
        if not fit_names:
            raise ValueError("No .fit file found inside the activity archive.")
        return parse_fit_bytes(archive.read(fit_names[0]))


def extract_and_parse_fit(zip_or_fit_path: Path | str) -> FitActivityData:
    """
    Parse a .FIT file from a .zip download archive or direct .fit file.
    """
    path = Path(zip_or_fit_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return parse_fit_download(path.read_bytes())


def _row_float(row: pd.Series, *keys: str) -> float | None:
    """First present, non-null, non-zero value among keys, as float."""
    for key in keys:
        value = row.get(key)
        if value is not None and not pd.isna(value) and value != 0:
            return float(value)
    return None


def laps_to_splits(laps_df: pd.DataFrame) -> list[Split]:
    """Convert FIT lap messages (the watch's auto or manual laps) into Splits."""
    splits: list[Split] = []
    for idx, (_, row) in enumerate(laps_df.iterrows(), start=1):
        dist = _row_float(row, "total_distance") or 0.0
        dur = _row_float(row, "total_timer_time", "total_elapsed_time") or 0.0
        spd = _row_float(row, "enhanced_avg_speed", "avg_speed") or (dist / dur if dur > 0 else 0.0)
        cad = _row_float(row, "avg_running_cadence")
        if cad is not None:
            # Per-leg cadence plus its fractional part, doubled to steps per minute.
            cad = round((cad + (_row_float(row, "avg_fractional_cadence") or 0.0)) * 2, 1)
        splits.append(
            Split(
                split_index=idx,
                distance_m=dist,
                duration_s=dur,
                avg_speed_mps=spd,
                avg_hr=_row_float(row, "avg_heart_rate"),
                max_hr=_row_float(row, "max_heart_rate"),
                avg_cadence=cad,
                elevation_gain_m=_row_float(row, "total_ascent"),
                elevation_loss_m=_row_float(row, "total_descent"),
            )
        )
    return splits


def decoupling_from_records(records_df: pd.DataFrame) -> float | None:
    """
    Aerobic decoupling (Pa:HR) from the per-sample record stream.

    Splits the moving time into two equal halves and compares efficiency
    (speed / HR) between them. Uses time rather than laps, so it works for
    any lap setup. Returns None for efforts under 20 minutes of moving time
    or without heart-rate data.
    """
    speed_col = "enhanced_speed" if "enhanced_speed" in records_df else "speed"
    if records_df.empty or "timestamp" not in records_df or "heart_rate" not in records_df or speed_col not in records_df:
        return None

    df = records_df[["timestamp", speed_col, "heart_rate"]].dropna().sort_values("timestamp").reset_index(drop=True)
    # Weight each sample by the time until the next one; a long gap is a pause.
    dt = pd.to_datetime(df["timestamp"]).diff().dt.total_seconds().shift(-1).fillna(0.0)
    df = df.assign(dt=dt.where(dt <= MAX_SAMPLE_GAP_S, 0.0))
    df = df[(df[speed_col] > MIN_MOVING_SPEED_MPS) & (df["heart_rate"] > 40) & (df["dt"] > 0)]

    moving_s = float(df["dt"].sum())
    if moving_s < MIN_DECOUPLING_MOVING_S:
        return None

    first = df[df["dt"].cumsum() <= moving_s / 2.0]
    second = df.drop(first.index)

    def efficiency(half: pd.DataFrame) -> float:
        weight = half["dt"].sum()
        return float((half[speed_col] * half["dt"]).sum() / weight) / float((half["heart_rate"] * half["dt"]).sum() / weight)

    eff1, eff2 = efficiency(first), efficiency(second)
    return round((eff1 - eff2) / eff1 * 100.0, 2)


def apply_fit_data(act: Activity, fit_data: FitActivityData) -> Activity:
    """Replace an activity's splits with the watch's real laps and add record-based drift."""
    splits = laps_to_splits(fit_data.laps_df)
    if splits:
        act.splits = splits
    act.record_decoupling_pct = decoupling_from_records(fit_data.records_df)
    return act


def fetch_activity_with_laps(client: Any, activity_id: int | str) -> Activity:
    """
    Fetch an activity and enrich it with lap and drift data from its FIT file.

    The activity details endpoint has no lap list (its splitSummaries are
    totals per movement type), so laps and cardiac drift come from the
    original FIT file. If the download fails, the activity is returned
    without laps rather than failing the whole request.
    """
    act = Activity.from_api_dict(client.get_activity(activity_id))
    try:
        apply_fit_data(act, parse_fit_download(client.download_activity_bytes(activity_id)))
    except Exception:
        pass
    return act
