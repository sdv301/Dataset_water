/**
 * Атлас гидрологической обстановки — единственная карта HydroPredict.
 * Слои: посты (ОЯ/НЯ/норма), will_flood, алерты, индикативные шаблоны затопления.
 */
import React, { useCallback, useEffect, useMemo, useState } from 'react';
import MapGL, { Marker, Source, Layer, type ViewStateChangeEvent } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import { API_BASE, MAP_SATELLITE_TILES_URL } from '../config';
import { Map, Crosshair, AlertOctagon, X, Activity, RefreshCw } from './icons';

type GeoJsonFeature = { type: string; geometry?: unknown; properties?: Record<string, unknown> | null };
type FeatureCollection = { type: 'FeatureCollection'; features: GeoJsonFeature[] };

export interface AtlasStation {
  label: string;
  river: string;
  post: string;
  lat: number;
  lng: number;
}

interface MapPoint {
  river: string;
  post: string;
  lat: number;
  lon: number;
  critical_oya?: number | null;
  low_oya?: number | null;
  risk_class?: string | null;
  will_flood?: boolean | null;
  confidence?: number | null;
  computed_at?: string | null;
  verdict?: {
    level?: string;
    level_ru?: string;
    confidence?: number;
    will_flood?: boolean;
  } | null;
  forecast_peak?: { level_cm?: number; date?: string } | null;
}

interface AtlasMeta {
  template_method?: string;
  disclaimer?: string;
  template_count?: number;
}

interface AlertRow {
  id: number;
  river: string;
  post: string;
  created_at: string;
  risk_class?: string;
  peak_level_cm?: number;
  peak_date?: string;
  narrative?: string;
  acknowledged?: number;
}

interface LayerToggles {
  stations: boolean;
  oya: boolean;
  nya: boolean;
  norm: boolean;
  willFlood: boolean;
  alerts: boolean;
  templates: boolean;
  labels: boolean;
}

const DEFAULT_LAYERS: LayerToggles = {
  stations: true,
  oya: true,
  nya: true,
  norm: true,
  willFlood: true,
  alerts: true,
  templates: true,
  labels: false,
};

function buildRasterStyle(name: string, sourceId: string, tilesUrl: string, maxzoom = 19) {
  return {
    version: 8 as const,
    name,
    sources: {
      [sourceId]: {
        type: 'raster' as const,
        tiles: [tilesUrl],
        tileSize: 256,
        maxzoom,
      },
    },
    layers: [
      {
        id: `${sourceId}-layer`,
        type: 'raster' as const,
        source: sourceId,
        minzoom: 0,
        maxzoom: 22,
      },
    ],
  };
}

const SATELLITE_STYLE = buildRasterStyle('Satellite', 'satellite-tiles', MAP_SATELLITE_TILES_URL, 18);

function riskColor(rk?: string | null): string {
  const r = (rk || 'low').toLowerCase();
  if (r === 'critical' || r === 'high') return '#ef4444';
  if (r === 'medium' || r === 'moderate') return '#f97316';
  return '#10b981';
}

function riskLabel(rk?: string | null): string {
  const r = (rk || 'low').toLowerCase();
  if (r === 'critical' || r === 'high') return 'Опасность (ОЯ)';
  if (r === 'medium' || r === 'moderate') return 'Предупреждение (НЯ)';
  return 'Норма';
}

function riskBucket(rk?: string | null): 'oya' | 'nya' | 'norm' {
  const r = (rk || 'low').toLowerCase();
  if (r === 'critical' || r === 'high') return 'oya';
  if (r === 'medium' || r === 'moderate') return 'nya';
  return 'norm';
}

function getRiverBounds(stations: { lat: number; lng: number }[]): { center: [number, number]; zoom: number } {
  if (stations.length === 0) return { center: [63, 130], zoom: 4 };
  const lat = stations.reduce((s, st) => s + st.lat, 0) / stations.length;
  const lng = stations.reduce((s, st) => s + st.lng, 0) / stations.length;
  const latSpan = Math.max(...stations.map(s => s.lat)) - Math.min(...stations.map(s => s.lat));
  const lngSpan = Math.max(...stations.map(s => s.lng)) - Math.min(...stations.map(s => s.lng));
  const span = Math.max(latSpan, lngSpan);
  let zoom = 8;
  if (stations.length > 1) {
    if (span > 8) zoom = 4;
    else if (span > 4) zoom = 5;
    else if (span > 2) zoom = 6;
    else zoom = 7;
  }
  return { center: [lat, lng], zoom };
}

