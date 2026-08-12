/**
 * Бейдж критических алертов агента в шапке.
 * Тянет `/api/agent/alerts/summary`, показывает счётчик неподтверждённых.
 * По клику — модалка со списком последних и кнопкой «Ознакомился».
 */
import React, { useCallback, useEffect, useState } from 'react';
import { API_BASE } from '../config';
import { AlertOctagon, X } from './icons';

interface AlertRow {
  id: number;
  river: string;
  post: string;
  created_at: string;
  observation_date?: string;
  risk_score?: number;
  risk_class?: string;
  peak_level_cm?: number;
  peak_date?: string;
  narrative?: string;
  acknowledged?: number;
}

interface Summary {
  total: number;
  requires_ack: number;
  by_risk_class: Record<string, number>;
  by_station: Record<string, number>;
  latest: AlertRow[];
}

export function AgentAlertsBadge() {
  const [summary, setSummary] = useState<Summary | null>(null);
  const [open, setOpen] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);

  const reload = useCallback(() => {
    fetch(`${API_BASE}/agent/alerts/summary?only_pending=true&limit=50`)
      .then(r => r.ok ? r.json() : null)
      .then((j: Summary | null) => setSummary(j))
      .catch(() => setSummary(null));
  }, []);

  useEffect(() => {
    reload();
    const t = window.setInterval(reload, 60_000);
    return () => window.clearInterval(t);
  }, [reload]);

  const ack = (id: number) => {
    setBusyId(id);
    fetch(`${API_BASE}/agent/alerts/${id}/acknowledge`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ user: 'operator' }),
    })
      .then(r => r.ok ? r.json() : Promise.reject(new Error(r.statusText)))
      .then(() => reload())
      .catch(() => { /* reload актуализирует */ })
      .finally(() => setBusyId(null));
  };

  const pending = summary?.requires_ack ?? 0;

  return (
    <>
      <button type="button" onClick={() => setOpen(true)}
        title={pending ? `${pending} алерт(ов) агента ожидают подтверждения` : 'Алертов агента нет'}
        className={`relative inline-flex items-center gap-1.5 px-2.5 py-1.5 rounded-lg text-xs font-medium border transition ${
          pending
            ? 'bg-red-50 border-red-200 text-red-700 hover:bg-red-100'
            : 'bg-slate-50 border-slate-200 text-slate-500 hover:bg-slate-100'
        }`}>
        <AlertOctagon className="w-4 h-4" />
        Алерты агента
        {pending > 0 && (
          <span className="ml-1 inline-flex items-center justify-center min-w-[20px] h-5 rounded-full bg-red-600 text-white text-[11px] font-semibold px-1.5">
            {pending}
          </span>
        )}
      </button>
      {open && <AlertsModal summary={summary} busyId={busyId} onAck={ack} onClose={() => setOpen(false)} pending={pending} />}
    </>
  );
}

export default AgentAlertsBadge;

interface ModalProps {
  summary: Summary | null;
  busyId: number | null;
  onAck: (id: number) => void;
  onClose: () => void;
  pending: number;
}

function AlertsModal({ summary, busyId, onAck, onClose, pending }: ModalProps) {
  return (
    <div className="fixed inset-0 z-50 bg-slate-900/40 flex items-center justify-center p-4" onClick={onClose}>
      <div className="bg-white rounded-2xl shadow-xl w-full max-w-2xl max-h-[80vh] flex flex-col" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between p-5 border-b border-slate-200">
          <div>
            <div className="text-lg font-semibold text-slate-800">Алерты ИИ-агента</div>
            <div className="text-xs text-slate-500">
              Ожидают подтверждения: {pending}
              {summary && Object.keys(summary.by_risk_class).length > 0 && (
                <> · {Object.entries(summary.by_risk_class).map(([k, v]) => `${k}: ${v}`).join(', ')}</>
              )}
            </div>
          </div>
          <button onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100">
            <X className="w-5 h-5 text-slate-500" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto p-5 space-y-3">
          {(!summary || summary.latest.length === 0) && (
            <div className="text-sm text-slate-500 text-center py-8">Неподтверждённых алертов нет.</div>
          )}
          {summary?.latest.map(a => (
            <div key={a.id} className="border border-slate-200 rounded-xl p-4 space-y-2">
              <div className="flex items-center justify-between gap-3 flex-wrap">
                <div className="font-semibold text-slate-800">
                  {a.river} / {a.post}
                  <span className={`ml-2 text-xs px-2 py-0.5 rounded-full ${
                    a.risk_class === 'critical' ? 'bg-red-100 text-red-700' : 'bg-orange-100 text-orange-700'
                  }`}>
                    {a.risk_class} · {a.risk_score ? Math.round(a.risk_score) : '?'}/100
                  </span>
                </div>
                <div className="text-xs text-slate-500">#{a.id} · {a.created_at?.slice(0, 10)}</div>
              </div>
              {a.peak_level_cm != null && (
                <div className="text-xs text-slate-600">
                  Прогнозный пик: <b>{Math.round(a.peak_level_cm)} см</b>
                  {a.peak_date ? ` (${a.peak_date})` : ''}
                </div>
              )}
              {a.narrative && <div className="text-sm text-slate-600 leading-relaxed">{a.narrative}</div>}
              <div className="flex justify-end">
                <button onClick={() => onAck(a.id)} disabled={busyId === a.id}
                  className="px-3 py-1.5 rounded-lg text-sm font-medium bg-red-600 text-white hover:bg-red-700 disabled:opacity-50">
                  {busyId === a.id ? '…' : 'Ознакомился'}
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
