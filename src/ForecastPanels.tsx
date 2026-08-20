import React, { useState, useEffect, useRef, useMemo } from 'react';
import {
  ComposedChart, Line, Area, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, Legend, ResponsiveContainer, ReferenceLine,
  ReferenceDot, BarChart, Bar, Cell, Brush
} from 'recharts';
import { format } from 'date-fns';
import { ru } from 'date-fns/locale';
import { AlertCircle, Info, ZoomIn } from './components/icons';
import { QUANTILE_HELP_SHORT, QUANTILE_LABELS, QUANTILE_LEGEND_ITEMS } from './quantiles';

export function QuantileLegend({ compact }: { compact?: boolean }) {
  if (compact) {
    return <p className="text-xs text-slate-500 leading-relaxed">{QUANTILE_HELP_SHORT}</p>;
  }
  return (
    <ul className="text-xs text-slate-600 space-y-1 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
      {QUANTILE_LEGEND_ITEMS.map(item => (
        <li key={item.key} className="flex items-start gap-2">
          <span className="w-2 h-2 rounded-full mt-1 shrink-0" style={{ background: item.color }} />
          <span>{item.text}</span>
        </li>
      ))}
    </ul>
  );
}

export interface ForecastPoint {
  date: string;
  median: number;
  q10?: number;
  q90?: number;
  q95?: number;
}

const OVERLAY_COLORS = ['#a78bfa', '#fbbf24', '#34d399', '#fb7185', '#38bdf8'];

/** Парсинг YYYY-MM-DD без сдвига часового пояса */
export function formatDateRu(iso: string | undefined, fmt = 'dd.MM.yyyy') {
  if (!iso) return '—';
  try {
    const d = iso.length === 10 ? new Date(`${iso}T12:00:00`) : new Date(iso);
    return format(d, fmt, { locale: ru });
  } catch {
    return iso;
  }
}

/** Recharts падает с width=-1, если контейнер ещё не отрисован */
function ChartBox({ height, children }: { height: number; children: React.ReactElement }) {
  const ref = useRef<HTMLDivElement>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const check = () => setReady(el.clientWidth > 2 && el.clientHeight > 2);
    check();
    const ro = new ResizeObserver(check);
    ro.observe(el);
    return () => ro.disconnect();
  }, [height]);

  return (
    <div ref={ref} className="w-full min-w-0" style={{ height, minHeight: height }}>
      {ready ? (
        <ResponsiveContainer width="100%" height={height} minWidth={0}>
          {children}
        </ResponsiveContainer>
      ) : (
        <div className="h-full flex items-center justify-center text-slate-400 text-sm">Загрузка графика…</div>
      )}
    </div>
  );
}

const FEATURE_LABELS_RU: Record<string, string> = {
  month: 'Месяц',
  day_of_year: 'День года',
  doy: 'День года',
  sin_doy: 'Сезонность (sin)',
  cos_doy: 'Сезонность (cos)',
  level_ma7: 'Средний уровень 7д',
  level_ma14: 'Средний уровень 14д',
  level_ma30: 'Средний уровень 30д',
  level_ma60: 'Средний уровень 60д',
  temp_mean: 'Температура сред.',
  temp_ma7: 'Температура 7д',
  temp_ma14: 'Температура 14д',
  precip_mm: 'Осадки, мм',
  precip_sum7: 'Осадки 7д, мм',
  precip_sum14: 'Осадки 14д, мм',
  snow_depth_cm: 'Высота снега, см',
  snow_pct_norm: 'Снег, % от нормы',
  ice_thickness_cm: 'Толщина льда, см',
  ice_flag: 'Ледостав',
  water_level_lag1: 'Уровень вчера',
  water_level_lag7: 'Уровень 7 дн. назад',
  trend_7d: 'Тренд 7д',
  trend_14d: 'Тренд 14д',
};

function translateFeature(name: string): string {
  if (!name) return name;
  if (FEATURE_LABELS_RU[name]) return FEATURE_LABELS_RU[name];
  const low = name.toLowerCase();
  if (FEATURE_LABELS_RU[low]) return FEATURE_LABELS_RU[low];
  return name;
}

