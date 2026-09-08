from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

import ee
import pyproj
from dotenv import load_dotenv
from shapely.geometry import shape

from calibration.modeling import load_artifact


ARTIFACT_PATH = Path("calibration/artifacts/public_landsat_surrogate.pkl")
_ee_initialized = False
GEOD = pyproj.Geod(ellps="WGS84")

# Landsat 8 OLI / MODIS Terra daytime overpass is nominally ~10:30 local time.
# This constant is used consistently across training and inference.
DAYTIME_OVERPASS_HOUR = 10


def initialize_ee() -> None:
    global _ee_initialized
    if _ee_initialized:
        return
    load_dotenv()
    project = os.getenv("EE_PROJECT_ID")
    if project:
        ee.Initialize(project=project)
    else:
        ee.Initialize()
    _ee_initialized = True


def artifact_available() -> bool:
    return ARTIFACT_PATH.exists()


def get_artifact_metadata() -> Dict[str, Any]:
    artifact = load_artifact(ARTIFACT_PATH)
    return {
        "model_name": artifact["model_name"],
        "metrics": artifact["metrics"],
        "feature_columns": artifact["feature_columns"],
        "residual_quantiles": artifact["residual_quantiles"],
    }


def _midpoint_day_of_year(start_date: str, end_date: str) -> int:
    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    midpoint = start + ((end - start) // 2)
    return midpoint.timetuple().tm_yday


def _geodesic_area_sqm(geometry: Dict[str, Any]) -> float:
    area, _ = GEOD.geometry_area_perimeter(shape(geometry))
    return abs(area)


def _liang_broadband_albedo(scaled_sr) -> Any:
    """
    Compute broadband surface albedo using the Liang (2001) empirical formula for Landsat OLI.
    All bands must already be scaled to surface reflectance [0, 1].
    Formula: α = 0.356·B2 + 0.130·B4 + 0.373·B5 + 0.085·B6 + 0.072·B7 - 0.0018
    """
    blue = scaled_sr.select("SR_B2")
    red = scaled_sr.select("SR_B4")
    nir = scaled_sr.select("SR_B5")
    swir1 = scaled_sr.select("SR_B6")
    swir2 = scaled_sr.select("SR_B7")
    return (
        blue.multiply(0.356)
        .add(red.multiply(0.130))
        .add(nir.multiply(0.373))
        .add(swir1.multiply(0.085))
        .add(swir2.multiply(0.072))
        .subtract(0.0018)
        .rename("albedo_proxy")
    )


def _feature_means(geometry: Dict[str, Any], start_date: str, end_date: str) -> Dict[str, float]:
    initialize_ee()
    region = ee.Geometry(geometry)

    landsat_sr = (
        ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUD_COVER", 15))
        .median()
    )
    landsat_scaled = landsat_sr.multiply(0.0000275).add(-0.2)
    nir = landsat_scaled.select("SR_B5")
    red = landsat_scaled.select("SR_B4")
    swir = landsat_scaled.select("SR_B6")

    dynamic_world = (
        ee.ImageCollection("GOOGLE/DYNAMICWORLD/V1")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .mean()
    )

    # GHSL population and built-surface (latest epoch: 2020)
    ghsl_population = (
        ee.ImageCollection("JRC/GHSL/P2023A/GHS_POP")
        .filterDate("2020-01-01", "2021-01-01")
        .first()
        .select("population_count")
        .rename("population_density_proxy")
    )
    ghsl_built = (
        ee.ImageCollection("JRC/GHSL/P2023A/GHS_BUILT_S")
        .filterDate("2020-01-01", "2021-01-01")
        .first()
        .select("built_surface")
        .rename("built_surface_proxy")
    )

    # VIIRS nightlights: monthly median as economic activity proxy
    viirs_ntl = (
        ee.ImageCollection("NOAA/VIIRS/DNB/MONTHLY_V1/VCMCFG")
        .filterBounds(region)
        .filterDate(start_date, end_date)
        .select("avg_rad")
        .median()
        .rename("viirs_nightlights")
    )

    image = ee.Image.cat(
        [
            ee.ImageCollection("MODIS/061/MOD11A1")
            .filterBounds(region)
            .filterDate(start_date, end_date)
            .select("LST_Day_1km")
            .median()
            .multiply(0.02)
            .subtract(273.15)
            .rename("baseline_lst_c"),
            nir.subtract(red).divide(nir.add(red)).rename("ndvi"),
            swir.subtract(nir).divide(swir.add(nir)).rename("ndbi"),
            # impervious_fraction: Dynamic World built class probability (0–1)
            dynamic_world.select("built").rename("impervious_fraction"),
            dynamic_world.select("trees").rename("tree_canopy_fraction"),
            # built_density: GHS_BUILT_S normalized to fraction of cell area (0–1)
            # GHS_BUILT_S is m² of built surface per 100m cell (max = 10 000 m²)
            ghsl_built.divide(10000.0).rename("built_density"),
            # Physically correct broadband albedo (Liang 2001)
            _liang_broadband_albedo(landsat_scaled),
            ghsl_population,
            ghsl_built,
            viirs_ntl,
            # day_of_year encodes seasonality; hour_local is removed (always constant)
            ee.Image.constant(_midpoint_day_of_year(start_date, end_date)).rename("day_of_year"),
        ]
    )
    stats = image.reduceRegion(
        reducer=ee.Reducer.mean(),
        geometry=region,
        scale=30,
        bestEffort=True,
        maxPixels=1_000_000,
    ).getInfo()
    return {key: float(value) for key, value in stats.items() if value is not None}


def predict_scenario_delta(
    geometry: Dict[str, Any],
    start_date: str,
    end_date: str,
    vegetation_fraction: float,
    albedo: float,
) -> Dict[str, Any]:
    """
    Predict the LST drop from a greening + albedo intervention.

    The weather_context parameter has been intentionally removed from this
    function. Weather adjustments are always applied as a final uniform step
    in main.simulate_request() so that both heuristic and surrogate paths
    stack modifiers in an identical order.

    Intervention feature adjustments use literature-grounded coefficients:
    - NDVI/canopy: +0.45 per unit canopy gap (regression coefficient range 0.40–0.50)
    - Impervious fraction reduction: ×(1 – canopy_gap × 0.50) (Gill et al. 2007)
    - NDBI: –0.25 per unit canopy gap, –0.08 per unit albedo gap
    - Albedo proxy: direct addition of albedo_gap
    - built_surface_proxy: ×(1 – canopy_gap × 0.10) (minor structural change)
    - built_density: ×(1 – canopy_gap × 0.15) (land-cover reclassification)
    """
    artifact = load_artifact(ARTIFACT_PATH)
    feature_columns = artifact["feature_columns"]
    model = artifact["model"]
    residual_quantiles = artifact["residual_quantiles"]

    baseline = _feature_means(geometry, start_date, end_date)
    baseline_row = {column: baseline.get(column, 0.0) for column in feature_columns}

    intervention_row = dict(baseline_row)
    canopy_gap = max(0.0, vegetation_fraction - intervention_row.get("tree_canopy_fraction", 0.0))
    albedo_gap = max(0.0, albedo - intervention_row.get("albedo_proxy", 0.0))

    # Apply intervention feature adjustments
    intervention_row["tree_canopy_fraction"] = min(1.0, intervention_row["tree_canopy_fraction"] + canopy_gap)
    intervention_row["impervious_fraction"] = max(0.0, intervention_row["impervious_fraction"] * (1.0 - (canopy_gap * 0.50)))
    intervention_row["built_density"] = max(0.0, intervention_row["built_density"] * (1.0 - (canopy_gap * 0.15)))
    intervention_row["ndvi"] = min(1.0, intervention_row.get("ndvi", 0.0) + (canopy_gap * 0.45))
    intervention_row["ndbi"] = max(-1.0, intervention_row.get("ndbi", 0.0) - (canopy_gap * 0.25) - (albedo_gap * 0.08))
    intervention_row["albedo_proxy"] = min(0.95, intervention_row["albedo_proxy"] + albedo_gap)
    intervention_row["built_surface_proxy"] = max(0.0, intervention_row.get("built_surface_proxy", 0.0) * (1.0 - (canopy_gap * 0.10)))

    baseline_prediction = float(model.predict([[baseline_row[column] for column in feature_columns]])[0])
    intervention_prediction = float(model.predict([[intervention_row[column] for column in feature_columns]])[0])
    raw_delta = max(0.0, baseline_prediction - intervention_prediction)

    area_sqm = _geodesic_area_sqm(geometry)
    baseline_heat_factor = max(0.8, min(1.35, 0.9 + ((baseline_prediction - 30.0) / 18.0)))
    area_factor = max(0.75, min(1.25, 0.78 + ((min(area_sqm, 250_000.0) / 250_000.0) ** 0.35)))
    seasonal_factor = max(0.9, min(1.08, 0.96 + ((baseline_row.get("day_of_year", 180.0) - 150.0) / 600.0)))

    delta = max(0.0, raw_delta * baseline_heat_factor * area_factor * seasonal_factor)

    return {
        "baseline_prediction_celsius": round(baseline_prediction, 2),
        "intervention_prediction_celsius": round(intervention_prediction, 2),
        "predicted_temp_drop_celsius": round(delta, 2),
        "raw_delta_celsius": round(raw_delta, 2),
        "residual_quantiles": residual_quantiles,
        "baseline_features": baseline_row,
        "intervention_features": intervention_row,
        "model_name": artifact["model_name"],
        "model_metrics": artifact["metrics"],
        "adjustment_factors": {
            "baseline_heat_factor": round(baseline_heat_factor, 3),
            "area_factor": round(area_factor, 3),
            "seasonal_factor": round(seasonal_factor, 3),
        },
        "context_proxies": {
            "population_density_proxy": round(baseline.get("population_density_proxy", 0.0), 3),
            "built_surface_proxy": round(baseline.get("built_surface_proxy", 0.0), 3),
            "viirs_nightlights": round(baseline.get("viirs_nightlights", 0.0), 3),
            "area_sqm": round(area_sqm, 2),
        },
    }
