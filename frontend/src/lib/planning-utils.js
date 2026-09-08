// ── Fallback planning options ─────────────────────────────────────────────────
// These are used when the backend is unreachable.
// Season dates use the most recently completed planning year (dynamically computed).
const FALLBACK_PLANNING_YEAR = (() => {
  const now = new Date();
  return now.getMonth() >= 8 ? now.getFullYear() : now.getFullYear() - 1;
})();

const FY = FALLBACK_PLANNING_YEAR;
const FY1 = FY + 1;

export const FALLBACK_PLANNING_OPTIONS = {
  cities: ['Delhi', 'Maharashtra', 'Karnataka', 'Tamil Nadu', 'Telangana', 'West Bengal', 'Gujarat', 'Rajasthan'],
  city_entries: [
    {
      value: 'Delhi',
      label: 'Delhi',
      boundary_source: 'FAO/GAUL ADM1',
      boundary_note: 'Administrative proxy for the National Capital Territory; upload exact wards or neighborhoods for finer local analysis.',
    },
    {
      value: 'Maharashtra',
      label: 'Maharashtra',
      boundary_source: 'FAO/GAUL ADM1',
      boundary_note: 'State-scale administrative proxy.',
    },
    {
      value: 'Karnataka',
      label: 'Karnataka',
      boundary_source: 'FAO/GAUL ADM1',
      boundary_note: 'State-scale administrative proxy.',
    },
    {
      value: 'Tamil Nadu',
      label: 'Tamil Nadu',
      boundary_source: 'FAO/GAUL ADM1',
      boundary_note: 'State-scale administrative proxy.',
    },
    {
      value: 'Telangana',
      label: 'Telangana',
      boundary_source: 'FAO/GAUL ADM1',
      boundary_note: 'State-scale administrative proxy.',
    },
    {
      value: 'West Bengal',
      label: 'West Bengal',
      boundary_source: 'FAO/GAUL ADM1',
      boundary_note: 'State-scale administrative proxy.',
    },
    {
      value: 'Gujarat',
      label: 'Gujarat',
      boundary_source: 'FAO/GAUL ADM1',
      boundary_note: 'State-scale administrative proxy.',
    },
    {
      value: 'Rajasthan',
      label: 'Rajasthan',
      boundary_source: 'FAO/GAUL ADM1',
      boundary_note: 'State-scale administrative proxy.',
    },
  ],
  seasons: [
    { value: 'summer',       start: `${FY}-05-01`,  end: `${FY}-08-30`  },
    { value: 'monsoon',      start: `${FY}-07-01`,  end: `${FY}-09-30`  },
    { value: 'pre_monsoon',  start: `${FY}-03-01`,  end: `${FY}-05-31`  },
    { value: 'post_monsoon', start: `${FY}-10-01`,  end: `${FY}-11-30`  },
    { value: 'winter',       start: `${FY}-12-01`,  end: `${FY1}-02-28` },
  ],
  feasibility_masks: [
    { value: 'mixed',         veg_multiplier: 1.0,  albedo_multiplier: 1.0,  feasibility: 0.75, cost_index: 0.65 },
    { value: 'roofs_only',    veg_multiplier: 0.45, albedo_multiplier: 1.15, feasibility: 0.82, cost_index: 0.55 },
    { value: 'roads_only',    veg_multiplier: 0.30, albedo_multiplier: 0.85, feasibility: 0.48, cost_index: 0.72 },
    { value: 'open_land_only',veg_multiplier: 1.20, albedo_multiplier: 0.25, feasibility: 0.88, cost_index: 0.50 },
  ],
  equity_overlays: [
    { value: 'none',                           label: 'No equity weighting',              default_weight: 0.00 },
    { value: 'heat_exposure_proxy',            label: 'Heat exposure proxy',              default_weight: 0.35 },
    { value: 'population_vulnerability_proxy', label: 'Population vulnerability proxy',   default_weight: 0.45 },
    { value: 'elderly_sensitivity_proxy',      label: 'Older adult sensitivity proxy',    default_weight: 0.40 },
    { value: 'economic_deprivation_proxy',     label: 'Economic deprivation proxy (VIIRS)',default_weight: 0.40 },
  ],
};

// ── Observation query helpers ─────────────────────────────────────────────────

export function buildObservationQueryString({ cityName, season, customStartDate, customEndDate }) {
  const params = new URLSearchParams({ city_name: cityName, season });
  if (customStartDate && customEndDate) {
    params.set('start_date', customStartDate);
    params.set('end_date', customEndDate);
  }
  return params.toString();
}

// ── Zone upload parser ────────────────────────────────────────────────────────

export function parseZoneUploadText(rawText) {
  let parsed;
  try {
    parsed = JSON.parse(rawText);
  } catch {
    throw new Error('File is not valid JSON. Supply a GeoJSON FeatureCollection.');
  }
  if (parsed.type !== 'FeatureCollection' || !Array.isArray(parsed.features)) {
    throw new Error('Expected a GeoJSON FeatureCollection with a "features" array.');
  }
  const zones = parsed.features.filter(
    (feature) =>
      feature.geometry?.type === 'Polygon' || feature.geometry?.type === 'MultiPolygon',
  );
  if (zones.length === 0) {
    throw new Error('No Polygon or MultiPolygon features were found in the uploaded file.');
  }
  return zones;
}

// ── Map bundle export ─────────────────────────────────────────────────────────

export function buildMapBundle(payload) {
  return JSON.stringify(
    {
      ...payload,
      exported_at: new Date().toISOString(),
      planning_tool: 'Urban Heat Island Prototype',
    },
    null,
    2,
  );
}

// ── API error classification ──────────────────────────────────────────────────
// Matches backend HTTP error messages and network failures to user-friendly labels.

export function classifyApiError(message) {
  const normalized = String(message ?? '').toLowerCase();
  if (
    normalized.includes('failed to fetch') ||
    normalized.includes('networkerror') ||
    normalized.includes('load failed') ||
    normalized.includes('econnrefused')
  ) {
    return {
      title: 'Backend offline',
      detail: 'The frontend cannot reach the FastAPI backend. Check that the server is running and NEXT_PUBLIC_API_URL is correct.',
    };
  }
  if (normalized.includes('earth engine')) {
    return {
      title: 'Earth Engine unavailable',
      detail: 'The backend is up, but Google Earth Engine could not initialize or process the request. Check EE_PROJECT_ID.',
    };
  }
  if (normalized.includes('no fao/gaul adm1 match') || normalized.includes('boundary not found')) {
    return {
      title: 'Boundary not found',
      detail: 'The entered place name does not match the current ADM1 boundary catalog. Check the exact spelling.',
    };
  }
  if (normalized.includes('invalid date')) {
    return {
      title: 'Invalid date',
      detail: 'Use YYYY-MM-DD format in the custom date fields.',
    };
  }
  if (normalized.includes('end_date must be on or after')) {
    return {
      title: 'Date range error',
      detail: 'The end date cannot be before the start date.',
    };
  }
  if (normalized.includes('no nighttime ecostress passes')) {
    return {
      title: 'No ECOSTRESS data',
      detail: 'No nighttime ECOSTRESS passes are available for the selected city and time window. Try a different date range or switch to exploratory night mode.',
    };
  }
  return {
    title: 'Request failed',
    detail: message ?? 'The request did not complete successfully.',
  };
}

// ── Scientific status formatter ───────────────────────────────────────────────

export function formatScientificStatus(status) {
  return String(status ?? 'unknown').replaceAll('_', ' ');
}
