/**
 * Панель ИИ-агента прогноза паводка.
 *
 * Тянет `/api/agent/flood-risk/{river}/{post}` и рисует:
 *  - скор риска и класс (low/moderate/high/critical);
 *  - прогнозный пик и посуточный график ожидаемого уровня;
 *  - список критических дней (превышение НЯ/ОЯ);
 *  - драйверы (сработавшие правила) с текущими весами;
 *  - narrative-объяснение;
 *  - кнопки «Верно / Ложная тревога» → POST `/api/agent/feedback`
 *    и «Ознакомился» → POST `/api/agent/alerts/{id}/acknowledge`.
 */
import React, { useEffect, useState } from 'react';
import { API_BASE } from '../config';
import { AlertOctagon, AlertTriangle, TrendingUp, Loader2 } from './icons';

interface Props {
  river: string;
  post: string;
  horizon?: number;
}

interface CritDay { date: string; level: 'warning' | 'critical'; median: number; q95?: number }
interface Driver { id: string; label: string; feature: string; value: number; threshold: number; weight: number; score: number; note?: string }
interface Verdict {
  will_flood: boolean;
  level: 'green' | 'yellow' | 'red';
  level_ru: string;
  confidence: number;
  p_exceed_low: number;
  p_exceed_crit: number;
  reason: string;
}
interface Scenario { peak_cm: number | null; date: string | null; quantile: string }
interface AgentResult {
  risk_score: number;
  risk_class: 'low' | 'moderate' | 'high' | 'critical';
  risk_class_ru: string;
  has_model: boolean;
  observation_date: string;
  horizon_days: number;
  thresholds: { low_oya: number; critical_oya: number };
  forecast_peak: { level_cm: number | null; date: string | null; prob_warning: number | null; prob_danger: number | null };
  forecast_daily: Array<{ date: string; median: number; q10?: number; q90?: number; q95?: number }>;
  critical_days: CritDay[];
  drivers: Driver[];
  verdict?: Verdict;
  scenarios?: { optimistic: Scenario; median: Scenario; pessimistic: Scenario };
  data_gaps?: Array<{ feature: string; label: string; hint: string; severity: string }>;
  narrative: string;
  requires_ack: boolean;
  alert_id: number | null;
}

interface AnalyticsPeak { year: number; peak_cm: number; peak_date: string; exceeded_low: boolean; exceeded_crit: boolean; days_over_low: number; days_over_crit: number }
interface AnalyticsResult {
  river: string; post: string;
  low_oya: number | null; critical_oya: number | null;
  peaks_by_year: AnalyticsPeak[];
  climatology_level: Array<{ doy: number; p10: number; p50: number; p90: number; n: number }>;
  ice_regime: { avg_freeze_up_doy: number | null; avg_break_up_doy: number | null; n_freeze_up_years: number; n_break_up_years: number };
  exceedance_days_per_year: Array<{ year: number; days_over_low: number; days_over_crit: number }>;
  trend_peak_cm_per_year: number | null;
  n_years_total: number;
}

const CLASS_COLORS: Record<string, { bg: string; text: string; ring: string }> = {
  low:      { bg: 'bg-emerald-50', text: 'text-emerald-700', ring: 'ring-emerald-200' },
  moderate: { bg: 'bg-amber-50',   text: 'text-amber-700',   ring: 'ring-amber-200' },
  high:     { bg: 'bg-orange-50',  text: 'text-orange-700',  ring: 'ring-orange-200' },
  critical: { bg: 'bg-red-50',     text: 'text-red-700',     ring: 'ring-red-300' },
};

