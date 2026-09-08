from __future__ import annotations

import json
import pickle
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import ElasticNet
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.model_selection import GroupShuffleSplit, train_test_split


# ── Feature columns ────────────────────────────────────────────────────────────
# hour_local removed: it was always constant (10 = daytime overpass) and
# contributed zero information to the model.
# built_density now uses GHS_BUILT_S / 10000 (normalized built-surface fraction)
# which is genuinely different from impervious_fraction (Dynamic World built probability).
# albedo_proxy uses the Liang (2001) broadband formula instead of the biased visible mean.
# viirs_nightlights added as an economic activity / deprivation proxy.
DEFAULT_FEATURE_COLUMNS = [
    "baseline_lst_c",
    "ndvi",
    "ndbi",
    "impervious_fraction",
    "tree_canopy_fraction",
    "built_density",
    "albedo_proxy",
    "population_density_proxy",
    "built_surface_proxy",
    "viirs_nightlights",
    "day_of_year",
]


@dataclass
class TrainingArtifacts:
    model_name: str
    metrics: Dict[str, float]
    feature_columns: List[str]
    model: Any
    residual_quantiles: Dict[str, float]


def load_training_frame(csv_path: str | Path) -> pd.DataFrame:
    frame = pd.read_csv(csv_path)
    missing = [column for column in DEFAULT_FEATURE_COLUMNS + ["target_surface_temperature_c"] if column not in frame.columns]
    if missing:
        raise ValueError(f"Missing required training columns: {missing}")
    frame = frame.dropna(subset=DEFAULT_FEATURE_COLUMNS + ["target_surface_temperature_c"]).copy()
    if frame.empty:
        raise ValueError("Training dataset is empty after dropping rows with missing required values.")
    return frame


def split_frame(frame: pd.DataFrame, group_column: Optional[str] = None):
    if group_column and group_column in frame.columns:
        splitter = GroupShuffleSplit(n_splits=1, test_size=0.2, random_state=42)
        train_idx, test_idx = next(splitter.split(frame, groups=frame[group_column]))
        train_frame = frame.iloc[train_idx].copy()
        test_frame = frame.iloc[test_idx].copy()
    else:
        train_frame, test_frame = train_test_split(frame, test_size=0.2, random_state=42)
    return train_frame, test_frame


def fit_baseline_models(frame: pd.DataFrame, group_column: Optional[str] = None) -> List[TrainingArtifacts]:
    train_frame, test_frame = split_frame(frame, group_column=group_column)
    x_train = train_frame[DEFAULT_FEATURE_COLUMNS]
    y_train = train_frame["target_surface_temperature_c"]
    x_test = test_frame[DEFAULT_FEATURE_COLUMNS]
    y_test = test_frame["target_surface_temperature_c"]

    candidates = {
        "elastic_net": ElasticNet(alpha=0.05, l1_ratio=0.3, random_state=42),
        "gradient_boosting": GradientBoostingRegressor(random_state=42),
    }

    artifacts: List[TrainingArtifacts] = []
    for model_name, model in candidates.items():
        model.fit(x_train, y_train)
        predictions = model.predict(x_test)
        residuals = y_test.to_numpy() - predictions
        artifacts.append(
            TrainingArtifacts(
                model_name=model_name,
                metrics={
                    "mae": float(mean_absolute_error(y_test, predictions)),
                    "rmse": float(mean_squared_error(y_test, predictions) ** 0.5),
                    "bias": float(residuals.mean()),
                    "n_test": float(len(y_test)),
                },
                feature_columns=DEFAULT_FEATURE_COLUMNS,
                model=model,
                residual_quantiles={
                    "q10": float(pd.Series(residuals).quantile(0.10)),
                    "q90": float(pd.Series(residuals).quantile(0.90)),
                },
            )
        )
    return artifacts


def choose_best_artifact(artifacts: List[TrainingArtifacts]) -> TrainingArtifacts:
    return min(artifacts, key=lambda artifact: artifact.metrics["rmse"])


def save_artifact(artifact: TrainingArtifacts, artifact_path: str | Path, report_path: str | Path) -> None:
    artifact_payload = {
        "model_name": artifact.model_name,
        "feature_columns": artifact.feature_columns,
        "metrics": artifact.metrics,
        "residual_quantiles": artifact.residual_quantiles,
        "model": artifact.model,
    }
    artifact_path = Path(artifact_path)
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    with artifact_path.open("wb") as handle:
        pickle.dump(artifact_payload, handle)

    report = {
        "selected_model": artifact.model_name,
        "metrics": artifact.metrics,
        "feature_columns": artifact.feature_columns,
        "residual_quantiles": artifact.residual_quantiles,
    }
    report_path = Path(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")


def load_artifact(artifact_path: str | Path) -> Dict[str, Any]:
    with Path(artifact_path).open("rb") as handle:
        return pickle.load(handle)
