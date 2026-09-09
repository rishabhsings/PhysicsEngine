'use client';

import React, { useState, useCallback, useEffect, useRef } from 'react';
import Map, { Layer, Source, useControl } from 'react-map-gl/mapbox';
import 'mapbox-gl/dist/mapbox-gl.css';
import MapboxDraw from '@mapbox/mapbox-gl-draw';
import { drawStyles } from './drawStyles';
import '@mapbox/mapbox-gl-draw/dist/mapbox-gl-draw.css';
import { area } from '@turf/area';
import center from '@turf/center';

// ── Configuration ───────────────────────────────────────────────────────────
const getApiBase = () => {
  const configuredBase = process.env.NEXT_PUBLIC_API_URL?.trim();

  if (!configuredBase) {
    return '';
  }

  if (
    typeof window !== 'undefined' &&
    window.location.hostname === 'localhost' &&
    /^https?:\/\/(localhost|127\.0\.0\.1)(:\d+)?$/i.test(configuredBase)
  ) {
    return '';
  }

  return configuredBase.replace(/\/+$/, '');
};
const MAPBOX_TOKEN = process.env.NEXT_PUBLIC_MAPBOX_ACCESS_TOKEN || '';

const INITIAL_VIEW_STATE = {
  longitude: 77.1025,
  latitude: 28.7041,
  zoom: 13,
  pitch: 45,
  bearing: 15,
};

const THERMAL_DETAIL_MIN_ZOOM = 10;

type ThermalTileUrls = {
  detail: string | null;
  overview: string | null;
};

type BackendStatus = {
  message: string;
  kind: 'tiles' | 'simulation';
} | null;

// ── DeckGL Overlay Hook ──────────────────────────────────────────────────────
function DrawControl({ drawRef, position, onUpdate, ...drawOptions }: any) {
  const draw = useControl(
    () => new MapboxDraw(drawOptions) as any,
    ({ map }: any) => {
      map.on('draw.create', onUpdate);
      map.on('draw.update', onUpdate);
      map.on('draw.delete', onUpdate);
    },
    ({ map }: any) => {
      map.off('draw.create', onUpdate);
      map.off('draw.update', onUpdate);
      map.off('draw.delete', onUpdate);
    },
    { position: position || 'top-right' }
  );

  useEffect(() => {
    if (drawRef) {
      drawRef.current = draw;
    }
  }, [draw, drawRef]);

  return null;
}

type MaskMode = 'Auto-Detect' | 'Roof Focus' | 'Open Land Focus';
type ScenarioState = {
  targetVeg: number;
  targetAlbedo: number;
  maskMode: MaskMode;
  highDeprivation: boolean;
  highElderly: boolean;
  ambientTemp: number;
  humidity: number;
  windSpeed: number;
  polygonFeature: any;
  zoneArea: number | null;
};

type MathStep = {
  step: number;
  name: string;
  equation: string;
  calculation: string;
  result: number;
  unit: string;
  uncertainty: string | null;
  explanation: string;
};

const asNumber = (value: unknown): number | null => (
  typeof value === 'number' && Number.isFinite(value) ? value : null
);

const formatFixed = (value: unknown, digits = 2): string => {
  const numericValue = asNumber(value);
  return numericValue === null ? 'N/A' : numericValue.toFixed(digits);
};