export function FloodAgentPanel({ river, post, horizon = 14 }: Props) {
  const [data, setData] = useState<AgentResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [feedbackSent, setFeedbackSent] = useState<'reward' | 'penalty' | null>(null);
  const [acked, setAcked] = useState(false);
  const [tab, setTab] = useState<'forecast' | 'analytics'>('forecast');
  const [analytics, setAnalytics] = useState<AnalyticsResult | null>(null);
  const [analyticsLoading, setAnalyticsLoading] = useState(false);
  const [analyticsError, setAnalyticsError] = useState<string | null>(null);

  const load = React.useCallback(() => {
    if (!river || !post) return;
    setLoading(true);
    setError(null);
    setFeedbackSent(null);
    setAcked(false);
    const url = `${API_BASE}/agent/flood-risk/${encodeURIComponent(river)}/${encodeURIComponent(post)}?horizon=${horizon}`;
    fetch(url)
      .then(async r => {
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || r.statusText);
        return j as AgentResult;
      })
      .then(j => setData(j))
      .catch(e => setError(e.message || 'Ошибка агента'))
      .finally(() => setLoading(false));
  }, [river, post, horizon]);

  useEffect(() => { load(); }, [load]);

  const loadAnalytics = React.useCallback(() => {
    if (!river || !post) return;
    setAnalyticsLoading(true);
    setAnalyticsError(null);
    const url = `${API_BASE}/agent/analytics/${encodeURIComponent(river)}/${encodeURIComponent(post)}?years=15`;
    fetch(url)
      .then(async r => {
        const j = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(j.detail || r.statusText);
        return j as AnalyticsResult;
      })
      .then(j => setAnalytics(j))
      .catch(e => setAnalyticsError(e.message || 'Ошибка аналитики'))
      .finally(() => setAnalyticsLoading(false));
  }, [river, post]);

  useEffect(() => {
    if (tab === 'analytics' && !analytics && !analyticsLoading) loadAnalytics();
  }, [tab, analytics, analyticsLoading, loadAnalytics]);

  // Сбрасываем аналитику при смене поста, чтобы догрузилась заново
  useEffect(() => { setAnalytics(null); }, [river, post]);

  const sendFeedback = (verdict: 'reward' | 'penalty') => {
    if (!data) return;
    fetch(`${API_BASE}/agent/feedback`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ river, post, verdict, alert_id: data.alert_id }),
    })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(r.statusText)))
      .then(() => setFeedbackSent(verdict))
      .catch(e => setError('Feedback: ' + e.message));
  };

  const ack = () => {
    if (!data?.alert_id) return;
    fetch(`${API_BASE}/agent/alerts/${data.alert_id}/acknowledge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: 'operator' }),
    })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(r.statusText)))
      .then(() => setAcked(true))
      .catch(e => setError('Ack: ' + e.message));
  };

  if (loading) {
    return (
      <div className="bg-white rounded-2xl p-6 border border-slate-200 shadow-sm flex items-center gap-3 text-slate-500">
        <Loader2 className="w-5 h-5 animate-spin" /> Агент считает риск паводка…
      </div>
    );
  }
  if (error || !data) {
    return (
      <div className="bg-white rounded-2xl p-6 border border-red-200 shadow-sm text-red-700 text-sm">
        Не удалось получить оценку агента: {error || 'нет данных'}
      </div>
    );
  }

  const cls = CLASS_COLORS[data.risk_class] || CLASS_COLORS.low;
  const peak = data.forecast_peak;
  const dailyMax = data.forecast_daily.length
    ? Math.max(...data.forecast_daily.map(d => d.q95 ?? d.median ?? 0), data.thresholds.critical_oya)
    : data.thresholds.critical_oya;

  return (
    <div className={`bg-white rounded-2xl p-6 border shadow-sm space-y-5 ring-1 ${cls.ring}`}>
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div className="flex items-center gap-3">
          <div className={`p-3 rounded-xl ${cls.bg}`}>
            {data.risk_class === 'critical' || data.risk_class === 'high'
              ? <AlertOctagon className={`w-6 h-6 ${cls.text}`} />
              : <TrendingUp className={`w-6 h-6 ${cls.text}`} />}
          </div>
          <div>
            <div className="text-sm text-slate-500">ИИ-агент HydroPredict</div>
            <div className="text-lg font-semibold text-slate-800">
              Риск паводка: <span className={cls.text}>{data.risk_class_ru}</span>
              <span className="ml-2 text-slate-400 text-sm font-normal">
                {Math.round(data.risk_score)}/100 · {horizon} дн.
              </span>
            </div>
          </div>
        </div>
        <div className="text-xs text-slate-400 text-right">
          Оценка от {data.observation_date}
          <div>{data.has_model ? 'ML+правила' : 'только правила'}</div>
        </div>
      </div>
      {data.requires_ack && !acked && (
        <div className="rounded-xl border border-red-300 bg-red-50 p-4 flex items-start gap-3">
          <AlertTriangle className="w-5 h-5 text-red-600 mt-0.5" />
          <div className="flex-1 text-sm text-red-800">
            <div className="font-semibold">Требуется подтверждение — критический риск.</div>
            <div className="text-red-700/80 mt-0.5">Алерт №{data.alert_id}. Проверьте прогноз и подтвердите ознакомление.</div>
          </div>
          <button onClick={ack} className="px-3 py-1.5 rounded-lg bg-red-600 text-white text-sm font-medium hover:bg-red-700">
            Ознакомился
          </button>
        </div>
      )}
      {acked && (
        <div className="text-xs text-emerald-700 bg-emerald-50 px-3 py-2 rounded-lg">Алерт подтверждён.</div>
      )}

      {data.verdict && (
        <div className={`rounded-xl p-4 border-2 ${
          data.verdict.level === 'red'    ? 'bg-red-50 border-red-300' :
          data.verdict.level === 'yellow' ? 'bg-amber-50 border-amber-300' :
                                            'bg-emerald-50 border-emerald-300'
        }`}>
          <div className="flex items-center justify-between gap-4 flex-wrap">
            <div>
              <div className="text-xs uppercase tracking-wide text-slate-500">Вердикт агента</div>
              <div className={`text-xl font-bold ${
                data.verdict.level === 'red'    ? 'text-red-700' :
                data.verdict.level === 'yellow' ? 'text-amber-700' :
                                                  'text-emerald-700'
              }`}>
                {data.verdict.level === 'red'    && '⚠️ Паводок ожидается'}
                {data.verdict.level === 'yellow' && '⚡ Паводок возможен'}
                {data.verdict.level === 'green'  && '✅ Паводок не ожидается'}
              </div>
              <div className="text-xs text-slate-600 mt-1">{data.verdict.reason}</div>
            </div>
            <div className="text-right">
              <div className="text-xs text-slate-500">Уверенность</div>
              <div className="text-2xl font-bold text-slate-800">{Math.round(data.verdict.confidence * 100)}%</div>
              <div className="text-[11px] text-slate-500">
                P(≥НЯ)={Math.round(data.verdict.p_exceed_low*100)}% · P(≥ОЯ)={Math.round(data.verdict.p_exceed_crit*100)}%
              </div>
            </div>
          </div>
          {data.scenarios && (
            <div className="grid grid-cols-3 gap-2 mt-3">
              {(['optimistic','median','pessimistic'] as const).map(k => {
                const s = data.scenarios![k];
                const label = k === 'optimistic' ? 'Оптим. (q10)' : k === 'median' ? 'Медиан. (q50)' : 'Пессим. (q90)';
                return (
                  <div key={k} className="bg-white/60 rounded-lg p-2 text-center">
                    <div className="text-[10px] uppercase text-slate-500">{label}</div>
                    <div className="text-lg font-semibold text-slate-800">
                      {s.peak_cm != null ? `${Math.round(s.peak_cm)} см` : '—'}
                    </div>
                    <div className="text-[10px] text-slate-500">{s.date || '—'}</div>
                  </div>
                );
              })}
            </div>
          )}
        </div>
      )}

      {data.data_gaps && data.data_gaps.length > 0 && (
        <div className="rounded-xl border border-sky-200 bg-sky-50 p-4">
          <div className="text-sm font-semibold text-sky-800 mb-2">Как улучшить прогноз</div>
          <ul className="space-y-1 text-xs text-slate-700">
            {data.data_gaps.map(g => (
              <li key={g.feature} className="flex items-start gap-2">
                <span className="text-sky-500 mt-0.5">•</span>
                <span><b>{g.label}:</b> {g.hint}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Вкладки */}
      <div className="flex gap-1 border-b border-slate-200">
        <button
          onClick={() => setTab('forecast')}
          className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
            tab === 'forecast'
              ? 'border-blue-600 text-blue-700'
              : 'border-transparent text-slate-500 hover:text-slate-700'
          }`}
        >
          Прогноз
        </button>
        <button
          onClick={() => setTab('analytics')}
          className={`px-3 py-2 text-sm font-medium border-b-2 transition-colors ${
            tab === 'analytics'
              ? 'border-blue-600 text-blue-700'
              : 'border-transparent text-slate-500 hover:text-slate-700'
          }`}
        >
          Аналитика поста
        </button>
      </div>

      {tab === 'forecast' && (
      <>
      <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <div className="bg-slate-50 rounded-xl p-4">
          <div className="text-xs text-slate-500 mb-1">Прогнозный пик</div>
          <div className="text-2xl font-bold text-slate-800">
            {peak.level_cm != null ? `${Math.round(peak.level_cm)} см` : '—'}
          </div>
          <div className="text-xs text-slate-500">{peak.date || '—'}</div>
        </div>
        <div className="bg-slate-50 rounded-xl p-4">
          <div className="text-xs text-slate-500 mb-1">ОЯ (низкий / критический)</div>
          <div className="text-lg font-semibold text-slate-700">
            {Math.round(data.thresholds.low_oya)} / {Math.round(data.thresholds.critical_oya)} см
          </div>
        </div>
        <div className="bg-slate-50 rounded-xl p-4">
          <div className="text-xs text-slate-500 mb-1">Вероятность превышения</div>
          <div className="text-lg font-semibold text-slate-700">
            НЯ: {peak.prob_warning != null ? `${Math.round(peak.prob_warning * 100)}%` : '—'}
            {' · '}
            Крит: {peak.prob_danger != null ? `${Math.round(peak.prob_danger * 100)}%` : '—'}
          </div>
        </div>
      </div>

      {data.forecast_daily.length > 0 && (
        <div>
          <div className="text-sm font-semibold text-slate-700 mb-2">Посуточный прогноз (медиана + полоса q10/q90)</div>
          <ForecastSvg
            daily={data.forecast_daily}
            low={data.thresholds.low_oya}
            crit={data.thresholds.critical_oya}
          />
        </div>
      )}

      {data.critical_days.length > 0 && (
        <div>
          <div className="text-sm font-semibold text-slate-700 mb-2">
            Критические дни ({data.critical_days.length})
          </div>
          <div className="flex flex-wrap gap-2">
            {data.critical_days.slice(0, 12).map(c => (
              <span key={c.date}
                className={`text-xs px-2 py-1 rounded-lg ${c.level === 'critical' ? 'bg-red-100 text-red-700' : 'bg-amber-100 text-amber-700'}`}>
                {c.date} · {Math.round(c.median)} см
              </span>
            ))}
          </div>
        </div>
      )}

      {data.drivers.length > 0 && (
        <div>
          <div className="text-sm font-semibold text-slate-700 mb-2">Драйверы риска</div>
          <div className="space-y-1.5">
            {data.drivers.map(d => (
              <div key={d.id} className="flex items-center justify-between text-sm bg-slate-50 rounded-lg px-3 py-2">
                <div>
                  <div className="text-slate-800 font-medium">{d.label}</div>
                  <div className="text-xs text-slate-500">{d.feature} = {d.value} (порог {d.threshold}) · вес ×{d.weight}</div>
                </div>
                <div className="text-slate-700 font-semibold">+{Math.round(d.score)}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      <div className="text-sm text-slate-600 leading-relaxed bg-slate-50 rounded-lg p-3">
        {data.narrative}
      </div>
      </>
      )}

      {tab === 'analytics' && (
        <AnalyticsView
          state={analytics}
          loading={analyticsLoading}
          error={analyticsError}
          onRetry={loadAnalytics}
        />
      )}

      <div className="flex items-center justify-between gap-3 flex-wrap pt-2 border-t border-slate-100">
        <div className="text-xs text-slate-500">
          Обратная связь помогает агенту: веса сработавших правил будут скорректированы для этой станции.
        </div>
        <div className="flex gap-2">
          <button disabled={!!feedbackSent}
            onClick={() => sendFeedback('reward')}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-emerald-600 text-white hover:bg-emerald-700 disabled:opacity-40">
            {feedbackSent === 'reward' ? '✓ Учтено' : 'Прогноз верный'}
          </button>
          <button disabled={!!feedbackSent}
            onClick={() => sendFeedback('penalty')}
            className="px-3 py-1.5 rounded-lg text-sm font-medium bg-slate-100 text-slate-700 hover:bg-slate-200 disabled:opacity-40">
            {feedbackSent === 'penalty' ? '✓ Учтено' : 'Ложная тревога'}
          </button>
          <button onClick={load}
            className="px-3 py-1.5 rounded-lg text-sm font-medium text-slate-600 hover:bg-slate-100">
            Пересчитать
          </button>
        </div>
      </div>
    </div>
  );
}

export default FloodAgentPanel;


// ---------------------------------------------------------------------------
// SVG-график посуточного прогноза: полоса q10..q90, линия медианы, пороги.
// ---------------------------------------------------------------------------

function ForecastSvg({
  daily, low, crit,
}: {
  daily: Array<{ date: string; median: number; q10?: number; q90?: number }>;
  low: number; crit: number;
}) {
  const W = 640, H = 180, PAD_L = 42, PAD_R = 8, PAD_T = 10, PAD_B = 26;
  const n = daily.length;
  if (n === 0) return null;

  const values: number[] = [];
  daily.forEach(d => {
    if (d.median != null) values.push(d.median);
    if (d.q10 != null) values.push(d.q10);
    if (d.q90 != null) values.push(d.q90);
  });
  values.push(low, crit);
  const vmin = Math.min(...values);
  const vmax = Math.max(...values);
  const span = Math.max(1, vmax - vmin);
  const yMin = vmin - span * 0.05;
  const yMax = vmax + span * 0.05;
  const ySpan = yMax - yMin;

  const x = (i: number) => PAD_L + ((W - PAD_L - PAD_R) * (n === 1 ? 0.5 : i / (n - 1)));
  const y = (v: number) => PAD_T + (H - PAD_T - PAD_B) * (1 - (v - yMin) / ySpan);

  const bandTop = daily.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(d.q90 ?? d.median)}`).join(' ');
  const bandBot = daily.slice().reverse().map((d, k) => {
    const i = n - 1 - k;
    return `L${x(i)},${y(d.q10 ?? d.median)}`;
  }).join(' ');
  const bandPath = `${bandTop} ${bandBot} Z`;

  const medianPath = daily.map((d, i) => `${i === 0 ? 'M' : 'L'}${x(i)},${y(d.median)}`).join(' ');
  const yTicks = [yMin, (yMin + yMax) / 2, yMax];

  return (
    <div className="overflow-x-auto">
      <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[560px] h-48">
        {yTicks.map((v, i) => (
          <g key={i}>
            <line x1={PAD_L} x2={W - PAD_R} y1={y(v)} y2={y(v)} stroke="#e2e8f0" strokeWidth={1} />
            <text x={4} y={y(v) + 4} fontSize={10} fill="#64748b">{Math.round(v)}</text>
          </g>
        ))}
        <line x1={PAD_L} x2={W - PAD_R} y1={y(low)} y2={y(low)} stroke="#f59e0b" strokeDasharray="4 3" strokeWidth={1.5} />
        <text x={W - PAD_R} y={y(low) - 3} fontSize={10} fill="#b45309" textAnchor="end">НЯ {Math.round(low)}</text>
        <line x1={PAD_L} x2={W - PAD_R} y1={y(crit)} y2={y(crit)} stroke="#dc2626" strokeDasharray="4 3" strokeWidth={1.5} />
        <text x={W - PAD_R} y={y(crit) - 3} fontSize={10} fill="#991b1b" textAnchor="end">ОЯ {Math.round(crit)}</text>
        <path d={bandPath} fill="#3b82f6" fillOpacity={0.15} stroke="none" />
        <path d={medianPath} fill="none" stroke="#2563eb" strokeWidth={2} />
        {daily.map((d, i) => (
          <g key={d.date}>
            <circle cx={x(i)} cy={y(d.median)} r={2.5}
                    fill={d.median >= crit ? '#dc2626' : d.median >= low ? '#f59e0b' : '#2563eb'} />
            {(i === 0 || i === n - 1 || i % Math.max(1, Math.ceil(n / 8)) === 0) && (
              <text x={x(i)} y={H - 8} fontSize={10} fill="#64748b" textAnchor="middle">{d.date.slice(5)}</text>
            )}
            <title>{`${d.date}: ${Math.round(d.median)} см${d.q10 != null && d.q90 != null ? ` (q10..q90: ${Math.round(d.q10)}..${Math.round(d.q90)})` : ''}`}</title>
          </g>
        ))}
      </svg>
    </div>
  );
}


// ---------------------------------------------------------------------------
// Вкладка «Аналитика поста»: peaks_by_year (SVG), тренд, лёд, exceedance.
// ---------------------------------------------------------------------------

function AnalyticsView({
  state, loading, error, onRetry,
}: {
  state: AnalyticsResult | null;
  loading: boolean;
  error: string | null;
  onRetry: () => void;
}) {
  if (loading) {
    return (
      <div className="flex items-center gap-2 text-slate-500 py-6">
        <Loader2 className="w-4 h-4 animate-spin" /> Загрузка аналитики…
      </div>
    );
  }
  if (error) {
    return (
      <div className="rounded-lg border border-red-200 bg-red-50 p-3 text-sm text-red-700">
        Не удалось загрузить аналитику: {error}
        <button onClick={onRetry} className="ml-3 underline text-red-800">Повторить</button>
      </div>
    );
  }
  if (!state) return null;

  const peaks = state.peaks_by_year;
  const low = state.low_oya;
  const crit = state.critical_oya;
  const trendSign = state.trend_peak_cm_per_year;

  const W = 640, H = 200, PAD_L = 42, PAD_R = 8, PAD_T = 10, PAD_B = 32;
  const values = peaks.map(p => p.peak_cm);
  const vmax = Math.max(...values, crit ?? 0, low ?? 0, 1) * 1.05;
  const bw = peaks.length > 0 ? Math.max(6, Math.floor((W - PAD_L - PAD_R) / peaks.length) - 4) : 0;
  const yScale = (v: number) => PAD_T + (H - PAD_T - PAD_B) * (1 - v / vmax);

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 md:grid-cols-4 gap-3">
        <div className="bg-slate-50 rounded-lg p-3">
          <div className="text-[11px] text-slate-500 uppercase">Лет наблюдений</div>
          <div className="text-lg font-semibold text-slate-800">{state.n_years_total}</div>
        </div>
        <div className="bg-slate-50 rounded-lg p-3">
          <div className="text-[11px] text-slate-500 uppercase">Тренд пика</div>
          <div className={`text-lg font-semibold ${trendSign == null ? 'text-slate-500' : trendSign > 0 ? 'text-red-700' : 'text-emerald-700'}`}>
            {trendSign == null ? '—' : `${trendSign > 0 ? '+' : ''}${trendSign.toFixed(2)} см/год`}
          </div>
        </div>
        <div className="bg-slate-50 rounded-lg p-3">
          <div className="text-[11px] text-slate-500 uppercase">Ср. начало ледостава (doy)</div>
          <div className="text-lg font-semibold text-slate-800">
            {state.ice_regime.avg_freeze_up_doy ?? '—'}
            <span className="text-xs text-slate-500 ml-1">n={state.ice_regime.n_freeze_up_years}</span>
          </div>
        </div>
        <div className="bg-slate-50 rounded-lg p-3">
          <div className="text-[11px] text-slate-500 uppercase">Ср. ледоход (doy)</div>
          <div className="text-lg font-semibold text-slate-800">
            {state.ice_regime.avg_break_up_doy ?? '—'}
            <span className="text-xs text-slate-500 ml-1">n={state.ice_regime.n_break_up_years}</span>
          </div>
        </div>
      </div>

      {peaks.length > 0 && (
        <div>
          <div className="text-sm font-semibold text-slate-700 mb-2">Пики уровня по годам</div>
          <div className="overflow-x-auto">
            <svg viewBox={`0 0 ${W} ${H}`} className="w-full min-w-[560px] h-52">
              {low != null && (
                <>
                  <line x1={PAD_L} x2={W - PAD_R} y1={yScale(low)} y2={yScale(low)} stroke="#f59e0b" strokeDasharray="4 3" />
                  <text x={W - PAD_R} y={yScale(low) - 3} fontSize={10} fill="#b45309" textAnchor="end">НЯ {Math.round(low)}</text>
                </>
              )}
              {crit != null && (
                <>
                  <line x1={PAD_L} x2={W - PAD_R} y1={yScale(crit)} y2={yScale(crit)} stroke="#dc2626" strokeDasharray="4 3" />
                  <text x={W - PAD_R} y={yScale(crit) - 3} fontSize={10} fill="#991b1b" textAnchor="end">ОЯ {Math.round(crit)}</text>
                </>
              )}
              {[0, vmax / 2, vmax].map((v, i) => (
                <g key={i}>
                  <line x1={PAD_L} x2={W - PAD_R} y1={yScale(v)} y2={yScale(v)} stroke="#f1f5f9" />
                  <text x={4} y={yScale(v) + 4} fontSize={10} fill="#64748b">{Math.round(v)}</text>
                </g>
              ))}
              {peaks.map((p, i) => {
                const cx = PAD_L + i * ((W - PAD_L - PAD_R) / Math.max(1, peaks.length)) + 4;
                const y0 = yScale(p.peak_cm);
                const yBase = yScale(0);
                const color = p.exceeded_crit ? '#dc2626' : p.exceeded_low ? '#f59e0b' : '#10b981';
                return (
                  <g key={p.year}>
                    <rect x={cx} y={y0} width={bw} height={Math.max(1, yBase - y0)} fill={color} rx={2} />
                    <text x={cx + bw / 2} y={H - 12} fontSize={10} fill="#475569" textAnchor="middle">{p.year}</text>
                    <text x={cx + bw / 2} y={y0 - 3} fontSize={9} fill="#334155" textAnchor="middle">{Math.round(p.peak_cm)}</text>
                    <title>{`${p.year}: пик ${Math.round(p.peak_cm)} см (${p.peak_date}), дн. над НЯ: ${p.days_over_low}, над ОЯ: ${p.days_over_crit}`}</title>
                  </g>
                );
              })}
            </svg>
          </div>
        </div>
      )}


      <div>
        <div className="text-sm font-semibold text-slate-700 mb-2">Дни превышений НЯ/ОЯ (по годам)</div>
        <div className="flex flex-wrap gap-1.5">
          {state.exceedance_days_per_year.map(e => (
            <span key={e.year}
              className={`text-xs px-2 py-1 rounded-lg ${
                e.days_over_crit > 0 ? 'bg-red-100 text-red-700' :
                e.days_over_low > 0  ? 'bg-amber-100 text-amber-700' :
                                       'bg-emerald-50 text-emerald-700'
              }`}
              title={`${e.year}: НЯ ${e.days_over_low} дн., ОЯ ${e.days_over_crit} дн.`}>
              {e.year}: {e.days_over_low}/{e.days_over_crit}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}

