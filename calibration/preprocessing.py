from __future__ import annotations

from pathlib import Path

import pandas as pd


def enrich_training_frame(input_csv: str | Path, output_csv: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(input_csv)
    timestamp = pd.to_datetime(frame["timestamp_local"])
    frame["hour_local"] = timestamp.dt.hour
    frame["day_of_year"] = timestamp.dt.dayofyear
    frame["tree_canopy_fraction"] = frame.get("tree_canopy_fraction", 0.0)
    frame["impervious_fraction"] = frame.get("impervious_fraction", 0.0)
    frame["built_density"] = frame.get("built_density", 0.0)
    frame["albedo_proxy"] = frame.get("albedo_proxy", 0.15)
    frame.to_csv(output_csv, index=False)
    return frame
