import os
from datetime import UTC, datetime
from typing import Any, Dict, List, Optional

import ee
import google.auth
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel, Field
import requests

import physics_engine as phys

load_dotenv()


def _parse_allowed_origins() -> List[str]:
    raw_origins = os.getenv("ALLOWED_ORIGINS", "*").strip()
    if not raw_origins or raw_origins == "*":
        return ["*"]
    return [origin.strip() for origin in raw_origins.split(",") if origin.strip()]


app = FastAPI(
    title="Urban Heat Island Prototype - Physics Engine",
    description="Backend using first-principles energy balance (R_net = H + LE + G) with full math transparency and uncertainty propagation.",
    version="6.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_allowed_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

EE_PROJECT_ID = os.getenv("EE_PROJECT_ID")
EE_SERVICE_ACCOUNT = os.getenv("EE_SERVICE_ACCOUNT")
EE_PRIVATE_KEY_JSON = os.getenv("EE_PRIVATE_KEY_JSON")
EE_INITIALIZED = False


def initialize_ee():
    global EE_INITIALIZED
    try:
        if EE_SERVICE_ACCOUNT and EE_PRIVATE_KEY_JSON:
            credentials = ee.ServiceAccountCredentials(
                EE_SERVICE_ACCOUNT,
                key_data=EE_PRIVATE_KEY_JSON,
            )
            if EE_PROJECT_ID:
                print(f"Initializing Earth Engine with service account and project: {EE_PROJECT_ID}")
                ee.Initialize(credentials=credentials, project=EE_PROJECT_ID)
            else:
                print("Initializing Earth Engine with service account credentials...")
                ee.Initialize(credentials=credentials)
        else:
            adc_credentials, adc_project = google.auth.default(
                scopes=[
                    "https://www.googleapis.com/auth/earthengine",
                    "https://www.googleapis.com/auth/cloud-platform",
                ]
            )
            project_id = EE_PROJECT_ID or adc_project
            if project_id:
                print(f"Initializing Earth Engine with application default credentials and project: {project_id}")
                ee.Initialize(credentials=adc_credentials, project=project_id)
            else:
                print("Initializing Earth Engine with application default credentials...")
                ee.Initialize(credentials=adc_credentials)
        EE_INITIALIZED = True
        print("Earth Engine initialized successfully.")
    except Exception as e:
        print(f"FAILED to initialize Earth Engine: {e}")


initialize_ee()

class SimulationRequest(BaseModel):
    label: str = Field(default="UI Simulation")
    polygon: Optional[Dict[str, Any]] = None
    zone_area_m2: float = Field(default=12000.0)

    # intervention surface
    target_vegetation: float = Field(default=0.30, ge=0.0, le=1.0)
    target_albedo: float = Field(default=0.45, ge=0.05, le=0.95)

    # baseline surface (auto-detected from satellite when polygon provided)
    baseline_vegetation: float = Field(default=0.05, ge=0.0, le=1.0)
    baseline_albedo: float = Field(default=0.15, ge=0.05, le=0.95)
    baseline_urban_fraction: float = Field(default=0.70, ge=0.0, le=1.0)

    # weather (the more accurate these are, the tighter the prediction)
    ambient_temp: float = Field(default=35.0, description="Air temperature at 2m height [°C]")
    humidity: float = Field(default=50.0, description="Relative humidity [%]")
    wind_speed: float = Field(default=2.0, description="Wind speed at 2m [m/s]")
    solar_radiation: Optional[float] = Field(default=None, description="Incoming solar [W/m²]. Auto-computed from lat/date/hour if omitted.")
    cloud_fraction: float = Field(default=0.0, ge=0.0, le=1.0)

    # location & time (needed for solar calculation if solar_radiation not given)
    latitude: float = Field(default=28.61)
    longitude: float = Field(default=77.23)
    day_of_year: int = Field(default=152, ge=1, le=366)
    hour_local: float = Field(default=14.0, ge=0, le=24)
    elevation_m: float = Field(default=216.0)

    # social
    high_economic_deprivation: bool = Field(default=False)
    high_elderly_population: bool = Field(default=False)


@app.get("/api/health")
async def get_health() -> Dict[str, Any]:
    return {
        "service": "urban_heat_planning_engine",
        "status": "ok",
        "timestamp_utc": datetime.now(UTC).isoformat(),
        "model_engine": {"type": "Physics Energy Balance", "version": "6.0.0"},
        "earth_engine": {"initialized": EE_INITIALIZED, "project": EE_PROJECT_ID},
    }


@app.get("/api/map-id/{city_name}")
async def get_map_id(city_name: str):
    if not EE_INITIALIZED:
        raise HTTPException(status_code=503, detail="Earth Engine not initialized.")

    try:
        boundary = ee.FeatureCollection("FAO/GAUL/2015/level1").filter(ee.Filter.eq("ADM1_NAME", city_name)).geometry()

        landsat_lst = (
            ee.ImageCollection("LANDSAT/LC08/C02/T1_L2")
            .filterBounds(boundary)
            .filterDate("2023-05-01", "2023-08-30")
            .filter(ee.Filter.lt("CLOUD_COVER", 15))
            .median()
            .select("ST_B10")
            .multiply(0.00341802).add(149.0).subtract(273.15)
            .clip(boundary)
        )

        landsat_lst_overview = (
            landsat_lst.resample("bilinear").reproject(crs="EPSG:3857", scale=1000).clip(boundary)
        )

        vis_params = {
            "min": 25,
            "max": 55,
            "palette": ["0000FF", "00FFFF", "FFFF00", "FF0000", "800000"],
        }

        detail_map_info = landsat_lst.getMapId(vis_params)
        overview_map_info = landsat_lst_overview.getMapId(vis_params)
        return {
            "tile_url": detail_map_info["tile_fetcher"].url_format,
            "detail_tile_url": detail_map_info["tile_fetcher"].url_format,
            "overview_tile_url": overview_map_info["tile_fetcher"].url_format,
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"GEE processing failed: {e}")


@app.get("/api/tiles/{path:path}")
async def proxy_tile(path: str):
    target_url = path
    if not target_url.startswith("http://") and not target_url.startswith("https://"):
        target_url = f"https://{target_url}"

    try:
        response = requests.get(target_url, timeout=30)
        return Response(content=response.content, media_type=response.headers.get("content-type", "image/png"), status_code=response.status_code)
    except requests.RequestException as exc:
        raise HTTPException(status_code=502, detail=f"Tile proxy failed: {exc}")


def _extract_baseline_from_satellite(polygon_geojson: Dict[str, Any]) -> Dict[str, float]:
    """Pull baseline NDVI / albedo from Sentinel-2 via GEE for the drawn polygon."""
    if not EE_INITIALIZED:
        return {}
    try:
        coords = polygon_geojson["geometry"]["coordinates"]
        ee_poly = ee.Geometry.Polygon(coords)

        s2 = (
            ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
            .filterBounds(ee_poly)
            .filterDate("2023-01-01", "2023-12-31")
            .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", 20))
            .median()
        )

        ndvi = s2.normalizedDifference(["B8", "B4"]).rename("NDVI")
        ndbi = s2.normalizedDifference(["B11", "B8"]).rename("NDBI")
        # Liang (2001) broadband albedo approximation
        albedo = (
            s2.select("B2").multiply(0.356)
            .add(s2.select("B3").multiply(0.130))
            .add(s2.select("B4").multiply(0.373))
            .add(s2.select("B8").multiply(0.085))
            .add(s2.select("B11").multiply(0.072))
            .subtract(0.0018)
            .divide(10000)
            .rename("Albedo")
        )

        composite = ndvi.addBands(ndbi).addBands(albedo)
        stats = composite.reduceRegion(
            reducer=ee.Reducer.mean(),
            geometry=ee_poly, scale=30, maxPixels=1e9, bestEffort=True,
        ).getInfo()

        out: Dict[str, float] = {}
        if stats.get("NDVI") is not None:
            out["vegetation"] = max(0.0, min(float(stats["NDVI"]), 1.0))
        if stats.get("NDBI") is not None:
            out["urban_fraction"] = max(0.0, min(0.5 + float(stats["NDBI"]), 1.0))
        if stats.get("Albedo") is not None:
            out["albedo"] = max(0.05, min(float(stats["Albedo"]), 0.95))
        return out
    except Exception as e:
        print(f"Satellite extraction failed, using request defaults: {e}")
        return {}


def _format_step(s: phys.CalculationStep) -> Dict[str, Any]:
    return {
        "step": s.step,
        "name": s.name,
        "equation": s.equation,
        "calculation": s.substitution,
        "result": s.result,
        "unit": s.unit,
        "uncertainty": f"± {s.uncertainty} {s.unit}" if s.uncertainty else None,
        "explanation": s.explanation,
    }


@app.post("/api/simulate-cooling")
async def simulate_cooling(request: SimulationRequest) -> Dict[str, Any]:
    # ── 1. Detect baseline from satellite if polygon provided ──
    sat_overrides: Dict[str, float] = {}
    if request.polygon and "geometry" in request.polygon:
        sat_overrides = _extract_baseline_from_satellite(request.polygon)

    baseline_veg   = sat_overrides.get("vegetation", request.baseline_vegetation)
    baseline_albedo = sat_overrides.get("albedo", request.baseline_albedo)
    baseline_urban = sat_overrides.get("urban_fraction", request.baseline_urban_fraction)

    # ── 2. Run physics engine ──
    comparison = phys.compare_scenarios(
        T_air=request.ambient_temp,
        rh=request.humidity,
        wind=request.wind_speed,
        solar=request.solar_radiation,
        latitude=request.latitude,
        day_of_year=request.day_of_year,
        hour_local=request.hour_local,
        cloud_fraction=request.cloud_fraction,
        elevation=request.elevation_m,
        baseline_albedo=baseline_albedo,
        baseline_veg=baseline_veg,
        baseline_urban=baseline_urban,
        intervention_albedo=request.target_albedo,
        intervention_veg=request.target_vegetation,
        intervention_urban=baseline_urban,
    )

    b = comparison.baseline
    iv = comparison.intervention

    # ── 3. Social impact ──
    impact_multiplier = 1.0
    if request.high_economic_deprivation and request.high_elderly_population:
        impact_multiplier = 2.0
    elif request.high_economic_deprivation or request.high_elderly_population:
        impact_multiplier = 1.5
    social_impact_score = comparison.cooling_c * impact_multiplier

    # ── 4. Build transparent response ──
    return {
        "label": request.label,
        "original_area_sqm": request.zone_area_m2,

        # headline numbers
        "baseline_surface_temp_c": b.surface_temp_c,
        "intervention_surface_temp_c": iv.surface_temp_c,
        "predicted_temp_drop_celsius": comparison.cooling_c,
        "cooling_uncertainty_celsius": comparison.cooling_uncertainty_c,
        "cooling_95_confidence": f"{comparison.cooling_95_low:.2f} – {comparison.cooling_95_high:.2f} °C",

        "social_impact_score": round(social_impact_score, 2),
        "impact_multiplier": impact_multiplier,

        # inputs used
        "applied_baseline": {
            "vegetation": round(baseline_veg, 3),
            "albedo": round(baseline_albedo, 3),
            "urban_fraction": round(baseline_urban, 3),
            "source": "satellite" if sat_overrides else "user/default",
        },
        "applied_intervention": {
            "vegetation": request.target_vegetation,
            "albedo": request.target_albedo,
        },
        "weather": {
            "air_temp_c": request.ambient_temp,
            "humidity_pct": request.humidity,
            "wind_speed_ms": request.wind_speed,
            "solar_wm2": request.solar_radiation or phys.solar_radiation_model(
                request.latitude, request.day_of_year,
                request.hour_local, request.cloud_fraction),
        },

        # energy balance breakdown
        "baseline_energy_balance": {
            "S_absorbed_wm2": b.fluxes.S_absorbed,
            "L_down_wm2": b.fluxes.L_down,
            "L_up_wm2": b.fluxes.L_up,
            "R_net_wm2": b.fluxes.R_net,
            "sensible_heat_wm2": b.fluxes.H,
            "latent_heat_wm2": b.fluxes.LE,
            "ground_heat_wm2": b.fluxes.G,
        },
        "intervention_energy_balance": {
            "S_absorbed_wm2": iv.fluxes.S_absorbed,
            "L_down_wm2": iv.fluxes.L_down,
            "L_up_wm2": iv.fluxes.L_up,
            "R_net_wm2": iv.fluxes.R_net,
            "sensible_heat_wm2": iv.fluxes.H,
            "latent_heat_wm2": iv.fluxes.LE,
            "ground_heat_wm2": iv.fluxes.G,
        },

        # full math — every step, every equation, every number
        "math_breakdown": {
            "baseline_steps": [_format_step(s) for s in b.steps],
            "intervention_steps": [_format_step(s) for s in iv.steps],
        },

        # exact error budget
        "error_budget": {
            "baseline_total_uncertainty_c": b.uncertainty_c,
            "baseline_95_CI": f"{b.confidence_95_low:.2f} – {b.confidence_95_high:.2f} °C",
            "intervention_total_uncertainty_c": iv.uncertainty_c,
            "intervention_95_CI": f"{iv.confidence_95_low:.2f} – {iv.confidence_95_high:.2f} °C",
            "cooling_uncertainty_c": comparison.cooling_uncertainty_c,
            "cooling_95_CI": f"{comparison.cooling_95_low:.2f} – {comparison.cooling_95_high:.2f} °C",
            "per_source": {
                src: f"± {val:.3f} °C"
                for src, val in sorted(
                    comparison.differential_error_budget.items(),
                    key=lambda x: -x[1],
                )
            },
        },

        "metadata": {
            "engine": "Physics Energy Balance v6.0",
            "method": "Newton-Raphson surface energy balance",
            "equation": "R_net = H + LE + G  →  (1-α)S↓ + εL↓ − εσTs⁴ = ρcp(Ts−Ta)/ra + ρLv(qs*−qa)/(ra+rs) + Λg·R_net",
            "baseline_converged": b.converged,
            "intervention_converged": iv.converged,
            "baseline_iterations": b.iterations,
            "intervention_iterations": iv.iterations,
        },
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