export default function Home() {
  const apiBase = getApiBase();
  const [activeTab, setActiveTab] = useState<'A' | 'B'>('A');
  const [scenarioA, setScenarioA] = useState<ScenarioState>({ targetVeg: 0.30, targetAlbedo: 0.45, maskMode: 'Auto-Detect', highDeprivation: false, highElderly: false, ambientTemp: 42, humidity: 35, windSpeed: 2.5, polygonFeature: null, zoneArea: null });
  const [scenarioB, setScenarioB] = useState<ScenarioState>({ targetVeg: 0.50, targetAlbedo: 0.60, maskMode: 'Auto-Detect', highDeprivation: false, highElderly: false, ambientTemp: 42, humidity: 35, windSpeed: 2.5, polygonFeature: null, zoneArea: null });
  const [showMath, setShowMath] = useState<'A' | 'B' | null>(null);

  const currentScenario = activeTab === 'A' ? scenarioA : scenarioB;
  const setCurrentScenario = (updates: any) => {
    if (activeTab === 'A') setScenarioA(prev => ({ ...prev, ...updates }));
    else setScenarioB(prev => ({ ...prev, ...updates }));
  };

  const [loading, setLoading] = useState(false);
  const [results, setResults] = useState<{ A: any, B: any } | null>(null);
  const [thermalTileUrls, setThermalTileUrls] = useState<ThermalTileUrls>({
    detail: null,
    overview: null
  });
  const [backendStatus, setBackendStatus] = useState<BackendStatus>(null);
  const [isDarkMode, setIsDarkMode] = useState(true);
  const mapRef = useRef<any>(null);
  const drawRef = useRef<any>(null);
  const [drawMode, setDrawMode] = useState(false);
  const [heatmapOpacity, setHeatmapOpacity] = useState(0.85);
  const activeTabRef = useRef(activeTab);
  useEffect(() => { activeTabRef.current = activeTab; }, [activeTab]);
  useEffect(() => {
    if (drawRef.current) {
      drawRef.current.deleteAll();
      const currentPoly = activeTab === 'A' ? scenarioA.polygonFeature : scenarioB.polygonFeature;
      if (currentPoly) drawRef.current.add(currentPoly);
    }
  }, [activeTab]);

  // Sync Mapbox Light Preset with state
  useEffect(() => {
    if (mapRef.current) {
      try {
        mapRef.current.setConfigProperty('basemap', 'lightPreset', isDarkMode ? 'night' : 'day');
      } catch (err) {
        console.warn("Mapbox Config property not ready yet", err);
      }
    }
  }, [isDarkMode]);

  const isRoofFocus = currentScenario.maskMode === 'Roof Focus' || (currentScenario.maskMode === 'Auto-Detect' && results?.[activeTab]?.is_built_up);
  const maxVeg = isRoofFocus ? 0.15 : 1;

  useEffect(() => {
    if (currentScenario.targetVeg > maxVeg) setCurrentScenario({ targetVeg: maxVeg });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [maxVeg, currentScenario.targetVeg, activeTab]);

  // 1. Robust Fetching for GEE Thermal Tile
  useEffect(() => {
    const fetchTiles = async () => {
      try {
        const response = await fetch(`${apiBase}/api/map-id/Delhi`);
        if (!response.ok) throw new Error(`HTTP Error: ${response.status}`);
        const data = await response.json();
        const toProxyTileUrl = (tileUrl: string | null | undefined) => {
          if (!tileUrl) return null;
          const url = new URL(tileUrl);
          const rawPath = url.origin + url.pathname + url.search;
          const cleanPath = rawPath
            .replace(/{z}/g, '__Z__')
            .replace(/{x}/g, '__X__')
            .replace(/{y}/g, '__Y__')
            .replace(/%7Bz%7D/gi, '__Z__')
            .replace(/%7Bx%7D/gi, '__X__')
            .replace(/%7By%7D/gi, '__Y__');
          const proxyPath = `/api/tiles/${encodeURIComponent(cleanPath)}`;
          return proxyPath
            .replace(/__Z__/g, '{z}')
            .replace(/__X__/g, '{x}')
            .replace(/__Y__/g, '{y}');
        };

        setThermalTileUrls({
          detail: toProxyTileUrl(data.detail_tile_url ?? data.tile_url ?? null),
          overview: toProxyTileUrl(data.overview_tile_url ?? data.tile_url ?? null)
        });
        setBackendStatus(null);
      } catch (err) {
        setThermalTileUrls({
          detail: null,
          overview: null
        });
        setBackendStatus({
          kind: 'tiles',
          message: 'Thermal Map Disabled: The backend is connected, but Google Earth Engine is not authenticated on Render. Add EE_SERVICE_ACCOUNT and EE_PRIVATE_KEY_JSON to Render to enable satellite imagery.'
        });
        console.warn('Thermal tile fetch failed.', err);
      }
    };
    fetchTiles();
  }, [apiBase]);

  const onDrawUpdate = useCallback((e: any) => {
    if (!drawRef.current) return;
    const data = drawRef.current.getAll();
    setResults(null); // Clear previous results when modifying drawing

    let newFeature = null;
    let newArea = null;

    if (data.features.length > 1) {
      const latest = data.features[data.features.length - 1];
      drawRef.current.deleteAll();
      drawRef.current.add(latest);
      newFeature = latest;
      newArea = area(latest);
    } else if (data.features.length === 1) {
      newFeature = data.features[0];
      newArea = area(data.features[0]);
    }

    if (activeTabRef.current === 'A') {
      setScenarioA(prev => ({ ...prev, polygonFeature: newFeature, zoneArea: newArea }));
    } else {
      setScenarioB(prev => ({ ...prev, polygonFeature: newFeature, zoneArea: newArea }));
    }

    if (e.type === 'draw.create') {
      setDrawMode(false);
    }
  }, []);

  const toggleDrawMode = () => {
    if (!drawRef.current) return;
    if (!drawMode) {
      drawRef.current.changeMode('draw_polygon');
      setDrawMode(true);
    } else {
      drawRef.current.changeMode('simple_select');
      setDrawMode(false);
    }
  };

  const handleSimulate = useCallback(async () => {
    setLoading(true);
    try {
      const runScenario = async (label: string, sc: any) => {
        const res = await fetch(`${apiBase}/api/simulate-cooling`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            label,
            polygon: sc.polygonFeature || scenarioA.polygonFeature,
            target_vegetation: sc.targetVeg,
            target_albedo: sc.targetAlbedo,
            high_economic_deprivation: sc.highDeprivation,
            high_elderly_population: sc.highElderly,
            ambient_temp: sc.ambientTemp,
            humidity: sc.humidity,
            wind_speed: sc.windSpeed,
            zone_area_m2: sc.zoneArea !== null ? Math.round(sc.zoneArea) : (scenarioA.zoneArea !== null ? Math.round(scenarioA.zoneArea) : 1250000)
          }),
        });
        const payload = await res.json();
        if (!res.ok) {
          throw new Error(payload?.detail ?? `Simulation failed with status ${res.status}`);
        }
        return payload;
      }

      const [resA, resB] = await Promise.all([
        runScenario('Scenario A', scenarioA),
        runScenario('Scenario B', scenarioB)
      ]);
      setBackendStatus(null);
      setResults({ A: resA, B: resB });
    } catch (err) {
      setBackendStatus({
        kind: 'simulation',
        message: 'Simulation service is unavailable. Make sure the FastAPI backend is running and reachable from the frontend.'
      });
      console.warn('Scenario simulation failed.', err);
      setResults(null);
    } finally {
      setLoading(false);
    }
  }, [apiBase, scenarioA, scenarioB]);

  return (
    <main className="relative h-screen w-screen overflow-hidden bg-slate-950 text-white font-sans">
      <div className="absolute inset-0">
        <Map
          initialViewState={INITIAL_VIEW_STATE}
          mapStyle="mapbox://styles/mapbox/standard"
          mapboxAccessToken={MAPBOX_TOKEN}
          maxPitch={85}
          onLoad={(e: any) => {
            const map = e.target;
            mapRef.current = map;
            map.setConfigProperty('basemap', 'lightPreset', isDarkMode ? 'night' : 'day');
          }}
        >
          {thermalTileUrls.overview && (
            <Source
              id="gee-thermal-overview-source"
              type="raster"
              tiles={[thermalTileUrls.overview]}
              tileSize={256}
              maxzoom={THERMAL_DETAIL_MIN_ZOOM}
            >
              <Layer
                id="gee-thermal-overview-layer"
                type="raster"
                maxzoom={THERMAL_DETAIL_MIN_ZOOM}
                paint={{
                  'raster-opacity': heatmapOpacity,
                  'raster-resampling': 'nearest',
                  'raster-fade-duration': 0
                }}
              />
            </Source>
          )}
          {thermalTileUrls.detail && (
            <Source
              id="gee-thermal-detail-source"
              type="raster"
              tiles={[thermalTileUrls.detail]}
              tileSize={256}
              minzoom={THERMAL_DETAIL_MIN_ZOOM}
            >
              <Layer
                id="gee-thermal-detail-layer"
                type="raster"
                minzoom={THERMAL_DETAIL_MIN_ZOOM}
                paint={{
                  'raster-opacity': heatmapOpacity,
                  'raster-resampling': 'nearest',
                  'raster-fade-duration': 0
                }}
              />
            </Source>
          )}
          {(() => {
            const inactivePoly = activeTab === 'A' ? scenarioB.polygonFeature : scenarioA.polygonFeature;
            if (!inactivePoly) return null;
            return (
              <Source id="inactive-polygon" type="geojson" data={inactivePoly}>
                <Layer
                  id="inactive-polygon-fill"
                  type="fill"
                  paint={{ 'fill-color': '#3b82f6', 'fill-opacity': 0.6 }}
                />
                <Layer
                  id="inactive-polygon-stroke"
                  type="line"
                  paint={{ 'line-color': '#3b82f6', 'line-width': 2, 'line-dasharray': [2, 2] }}
                />
              </Source>
            );
          })()}
          {(() => {
            const labels = [];
            if (scenarioA.polygonFeature) {
              const c = center(scenarioA.polygonFeature);
              labels.push({
                type: 'Feature',
                geometry: c.geometry,
                properties: {
                  title: 'SCENARIO A',
                  color: activeTab === 'A' ? '#16a34a' : '#3b82f6'
                }
              });
            }
            if (scenarioB.polygonFeature) {
              const c = center(scenarioB.polygonFeature);
              labels.push({
                type: 'Feature',
                geometry: c.geometry,
                properties: {
                  title: 'SCENARIO B',
                  color: activeTab === 'B' ? '#16a34a' : '#3b82f6'
                }
              });
            }
            if (labels.length === 0) return null;
            return (
              <Source id="scenario-labels-source" type="geojson" data={{ type: 'FeatureCollection', features: labels } as any}>
                <Layer
                  id="scenario-labels-layer"
                  type="symbol"
                  layout={{
                    'text-field': ['get', 'title'],
                    'text-size': 24,
                    'text-pitch-alignment': 'viewport',
                    'text-rotation-alignment': 'viewport',
                    'text-offset': [0, -1],
                    'text-allow-overlap': true
                  }}
                  paint={{
                    'text-color': ['get', 'color'],
                    'text-halo-color': 'rgba(255, 255, 255, 0.9)',
                    'text-halo-width': 3
                  }}
                />
              </Source>
            );
          })()}
          <DrawControl
            position="top-right"
            displayControlsDefault={false}
            styles={drawStyles}
            controls={{
              polygon: true,
              trash: true
            }}
            onUpdate={onDrawUpdate}
            drawRef={drawRef}
          />
        </Map>
      </div>

      <div className={`absolute left-6 bottom-6 z-10 flex flex-col gap-6 w-80 rounded-2xl p-6 backdrop-blur-xl shadow-2xl border transition-all duration-500 ${isDarkMode ? 'bg-slate-900/40 border-white/10 text-white' : 'bg-white/80 border-slate-200 text-slate-950'
        }`}>
        <div>
          <div className="flex items-center justify-between mb-1">
            <div className="flex items-center gap-2">
              <div className="w-2 h-2 rounded-full bg-emerald-500 animate-pulse" />
              <h1 className="text-xl font-bold tracking-tight">Delhi Digital Twin</h1>
            </div>
            <button
              onClick={() => setIsDarkMode(!isDarkMode)}
              className={`p-1.5 rounded-lg border transition-all ${isDarkMode ? 'bg-slate-800 border-white/10 text-amber-400 hover:bg-slate-700' : 'bg-slate-100 border-slate-200 text-slate-600 hover:bg-slate-200'
                }`}
            >
              {isDarkMode ? (
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path d="M10 2a1 1 0 011 1v1a1 1 0 11-2 0V3a1 1 0 011-1zm4 8a4 4 0 11-8 0 4 4 0 018 0zm-.464 4.95l.707.707a1 1 0 001.414-1.414l-.707-.707a1 1 0 00-1.414 1.414zm2.12-10.607a1 1 0 010 1.414l-.706.707a1 1 0 11-1.414-1.414l.707-.707a1 1 0 011.414 0zM17 11a1 1 0 100-2h-1a1 1 0 100 2h1zm-7 4a1 1 0 011 1v1a1 1 0 11-2 0v-1a1 1 0 011-1zM5.05 6.464A1 1 0 106.465 5.05l-.708-.707a1 1 0 00-1.414 1.414l.707.707zm1.414 8.486l-.707.707a1 1 0 01-1.414-1.414l.707-.707a1 1 0 011.414 1.414zM4 11a1 1 0 100-2H3a1 1 0 000 2h1z" /></svg>
              ) : (
                <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path d="M17.293 13.293A8 8 0 016.707 2.707a8.001 8.001 0 1010.586 10.586z" /></svg>
              )}
            </button>
          </div>
          <p className={`text-xs mb-4 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Physics Energy Balance Engine v6.0</p>

          <div className="mb-4">
            <div className="flex justify-between items-center mb-1">
              <span className={`text-xs font-semibold ${isDarkMode ? 'text-slate-300' : 'text-slate-600'}`}>Heatmap Opacity</span>
              <span className={`text-xs ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>{Math.round(heatmapOpacity * 100)}%</span>
            </div>
            <input
              type="range"
              min="0" max="1" step="0.05"
              value={heatmapOpacity}
              onChange={(e) => setHeatmapOpacity(parseFloat(e.target.value))}
              className={`w-full accent-emerald-500 h-1.5 rounded-lg appearance-none cursor-pointer ${isDarkMode ? 'bg-slate-700' : 'bg-slate-200'}`}
            />
          </div>
        </div>

        <div className={`flex rounded-lg p-1 border ${isDarkMode ? 'bg-slate-800 border-white/5' : 'bg-slate-100 border-slate-200'}`}>
          <button onClick={() => setActiveTab('A')} className={`flex-1 py-1 text-[10px] font-bold uppercase rounded-md transition-all ${activeTab === 'A'
              ? (isDarkMode ? 'bg-slate-600 text-white shadow-sm' : 'bg-white text-slate-900 shadow-sm border border-slate-200')
              : (isDarkMode ? 'text-slate-400 hover:text-white' : 'text-slate-500 hover:text-slate-900')
            }`}>Scenario A</button>
          <button onClick={() => setActiveTab('B')} className={`flex-1 py-1 text-[10px] font-bold uppercase rounded-md transition-all ${activeTab === 'B'
              ? (isDarkMode ? 'bg-emerald-600 text-white shadow-sm' : 'bg-emerald-500 text-white shadow-sm')
              : (isDarkMode ? 'text-slate-400 hover:text-white' : 'text-slate-500 hover:text-slate-900')
            }`}>Scenario B</button>
        </div>

        <div className="space-y-4">
          <div>
            <label className={`text-[10px] uppercase tracking-wider font-bold mb-1 block ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Feasibility Mask</label>
            <select
              value={currentScenario.maskMode}
              onChange={(e) => {
                setCurrentScenario({ maskMode: e.target.value });
                setResults(null);
              }}
              className={`w-full text-xs p-2 rounded-lg outline-none border transition-all ${isDarkMode ? 'bg-slate-800 text-white border-slate-700' : 'bg-white text-slate-950 border-slate-200 shadow-sm'
                }`}
            >
              <option value="Auto-Detect">Auto-Detect</option>
              <option value="Roof Focus">Roof Focus</option>
              <option value="Open Land Focus">Open Land Focus</option>
            </select>
          </div>

          <div>
            <div className="flex justify-between mb-1">
              <label className={`text-[10px] uppercase tracking-wider font-bold ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Vegetation Fraction</label>
              <span className="text-xs font-mono font-bold text-emerald-500">{Math.round(currentScenario.targetVeg * 100)}%</span>
            </div>
            <input
              type="range" min="0" max={maxVeg} step="0.05"
              value={currentScenario.targetVeg}
              onChange={(e) => setCurrentScenario({ targetVeg: parseFloat(e.target.value) })}
              className={`w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-emerald-500 ${isDarkMode ? 'bg-slate-700' : 'bg-slate-200'}`}
            />
          </div>

          <div>
            <div className="flex justify-between mb-1">
              <label className={`text-[10px] uppercase tracking-wider font-bold ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Target Albedo</label>
              <span className="text-xs font-mono font-bold text-sky-500">{currentScenario.targetAlbedo.toFixed(2)}</span>
            </div>
            <input
              type="range" min="0" max="1" step="0.05"
              value={currentScenario.targetAlbedo}
              onChange={(e) => setCurrentScenario({ targetAlbedo: parseFloat(e.target.value) })}
              className={`w-full h-1.5 rounded-lg appearance-none cursor-pointer accent-sky-500 ${isDarkMode ? 'bg-slate-700' : 'bg-slate-200'} ${isRoofFocus ? 'ring-2 ring-sky-500 shadow-[0_0_10px_rgba(14,165,233,0.5)]' : ''}`}
            />
          </div>

          <div>
            <label className={`text-[10px] uppercase tracking-wider font-bold mb-2 block ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Equity & Vulnerability</label>
            <div className="flex flex-col gap-2">
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={currentScenario.highDeprivation}
                  onChange={(e) => setCurrentScenario({ highDeprivation: e.target.checked })}
                  className={`w-3.5 h-3.5 appearance-none rounded border checked:bg-amber-500 transition-all ${isDarkMode ? 'border-slate-600' : 'border-slate-300'}`}
                />
                <span className={`text-[10px] uppercase font-bold ${isDarkMode ? 'text-slate-300' : 'text-slate-600'}`}>High Economic Deprivation</span>
              </label>
              <label className="flex items-center gap-2 cursor-pointer">
                <input
                  type="checkbox"
                  checked={currentScenario.highElderly}
                  onChange={(e) => setCurrentScenario({ highElderly: e.target.checked })}
                  className={`w-3.5 h-3.5 appearance-none rounded border checked:bg-amber-500 transition-all ${isDarkMode ? 'border-slate-600' : 'border-slate-300'}`}
                />
                <span className={`text-[10px] uppercase font-bold ${isDarkMode ? 'text-slate-300' : 'text-slate-600'}`}>High Elderly Population</span>
              </label>
            </div>
          </div>

          <div>
            <label className={`text-[10px] uppercase tracking-wider font-bold mb-2 block ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Live Microclimate</label>
            <div className="grid grid-cols-3 gap-2">
              <div>
                <label className="text-[8px] uppercase text-slate-500 font-bold">Temp (°C)</label>
                <input
                  type="number"
                  value={currentScenario.ambientTemp}
                  onChange={(e) => setCurrentScenario({ ambientTemp: parseFloat(e.target.value) || 0 })}
                  className={`w-full border rounded p-1 text-xs outline-none focus:border-amber-500 mt-1 transition-all ${isDarkMode ? 'bg-slate-900 border-slate-700 text-white' : 'bg-white border-slate-200 text-slate-950 shadow-sm'}`}
                />
              </div>
              <div>
                <label className="text-[8px] uppercase text-slate-500 font-bold">Humidity (%)</label>
                <input
                  type="number"
                  value={currentScenario.humidity}
                  onChange={(e) => setCurrentScenario({ humidity: parseFloat(e.target.value) || 0 })}
                  className={`w-full border rounded p-1 text-xs outline-none focus:border-amber-500 mt-1 transition-all ${isDarkMode ? 'bg-slate-900 border-slate-700 text-white' : 'bg-white border-slate-200 text-slate-950 shadow-sm'}`}
                />
              </div>
              <div>
                <label className="text-[8px] uppercase text-slate-500 font-bold">Wind (m/s)</label>
                <input
                  type="number"
                  step="0.1"
                  value={currentScenario.windSpeed}
                  onChange={(e) => setCurrentScenario({ windSpeed: parseFloat(e.target.value) || 0 })}
                  className={`w-full border rounded p-1 text-xs outline-none focus:border-amber-500 mt-1 transition-all ${isDarkMode ? 'bg-slate-900 border-slate-700 text-white' : 'bg-white border-slate-200 text-slate-950 shadow-sm'}`}
                />
              </div>
            </div>
          </div>
          <div className="pt-2 pb-2">
            <button
              onClick={toggleDrawMode}
              className={`w-full py-2 px-4 rounded-xl font-bold text-[10px] uppercase tracking-widest transition-all border ${drawMode
                  ? 'bg-amber-500 text-white border-amber-500 shadow-lg shadow-amber-500/20'
                  : (isDarkMode ? 'bg-transparent text-amber-500 border-amber-500/30 hover:bg-amber-500/10' : 'bg-transparent text-amber-600 border-amber-500/50 hover:bg-amber-500/5')
                }`}
            >
              {drawMode ? 'Cancel Drawing' : 'Draw Intervention Zone'}
            </button>
            {currentScenario.zoneArea && !drawMode && (
              <p className={`text-center text-[10px] mt-2 font-mono ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                Zone Area: {(currentScenario.zoneArea / 10000).toFixed(2)} Ha
              </p>
            )}
          </div>

        </div>

        <button
          onClick={handleSimulate}
          disabled={loading || (!scenarioA.polygonFeature && !scenarioB.polygonFeature)}
          className={`w-full flex justify-center items-center gap-2 py-3 px-4 rounded-xl font-bold text-xs uppercase tracking-widest transition-all disabled:opacity-50 ${isDarkMode ? 'bg-white text-slate-950 hover:bg-slate-200' : 'bg-slate-900 text-white hover:bg-slate-800 shadow-xl'
            }`}
        >
          {loading && (
            <svg className={`animate-spin h-4 w-4 ${isDarkMode ? 'text-slate-950' : 'text-white'}`} xmlns="http://www.w3.org/2000/svg" fill="none" viewBox="0 0 24 24">
              <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4"></circle>
              <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
            </svg>
          )}
          {loading ? 'Extracting Geo-Physics...' : (!scenarioA.polygonFeature && !scenarioB.polygonFeature) ? 'Draw a Zone First' : 'Compare Scenarios'}
        </button>

        {backendStatus && (
          <div
            className={`relative p-3 rounded-lg border text-xs leading-relaxed ${
              isDarkMode
                ? 'bg-amber-500/10 border-amber-500/20 text-amber-300'
                : 'bg-amber-50 border-amber-200 text-amber-800'
            }`}
          >
            <button 
              onClick={() => setBackendStatus(null)}
              className="absolute top-2 right-2 opacity-50 hover:opacity-100"
              title="Dismiss warning"
            >
              ✕
            </button>
            <span className="font-bold uppercase tracking-wide block mb-1">
              {backendStatus.kind === 'tiles' ? 'Map Data Offline' : 'Simulation Offline'}
            </span>
            {backendStatus.message}
          </div>
        )}

        {results && (
          <div className={`space-y-3 pt-4 border-t ${isDarkMode ? 'border-white/10' : 'border-slate-200'}`}>
            {/* Winner Badge */}
            {(() => {
              const dropA = asNumber(results.A?.predicted_temp_drop_celsius);
              const dropB = asNumber(results.B?.predicted_temp_drop_celsius);
              if (dropA === null || dropB === null) return null;
              const winner = dropA > dropB ? 'Scenario A' : (dropB > dropA ? 'Scenario B' : 'Tie');
              if (winner === 'Tie') return (
                <div className={`p-2 rounded-lg border text-center ${isDarkMode ? 'bg-slate-500/10 border-slate-500/20 text-slate-300' : 'bg-slate-100 border-slate-200 text-slate-600'}`}>
                  <p className="text-[10px] font-bold uppercase tracking-wide">Tie - Identical Cooling</p>
                </div>
              );
              return (
                <div className={`p-2 rounded-lg border text-center relative overflow-hidden ${isDarkMode ? 'bg-emerald-500/10 border-emerald-500/20' : 'bg-emerald-50 border-emerald-100'}`}>
                  <div className="absolute top-0 right-0 p-1"><div className="w-1.5 h-1.5 bg-emerald-400 rounded-full animate-ping"></div></div>
                  <p className={`text-[10px] font-black uppercase tracking-widest mb-0.5 ${isDarkMode ? 'text-emerald-400' : 'text-emerald-600'}`}>Recommended</p>
                  <p className={`text-[10px] ${isDarkMode ? 'text-emerald-100' : 'text-emerald-800'}`}>{winner} cools <span className="font-bold">{Math.abs(dropA - dropB).toFixed(2)}°C</span> more</p>
                </div>
              );
            })()}

            {/* Side-by-side scenario cards */}
            <div className="grid grid-cols-2 gap-2">
              {(['A', 'B'] as const).map(key => {
                const r = results[key];
                if (!r) return null;
                const isActive = activeTab === key;
                return (
                  <div key={key} className={`p-3 rounded-lg border transition-all ${isActive
                      ? (isDarkMode ? 'border-emerald-500/50 bg-slate-800' : 'border-emerald-300 bg-white shadow-md')
                      : (isDarkMode ? 'border-white/5 bg-slate-900/50' : 'border-slate-100 bg-slate-50')
                    }`}>
                    <p className={`text-[9px] uppercase tracking-wider font-bold mb-2 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>Scenario {key}</p>

                    {/* Temperature drop with confidence */}
                    <p className="text-lg font-black text-emerald-500 leading-none">
                      {asNumber(r.predicted_temp_drop_celsius) !== null
                        ? `−${formatFixed(r.predicted_temp_drop_celsius)}°C`
                        : 'N/A'}
                    </p>
                    {r.cooling_uncertainty_celsius != null && (
                      <p className={`text-[9px] font-mono mt-0.5 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                        ± {formatFixed(r.cooling_uncertainty_celsius)} °C
                      </p>
                    )}
                    {r.cooling_95_confidence && (
                      <p className={`text-[8px] mt-0.5 ${isDarkMode ? 'text-slate-500' : 'text-slate-400'}`}>
                        95% CI: {r.cooling_95_confidence}
                      </p>
                    )}

                    {/* Baseline & intervention temps */}
                    {r.baseline_surface_temp_c != null && (
                      <div className={`mt-2 pt-2 border-t space-y-0.5 ${isDarkMode ? 'border-white/5' : 'border-slate-100'}`}>
                        <p className={`text-[9px] ${isDarkMode ? 'text-red-400' : 'text-red-500'}`}>
                          Baseline: {formatFixed(r.baseline_surface_temp_c)}°C
                        </p>
                        <p className={`text-[9px] ${isDarkMode ? 'text-sky-400' : 'text-sky-500'}`}>
                          After: {formatFixed(r.intervention_surface_temp_c)}°C
                        </p>
                      </div>
                    )}

                    {/* Social impact */}
                    {asNumber(r.impact_multiplier) !== null && r.impact_multiplier > 1.0 && (
                      <div className="inline-block px-1.5 py-0.5 mt-2 bg-amber-500/20 border border-amber-500/30 rounded text-[8px] text-amber-500 font-bold uppercase">
                        Vulnerability ×{r.impact_multiplier}
                      </div>
                    )}
                    <p className={`text-[10px] font-bold mt-1 ${isDarkMode ? 'text-emerald-400' : 'text-emerald-600'}`}>
                      Impact: {formatFixed(r.social_impact_score)}
                    </p>
                  </div>
                );
              })}
            </div>

            {/* Error Budget */}
            {results[activeTab]?.error_budget && (
              <div className={`p-3 rounded-lg border ${isDarkMode ? 'bg-slate-800/50 border-white/5' : 'bg-slate-50 border-slate-200'}`}>
                <p className={`text-[9px] uppercase tracking-wider font-bold mb-2 ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>
                  Error Budget — Scenario {activeTab}
                </p>
                <div className="space-y-1">
                  {results[activeTab].error_budget.per_source && Object.entries(results[activeTab].error_budget.per_source as Record<string, string>).map(([src, val]) => (
                    <div key={src} className="flex justify-between items-center">
                      <span className={`text-[9px] capitalize ${isDarkMode ? 'text-slate-300' : 'text-slate-600'}`}>{src.replace(/_/g, ' ')}</span>
                      <span className={`text-[9px] font-mono ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>{val}</span>
                    </div>
                  ))}
                  <div className={`flex justify-between items-center pt-1 border-t ${isDarkMode ? 'border-white/10' : 'border-slate-200'}`}>
                    <span className={`text-[9px] font-bold ${isDarkMode ? 'text-white' : 'text-slate-900'}`}>Cooling 95% CI</span>
                    <span className={`text-[9px] font-mono font-bold ${isDarkMode ? 'text-emerald-400' : 'text-emerald-600'}`}>
                      {results[activeTab].error_budget.cooling_95_CI}
                    </span>
                  </div>
                </div>
              </div>
            )}

            {/* Math Breakdown Toggle */}
            <button
              onClick={() => setShowMath(showMath === activeTab ? null : activeTab)}
              className={`w-full py-2 rounded-lg text-[10px] uppercase tracking-wider font-bold transition-all border ${
                showMath === activeTab
                  ? (isDarkMode ? 'bg-sky-600 text-white border-sky-500' : 'bg-sky-500 text-white border-sky-500')
                  : (isDarkMode ? 'bg-transparent text-sky-400 border-sky-500/30 hover:bg-sky-500/10' : 'bg-transparent text-sky-600 border-sky-300 hover:bg-sky-50')
              }`}
            >
              {showMath === activeTab ? 'Hide Math Derivation' : `Show Full Math — Scenario ${activeTab}`}
            </button>

            {/* Math Steps */}
            {showMath && results[showMath]?.math_breakdown && (
              <div className={`space-y-2 max-h-96 overflow-y-auto rounded-lg p-3 border ${isDarkMode ? 'bg-slate-900 border-white/5' : 'bg-white border-slate-200'}`}>
                <p className={`text-[9px] uppercase tracking-wider font-bold mb-2 ${isDarkMode ? 'text-sky-400' : 'text-sky-600'}`}>
                  Baseline Derivation
                </p>
                {(results[showMath].math_breakdown.baseline_steps as MathStep[]).map((s: MathStep) => (
                  <div key={s.step} className={`p-2 rounded border ${isDarkMode ? 'bg-slate-800/50 border-white/5' : 'bg-slate-50 border-slate-100'}`}>
                    <div className="flex items-baseline gap-2 mb-1">
                      <span className={`text-[8px] font-bold px-1.5 py-0.5 rounded ${isDarkMode ? 'bg-sky-900 text-sky-300' : 'bg-sky-100 text-sky-700'}`}>
                        {s.step}
                      </span>
                      <span className={`text-[10px] font-bold ${isDarkMode ? 'text-white' : 'text-slate-900'}`}>{s.name}</span>
                    </div>
                    <p className={`text-[9px] font-mono mb-1 ${isDarkMode ? 'text-amber-400' : 'text-amber-600'}`}>{s.equation}</p>
                    <pre className={`text-[8px] font-mono whitespace-pre-wrap leading-relaxed mb-1 ${isDarkMode ? 'text-slate-300' : 'text-slate-600'}`}>{s.calculation}</pre>
                    <div className="flex items-center gap-2">
                      <span className={`text-[9px] font-bold ${isDarkMode ? 'text-emerald-400' : 'text-emerald-600'}`}>
                        = {s.result} {s.unit}
                      </span>
                      {s.uncertainty && (
                        <span className={`text-[8px] ${isDarkMode ? 'text-slate-500' : 'text-slate-400'}`}>({s.uncertainty})</span>
                      )}
                    </div>
                    <p className={`text-[8px] mt-1 italic ${isDarkMode ? 'text-slate-500' : 'text-slate-400'}`}>{s.explanation}</p>
                  </div>
                ))}

                {/* Energy balance summary */}
                {results[showMath].baseline_energy_balance && (
                  <div className={`p-2 rounded border ${isDarkMode ? 'bg-amber-900/20 border-amber-500/20' : 'bg-amber-50 border-amber-200'}`}>
                    <p className={`text-[9px] font-bold mb-1 ${isDarkMode ? 'text-amber-400' : 'text-amber-700'}`}>Energy Balance Summary</p>
                    {Object.entries(results[showMath].baseline_energy_balance as Record<string, number>).map(([k, v]) => (
                      <div key={k} className="flex justify-between">
                        <span className={`text-[8px] ${isDarkMode ? 'text-slate-300' : 'text-slate-600'}`}>{k.replace(/_/g, ' ')}</span>
                        <span className={`text-[8px] font-mono ${isDarkMode ? 'text-slate-400' : 'text-slate-500'}`}>{(v as number).toFixed(1)} W/m²</span>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            )}
          </div>
        )}
      </div>
    </main>
  );
}
