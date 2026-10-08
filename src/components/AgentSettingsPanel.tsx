import React from 'react';
import { API_BASE } from '../config';

function formatLocalTime(isoStr?: string | null): string {
  if (!isoStr) return '-';
  try {
    const d = new Date(isoStr);
    if (isNaN(d.getTime())) return isoStr;
    return d.toLocaleString('ru-RU', { timeZone: 'Asia/Yakutsk', dateStyle: 'short', timeStyle: 'short' }) + ' (Якт)';
  } catch {
    return isoStr;
  }
}

export function AgentSettingsPanel() {
  const [st, setSt] = React.useState<any>(null);
  const [sc, setSc] = React.useState<any>(null);
  const [err, setErr] = React.useState<string|null>(null);
  const [form, setForm] = React.useState({
    hour: '3',
    minute: '0',
    horizon: '14',
    preset: 'balanced',
    will_flood_threshold: '0.75',
    verdict_red_threshold: '0.75',
    station_timeout_seconds: '15',
    station_delay_seconds: '0.05',
    challenger_enabled: false,
  });
  const [saving, setSaving] = React.useState(false);
  const load = React.useCallback(async ()=>{
    try {
      const r = await fetch(API_BASE + '/agent/scheduler/status');
      if(r.ok){
        const j=await r.json();
        setSt(j);
        setForm({
          hour: String(j.schedule?.hour ?? j.hour ?? 3),
          minute: String(j.schedule?.minute ?? j.minute ?? 0),
          horizon: String(j.horizon_days ?? j.horizon ?? 14),
          preset: String(j.thresholds?.preset ?? j.preset ?? 'balanced'),
          will_flood_threshold: String(j.thresholds?.will_flood_threshold ?? j.will_flood_threshold ?? 0.75),
          verdict_red_threshold: String(j.thresholds?.verdict_red_threshold ?? j.verdict_red_threshold ?? 0.75),
          station_timeout_seconds: String(j.safety?.station_timeout_seconds ?? 15),
          station_delay_seconds: String(j.safety?.station_delay_seconds ?? 0.05),
          challenger_enabled: Boolean(j.challenger?.enabled ?? false),
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
  const handlePresetChange = (presetName: string) => {
    if (presetName === 'sensitive') {
      setForm((prev) => ({ ...prev, preset: 'sensitive', will_flood_threshold: '0.60', verdict_red_threshold: '0.60' }));
    } else if (presetName === 'balanced') {
      setForm((prev) => ({ ...prev, preset: 'balanced', will_flood_threshold: '0.75', verdict_red_threshold: '0.75' }));
    } else if (presetName === 'conservative') {
      setForm((prev) => ({ ...prev, preset: 'conservative', will_flood_threshold: '0.85', verdict_red_threshold: '0.85' }));
    } else {
      setForm((prev) => ({ ...prev, preset: 'custom' }));
    }
  };
  const save = async ()=>{
    setSaving(true);
    try{
      const patch:any={};
      const h=parseInt(form.hour,10); if(!isNaN(h)) patch.hour=h;
      const m=parseInt(form.minute,10); if(!isNaN(m)) patch.minute=m;
      const hz=parseInt(form.horizon,10); if(!isNaN(hz)) patch.horizon=hz;
      const wf=parseFloat(form.will_flood_threshold.replace(',','.')); if(!isNaN(wf)) patch.will_flood_threshold=wf;
      const rd=parseFloat(form.verdict_red_threshold.replace(',','.')); if(!isNaN(rd)) patch.verdict_red_threshold=rd;
      const sto = parseFloat(form.station_timeout_seconds.replace(',', '.')); if (!isNaN(sto)) patch.station_timeout_seconds = sto;
      const sdl = parseFloat(form.station_delay_seconds.replace(',', '.')); if (!isNaN(sdl)) patch.station_delay_seconds = sdl;
      patch.preset = form.preset;
      patch.challenger_enabled = form.challenger_enabled;
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
        <div className="flex items-center justify-between">
          <h3 className="font-semibold text-slate-800">Настройки агента паводков</h3>
          <span className={`text-xs px-2.5 py-1 rounded-full font-medium ${st.enabled ? 'bg-emerald-50 text-emerald-700 border border-emerald-200' : 'bg-amber-50 text-amber-700 border border-amber-200'}`}>
            {st.enabled ? 'Планировщик активен' : 'Планировщик отключен'}
          </span>
        </div>

        {/* Инфо-карточки */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-3 text-xs bg-slate-50 p-3 rounded-xl border border-slate-100">
          <div><span className="text-slate-400 block">Расписание:</span><span className="font-medium text-slate-700">{String(st.schedule?.hour ?? st.hour).padStart(2,'0')}:{String(st.schedule?.minute ?? st.minute).padStart(2,'0')} Якт</span></div>
          <div><span className="text-slate-400 block">Горизонт:</span><span className="font-medium text-slate-700">{st.horizon_days ?? st.horizon} дней</span></div>
          <div><span className="text-slate-400 block">Пресет / Пороги:</span><span className="font-medium text-slate-700">{st.thresholds?.preset ?? st.preset} ({st.thresholds?.will_flood_threshold ?? st.will_flood_threshold})</span></div>
          <div><span className="text-slate-400 block">Challenger (TimesFM):</span><span className={`font-medium ${st.challenger?.enabled ? 'text-indigo-600' : 'text-slate-500'}`}>{st.challenger?.enabled ? 'Включен' : 'Выключен'}</span></div>
          <div><span className="text-slate-400 block">Следующий запуск:</span><span className="font-medium text-slate-700">{formatLocalTime(st.next_run)}</span></div>
          <div><span className="text-slate-400 block">Последний прогон:</span><span className="font-medium text-slate-700">{formatLocalTime(st.last_run_finished)}</span></div>
          <div><span className="text-slate-400 block">Кеш снапшотов:</span><span className="font-medium text-slate-700">{st.snapshot_stats?.total ?? '-'} постов</span></div>
          <div><span className="text-slate-400 block">Ожидается паводок:</span><span className="font-medium text-amber-700">{st.snapshot_stats?.will_flood ?? 0}</span></div>
        </div>
        {/* Форма параметров */}
        <div className="border-t pt-4 space-y-4">
          <div className="text-xs font-semibold text-slate-700 uppercase tracking-wide">Параметры расписания и горизонта</div>
          <div className="grid grid-cols-2 md:grid-cols-3 gap-3 text-sm">
            <label className="space-y-1">
              <span className="text-xs text-slate-500">Час запуска (0-23 Якт)</span>
              <input className="w-full border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm" value={form.hour} onChange={(e) => setForm({ ...form, hour: e.target.value })} />
            </label>
            <label className="space-y-1">
              <span className="text-xs text-slate-500">Минута запуска (0-59)</span>
              <input className="w-full border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm" value={form.minute} onChange={(e) => setForm({ ...form, minute: e.target.value })} />
            </label>
            <label className="space-y-1">
              <span className="text-xs text-slate-500">Горизонт прогноза (дней)</span>
              <input className="w-full border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm" value={form.horizon} onChange={(e) => setForm({ ...form, horizon: e.target.value })} />
            </label>
          </div>

          <div className="text-xs font-semibold text-slate-700 uppercase tracking-wide pt-2">Пороги вероятности паводка</div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
            <label className="space-y-1">
              <span className="text-xs text-slate-500">Пресет порогов</span>
              <select
                className="w-full border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm bg-white"
                value={form.preset}
                onChange={(e) => handlePresetChange(e.target.value)}
              >
                <option value="sensitive">Чувствительный (0.60 / 0.60)</option>
                <option value="balanced">Сбалансированный (0.75 / 0.75)</option>
                <option value="conservative">Консервативный (0.85 / 0.85)</option>
                <option value="custom">Пользовательский (ручной)</option>
              </select>
            </label>
            <label className="space-y-1">
              <span className="text-xs text-slate-500">Порог «Паводок возможен» (0.1–0.99)</span>
              <input
                className="w-full border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm"
                value={form.will_flood_threshold}
                onChange={(e) => setForm({ ...form, will_flood_threshold: e.target.value, preset: 'custom' })}
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs text-slate-500">Порог «Паводок ожидается» (0.1–0.99)</span>
              <input
                className="w-full border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm"
                value={form.verdict_red_threshold}
                onChange={(e) => setForm({ ...form, verdict_red_threshold: e.target.value, preset: 'custom' })}
              />
            </label>
          </div>

          <div className="text-xs font-semibold text-slate-700 uppercase tracking-wide pt-2">Безопасность ресурсов контейнера (2 CPU / 2 GB RAM)</div>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 text-sm">
            <label className="space-y-1">
              <span className="text-xs text-slate-500">Таймаут на 1 станцию (сек)</span>
              <input
                className="w-full border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm"
                value={form.station_timeout_seconds}
                onChange={(e) => setForm({ ...form, station_timeout_seconds: e.target.value })}
              />
            </label>
            <label className="space-y-1">
              <span className="text-xs text-slate-500">Пауза между станциями (сек)</span>
              <input
                className="w-full border border-slate-200 rounded-lg px-2.5 py-1.5 text-sm"
                value={form.station_delay_seconds}
                onChange={(e) => setForm({ ...form, station_delay_seconds: e.target.value })}
              />
            </label>
            <label className="flex items-center space-x-2 pt-5 cursor-pointer">
              <input
                type="checkbox"
                className="rounded text-blue-600 focus:ring-blue-500 h-4 w-4"
                checked={form.challenger_enabled}
                onChange={(e) => setForm({ ...form, challenger_enabled: e.target.checked })}
              />
              <span className="text-xs text-slate-700 font-medium">Challenger TimesFM</span>
            </label>
          </div>

          <div className="flex flex-wrap gap-2 pt-2">
            <button onClick={save} disabled={saving} className="bg-blue-600 hover:bg-blue-700 text-white font-medium px-4 py-2 rounded-lg text-sm disabled:opacity-40 transition-colors">
              {saving ? 'Сохранение...' : 'Сохранить настройки'}
            </button>
            <button onClick={toggle} className="bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 font-medium px-4 py-2 rounded-lg text-sm transition-colors">
              {st.enabled ? 'Отключить расписание' : 'Включить расписание'}
            </button>
            <button onClick={run} disabled={st.running} className="bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 font-medium px-4 py-2 rounded-lg text-sm disabled:opacity-40 transition-colors">
              {st.running ? 'Идёт расчет...' : 'Запустить прогон сейчас'}
            </button>
            <button onClick={load} className="bg-white border border-slate-200 hover:bg-slate-50 text-slate-700 font-medium px-4 py-2 rounded-lg text-sm transition-colors">
              Обновить
            </button>
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