const EMPTY_FC: FeatureCollection = { type: 'FeatureCollection', features: [] };

export interface AtlasMapProps {
  stations: AtlasStation[];
  selectedLabel?: string;
  onSelectStation: (label: string) => void;
  onOpenForecast?: (label: string, mode?: 'short' | 'medium') => void;
}

export function AtlasMap({ stations, selectedLabel, onSelectStation, onOpenForecast }: AtlasMapProps) {
  const [points, setPoints] = useState<MapPoint[]>([]);
  const [templates, setTemplates] = useState<FeatureCollection>(EMPTY_FC);
  const [meta, setMeta] = useState<AtlasMeta | null>(null);
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [layers, setLayers] = useState<LayerToggles>(DEFAULT_LAYERS);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [mapCenter, setMapCenter] = useState<[number, number]>([63, 130]);
  const [mapZoom, setMapZoom] = useState(4);
  const [generatedAt, setGeneratedAt] = useState<string | null>(null);

  const reload = useCallback(() => {
    setLoading(true);
    setError(null);
    Promise.all([
      fetch(`${API_BASE}/map/latest`).then(r => (r.ok ? r.json() : Promise.reject(new Error(`map/latest ${r.status}`)))),
      fetch(`${API_BASE}/agent/alerts/summary?only_pending=true&limit=100`)
        .then(r => (r.ok ? r.json() : null))
        .catch(() => null),
    ])
      .then(([mapData, alertSum]) => {
        const pts: MapPoint[] = Array.isArray(mapData?.points) ? mapData.points : [];
        setPoints(pts);
        const fc = mapData?.templates;
        if (fc && fc.type === 'FeatureCollection' && Array.isArray(fc.features)) {
          setTemplates(fc as FeatureCollection);
        } else {
          setTemplates(EMPTY_FC);
        }
        setMeta(mapData?.meta || null);
        setGeneratedAt(mapData?.generated_at || null);
        const latest: AlertRow[] = Array.isArray(alertSum?.latest) ? alertSum.latest : [];
        setAlerts(latest);
      })
      .catch((e: Error) => {
        setError(e?.message || 'Не удалось загрузить атлас');
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    reload();
    const t = window.setInterval(reload, 120_000);
    return () => window.clearInterval(t);
  }, [reload]);

  useEffect(() => {
    if (!selectedLabel || !stations.length) return;
    const st = stations.find(s => s.label === selectedLabel);
    if (st) setSelectedKey(`${st.river}|${st.post}`);
  }, [selectedLabel, stations]);

  const pointByKey = useMemo(() => {
    const m: Record<string, MapPoint> = {};
    for (const p of points) m[`${p.river}|${p.post}`] = p;
    return m;
  }, [points]);

  const alertKeys = useMemo(() => {
    const s = new Set<string>();
    for (const a of alerts) s.add(`${a.river}|${a.post}`);
    return s;
  }, [alerts]);

  const selectedPoint = selectedKey ? pointByKey[selectedKey] : null;
  const selectedStation = useMemo(() => {
    if (!selectedKey) return null;
    const [river, post] = selectedKey.split('|');
    return stations.find(s => s.river === river && s.post === post) || null;
  }, [selectedKey, stations]);

  const stationAlerts = useMemo(() => {
    if (!selectedKey) return [];
    const [river, post] = selectedKey.split('|');
    return alerts.filter(a => a.river === river && a.post === post);
  }, [alerts, selectedKey]);

  const visibleStations = useMemo(() => {
    if (!layers.stations) return [];
    return stations.filter(s => {
      const key = `${s.river}|${s.post}`;
      const mr = pointByKey[key];
      const bucket = riskBucket(mr?.risk_class || 'low');
      if (bucket === 'oya' && !layers.oya) return false;
      if (bucket === 'nya' && !layers.nya) return false;
      if (bucket === 'norm' && !layers.norm) return false;
      return true;
    });
  }, [stations, pointByKey, layers]);

  const filteredTemplates = useMemo((): FeatureCollection => {
    if (!layers.templates) return EMPTY_FC;
    const features = (templates.features || []).filter(f => {
      const props = (f.properties || {}) as Record<string, unknown>;
      const zone = String(props.zone || '');
      const rk = String(props.risk_class || '');
      if (zone === 'oya' || ['high', 'critical'].includes(rk.toLowerCase())) {
        return layers.oya;
      }
      return layers.nya;
    });
    return { type: 'FeatureCollection', features };
  }, [templates, layers]);

  const centerOnRiver = (river: string) => {
    const rs = stations.filter(s => s.river === river).map(s => ({ lat: s.lat, lng: s.lng }));
    const { center, zoom } = getRiverBounds(rs);
    setMapCenter(center);
    setMapZoom(zoom);
  };

  const centerOnAll = () => {
    const { center, zoom } = getRiverBounds(stations.map(s => ({ lat: s.lat, lng: s.lng })));
    setMapCenter(center);
    setMapZoom(Math.min(zoom, 5));
  };

  const toggle = (key: keyof LayerToggles) => {
    setLayers(prev => ({ ...prev, [key]: !prev[key] }));
  };

  const handleSelect = (s: AtlasStation) => {
    const key = `${s.river}|${s.post}`;
    setSelectedKey(key);
    onSelectStation(s.label);
    setMapCenter([s.lat, s.lng]);
    setMapZoom(z => Math.max(z, 7));
  };

  const counts = useMemo(() => {
    let oya = 0;
    let nya = 0;
    let norm = 0;
    let wf = 0;
    for (const s of stations) {
      const mr = pointByKey[`${s.river}|${s.post}`];
      const b = riskBucket(mr?.risk_class);
      if (b === 'oya') oya++;
      else if (b === 'nya') nya++;
      else norm++;
      if (mr?.will_flood || mr?.verdict?.will_flood) wf++;
    }
    return { oya, nya, norm, wf, alerts: alerts.length, templates: templates.features?.length || 0 };
  }, [stations, pointByKey, alerts, templates]);

    return (
    <div className="flex flex-col h-full min-h-0 animate-in fade-in duration-300">
      <div className="shrink-0 flex flex-wrap items-center justify-between gap-3 mb-3">
        <div className="flex items-center gap-2">
          <Map className="w-5 h-5 text-blue-500" />
          <h3 className="text-base font-semibold text-slate-800">Атлас гидрологической обстановки</h3>
          {loading && <RefreshCw className="w-4 h-4 text-slate-400 animate-spin" />}
          {generatedAt && (
            <span className="text-[11px] text-slate-400">
              обновлено {generatedAt.replace('T', ' ').replace('Z', ' UTC')}
            </span>
          )}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <button type="button" onClick={reload}
            className="text-xs px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 flex items-center gap-1">
            <RefreshCw className="w-3.5 h-3.5" /> Обновить
          </button>
          <button type="button" onClick={centerOnAll}
            className="text-xs px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 flex items-center gap-1">
            <Crosshair className="w-3.5 h-3.5 text-blue-500" /> Вся Якутия
          </button>
          {selectedStation && (
            <button type="button" onClick={() => centerOnRiver(selectedStation.river)}
              className="text-xs px-2.5 py-1.5 rounded-lg border border-slate-200 bg-white text-slate-600 hover:bg-slate-50 flex items-center gap-1">
              <Crosshair className="w-3.5 h-3.5 text-blue-500" /> {selectedStation.river}
            </button>
          )}
        </div>
      </div>
      {error && (
        <div className="mb-3 text-sm text-amber-900 bg-amber-50 border border-amber-200 rounded-xl px-3 py-2">{error}</div>
      )}
      <div className="flex-1 min-h-0 flex gap-3">
        <div className="flex-1 min-w-0 relative rounded-2xl overflow-hidden border border-slate-200 bg-slate-900 shadow-sm">
                    <MapGL
            longitude={mapCenter[1]}
            latitude={mapCenter[0]}
            zoom={mapZoom}
            mapStyle={SATELLITE_STYLE}
            onMove={(e: ViewStateChangeEvent) => {
              const { latitude, longitude, zoom } = e.viewState;
              setMapCenter([latitude, longitude]);
              setMapZoom(zoom);
            }}
            attributionControl={false}
            style={{ width: '100%', height: '100%' }}
          >
            {layers.templates && filteredTemplates.features.length > 0 && (
              <Source id="atlas-templates" type="geojson" data={filteredTemplates as any}>

                <Layer id="atlas-templates-fill" type="fill" paint={{
                  'fill-color': ['match', ['get', 'zone'], 'oya', '#ef4444', 'nya', '#f97316', '#f97316'],
                  'fill-opacity': ['interpolate', ['linear'], ['coalesce', ['get', 'confidence'], 0.5], 0.15, 0.12, 1, 0.35],
                }} />
                <Layer id="atlas-templates-line" type="line" paint={{
                  'line-color': ['match', ['get', 'zone'], 'oya', '#dc2626', 'nya', '#ea580c', '#ea580c'],
                  'line-width': 1.5,
                  'line-opacity': 0.85,
                }} />
              </Source>
            )}
            {visibleStations.map(s => {
              const key = `${s.river}|${s.post}`;
              const mr = pointByKey[key];
              const fill = riskColor(mr?.risk_class);
              const isSelected = key === selectedKey || s.label === selectedLabel;
              const willFlood = !!(layers.willFlood && (mr?.will_flood || mr?.verdict?.will_flood));
              const hasAlert = layers.alerts && alertKeys.has(key);
              return (
                <Marker key={s.label} longitude={s.lng} latitude={s.lat} anchor="center">
                  <div className="relative group cursor-pointer" onClick={(e) => { e.stopPropagation(); handleSelect(s); }}>
                    {willFlood && <span className="absolute -inset-2 rounded-full bg-red-500/40 animate-ping" />}
                    <div
                      className={`rounded-full shadow-md transition-all relative z-10 ${isSelected ? 'w-6 h-6' : 'w-4 h-4'}`}
                      style={{
                        backgroundColor: fill, opacity: 0.92,
                        border: `${isSelected ? '3px' : '2px'} solid ${isSelected ? '#3b82f6' : 'rgba(255,255,255,0.85)'}`,
                        boxShadow: hasAlert ? '0 0 0 3px rgba(220,38,38,0.7)' : undefined,
                      }}
                    />
                    {hasAlert && (
                      <span className="absolute -top-2 -right-2 z-20 w-4 h-4 rounded-full bg-red-600 text-white text-[9px] font-bold flex items-center justify-center border border-white">!</span>
                    )}
                    {(layers.labels || isSelected) && (
                      <div className="absolute left-full ml-2 top-1/2 -translate-y-1/2 bg-black/70 text-white text-[10px] px-1.5 py-0.5 rounded whitespace-nowrap pointer-events-none">{s.post}</div>
                    )}
                    <div className="absolute top-1/2 left-full ml-3 -translate-y-1/2 bg-white px-3 py-2 rounded-lg shadow-xl border border-slate-200 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 whitespace-nowrap">
                      <div className="text-sm font-semibold text-slate-800">{s.label}</div>
                      <div className="text-xs text-slate-600 mt-1">Риск: <strong>{riskLabel(mr?.risk_class)}</strong></div>
                      {mr?.verdict && (
                        <div className="text-xs text-slate-500 mt-0.5">
                          Вердикт: {mr.verdict.level_ru || mr.verdict.level || '—'}
                          {mr.verdict.confidence != null ? ` · ${Math.round((mr.verdict.confidence || 0) * 100)}%` : ''}
                        </div>
                      )}
                    </div>
                  </div>
                </Marker>
              );
            })}
          </MapGL>

                    <div className="absolute bottom-3 left-3 z-10 bg-white/95 backdrop-blur-sm border border-slate-200 rounded-xl shadow-lg px-3 py-2.5 text-xs space-y-1.5 max-w-[220px]">
            <p className="font-semibold text-slate-700 mb-1">Легенда</p>
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-red-500" /> Опасность (ОЯ) · {counts.oya}</div>
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-orange-500" /> Предупреждение (НЯ) · {counts.nya}</div>
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-emerald-500" /> Норма · {counts.norm}</div>
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full bg-red-500 ring-2 ring-red-300 animate-pulse" /> will_flood · {counts.wf}</div>
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-full border-2 border-red-600 bg-white" /> Алерт · {counts.alerts}</div>
            <div className="flex items-center gap-2"><span className="w-3 h-3 rounded-sm bg-red-500/30 border border-red-500" /> Шаблон · {counts.templates}</div>
          </div>
          <div className="absolute top-3 right-3 z-10 bg-white/95 backdrop-blur-sm border border-slate-200 rounded-xl shadow-lg px-3 py-2.5 text-xs space-y-1 min-w-[160px]">
            <p className="font-semibold text-slate-700 mb-1">Слои</p>
            {([
              ['stations', 'Посты'],
              ['oya', 'Опасность (ОЯ)'],
              ['nya', 'Предупреждение (НЯ)'],
              ['norm', 'Норма'],
              ['willFlood', 'will_flood'],
              ['alerts', 'Алерты'],
              ['templates', 'Шаблоны затопления'],
              ['labels', 'Подписи'],
            ] as [keyof LayerToggles, string][]).map(([k, label]) => (
              <label key={k} className="flex items-center gap-2 cursor-pointer text-slate-700 hover:text-slate-900">
                <input type="checkbox" checked={layers[k]} onChange={() => toggle(k)}
                  className="rounded border-slate-300 text-blue-600 focus:ring-blue-500" />
                {label}
              </label>
            ))}
          </div>

        </div>
                <aside className="w-80 shrink-0 flex flex-col gap-3 overflow-y-auto">
          {selectedPoint || selectedStation ? (
            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 space-y-3">
              <div className="flex items-start justify-between gap-2">
                <div>
                  <p className="text-xs text-slate-500 uppercase font-semibold">Пост</p>
                  <h4 className="text-base font-bold text-slate-800">
                    {selectedStation?.label || `${selectedPoint?.river} — ${selectedPoint?.post}`}
                  </h4>
                </div>
                <button type="button" onClick={() => setSelectedKey(null)}
                  className="p-1 rounded-lg text-slate-400 hover:bg-slate-100 hover:text-slate-600">
                  <X className="w-4 h-4" />
                </button>
              </div>
              <div className="rounded-xl px-3 py-2 text-sm font-medium"
                style={{ backgroundColor: `${riskColor(selectedPoint?.risk_class)}18`, color: riskColor(selectedPoint?.risk_class) }}>
                {riskLabel(selectedPoint?.risk_class)}
                {selectedPoint?.confidence != null && (
                  <span className="ml-2 text-slate-600 font-normal">· conf {Math.round(selectedPoint.confidence * 100)}%</span>
                )}
              </div>
              {selectedPoint?.verdict && (
                <p className="text-sm text-slate-700">
                  <span className="text-slate-500">Вердикт:</span>{' '}
                  <strong>{selectedPoint.verdict.level_ru || selectedPoint.verdict.level || '—'}</strong>
                  {selectedPoint.verdict.will_flood ? <span className="ml-2 text-red-600 font-semibold">will_flood</span> : null}
                </p>
              )}
              {selectedPoint?.forecast_peak?.level_cm != null && (
                <p className="text-sm text-slate-700">
                  <span className="text-slate-500">Пик:</span>{' '}
                  <strong className="font-mono">{selectedPoint.forecast_peak.level_cm} см</strong>
                  {selectedPoint.forecast_peak.date ? ` @ ${selectedPoint.forecast_peak.date}` : ''}
                </p>
              )}
              <div className="text-xs text-slate-600 space-y-0.5 border-t border-slate-100 pt-2">
                <p>НЯ: <span className="font-mono text-orange-600">{selectedPoint?.low_oya ?? '—'} см</span></p>
                <p>ОЯ: <span className="font-mono text-red-600">{selectedPoint?.critical_oya ?? '—'} см</span></p>
                {selectedPoint?.computed_at && <p className="text-slate-400">снимок: {selectedPoint.computed_at}</p>}
              </div>
                            {stationAlerts.length > 0 && (
                <div className="border-t border-slate-100 pt-2 space-y-2">
                  <p className="text-xs font-semibold text-red-700 flex items-center gap-1">
                    <AlertOctagon className="w-3.5 h-3.5" /> Алерты ({stationAlerts.length})
                  </p>
                  {stationAlerts.slice(0, 3).map(a => (
                    <div key={a.id} className="text-xs bg-red-50 border border-red-100 rounded-lg px-2 py-1.5 text-red-900">
                      <p className="font-medium">{a.risk_class || 'alert'} · {a.created_at?.slice(0, 16)}</p>
                      {a.narrative && <p className="mt-0.5 line-clamp-3 text-red-800/90">{a.narrative}</p>}
                    </div>
                  ))}
                </div>
              )}
              {onOpenForecast && selectedStation && (
                <div className="flex gap-2 pt-1">
                  <button type="button" onClick={() => onOpenForecast(selectedStation.label, 'short')}
                    className="flex-1 text-xs font-medium px-3 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700 flex items-center justify-center gap-1">
                    <Activity className="w-3.5 h-3.5" /> Краткий
                  </button>
                  <button type="button" onClick={() => onOpenForecast(selectedStation.label, 'medium')}
                    className="flex-1 text-xs font-medium px-3 py-2 rounded-lg border border-blue-200 text-blue-700 bg-blue-50 hover:bg-blue-100">
                    Средний
                  </button>
                </div>
              )}

            </div>
          ) : (
            <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 text-sm text-slate-500">
              Кликните по посту на карте — карточка риска, вердикт и переход к прогнозу.
            </div>
          )}
                    <div className="bg-white rounded-2xl border border-slate-200 shadow-sm p-4 flex-1 min-h-0">
            <p className="text-xs font-semibold text-slate-600 uppercase tracking-wider mb-2 flex items-center gap-1.5">
              <AlertOctagon className="w-3.5 h-3.5 text-red-500" />
              Алерты агента ({alerts.length})
            </p>
            {alerts.length === 0 ? (
              <p className="text-xs text-slate-400">Нет неподтверждённых алертов</p>
            ) : (
              <ul className="space-y-2 max-h-64 overflow-y-auto">
                {alerts.slice(0, 20).map(a => {
                  const label = `${a.river} — ${a.post}`;
                  return (
                    <li key={a.id}>
                      <button type="button"
                        onClick={() => {
                          const st = stations.find(s => s.river === a.river && s.post === a.post);
                          if (st) handleSelect(st);
                          else {
                            setSelectedKey(`${a.river}|${a.post}`);
                            onSelectStation(label);
                          }
                        }}
                        className="w-full text-left text-xs rounded-lg border border-slate-100 hover:border-red-200 hover:bg-red-50/50 px-2.5 py-2 transition-colors">
                        <div className="font-medium text-slate-800">{label}</div>
                        <div className="text-slate-500 mt-0.5">
                          {a.risk_class || '—'}
                          {a.peak_level_cm != null ? ` · пик ${a.peak_level_cm} см` : ''}
                        </div>
                      </button>
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
          <div className="bg-amber-50 border border-amber-200 rounded-xl px-3 py-2.5 text-[11px] text-amber-900 leading-relaxed">
            <strong>Схематично.</strong>{' '}
            {meta?.disclaimer || 'Шаблоны затопления — индикативные буферы по риску агента (не кадастр, не ЦМР).'}
            {meta?.template_method && (
              <span className="block mt-1 text-amber-700/80">метод: {meta.template_method}</span>
            )}
          </div>

        </aside>

      </div>
    </div>
  );
}

export default AtlasMap;


