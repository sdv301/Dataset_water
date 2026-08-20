import React, { useEffect, useState } from 'react';
import {
  BarChart, Bar, LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip as RechartsTooltip, Legend, ResponsiveContainer, ReferenceLine,
} from 'recharts';
import { API_BASE } from '../config';

interface Props { river?: string; post?: string; horizon?: number; }
interface DriftFeature { feature: string; key: string; ks: number; p_value: number; flag: boolean; }
interface DriftResp { ok: boolean; drift_detected?: boolean; max_ks?: number; worst_feature?: string; per_feature?: DriftFeature[]; note?: string; error?: string; }
interface ReliabilityResp { ok: boolean; centers?: number[]; frac_raw?: number[]; frac_cal?: number[]; counts_raw?: number[]; brier_raw?: number; brier_cal?: number; ece_raw?: number; n?: number; error?: string; }

export function QualityPanel({ river, post, horizon = 7 }: Props) {
  const [drift, setDrift] = useState<DriftResp | null>(null);
  const [rel, setRel] = useState<ReliabilityResp | null>(null);
  const [level, setLevel] = useState<'low' | 'crit'>('low');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    if (!river || !post) return;
    setLoading(true); setErr(null);
    const encR = encodeURIComponent(river);
    const encP = encodeURIComponent(post);
    Promise.all([
      fetch(`${API_BASE}/drift/${encR}/${encP}`).then(async r => {
        const b = await r.json().catch(() => ({} as any));
        return r.ok ? b : { ok: false, error: b.detail || `HTTP ${r.status}` };
      }),
      fetch(`${API_BASE}/reliability/${encR}/${encP}?horizon=${horizon}&level=${level}`).then(async r => {
        const b = await r.json().catch(() => ({} as any));
        return r.ok ? b : { ok: false, error: b.detail || `HTTP ${r.status}` };
      }),
    ]).then(([d, r2]) => { setDrift(d as DriftResp); setRel(r2 as ReliabilityResp); })
      .catch(e => setErr(String(e))).finally(() => setLoading(false));
  }, [river, post, horizon, level]);

  const relData = rel?.ok && rel.centers
    ? rel.centers.map((c, i) => ({ p: c, ideal: c, raw: rel.frac_raw?.[i] ?? 0, cal: rel.frac_cal?.[i] ?? 0 }))
    : [];

  return (
    <div className="bg-white p-6 rounded-2xl border border-slate-200 shadow-sm space-y-6">
      <div className="flex items-center justify-between flex-wrap gap-2">
        <div>
          <h3 className="text-base font-semibold text-slate-800">Диагностика качества модели</h3>
          <p className="text-xs text-slate-500">Дрейф признаков (KS-тест) и калибровка вероятностей (reliability + Brier/ECE).</p>
        </div>
        <div className="flex items-center gap-2 text-xs">
          <span className="text-slate-500">Порог:</span>
          <select value={level} onChange={e => setLevel(e.target.value as 'low' | 'crit')} className="border border-slate-200 rounded-lg px-2 py-1">
            <option value="low">НЯ</option><option value="crit">ОЯ</option>
          </select>
          <span className="text-slate-500">горизонт: {horizon} дн.</span>
        </div>
      </div>
      {loading && <div className="text-sm text-slate-500">Загрузка…</div>}
      {err && <div className="text-sm text-red-600">{err}</div>}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          {!drift?.ok ? (
            <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              {drift?.error || 'Нет данных для оценки дрейфа'}
            </div>
          ) : (
            <>
              <div className={`text-xs px-3 py-2 rounded-lg mb-2 ${drift.drift_detected ? 'bg-red-50 border border-red-200 text-red-800' : 'bg-emerald-50 border border-emerald-200 text-emerald-800'}`}>
                {drift.drift_detected ? 'Обнаружен дрейф' : 'Дрейф не выявлен'}
                {drift.worst_feature && drift.max_ks != null && <> — максимум KS={drift.max_ks} по «{drift.worst_feature}»</>}
              </div>
              <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={drift.per_feature || []} margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" vertical={false} />
                    <XAxis dataKey="feature" fontSize={10} tickLine={false} axisLine={false} interval={0} angle={-15} textAnchor="end" height={60} />
                    <YAxis fontSize={10} tickLine={false} axisLine={false} domain={[0, 1]} />
                    <RechartsTooltip />
                    <ReferenceLine y={0.25} stroke="#ef4444" strokeDasharray="3 3" />
                    <Bar dataKey="ks" fill="#3b82f6" radius={[4, 4, 0, 0]} />
                  </BarChart>
                </ResponsiveContainer>
              </div>
              <p className="text-[11px] text-slate-500 mt-2">{drift.note}</p>
            </>
          )}
        </div>
        <div>
          <h4 className="text-sm font-semibold text-slate-700 mb-2">Reliability diagram</h4>
          {!rel?.ok ? (
            <div className="text-xs text-amber-700 bg-amber-50 border border-amber-200 rounded-lg px-3 py-2">
              {rel?.error || 'Нужна обученная модель и история'}
            </div>
          ) : (
            <>
              <div className="grid grid-cols-3 gap-2 text-[11px] mb-2">
                <div className="bg-slate-50 rounded-lg px-2 py-1"><div className="text-slate-500">Brier (raw)</div><div className="font-semibold text-slate-800">{rel.brier_raw}</div></div>
                <div className="bg-slate-50 rounded-lg px-2 py-1"><div className="text-slate-500">Brier (cal)</div><div className="font-semibold text-slate-800">{rel.brier_cal}</div></div>
                <div className="bg-slate-50 rounded-lg px-2 py-1"><div className="text-slate-500">ECE / n</div><div className="font-semibold text-slate-800">{rel.ece_raw} / {rel.n}</div></div>
              </div>
              <div className="h-[220px]">
                <ResponsiveContainer width="100%" height="100%">
                  <LineChart data={relData} margin={{ top: 10, right: 10, bottom: 10, left: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                    <XAxis dataKey="p" fontSize={10} tickLine={false} axisLine={false} domain={[0, 1]} type="number" />
                    <YAxis fontSize={10} tickLine={false} axisLine={false} domain={[0, 1]} />
                    <RechartsTooltip />
                    <Legend />
                    <Line type="monotone" dataKey="ideal" name="Идеал" stroke="#94a3b8" strokeDasharray="4 4" dot={false} />
                    <Line type="monotone" dataKey="raw" name="Модель (raw)" stroke="#3b82f6" dot={{ r: 3 }} />
                    <Line type="monotone" dataKey="cal" name="Калиброванная" stroke="#16a34a" dot={{ r: 3 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
              <p className="text-[11px] text-slate-500 mt-2">Точка = фактическая частота в бине предсказанной вероятности. ECE&#60;0.05 — отлично.</p>
            </>
          )}
        </div>
      </div>
    </div>
  );
}

