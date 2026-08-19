import { useEffect, useState } from 'react';
import { API_BASE } from '../config';
export function BacktestPanel({ river, post }: { river: string; post: string }) {
  const [data, setData] = useState<any>(null);
  const [err, setErr] = useState('');
  useEffect(() => {
    if (!river || !post) return;
    fetch(`${API_BASE}/backtest/${encodeURIComponent(river)}/${encodeURIComponent(post)}?horizon=7&limit=30`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setData).catch(e => setErr(String(e)));
  }, [river, post]);
  if (err) return <div className="text-sm text-slate-500">Бэктест недоступен: {err}</div>;
  if (!data) return <div className="text-sm text-slate-500">Загрузка бэктеста…</div>;
  return (
    <div className="bg-white p-4 rounded-xl border border-slate-200">
      <h4 className="font-semibold text-slate-800 mb-2">Бэктест (последние {data.n} дней, naive lag-1)</h4>
      <div className="text-sm text-slate-600 mb-2">RMSE {data.rmse} см · MAE {data.mae} см {data.manifest_ref ? '· есть метрика из manifest' : ''}</div>
      <div className="max-h-40 overflow-auto text-xs">
        <table className="w-full">
          <thead><tr className="text-slate-500"><th className="text-left">Дата</th><th>Факт</th><th>Прогноз</th><th>Ошибка</th></tr></thead>
          <tbody>{(data.points||[]).slice(-10).map((p:any)=><tr key={p.date}><td>{p.date}</td><td className="text-right">{Math.round(p.actual)}</td><td className="text-right">{Math.round(p.predicted)}</td><td className={`text-right ${Math.abs(p.error)>20?'text-amber-600':''}`}>{p.error}</td></tr>)}</tbody>
        </table>
      </div>
    </div>
  );
}
