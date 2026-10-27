"""Analyze 2 years of Garmin VO2 max history and correlation with running gaps."""

from __future__ import annotations

import io
from pathlib import Path
import zipfile

import fitparse
import pandas as pd

from ricelo.client import default_client


def main():
    print("Fetching activities from 2024-10-01 to 2026-10-25...")
    acts = default_client.search_all_activities(start_date="2024-10-01", activity_type="running")
    print(f"Found {len(acts)} running activities.")

    # Sort chronologically
    acts = sorted(acts, key=lambda a: a.get("startTimeLocal", ""))

    records = []
    downloads_dir = Path("./downloads")
    downloads_dir.mkdir(exist_ok=True)

    for idx, a in enumerate(acts):
        act_id = a.get("activityId")
        date_str = a.get("startTimeLocal", "")[:10]
        name = a.get("activityName", "Run")
        dist_m = float(a.get("distance", 0.0))
        speed = float(a.get("averageSpeed", 0.0))
        hr = a.get("averageHR") or a.get("averageHeartRate")

        zip_file = downloads_dir / f"activity_{act_id}.zip"
        if not zip_file.exists():
            try:
                default_client.download_activity(act_id, output_dir=downloads_dir)
            except Exception as e:
                print(f"Error downloading {act_id}: {e}")
                continue

        # Parse FIT
        vo2 = None
        try:
            with zipfile.ZipFile(zip_file) as z:
                for fn in z.namelist():
                    if fn.endswith(".fit"):
                        f = fitparse.FitFile(io.BytesIO(z.read(fn)))
                        for m in f.get_messages("unknown_140"):
                            for d in m:
                                if d.name == "unknown_7" and d.value:
                                    vo2 = (float(d.value) * 3.5) / 65536.0
                                    break
                            if vo2:
                                break
        except Exception as e:
            pass

        records.append({
            "activity_id": act_id,
            "date": date_str,
            "name": name,
            "distance_km": round(dist_m / 1000.0, 2),
            "avg_speed_mps": speed,
            "avg_hr": hr,
            "vo2_max": round(vo2, 2) if vo2 else None,
            "vo2_display": round(vo2) if vo2 else None,
        })

    df = pd.DataFrame(records)
    df.to_csv("vo2max_2year_history.csv", index=False)
    print(f"Saved {len(df)} records to vo2max_2year_history.csv")


if __name__ == "__main__":
    main()
