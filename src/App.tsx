import React, { useState, useEffect, useCallback, useRef } from 'react';
import { 
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, Legend, 
  BarChart, Bar, ResponsiveContainer, ReferenceLine, ReferenceDot, PieChart, Pie, Cell, ComposedChart, Scatter
} from 'recharts';
import {
  Map, Activity, Calendar, LayoutDashboard, Settings2,
  Thermometer, CloudRain, Snowflake, AlertOctagon, TrendingUp, AlertTriangle, Plus, X, BarChart2,
  Database, Upload, RefreshCw, FileText, Loader2, Crosshair,
} from './components/icons';
import MapGL, { Marker, type ViewStateChangeEvent } from 'react-map-gl/maplibre';
import 'maplibre-gl/dist/maplibre-gl.css';
import { format, addDays, subDays } from 'date-fns';
import { ru } from 'date-fns/locale';
import {
  ExplainPanel, HydroChart, YearOverview, ClimatologyChart, MediumForecastView, mapTierToChart,
  formatDateRu, QuantileLegend,
  type YearChartFocus,
} from './ForecastPanels';
import { StationSearchSelect } from './components/StationSearchSelect';
import { FloodAgentPanel } from './components/FloodAgentPanel';
import { AgentAlertsBadge } from './components/AgentAlertsBadge';
import { notifyTrainingFinished, requestTrainingNotifications } from './utils/trainingNotify';
import { API_BASE, MAP_SATELLITE_TILES_URL, MAP_SCHEME_TILES_URL } from './config';

// --- Types & API ---
type ForecastMode = 'short' | 'medium' | 'season' | 'year' | 'norm' | 'dashboards' | 'data';
type WidgetId = 'cross_model' | 'scatter' | 'basin_risk' | 'feature_importance' | 'heatmap' | 'risk_pie' | 'peak_analysis';

interface StationInfo {
  label: string;
  river: string;
  post: string;
  lat: number;
  lng: number;
  risk: string;
  critical_oya?: number;
  low_oya?: number;
  has_model?: boolean;
}

interface TrainingStatus {
  status: 'idle' | 'training' | 'complete' | 'error';
  progress: number;
  current_station: string;
  message: string;
  step_detail?: string;
  station_index?: number;
  stations_total?: number;
}

interface TrainingHistoryRow {
  id: number;
  started_at: string;
  finished_at?: string;
  river?: string;
  post?: string;
  scope: string;
  fast: boolean;
  status: string;
  stations_total: number;
  stations_trained: number;
  stations_skipped: number;
  message?: string;
}

// Хук для загрузки данных с API с fallback на моки
function useApi<T>(url: string, fallback: T): { data: T; loading: boolean; error: boolean; refetch: () => void } {
  const [data, setData] = useState<T>(fallback);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);

  const refetch = useCallback(() => {
    setLoading(true);
    setError(false);
    fetch(`${API_BASE}${url}`)
      .then(r => { if (!r.ok) throw new Error(r.statusText); return r.json(); })
      .then(d => { setData(d); setLoading(false); })
      .catch(() => { setData(fallback); setLoading(false); setError(true); });
  }, [url]);

  useEffect(() => { refetch(); }, [refetch]);

  return { data, loading, error, refetch };
}

const AVAILABLE_WIDGETS: { id: WidgetId, label: string, width: 'full' | 'half' }[] = [
  { id: 'peak_analysis', label: 'Анализ экстремумов (Пики)', width: 'full' },
  { id: 'cross_model', label: 'Сценарии «что если»', width: 'full' },
  { id: 'scatter', label: 'Корреляция аномалий', width: 'half' },
  { id: 'basin_risk', label: 'Риск по бассейнам', width: 'half' },
  { id: 'feature_importance', label: 'Важность признаков', width: 'half' },
  { id: 'heatmap', label: 'Матрица корреляций', width: 'half' },
  { id: 'risk_pie', label: 'Распределение риска', width: 'half' },
];

// Демо-данные: используются ТОЛЬКО когда API не вернул ни истории, ни прогноза
// (has_model == false или API недоступен). В остальных случаях игнорируется.
// На карточках стоит бейдж «Демо / нет модели» (isMock), а ExplainPanel показывает «демо».
const generateMockData = (days: number, baseLevel: number, tempMod: number, precipMod: number) => {
  return Array.from({ length: days }).map((_, i) => {
    const date = addDays(new Date(), i);
    const trend = i * 1.5;
    const modifier = (tempMod * 5) + ((precipMod - 100) * 0.5);
    const trendValue = baseLevel + trend + modifier;
    const seasonality = Math.sin(i / 5) * 20;
    const median = Math.max(0, trendValue + seasonality);
    
    return {
      date: format(date, 'dd MMM', { locale: ru }),
      fullDate: date,
      median: median,
      trend: Math.max(0, trendValue),
      q90: Math.max(0, median + 30 + (i * 1.2)),
      q95: Math.max(0, median + 50 + (i * 2.0)),
    };
  });
};

function getRiverBounds(riverStations: StationInfo[]): { center: [number, number]; zoom: number } {
  if (riverStations.length === 0) return { center: [63, 130], zoom: 4 };
  const lat = riverStations.reduce((s, st) => s + st.lat, 0) / riverStations.length;
  const lng = riverStations.reduce((s, st) => s + st.lng, 0) / riverStations.length;
  const latSpan = Math.max(...riverStations.map(s => s.lat)) - Math.min(...riverStations.map(s => s.lat));
  const lngSpan = Math.max(...riverStations.map(s => s.lng)) - Math.min(...riverStations.map(s => s.lng));
  const span = Math.max(latSpan, lngSpan);
  let zoom = 8;
  if (riverStations.length > 1) {
    if (span > 8) zoom = 4;
    else if (span > 4) zoom = 5;
    else if (span > 2) zoom = 6;
    else zoom = 7;
  }
  return { center: [lat, lng], zoom };
}

const DEFAULT_STATIONS: StationInfo[] = [
  { label: 'Лена — Якутск', river: 'Лена', post: 'Якутск', lat: 62.0, lng: 129.7, risk: 'low', critical_oya: 827, low_oya: -115 },
  { label: 'Лена — Ленск', river: 'Лена', post: 'Ленск', lat: 60.72, lng: 114.95, risk: 'medium', critical_oya: 1760, low_oya: 75 },
  { label: 'Алдан — Томмот', river: 'Алдан', post: 'Томмот', lat: 58.96, lng: 126.28, risk: 'low', critical_oya: 820 },
  { label: 'Вилюй — Вилюйск', river: 'Вилюй', post: 'Вилюйск', lat: 63.75, lng: 121.63, risk: 'low', critical_oya: 1050, low_oya: 110 },
  { label: 'Колыма — Черский', river: 'Колыма', post: 'Черский', lat: 68.75, lng: 161.33, risk: 'low', critical_oya: 600 },
  { label: 'Амга — Амга', river: 'Амга', post: 'Амга', lat: 60.90, lng: 131.98, risk: 'low', critical_oya: 925 },
];

