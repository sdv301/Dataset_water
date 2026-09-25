import React from 'react';
import { API_BASE } from '../config';

export function AgentSettingsPanel() {
  const [st, setSt] = React.useState<any>(null);
  const [sc, setSc] = React.useState<any>(null);
  const [err, setErr] = React.useState<string|null>(null);
  const [form, setForm] = React.useState({hour:'3', minute:'0', horizon:'14', will_flood_threshold:'0.7', verdict_red_threshold:'0.7'});
  const [saving, setSaving] = React.useState(false);
  const load = React.useCallback(async ()=>{
    try {
      const r = await fetch(API_BASE + '/agent/scheduler/status');
      if(r.ok){
        const j=await r.json();
        setSt(j);
        setForm({
          hour:String(j.hour??3),
          minute:String(j.minute??0),
          horizon:String(j.horizon??14),
          will_flood_threshold:String(j.will_flood_threshold??0.7),
          verdict_red_threshold:String(j.verdict_red_threshold??0.7)
        });
      }
      const r2 = await fetch(API_BASE + '/agent/self-check');
      if(r2.ok) setSc(await r2.json());
    } catch(e:any){ setErr(String(e)); }
  },[]);
  React.useEffect(()=>{ load(); },[load]);
  if(err) return <div className="text-sm text-red-600">Error: {err}</div>;
  if(!st) return <div className="text-sm text-slate-500">Loading...</div>;
  const run = ()=>{ fetch(API_BASE + '/agent/scheduler/run-now',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({background:true})}).then(()=> setTimeout(load, 800)); };
  const save = async ()=>{
    setSaving(true);
    try{
      const patch:any={};
      const h=parseInt(form.hour,10); if(!isNaN(h)) patch.hour=h;
      const m=parseInt(form.minute,10); if(!isNaN(m)) patch.minute=m;
      const hz=parseInt(form.horizon,10); if(!isNaN(hz)) patch.horizon=hz;
      const wf=parseFloat(form.will_flood_threshold.replace(',','.')); if(!isNaN(wf)) patch.will_flood_threshold=wf;
      const rd=parseFloat(form.verdict_red_threshold.replace(',','.')); if(!isNaN(rd)) patch.verdict_red_threshold=rd;
      const r=await fetch(API_BASE + '/agent/scheduler/config',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify(patch)});
      if(!r.ok){ const t=await r.text(); throw new Error(t); }
      setSt(await r.json());
      await load();
    } catch(e:any){ alert('Save error: '+String(e)); } finally{ setSaving(false); }
  };
  const toggle = async ()=>{
    const r=await fetch(API_BASE + '/agent/scheduler/config',{method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({enabled: !st.enabled})});
    if(r.ok) setSt(await r.json());
  };
  return (
    <div className="space-y-4">
      <div className="bg-white rounded-2xl border border-slate-200 p-6 shadow-sm space-y-4">
        <h3 className="font-semibold text-slate-800">Agent settings</h3>
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4 text-sm">
          <div>Status: <span className={st.enabled? 'text-emerald-700 font-semibold':'text-amber-700 font-semibold'}>{st.enabled? 'enabled':'disabled'}</span></div>
          <div>Schedule: {String(st.hour).padStart(2,'0')}:{String(st.minute).padStart(2,'0')} {st.timezone}</div>
          <div>Horizon: {st.horizon} days</div>
          <div>will_flood thr: {st.will_flood_threshold ?? '-'}</div>
          <div>red thr: {st.verdict_red_threshold ?? '-'}</div>
          <div>Next run: {st.next_run || '-'}</div>
          <div>Last run: {st.last_run_finished || '-'}</div>
          <div>Snapshots: {st.snapshot_stats?.total ?? st.snapshot_stats?.count ?? '-'}</div>
        </div>
        <div className="border-t pt-4 space-y-3">
          <div className="text-xs font-semibold text-slate-600">Schedule and thresholds (persisted)</div>
          <div className="grid grid-cols-2 md:grid-cols-5 gap-3 text-sm">
            <label className="space-y-1"><span className="text-xs text-slate-500">Hour 0-23</span><input className="w-full border rounded-lg px-2 py-1.5" value={form.hour} onChange={e=>setForm({...form, hour:e.target.value})}/></label>
            <label className="space-y-1"><span className="text-xs text-slate-500">Minute 0-59</span><input className="w-full border rounded-lg px-2 py-1.5" value={form.minute} onChange={e=>setForm({...form, minute:e.target.value})}/></label>
            <label className="space-y-1"><span className="text-xs text-slate-500">Horizon 1-60</span><input className="w-full border rounded-lg px-2 py-1.5" value={form.horizon} onChange={e=>setForm({...form, horizon:e.target.value})}/></label>
            <label className="space-y-1"><span className="text-xs text-slate-500">will_flood 0.1-0.99</span><input className="w-full border rounded-lg px-2 py-1.5" value={form.will_flood_threshold} onChange={e=>setForm({...form, will_flood_threshold:e.target.value})}/></label>
            <label className="space-y-1"><span className="text-xs text-slate-500">red 0.1-0.99</span><input className="w-full border rounded-lg px-2 py-1.5" value={form.verdict_red_threshold} onChange={e=>setForm({...form, verdict_red_threshold:e.target.value})}/></label>
          </div>
          <div className="flex flex-wrap gap-2">
            <button onClick={save} disabled={saving} className="bg-blue-600 text-white px-4 py-2 rounded-lg text-sm disabled:opacity-40">{saving?'Saving...':'Save'}</button>
            <button onClick={toggle} className="bg-white border border-slate-200 px-4 py-2 rounded-lg text-sm">{st.enabled? 'Disable':'Enable'}</button>
            <button onClick={run} disabled={st.running} className="bg-white border border-slate-200 px-4 py-2 rounded-lg text-sm disabled:opacity-40">{st.running? 'Running...':'Run now'}</button>
            <button onClick={load} className="bg-white border border-slate-200 px-4 py-2 rounded-lg text-sm">Refresh</button>
          </div>
        </div>
        {sc && (
          <div className="border-t pt-4 space-y-2">
            <div className="text-xs font-semibold text-slate-600">Self-check</div>
            {sc.ok === false ? <div className="text-xs text-red-600">{sc.error}</div> : (
              <>
                <div className="text-xs text-slate-600">Rows: {sc.total_rows} | stale: {sc.stale_count} | missing OYA: {sc.missing_oya_count} | model age: {sc.model_age_days ?? '-'} days</div>
                {sc.gaps?.length ? <ul className="text-xs space-y-1">{sc.gaps.map((g:any)=>(<li key={g.feature} className={g.severity==='high'?'text-amber-700':'text-slate-600'}>{g.label} ({g.feature}): {g.non_null} ({g.pct}%)</li>))}</ul> : <div className="text-xs text-emerald-700">No critical gaps</div>}
              </>
            )}
          </div>
        )}
        <details className="bg-slate-50 border border-slate-200 rounded-lg px-3 py-2"><summary className="text-xs font-semibold cursor-pointer">Debug (raw status)</summary><pre className="text-[11px] overflow-auto mt-2">{JSON.stringify(st,null,2)}</pre></details>
      </div>
    </div>
  );
}