export function ExplainPanel({ explain, isMock, shapData }: { explain: any; isMock?: boolean; shapData?: any }) {
  const [tab, setTab] = useState<'bars' | 'table'>('bars');
  if (!explain && !shapData) return null;
  const shapValues: { feature: string; key?: string; value: number; abs?: number }[] = shapData?.values || [];
  const fiEntries = shapValues.length
    ? shapValues.map(v => [translateFeature(v.feature), v.value] as [string, number])
    : (Object.entries((explain?.feature_importance || {}) as Record<string, number>).sort((a: any, b: any) => b[1] - a[1]).slice(0, 5) as [string, number][]).map(([k, v]) => [translateFeature(k), v] as [string, number]);
  const maxFi = Math.max(...fiEntries.map(([, v]) => Math.abs(v)), 1);
  const baseVal = shapData?.base_value;
  const method = shapData?.method;
  return (
    <div className="bg-blue-50/50 border border-blue-100 rounded-xl p-5 space-y-3">
      <div className="flex items-center gap-2 text-blue-800 font-semibold text-sm">
        <Info className="w-4 h-4" />
        Почему такой прогноз {explain?.horizon ? `· горизонт ${explain.horizon} дн.` : ''}
        {isMock && <span className="text-xs bg-amber-100 text-amber-800 px-2 py-0.5 rounded-full">демо</span>}
        {method && <span className="text-[10px] bg-white border border-blue-200 text-blue-700 px-2 py-0.5 rounded-full">{method}</span>}
      </div>
      {explain?.narrative && <p className="text-slate-700 text-sm leading-relaxed">{explain.narrative}</p>}
      {explain?.factors?.length > 0 && (
        <ul className="text-xs text-slate-600 space-y-1">
          {explain.factors.map((f: any, i: number) => (<li key={i}><strong>{f.label}:</strong> {f.value} — {f.note}</li>))}
        </ul>
      )}
      {(fiEntries.length > 0) && (
        <div className="pt-2 border-t border-blue-100">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-xs font-semibold text-blue-900">Вклады признаков</span>
            <div className="ml-auto flex bg-white rounded-full border border-blue-200 p-0.5 text-[11px]">
              <button onClick={() => setTab('bars')} className={`px-2 py-0.5 rounded-full ${tab === 'bars' ? 'bg-blue-600 text-white' : 'text-slate-600'}`}>Бары</button>
              <button onClick={() => setTab('table')} className={`px-2 py-0.5 rounded-full ${tab === 'table' ? 'bg-blue-600 text-white' : 'text-slate-600'}`}>Таблица</button>
            </div>
          </div>
          {baseVal != null && <p className="text-[11px] text-slate-500 mb-2">base_value: {baseVal}</p>}
          {tab === 'bars' ? (
            <div className="space-y-1.5">
              {fiEntries.map(([k, v]) => {
                const isNeg = v < 0;
                return (
                  <div key={k} className="flex items-center gap-2 text-xs">
                    <span className="w-36 truncate text-slate-700" title={k}>{k}</span>
                    <div className="flex-1 h-2 bg-blue-100 rounded-full overflow-hidden flex"><div className={`h-full ${isNeg ? 'bg-amber-500' : 'bg-blue-600'}`} style={{ width: `${Math.round((Math.abs(v) / maxFi) * 100)}%` }} /></div>
                    <span className={`w-16 text-right ${isNeg ? 'text-amber-700' : 'text-slate-600'}`}>{Math.abs(v) > 1 ? v.toFixed(3) : (v * 100).toFixed(1) + '%'}</span>
                  </div>
                );
              })}
            </div>
          ) : (
            <div className="overflow-auto max-h-64 border border-blue-100 rounded-lg bg-white">
              <table className="w-full text-xs">
                <thead className="bg-blue-50 text-blue-900 sticky top-0"><tr><th className="text-left px-2 py-1">Признак</th><th className="text-right px-2 py-1">Вклад</th><th className="text-right px-2 py-1">|Вклад|</th></tr></thead>
                <tbody>{fiEntries.map(([k, v]) => <tr key={k} className="border-t border-slate-100"><td className="px-2 py-1 text-slate-700">{k}</td><td className="px-2 py-1 text-right font-mono">{typeof v === 'number' ? v.toFixed(4) : String(v)}</td><td className="px-2 py-1 text-right text-slate-500">{Math.abs(v as number).toFixed(4)}</td></tr>)}</tbody>
              </table>
            </div>
          )}
          {shapData?.note && <p className="text-[11px] text-slate-500 mt-2">{shapData.note}</p>}
          {shapData?.error && <p className="text-[11px] text-red-600 mt-1">Ошибка SHAP: {shapData.error}</p>}
        </div>
      )}
    </div>
  );
}

export function mapTierToChart(points: ForecastPoint[]) {
  return points.map(d => ({
    date: formatDateRu(d.date, 'dd MMM yyyy'),
    dateIso: d.date,
    median: d.median,
    q10: d.q10 ?? d.median * 0.9,
    q90: d.q90 ?? d.median * 1.1,
    q95: d.q95,
  }));
}

export function HydroChart({
  data,
  warningLevel,
  dangerLevel,
  minLevel,
  height = 400,
  showBand = true,
}: {
  data: any[];
  warningLevel: number;
  dangerLevel: number;
  minLevel: number;
  height?: number;
  showBand?: boolean;
}) {
  if (!data?.length) {
    return <div className="text-slate-500 text-sm p-8">Нет данных прогноза</div>;
  }
  return (
    <ChartBox height={height}>
      <ComposedChart data={data} margin={{ top: 20, right: 30, left: 8, bottom: 8 }}>
        <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
        <XAxis dataKey="date" stroke="#64748b" fontSize={11} tickLine={false} minTickGap={28} />
        <YAxis stroke="#64748b" fontSize={11} tickLine={false} domain={['auto', 'auto']} />
        <RechartsTooltip />
        <Legend />
        <ReferenceLine y={minLevel} stroke="#3b82f6" strokeDasharray="3 3" label="НЯ" />
        <ReferenceLine y={warningLevel} stroke="#f97316" strokeDasharray="3 3" />
        <ReferenceLine y={dangerLevel} stroke="#ef4444" strokeDasharray="3 3" label="ОЯ" />
        {showBand && (
          <>
            <Area type="monotone" dataKey="q90" stroke="none" fill="#fecaca" fillOpacity={0.35} name={QUANTILE_LABELS.q90} />
            <Area type="monotone" dataKey="q10" stroke="none" fill="#dbeafe" fillOpacity={0.35} name={QUANTILE_LABELS.q10} />
          </>
        )}
        <Line type="monotone" dataKey="median" stroke="#2563eb" strokeWidth={2} dot={false} name={QUANTILE_LABELS.median} />
      </ComposedChart>
    </ChartBox>
  );
}

