import { expect, test } from '@playwright/test';

const planningOptions = {
  cities: ['Delhi', 'Maharashtra'],
  seasons: [{ value: 'summer', start: '2023-05-01', end: '2023-08-30' }],
  feasibility_masks: [
    { value: 'mixed', veg_multiplier: 1.0, albedo_multiplier: 1.0, feasibility: 0.75, cost_index: 0.65 },
    { value: 'roofs_only', veg_multiplier: 0.45, albedo_multiplier: 1.15, feasibility: 0.82, cost_index: 0.55 },
    { value: 'open_land_only', veg_multiplier: 1.2, albedo_multiplier: 0.25, feasibility: 0.88, cost_index: 0.5 },
  ],
  equity_overlays: [
    { value: 'none', label: 'No equity weighting', default_weight: 0.0 },
    { value: 'heat_exposure_proxy', label: 'Heat exposure proxy', default_weight: 0.35 },
  ],
};

const observationPayload = {
  tile_url: 'https://example.com/mock-tiles/{z}/{x}/{y}.png',
  metadata: {
    dataset_name: 'LANDSAT/LC08/C02/T1_L2',
    temporal_window: { start: '2023-05-01', end: '2023-08-30' },
    nominal_resolution_m: 30,
    methodology: 'Mock observation composite for browser verification.',
    scientific_status: 'observational_composite',
    season: 'summer',
    caveats: ['Mocked in Playwright'],
  },
};

