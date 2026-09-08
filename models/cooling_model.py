import numpy as np
from sklearn.linear_model import LinearRegression


class HeuristicCoolingModel:
    """
    Heuristic scenario model predicting relative LST response from two independent
    urban heat mitigation strategies.

      1. Vegetation (trees / green roofs) — evapotranspiration + shading
      2. Cool Roofs / High-Albedo surfaces — increased shortwave reflectivity

    Both effects are modeled separately then combined:
        Total Drop = (Veg_Effect × 0.6) + (Albedo_Effect × 0.4)

    Important:
    - This model is not calibrated against local station or field measurements.
    - The hard-coded coefficients and sample points are placeholders intended
      to provide a demo-ready scenario workflow.
    - Outputs should be treated as directional scenario scores, not validated
      physical forecasts.
    """

    def __init__(self):
        self._veg_model = LinearRegression()
        X_veg = np.array([
            [100,    0.1],
            [500,    0.3],
            [1_000,  0.5],
            [5_000,  0.8],
            [10_000, 1.0],
            [20_000, 1.0],
            [100,    0.9],
        ])
        y_veg = np.array([0.1, 1.0, 1.5, 2.5, 3.0, 3.5, 0.5])
        self._veg_model.fit(X_veg, y_veg)

        self._albedo_model = LinearRegression()
        X_alb = np.array([
            [100,    0.10],
            [100,    0.40],
            [500,    0.40],
            [1_000,  0.60],
            [5_000,  0.70],
            [5_000,  0.80],
            [10_000, 0.80],
            [20_000, 0.90],
        ])
        y_alb = np.array([0.0, 0.3, 0.6, 1.0, 1.6, 2.0, 2.4, 2.8])
        self._albedo_model.fit(X_alb, y_alb)

    def _veg_effect(self, area_sqm: float, veg_fraction: float) -> float:
        area = min(area_sqm, 20_000)
        veg = np.clip(veg_fraction, 0.0, 1.0)
        raw = self._veg_model.predict(np.array([[area, veg]]))[0]
        return float(np.clip(raw, 0.0, 3.5))

    def _albedo_effect(self, area_sqm: float, albedo: float) -> float:
        area = min(area_sqm, 20_000)
        alb = np.clip(albedo, 0.1, 0.9)
        raw = self._albedo_model.predict(np.array([[area, alb]]))[0]
        return float(np.clip(raw, 0.0, 2.8))

    def predict(
        self,
        area_sqm: float,
        vegetation_fraction: float,
        target_albedo: float,
    ) -> dict:
        veg_drop = self._veg_effect(area_sqm, vegetation_fraction)
        albedo_drop = self._albedo_effect(area_sqm, target_albedo)

        total_drop = (veg_drop * 0.6) + (albedo_drop * 0.4)

        pure_veg_max = self._veg_effect(area_sqm, 1.0)
        pure_albedo_max = self._albedo_effect(area_sqm, 0.9)

        if abs(pure_veg_max - pure_albedo_max) < 0.15:
            best_strategy = "Combined (both strategies are equally effective here)"
        elif pure_veg_max >= pure_albedo_max:
            best_strategy = "Vegetation (trees/green roofs are more effective for this area)"
        else:
            best_strategy = "Cool Roofs (high-albedo surfaces are more effective for this area size)"

        return {
            "veg_effect_celsius": round(veg_drop, 2),
            "albedo_effect_celsius": round(albedo_drop, 2),
            "total_drop_celsius": round(total_drop, 2),
            "best_strategy": best_strategy,
            "model_family": "heuristic_linear_placeholder",
            "calibration_status": "not_calibrated",
            "valid_for_relative_comparison_only": True,
        }


_model = HeuristicCoolingModel()


def predict_temperature_drop(
    area_sqm: float,
    vegetation_fraction: float,
    target_albedo: float = 0.15,
) -> dict:
    return _model.predict(area_sqm, vegetation_fraction, target_albedo)
