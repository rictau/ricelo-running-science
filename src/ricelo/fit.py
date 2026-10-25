"""Parser for Garmin raw .FIT activity files."""

from __future__ import annotations

import io
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import fitparse
import pandas as pd


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
                res["avg_cadence"] = round(float(self.records_df["cadence"].mean()), 1)
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


def extract_and_parse_fit(zip_or_fit_path: Path | str) -> FitActivityData:
    """
    Parse a .FIT file from a .zip download archive or direct .fit file.
    """
    path = Path(zip_or_fit_path).expanduser()
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")

    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path, "r") as archive:
            fit_names = [n for n in archive.namelist() if n.lower().endswith(".fit")]
            if not fit_names:
                raise ValueError(f"No .fit file found inside archive: {path}")
            fit_bytes = archive.read(fit_names[0])
            return parse_fit_bytes(fit_bytes)
    else:
        return parse_fit_bytes(path.read_bytes())
