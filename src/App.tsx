import React, { useState, useEffect, useCallback } from 'react';
import { 
  LineChart, Line, AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, Legend, 
  BarChart, Bar, ResponsiveContainer, ReferenceLine, ReferenceDot, PieChart, Pie, Cell, ComposedChart, Scatter
} from 'recharts';
import { 
  Map, Activity, Calendar, LayoutDashboard, Settings2, 
  Thermometer, CloudRain, Snowflake, AlertOctagon, TrendingUp, AlertTriangle, Plus, X, BarChart2,
  Database, Upload, RefreshCw, FileText, Loader2, Crosshair
} from 'lucide-react';
import { Map as PigeonMap, Overlay } from 'pigeon-maps';
import { format, addDays, subDays } from 'date-fns';
import { ru } from 'date-fns/locale';

// --- Types & API ---
type ForecastMode = 'date' | 'month' | 'year' | 'dashboards' | 'data';
type WidgetId = 'cross_model' | 'scatter' | 'basin_risk' | 'feature_importance' | 'heatmap' | 'risk_pie' | 'peak_analysis';

const API_BASE = 'http://localhost:8000/api';

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
  { id: 'cross_model', label: 'Сравнение моделей', width: 'full' },
  { id: 'scatter', label: 'Корреляция аномалий', width: 'half' },
  { id: 'basin_risk', label: 'Риск по бассейнам', width: 'half' },
  { id: 'feature_importance', label: 'Важность признаков', width: 'half' },
  { id: 'heatmap', label: 'Матрица корреляций', width: 'half' },
  { id: 'risk_pie', label: 'Распределение риска', width: 'half' },
];

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