// --- MapLibre raster style builder (Task 7) ---
function buildRasterStyle(
  name: string,
  sourceId: string,
  tilesUrl: string,
  attribution: string,
  maxzoom = 19,
) {
  return {
    version: 8 as const,
    name,
    glyphs: 'https://demotiles.maplibre.org/font/{fontstack}/{range}.pbf',
    sources: {
      [sourceId]: {
        type: 'raster' as const,
        tiles: [tilesUrl],
        tileSize: 256,
        maxzoom,
        attribution,
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

const SATELLITE_STYLE = buildRasterStyle(
  'Satellite',
  'satellite-tiles',
  MAP_SATELLITE_TILES_URL.replace('{z}', '{z}').replace('{y}', '{y}').replace('{x}', '{x}'),
  '© Esri, Maxar, Earthstar Geographics',
  18,
);

const SCHEME_STYLE = buildRasterStyle(
  'Scheme',
  'scheme-tiles',
  MAP_SCHEME_TILES_URL.replace('{z}', '{z}').replace('{x}', '{x}').replace('{y}', '{y}'),
  '© CARTO, © OpenStreetMap contributors',
  19,
);

export default function App() {
  const [mode, setMode] = useState<ForecastMode>('short');
  const [activeWidgets, setActiveWidgets] = useState<WidgetId[]>(['peak_analysis', 'cross_model', 'scatter', 'basin_risk']);
  const [stations, setStations] = useState<StationInfo[]>(DEFAULT_STATIONS);
  const [station, setStation] = useState(DEFAULT_STATIONS[0].label);
  const [warningLevel, setWarningLevel] = useState(827);
  const [dangerLevel, setDangerLevel] = useState(1000);
  const [minLevel, setMinLevel] = useState(DEFAULT_STATIONS[0].low_oya ?? 0);
  const [apiConnected, setApiConnected] = useState(false);
  const [dbReady, setDbReady] = useState(false);
  const [dbError, setDbError] = useState<string | null>(null);
  const [dataStats, setDataStats] = useState<{
    total_records?: number;
    total_stations?: number;
    date_range?: [string, string];
  } | null>(null);
  const [datasetRows, setDatasetRows] = useState<Array<{
    date: string;
    water_level_cm: number;
    temp_mean?: number | null;
    precip_mm?: number | null;
    snow_depth_cm?: number | null;
  }>>([]);
  const [datasetLoading, setDatasetLoading] = useState(false);
  const [importMsg, setImportMsg] = useState<{ kind: 'ok' | 'err'; text: string } | null>(null);
  const [importing, setImporting] = useState<'observations' | 'stations' | null>(null);
  const [trainingStatus, setTrainingStatus] = useState<TrainingStatus>({ status: 'idle', progress: 0, current_station: '', message: '' });
  const [trainingHistory, setTrainingHistory] = useState<TrainingHistoryRow[]>([]);
  const trainPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [targetDate, setTargetDate] = useState<string>('');
  const [apiForecast, setApiForecast] = useState<any[]>([]);
  const [apiHistory, setApiHistory] = useState<any[]>([]);
  const [tierPayload, setTierPayload] = useState<any>(null);
  const [yearPayload, setYearPayload] = useState<any>(null);
  const [climatology, setClimatology] = useState<any[]>([]);
  const [explain, setExplain] = useState<any>(null);
  const [isMock, setIsMock] = useState(false);
  const [forecastLoading, setForecastLoading] = useState(false);
  const [forecastError, setForecastError] = useState<string | null>(null);
  const [yearLoading, setYearLoading] = useState(false);
  const [yearSelected, setYearSelected] = useState<number>(() => new Date().getFullYear());
  const stationKeyRef = useRef('');
  const [yearChartFocus, setYearChartFocus] = useState<YearChartFocus>('all');
  const [normExcludeYear, setNormExcludeYear] = useState<number | null>(null);
  const [modelStatus, setModelStatus] = useState<any>(null);
  const [scenarioPayload, setScenarioPayload] = useState<any>(null);
  const [historyOnlyCurrent, setHistoryOnlyCurrent] = useState(false);
  
  // What If scenarios
  const [tempMod, setTempMod] = useState(0);
  const [precipMod, setPrecipMod] = useState(100);
  const [snowMod, setSnowMod] = useState(100);
  
  const [mapStyle, setMapStyle] = useState<'scheme' | 'satellite'>('satellite');
  const [mapData, setMapData] = useState<'risk' | 'temp' | 'snow'>('risk');
  const [mapCenter, setMapCenter] = useState<[number, number]>([63, 130]);
  const [mapZoom, setMapZoom] = useState(4);

  const centerOnRiver = useCallback((river: string) => {
    const riverStations = stations.filter(s => s.river === river);
    const { center, zoom } = getRiverBounds(riverStations);
    setMapCenter(center);
    setMapZoom(zoom);
  }, [stations]);

  const loadStationsFromApi = useCallback((preserveLabel?: string) => {
    return fetch(`${API_BASE}/rivers`)
      .then(async (r) => {
        if (!r.ok) {
          const text = await r.text().catch(() => '');
          console.error(`[HydroPredict] GET /rivers → ${r.status} ${r.statusText}`, text);
          throw new Error(`API /rivers returned ${r.status}`);
        }
        return r.json();
      })
      .then((rivers: any[]) => {
        if (!rivers?.length) {
          console.warn('[HydroPredict] /rivers returned empty array');
          return;
        }
        console.info(`[HydroPredict] Loaded ${rivers.length} rivers from API`);
        const fetchPosts = rivers.map(r =>
          fetch(`${API_BASE}/rivers/${encodeURIComponent(r.river)}/posts`)
            .then(async (res) => {
              if (!res.ok) {
                console.error(`[HydroPredict] GET /rivers/${r.river}/posts → ${res.status}`);
                return [];
              }
              return res.json();
            })
            .catch((err) => { console.error(`[HydroPredict] posts fetch error for ${r.river}:`, err); return []; })
        );
        return Promise.all(fetchPosts).then(allPosts => {
          const newStations: StationInfo[] = [];
          allPosts.forEach((posts: any[], i) => {
            posts.forEach((p: any) => {
              newStations.push({
                label: `${rivers[i].river} — ${p.post}`,
                river: rivers[i].river,
                post: p.post,
                lat: p.lat || 62.0,
                lng: p.lon || 129.7,
                risk: 'low',
                critical_oya: p.critical_oya,
                low_oya: p.low_oya,
                has_model: p.has_model,
              });
            });
          });
          console.info(`[HydroPredict] Built ${newStations.length} stations from ${rivers.length} rivers`);
          if (newStations.length > 0) {
            setStations(newStations);
            const keep = preserveLabel && newStations.some(s => s.label === preserveLabel)
              ? preserveLabel
              : newStations.find(s => s.label === station)?.label ?? newStations[0].label;
            setStation(keep);
            const sel = newStations.find(s => s.label === keep) || newStations[0];
            if (sel.critical_oya) setDangerLevel(sel.critical_oya);
            if (sel.low_oya != null && !Number.isNaN(sel.low_oya)) setMinLevel(sel.low_oya);
          }
          setApiConnected(true);
        });
      })
      .catch((err) => {
        console.error('[HydroPredict] loadStationsFromApi FAILED:', err);
        setApiConnected(false);
      });
  }, [station]);

  useEffect(() => {
    console.info(`[HydroPredict] Startup — API_BASE="${API_BASE}", DEV=${import.meta.env.DEV}`);
    fetch(`${API_BASE}/health`)
      .then(async (r) => {
        const data = await r.json().catch(() => ({}));
        console.info('[HydroPredict] /health →', r.status, data);
        setApiConnected(!!r.ok && !!data.ok);
        setDbReady(!!data.db);
        setDbError(
          data.db
            ? null
            : (data.db_path
              ? `БД не найдена: ${data.db_path}. Запустите prepare_ml_data.py или загрузите CSV.`
              : 'База данных недоступна'),
        );
      })
      .catch((err) => {
        console.error('[HydroPredict] /health FAILED:', err);
        setApiConnected(false);
        setDbReady(false);
        setDbError('API недоступен — запустите npm run start или проверьте контейнер dataset-water');
      });
    loadStationsFromApi();
  }, []);

  useEffect(() => {
    return () => {
      if (trainPollRef.current) clearInterval(trainPollRef.current);
    };
  }, []);

  const fetchTrainingHistory = useCallback(() => {
    fetch(`${API_BASE}/train/history?limit=25`)
      .then(r => r.ok ? r.json() : [])
      .then(setTrainingHistory)
      .catch(() => setTrainingHistory([]));
  }, []);

  const currentStation = stations.find(s => s.label === station) || stations[0];

  const fetchDataStats = useCallback(() => {
    fetch(`${API_BASE}/data/stats`)
      .then(async (r) => {
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(typeof err.detail === 'string' ? err.detail : r.statusText);
        }
        return r.json();
      })
      .then((stats) => {
        setDataStats(stats);
        setDbReady(true);
        setDbError(null);
      })
      .catch((e: Error) => {
        setDataStats(null);
        setDbError(e.message || 'Не удалось загрузить статистику БД');
      });
  }, []);

  const fetchDatasetPreview = useCallback(() => {
    if (!currentStation) return;
    const encR = encodeURIComponent(currentStation.river);
    const encP = encodeURIComponent(currentStation.post);
    setDatasetLoading(true);
    fetch(`${API_BASE}/history/${encR}/${encP}?days=30`)
      .then(async (r) => {
        if (!r.ok) {
          const err = await r.json().catch(() => ({}));
          throw new Error(typeof err.detail === 'string' ? err.detail : r.statusText);
        }
        return r.json();
      })
      .then((rows) => {
        setDatasetRows(Array.isArray(rows) ? rows.slice(-10).reverse() : []);
        setDbReady(true);
        setDbError(null);
      })
      .catch((e: Error) => {
        setDatasetRows([]);
        setDbError(e.message || 'Не удалось загрузить данные станции');
      })
      .finally(() => setDatasetLoading(false));
  }, [currentStation?.river, currentStation?.post]);

  const downloadTemplate = useCallback(async (kind: 'observations' | 'stations') => {
    try {
      const r = await fetch(`${API_BASE}/data/templates/${kind}`);
      if (!r.ok) {
        const err = await r.json().catch(() => ({}));
        throw new Error(typeof err.detail === 'string' ? err.detail : r.statusText);
      }
      const blob = await r.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${kind}_template.csv`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
    } catch (e: any) {
      setImportMsg({ kind: 'err', text: `Не удалось скачать шаблон: ${e.message || e}` });
    }
  }, []);

  const importCsv = useCallback(async (kind: 'observations' | 'stations', file: File) => {
    setImporting(kind);
    setImportMsg(null);
    try {
      const formData = new FormData();
      formData.append('file', file);
      const r = await fetch(`${API_BASE}/data/import/${kind}`, { method: 'POST', body: formData });
      const body = await r.json().catch(() => ({}));
      if (!r.ok) {
        throw new Error(typeof body.detail === 'string' ? body.detail : `HTTP ${r.status}`);
      }
      const parts = [`Импортировано: ${body.rows_inserted} из ${body.rows_total}`];
      if (body.rows_skipped) parts.push(`пропущено: ${body.rows_skipped}`);
      if (body.errors?.length) parts.push(`ошибки: ${body.errors.slice(0, 3).join('; ')}`);
      setImportMsg({ kind: 'ok', text: `${kind === 'observations' ? 'Наблюдения' : 'Станции'} — ${parts.join(', ')}` });
      fetchDataStats();
      fetchDatasetPreview();
      loadStationsFromApi(station);
    } catch (e: any) {
      setImportMsg({ kind: 'err', text: `Ошибка импорта: ${e.message || e}` });
    } finally {
      setImporting(null);
    }
  }, [fetchDataStats, fetchDatasetPreview, loadStationsFromApi, station]);

  useEffect(() => {
    if (mode !== 'data') return;
    fetchTrainingHistory();
    fetchDataStats();
    fetchDatasetPreview();
  }, [mode, fetchTrainingHistory, fetchDataStats, fetchDatasetPreview]);

  const fetchModelStatus = useCallback(() => {
    if (!currentStation) return;
    const encR = encodeURIComponent(currentStation.river);
    const encP = encodeURIComponent(currentStation.post);
    fetch(`${API_BASE}/stations/${encR}/${encP}/model-status`)
      .then(r => r.ok ? r.json() : null)
      .then(data => {
        if (data) setModelStatus(data);
      })
      .catch(() => setModelStatus(null));
  }, [currentStation?.river, currentStation?.post]);

  useEffect(() => {
    fetchModelStatus();
  }, [fetchModelStatus]);

  const startTrainingPoll = useCallback(() => {
    if (trainPollRef.current) clearInterval(trainPollRef.current);
    trainPollRef.current = setInterval(() => {
      fetch(`${API_BASE}/train/status`)
        .then(r => r.json())
        .then((s: TrainingStatus) => {
          setTrainingStatus(s);
          if (s.status === 'complete' || s.status === 'error') {
            if (trainPollRef.current) clearInterval(trainPollRef.current);
            trainPollRef.current = null;
            notifyTrainingFinished(s.status === 'complete', s.message);
            fetchTrainingHistory();
            fetchModelStatus();
          }
        })
        .catch(() => {
          if (trainPollRef.current) clearInterval(trainPollRef.current);
          trainPollRef.current = null;
        });
    }, 2000);
  }, [fetchTrainingHistory, fetchModelStatus]);

  const runTraining = useCallback(async (body: { river?: string; post?: string; fast: boolean }, label: string) => {
    await requestTrainingNotifications();
    const res = await fetch(`${API_BASE}/train`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || res.statusText);
    }
    setTrainingStatus({ status: 'training', progress: 0, current_station: label, message: 'Запущено…' });
    startTrainingPoll();
  }, [startTrainingPoll]);

  const dismissTrainingStatus = () => {
    fetch(`${API_BASE}/train/reset-status`, { method: 'POST' }).catch(() => {});
    setTrainingStatus({ status: 'idle', progress: 0, current_station: '', message: '' });
  };

  useEffect(() => {
    if (currentStation?.critical_oya) {
      setDangerLevel(currentStation.critical_oya);
      setWarningLevel(Math.round(currentStation.critical_oya * 0.7));
    }
    if (currentStation?.low_oya != null && !Number.isNaN(currentStation.low_oya)) {
      setMinLevel(currentStation.low_oya);
    }
  }, [station, currentStation?.critical_oya, currentStation?.low_oya]);

  useEffect(() => {
    if (currentStation) {
      setMapCenter([currentStation.lat, currentStation.lng]);
      setMapZoom(9);
    }
  }, [station, currentStation?.lat, currentStation?.lng]);

  useEffect(() => {
    if (!currentStation) return;
    const key = `${currentStation.river}|${currentStation.post}`;
    if (stationKeyRef.current !== key) {
      stationKeyRef.current = key;
      setYearSelected(new Date().getFullYear());
    }
  }, [currentStation?.river, currentStation?.post]);

  useEffect(() => {
    if (!currentStation) return;
    const encR = encodeURIComponent(currentStation.river);
    const encP = encodeURIComponent(currentStation.post);
    const dateQ = targetDate ? `?base_date=${targetDate}` : '';
    const dateHist = targetDate ? `?end_date=${targetDate}&days=60` : '?days=60';
    const yearParam = yearSelected > 0 ? yearSelected : new Date().getFullYear();

    const tierPath = mode === 'short' ? 'short' : mode === 'medium' ? 'medium' : mode === 'season' ? 'season' : null;

    if (tierPath) {
      setForecastLoading(true);
      setForecastError(null);
      fetch(`${API_BASE}/forecast/${encR}/${encP}/${tierPath}${dateQ}`)
        .then(async r => {
          const data = await r.json().catch(() => ({}));
          if (!r.ok) {
            const detail = typeof data.detail === 'string'
              ? data.detail
              : Array.isArray(data.detail)
                ? data.detail.map((x: { msg?: string }) => x.msg).filter(Boolean).join('; ')
                : 'Ошибка загрузки прогноза';
            throw new Error(detail);
          }
          return data;
        })
        .then(data => {
          setTierPayload(data);
          const pts = data.forecast || [];
          setApiForecast(pts);
          setIsMock(!!data.is_mock);
          setForecastError(null);
          if (pts.length) setApiConnected(true);
        })
        .catch((e: Error) => {
          setTierPayload(null);
          setApiForecast([]);
          setForecastError(e.message || 'Ошибка API');
        })
        .finally(() => setForecastLoading(false));
      const h = mode === 'short' ? 7 : mode === 'medium' ? 30 : 90;
      fetch(`${API_BASE}/explain/${encR}/${encP}?horizon=${h}${dateQ ? '&' + dateQ.slice(1) : ''}`)
        .then(r => r.ok ? r.json() : null)
        .then(setExplain)
        .catch(() => setExplain(null));
    } else if (mode === 'year') {
      setYearLoading(true);
      fetch(`${API_BASE}/forecast/${encR}/${encP}/year?year=${yearParam}&overlay_years=3`)
        .then(r => {
          if (!r.ok) throw new Error(String(r.status));
          return r.json();
        })
        .then(data => {
          setYearPayload(data);
          setApiConnected(true);
          if (data.view_type) {
            setYearChartFocus(data.view_type === 'past' ? 'past' : data.view_type === 'future' ? 'future' : data.view_type === 'mixed' ? 'mixed' : 'all');
          }
        })
        .catch(() => setYearPayload(null))
        .finally(() => setYearLoading(false));
      fetch(`${API_BASE}/explain/${encR}/${encP}?horizon=30`)
        .then(r => r.ok ? r.json() : null)
        .then(setExplain)
        .catch(() => setExplain(null));
    } else if (mode === 'norm') {
      const climQ = normExcludeYear ? `?year=${normExcludeYear}` : '';
      fetch(`${API_BASE}/climatology/${encR}/${encP}${climQ}`)
        .then(r => r.ok ? r.json() : { points: [] })
        .then(d => {
          setClimatology(d.points || []);
          setApiConnected(true);
        })
        .catch(() => setClimatology([]));
    }

    // Дашборды: подгружаем explain (горизонт 30) и историю для scatter/heatmap
    if (mode === 'dashboards' && currentStation) {
      fetch(`${API_BASE}/explain/${encR}/${encP}?horizon=30`)
        .then(r => r.ok ? r.json() : null)
        .then(setExplain)
        .catch(() => setExplain(null));
      fetch(`${API_BASE}/history/${encR}/${encP}?days=90`)
        .then(r => r.ok ? r.json() : [])
        .then(data => { if (Array.isArray(data)) setApiHistory(data); })
        .catch(() => setApiHistory([]));
    }

    if (mode === 'short' || mode === 'medium') {
      fetch(`${API_BASE}/history/${encR}/${encP}${dateHist}`)
        .then(r => r.ok ? r.json() : [])
        .then(data => { if (Array.isArray(data)) setApiHistory(data); })
        .catch(() => setApiHistory([]));
    }
  }, [currentStation, targetDate, mode, yearSelected, normExcludeYear]);

  useEffect(() => {
    if (!currentStation || (mode !== 'dashboards' && mode !== 'short' && mode !== 'medium')) return;
    const encR = encodeURIComponent(currentStation.river);
    const encP = encodeURIComponent(currentStation.post);
    const days = mode === 'short' ? 14 : 30;
    const q = `days=${days}&temp_delta=${tempMod}&precip_pct=${precipMod}&snow_pct=${snowMod}`;
    const t = window.setTimeout(() => {
      fetch(`${API_BASE}/forecast/${encR}/${encP}/scenarios?${q}`)
        .then(r => r.ok ? r.json() : null)
        .then(setScenarioPayload)
        .catch(() => setScenarioPayload(null));
    }, 400);
    return () => window.clearTimeout(t);
  }, [currentStation, mode, tempMod, precipMod, snowMod]);

  const { historyMapped, forecastMapped, forecastData } = React.useMemo(() => {
    const mapForecastPoint = (d: { date: string; median: number; q90?: number; q95?: number }) => {
      const iso = d.date?.slice(0, 10);
      const fullDate = iso ? new Date(`${iso}T12:00:00`) : new Date(d.date);
      return {
        date: format(fullDate, 'dd MMM', { locale: ru }),
        dateIso: iso || d.date,
        dateFull: formatDateRu(iso || d.date, 'd MMMM yyyy'),
        fullDate,
        median: d.median,
        q90: d.q90,
        q95: d.q95,
      };
    };

    if (mode === 'medium' && apiForecast.length > 0) {
      const foreMap = apiForecast.map(mapForecastPoint);
      return { historyMapped: [], forecastMapped: foreMap, forecastData: foreMap };
    }
    if (apiForecast.length === 0 && apiHistory.length === 0) {
      const mock = generateMockData(60, 400, tempMod, precipMod);
      return { historyMapped: mock, forecastMapped: mock, forecastData: mock };
    }
    const histMap = apiHistory.map(d => ({
      date: format(new Date(d.date), 'dd MMM', { locale: ru }),
      fullDate: new Date(d.date),
      median: d.water_level_cm,
      q90: d.water_level_cm,
      q95: d.water_level_cm,
    }));
    const foreMap = apiForecast.map(mapForecastPoint);
    
    return { historyMapped: histMap, forecastMapped: foreMap, forecastData: [...histMap, ...foreMap] };
  }, [apiForecast, apiHistory, tempMod, precipMod, targetDate, mode]);

  const maxQ95 = forecastMapped.length > 0 ? Math.max(...forecastMapped.map(d => d.q95)) : 0;
  const minForecastMedian = forecastMapped.length > 0
    ? Math.min(...forecastMapped.map(d => d.median))
    : 0;
  const lowWaterAlert = forecastMapped.length > 0 && minForecastMedian < minLevel;

  const currentRisk = maxQ95 >= dangerLevel ? 'ОПАСНЫЙ (ОЯ)' : maxQ95 >= warningLevel ? 'ПОВЫШЕННЫЙ (НЯ)' : 'НИЗКИЙ';
  const riskColor = maxQ95 >= dangerLevel ? 'text-red-500' : maxQ95 >= warningLevel ? 'text-orange-500' : 'text-emerald-500';

  return (
    <div className="flex h-screen bg-slate-50 text-slate-900 font-sans overflow-hidden">
      
      {/* Sidebar */}
      <aside className="w-80 bg-white border-r border-slate-200 flex flex-col h-full overflow-y-auto">
        <div className="p-6 border-b border-slate-100">
          <div className="flex items-center gap-3 text-blue-600 mb-2">
            <Activity className="w-6 h-6" />
            <h1 className="text-xl font-bold tracking-tight">HydroPredict</h1>
          </div>
          <p className="text-xs text-slate-500">Система вероятностного прогноза паводков</p>
          <a
            href="/dashboard.html"
            className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-slate-500 hover:text-blue-600 border border-slate-200 hover:border-blue-300 bg-slate-50 hover:bg-blue-50 px-3 py-1.5 rounded-lg transition-colors"
          >
            <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" /></svg>
            На портал
          </a>
        </div>

        <div className="p-4 flex-1 flex flex-col gap-6">
          <div className="space-y-2">
            <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Режим работы</label>
            <div className="flex flex-col gap-1">
              {[
                { id: 'short', label: 'Краткий (1–7 дн)', icon: Activity },
                { id: 'medium', label: 'Средний (14–30 дн)', icon: Calendar },
                { id: 'season', label: 'Сезонный', icon: Snowflake },
                { id: 'year', label: 'Год', icon: TrendingUp },
                { id: 'norm', label: 'Норма', icon: BarChart2 },
                { id: 'dashboards', label: 'Дашборды', icon: LayoutDashboard },
                { id: 'data', label: 'Управление данными', icon: Database },
              ].map((item) => (
                <button
                  key={item.id}
                  onClick={() => setMode(item.id as ForecastMode)}
                  className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                    mode === item.id 
                      ? 'bg-blue-50 text-blue-700' 
                      : 'text-slate-600 hover:bg-slate-50'
                  }`}
                >
                  <item.icon className={`w-4 h-4 ${mode === item.id ? 'text-blue-600' : 'text-slate-400'}`} />
                  {item.label}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-4">
            <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">
              Локация
              <span className="ml-2 text-[10px] font-normal normal-case text-slate-400">
                ({stations.length} станций{apiConnected ? '' : ' — offline'})
              </span>
            </label>
            <StationSearchSelect
              stations={stations}
              value={station}
              onChange={setStation}
            />
            {modelStatus ? (
              <div className="text-[11px] mt-2 p-2 rounded-lg bg-slate-50 border border-slate-200 space-y-1">
                <p className={modelStatus.has_model ? 'text-emerald-800 font-medium' : 'text-amber-800 font-medium'}>
                  {modelStatus.has_model ? 'Модель обучена' : 'Нет модели — демо-прогноз'}
                </p>
                {modelStatus.trained_at && (
                  <p className="text-slate-600">Обновлена: {modelStatus.trained_at.replace('T', ' ').slice(0, 16)}</p>
                )}
                {modelStatus.horizons?.length > 0 && (
                  <p className="text-slate-600">Горизонты: {modelStatus.horizons.join(', ')} дн.</p>
                )}
                {modelStatus.backend && (
                  <p className="text-slate-600">Модель: {modelStatus.backend === 'catboost' ? 'CatBoost' : 'XGBoost'}</p>
                )}
                {modelStatus.model_dir && (
                  <p className="text-slate-600 break-all" title={modelStatus.model_dir}>
                    Файлы: {modelStatus.model_dir}
                    {modelStatus.n_model_files ? ` (${modelStatus.n_model_files})` : ''}
                  </p>
                )}
                {modelStatus.git_add_command && (
                  <p className="text-slate-500 break-all font-mono text-[10px]">{modelStatus.git_add_command}</p>
                )}
                {modelStatus.data_through && (
                  <p className="text-slate-600">Данные в БД до: {modelStatus.data_through}</p>
                )}
                {modelStatus.last_training && (
                  <p className="text-slate-500">
                    Последнее обучение: {modelStatus.last_training.status}
                    {modelStatus.last_training.finished_at ? ` (${modelStatus.last_training.finished_at.slice(0, 10)})` : ''}
                  </p>
                )}
                <button
                  type="button"
                  onClick={fetchModelStatus}
                  className="text-blue-600 hover:underline mt-1"
                >
                  Проверить модель
                </button>
              </div>
            ) : (
              <p className="text-[11px] mt-1.5 text-slate-500">Загрузка статуса модели…</p>
            )}
          </div>

          {mode === 'year' && (
            <div className="space-y-2">
              <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Год обзора</label>
              <select
                value={yearSelected}
                onChange={e => setYearSelected(Number(e.target.value))}
                className="w-full bg-slate-50 border border-slate-200 rounded-lg px-3 py-2 text-sm"
              >
                <option value={new Date().getFullYear()}>
                  {new Date().getFullYear()} (текущий: факт + прогноз на весь год)
                </option>
                {(yearPayload?.available_years || modelStatus?.available_years || [])
                  .filter((y: number) => y !== new Date().getFullYear())
                  .map((y: number) => (
                    <option key={y} value={y}>{y} {y < new Date().getFullYear() ? '(архив)' : '(прогноз)'}</option>
                  ))}
              </select>
            </div>
          )}

          <div className="space-y-4">
            <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Дата прогноза</label>
            <input 
              type="date"
              value={targetDate}
              onChange={(e) => setTargetDate(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 text-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            />
            {forecastMapped.length > 0 && (
              <p className="text-[10px] text-slate-400 mt-1">
                Охват: {forecastMapped[0].date} — {forecastMapped[forecastMapped.length - 1].date}
              </p>
            )}
          </div>

          <div className="space-y-4">
            <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center justify-between">
              Критические уровни
              <AlertTriangle className="w-4 h-4 text-orange-400" />
            </label>
            <div className="space-y-3">
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-600">Повышенный (НЯ)</span>
                  <span className="font-mono text-orange-600">{warningLevel} см</span>
                </div>
                <input 
                  type="range" min="300" max="800" step="10" 
                  value={warningLevel} onChange={(e) => setWarningLevel(Number(e.target.value))}
                  className="w-full accent-orange-500"
                />
              </div>
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-600">Опасный (ОЯ)</span>
                  <span className="font-mono text-red-600">{dangerLevel} см</span>
                </div>
                <input 
                  type="range" min="400" max="1000" step="10" 
                  value={dangerLevel} onChange={(e) => setDangerLevel(Number(e.target.value))}
                  className="w-full accent-red-600"
                />
              </div>
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-600">Минимальный (низкий ОЯ)</span>
                  <span className="font-mono text-blue-600">{minLevel} см</span>
                </div>
                <input 
                  type="range" min={-300} max={2000} step="10" 
                  value={minLevel} onChange={(e) => setMinLevel(Number(e.target.value))}
                  className="w-full accent-blue-500"
                />
              </div>
            </div>
          </div>

          <div className="space-y-4 pt-4 border-t border-slate-100">
            <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider flex items-center gap-2">
              <Settings2 className="w-4 h-4" />
              Сценарий "Что если"
            </label>
            
            <div className="space-y-4">
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-600 flex items-center gap-1"><Thermometer className="w-3 h-3"/> Температура</span>
                  <span className="font-mono">{tempMod > 0 ? '+' : ''}{tempMod}°C</span>
                </div>
                <input 
                  type="range" min="-5" max="5" step="0.5" 
                  value={tempMod} onChange={(e) => setTempMod(Number(e.target.value))}
                  className="w-full accent-blue-500"
                />
              </div>
              
              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-600 flex items-center gap-1"><CloudRain className="w-3 h-3"/> Осадки</span>
                  <span className="font-mono">{precipMod}% от нормы</span>
                </div>
                <input 
                  type="range" min="0" max="200" step="10" 
                  value={precipMod} onChange={(e) => setPrecipMod(Number(e.target.value))}
                  className="w-full accent-blue-500"
                />
              </div>

              <div>
                <div className="flex justify-between text-xs mb-1">
                  <span className="text-slate-600 flex items-center gap-1"><Snowflake className="w-3 h-3"/> Снежный покров</span>
                  <span className="font-mono">{snowMod}%</span>
                </div>
                <input 
                  type="range" min="0" max="200" step="10" 
                  value={snowMod} onChange={(e) => setSnowMod(Number(e.target.value))}
                  className="w-full accent-blue-500"
                />
              </div>
            </div>
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 flex flex-col h-full overflow-hidden bg-slate-50/50">
        <header className="h-16 bg-white border-b border-slate-200 flex items-center px-8 justify-between shrink-0">
          <h2 className="text-lg font-semibold text-slate-800">
            {mode === 'short' && 'Краткосрочный прогноз (1–7 дней)'}
            {mode === 'medium' && 'Среднесрочный прогноз (14–30 дней)'}
            {mode === 'season' && 'Сезонный прогноз (весенний паводок)'}
            {mode === 'year' && 'Годовой обзор'}
            {mode === 'norm' && 'Климатическая норма'}
            {mode === 'dashboards' && 'Сводные аналитические дашборды'}
            {mode === 'data' && 'Каталог данных и ретрейн моделей'}
          </h2>
          <div className="flex items-center gap-3">
            <AgentAlertsBadge />
            {isMock && mode !== 'dashboards' && mode !== 'data' && mode !== 'norm' && (
              <span className="text-xs bg-amber-100 text-amber-800 px-2 py-1 rounded-lg border border-amber-200">Демо / нет модели</span>
            )}
            {trainingStatus.status === 'training' && (
              <div className="flex flex-col gap-0.5 bg-blue-50 px-3 py-1.5 rounded-lg border border-blue-100 max-w-md">
                <div className="flex items-center gap-2">
                  <Loader2 className="w-4 h-4 text-blue-600 animate-spin shrink-0" />
                  <span className="text-xs font-medium text-blue-700">
                    {trainingStatus.current_station}
                    {trainingStatus.stations_total
                      ? ` (${trainingStatus.station_index ?? '?'}/${trainingStatus.stations_total})`
                      : ''}
                    {' '}
                    — {Math.round(trainingStatus.progress * 100)}%
                  </span>
                </div>
                {(trainingStatus.step_detail || trainingStatus.message) && (
                  <span
                    className="text-[10px] text-blue-600/90 pl-6 truncate"
                    title={trainingStatus.step_detail || trainingStatus.message}
                  >
                    {trainingStatus.step_detail || trainingStatus.message}
                  </span>
                )}
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className="relative flex h-3 w-3">
                <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${apiConnected ? 'bg-emerald-400' : 'bg-amber-400'}`}></span>
                <span className={`relative inline-flex rounded-full h-3 w-3 ${apiConnected ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
              </span>
              <span className="text-sm font-medium text-slate-600">
                {apiConnected
                  ? (dbReady ? 'API и БД подключены' : 'API работает, БД не готова')
                  : 'API недоступен — npm run start'}
              </span>
              {dbError && apiConnected && !dbReady && (
                <span className="text-xs text-amber-700 max-w-xs truncate" title={dbError}>{dbError}</span>
              )}
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            
            {/* Natural Language Summary Card */}
            {(mode !== 'dashboards' && mode !== 'data' && mode !== 'norm') && (
              <div className="bg-white rounded-2xl p-6 shadow-sm border border-slate-200">
                <div className="flex gap-4">
                  <div className={`p-3 rounded-xl shrink-0 ${maxQ95 >= dangerLevel ? 'bg-red-50 text-red-600' : maxQ95 >= warningLevel ? 'bg-orange-50 text-orange-600' : 'bg-emerald-50 text-emerald-600'}`}>
                    {maxQ95 >= warningLevel ? <AlertOctagon className="w-8 h-8" /> : <Activity className="w-8 h-8" />}
                  </div>
                  <div>
                    <h3 className="text-sm font-medium text-slate-500 mb-1">Резюме модели (Ожидаемый риск: <span className={riskColor}>{currentRisk}</span>)</h3>
                    <p className="text-slate-800 leading-relaxed text-lg">
                      {explain?.narrative || (
                        <>Для <strong>{station}</strong> ожидается <span className="lowercase">{currentRisk}</span> риск.
                        Пик медианы ~<strong>{forecastData.length ? Math.round(Math.max(...forecastData.map(d => d.median || 0))) : 0} см</strong>,
                        q95 до <strong>{Math.round(maxQ95)} см</strong>.</>
                      )}
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* View Specific Content */}
            {(mode === 'short' || mode === 'season') && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <ExplainPanel explain={explain} isMock={isMock} />
                {currentStation && (
                  <FloodAgentPanel
                    river={currentStation.river}
                    post={currentStation.post}
                    horizon={mode === 'short' ? 7 : 14}
                  />
                )}
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm">
                    <p className="text-sm text-slate-500 mb-1">Базовая дата</p>
                    <div className="flex items-baseline gap-2">
                      <span className="text-lg font-bold text-slate-800">{tierPayload?.base_date || '—'}</span>
                      <span className="text-sm font-medium text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">-5 см/сут</span>
                    </div>
                  </div>
                  <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm">
                    <p className="text-sm text-slate-500 mb-1">Макс. прогноз (Медиана 0.5)</p>
                    <div className="flex items-baseline gap-2">
                      <span className="text-3xl font-bold text-slate-800">
                        {forecastMapped.length
                          ? Math.round(Math.max(...forecastMapped.map(d => d.median ?? 0)))
                          : '—'} см
                      </span>
                    </div>
                  </div>
                  <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm">
                    <p className="text-sm text-slate-500 mb-1">Риск превышения ОЯ (Квантиль 0.95)</p>
                    <div className="flex items-baseline gap-2">
                      <span className={`text-3xl font-bold ${maxQ95 >= dangerLevel ? 'text-red-600' : 'text-emerald-600'}`}>
                        {maxQ95 >= dangerLevel ? '>15%' : '<1%'}
                      </span>
                    </div>
                  </div>
                </div>

                <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
                  <h3 className="text-base font-semibold text-slate-800 mb-6">
                    Вероятностный гидрограф ({mode === 'short' ? '7' : mode === 'medium' ? '30' : '90'} дней)
                  </h3>
                  <QuantileLegend compact />
                  <div className="mt-3">
                    <HydroChart
                      data={mapTierToChart(apiForecast)}
                      warningLevel={warningLevel}
                      dangerLevel={dangerLevel}
                      minLevel={minLevel}
                    />
                  </div>
                </div>
              </div>
            )}

            {mode === 'medium' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <ExplainPanel explain={explain} isMock={isMock} />
                {currentStation && (
                  <FloodAgentPanel
                    river={currentStation.river}
                    post={currentStation.post}
                    horizon={30}
                  />
                )}
                <MediumForecastView
                  forecast={apiForecast}
                  tierPayload={tierPayload}
                  warningLevel={warningLevel}
                  dangerLevel={dangerLevel}
                  minLevel={minLevel}
                  isMock={isMock}
                  loading={forecastLoading}
                  forecastError={forecastError || undefined}
                />
              </div>
            )}

            {mode === 'year' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <ExplainPanel explain={explain} isMock={!yearPayload?.has_model} />
                <YearOverview
                  yearPayload={yearPayload}
                  warningLevel={warningLevel}
                  dangerLevel={dangerLevel}
                  minLevel={minLevel}
                  loading={yearLoading}
                  chartFocus={yearChartFocus}
                  onChartFocusChange={setYearChartFocus}
                />
              </div>
            )}

            {mode === 'norm' && (
              <div className="space-y-6">
                <ClimatologyChart
                  points={climatology}
                  excludeYear={normExcludeYear}
                  onExcludeYearChange={y => {
                    setNormExcludeYear(y);
                  }}
                />
                <ExplainPanel explain={explain} />
              </div>
            )}

            {mode === 'dashboards' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <p className="text-sm text-slate-600 bg-slate-50 border border-slate-200 rounded-xl px-4 py-3">
                  Данные виджетов — из последнего загруженного прогноза (откройте «Краткий» или «Средний» для актуализации).
                  {tierPayload?.base_date && (
                    <> База: <strong>{formatDateRu(tierPayload.base_date)}</strong>.</>
                  )}
                  {' '}Пики паводка <strong>по календарному году</strong> — только во вкладке «Год».
                </p>
                <div className="bg-white p-4 rounded-xl border border-slate-200 shadow-sm flex flex-wrap gap-2 items-center">
                  <span className="text-sm font-semibold text-slate-700 mr-2 flex items-center gap-1"><LayoutDashboard className="w-4 h-4"/> Управление виджетами:</span>
                  {AVAILABLE_WIDGETS.map(w => {
                    const isActive = activeWidgets.includes(w.id);
                    return (
                      <button
                        key={w.id}
                        onClick={() => isActive ? setActiveWidgets(aw => aw.filter(id => id !== w.id)) : setActiveWidgets(aw => [...aw, w.id])}
                        className={`px-3 py-1.5 text-xs font-medium rounded-full transition-colors flex items-center gap-1.5 ${
                          isActive ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-600 hover:bg-slate-200'
                        }`}
                      >
                        {isActive ? <X className="w-3 h-3" /> : <Plus className="w-3 h-3" />}
                        {w.label}
                      </button>
                    );
                  })}
                </div>

                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                  {activeWidgets.map(wId => {
                    const widgetDef = AVAILABLE_WIDGETS.find(w => w.id === wId)!;
                    const colClass = widgetDef.width === 'full' ? 'lg:col-span-2' : 'lg:col-span-1';
                    
                    if (wId === 'peak_analysis') {
                      if (forecastMapped.length === 0) return null;
                      let maxPoint = forecastMapped[0];
                      let minPoint = forecastMapped[0];
                      forecastMapped.forEach((d: { median: number; q95?: number }) => {
                        if (d.median > maxPoint.median) maxPoint = d;
                        if (d.median < minPoint.median) minPoint = d;
                      });
                      const peakSub = tierPayload?.base_date
                        ? `Прогноз от ${formatDateRu(tierPayload.base_date)}, горизонт ${forecastMapped.length} дн.`
                        : `Прогноз на ближайшие ${forecastMapped.length} дн.`;

                      return (
                        <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                          <h3 className="text-base font-semibold text-slate-800 mb-2 flex items-center justify-between">
                            <span>Экстремумы на горизонте прогноза</span>
                            <TrendingUp className="w-5 h-5 text-slate-400" />
                          </h3>
                          <p className="text-xs text-slate-500 mb-4">{peakSub}. Не путать с пиками паводка за календарный год (вкладка «Год»).</p>
                          
                          <div className="flex flex-wrap gap-4 mb-6">
                            <div className="flex-1 min-w-[200px] bg-red-50 rounded-xl p-4 border border-red-100">
                              <div className="text-xs text-red-600 font-semibold uppercase mb-1 flex items-center gap-1">Макс. медиана <TrendingUp className="w-3 h-3"/></div>
                              <div className="text-2xl font-bold text-slate-800">{Math.round(maxPoint.median)} <span className="text-sm font-medium text-slate-500">см</span></div>
                              <div className="text-sm text-slate-600 mt-1">
                                {(maxPoint as { dateFull?: string }).dateFull || maxPoint.date}
                              </div>
                              <div className="text-xs text-slate-500 mt-2">q95: {Math.round(maxPoint.q95 ?? maxPoint.median)} см</div>
                            </div>
                            <div className="flex-1 min-w-[200px] bg-blue-50 rounded-xl p-4 border border-blue-100">
                              <div className="text-xs text-blue-600 font-semibold uppercase mb-1 flex items-center gap-1">Мин. медиана</div>
                              <div className="text-2xl font-bold text-slate-800">{Math.round(minPoint.median)} <span className="text-sm font-medium text-slate-500">см</span></div>
                              <div className="text-sm text-slate-600 mt-1">
                                {(minPoint as { dateFull?: string }).dateFull || minPoint.date}
                              </div>
                              <div className="text-xs text-slate-500 mt-2">Медианный уровень на эту дату</div>
                            </div>
                          </div>

                          <div className="h-[300px] w-full">
                            <ResponsiveContainer width="100%" height="100%">
                              <ComposedChart data={forecastData} margin={{ top: 20, right: 30, left: 20, bottom: 20 }}>
                                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                                <XAxis dataKey="date" stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} minTickGap={30} />
                                <YAxis stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} domain={['dataMin - 50', 'dataMax + 50']} />
                                <RechartsTooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
                                <Line type="monotone" dataKey="median" name="Медианный уровень" stroke="#64748b" strokeWidth={2} dot={{r: 2}} activeDot={{r: 6}} />
                                <ReferenceDot x={maxPoint.date} y={maxPoint.median} r={6} fill="#ef4444" stroke="#ffffff" strokeWidth={2} />
                                <ReferenceDot x={minPoint.date} y={minPoint.median} r={6} fill="#3b82f6" stroke="#ffffff" strokeWidth={2} />
                              </ComposedChart>
                            </ResponsiveContainer>
                          </div>
                        </div>
                      );
                    }

                    if (wId === 'cross_model') {
                      const scenarios = scenarioPayload?.scenarios || [];
                      const palette: Record<string, string> = {
                        baseline: '#2563eb',
                        sliders: '#16a34a',
                        wet_warm: '#dc2626',
                        cold_dry: '#0891b2',
                        heavy_rain: '#9333ea',
                      };
                      const crossData = (() => {
                        if (!scenarios.length) return [];
                        const byDate: Record<string, Record<string, string | number>> = {};
                        scenarios.forEach((sc: any) => {
                          (sc.points || []).forEach((p: any) => {
                            const label = formatDateRu(p.date, 'dd MMM');
                            if (!byDate[label]) byDate[label] = { date: label };
                            byDate[label][sc.id] = Math.round(p.median);
                            if (sc.id === 'sliders') {
                              byDate[label]['sliders_q10'] = Math.round(p.q10 ?? p.median * 0.9);
                              byDate[label]['sliders_q95'] = Math.round(p.q95 ?? p.median * 1.15);
                            }
                          });
                        });
                        return Object.values(byDate).slice(0, 30);
                      })();
                      // p_exceed для сценария «ползунки»: P(q90 >= НЯ) без повторного запроса модели
                      const slidersSc = scenarios.find((s: any) => s.id === 'sliders');
                      const slidersPts = slidersSc?.points || [];
                      const pExceed = slidersPts.length > 0
                        ? slidersPts.filter((p: any) => (p.q90 ?? p.median) >= warningLevel).length / slidersPts.length
                        : null;
                      const willFloodVerdict = pExceed !== null
                        ? (pExceed >= 0.7 ? 'Вероятен паводок' : pExceed >= 0.3 ? 'Умеренный риск' : 'Низкий риск')
                        : null;
                      return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-2">Сценарийная комната «что если»</h3>
                        <p className="text-xs text-slate-500 mb-2">
                          От базового прогноза модели (CatBoost). Ползунки слева меняют сценарий «Ваш сценарий».
                          Пунктирные линии — оптимистичная (q10) и пессимистичная (q95) границы вашего сценария.
                          {scenarioPayload?.base_date && <> База: {formatDateRu(scenarioPayload.base_date)}.</>}
                        </p>
                        <QuantileLegend compact />
                        {!crossData.length ? (
                          <p className="text-sm text-slate-500 py-8 text-center">Загрузка сценариев… Откройте «Средний» или «Краткий» прогноз при необходимости.</p>
                        ) : (
                        <>
                        <div className="h-[350px] w-full mt-3">
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart data={crossData} margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                              <CartesianGrid stroke="#f1f5f9" vertical={false} />
                              <XAxis dataKey="date" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} />
                              <YAxis stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} domain={['dataMin - 50', 'dataMax + 50']} />
                              <RechartsTooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
                              <Legend wrapperStyle={{ paddingTop: '12px' }} />
                              <ReferenceLine y={warningLevel} stroke="#f97316" strokeDasharray="4 4" label={{ value: `НЯ ${warningLevel}`, fill: '#f97316', fontSize: 11 }} />
                              <ReferenceLine y={dangerLevel} stroke="#ef4444" strokeDasharray="4 4" label={{ value: `ОЯ ${dangerLevel}`, fill: '#ef4444', fontSize: 11 }} />
                              {scenarios.map((sc: any) => (
                                <Line
                                  key={sc.id}
                                  type="monotone"
                                  dataKey={sc.id}
                                  name={sc.label}
                                  stroke={palette[sc.id] || '#64748b'}
                                  strokeWidth={sc.id === 'sliders' ? 3 : 2}
                                  strokeDasharray={sc.id === 'baseline' ? undefined : '4 4'}
                                  dot={false}
                                />
                              ))}
                              <Line type="monotone" dataKey="sliders_q10" name="Оптимистичный (q10)" stroke="#93c5fd" strokeWidth={1.5} strokeDasharray="2 2" dot={false} />
                              <Line type="monotone" dataKey="sliders_q95" name="Пессимистичный (q95)" stroke="#ef4444" strokeWidth={1.5} strokeDasharray="2 2" dot={false} />
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
                        {pExceed !== null && (
                          <div className="mt-4 p-4 rounded-xl bg-slate-50 border border-slate-100 flex items-center justify-between">
                            <div>
                              <div className="text-xs text-slate-500 uppercase font-semibold">Вероятность превышения НЯ ({warningLevel} см)</div>
                              <div className="text-lg font-bold text-slate-800 mt-1">
                                P(q90 ≥ НЯ) = {(pExceed * 100).toFixed(0)}%
                                <span className="ml-3 text-sm font-medium text-slate-500">
                                  ({slidersPts.filter((p: any) => (p.q90 ?? p.median) >= warningLevel).length} из {slidersPts.length} дней)
                                </span>
                              </div>
                            </div>
                            <div className={`px-4 py-2 rounded-lg font-bold text-white ${
                              pExceed >= 0.7 ? 'bg-red-500' : pExceed >= 0.3 ? 'bg-orange-500' : 'bg-emerald-500'
                            }`}>
                              {willFloodVerdict}
                            </div>
                          </div>
                        )}
                        </>
                        )}
                      </div>
                    );}

                    if (wId === 'scatter') {
                      // Реальная корреляция: температура vs уровень из истории наблюдений
                      const scatterData = apiHistory
                        .filter((d: any) => d.temp_mean != null && d.water_level_cm != null)
                        .slice(-90)
                        .map((d: any) => ({
                          temp: Number(d.temp_mean),
                          level: Number(d.water_level_cm),
                        }));
                      const hasScatterData = scatterData.length >= 5;
                      return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                          <span>Корреляция: Температура и уровень воды (последние 90 дней)</span>
                          <BarChart2 className="w-5 h-5 text-slate-400" />
                        </h3>
                        {!hasScatterData ? (
                          <div className="h-[300px] w-full flex items-center justify-center text-sm text-slate-500 bg-slate-50 rounded-xl border border-dashed border-slate-300">
                            Недостаточно данных с температурой для построения диаграммы рассеяния
                          </div>
                        ) : (
                        <div className="h-[300px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                              <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" />
                              <XAxis type="number" dataKey="temp" name="Температура (°C)" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} domain={['dataMin - 2', 'dataMax + 2']} />
                              <YAxis type="number" dataKey="level" name="Уровень воды (см)" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} domain={['dataMin - 50', 'dataMax + 50']} />
                              <RechartsTooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
                              <Scatter name="Наблюдения" data={scatterData} fill="#3b82f6" opacity={0.8} />
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
                        )}
                      </div>
                    );}

                    if (wId === 'basin_risk') return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                          <span>Оценка уровня риска (Текущий пост)</span>
                          <Activity className="w-5 h-5 text-slate-400" />
                        </h3>
                        <div className="text-sm text-slate-600 mb-4">
                          Уровень риска рассчитывается автоматически на основе соотношения максимального прогнозного значения (Квантиль 0.95) к заданным критическим отметкам:
                        </div>
                        <ul className="space-y-3 mb-6">
                          <li className="flex items-center gap-2 text-sm"><div className="w-3 h-3 rounded-full bg-red-500"></div> Опасный (ОЯ): Квантиль 0.95 &ge; {dangerLevel} см</li>
                          <li className="flex items-center gap-2 text-sm"><div className="w-3 h-3 rounded-full bg-orange-400"></div> Повышенный (НЯ): Квантиль 0.95 &ge; {warningLevel} см</li>
                          <li className="flex items-center gap-2 text-sm"><div className="w-3 h-3 rounded-full bg-emerald-500"></div> Норма: Квантиль 0.95 &lt; {warningLevel} см</li>
                          <li className="flex items-center gap-2 text-sm"><div className="w-3 h-3 rounded-full bg-blue-500"></div> Минимальный (низкий ОЯ): {minLevel} см{lowWaterAlert ? ` — прогноз ниже (${Math.round(minForecastMedian)} см)` : ''}</li>
                        </ul>
                        <div className="p-4 rounded-xl bg-slate-50 border border-slate-100 flex items-center justify-between">
                          <div>
                            <div className="text-xs text-slate-500 uppercase font-semibold">Текущий расчет</div>
                            <div className="text-lg font-bold text-slate-800 mt-1">Макс. 95% = {Math.round(maxQ95)} см</div>
                          </div>
                          <div className={`px-4 py-2 rounded-lg font-bold text-white ${maxQ95 >= dangerLevel ? 'bg-red-500' : maxQ95 >= warningLevel ? 'bg-orange-500' : 'bg-emerald-500'}`}>
                            {maxQ95 >= dangerLevel ? 'ОПАСНЫЙ УРОВЕНЬ' : maxQ95 >= warningLevel ? 'ПОВЫШЕННЫЙ УРОВЕНЬ' : 'НОРМА'}
                          </div>
                        </div>
                      </div>
                    );

                    if (wId === 'feature_importance') {
                      // Реальная важность признаков из /api/explain
                      const fiRaw = explain?.feature_importance || {};
                      const fiData = Object.entries(fiRaw)
                        .map(([name, imp]) => ({ name, imp: Number(imp) }))
                        .sort((a, b) => b.imp - a.imp)
                        .slice(0, 7);
                      const hasFi = fiData.length > 0;
                      return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                          <span>Влияние предикторов модели (Feature Importance)</span>
                          <Settings2 className="w-5 h-5 text-slate-400" />
                        </h3>
                        {!hasFi ? (
                          <div className="h-[300px] w-full flex items-center justify-center text-sm text-slate-500 bg-slate-50 rounded-xl border border-dashed border-slate-300">
                            Нет данных о важности признаков. Запустите прогноз или обучение модели.
                          </div>
                        ) : (
                        <div className="h-[300px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart layout="vertical" data={fiData} margin={{ top: 5, right: 30, left: 40, bottom: 5 }}>
                              <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="#e2e8f0" />
                              <XAxis type="number" domain={[0, 'auto']} stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} />
                              <YAxis dataKey="name" type="category" stroke="#475569" fontSize={12} tickLine={false} axisLine={false} fontWeight={500} width={120} />
                              <RechartsTooltip cursor={{fill: 'transparent'}} contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
                              <Bar dataKey="imp" fill="#3b82f6" radius={[0, 4, 4, 0]} barSize={20} />
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                        )}
                      </div>
                    );}

                    if (wId === 'heatmap') {
                      // Реальная матрица корреляций из истории наблюдений
                      const hmKeys = [
                        { key: 'water_level_cm', label: 'Уров' },
                        { key: 'precip_mm', label: 'Осад' },
                        { key: 'temp_mean', label: 'Т°С' },
                      ];
                      const hmRows = apiHistory.filter((d: any) =>
                        d.water_level_cm != null && d.temp_mean != null && d.precip_mm != null
                      ).slice(-120);
                      const computeCorr = (a: number[], b: number[]) => {
                        const n = a.length;
                        if (n < 3) return 0;
                        const ma = a.reduce((s, v) => s + v, 0) / n;
                        const mb = b.reduce((s, v) => s + v, 0) / n;
                        let num = 0, da = 0, db = 0;
                        for (let i = 0; i < n; i++) {
                          const x = a[i] - ma, y = b[i] - mb;
                          num += x * y; da += x * x; db += y * y;
                        }
                        const denom = Math.sqrt(da * db);
                        return denom > 0 ? num / denom : 0;
                      };
                      const corrMatrix = hmKeys.map((_, i) =>
                        hmKeys.map((_, j) => {
                          if (i === j) return 1.0;
                          const ai = hmRows.map((d: any) => Number(d[hmKeys[i].key]));
                          const aj = hmRows.map((d: any) => Number(d[hmKeys[j].key]));
                          return Math.round(computeCorr(ai, aj) * 100) / 100;
                        })
                      );
                      const metrics = hmKeys.map(k => k.label);
                      const hasHmData = hmRows.length >= 10;
                      return (
                        <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                          <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                            <span>Матрица корреляций Пирсона</span>
                            <Map className="w-5 h-5 text-slate-400" />
                          </h3>
                          {!hasHmData ? (
                            <div className="h-[300px] w-full flex items-center justify-center text-sm text-slate-500 bg-slate-50 rounded-xl border border-dashed border-slate-300">
                              Недостаточно данных для расчёта корреляций (нужно минимум 10 дней с полными наблюдениями)
                            </div>
                          ) : (
                          <>
                          <div className="w-full aspect-square max-h-[300px] mx-auto flex flex-col">
                            <div className="flex">
                              <div className="w-12 h-6"></div>
                              {metrics.map(m => <div key={m} className="flex-1 text-center text-[10px] sm:text-xs font-medium text-slate-500">{m}</div>)}
                            </div>
                            {corrMatrix.map((row, i) => (
                              <div key={i} className="flex flex-1 mt-1">
                                <div className="w-12 flex items-center justify-end pr-2 text-[10px] sm:text-xs font-medium text-slate-500">{metrics[i]}</div>
                                {row.map((val, j) => {
                                  const isPos = val > 0;
                                  const absVal = Math.abs(val);
                                  const r = isPos ? Math.round(255 - (255 - 59)*absVal) : Math.round(255 - (255 - 239)*absVal);
                                  const g = isPos ? Math.round(255 - (255 - 130)*absVal) : Math.round(255 - (255 - 68)*absVal);
                                  const b = isPos ? Math.round(255 - (255 - 246)*absVal) : Math.round(255 - (255 - 68)*absVal);
                                  return (
                                    <div
                                      key={j}
                                      className="flex-1 m-px rounded flex items-center justify-center text-xs font-medium group relative cursor-pointer hover:ring-2 hover:ring-slate-300 transition-all border border-slate-100"
                                      style={{ backgroundColor: `rgb(${r},${g},${b})`, color: absVal > 0.5 ? 'white' : '#334155' }}
                                    >
                                      {val.toFixed(1)}
                                      <div className="absolute opacity-0 group-hover:opacity-100 bg-slate-800 text-white text-[10px] rounded px-2 py-1 -top-8 whitespace-nowrap z-10 pointer-events-none transition-opacity">
                                        {metrics[i]} x {metrics[j]}: {val.toFixed(2)}
                                      </div>
                                    </div>
                                  );
                                })}
                              </div>
                            ))}
                          </div>
                          <div className="flex items-center justify-between mt-4 text-[10px] text-slate-500 px-12">
                            <span>Обратная (-1)</span>
                            <div className="h-2 flex-1 mx-2 rounded bg-gradient-to-r from-red-500 via-white to-blue-500 border border-slate-200"></div>
                            <span>Прямая (1)</span>
                          </div>
                          </>
                          )}
                        </div>
                      );
                    }

                    if (wId === 'risk_pie') {
                      // Реальное распределение риска по всем загруженным станциям
                      const riskCounts = stations.reduce((acc, s) => {
                        const r = s.risk || 'low';
                        if (r === 'critical' || r === 'high') acc.danger += 1;
                        else if (r === 'medium') acc.warning += 1;
                        else acc.normal += 1;
                        return acc;
                      }, { danger: 0, warning: 0, normal: 0 });
                      const pieData = [
                        { name: 'Опасный', value: riskCounts.danger, color: '#ef4444' },
                        { name: 'Повышенный', value: riskCounts.warning, color: '#f97316' },
                        { name: 'В норме', value: riskCounts.normal, color: '#10b981' },
                      ].filter(d => d.value > 0);
                      const totalStations = stations.length;
                      return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                          <span>Долевое распределение по уровням риска ({totalStations} постов)</span>
                          <AlertOctagon className="w-5 h-5 text-slate-400" />
                        </h3>
                        {pieData.length === 0 ? (
                          <div className="h-[300px] w-full flex items-center justify-center text-sm text-slate-500 bg-slate-50 rounded-xl border border-dashed border-slate-300">
                            Нет данных о станциях для расчёта распределения
                          </div>
                        ) : (
                        <div className="h-[300px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie
                                data={pieData}
                                cx="50%" cy="50%" innerRadius={80} outerRadius={110} paddingAngle={5}
                                dataKey="value" stroke="none"
                                label={({name, percent}) => `${name} ${(percent * 100).toFixed(0)}%`}
                                labelLine={false}
                              >
                                {pieData.map((entry, index) => (
                                  <Cell key={`cell-${index}`} fill={entry.color} />
                                ))}
                              </Pie>
                              <RechartsTooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 8px 16px -4px rgb(0 0 0 / 0.1)' }} />
                            </PieChart>
                          </ResponsiveContainer>
                        </div>
                        )}
                      </div>
                    );}

                    return null;
                  })}
                </div>
              </div>
            )}

            {/* Map Section */}
            {mode !== 'data' && (
              <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm mt-6">
                <div className="flex flex-col sm:flex-row sm:items-center justify-between mb-6 gap-4">
                  <h3 className="text-base font-semibold text-slate-800 flex items-center gap-2">
                    <Map className="w-5 h-5 text-blue-500" />
                    Карта гидрологической обстановки
                  </h3>
                  <div className="flex flex-wrap items-center gap-3">
                    <select 
                      value={mapData} 
                      onChange={(e) => setMapData(e.target.value as any)}
                      className="text-xs border border-slate-200 rounded-lg px-2 py-1.5 outline-none text-slate-700 bg-slate-50 font-medium cursor-pointer"
                    >
                      <option value="risk">Уровень риска</option>
                      <option value="temp">Температурный фон</option>
                      <option value="snow">Снегозапасы</option>
                    </select>
                    <div className="flex bg-slate-100 rounded-lg p-0.5">
                      <button 
                        onClick={() => setMapStyle('scheme')}
                        className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors ${mapStyle === 'scheme' ? 'bg-white shadow-sm text-slate-800' : 'text-slate-500 hover:text-slate-700'}`}
                      >
                        Схема
                      </button>
                      <button 
                        onClick={() => setMapStyle('satellite')}
                        className={`text-xs px-3 py-1.5 rounded-md font-medium transition-colors ${mapStyle === 'satellite' ? 'bg-white shadow-sm text-slate-800' : 'text-slate-500 hover:text-slate-700'}`}
                      >
                        Спутник
                      </button>
                    </div>
                    <button
                      type="button"
                      onClick={() => centerOnRiver(currentStation.river)}
                      className="text-xs px-3 py-1.5 rounded-lg font-medium border border-slate-200 bg-slate-50 text-slate-700 hover:bg-slate-100 transition-colors flex items-center gap-1.5"
                    >
                      <Crosshair className="w-3.5 h-3.5 text-blue-500" />
                      Центрировать: {currentStation.river}
                    </button>
                  </div>
                </div>
                <div className="h-[400px] w-full rounded-xl overflow-hidden border border-slate-200 z-0 relative bg-slate-900">
                  <MapGL
                    longitude={mapCenter[1]}
                    latitude={mapCenter[0]}
                    zoom={mapZoom}
                    mapStyle={mapStyle === 'satellite' ? SATELLITE_STYLE : SCHEME_STYLE}
                    onMove={(e: ViewStateChangeEvent) => {
                      const { latitude, longitude, zoom } = e.viewState;
                      setMapCenter([latitude, longitude]);
                      setMapZoom(zoom);
                    }}
                    attributionControl={false}
                  >
                    {stations.map(s => {
                      let fillColor = '';
                      let dataValue = '';
                      let labelObj = '';
                      
                      if (mapData === 'risk') {
                        fillColor = s.risk === 'high' ? '#ef4444' : s.risk === 'medium' ? '#f97316' : '#10b981';
                        dataValue = s.risk === 'high' ? 'Критическая (ОЯ)' : s.risk === 'medium' ? 'Повышенная (НЯ)' : 'Низкая (Норма)';
                        labelObj = 'Опасность:';
                      } else if (mapData === 'temp') {
                        const temp = s.lat > 60 ? -5 + tempMod : 12 + tempMod;
                        fillColor = temp > 0 ? '#ef4444' : '#3b82f6';
                        dataValue = `${temp.toFixed(1)}°C`;
                        labelObj = 'Средняя T°:';
                      } else if (mapData === 'snow') {
                        const snow = Math.round((s.lat > 60 ? 120 : 15) * (snowMod / 100));
                        fillColor = snow > 50 ? '#0891b2' : '#38bdf8';
                        dataValue = `${snow} см`;
                        labelObj = 'Снежный покров:';
                      }

                      const markColor = mapStyle === 'satellite' ? 'rgba(255,255,255,0.8)' : 'white';
                      const isSelected = s.label === station;

                      return (
                        <Marker key={s.label} longitude={s.lng} latitude={s.lat} anchor="center">
                          <div className="relative group cursor-pointer" onClick={() => setStation(s.label)}>
                            <div 
                              className={`rounded-full shadow-md transition-all ${isSelected ? 'w-6 h-6 relative z-10' : 'w-4 h-4'}`}
                              style={{ 
                                backgroundColor: fillColor,
                                opacity: mapStyle === 'satellite' ? 0.9 : 0.8,
                                border: `${isSelected ? '3px' : '1px'} solid ${isSelected ? '#3b82f6' : markColor}` 
                              }} 
                            />
                            <div className="absolute top-1/2 left-full ml-3 -translate-y-1/2 bg-white px-3 py-2 rounded-lg shadow-xl border border-slate-200 opacity-0 group-hover:opacity-100 transition-opacity pointer-events-none z-50 whitespace-nowrap">
                              <div className="text-sm font-semibold text-slate-800">{s.label}</div>
                              <div className="text-xs text-slate-600 mt-1">{labelObj} <strong>{dataValue}</strong></div>
                            </div>
                          </div>
                        </Marker>
                      );
                    })}
                  </MapGL>
                  <div className="absolute bottom-2 left-2 right-2 text-[10px] text-white/90 bg-black/40 px-2 py-1 rounded pointer-events-none">
                    {mapStyle === 'satellite'
                      ? '© Esri, Maxar, Earthstar Geographics'
                      : '© CARTO, © OpenStreetMap contributors'}
                  </div>
                </div>
              </div>
            )}

            {mode === 'data' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                {dataStats && (
                  <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                    <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm">
                      <p className="text-xs text-slate-500 uppercase font-semibold">Записей в БД</p>
                      <p className="text-2xl font-bold text-slate-800 mt-1">{dataStats.total_records?.toLocaleString('ru-RU') ?? '—'}</p>
                    </div>
                    <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm">
                      <p className="text-xs text-slate-500 uppercase font-semibold">Станций</p>
                      <p className="text-2xl font-bold text-slate-800 mt-1">{dataStats.total_stations ?? '—'}</p>
                    </div>
                    <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm">
                      <p className="text-xs text-slate-500 uppercase font-semibold">Диапазон дат</p>
                      <p className="text-sm font-semibold text-slate-800 mt-2">
                        {dataStats.date_range?.[0] && dataStats.date_range?.[1]
                          ? `${dataStats.date_range[0]} — ${dataStats.date_range[1]}`
                          : '—'}
                      </p>
                    </div>
                  </div>
                )}
                {dbError && (
                  <div className="bg-amber-50 border border-amber-200 text-amber-900 rounded-xl px-4 py-3 text-sm">
                    {dbError}
                  </div>
                )}
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                  <div className="lg:col-span-2 bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
                    <h3 className="text-base font-semibold text-slate-800 mb-4 flex items-center gap-2">
                      <Database className="w-5 h-5 text-blue-500" />
                      Импорт данных (CSV)
                    </h3>
                    {importMsg && (
                      <div className={
                        importMsg.kind === 'ok'
                          ? "bg-emerald-50 border border-emerald-200 text-emerald-900 rounded-xl px-4 py-3 text-sm mb-4"
                          : "bg-red-50 border border-red-200 text-red-900 rounded-xl px-4 py-3 text-sm mb-4"
                      }>
                        {importMsg.text}
                      </div>
                    )}
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                      <div className="border-2 border-dashed border-slate-200 bg-slate-50 rounded-xl p-5 flex flex-col">
                        <h4 className="text-sm font-semibold text-slate-700 mb-1">Наблюдения (уровни воды)</h4>
                        <p className="text-xs text-slate-500 mb-3">
                          Обязательные колонки: river, post, date. Опционально: water_level_cm, temp_min/mean/max, precip_mm, snow_pct_norm, ice_thickness_cm. Дубликаты перезаписываются.
                        </p>
                        <div className="mt-auto flex flex-wrap gap-2">
                          <button
                            onClick={() => downloadTemplate('observations')}
                            className="bg-slate-100 text-slate-700 text-xs font-medium px-3 py-2 rounded-lg hover:bg-slate-200 transition-colors border border-slate-200 inline-flex items-center gap-1.5"
                          >
                            <FileText className="w-3.5 h-3.5" /> Шаблон CSV
                          </button>
                          <label className="bg-blue-600 text-white text-xs font-medium px-3 py-2 rounded-lg hover:bg-blue-700 transition-colors cursor-pointer inline-flex items-center gap-1.5 disabled:opacity-50">
                            <Upload className="w-3.5 h-3.5" />
                            {importing === 'observations' ? 'Импорт…' : 'Импортировать'}
                            <input type="file" className="hidden" accept=".csv" disabled={importing !== null} onChange={(e) => {
                              const file = e.target.files?.[0];
                              e.target.value = '';
                              if (!file) return;
                              importCsv('observations', file);
                            }} />
                          </label>
                        </div>
                      </div>
                      <div className="border-2 border-dashed border-slate-200 bg-slate-50 rounded-xl p-5 flex flex-col">
                        <h4 className="text-sm font-semibold text-slate-700 mb-1">Станции (гидропосты)</h4>
                        <p className="text-xs text-slate-500 mb-3">
                          Обязательные колонки: river, post. Опционально: lat, lon, low_oya, critical_oya, district, oktmo, name_ru. Существующие станции обновляются.
                        </p>
                        <div className="mt-auto flex flex-wrap gap-2">
                          <button
                            onClick={() => downloadTemplate('stations')}
                            className="bg-slate-100 text-slate-700 text-xs font-medium px-3 py-2 rounded-lg hover:bg-slate-200 transition-colors border border-slate-200 inline-flex items-center gap-1.5"
                          >
                            <FileText className="w-3.5 h-3.5" /> Шаблон CSV
                          </button>
                          <label className="bg-blue-600 text-white text-xs font-medium px-3 py-2 rounded-lg hover:bg-blue-700 transition-colors cursor-pointer inline-flex items-center gap-1.5 disabled:opacity-50">
                            <Upload className="w-3.5 h-3.5" />
                            {importing === 'stations' ? 'Импорт…' : 'Импортировать'}
                            <input type="file" className="hidden" accept=".csv" disabled={importing !== null} onChange={(e) => {
                              const file = e.target.files?.[0];
                              e.target.value = '';
                              if (!file) return;
                              importCsv('stations', file);
                            }} />
                          </label>
                        </div>
                      </div>
                    </div>
                  </div>
                  <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col">
                    <h3 className="text-base font-semibold text-slate-800 mb-4 flex items-center gap-2">
                      <RefreshCw className="w-5 h-5 text-blue-500" />
                      Пайплайн обучения
                    </h3>
                    <p className="text-sm text-slate-600 mb-4">
                      После загрузки новых гидрологических наблюдений необходимо переобучить ансамбль для обновления весов.
                      Страница <strong>не перезагружается</strong> — по готовности придёт уведомление; обновите вкладку вручную (F5).
                    </p>
                    <p className="text-xs text-slate-500 mb-4 bg-slate-50 rounded-lg px-3 py-2 border border-slate-100">
                      <strong>Одна станция</strong> — кнопка ниже: модель сохраняется в <code className="text-[10px]">models/река/пост/</code> и в БД (метаданные).
                      После перезапуска API переобучать не нужно. В GitHub: <code className="text-[10px]">git add models/…</code> (см. models/README.md).
                      <br />
                      <strong>Быстрое</strong>: ~5–15 мин. <strong>Все станции</strong> ({stations.length}) — только если нужен массовый прогон.
                    </p>
                    {typeof Notification !== 'undefined' && Notification.permission === 'denied' && (
                      <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-2 py-1.5 mb-4">
                        Push отключены в браузере. Разрешите уведомления для этого сайта в настройках.
                      </p>
                    )}
                    <div className="space-y-3 mt-auto">
                      <button 
                        onClick={() => {
                          const stInfo = stations.find(s => s.label === station);
                          runTraining(
                            { river: stInfo?.river, post: stInfo?.post, fast: true },
                            station,
                          ).catch(e => alert(e.message || 'API недоступен. Запустите: npm run start'));
                        }}
                        disabled={trainingStatus.status === 'training'}
                        className="w-full bg-slate-800 text-white text-sm font-medium px-4 py-2.5 rounded-lg hover:bg-slate-900 transition-colors flex justify-center items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {trainingStatus.status === 'training' ? <><Loader2 className="w-4 h-4 animate-spin" /> Обучение…</> : 'Быстрое (~5–15 мин, 1 станция)'}
                      </button>
                      <button 
                        onClick={() => {
                          const n = stations.length;
                          const ok = window.confirm(
                            `Обучить все ${n} станций в ускоренном пакетном режиме?\n\n` +
                            `Ориентир: ~8–15 мин на станцию (суммарно несколько часов).\n` +
                            `В шапке будет видно: горизонт, квантиль, номер станции.`,
                          );
                          if (!ok) return;
                          runTraining({ fast: false }, 'Все станции').catch(e => alert(e.message || 'API недоступен'));
                        }}
                        disabled={trainingStatus.status === 'training'}
                        className="w-full bg-slate-100 text-slate-700 text-sm font-medium px-4 py-2.5 rounded-lg hover:bg-slate-200 transition-colors flex justify-center items-center gap-2 border border-slate-200 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        Все станции (пакетное, ускоренное)
                      </button>
                    </div>
                  </div>
                </div>
                
                <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm">
                  <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
                    <h3 className="text-base font-semibold text-slate-800 flex items-center gap-2">
                      <RefreshCw className="w-5 h-5 text-slate-400" />
                      История обучения (БД)
                    </h3>
                    <div className="flex items-center gap-3">
                      <label className="text-xs flex items-center gap-1 cursor-pointer">
                        <input
                          type="checkbox"
                          checked={historyOnlyCurrent}
                          onChange={e => setHistoryOnlyCurrent(e.target.checked)}
                        />
                        Только эта станция
                      </label>
                      <button
                        type="button"
                        onClick={fetchTrainingHistory}
                        className="text-xs text-blue-600 hover:underline"
                      >
                        Обновить
                      </button>
                    </div>
                  </div>
                  <div className="overflow-x-auto max-h-64">
                    <table className="w-full text-xs text-left">
                      <thead className="bg-slate-50 text-slate-600 sticky top-0">
                        <tr>
                          <th className="px-4 py-2">Начало</th>
                          <th className="px-4 py-2">Объект</th>
                          <th className="px-4 py-2">Статус</th>
                          <th className="px-4 py-2">Станций</th>
                          <th className="px-4 py-2">Сообщение</th>
                        </tr>
                      </thead>
                      <tbody>
                        {trainingHistory.length === 0 ? (
                          <tr><td colSpan={5} className="px-4 py-6 text-slate-500 text-center">Запусков обучения пока нет</td></tr>
                        ) : trainingHistory
                          .filter(h => !historyOnlyCurrent || !currentStation || (
                            (h.river === currentStation.river && h.post === currentStation.post) ||
                            (h.scope === 'all')
                          ))
                          .map(h => (
                          <tr key={h.id} className="border-t border-slate-100">
                            <td className="px-4 py-2 whitespace-nowrap">{h.started_at?.replace('T', ' ').replace('Z', '')}</td>
                            <td className="px-4 py-2">
                              {h.scope === 'station' && h.river ? `${h.river} — ${h.post}` : h.scope === 'river' ? h.river : 'Все станции'}
                              <span className="text-slate-500 block text-[10px]">
                                {h.fast ? 'Быстрое: 5 ит. Optuna × 45 с' : 'Пакетное: 5 ит. × 45 с на станцию'}
                              </span>
                            </td>
                            <td className="px-4 py-2">
                              <span className={`px-2 py-0.5 rounded-full ${
                                h.status === 'success' ? 'bg-emerald-100 text-emerald-800' :
                                h.status === 'partial' ? 'bg-amber-100 text-amber-800' :
                                h.status === 'running' ? 'bg-blue-100 text-blue-800' :
                                'bg-red-100 text-red-800'
                              }`}>{h.status}</span>
                            </td>
                            <td className="px-4 py-2">{h.stations_trained}/{h.stations_total}</td>
                            <td className="px-4 py-2 text-slate-600 max-w-xs truncate" title={h.message}>{h.message}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>

                <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm">
                  <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
                    <h3 className="text-base font-semibold text-slate-800 flex items-center gap-2">
                      <FileText className="w-5 h-5 text-slate-400" />
                      Фрагмент текущего датасета ({station})
                    </h3>
                    <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${dbReady ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>
                      {datasetLoading ? 'Загрузка…' : dbReady ? 'Данные из БД' : 'БД недоступна'}
                    </span>
                    <button
                      type="button"
                      onClick={() => { fetchDataStats(); fetchDatasetPreview(); }}
                      className="text-xs text-blue-600 hover:underline"
                    >
                      Обновить
                    </button>
                  </div>
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                      <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                        <tr>
                          <th className="px-6 py-3 font-semibold">date</th>
                          <th className="px-6 py-3 font-semibold">water_level_cm</th>
                          <th className="px-6 py-3 font-semibold">temp_mean_c</th>
                          <th className="px-6 py-3 font-semibold">precip_mm</th>
                          <th className="px-6 py-3 font-semibold">snow_depth_cm</th>
                          <th className="px-6 py-3 font-semibold">status</th>
                        </tr>
                      </thead>
                      <tbody>
                        {datasetLoading ? (
                          <tr><td colSpan={6} className="px-6 py-8 text-slate-500 text-center">Загрузка из API…</td></tr>
                        ) : datasetRows.length === 0 ? (
                          <tr><td colSpan={6} className="px-6 py-8 text-slate-500 text-center">Нет данных для станции — проверьте БД или загрузите CSV</td></tr>
                        ) : datasetRows.map((row, i) => (
                          <tr key={`${row.date}-${i}`} className="border-b border-slate-100 hover:bg-slate-50/50">
                            <td className="px-6 py-3 font-mono text-slate-800">{row.date}</td>
                            <td className="px-6 py-3 text-blue-600 font-medium">{row.water_level_cm}</td>
                            <td className="px-6 py-3 text-slate-600">{row.temp_mean ?? '—'}</td>
                            <td className="px-6 py-3 text-slate-600">{row.precip_mm ?? '—'}</td>
                            <td className="px-6 py-3 text-slate-600">{row.snow_depth_cm ?? '—'}</td>
                            <td className="px-6 py-3">
                              <span className="text-[10px] uppercase font-bold px-2 py-0.5 rounded-sm bg-emerald-100 text-emerald-700">DB</span>
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

          </div>
        </div>
      </main>

      {(trainingStatus.status === 'complete' || trainingStatus.status === 'error') && trainingStatus.message && (
        <div
          className={`fixed bottom-6 right-6 z-[100] max-w-sm rounded-xl shadow-2xl border p-4 ${
            trainingStatus.status === 'complete'
              ? 'bg-emerald-50 border-emerald-300 text-emerald-900'
              : 'bg-red-50 border-red-300 text-red-900'
          }`}
          role="alert"
        >
          <p className="font-semibold text-sm mb-1">
            {trainingStatus.status === 'complete' ? 'Обучение завершено' : 'Ошибка обучения'}
          </p>
          <p className="text-sm mb-3">{trainingStatus.message}</p>
          {trainingStatus.status === 'complete' && (
            <>
              <p className="text-xs mb-2 opacity-90">
                Модель на диске — при следующем запуске API подхватится автоматически. F5 — обновить статус в UI.
              </p>
              {currentStation && modelStatus?.git_add_command && (
                <p className="text-xs mb-2 font-mono break-all opacity-90">
                  GitHub: {modelStatus.git_add_command}
                </p>
              )}
            </>
          )}
          <button
            type="button"
            onClick={dismissTrainingStatus}
            className="text-xs font-medium underline hover:no-underline"
          >
            Закрыть
          </button>
        </div>
      )}
    </div>
  );
}

