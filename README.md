# Urban Heat Planning Prototype

This repository is now structured as a free-data-first planning prototype for urban heat analysis and scenario screening.

Today it provides three distinct capabilities:

1. Observed land-surface-temperature composites served from Google Earth Engine.
2. A trained public-data surrogate model for comparing vegetation and cool-roof interventions with area, seasonal-window, and optional weather-context adjustments.
3. Planner-facing endpoints for scenario comparison, zonal summaries, priority ranking, and report export.

The current scenario model is still not locally validated or planning-grade. It is now backed by a held-out public-data benchmark, but it has not yet been calibrated against local station, field, or municipal data.

## Current scientific status

- Observed rasters: real remote-sensing products with parameterized dates and seasons.
- Scenario estimates: trained free-data surrogate outputs for comparative screening.
- Building-energy savings: intentionally withheld until calibration is complete.
- Frontend browser verification: Playwright e2e coverage for planning console flows.

## Repository priorities

- `main.py`: FastAPI backend for observed raster tiles, planning endpoints, and scenario responses.
- `models/cooling_model.py`: heuristic fallback model retained for offline or pre-artifact use.
- `models/public_data_surrogate.py`: trained public-data surrogate model used when the artifact is present.
- `frontend/`: Next.js client for map interaction and scenario exploration.
- `docs/`: model card, data lineage, and scientific limitations.
- `calibration/`: local-data ingestion templates and calibration workflow scaffolding.
- `scripts/`: reproducible preprocessing, baseline training, and evaluation entry points.

## Free-data standard

The preferred source strategy is:

- legally usable
- documented
- auditable
- current enough for exploratory planning
- accurate enough for the decision class
- consistent with procurement and privacy expectations

See [docs/FREE_DATA_STACK.md](docs/FREE_DATA_STACK.md), [docs/ASSUMPTIONS.md](docs/ASSUMPTIONS.md), and [docs/GOVERNANCE_CHECKLIST.md](docs/GOVERNANCE_CHECKLIST.md).

## Public-data benchmark status

- Training dataset: `calibration/derived_feature_matrix.csv`
- Validation strategy: grouped temporal holdout by sampled window
- Selected model: `gradient_boosting`
- Hold-out metrics:
  - `MAE = 6.485 C`
  - `RMSE = 6.972 C`
  - `Bias = 6.485 C`
  - residual quantiles `Q10 = 3.747 C`, `Q90 = 9.888 C`

These metrics come from held-out public observations and support comparative screening, not municipal sign-off. They also show why local calibration is still the highest-value next step.

## Near-term roadmap

1. Ingest local station, field, and municipal datasets into the schemas in `calibration/`.
2. Extend the benchmark with spatial holdouts and additional cities.
3. Replace the public-data surrogate with a locally calibrated model.
4. Add parcel-grade feasibility inventories and official equity layers.

## Local calibration requirements

To complete the "best" path, we need at least one of the following:

- Station observations with timestamp, latitude, longitude, and temperature.
- Field campaign measurements for surface or near-surface heat.
- Municipal layers for land cover, tree canopy, roof characteristics, and building stock.

See [docs/MODEL_CARD.md](docs/MODEL_CARD.md), [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md), and [calibration/README.md](calibration/README.md).

## Vercel deployment

Use two Vercel projects from the same repository:

1. Backend project
   Root directory: repository root
2. Frontend project
   Root directory: `frontend`

### Backend environment variables

- `EE_PROJECT_ID`
- `ALLOWED_ORIGINS=https://<your-frontend-domain>`

### Frontend environment variables

- `NEXT_PUBLIC_API_URL=https://<your-backend-domain>`
- `NEXT_PUBLIC_MAPBOX_ACCESS_TOKEN=<your-public-mapbox-token>`

### Local development

- Run the FastAPI app on port `8000`.
- Run the Next.js app from `frontend/`.
- In development, `frontend/next.config.ts` rewrites `/api/*` to `http://127.0.0.1:8000/api/*`.