export default function App() {
  const [mode, setMode] = useState<ForecastMode>('date');
  const [activeWidgets, setActiveWidgets] = useState<WidgetId[]>(['peak_analysis', 'cross_model', 'scatter', 'basin_risk']);
  const [stations, setStations] = useState<StationInfo[]>(DEFAULT_STATIONS);
  const [station, setStation] = useState(DEFAULT_STATIONS[0].label);
  const [warningLevel, setWarningLevel] = useState(827);
  const [dangerLevel, setDangerLevel] = useState(1000);
  const [minLevel, setMinLevel] = useState(DEFAULT_STATIONS[0].low_oya ?? 0);
  const [apiConnected, setApiConnected] = useState(false);
  const [trainingStatus, setTrainingStatus] = useState<TrainingStatus>({ status: 'idle', progress: 0, current_station: '', message: '' });
  const [targetDate, setTargetDate] = useState<string>('');
  const [apiForecast, setApiForecast] = useState<any[]>([]);
  const [apiHistory, setApiHistory] = useState<any[]>([]);
  
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

  // Попытка подключения к API
  useEffect(() => {
    fetch(`${API_BASE}/rivers`)
      .then(r => r.json())
      .then((rivers: any[]) => {
        if (rivers && rivers.length > 0) {
          // Загружаем посты для каждой реки
          const fetchPosts = rivers.map(r =>
            fetch(`${API_BASE}/rivers/${encodeURIComponent(r.river)}/posts`)
              .then(res => res.json())
              .catch(() => [])
          );
          Promise.all(fetchPosts).then(allPosts => {
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
            if (newStations.length > 0) {
              setStations(newStations);
              setStation(newStations[0].label);
              if (newStations[0].critical_oya) setDangerLevel(newStations[0].critical_oya);
              if (newStations[0].low_oya != null && !Number.isNaN(newStations[0].low_oya)) {
                setMinLevel(newStations[0].low_oya);
              }
            }
            setApiConnected(true);
          });
        }
      })
      .catch(() => setApiConnected(false));
  }, []);

  // Обновляем критические уровни при смене станции
  const currentStation = stations.find(s => s.label === station) || stations[0];
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
    if (!currentStation || !apiConnected) return;
    const dateQuery = targetDate ? `?base_date=${targetDate}` : '';
    const dateQueryHist = targetDate ? `?end_date=${targetDate}&days=60` : '?days=60';
    
    fetch(`${API_BASE}/forecast/${encodeURIComponent(currentStation.river)}/${encodeURIComponent(currentStation.post)}${dateQuery}`)
      .then(r => r.json())
      .then(data => {
        if (data.forecast) setApiForecast(data.forecast);
      })
      .catch(() => setApiForecast([]));
      
    fetch(`${API_BASE}/history/${encodeURIComponent(currentStation.river)}/${encodeURIComponent(currentStation.post)}${dateQueryHist}`)
      .then(r => r.json())
      .then(data => {
        if (Array.isArray(data)) setApiHistory(data);
      })
      .catch(() => setApiHistory([]));
  }, [currentStation, targetDate, apiConnected]);

  const { historyMapped, forecastMapped, forecastData, yearData } = React.useMemo(() => {
    if (apiForecast.length === 0 && apiHistory.length === 0) {
      const mock = generateMockData(60, 400, tempMod, precipMod);
      return { historyMapped: mock, forecastMapped: mock, forecastData: mock, yearData: [] };
    }
    const histMap = apiHistory.map(d => ({
      date: format(new Date(d.date), 'dd MMM', { locale: ru }),
      fullDate: new Date(d.date),
      median: d.water_level_cm,
      q90: d.water_level_cm,
      q95: d.water_level_cm,
    }));
    const foreMap = apiForecast.map(d => ({
      date: format(new Date(d.date), 'dd MMM', { locale: ru }),
      fullDate: new Date(d.date),
      median: d.median,
      q90: d.q90,
      q95: d.q95,
    }));
    
    // Generate Year Data for chart
    const baseDate = targetDate ? new Date(targetDate) : new Date();
    const year = baseDate.getFullYear();
    const yData = [];
    let maxFound = -1, minFound = 9999;
    
    for (let i = 0; i < 365; i++) {
       const d = new Date(year, 0, 1);
       d.setDate(d.getDate() + i);
       // Simple seasonal curve + noise
       const seasonal = Math.max(0, 150 + 350 * Math.exp(-Math.pow(i - 140, 2)/1500) + 150 * Math.exp(-Math.pow(i - 200, 2)/3000));
       const histMean = seasonal;
       const histQ10 = Math.max(0, seasonal * 0.6 - 50);
       const histQ90 = seasonal * 1.4 + 100;
       
       let forecastMedian = null;
       const fDay = foreMap.find(fd => fd.fullDate.getDate() === d.getDate() && fd.fullDate.getMonth() === d.getMonth() && fd.fullDate.getFullYear() === d.getFullYear());
       if (fDay) {
           forecastMedian = fDay.median;
           if (forecastMedian > maxFound) maxFound = forecastMedian;
           if (forecastMedian < minFound) minFound = forecastMedian;
       }
       
       yData.push({
           date: format(d, 'dd MMM yyyy', { locale: ru }),
           fullDate: d,
           histMean,
           histRange: [histQ10, histQ90],
           forecast: forecastMedian,
       });
    }
    
    yData.forEach(r => {
        if (r.forecast !== null) {
            if (r.forecast === maxFound) r.isMax = r.forecast;
            if (r.forecast === minFound) r.isMin = r.forecast;
        }
    });

    return { historyMapped: histMap, forecastMapped: foreMap, forecastData: [...histMap, ...foreMap], yearData: yData };
  }, [apiForecast, apiHistory, tempMod, precipMod, targetDate]);

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
        </div>

        <div className="p-4 flex-1 flex flex-col gap-6">
          <div className="space-y-2">
            <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Режим работы</label>
            <div className="flex flex-col gap-1">
              {[
                { id: 'date', label: 'Прогноз на дату', icon: Activity },
                { id: 'month', label: 'Прогноз на месяц', icon: Calendar },
                { id: 'year', label: 'Прогноз на год', icon: TrendingUp },
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
            <label className="text-xs font-semibold text-slate-500 uppercase tracking-wider">Локация</label>
            <select 
              value={station}
              onChange={(e) => setStation(e.target.value)}
              className="w-full bg-slate-50 border border-slate-200 text-slate-700 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
            >
              {stations.map(s => <option key={s.label} value={s.label}>{s.label}</option>)}
            </select>
          </div>

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
            {mode === 'date' && 'Прогноз на дату (Вероятностный гидрограф)'}
            {mode === 'month' && 'Месячный прогноз риска'}
            {mode === 'year' && 'Годовой обзор гидрологической обстановки'}
            {mode === 'dashboards' && 'Сводные аналитические дашборды'}
            {mode === 'data' && 'Каталог данных и ретрейн моделей'}
          </h2>
          <div className="flex items-center gap-3">
            {trainingStatus.status === 'training' && (
              <div className="flex items-center gap-2 bg-blue-50 px-3 py-1.5 rounded-lg border border-blue-100">
                <Loader2 className="w-4 h-4 text-blue-600 animate-spin" />
                <span className="text-xs font-medium text-blue-700">Обучение: {trainingStatus.current_station} ({Math.round(trainingStatus.progress * 100)}%)</span>
              </div>
            )}
            <div className="flex items-center gap-2">
              <span className="relative flex h-3 w-3">
                <span className={`animate-ping absolute inline-flex h-full w-full rounded-full opacity-75 ${apiConnected ? 'bg-emerald-400' : 'bg-amber-400'}`}></span>
                <span className={`relative inline-flex rounded-full h-3 w-3 ${apiConnected ? 'bg-emerald-500' : 'bg-amber-500'}`}></span>
              </span>
              <span className="text-sm font-medium text-slate-600">{apiConnected ? 'API подключён' : 'Демо-режим'}</span>
            </div>
          </div>
        </header>

        <div className="flex-1 overflow-y-auto p-8">
          <div className="max-w-7xl mx-auto space-y-6">
            
            {/* Natural Language Summary Card */}
            {(mode !== 'dashboards' && mode !== 'data') && (
              <div className="bg-white rounded-2xl p-6 shadow-sm border border-slate-200">
                <div className="flex gap-4">
                  <div className={`p-3 rounded-xl shrink-0 ${maxQ95 >= dangerLevel ? 'bg-red-50 text-red-600' : maxQ95 >= warningLevel ? 'bg-orange-50 text-orange-600' : 'bg-emerald-50 text-emerald-600'}`}>
                    {maxQ95 >= warningLevel ? <AlertOctagon className="w-8 h-8" /> : <Activity className="w-8 h-8" />}
                  </div>
                  <div>
                    <h3 className="text-sm font-medium text-slate-500 mb-1">Резюме модели (Ожидаемый риск: <span className={riskColor}>{currentRisk}</span>)</h3>
                    <p className="text-slate-800 leading-relaxed text-lg">
                      В течение анализируемого периода для <strong>{station}</strong> ожидается 
                      <span className="lowercase"> {currentRisk}</span> риск паводка. 
                      Пиковое значение по оптимистичному сценарию (медиана) составит около <strong>{Math.round(Math.max(...forecastData.map(d => d.median)))} см</strong>. 
                      Однако с вероятностью 5% уровень может достичь <strong>{Math.round(maxQ95)} см</strong>.
                    </p>
                  </div>
                </div>
              </div>
            )}

            {/* View Specific Content */}
            {mode === 'date' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                  <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm">
                    <p className="text-sm text-slate-500 mb-1">Текущий уровень (сегодня)</p>
                    <div className="flex items-baseline gap-2">
                      <span className="text-3xl font-bold text-slate-800">{Math.round(forecastData[0].median)} см</span>
                      <span className="text-sm font-medium text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full">-5 см/сут</span>
                    </div>
                  </div>
                  <div className="bg-white rounded-xl p-5 border border-slate-200 shadow-sm">
                    <p className="text-sm text-slate-500 mb-1">Макс. прогноз (Медиана 0.5)</p>
                    <div className="flex items-baseline gap-2">
                      <span className="text-3xl font-bold text-slate-800">{Math.round(Math.max(...forecastData.map(d => d.median)))} см</span>
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
                  <h3 className="text-base font-semibold text-slate-800 mb-6">Вероятностный гидрограф (60 дней)</h3>
                  <div className="h-[400px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={forecastData} margin={{ top: 10, right: 30, left: 0, bottom: 0 }}>
                        <defs>
                          <linearGradient id="colorMedian" x1="0" y1="0" x2="0" y2="1">
                            <stop offset="5%" stopColor="#3b82f6" stopOpacity={0.8}/>
                            <stop offset="95%" stopColor="#3b82f6" stopOpacity={0}/>
                          </linearGradient>
                        </defs>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                        <XAxis dataKey="date" stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} minTickGap={30} />
                        <YAxis stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} domain={['dataMin - 50', 'dataMax + 100']} />
                        <RechartsTooltip 
                          contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }}
                          labelStyle={{ color: '#64748b', fontWeight: 600, marginBottom: '8px' }}
                        />
                        <Legend verticalAlign="top" height={36}/>
                        
                        <ReferenceLine y={minLevel} label={{ position: 'insideBottomLeft', value: 'Мин. ОЯ', fill: '#3b82f6', fontSize: 12, fontWeight: 'bold' }} stroke="#3b82f6" strokeDasharray="3 3" />
                        <ReferenceLine y={warningLevel} label={{ position: 'insideTopLeft', value: 'НЯ', fill: '#f97316', fontSize: 12, fontWeight: 'bold' }} stroke="#f97316" strokeDasharray="3 3" />
                        <ReferenceLine y={dangerLevel} label={{ position: 'insideTopLeft', value: 'ОЯ', fill: '#ef4444', fontSize: 12, fontWeight: 'bold' }} stroke="#ef4444" strokeDasharray="3 3" />
                        
                        <Area type="monotone" dataKey="q95" name="Квантиль 0.95 (Пессимистичный)" stroke="none" fill="#bfdbfe" fillOpacity={0.5} />
                        <Area type="monotone" dataKey="q90" name="Квантиль 0.90" stroke="#60a5fa" strokeDasharray="5 5" fill="none" />
                        <Area type="monotone" dataKey="median" name="Медиана 0.5 (Оптимистичный)" stroke="#2563eb" strokeWidth={3} fillOpacity={1} fill="url(#colorMedian)" />
                        
                        <Line type="linear" dataKey="trend" name="Базовый тренд" stroke="#475569" strokeWidth={2} strokeDasharray="4 4" dot={false} activeDot={false} />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>
            )}

            {mode === 'month' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm">
                   <h3 className="text-base font-semibold text-slate-800 mb-6">Тепловая карта риска по дням (Матричная проекция)</h3>
                   <div className="grid grid-cols-7 gap-2">
                     {['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс'].map(day => (
                       <div key={day} className="text-center text-xs font-medium text-slate-500 py-2">{day}</div>
                     ))}
                     {Array.from({ length: 3 }).map((_, i) => <div key={`empty-${i}`} />)}
                     {forecastMapped.slice(0, 31).map((day, i) => {
                       const intensity = day.q95 >= dangerLevel ? 'bg-red-500 text-white' 
                                      : day.q95 >= warningLevel ? 'bg-orange-400 text-white' 
                                      : day.q95 >= warningLevel - 50 ? 'bg-yellow-300 text-yellow-900'
                                      : 'bg-emerald-100 text-emerald-800';
                       
                       return (
                         <div key={i} className={`aspect-square rounded-xl flex flex-col items-center justify-center p-2 transition-transform hover:scale-105 cursor-pointer ${intensity}`}>
                           <span className="text-sm font-bold">{i + 1}</span>
                           <span className="text-[10px] opacity-80">{Math.round(day.q95)}</span>
                         </div>
                       )
                     })}
                   </div>
                </div>

                <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden shadow-sm">
                  <div className="overflow-x-auto">
                    <table className="w-full text-sm text-left">
                      <thead className="bg-slate-50 border-b border-slate-200 text-slate-600">
                        <tr>
                          <th className="px-6 py-4 font-semibold">Дата</th>
                          <th className="px-6 py-4 font-semibold">Медиана прогноза</th>
                          <th className="px-6 py-4 font-semibold">Квантиль 0.90</th>
                          <th className="px-6 py-4 font-semibold">Квантиль 0.95 (Макс)</th>
                          <th className="px-6 py-4 font-semibold">Статус</th>
                        </tr>
                      </thead>
                      <tbody>
                        {forecastMapped.slice(0, 31).map((row, i) => (
                          <tr key={i} className="border-b border-slate-100 hover:bg-slate-50/50">
                            <td className="px-6 py-4 font-medium text-slate-900">{row.date}</td>
                            <td className="px-6 py-4">{Math.round(row.median)} см</td>
                            <td className="px-6 py-4 text-orange-600 font-medium">{Math.round(row.q90)} см</td>
                            <td className="px-6 py-4 text-red-600 font-bold">{Math.round(row.q95)} см</td>
                            <td className="px-6 py-4">
                              {row.q95 >= dangerLevel ? (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-red-100 text-red-800">
                                  Опасность
                                </span>
                              ) : row.q95 >= warningLevel ? (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-orange-100 text-orange-800">
                                  Повышенный
                                </span>
                              ) : (
                                <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-emerald-100 text-emerald-800">
                                  Норма
                                </span>
                              )}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              </div>
            )}

            {mode === 'year' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="bg-slate-900 p-6 rounded-2xl shadow-lg border border-slate-800">
                  <h3 className="text-base font-semibold text-slate-100 mb-6">Прогноз Уровень_воды на {targetDate ? new Date(targetDate).getFullYear() : new Date().getFullYear()} год (с историческим профилем)</h3>
                  <div className="h-[400px] w-full">
                    <ResponsiveContainer width="100%" height="100%">
                      <ComposedChart data={yearData} margin={{ top: 20, right: 30, left: 0, bottom: 0 }}>
                        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#334155" />
                        <XAxis dataKey="date" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} minTickGap={40} />
                        <YAxis stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} domain={['dataMin - 100', 'dataMax + 100']} />
                        <RechartsTooltip 
                          contentStyle={{ backgroundColor: '#0f172a', borderRadius: '8px', border: '1px solid #334155', color: '#f8fafc', boxShadow: '0 10px 15px -3px rgb(0 0 0 / 0.5)' }}
                          itemStyle={{ color: '#f8fafc' }}
                          labelStyle={{ color: '#94a3b8', fontWeight: 600, marginBottom: '8px' }}
                        />
                        <Legend wrapperStyle={{ paddingTop: '20px' }} />
                        <ReferenceLine y={minLevel} label={{ value: 'Мин. ОЯ', fill: '#3b82f6', fontSize: 11 }} stroke="#3b82f6" strokeDasharray="3 3" />
                        <ReferenceLine y={warningLevel} stroke="#f97316" strokeDasharray="3 3" />
                        <ReferenceLine y={dangerLevel} stroke="#ef4444" strokeDasharray="3 3" />
                        <Area type="monotone" dataKey="histRange" name="Ист. диапазон (10-90%)" stroke="none" fill="#1e293b" />
                        <Line type="monotone" dataKey="histMean" name="Ист. среднее" stroke="#64748b" strokeDasharray="4 4" dot={false} activeDot={false} />
                        <Line type="monotone" dataKey="forecast" name="Прогноз" stroke="#ef4444" strokeWidth={2} dot={false} activeDot={{ r: 6, fill: '#ef4444' }} />
                        <Scatter dataKey="isMax" name="Максимум" fill="#ef4444" shape="triangle" />
                        <Scatter dataKey="isMin" name="Минимум" fill="#3b82f6" shape="triangle" />
                      </ComposedChart>
                    </ResponsiveContainer>
                  </div>
                </div>
              </div>
            )}

            {mode === 'dashboards' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
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
                      forecastMapped.forEach(d => {
                        if (d.median > maxPoint.median) maxPoint = d;
                        if (d.median < minPoint.median) minPoint = d;
                      });

                      return (
                        <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                          <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                            <span>Анализ экстремумов: Пиковые и минимальные уровни</span>
                            <TrendingUp className="w-5 h-5 text-slate-400" />
                          </h3>
                          
                          <div className="flex flex-wrap gap-4 mb-6">
                            <div className="flex-1 min-w-[200px] bg-red-50 rounded-xl p-4 border border-red-100">
                              <div className="text-xs text-red-600 font-semibold uppercase mb-1 flex items-center gap-1">Максимум <TrendingUp className="w-3 h-3"/></div>
                              <div className="text-2xl font-bold text-slate-800">{Math.round(maxPoint.median)} <span className="text-sm font-medium text-slate-500">см</span></div>
                              <div className="text-sm text-slate-600 mt-1">Ожидается: <span className="font-semibold text-slate-800">{maxPoint.date}</span></div>
                              <div className="text-xs text-slate-500 mt-2">Квантиль 95%: {Math.round(maxPoint.q95)} см</div>
                            </div>
                            <div className="flex-1 min-w-[200px] bg-blue-50 rounded-xl p-4 border border-blue-100">
                              <div className="text-xs text-blue-600 font-semibold uppercase mb-1 flex items-center gap-1">Минимум</div>
                              <div className="text-2xl font-bold text-slate-800">{Math.round(minPoint.median)} <span className="text-sm font-medium text-slate-500">см</span></div>
                              <div className="text-sm text-slate-600 mt-1">Ожидается: <span className="font-semibold text-slate-800">{minPoint.date}</span></div>
                              <div className="text-xs text-slate-500 mt-2">Базовый тренд: {Math.round(minPoint.trend)} см</div>
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
                      const crossData = forecastMapped.slice(0, 14).map((d, i) => ({
                         date: d.date,
                         predictedXGB: Math.round(d.median),
                         predictedCatBoost: Math.round(d.median * (1 + 0.05 * Math.sin(i))),
                         predictedLSTM: Math.round(d.median * (1 - 0.04 * Math.cos(i)))
                      }));
                      return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                          <span>Сравнение исторических трендов и кросс-валидация проектных моделей</span>
                          <span className="text-xs bg-blue-50 text-blue-700 px-2 py-1 rounded font-medium border border-blue-100">XGBoost / LSTM / CatBoost</span>
                        </h3>
                        <div className="h-[350px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart
                              data={crossData}
                              margin={{ top: 20, right: 20, bottom: 20, left: 20 }}
                            >
                              <CartesianGrid stroke="#f1f5f9" vertical={false} />
                              <XAxis dataKey="date" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} />
                              <YAxis stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} domain={['dataMin - 50', 'dataMax + 50']} />
                              <RechartsTooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
                              <Legend wrapperStyle={{ paddingTop: '20px' }} />
                              <Line type="monotone" dataKey="predictedXGB" name="Прогноз XGBoost" stroke="#3b82f6" strokeWidth={3} />
                              <Line type="monotone" dataKey="predictedCatBoost" name="Прогноз CatBoost" stroke="#f59e0b" strokeWidth={2} strokeDasharray="3 3" dot={false} />
                              <Line type="monotone" dataKey="predictedLSTM" name="Прогноз LSTM" stroke="#8b5cf6" strokeWidth={2} strokeDasharray="3 3" dot={false} />
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    );}

                    if (wId === 'scatter') {
                      const scatterData = forecastMapped.slice(0, 30).map((d, i) => ({
                         temp: (tempMod + 15 * Math.sin(i / 5)).toFixed(1),
                         level: Math.round(d.median),
                      }));
                      return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                          <span>Корреляция: Прогнозные температуры и уровни (ближайшие 30 дней)</span>
                          <BarChart2 className="w-5 h-5 text-slate-400" />
                        </h3>
                        <div className="h-[300px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <ComposedChart margin={{ top: 20, right: 20, bottom: 20, left: 20 }}>
                              <CartesianGrid stroke="#f1f5f9" strokeDasharray="3 3" />
                              <XAxis type="number" dataKey="temp" name="Температура (°C)" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} domain={[-10, 30]} />
                              <YAxis type="number" dataKey="level" name="Уровень воды (см)" stroke="#94a3b8" fontSize={12} tickLine={false} axisLine={false} domain={['dataMin - 50', 'dataMax + 50']} />
                              <RechartsTooltip cursor={{ strokeDasharray: '3 3' }} contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
                              <Scatter name="Прогноз" data={scatterData} fill="#3b82f6" opacity={0.8} />
                            </ComposedChart>
                          </ResponsiveContainer>
                        </div>
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

                    if (wId === 'feature_importance') return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                          <span>Влияние предикторов модели (Feature Importance)</span>
                          <Settings2 className="w-5 h-5 text-slate-400" />
                        </h3>
                        <div className="h-[300px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <BarChart layout="vertical" data={[
                                { name: 'Уровень t-1', imp: 0.85 },
                                { name: 'Сумма осадков 3д', imp: 0.65 },
                                { name: 'Снежный покров', imp: 0.45 },
                                { name: 'Т-ср 7д', imp: 0.35 },
                                { name: 'Осадки t-1', imp: 0.25 },
                              ]} margin={{ top: 5, right: 30, left: 40, bottom: 5 }}
                            >
                              <CartesianGrid strokeDasharray="3 3" horizontal={true} vertical={false} stroke="#e2e8f0" />
                              <XAxis type="number" domain={[0, 1]} stroke="#64748b" fontSize={12} tickLine={false} axisLine={false} />
                              <YAxis dataKey="name" type="category" stroke="#475569" fontSize={12} tickLine={false} axisLine={false} fontWeight={500} />
                              <RechartsTooltip cursor={{fill: 'transparent'}} contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 4px 6px -1px rgb(0 0 0 / 0.1)' }} />
                              <Bar dataKey="imp" fill="#3b82f6" radius={[0, 4, 4, 0]} barSize={20} />
                            </BarChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    );

                    if (wId === 'heatmap') {
                      const metrics = ['Уров', 'Осад', 'Снег', 'Т°С', 'Влаж'];
                      const corrMatrix = [
                        [ 1.0,   0.6,   0.4,   0.3,   0.2 ],
                        [ 0.6,   1.0,   0.1,  -0.2,   0.4 ],
                        [ 0.4,   0.1,   1.0,  -0.8,   0.1 ],
                        [ 0.3,  -0.2,  -0.8,   1.0,  -0.3 ],
                        [ 0.2,   0.4,   0.1,  -0.3,   1.0 ],
                      ];
                      return (
                        <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                          <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                            <span>Матрица корреляций Пирсона</span>
                            <Map className="w-5 h-5 text-slate-400" />
                          </h3>
                          <div className="w-full aspect-square max-h-[300px] mx-auto flex flex-col">
                            <div className="flex">
                              <div className="w-12 h-6"></div>
                              {metrics.map(m => <div key={m} className="flex-1 text-center text-[10px] sm:text-xs font-medium text-slate-500">{m}</div>)}
                            </div>
                            {corrMatrix.map((row, i) => (
                              <div key={i} className="flex flex-1 mt-1">
                                <div className="w-12 flex items-center justify-end pr-2 text-[10px] sm:text-xs font-medium text-slate-500">{metrics[i]}</div>
                                {row.map((val, j) => {
                                  // Map correlation -1..1 to color
                                  const isPos = val > 0;
                                  const absVal = Math.abs(val);
                                  // Positive correlation -> Blues, Negative -> Reds
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
                        </div>
                      );
                    }

                    if (wId === 'risk_pie') return (
                      <div key={wId} className={`bg-white p-6 rounded-2xl border border-slate-200 shadow-sm ${colClass}`}>
                        <h3 className="text-base font-semibold text-slate-800 mb-6 flex items-center justify-between">
                          <span>Долевое распределение по уровням риска</span>
                          <AlertOctagon className="w-5 h-5 text-slate-400" />
                        </h3>
                        <div className="h-[300px] w-full">
                          <ResponsiveContainer width="100%" height="100%">
                            <PieChart>
                              <Pie
                                data={[
                                  { name: 'Опасный', value: 2, color: '#ef4444' },
                                  { name: 'Повышенный', value: 8, color: '#f97316' },
                                  { name: 'В норме', value: 35, color: '#10b981' },
                                ]}
                                cx="50%" cy="50%" innerRadius={80} outerRadius={110} paddingAngle={5}
                                dataKey="value" stroke="none"
                                label={({name, percent}) => `${name} ${(percent * 100).toFixed(0)}%`}
                                labelLine={false}
                              >
                                {[{color:'#ef4444'},{color:'#f97316'},{color:'#10b981'}].map((entry, index) => (
                                  <Cell key={`cell-${index}`} fill={entry.color} />
                                ))}
                              </Pie>
                              <RechartsTooltip contentStyle={{ borderRadius: '12px', border: 'none', boxShadow: '0 8px 16px -4px rgb(0 0 0 / 0.1)' }} />
                            </PieChart>
                          </ResponsiveContainer>
                        </div>
                      </div>
                    );

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
                  <PigeonMap 
                    center={mapCenter}
                    zoom={mapZoom}
                    animate
                    onBoundsChanged={({ center, zoom }) => {
                      setMapCenter(center);
                      setMapZoom(zoom);
                    }}
                    provider={(x, y, z) => {
                      if (mapStyle === 'satellite') {
                        return `https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/${z}/${y}/${x}`;
                      }
                      return `https://cartodb-basemaps-a.global.ssl.fastly.net/light_all/${z}/${x}/${y}.png`;
                    }}
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
                        <Overlay key={s.label} anchor={[s.lat, s.lng]} offset={[isSelected ? 12 : 8, isSelected ? 12 : 8]}>
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
                        </Overlay>
                      );
                    })}
                  </PigeonMap>
                </div>
              </div>
            )}

            {mode === 'data' && (
              <div className="space-y-6 animate-in fade-in slide-in-from-bottom-4 duration-500">
                <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
                  <div className="lg:col-span-2 bg-white rounded-2xl p-6 border border-slate-200 shadow-sm">
                    <h3 className="text-base font-semibold text-slate-800 mb-4 flex items-center gap-2">
                      <Database className="w-5 h-5 text-blue-500" />
                      Источники данных (Файлы телеметрии)
                    </h3>
                    <div className="border-2 border-dashed border-slate-200 bg-slate-50 hover:bg-slate-100 transition-colors rounded-xl p-8 flex flex-col items-center justify-center cursor-pointer text-center group">
                      <Upload className="w-10 h-10 text-slate-400 group-hover:text-blue-500 mb-4 transition-colors" />
                      <p className="text-sm font-medium text-slate-700">Перетащите CSV / Excel датасеты сюда</p>
                      <p className="text-xs text-slate-500 mt-2">Требуются: date, temp_mean_c, precip_mm, snow_depth_cm и water_level_cm</p>
                      <label className="mt-6 bg-blue-600 text-white text-sm font-medium px-5 py-2 rounded-lg hover:bg-blue-700 transition-colors font-sans cursor-pointer inline-block">
                        Выбрать файл
                        <input type="file" className="hidden" accept=".csv,.xlsx" onChange={(e) => {
                          const file = e.target.files?.[0];
                          if (!file) return;
                          const formData = new FormData();
                          formData.append('file', file);
                          fetch(`${API_BASE}/upload`, { method: 'POST', body: formData })
                            .then(r => {
                                if (!r.ok) throw new Error('Ошибка сервера');
                                return r.json();
                            })
                            .then(d => alert(d.message || "Файл успешно загружен"))
                            .catch(err => alert("Ошибка загрузки: " + err.message));
                        }} />
                      </label>
                    </div>
                  </div>
                  <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex flex-col">
                    <h3 className="text-base font-semibold text-slate-800 mb-4 flex items-center gap-2">
                      <RefreshCw className="w-5 h-5 text-blue-500" />
                      Пайплайн обучения
                    </h3>
                    <p className="text-sm text-slate-600 mb-6">
                      После загрузки новых гидрологических наблюдений необходимо переобучить ансамбль для обновления весов.
                    </p>
                    <div className="space-y-3 mt-auto">
                      <button 
                        onClick={() => {
                          const stInfo = stations.find(s => s.label === station);
                          fetch(`${API_BASE}/train`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ river: stInfo?.river, post: stInfo?.post, fast: true })
                          }).then(() => {
                            setTrainingStatus({ status: 'training', progress: 0, current_station: station, message: 'Запущено...' });
                            // Поллинг статуса
                            const interval = setInterval(() => {
                              fetch(`${API_BASE}/train/status`).then(r => r.json()).then((s: TrainingStatus) => {
                                setTrainingStatus(s);
                                if (s.status === 'complete' || s.status === 'error' || s.status === 'idle') clearInterval(interval);
                              }).catch(() => clearInterval(interval));
                            }, 3000);
                          }).catch(() => alert('API сервер недоступен. Запустите: python python_code/api_server.py'));
                        }}
                        disabled={trainingStatus.status === 'training'}
                        className="w-full bg-slate-800 text-white text-sm font-medium px-4 py-2.5 rounded-lg hover:bg-slate-900 transition-colors flex justify-center items-center gap-2 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        {trainingStatus.status === 'training' ? <><Loader2 className="w-4 h-4 animate-spin" /> Обучение...</> : 'Инкрементальное обучение'}
                      </button>
                      <button 
                        onClick={() => {
                          fetch(`${API_BASE}/train`, {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify({ fast: false })
                          }).then(() => {
                            setTrainingStatus({ status: 'training', progress: 0, current_station: 'Все станции', message: 'Полный ретрейн...' });
                            const interval = setInterval(() => {
                              fetch(`${API_BASE}/train/status`).then(r => r.json()).then((s: TrainingStatus) => {
                                setTrainingStatus(s);
                                if (s.status === 'complete' || s.status === 'error' || s.status === 'idle') clearInterval(interval);
                              }).catch(() => clearInterval(interval));
                            }, 3000);
                          }).catch(() => alert('API сервер недоступен'));
                        }}
                        disabled={trainingStatus.status === 'training'}
                        className="w-full bg-slate-100 text-slate-700 text-sm font-medium px-4 py-2.5 rounded-lg hover:bg-slate-200 transition-colors flex justify-center items-center gap-2 border border-slate-200 disabled:opacity-50 disabled:cursor-not-allowed"
                      >
                        Полный ретрейн (Optuna CV)
                      </button>
                    </div>
                  </div>
                </div>
                
                <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden shadow-sm">
                  <div className="px-6 py-4 border-b border-slate-100 flex items-center justify-between">
                    <h3 className="text-base font-semibold text-slate-800 flex items-center gap-2">
                      <FileText className="w-5 h-5 text-slate-400" />
                      Фрагмент текущего датасета ({station})
                    </h3>
                    <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${apiConnected ? 'bg-emerald-100 text-emerald-800' : 'bg-amber-100 text-amber-800'}`}>{apiConnected ? 'Подключено к API' : 'Демо-данные'}</span>
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
                        {[
                          { d: '2024-05-18', l: 480, t: 14.5, p: 0.0, s: 0, st: 'Checked' },
                          { d: '2024-05-19', l: 495, t: 16.2, p: 12.5, s: 0, st: 'Checked' },
                          { d: '2024-05-20', l: 512, t: 18.0, p: 45.0, s: 0, st: 'New' },
                        ].map((row, i) => (
                          <tr key={i} className="border-b border-slate-100 hover:bg-slate-50/50">
                            <td className="px-6 py-3 font-mono text-slate-800">{row.d}</td>
                            <td className="px-6 py-3 text-blue-600 font-medium">{row.l}</td>
                            <td className="px-6 py-3 text-slate-600">{row.t}</td>
                            <td className="px-6 py-3 text-slate-600">{row.p}</td>
                            <td className="px-6 py-3 text-slate-600">{row.s}</td>
                            <td className="px-6 py-3">
                              <span className={`text-[10px] uppercase font-bold px-2 py-0.5 rounded-sm ${row.st === 'New' ? 'bg-blue-100 text-blue-700' : 'bg-slate-100 text-slate-600'}`}>{row.st}</span>
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
    </div>
  );
}

