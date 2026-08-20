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
  tempDelta?: number;
  precipPct?: number;
  snowPct?: number;
  onScenarioParamsChange?: (p: { temp_delta: number; precip_pct: number; snow_pct: number }) => void;
}

export function ScenarioRoom({ scenarios, warningLevel, dangerLevel, baseDate, low, crit, onThresholdChange, tempDelta, precipPct, snowPct, onScenarioParamsChange }: Props) {
  const [localLow, setLocalLow] = React.useState(low);
  const [localCrit, setLocalCrit] = React.useState(crit);
  React.useEffect(()=>{ setLocalLow(low); setLocalCrit(crit); },[low, crit]);
  const [visible, setVisible] = React.useState<Record<string, boolean>>({});
  React.useEffect(()=>{ if(!scenarios.length) return; setVisible(prev=>{ const n: Record<string,boolean>={}; scenarios.forEach(s=>{ n[s.id]=prev[s.id]!==undefined?prev[s.id]:true; }); return n; }); },[scenarios]);
  const [t, setT] = React.useState(tempDelta??0);
  const [p, setP] = React.useState(precipPct??100);
  const [s, setS] = React.useState(snowPct??100);
  React.useEffect(()=>{ if(tempDelta!==undefined) setT(tempDelta); },[tempDelta]);
  React.useEffect(()=>{ if(precipPct!==undefined) setP(precipPct); },[precipPct]);
  React.useEffect(()=>{ if(snowPct!==undefined) setS(snowPct); },[snowPct]);
  const LS='scenario_room_v1';
  React.useEffect(()=>{ try{ const j=JSON.parse(localStorage.getItem(LS)||'null'); if(j){ if(typeof j.t==='number') setT(j.t); if(typeof j.p==='number') setP(j.p); if(typeof j.s==='number') setS(j.s); onScenarioParamsChange?.({temp_delta:j.t??0, precip_pct:j.p??100, snow_pct:j.s??100}); } }catch{} },[]);
  const apply = (a=t,b=p,c=s)=>{ try{ localStorage.setItem(LS, JSON.stringify({t:a,p:b,s:c})); }catch{} onScenarioParamsChange?.({temp_delta:a, precip_pct:b, snow_pct:c}); };
  const presets=[{l:'Базовый',t:0,p:100,s:100},{l:'Тепло +2°C',t:2,p:100,s:100},{l:'Дождливо 150%',t:0,p:150,s:100},{l:'Мало снега 50%',t:0,p:100,s:50},{l:'Экстрим +3/200%',t:3,p:200,s:120}];

  const sliders = scenarios.find(s=>s.id==='sliders');
  const palette: Record<string,string> = { baseline:'#2563eb', sliders:'#16a34a', wet_warm:'#dc2626', cold_dry:'#0891b2', heavy_rain:'#9333ea' };
  const filtered = React.useMemo(()=> scenarios.filter(s=> visible[s.id]!==false), [scenarios, visible]);
  const chartData = React.useMemo(()=>{
    if(!filtered.length) return [];
    const byDate: Record<string, any> = {};
    filtered.forEach(sc=>{
      (sc.points||[]).forEach(pt=>{
        const d = pt.date.slice(0,10);
        if(!byDate[d]) byDate[d] = { date: d, label: formatDateRu(d,'dd MMM') };
        byDate[d][sc.id] = pt.median;
      });
    });
    return Object.values(byDate).sort((a:any,b:any)=> a.date.localeCompare(b.date));
  },[filtered]);
  const deltas = React.useMemo(()=>{
    const base = scenarios.find(s=>s.id==='baseline');
    if(!base) return null;
    return scenarios.filter(s=>s.id!=='baseline').map(sc=>{
      const bMax = Math.max(...(base.points||[]).map(p=>p.median));
      const sMax = Math.max(...(sc.points||[]).map(p=>p.median));
      const bMean = (base.points||[]).reduce((a,c)=>a+c.median,0)/Math.max(1,(base.points||[]).length);
      const sMean = (sc.points||[]).reduce((a,c)=>a+c.median,0)/Math.max(1,(sc.points||[]).length);
      const over = (sc.points||[]).filter(pt=> (pt.q90??pt.median) >= localLow).length;
      const prob = (sc.points||[]).length ? over/(sc.points||[]).length : 0;
      return { id: sc.id, label: sc.label, dMax: sMax-bMax, dMean: sMean-bMean, pNY: prob };
    });
  },[scenarios, localLow]);
  const exportCsv = ()=>{
    if(!chartData.length) return;
    const header=['date',...filtered.map(s=>s.id)].join(',');
    const rows=chartData.map((r:any)=> [r.date,...filtered.map(s=> r[s.id]??'')].join(','));
    const csv=[header,...rows].join('\n');
    const blob=new Blob([csv],{type:'text/csv;charset=utf-8;'});
    const url=URL.createObjectURL(blob); const a=document.createElement('a'); a.href=url; a.download=`scenarios_${baseDate||'forecast'}.csv`; a.click(); URL.revokeObjectURL(url);
  };

  return (
    <div className="space-y-6">
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm">
        <h3 className="font-semibold text-slate-800 mb-2">Комната сценариев — what-if анализ</h3>
        <p className="text-sm text-slate-600 mb-4">
          Сравниваются сценарии прогноза на 30 дней от базы {baseDate ? formatDateRu(baseDate) : '—'}. Порог наводнения агента: P(q90 ≥ НЯ) ≥ 0.7.
        </p>
        <div className="flex flex-wrap gap-2 mb-4">
          {presets.map(pr=> (
            <button key={pr.l} onClick={()=>{ setT(pr.t); setP(pr.p); setS(pr.s); apply(pr.t,pr.p,pr.s); }} className="text-xs bg-slate-100 hover:bg-slate-200 border border-slate-200 rounded-full px-3 py-1.5">{pr.l}</button>
          ))}
        </div>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
          <div><div className="flex justify-between text-xs mb-1"><span>Температура ΔT</span><span className="font-mono">{t>0?'+':''}{t}°C</span></div><input type="range" min={-5} max={5} step={0.5} value={t} onChange={e=> setT(Number(e.target.value))} onMouseUp={()=>apply(t,p,s)} onTouchEnd={()=>apply(t,p,s)} className="w-full accent-blue-500" /></div>
          <div><div className="flex justify-between text-xs mb-1"><span>Осадки %</span><span className="font-mono">{p}%</span></div><input type="range" min={0} max={200} step={10} value={p} onChange={e=> setP(Number(e.target.value))} onMouseUp={()=>apply(t,p,s)} onTouchEnd={()=>apply(t,p,s)} className="w-full accent-blue-500" /></div>
          <div><div className="flex justify-between text-xs mb-1"><span>Снег %</span><span className="font-mono">{s}%</span></div><input type="range" min={0} max={200} step={10} value={s} onChange={e=> setS(Number(e.target.value))} onMouseUp={()=>apply(t,p,s)} onTouchEnd={()=>apply(t,p,s)} className="w-full accent-blue-500" /></div>
        </div>
        <div className="flex gap-2 mb-4">
          <button onClick={()=>apply(t,p,s)} className="bg-blue-600 text-white text-xs px-3 py-1.5 rounded-lg">Применить сценарий</button>
          <button onClick={exportCsv} disabled={!chartData.length} className="bg-white border border-slate-200 text-xs px-3 py-1.5 rounded-lg disabled:opacity-40">Экспорт CSV</button>
        </div>
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
        <div className="flex flex-wrap gap-3 mt-4">
          {scenarios.map(sc=> (
            <label key={sc.id} className="flex items-center gap-1.5 text-xs cursor-pointer">
              <input type="checkbox" checked={visible[sc.id]!==false} onChange={e=> setVisible(v=> ({...v,[sc.id]:e.target.checked}))} />
              <span className="w-3 h-3 rounded-full inline-block" style={{background: palette[sc.id]||'#64748b'}} />{sc.label}
            </label>
          ))}
        </div>
        {deltas && deltas.length>0 && (
          <div className="mt-4 overflow-auto">
            <table className="w-full text-xs border border-slate-200 rounded-lg overflow-hidden">
              <thead className="bg-slate-50"><tr><th className="px-2 py-1 text-left">Сценарий</th><th className="px-2 py-1 text-right">Δ макс, см</th><th className="px-2 py-1 text-right">Δ средн, см</th><th className="px-2 py-1 text-right">P(≥НЯ)</th></tr></thead>
              <tbody>{deltas.map(d=> (<tr key={d.id} className="border-t border-slate-100"><td className="px-2 py-1 flex items-center gap-1.5"><span className="w-2 h-2 rounded-full" style={{background: palette[d.id]||'#64748b'}} />{d.label} {d.pNY>=0.7 && <span className="bg-red-500 text-white px-1.5 py-0.5 rounded-full text-[10px]">НЯ+ ≥0.7</span>}</td><td className="px-2 py-1 text-right font-mono">{d.dMax>0?'+':''}{Math.round(d.dMax)}</td><td className="px-2 py-1 text-right font-mono">{d.dMean>0?'+':''}{Math.round(d.dMean)}</td><td className="px-2 py-1 text-right font-mono">{(d.pNY*100).toFixed(0)}%</td></tr>))}</tbody>
            </table>
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
                {filtered.map(sc=> (
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
