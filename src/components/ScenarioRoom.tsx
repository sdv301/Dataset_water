import React from 'react';
import { ComposedChart, Line, XAxis, YAxis, CartesianGrid, Tooltip as RechartsTooltip, ResponsiveContainer, ReferenceLine, Legend } from 'recharts';
import { formatDateRu } from '../ForecastPanels';

interface ScenarioPoint { date: string; median: number; q10?: number; q90?: number; q95?: number }
interface Scenario { id: string; label: string; temp_delta: number; precip_pct: number; snow_pct: number; points: ScenarioPoint[] }

interface Props {
  scenarios: Scenario[];
  warningLevel: number;
  dangerLevel: number;
  baseDate?: string;
  onThresholdChange?: (low: number, crit: number) => void;
  low: number;
  crit: number;
}

export function ScenarioRoom({ scenarios, warningLevel, dangerLevel, baseDate, low, crit, onThresholdChange }: Props) {
  const [localLow, setLocalLow] = React.useState(low);
  const [localCrit, setLocalCrit] = React.useState(crit);
  React.useEffect(()=>{ setLocalLow(low); setLocalCrit(crit); },[low, crit]);

  const sliders = scenarios.find(s=>s.id==='sliders');
  const chartData = React.useMemo(()=>{
    if(!scenarios.length) return [];
    const byDate: Record<string, any> = {};
    scenarios.forEach(sc=>{
      (sc.points||[]).forEach(p=>{
        const d = p.date.slice(0,10);
        if(!byDate[d]) byDate[d] = { date: d, label: formatDateRu(d,'dd MMM') };
        byDate[d][sc.id] = p.median;
        byDate[d][sc.id+'_q10'] = p.q10;
        byDate[d][sc.id+'_q95'] = p.q95;
      });
    });
    return Object.values(byDate).sort((a:any,b:any)=> a.date.localeCompare(b.date));
  },[scenarios]);

  const palette: Record<string,string> = { baseline:'#2563eb', sliders:'#16a34a', wet_warm:'#dc2626', cold_dry:'#0891b2', heavy_rain:'#9333ea' };

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
        <h3 className="font-semibold text-slate-800 mb-2">Комната сценариев — what-if анализ</h3>
        <p className="text-sm text-slate-600 mb-4">
          Сравниваются 5 сценариев прогноза на 30 дней от базы {baseDate ? formatDateRu(baseDate) : '—'}. Ползунки «Вашего сценария» — в левой панели (температура/осадки/снег).
          Пороги НЯ/ОЯ можно уточнить ниже — пересчёт риска происходит локально на графике.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs text-slate-500">Порог НЯ (см)</span>
            <input type="number" value={localLow} onChange={e=>setLocalLow(Number(e.target.value))} className="border rounded-lg px-3 py-2" />
          </label>
          <label className="flex flex-col gap-1 text-sm">
            <span className="text-xs text-slate-500">Порог ОЯ (см)</span>
            <input type="number" value={localCrit} onChange={e=>setLocalCrit(Number(e.target.value))} className="border rounded-lg px-3 py-2" />
          </label>
          <div className="flex items-end">
            <button onClick={()=> onThresholdChange?.(localLow, localCrit)} className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm font-medium hover:bg-blue-700">Применить пороги</button>
          </div>
        </div>
        {sliders && (
          <div className="mt-4 text-xs text-slate-600 bg-slate-50 border border-slate-100 rounded-lg px-3 py-2">
            Ваш сценарий: ΔT {sliders.temp_delta}°C, осадки {sliders.precip_pct}%, снег {sliders.snow_pct}% — зелёная линия на графике.
          </div>
        )}
      </div>

      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
        <h4 className="font-semibold text-slate-800 mb-4">Сравнение сценариев (медиана)</h4>
        {chartData.length===0 ? <div className="text-sm text-slate-500">Нет данных сценариев — выберите станцию с моделью.</div> : (
          <div className="h-[380px]">
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart data={chartData}>
                <CartesianGrid strokeDasharray="3 3" vertical={false} stroke="#e2e8f0" />
                <XAxis dataKey="label" fontSize={11} tickLine={false} axisLine={false} minTickGap={18} />
                <YAxis fontSize={11} unit=" см" />
                <RechartsTooltip />
                <Legend />
                <ReferenceLine y={localLow} stroke="#f97316" strokeDasharray="4 4" label={{ value:`НЯ ${localLow}`, fill:'#f97316', fontSize:11 }} />
                <ReferenceLine y={localCrit} stroke="#ef4444" strokeDasharray="4 4" label={{ value:`ОЯ ${localCrit}`, fill:'#ef4444', fontSize:11 }} />
                {scenarios.map(sc=> (
                  <Line key={sc.id} type="monotone" dataKey={sc.id} name={sc.label} stroke={palette[sc.id]||'#64748b'} dot={false} strokeWidth={sc.id==='sliders'?2.5:2} />
                ))}
              </ComposedChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </div>
  );
}