const comparisonPayload = {
  left: {
    label: 'Greening Focus',
    original_area_sqm: 125000,
    veg_effect_celsius: 0.9,
    albedo_effect_celsius: 0.6,
    predicted_temp_drop_celsius: 0.78,
    estimated_ac_energy_saved_kwh: null,
    best_strategy: 'Public-data surrogate scenario',
    planning_scores: { feasibility_score: 0.74, cost_index: 0.55, equity_score: 0.42 },
    metadata: {
      scientific_status: 'public_data_surrogate_model',
      calibration_status: 'validated_against_held_out_public_observations',
      intended_use: 'comparative urban-heat screening with free public geospatial data',
      not_intended_for: [],
      uncertainty: {
        type: 'held_out_residual_quantiles',
        summary: 'Held-out residual envelope from the trained gradient_boosting surrogate. Observed benchmark performance: MAE 1.37 C, RMSE 1.69 C.',
        bounds_celsius: { lower_celsius: -1.14, upper_celsius: 3.14 },
      },
      warnings: [
        'This model is trained on free public remote-sensing predictors and held-out public observations, not local station or municipal calibration data.',
        'Energy savings remain unavailable because no local building-energy calibration has been completed.',
        "Feasibility mask 'open_land_only' is adjusted with public land-cover and settlement proxies, not parcel inventories.",
      ],
      feasibility_mask: 'open_land_only',
      feasibility_profile: { feasibility: 0.88, cost_index: 0.5, veg_multiplier: 1.2, albedo_multiplier: 0.25 },
      model_evidence: {
        model_name: 'gradient_boosting',
        metrics: { mae: 1.374761119798559, rmse: 1.6886520535579972, bias: 0.383183707085045, n_test: 50 },
        residual_quantiles: { q10: -1.9229678130343721, q90: 2.3631696436294183 },
      },
    },
  },
  right: {
    label: 'Cool Roof Focus',
    original_area_sqm: 125000,
    veg_effect_celsius: 0.4,
    albedo_effect_celsius: 0.7,
    predicted_temp_drop_celsius: 0.52,
    estimated_ac_energy_saved_kwh: null,
    best_strategy: 'Public-data surrogate scenario',
    planning_scores: { feasibility_score: 0.81, cost_index: 0.45, equity_score: 0.39 },
    metadata: {
      scientific_status: 'public_data_surrogate_model',
      calibration_status: 'validated_against_held_out_public_observations',
      intended_use: 'comparative urban-heat screening with free public geospatial data',
      not_intended_for: [],
      uncertainty: {
        type: 'held_out_residual_quantiles',
        summary: 'Held-out residual envelope from the trained gradient_boosting surrogate. Observed benchmark performance: MAE 1.37 C, RMSE 1.69 C.',
        bounds_celsius: { lower_celsius: -1.40, upper_celsius: 2.88 },
      },
      warnings: [
        'This model is trained on free public remote-sensing predictors and held-out public observations, not local station or municipal calibration data.',
        'Energy savings remain unavailable because no local building-energy calibration has been completed.',
        "Feasibility mask 'roofs_only' is adjusted with public land-cover and settlement proxies, not parcel inventories.",
      ],
      feasibility_mask: 'roofs_only',
      feasibility_profile: { feasibility: 0.82, cost_index: 0.55, veg_multiplier: 0.45, albedo_multiplier: 1.15 },
      model_evidence: {
        model_name: 'gradient_boosting',
        metrics: { mae: 1.374761119798559, rmse: 1.6886520535579972, bias: 0.383183707085045, n_test: 50 },
        residual_quantiles: { q10: -1.9229678130343721, q90: 2.3631696436294183 },
      },
    },
  },
  comparison: {
    delta_temp_drop_celsius: 0.26,
    higher_feasibility_label: 'Cool Roof Focus',
  },
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/planning-options', async (route) => {
    await route.fulfill({ json: planningOptions });
  });
  await page.route('**/api/health', async (route) => {
    await route.fulfill({
      json: {
        status: 'ok',
        earth_engine: { configured: true, initialized: true, detail: null },
        trained_surrogate: {
          available: true,
          detail: {
            model_name: 'gradient_boosting',
            metrics: { mae: 1.374761119798559, rmse: 1.6886520535579972, bias: 0.383183707085045, n_test: 50 },
          },
        },
        default_city_boundary_source: 'FAO/GAUL ADM1',
        known_limitations: [],
      },
    });
  });
  await page.route('**/api/get-lst-tiles**', async (route) => {
    await route.fulfill({ json: observationPayload });
  });
  await page.route('**/api/simulate-cooling-compare', async (route) => {
    await route.fulfill({ json: comparisonPayload });
  });
  await page.route('**/api/priority-ranking', async (route) => {
    await route.fulfill({
      json: {
        rankings: [
          {
            label: 'Greening Focus',
            priority_score: 1.81,
            impact_celsius: 0.78,
            feasibility_score: 0.74,
            equity_score: 0.42,
            cost_index: 0.55,
            feasibility_mask: 'open_land_only',
          },
          {
            label: 'Cool Roof Focus',
            priority_score: 1.74,
            impact_celsius: 0.52,
            feasibility_score: 0.81,
            equity_score: 0.39,
            cost_index: 0.45,
            feasibility_mask: 'roofs_only',
          },
        ],
      },
    });
  });
  await page.route('**/api/zonal-summary', async (route) => {
    await route.fulfill({
      json: {
        zones: [
          {
            name: 'Selected polygon',
            area_sqm: 125000,
            predicted_temp_drop_celsius: 0.78,
            feasibility_score: 0.74,
            best_strategy: 'Public-data surrogate scenario',
          },
        ],
      },
    });
  });
  await page.route('**/api/export-report', async (route) => {
    await route.fulfill({
      json: {
        markdown: '# Mock report',
        json_payload: { ok: true },
      },
    });
  });
});

test('planner console renders controls and exports the map bundle in browser mode', async ({ page }) => {
  await page.goto('/');

  await expect(page.getByText('Urban Heat Planning Console')).toBeVisible();
  await expect(page.getByText('Observation layer')).toBeVisible();
  await expect(page.getByText('Planner controls')).toBeVisible();
  await expect(page.getByText('System status')).toBeVisible();
  const combos = page.locator('select');
  await combos.nth(0).selectOption('winter');
  await combos.nth(1).selectOption('night');
  await combos.nth(2).selectOption('light');

  await page.getByTestId('export-map-button').click();
  await expect(page.getByTestId('export-map-button')).toBeVisible();
});