export function MediumForecastView({
  forecast,
  tierPayload,
  warningLevel,
  dangerLevel,
  minLevel,
  isMock,
  loading,
  forecastError,
}: {
  forecast: ForecastPoint[];
  tierPayload: any;
  warningLevel: number;
  dangerLevel: number;
  minLevel: number;
  isMock?: boolean;
  loading?: boolean;
  forecastError?: string;
}) {
  const chartData = mapTierToChart(forecast);
  const trainedHorizons: number[] = tierPayload?.trained_horizons || [];
  const lacksMedium = trainedHorizons.length > 0 && !trainedHorizons.some((h: number) => h >= 14);

  if (loading) {
    return (
      <div className="text-slate-500 p-8 text-center border border-dashed border-slate-200 rounded-xl animate-pulse">
        Загрузка прогноза…
      </div>
    );
  }

  if (forecastError && !forecast.length) {
    return (
      <div className="text-red-800 p-8 text-center border border-red-200 bg-red-50 rounded-xl text-sm">
        {forecastError}
      </div>
    );
  }

  if (!forecast.length) {
    return (
      <div className="text-slate-600 p-8 text-center border border-amber-200 bg-amber-50 rounded-xl text-sm">
        Нет данных прогноза. Обучите модель в разделе «Управление данными» или проверьте, что API запущен.
        {lacksMedium && (
          <p className="mt-2 text-amber-800">Для среднего прогноза нужны горизонты 14–30 дней — выполните обучение заново.</p>
        )}
      </div>
    );
  }

  return (
    <div className="space-y-6">
      {(tierPayload?.forecast_error || tierPayload?.forecast_note) && (
        <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          {tierPayload.forecast_note || tierPayload.forecast_error}
        </p>
      )}
      {trainedHorizons.length > 0 && (
        <p className="text-xs text-slate-500">Обученные горизонты: {trainedHorizons.join(', ')} дн.</p>
      )}
      {lacksMedium && !isMock && (
        <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          В модели нет горизонта 14–30 дней. Средний прогноз может быть неточным — рекомендуется полное обучение.
        </p>
      )}
      {isMock && (
        <p className="text-sm text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
          Демо-прогноз: обучите модель для этой станции в разделе «Данные».
        </p>
      )}
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-white rounded-xl p-4 border border-slate-200">
          <p className="text-xs text-slate-500">Базовая дата</p>
          <p className="text-lg font-bold">{tierPayload?.base_date || '—'}</p>
        </div>
        <div className="bg-white rounded-xl p-4 border border-slate-200">
          <p className="text-xs text-slate-500">Горизонт</p>
          <p className="text-lg font-bold">14–30 дней</p>
        </div>
        <div className="bg-white rounded-xl p-4 border border-slate-200">
          <p className="text-xs text-slate-500">Точек прогноза</p>
          <p className="text-lg font-bold">{forecast.length}</p>
        </div>
      </div>
      <div className="bg-white p-6 rounded-2xl border border-slate-200">
        <h3 className="font-semibold mb-2">Среднесрочный гидрограф (30 дней)</h3>
        <QuantileLegend compact />
        <div className="mt-3">
          <HydroChart
            data={chartData}
            warningLevel={warningLevel}
            dangerLevel={dangerLevel}
            minLevel={minLevel}
            height={380}
          />
        </div>
      </div>
      <div className="bg-white border border-slate-200 rounded-2xl overflow-hidden">
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="px-4 py-3 text-left">Дата</th>
              <th className="px-4 py-3 text-left">Медиана</th>
              <th className="px-4 py-3 text-left" title={QUANTILE_LABELS.q90}>q90 (90%)</th>
              <th className="px-4 py-3 text-left" title={QUANTILE_LABELS.q95}>q95 (95%)</th>
              <th className="px-4 py-3 text-left">Статус</th>
            </tr>
          </thead>
          <tbody>
            {chartData.map((row, i) => (
              <tr key={i} className="border-t border-slate-100 hover:bg-slate-50">
                <td className="px-4 py-2 font-medium">{formatDateRu(row.dateIso)}</td>
                <td className="px-4 py-2">{row.median != null ? `${Math.round(row.median)} см` : '—'}</td>
                <td className="px-4 py-2 text-orange-600">{row.q90 != null ? `${Math.round(row.q90)} см` : '—'}</td>
                <td className="px-4 py-2 text-red-600 font-semibold">{row.q95 != null ? `${Math.round(row.q95)} см` : '—'}</td>
                <td className="px-4 py-2">
                  {row.q95 >= dangerLevel ? (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-red-100 text-red-800">ОЯ</span>
                  ) : row.median >= warningLevel ? (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-orange-100 text-orange-800">НЯ</span>
                  ) : (
                    <span className="text-xs px-2 py-0.5 rounded-full bg-emerald-100 text-emerald-800">Норма</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function buildYearChartRows(yearPayload: any, showOverlays: boolean) {
  const overlays = showOverlays ? (yearPayload.overlays || []) : [];
  const yMax = yearPayload.year_max;
  const yMin = yearPayload.year_min;

  return (yearPayload.series || [])
    .filter((r: any) => r.actual != null || r.median != null || r.hist_mean != null)
    .map((r: any) => {
      const row: any = {
        dateLabel: r.date_label || formatDateRu(r.date),
        dateShort: formatDateRu(r.date, 'dd.MM'),
        dateIso: r.date,
        histMean: r.hist_mean ?? null,
        histQ10: r.hist_q10 ?? null,
        histQ90: r.hist_q90 ?? null,
        actual: r.actual != null ? Number(r.actual) : null,
        forecast: r.median != null ? Number(r.median) : null,
        forecastQ95: r.q95 != null ? Number(r.q95) : null,
      };

      if (showOverlays) {
        overlays.forEach((o: any) => {
          const match = o.series?.find((p: { date: string }) => p.date === r.date);
          row[`overlay_${o.year}`] = match?.level != null ? Number(match.level) : null;
        });
      }

      if (yMax?.date === r.date) row.markMax = Number(yMax.value);
      if (yMin?.date === r.date) row.markMin = Number(yMin.value);
      const peak = yearPayload.peaks?.find((p: any) => p.date === r.date);
      if (peak) row.markPeak = Number(peak.level);

      return row;
    });
}

function IndicatorCard({ label, value, sub }: { label: string; value: string; sub?: string }) {
  const subFmt = sub && sub.includes('-') ? formatDateRu(sub) : sub;
  return (
    <div className="bg-white rounded-xl p-4 border border-slate-200 shadow-sm">
      <p className="text-xs text-slate-500 mb-1">{label}</p>
      <p className="text-xl font-bold text-slate-800">{value}</p>
      {subFmt && subFmt !== '—' && <p className="text-[10px] text-slate-500 mt-1">{subFmt}</p>}
    </div>
  );
}

export type YearChartFocus = 'past' | 'future' | 'mixed' | 'all';

export function YearOverview({
  yearPayload,
  warningLevel,
  dangerLevel,
  minLevel,
  loading,
  chartFocus,
  onChartFocusChange,
}: {
  yearPayload: any;
  warningLevel: number;
  dangerLevel: number;
  minLevel: number;
  loading?: boolean;
  chartFocus?: YearChartFocus;
  onChartFocusChange?: (f: YearChartFocus) => void;
}) {
  const [showOverlays, setShowOverlays] = useState(false);
  const [brushIdx, setBrushIdx] = useState<{ startIndex?: number; endIndex?: number }>({});
  const focus: YearChartFocus = chartFocus || yearPayload?.view_type || 'all';
  const summary = yearPayload?.summary || {};

  const chartData = useMemo(
    () => (yearPayload?.series?.length ? buildYearChartRows(yearPayload, showOverlays) : []),
    [yearPayload, showOverlays]
  );

  const hasActual = chartData.some(r => r.actual != null);

  const brushYearRef = useRef<number | null>(null);
  useEffect(() => {
    if (!chartData.length) return;
    const y = yearPayload?.year;
    if (brushYearRef.current === y) return;
    brushYearRef.current = y ?? null;
    const springStart = chartData.findIndex(r => {
      const m = new Date(`${r.dateIso}T12:00:00`).getMonth();
      return m >= 2 && m <= 3;
    });
    const summerEnd = chartData.findIndex(r => new Date(`${r.dateIso}T12:00:00`).getMonth() >= 7);
    setBrushIdx({
      startIndex: springStart >= 0 ? springStart : 0,
      endIndex: summerEnd >= 0 ? summerEnd : chartData.length - 1,
    });
  }, [yearPayload?.year, chartData.length]);

  if (loading) {
    return <div className="text-slate-500 p-8 animate-pulse">Загрузка годового обзора…</div>;
  }

  if (!yearPayload || !yearPayload.series?.length) {
    return (
      <div className="flex items-center gap-2 text-slate-500 p-8 border border-dashed rounded-xl">
        <AlertCircle className="w-5 h-5" />
        Нет данных для годового обзора. Проверьте API и наличие записей в БД для станции.
      </div>
    );
  }

  const ind = yearPayload.indicators || {};
  const yearMax = yearPayload.year_max || ind.year_max;
  const yearMin = yearPayload.year_min || ind.year_min;
  const overlays = yearPayload.overlays || [];
  const extremeYears = yearPayload.extreme_years || [];
  const peaks = yearPayload.peaks || [];
  const monthlyActual = yearPayload.monthly_actual || [];
  const monthlyRisk = yearPayload.monthly_risk || [];

  const zoomStart = brushIdx.startIndex ?? 0;
  const zoomEnd = brushIdx.endIndex ?? Math.max(0, chartData.length - 1);
  const zoomedData = chartData.slice(zoomStart, zoomEnd + 1);

  const showActual = focus === 'past' || focus === 'mixed' || focus === 'all';
  const showForecast = focus === 'future' || focus === 'mixed' || focus === 'all';

  return (
    <div className="space-y-6">
      {yearPayload.note && (
        <p className="text-sm text-amber-800 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{yearPayload.note}</p>
      )}

      {onChartFocusChange && (
        <div className="flex flex-wrap gap-2">
          {([
            ['past', 'Прошлый год (факт)'],
            ['future', 'Будущий год (прогноз)'],
            ['mixed', 'Факт + прогноз'],
            ['all', 'Всё'],
          ] as const).map(([id, label]) => (
            <button
              key={id}
              type="button"
              onClick={() => onChartFocusChange(id)}
              className={`text-xs px-3 py-1.5 rounded-full border ${
                focus === id
                  ? 'bg-blue-600 text-white border-blue-600'
                  : 'bg-white text-slate-600 border-slate-200 hover:bg-slate-50'
              }`}
            >
              {label}
            </button>
          ))}
        </div>
      )}

      {summary.view_type_ru && (
        <p className="text-sm font-medium text-slate-700">
          Режим: {summary.view_type_ru}
          {yearPayload.data_through && (
            <span className="text-slate-500 font-normal"> · данные в БД до {formatDateRu(yearPayload.data_through)}</span>
          )}
        </p>
      )}

      <div className="grid grid-cols-2 md:grid-cols-4 lg:grid-cols-6 gap-3">
        <IndicatorCard
          label="Макс. факт"
          value={summary.year_max_fact ? `${summary.year_max_fact.value} см` : '—'}
          sub={summary.year_max_fact?.date}
        />
        <IndicatorCard
          label="Мин. факт"
          value={summary.year_min_fact ? `${summary.year_min_fact.value} см` : '—'}
          sub={summary.year_min_fact?.date}
        />
        <IndicatorCard
          label="Макс. прогноз"
          value={summary.forecast_max?.value != null ? `${Math.round(summary.forecast_max.value)} см` : '—'}
          sub={summary.forecast_max?.date}
        />
        <IndicatorCard label="Пиков паводка" value={String(summary.flood_peaks_count ?? peaks.length)} />
        <IndicatorCard label="Дней &gt; НЯ" value={String(summary.days_above_nya ?? ind.days_above_nya ?? 0)} />
        <IndicatorCard label="Дней &gt; ОЯ" value={String(summary.days_above_oya ?? ind.days_above_oya ?? 0)} />
      </div>

      <div className="bg-emerald-50 border border-emerald-200 rounded-xl p-4">
        <h4 className="font-semibold text-emerald-900 mb-2">
          Год: {yearPayload.year}
          {yearPayload.requested_year && yearPayload.requested_year !== yearPayload.year && (
            <span className="text-xs font-normal text-emerald-700 ml-2">(запрошен {yearPayload.requested_year})</span>
          )}
        </h4>
        <div className="grid grid-cols-2 md:grid-cols-4 gap-2 text-sm text-emerald-800">
          {yearMax ? (
            <div><span className="text-emerald-600">MAX уровень воды:</span> <strong>{yearMax.value} см</strong> — {formatDateRu(yearMax.date)}</div>
          ) : (
            <div>MAX: нет данных</div>
          )}
          {yearMin ? (
            <div><span className="text-emerald-600">MIN уровень воды:</span> <strong>{yearMin.value} см</strong> — {formatDateRu(yearMin.date)}</div>
          ) : (
            <div>MIN: нет данных</div>
          )}
          <div>Пиков паводка: {peaks.length}</div>
          <div>Дней &gt; НЯ: {ind.days_above_nya ?? 0}</div>
        </div>
        {!hasActual && (
          <p className="text-xs text-amber-700 mt-2">За этот год нет фактических измерений — на графике только норма и прогноз.</p>
        )}
        {peaks.length > 0 && (
          <div className="mt-3 space-y-2">
            <p className="text-xs text-slate-600">
              Пики паводка за <strong>{yearPayload.year}</strong> г. (факт в БД): локальные максимумы выше НЯ ({warningLevel} см).
            </p>
            <div className="flex flex-wrap gap-2">
              {peaks.map((p: any, i: number) => (
                <span
                  key={i}
                  className={`text-xs px-2 py-1 rounded-full ${p.type === 'oya' ? 'bg-red-200 text-red-900' : 'bg-orange-200 text-orange-900'}`}
                >
                  {formatDateRu(p.date)}: {p.level} см ({p.type === 'oya' ? 'ОЯ' : 'НЯ'})
                </span>
              ))}
            </div>
          </div>
        )}
      </div>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <IndicatorCard label="Макс. года" value={yearMax ? `${yearMax.value} см` : '—'} sub={yearMax?.date} />
        <IndicatorCard label="Мин. года" value={yearMin ? `${yearMin.value} см` : '—'} sub={yearMin?.date} />
        <IndicatorCard label="Ранг среди лет" value={ind.rank_among_years != null ? `#${ind.rank_among_years}` : '—'} />
        <IndicatorCard
          label="Критический год?"
          value={ind.is_critical_year ? 'Да' : 'Нет'}
          sub={ind.is_critical_year ? 'экстремальный сезон' : 'в пределах нормы'}
        />
      </div>

      <div className="bg-slate-900 p-6 rounded-2xl">
        <div className="flex flex-wrap items-center justify-between gap-3 mb-3">
          <h3 className="text-base font-semibold text-slate-100 flex items-center gap-2">
            <ZoomIn className="w-4 h-4" />
            {yearPayload.year} — факт и прогноз (приближение ниже)
          </h3>
          <label className="flex items-center gap-2 text-xs text-slate-300 cursor-pointer">
            <input
              type="checkbox"
              checked={showOverlays}
              onChange={e => setShowOverlays(e.target.checked)}
              className="rounded"
            />
            Дополнительно: другие экстремальные годы
          </label>
        </div>
        <p className="text-xs text-slate-400 mb-3">
          Жирная зелёная — факт {yearPayload.year}. Красная — прогноз (медиана). Синий/розовый коридор — норма q10–q90 (10–90% квантили по многолетним данным).
        </p>
        <ChartBox height={360}>
          <ComposedChart data={zoomedData} margin={{ top: 12, right: 20, left: 4, bottom: 0 }}>
            <CartesianGrid stroke="#334155" strokeDasharray="3 3" />
            <XAxis dataKey="dateShort" stroke="#94a3b8" fontSize={9} minTickGap={12} />
            <YAxis stroke="#94a3b8" fontSize={11} unit=" см" />
            <RechartsTooltip
              contentStyle={{ background: '#0f172a', border: '1px solid #334155' }}
              labelFormatter={(_, payload) => {
                const p = payload?.[0]?.payload;
                return p?.dateIso ? formatDateRu(p.dateIso, 'dd MMMM yyyy') : '';
              }}
              formatter={(value: number, name: string) => [
                value != null && !Number.isNaN(value) ? `${Math.round(value)} см` : '—',
                name,
              ]}
            />
            <Legend />
            <ReferenceLine y={dangerLevel} stroke="#ef4444" strokeDasharray="3 3" label="ОЯ" />
            <ReferenceLine y={minLevel} stroke="#3b82f6" strokeDasharray="3 3" label="НЯ" />
            <Line dataKey="histQ10" stroke="#475569" strokeDasharray="2 2" dot={false} name="Норма q10" strokeWidth={1} connectNulls />
            <Line dataKey="histQ90" stroke="#475569" strokeDasharray="2 2" dot={false} name="Норма q90" strokeWidth={1} connectNulls />
            <Line dataKey="histMean" stroke="#64748b" strokeDasharray="4 4" dot={false} name="Норма ср." strokeWidth={1} connectNulls />
            {showActual && (
              <Line
                type="monotone"
                dataKey="actual"
                stroke="#22c55e"
                strokeWidth={3}
                dot={false}
                name={`Факт ${yearPayload.year}`}
                connectNulls={false}
                isAnimationActive={false}
              />
            )}
            {showForecast && (
              <>
                <Line dataKey="forecast" stroke="#f87171" strokeWidth={2} strokeDasharray="6 3" dot={false} name="Прогноз (медиана)" connectNulls={false} />
                <Line dataKey="forecastQ95" stroke="#fb923c" strokeWidth={1} strokeDasharray="2 2" dot={false} name="Прогноз q95" connectNulls={false} />
              </>
            )}
            {showOverlays && overlays.map((o: any, i: number) => (
              <Line
                key={o.year}
                dataKey={`overlay_${o.year}`}
                stroke={OVERLAY_COLORS[i % OVERLAY_COLORS.length]}
                strokeWidth={1}
                dot={false}
                strokeOpacity={0.55}
                name={`${o.year} (макс ${o.annual_max})`}
                connectNulls
              />
            ))}
            {chartData.filter((r: any) => r.markMax != null).map((r: any) => (
              <ReferenceDot
                key={`max-${r.dateIso}`}
                x={r.dateShort}
                y={r.markMax}
                r={8}
                fill="#ef4444"
                label={{ value: 'MAX', fill: '#fca5a5', fontSize: 10 }}
              />
            ))}
            {chartData.filter((r: any) => r.markMin != null).map((r: any) => (
              <ReferenceDot
                key={`min-${r.dateIso}`}
                x={r.dateShort}
                y={r.markMin}
                r={8}
                fill="#3b82f6"
                label={{ value: 'MIN', fill: '#93c5fd', fontSize: 10 }}
              />
            ))}
          </ComposedChart>
        </ChartBox>
        <p className="text-[10px] text-slate-500 mt-2 mb-1">Перетащите ползунки для приближения периода:</p>
        <ChartBox height={56}>
          <ComposedChart data={chartData} margin={{ top: 4, right: 12, left: 4, bottom: 4 }}>
            <Line dataKey="actual" stroke="#475569" dot={false} strokeWidth={1} isAnimationActive={false} />
            <Brush
              dataKey="dateShort"
              height={40}
              stroke="#64748b"
              fill="#334155"
              travellerWidth={10}
              startIndex={zoomStart}
              endIndex={zoomEnd}
              onChange={(e: { startIndex?: number; endIndex?: number }) => {
                if (e.startIndex != null && e.endIndex != null) {
                  setBrushIdx({ startIndex: e.startIndex, endIndex: e.endIndex });
                }
              }}
            />
          </ComposedChart>
        </ChartBox>
      </div>

      {extremeYears.length > 0 && (
        <details className="bg-white p-4 rounded-2xl border border-slate-200">
          <summary className="font-semibold cursor-pointer text-slate-700">
            Справка: рейтинг всех лет (доп. информация)
          </summary>
          <div className="h-[200px] mt-4">
            <ChartBox height={200}>
              <BarChart
                data={extremeYears.slice(0, 15).map((y: any) => ({
                  year: String(y.year),
                  annual_max: y.annual_max,
                  is_critical: y.is_critical,
                }))}
                layout="vertical"
                margin={{ left: 36 }}
              >
                <XAxis type="number" fontSize={10} />
                <YAxis type="category" dataKey="year" width={40} fontSize={10} />
                <Bar dataKey="annual_max" name="Макс, см" radius={[0, 4, 4, 0]}>
                  {extremeYears.slice(0, 15).map((y: any, i: number) => (
                    <Cell key={i} fill={y.year === yearPayload.year ? '#22c55e' : y.is_critical ? '#ef4444' : '#94a3b8'} />
                  ))}
                </Bar>
                <RechartsTooltip />
              </BarChart>
            </ChartBox>
          </div>
        </details>
      )}

      {monthlyActual.length > 0 && (
        <div className="bg-white p-6 rounded-2xl border border-slate-200">
          <h3 className="font-semibold mb-4">Уровень воды по месяцам (факт, см)</h3>
          <ChartBox height={220}>
            <BarChart data={monthlyActual.filter((m: any) => m.max_level != null)}>
              <CartesianGrid strokeDasharray="3 3" vertical={false} />
              <XAxis dataKey="month_name" fontSize={10} />
              <YAxis fontSize={10} unit=" см" />
              <RechartsTooltip formatter={(v: number) => [`${v} см`, 'Макс.']} />
              <Bar dataKey="max_level" fill="#22c55e" name="Макс. за месяц" radius={[4, 4, 0, 0]} />
            </BarChart>
          </ChartBox>
        </div>
      )}

      <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
        <h3 className="font-semibold px-4 py-3 border-b border-slate-100">Ключевые точки года</h3>
        <table className="w-full text-sm">
          <thead className="bg-slate-50 text-slate-600">
            <tr>
              <th className="px-4 py-2 text-left">Дата</th>
              <th className="px-4 py-2 text-left">Тип</th>
              <th className="px-4 py-2 text-left">Уровень воды</th>
            </tr>
          </thead>
          <tbody>
            {yearMax && (
              <tr className="border-t border-slate-100">
                <td className="px-4 py-2">{formatDateRu(yearMax.date)}</td>
                <td className="px-4 py-2 text-red-700 font-medium">Максимум года</td>
                <td className="px-4 py-2 font-bold">{yearMax.value} см</td>
              </tr>
            )}
            {yearMin && (
              <tr className="border-t border-slate-100">
                <td className="px-4 py-2">{formatDateRu(yearMin.date)}</td>
                <td className="px-4 py-2 text-blue-700 font-medium">Минимум года</td>
                <td className="px-4 py-2 font-bold">{yearMin.value} см</td>
              </tr>
            )}
            {peaks.map((p: any, i: number) => (
              <tr key={i} className="border-t border-slate-100">
                <td className="px-4 py-2">{formatDateRu(p.date)}</td>
                <td className="px-4 py-2">Пик паводка ({p.type === 'oya' ? 'ОЯ' : 'НЯ'})</td>
                <td className="px-4 py-2 font-semibold">{p.level} см</td>
              </tr>
            ))}
            {!yearMax && !yearMin && peaks.length === 0 && (
              <tr><td colSpan={3} className="px-4 py-6 text-slate-500 text-center">Нет экстремумов за выбранный год</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {monthlyRisk.some((m: any) => m.max_q95 > 0) && (
        <div className="bg-white rounded-2xl border border-slate-200 overflow-hidden">
          <h3 className="font-semibold px-4 py-3 border-b border-slate-100">Прогноз риска по месяцам (q95)</h3>
          <table className="w-full text-sm">
            <thead className="bg-slate-50 text-slate-600">
              <tr>
                <th className="px-4 py-2 text-left">Месяц</th>
                <th className="px-4 py-2 text-left">Макс. q95, см</th>
                <th className="px-4 py-2 text-left">Риск</th>
              </tr>
            </thead>
            <tbody>
              {monthlyRisk.map((m: any) => (
                <tr key={m.month} className="border-t border-slate-100">
                  <td className="px-4 py-2">{m.month_name}</td>
                  <td className="px-4 py-2">{m.max_q95 > 0 ? `${m.max_q95} см` : '—'}</td>
                  <td className="px-4 py-2">{m.risk || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

export function ClimatologyChart({
  points,
  excludeYear,
  onExcludeYearChange,
}: {
  points: any[];
  excludeYear?: number | null;
  onExcludeYearChange?: (year: number | null) => void;
}) {
  const data = (points || []).map((p: any) => {
    const dm = p.date_label || '01.01';
    const [dd, mm] = dm.split('.');
    const dateFull = dd && mm ? `${dd.padStart(2, '0')}.${mm.padStart(2, '0')}.2000` : dm;
    return {
      date: dateFull,
      dateShort: dm,
      dateLong: p.date_label_long || dm,
      doy: p.day_of_year,
      histMean: p.hist_mean,
      histMin: p.hist_min,
      histMax: p.hist_max,
    };
  });
  const excludeYearLabel = excludeYear ? ` (${excludeYear})` : ` (${new Date().getFullYear()})`;

  return (
    <div className="bg-white p-6 rounded-2xl border border-slate-200">
      <h3 className="font-semibold mb-2">Многолетняя климатическая норма</h3>
      <p className="text-xs text-slate-500 mb-3">
        Средний, минимальный и максимальный уровень по всем годам наблюдений в БД для каждого календарного дня (дд.мм).
        Данные уже отсортированы по дню года.
      </p>
      {onExcludeYearChange && (
        <label className="flex items-center gap-2 text-xs text-slate-600 mb-4 cursor-pointer">
          <input
            type="checkbox"
            checked={excludeYear != null && excludeYear > 0}
            onChange={e => onExcludeYearChange(e.target.checked ? (excludeYear && excludeYear > 0 ? excludeYear : new Date().getFullYear()) : null)}
          />
          Исключить календарный год{excludeYearLabel} из расчёта нормы
        </label>
      )}
      <ChartBox height={340}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis
            dataKey="dateShort"
            fontSize={8}
            minTickGap={18}
            angle={-45}
            textAnchor="end"
            height={50}
          />
          <YAxis fontSize={11} unit=" см" />
          <RechartsTooltip
            labelFormatter={(_, payload) => {
              const p = payload?.[0]?.payload;
              return p ? `${p.dateLong || p.dateShort}${p.doy ? ` — день ${p.doy}` : ''}` : '';
            }}
            formatter={(value: number, name: string) => [
              value != null ? `${Math.round(value)} см` : '—',
              name,
            ]}
          />
          <Legend />
          <Line dataKey="histMean" stroke="#2563eb" dot={false} name="Средний уровень" strokeWidth={2} />
          <Line dataKey="histMin" stroke="#94a3b8" strokeDasharray="2 2" dot={false} name="Минимум" />
          <Line dataKey="histMax" stroke="#f97316" strokeDasharray="2 2" dot={false} name="Максимум" />
        </ComposedChart>
      </ChartBox>
    </div>
  );
}

// ---- Аудит-виджеты портала (coverage, precip×level, ice timeline, duration) ----

export function CoverageBar({ coverage }: { coverage: any }) {
  if (!coverage || !coverage.fields) return null;
  return (
    <div className="bg-white border border-slate-200 rounded-xl p-4 space-y-3">
      <div className="flex items-center justify-between">
        <h4 className="text-sm font-semibold text-slate-800">Покрытие данных</h4>
        <span className="text-xs text-slate-500">{coverage.total_days} дней в БД</span>
      </div>
      <div className="space-y-2">
        {coverage.fields.map((f: any) => (
          <div key={f.field} className="flex items-center gap-2 text-xs">
            <span className="w-40 truncate text-slate-600" title={f.field}>{f.field}</span>
            <div className="flex-1 h-2 bg-slate-100 rounded-full overflow-hidden">
              <div className={`h-full ${f.pct < 20 ? 'bg-red-500' : f.pct < 60 ? 'bg-amber-500' : 'bg-emerald-500'}`} style={{ width: `${f.pct}%` }} />
            </div>
            <span className="w-12 text-right text-slate-600">{f.pct}%</span>
            <span className="w-20 text-right text-slate-400">{f.filled}/{f.total}</span>
          </div>
        ))}
      </div>
      {coverage.hint && (
        <p className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">{coverage.hint}</p>
      )}
    </div>
  );
}

export function DualAxisPrecipLevelChart({ history }: { history: any[] }) {
  const data = (history || []).slice(-90).map((r: any) => ({
    date: formatDateRu(r.date, 'dd.MM'),
    level: r.water_level_cm,
    precip: r.precip_mm,
  }));
  if (!data.length) return <div className="text-sm text-slate-500 p-6 border border-dashed rounded-xl">Нет истории для графика «осадки × уровень»</div>;
  return (
    <div className="bg-white p-4 rounded-2xl border border-slate-200">
      <h4 className="font-semibold text-sm mb-2">Осадки × уровень (последние 90 дней)</h4>
      <ChartBox height={300}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="date" fontSize={10} minTickGap={16} />
          <YAxis yAxisId="left" fontSize={11} unit=" см" />
          <YAxis yAxisId="right" orientation="right" fontSize={11} unit=" мм" />
          <RechartsTooltip />
          <Legend />
          <Bar yAxisId="right" dataKey="precip" name="Осадки, мм" fill="#60a5fa" opacity={0.7} barSize={6} />
          <Line yAxisId="left" type="monotone" dataKey="level" name="Уровень, см" stroke="#0f172a" dot={false} strokeWidth={1.8} />
        </ComposedChart>
      </ChartBox>
    </div>
  );
}

export function IceTimeline({ history }: { history: any[] }) {
  const rows = (history || []).filter((r: any) => r.ice_event || r.ice_thickness_cm != null).slice(-120);
  if (!rows.length) return <div className="text-sm text-slate-500 p-6 border border-dashed rounded-xl">Нет данных о ледовом режиме</div>;
  return (
    <div className="bg-white p-4 rounded-2xl border border-slate-200">
      <h4 className="font-semibold text-sm mb-2">Ледовый режим (таймлайн)</h4>
      <div className="space-y-1 max-h-64 overflow-auto text-xs">
        {rows.map((r: any, i: number) => (
          <div key={i} className="flex items-center gap-2 border-b border-slate-50 py-1">
            <span className="w-20 text-slate-500">{formatDateRu(r.date, 'dd.MM.yyyy')}</span>
            <span className="flex-1 text-slate-700 truncate">{r.ice_event || '—'}</span>
            <span className="w-20 text-right text-slate-600">{r.ice_thickness_cm != null ? `${r.ice_thickness_cm} см` : '—'}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

export function DurationCurve({ history }: { history: any[] }) {
  const vals = (history || []).map((r: any) => r.water_level_cm).filter((v: any) => v != null).map(Number).sort((a: number, b: number) => b - a);
  if (!vals.length) return <div className="text-sm text-slate-500 p-6 border border-dashed rounded-xl">Нет данных для кривой продолжительности</div>;
  const n = vals.length;
  const data = vals.map((v: number, i: number) => ({ p: Math.round((i / Math.max(1, n - 1)) * 100), level: v }));
  return (
    <div className="bg-white p-4 rounded-2xl border border-slate-200">
      <h4 className="font-semibold text-sm mb-2">Кривая продолжительности уровня</h4>
      <p className="text-xs text-slate-500 mb-2">По оси X — обеспеченность (% времени, когда уровень был не ниже указанного).</p>
      <ChartBox height={280}>
        <ComposedChart data={data}>
          <CartesianGrid strokeDasharray="3 3" vertical={false} />
          <XAxis dataKey="p" fontSize={11} unit="%" />
          <YAxis fontSize={11} unit=" см" />
          <RechartsTooltip formatter={(v: any) => [`${v} см`, 'Уровень']} labelFormatter={(l: any) => `${l}% обеспеченности`} />
          <Line type="monotone" dataKey="level" name="Уровень" stroke="#2563eb" dot={false} strokeWidth={2} />
        </ComposedChart>
      </ChartBox>
    </div>
  );
}

