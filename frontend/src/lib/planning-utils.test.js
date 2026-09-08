import assert from 'node:assert/strict';
import { buildMapBundle, buildObservationQueryString, parseZoneUploadText } from './planning-utils.js';

function run() {
  const query = buildObservationQueryString({
    cityName: 'Delhi',
    season: 'summer',
    customStartDate: '2024-05-01',
    customEndDate: '2024-05-31',
  });
  assert.match(query, /city_name=Delhi/);
  assert.match(query, /start_date=2024-05-01/);
  assert.match(query, /end_date=2024-05-31/);

  const zones = parseZoneUploadText(
    JSON.stringify({
      type: 'FeatureCollection',
      features: [
        {
          type: 'Feature',
          properties: { name: 'Zone 1' },
          geometry: {
            type: 'Polygon',
            coordinates: [[[77, 28], [77.1, 28], [77.1, 28.1], [77, 28.1], [77, 28]]],
          },
        },
      ],
    }),
  );
  assert.equal(zones.length, 1);
  assert.equal(zones[0].properties.name, 'Zone 1');

  const content = buildMapBundle({ cityName: 'Delhi', datasetMode: 'day' });
  assert.match(content, /Delhi/);
  assert.match(content, /day/);

  console.log('planning-utils tests passed');
}

run();
