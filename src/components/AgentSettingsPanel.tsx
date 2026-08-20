import React from 'react';
import { API_BASE } from '../config';

export function AgentSettingsPanel() {
  const [st, setSt] = React.useState<any>(null);
  const [err, setErr] = React.useState<string|null>(null);
  const load = React.useCallback(()=>{ fetch(`${API_BASE}/agent/scheduler/status`).then(r=>r.ok?r.json():null).then(setSt).catch(e=> setErr(String(e))); },[]);
  React.useEffect(()=>{ load(); },[load]);
  if (err) return <div className="text-sm text-red-600">Ошибка: {err}</div>;
  if (!st) return <div className="text-sm text-slate-500">Загрузка…</div>;
  const thresholdNote = 'Порог will_flood = P(q90 ≥ НЯ) ≥ 0.7 — строго фиксирован в коде агента (flood_agent.py).';
  const run = ()=>{ fetch(`${API_BASE}/agent/scheduler/run-now`,{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({background:true})}).then(()=> setTimeout(load, 800)); };
  const save = async (patch: any)=>{ const r= await fetch(`${API_BASE}/agent/scheduler/config`,{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(patch)}); if(r.ok) setSt(await r.json()); };
  return (
    <div className="space-y-4">
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-3">
        <h3 className="font-semibold text-slate-800">Настройки агента паводка</h3>
        <p className="text-xs text-slate-500">{thresholdNote}</p>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div>Статус планировщика: <span className={st.enabled? 'text-emerald-700 font-semibold':'text-amber-700 font-semibold'}>{st.enabled? 'включен':'выключен'}</span></div>
          <div>Расписание: {String(st.hour).padStart(2,'0')}:{String(st.minute).padStart(2,'0')} {st.timezone}</div>
          <div>Горизонт: {st.horizon} дн.</div>
          <div>След. прогон: {st.next_run || '—'}</div>
          <div>Последний прогон: {st.last_run_finished || '—'}</div>
          <div>Снимков в кеше: {st.snapshot_stats?.count ?? '—'}</div>
        </div>
        <div className="flex flex-wrap gap-2">
          <button onClick={run} disabled={st.running} className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm disabled:opacity-40">{st.running? 'Выполняется…':'Запустить прогон сейчас'}</button>
          <button onClick={()=> save({enabled: !st.enabled})} className="bg-white border border-slate-200 px-4 py-2 rounded-lg text-sm">{st.enabled? 'Выключить планировщик':'Включить планировщик'}</button>
          <button onClick={load} className="bg-white border border-slate-200 px-4 py-2 rounded-lg text-sm">Обновить</button>
        </div>
        <details className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2"><summary className="text-xs font-semibold cursor-pointer">Отладка (raw status)</summary><pre className="text-[11px] overflow-auto mt-2">{JSON.stringify(st,null,2)}</pre></details>
      </div>
    </div>
  );
}
