# Calibration Workflow

This folder defines the path from prototype heuristics to a validated local model.

## Goal

Calibrate intervention and temperature models using local station, field, or municipal data.

## Required inputs

1. `station_observations.csv`
2. `field_campaign_observations.csv`
3. `municipal_assets.geojson`
4. `calibration_config.json`

Templates are provided in `templates/`.

## Recommended workflow

1. Ingest local measurements into the provided schemas.
2. Join observations to raster predictors and municipal context variables.
3. Split train/validation/test by space and time.
4. Train transparent baseline models first.
5. Publish metrics, uncertainty, and known failure cases.
6. Only then replace the placeholder scenario model in production.

## Suggested predictor groups

- Baseline LST
- NDVI / vegetation indices
- Built-up / impervious indicators
- Albedo proxy variables
- Building density and height
- Tree canopy
- Weather context at timestamp
- Land use and zoning

## Deliverables before a planning-grade claim

- Data dictionary
- Calibration script or notebook
- Evaluation report
- Model artifact version
- Approval log for assumptions and exclusions
