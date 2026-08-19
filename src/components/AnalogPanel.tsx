import { useEffect, useState } from 'react';
import { API_BASE } from '../config';
export function AnalogPanel({ river, post }: { river: string; post: string }) {
  const [data, setData] = useState<any>(null);
  useEffect(() => {
    if (!river || !post) return;
    fetch(`${API_BASE}/analog/${encodeURIComponent(river)}/${encodeURIComponent(post)}?k=5`)
      .then(r => r.ok ? r.json() : Promise.reject(r.statusText))
      .then(setData).catch(() => setData({ analogs: [] }));
  }, [river, post]);
  if (!data) return <div className="text-sm text-slate-500">Загрузка аналогов…</div>;
  if (!data.analogs?.length) return <div className="text-sm text-slate-500">Недостаточно истории для аналогов.</div>;
  return (
    <div className="bg-white p-4 rounded-xl border border-slate-200">
      <h4 className="font-semibold text-slate-800 mb-2">Годы-аналоги (по корреляции 60 дн.)</h4>
      <ul className="text-sm space-y-1">{data.analogs.map((a: any) => <li key={a.year} className="flex justify-between"><span>{a.year}</span><span className="text-slate-500">r={a.corr} · n={a.n}</span></li>)}</ul>
    </div>
  );
}
