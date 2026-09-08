"""
Physics-Based Urban Surface Energy Balance Model
=================================================

Predicts surface temperature from first principles by solving:

    R_net = H + LE + G

where:
    R_net = (1-α)S↓ + εL↓ - εσTs⁴       net radiation
    H     = ρ·cp·(Ts - Ta) / ra           sensible heat flux
    LE    = ρ·Lv·(qs* - qa) / (ra + rs)   latent heat flux (evapotranspiration)
    G     = Λg · R_net                     ground/storage heat flux

Solved iteratively via Newton-Raphson. Every intermediate value is
recorded so the caller can reconstruct the full derivation.

Uncertainty is propagated analytically through finite-difference
partial derivatives of each input, yielding a per-source error budget
and a combined ±°C confidence band.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

# ── Physical constants ──────────────────────────────────────────────
SIGMA = 5.670374419e-8       # Stefan-Boltzmann  [W m⁻² K⁻⁴]
K_VON_KARMAN = 0.41          # von Kármán constant
CP_AIR = 1005.0              # specific heat of dry air  [J kg⁻¹ K⁻¹]
LV = 2.45e6                  # latent heat of vaporisation  [J kg⁻¹]
P_STD = 101325.0             # standard sea-level pressure  [Pa]
R_DRY = 287.05               # gas constant for dry air  [J kg⁻¹ K⁻¹]
SOLAR_CONSTANT = 1361.0      # top-of-atmosphere irradiance  [W m⁻²]

# ── Default measurement uncertainties (1σ) ──────────────────────────
DEFAULT_UNCERTAINTIES = {
    "T_air":      0.5,    # °C   — weather station accuracy
    "RH":         5.0,    # %    — typical sensor
    "wind":       0.5,    # m/s  — cup anemometer
    "solar":      30.0,   # W/m² — pyranometer / model
    "albedo":     0.02,   # —    — satellite retrieval
    "emissivity": 0.01,   # —    — literature range
    "veg":        0.05,   # —    — NDVI-derived fraction
}


# ── Data containers ─────────────────────────────────────────────────
@dataclass
class CalculationStep:
    """One transparent step in the physics derivation."""
    step: int
    name: str
    equation: str           # human-readable formula
    substitution: str       # the actual numbers plugged in
    result: float
    unit: str
    uncertainty: float      # ± 1σ in same unit
    explanation: str


@dataclass
class EnergyFluxes:
    """Fully resolved energy balance at the surface."""
    S_absorbed: float       # W/m²
    L_down: float           # W/m²
    L_up: float             # W/m²
    R_net: float            # W/m²
    H: float                # W/m²
    LE: float               # W/m²
    G: float                # W/m²
    residual: float         # should be ~0 when converged


@dataclass
class PredictionResult:
    """Everything the API returns for one scenario."""
    surface_temp_c: float
    uncertainty_c: float                     # ± 1σ
    confidence_95_low: float
    confidence_95_high: float
    fluxes: EnergyFluxes
    steps: List[CalculationStep]
    error_budget: Dict[str, float]           # source → ± °C
    iterations: int
    converged: bool


@dataclass
class CoolingResult:
    """Comparison of baseline vs intervention."""
    baseline: PredictionResult
    intervention: PredictionResult
    cooling_c: float                         # baseline − intervention
    cooling_uncertainty_c: float             # propagated
    cooling_95_low: float
    cooling_95_high: float
    differential_error_budget: Dict[str, float]


# ── Helper physics ──────────────────────────────────────────────────

def saturation_vapor_pressure(T_c: float) -> float:
    """Magnus-Tetens formula  →  e_sat [Pa]."""
    return 610.78 * math.exp(17.269 * T_c / (T_c + 237.3))


def air_density(T_c: float, elev_m: float) -> float:
    """ρ adjusted for temperature and elevation  [kg/m³]."""
    P = P_STD * (1 - 2.25577e-5 * elev_m) ** 5.25588
    return P / (R_DRY * (T_c + 273.15))


def atmospheric_emissivity(T_c: float, rh: float) -> float:
    """Brutsaert (1975) clear-sky emissivity of the atmosphere."""
    e_a = (rh / 100.0) * saturation_vapor_pressure(T_c)
    e_a_kpa = e_a / 1000.0
    T_k = T_c + 273.15
    return min(1.24 * (e_a_kpa / T_k) ** (1.0 / 7.0), 1.0)


def solar_radiation_model(
    lat_deg: float,
    day_of_year: int,
    hour_local: float = 14.0,
    cloud_fraction: float = 0.0,
) -> float:
    """
    Estimate global horizontal irradiance from location + time.
    Uses the Hottel (1976) tropical clear-sky transmittance.
    """
    lat = math.radians(lat_deg)
    decl = math.radians(23.45 * math.sin(math.radians(360 / 365 * (day_of_year - 81))))
    ha = math.radians(15.0 * (hour_local - 12.0))

    sin_elev = math.sin(lat) * math.sin(decl) + math.cos(lat) * math.cos(decl) * math.cos(ha)
    if sin_elev <= 0.01:
        return 0.0

    am = 1.0 / sin_elev                          # air mass
    tau = max(0.271 + 0.706 * math.exp(-0.113 * am), 0.15)   # Hottel tropical
    S_clear = SOLAR_CONSTANT * sin_elev * tau
    return max(S_clear * (1.0 - 0.75 * cloud_fraction ** 3.4), 0.0)


def aerodynamic_resistance(u: float, z0m: float, z_ref: float = 2.0) -> float:
    """r_a for neutral stability  [s/m]."""
    u = max(u, 0.3)
    z0h = z0m / 10.0
    r = (math.log(z_ref / z0m) * math.log(z_ref / z0h)) / (K_VON_KARMAN ** 2 * u)
    return max(r, 5.0)


def surface_resistance(f_veg: float, T_c: float, rh: float) -> float:
    """Bulk canopy resistance  [s/m].  Infinite when no vegetation."""
    if f_veg < 0.01:
        return 1e6
    r_min = 100.0
    if T_c > 40.0 and rh < 30.0:
        r_min = 300.0      # heat-stressed stomata
    lai = 2.5 * f_veg
    return r_min / max(lai * f_veg, 0.01)


def ground_heat_fraction(f_urban: float, f_veg: float) -> float:
    """Fraction Λg of R_net stored in the substrate."""
    u = f_urban * (1.0 - f_veg)
    v = f_veg
    b = max(0.0, 1.0 - u - v)
    return u * 0.40 + v * 0.12 + b * 0.30


# ── Core solver ─────────────────────────────────────────────────────

def solve_surface_temp(
    T_air: float,
    rh: float,
    wind: float,
    solar: float,
    albedo: float,
    emissivity: float,
    f_veg: float,
    f_urban: float,
    z0m: float = 0.5,
    elev: float = 216.0,
    _record_steps: bool = True,
) -> Tuple[float, EnergyFluxes, List[CalculationStep], int, bool]:
    """
    Newton-Raphson iterative solve for T_surface.

    Returns (T_s_celsius, fluxes, steps, iterations, converged).
    """
    steps: List[CalculationStep] = []
    sn = 0

    # ── step 1: air density ──
    rho = air_density(T_air, elev)
    if _record_steps:
        P_local = P_STD * (1 - 2.25577e-5 * elev) ** 5.25588
        sn += 1
        steps.append(CalculationStep(
            sn, "Air density",
            "ρ = P / (Rd × T)",
            f"ρ = {P_local:.0f} / (287.05 × {T_air + 273.15:.2f}) = {rho:.4f}",
            round(rho, 4), "kg/m³", 0.01,
            f"Local pressure {P_local:.0f} Pa at {elev:.0f} m elevation."))

    # ── step 2: vapour pressure ──
    e_sat = saturation_vapor_pressure(T_air)
    e_act = (rh / 100.0) * e_sat
    q_air = 0.622 * e_act / (P_STD - 0.378 * e_act)
    if _record_steps:
        sn += 1
        steps.append(CalculationStep(
            sn, "Saturation & actual vapour pressure",
            "e_sat = 610.78 × exp(17.269·T / (T+237.3)),  e = RH/100 × e_sat",
            f"e_sat = 610.78 × exp(17.269×{T_air:.1f} / ({T_air:.1f}+237.3)) = {e_sat:.1f} Pa\n"
            f"e_actual = {rh:.0f}/100 × {e_sat:.1f} = {e_act:.1f} Pa\n"
            f"q_air = 0.622 × {e_act:.1f} / ({P_STD:.0f} − 0.378×{e_act:.1f}) = {q_air:.6f} kg/kg",
            round(e_act, 1), "Pa", round(e_sat * 0.05, 1),
            "Specific humidity of ambient air controls how much evaporative cooling is possible."))

    # ── step 3: incoming longwave ──
    eps_a = atmospheric_emissivity(T_air, rh)
    T_air_K = T_air + 273.15
    L_down = eps_a * SIGMA * T_air_K ** 4
    if _record_steps:
        sn += 1
        steps.append(CalculationStep(
            sn, "Incoming longwave radiation",
            "L↓ = ε_atm × σ × T_air⁴",
            f"ε_atm = 1.24 × ({e_act/1000:.3f} / {T_air_K:.2f})^(1/7) = {eps_a:.4f}\n"
            f"L↓ = {eps_a:.4f} × {SIGMA:.4e} × {T_air_K:.2f}⁴ = {L_down:.1f} W/m²",
            round(L_down, 1), "W/m²", round(L_down * 0.05, 1),
            "The atmosphere radiates heat back to the surface. Higher humidity → more L↓."))

    # ── step 4: aerodynamic resistance ──
    r_a = aerodynamic_resistance(wind, z0m)
    if _record_steps:
        sn += 1
        steps.append(CalculationStep(
            sn, "Aerodynamic resistance to heat transfer",
            "r_a = [ln(z/z₀ₘ) × ln(z/z₀ₕ)] / (k² × u)",
            f"r_a = [ln(2.0/{z0m}) × ln(2.0/{z0m/10:.3f})] / (0.41² × {max(wind,0.3):.1f}) = {r_a:.1f} s/m",
            round(r_a, 1), "s/m", round(r_a * 0.20, 1),
            "Controls how efficiently heat escapes the surface into the air. "
            "Low wind → high resistance → surface heats up."))

    # ── step 5: surface (stomatal) resistance ──
    r_s = surface_resistance(f_veg, T_air, rh)
    if _record_steps:
        sn += 1
        if f_veg < 0.01:
            rs_expl = "No vegetation present → no evapotranspiration. All energy goes to heating."
            rs_sub = f"f_veg = {f_veg:.2f} < 0.01 → r_s = ∞ (no ET)"
        else:
            lai = 2.5 * f_veg
            rs_expl = (f"Vegetation fraction {f_veg:.0%} with LAI={lai:.2f}. "
                       f"Stomata release water vapour, cooling the surface.")
            rs_sub = f"LAI = 2.5 × {f_veg:.2f} = {lai:.2f}\nr_s = {100 if T_air<=40 or rh>=30 else 300} / ({lai:.2f} × {f_veg:.2f}) = {r_s:.0f} s/m"
        steps.append(CalculationStep(
            sn, "Surface (stomatal) resistance",
            "r_s = r_s_min / (LAI × f_veg),  LAI = 2.5 × f_veg",
            rs_sub, round(r_s, 1), "s/m", round(min(r_s, 1e5) * 0.30, 1), rs_expl))

    # ── step 6: ground heat fraction ──
    cg = ground_heat_fraction(f_urban, f_veg)
    if _record_steps:
        sn += 1
        steps.append(CalculationStep(
            sn, "Ground / storage heat fraction",
            "Λ_G = f_urban×0.40 + f_veg×0.12 + f_bare×0.30",
            f"Λ_G = {f_urban*(1-f_veg):.2f}×0.40 + {f_veg:.2f}×0.12 + "
            f"{max(0,1-f_urban*(1-f_veg)-f_veg):.2f}×0.30 = {cg:.3f}",
            round(cg, 3), "—", 0.05,
            "Fraction of net radiation absorbed into ground/buildings. "
            "Concrete & asphalt store ~40 %; vegetation only ~12 %."))

    # ── step 7: absorbed shortwave ──
    S_abs = (1.0 - albedo) * solar
    if _record_steps:
        sn += 1
        steps.append(CalculationStep(
            sn, "Absorbed shortwave radiation",
            "S_abs = (1 − α) × S↓",
            f"S_abs = (1 − {albedo:.2f}) × {solar:.1f} = {S_abs:.1f} W/m²",
            round(S_abs, 1), "W/m²",
            round(math.sqrt((albedo * 30) ** 2 + ((1 - albedo) * 0.02 * solar) ** 2), 1),
            f"Surface absorbs {(1-albedo)*100:.0f}% of incoming sunlight. "
            f"Higher albedo (lighter colour) → less absorption → cooler surface."))

    # ── step 8: Newton-Raphson iteration ──
    T_s = T_air + 5.0
    converged = False
    n_iter = 0

    for i in range(80):
        n_iter = i + 1
        T_s_K = T_s + 273.15

        L_up  = emissivity * SIGMA * T_s_K ** 4
        R_net = S_abs + emissivity * L_down - L_up
        G     = cg * R_net
        H     = rho * CP_AIR * (T_s - T_air) / r_a

        e_sat_s = saturation_vapor_pressure(T_s)
        q_sat_s = 0.622 * e_sat_s / (P_STD - 0.378 * e_sat_s)
        LE = (rho * LV * max(q_sat_s - q_air, 0.0) / (r_a + r_s)) if r_s < 1e5 else 0.0

        residual = R_net - G - H - LE

        # derivatives
        dLup   = 4.0 * emissivity * SIGMA * T_s_K ** 3
        dRnet  = -dLup
        dG     = cg * dRnet
        dH     = rho * CP_AIR / r_a
        de_sat = e_sat_s * 17.269 * 237.3 / (T_s + 237.3) ** 2
        dq_sat = 0.622 * de_sat / P_STD
        dLE    = (rho * LV * dq_sat / (r_a + r_s)) if r_s < 1e5 else 0.0

        dR_dTs = dRnet - dG - dH - dLE
        if abs(dR_dTs) < 1e-12:
            break
        dt = -residual / dR_dTs
        T_s += dt
        if abs(dt) < 0.0005:
            converged = True
            break

    # final flux evaluation
    T_s_K = T_s + 273.15
    L_up  = emissivity * SIGMA * T_s_K ** 4
    R_net = S_abs + emissivity * L_down - L_up
    G     = cg * R_net
    H     = rho * CP_AIR * (T_s - T_air) / r_a
    e_sat_s = saturation_vapor_pressure(T_s)
    q_sat_s = 0.622 * e_sat_s / (P_STD - 0.378 * e_sat_s)
    LE = (rho * LV * max(q_sat_s - q_air, 0.0) / (r_a + r_s)) if r_s < 1e5 else 0.0
    residual = R_net - G - H - LE

    fluxes = EnergyFluxes(
        S_absorbed=round(S_abs, 2),
        L_down=round(emissivity * L_down, 2),
        L_up=round(L_up, 2),
        R_net=round(R_net, 2),
        H=round(H, 2),
        LE=round(LE, 2),
        G=round(G, 2),
        residual=round(residual, 4),
    )

    if _record_steps:
        sn += 1
        steps.append(CalculationStep(
            sn, "Energy balance → surface temperature (Newton-Raphson)",
            "(1−α)S↓ + εL↓ − εσTs⁴ = H + LE + G",
            f"Absorbed solar   = {S_abs:>8.1f} W/m²\n"
            f"Absorbed longwave= {emissivity*L_down:>8.1f} W/m²\n"
            f"Emitted longwave = {L_up:>8.1f} W/m²\n"
            f"────────────────────────────\n"
            f"Net radiation    = {R_net:>8.1f} W/m²\n"
            f"  → Sensible (H) = {H:>8.1f} W/m²  (heats the air)\n"
            f"  → Latent  (LE) = {LE:>8.1f} W/m²  (evaporation cooling)\n"
            f"  → Ground   (G) = {G:>8.1f} W/m²  (stored in substrate)\n"
            f"  → Residual     = {residual:>8.4f} W/m²\n"
            f"\n"
            f"Solved T_surface = {T_s:.2f} °C  in {n_iter} iterations",
            round(T_s, 2), "°C", 0.0,
            f"The surface must reach {T_s:.1f} °C for outgoing energy to balance incoming energy. "
            f"{'Converged.' if converged else 'WARNING: did not fully converge.'}"))

    return T_s, fluxes, steps, n_iter, converged


# ── Uncertainty propagation ─────────────────────────────────────────

def _solve_quiet(T_air, rh, wind, solar, albedo, emissivity, f_veg, f_urban,
                 z0m=0.5, elev=216.0) -> float:
    """Fast solve without recording steps."""
    return solve_surface_temp(
        T_air, rh, wind, solar, albedo, emissivity,
        f_veg, f_urban, z0m, elev, _record_steps=False)[0]


def propagate_uncertainty(
    T_air: float, rh: float, wind: float, solar: float,
    albedo: float, emissivity: float, f_veg: float, f_urban: float,
    z0m: float = 0.5, elev: float = 216.0,
    uncertainties: Optional[Dict[str, float]] = None,
) -> Tuple[float, Dict[str, float]]:
    """
    Finite-difference error propagation.

    Returns (total_1sigma_celsius, {source: contribution_celsius}).
    Each contribution is |∂T/∂x_i| × σ_i.  They add in quadrature:
        σ_total = √(Σ σ_i²)
    """
    unc = {**DEFAULT_UNCERTAINTIES, **(uncertainties or {})}

    base = _solve_quiet(T_air, rh, wind, solar, albedo, emissivity, f_veg, f_urban, z0m, elev)

    params = [
        ("T_air",      T_air,      unc["T_air"],
         lambda v: _solve_quiet(v, rh, wind, solar, albedo, emissivity, f_veg, f_urban, z0m, elev)),
        ("RH",         rh,         unc["RH"],
         lambda v: _solve_quiet(T_air, v, wind, solar, albedo, emissivity, f_veg, f_urban, z0m, elev)),
        ("wind",       wind,       unc["wind"],
         lambda v: _solve_quiet(T_air, rh, max(v, 0.1), solar, albedo, emissivity, f_veg, f_urban, z0m, elev)),
        ("solar",      solar,      unc["solar"],
         lambda v: _solve_quiet(T_air, rh, wind, max(v, 0.0), albedo, emissivity, f_veg, f_urban, z0m, elev)),
        ("albedo",     albedo,     unc["albedo"],
         lambda v: _solve_quiet(T_air, rh, wind, solar, min(max(v, 0.01), 0.99), emissivity, f_veg, f_urban, z0m, elev)),
        ("emissivity", emissivity, unc["emissivity"],
         lambda v: _solve_quiet(T_air, rh, wind, solar, albedo, min(max(v, 0.8), 1.0), f_veg, f_urban, z0m, elev)),
        ("veg",        f_veg,      unc["veg"],
         lambda v: _solve_quiet(T_air, rh, wind, solar, albedo, emissivity, min(max(v, 0.0), 1.0), f_urban, z0m, elev)),
    ]

    budget: Dict[str, float] = {}
    total_var = 0.0

    for name, val, sigma, fn in params:
        T_plus  = fn(val + sigma)
        T_minus = fn(val - sigma)
        partial = (T_plus - T_minus) / (2.0 * sigma)
        contrib = abs(partial * sigma)
        budget[name] = round(contrib, 4)
        total_var += contrib ** 2

    total = math.sqrt(total_var)
    return round(total, 4), budget


# ── High-level API ──────────────────────────────────────────────────

def predict_surface_temperature(
    T_air: float = 35.0,
    rh: float = 50.0,
    wind: float = 2.0,
    solar: Optional[float] = None,
    albedo: float = 0.15,
    emissivity: float = 0.95,
    f_veg: float = 0.05,
    f_urban: float = 0.70,
    latitude: float = 28.61,
    day_of_year: int = 152,
    hour_local: float = 14.0,
    cloud_fraction: float = 0.0,
    elevation: float = 216.0,
    z0m: float = 0.5,
) -> PredictionResult:
    """
    Predict the equilibrium surface temperature and return
    the full mathematical derivation with uncertainty.
    """
    # default solar from position model
    if solar is None:
        solar = solar_radiation_model(latitude, day_of_year, hour_local, cloud_fraction)

    T_s, fluxes, steps, n_iter, ok = solve_surface_temp(
        T_air, rh, wind, solar, albedo, emissivity, f_veg, f_urban, z0m, elevation)

    sigma, budget = propagate_uncertainty(
        T_air, rh, wind, solar, albedo, emissivity, f_veg, f_urban, z0m, elevation)

    # add uncertainty step
    budget_lines = "\n".join(f"  {k:>12s} : ± {v:.3f} °C" for k, v in sorted(budget.items(), key=lambda x: -x[1]))
    steps.append(CalculationStep(
        step=len(steps) + 1,
        name="Uncertainty propagation (finite-difference partials)",
        equation="σ_total = √( Σ (∂T/∂xᵢ × σ_xᵢ)² )",
        substitution=f"{budget_lines}\n  ────────────\n  Total 1σ    : ± {sigma:.3f} °C\n  95 % CI     : ± {1.96*sigma:.3f} °C",
        result=round(sigma, 3),
        unit="°C",
        uncertainty=0.0,
        explanation="Each input's measurement error is propagated through the energy balance. "
                    "Contributions add in quadrature (root-sum-of-squares) because they are independent."))

    return PredictionResult(
        surface_temp_c=round(T_s, 2),
        uncertainty_c=round(sigma, 2),
        confidence_95_low=round(T_s - 1.96 * sigma, 2),
        confidence_95_high=round(T_s + 1.96 * sigma, 2),
        fluxes=fluxes,
        steps=steps,
        error_budget=budget,
        iterations=n_iter,
        converged=ok,
    )


def compare_scenarios(
    T_air: float = 35.0,
    rh: float = 50.0,
    wind: float = 2.0,
    solar: Optional[float] = None,
    latitude: float = 28.61,
    day_of_year: int = 152,
    hour_local: float = 14.0,
    cloud_fraction: float = 0.0,
    elevation: float = 216.0,
    # baseline surface
    baseline_albedo: float = 0.15,
    baseline_emissivity: float = 0.95,
    baseline_veg: float = 0.05,
    baseline_urban: float = 0.70,
    baseline_z0m: float = 0.5,
    # intervention surface
    intervention_albedo: float = 0.40,
    intervention_emissivity: float = 0.93,
    intervention_veg: float = 0.30,
    intervention_urban: float = 0.70,
    intervention_z0m: float = 0.8,
) -> CoolingResult:
    """
    Run the energy balance for both baseline and intervention surfaces
    under identical weather, then report the cooling and its uncertainty.

    The differential (cooling) uncertainty is smaller than either absolute
    uncertainty because atmospheric / sensor biases cancel.
    """
    common = dict(
        T_air=T_air, rh=rh, wind=wind, solar=solar,
        latitude=latitude, day_of_year=day_of_year,
        hour_local=hour_local, cloud_fraction=cloud_fraction,
        elevation=elevation,
    )

    baseline = predict_surface_temperature(
        **common,
        albedo=baseline_albedo, emissivity=baseline_emissivity,
        f_veg=baseline_veg, f_urban=baseline_urban, z0m=baseline_z0m)

    intervention = predict_surface_temperature(
        **common,
        albedo=intervention_albedo, emissivity=intervention_emissivity,
        f_veg=intervention_veg, f_urban=intervention_urban, z0m=intervention_z0m)

    cooling = baseline.surface_temp_c - intervention.surface_temp_c

    # differential uncertainty: atmospheric errors cancel, only surface
    # property errors survive  → much tighter than absolute uncertainty
    surf_sources = {"albedo", "emissivity", "veg"}
    diff_budget: Dict[str, float] = {}
    diff_var = 0.0
    for src in baseline.error_budget:
        if src in surf_sources:
            c = math.sqrt(baseline.error_budget[src] ** 2 + intervention.error_budget[src] ** 2)
        else:
            c = abs(baseline.error_budget[src] - intervention.error_budget[src])
        diff_budget[src] = round(c, 4)
        diff_var += c ** 2
    cool_sigma = math.sqrt(diff_var)

    return CoolingResult(
        baseline=baseline,
        intervention=intervention,
        cooling_c=round(cooling, 2),
        cooling_uncertainty_c=round(cool_sigma, 2),
        cooling_95_low=round(cooling - 1.96 * cool_sigma, 2),
        cooling_95_high=round(cooling + 1.96 * cool_sigma, 2),
        differential_error_budget=diff_budget,
    )


# ── CLI demo ────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 70)
    print("  Delhi baseline vs green-roof intervention  (June 1, 2 pm)")
    print("=" * 70)

    result = compare_scenarios(
        T_air=42.0, rh=35.0, wind=2.5,
        day_of_year=152, hour_local=14.0,
        baseline_albedo=0.15, baseline_veg=0.02, baseline_urban=0.85,
        intervention_albedo=0.45, intervention_veg=0.25, intervention_urban=0.85,
    )

    print(f"\n  Baseline surface temp : {result.baseline.surface_temp_c:.2f} "
          f"± {result.baseline.uncertainty_c:.2f} °C")
    print(f"  Intervention surface  : {result.intervention.surface_temp_c:.2f} "
          f"± {result.intervention.uncertainty_c:.2f} °C")
    print(f"  Cooling effect        : {result.cooling_c:.2f} "
          f"± {result.cooling_uncertainty_c:.2f} °C "
          f"(95 %: {result.cooling_95_low:.2f} – {result.cooling_95_high:.2f} °C)")

    print("\n── Baseline math breakdown ──")
    for s in result.baseline.steps:
        print(f"\n  Step {s.step}: {s.name}")
        print(f"  Equation: {s.equation}")
        print(f"  {s.substitution}")
        print(f"  → {s.result} {s.unit}  (±{s.uncertainty})")
        print(f"  {s.explanation}")

    print("\n── Error budget (differential) ──")
    for src, c in sorted(result.differential_error_budget.items(), key=lambda x: -x[1]):
        print(f"  {src:>12s} : ± {c:.3f} °C")
