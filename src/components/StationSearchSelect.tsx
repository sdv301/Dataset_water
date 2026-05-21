import React, { useState, useRef, useEffect, useMemo } from 'react';
import { Search, ChevronDown, CheckCircle2, Circle } from 'lucide-react';

export interface StationOption {
  label: string;
  river: string;
  post: string;
  has_model?: boolean;
}

export function StationSearchSelect({
  stations,
  value,
  onChange,
}: {
  stations: StationOption[];
  value: string;
  onChange: (label: string) => void;
}) {
  const [query, setQuery] = useState('');
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLDivElement>(null);

  const selected = stations.find(s => s.label === value);

  useEffect(() => {
    if (selected) setQuery(selected.label);
  }, [value, selected?.label]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return stations.slice(0, 80);
    return stations
      .filter(
        s =>
          s.label.toLowerCase().includes(q) ||
          s.river.toLowerCase().includes(q) ||
          s.post.toLowerCase().includes(q)
      )
      .slice(0, 80);
  }, [stations, query]);

  useEffect(() => {
    const onDoc = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
        if (selected) setQuery(selected.label);
      }
    };
    document.addEventListener('mousedown', onDoc);
    return () => document.removeEventListener('mousedown', onDoc);
  }, [selected]);

  return (
    <div ref={wrapRef} className="relative">
      <div className="relative">
        <Search className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
        <input
          type="text"
          value={query}
          onChange={e => {
            setQuery(e.target.value);
            setOpen(true);
          }}
          onFocus={() => setOpen(true)}
          placeholder="Река или пост…"
          className="w-full bg-slate-50 border border-slate-200 text-slate-700 rounded-lg pl-9 pr-9 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500 focus:border-transparent"
        />
        <ChevronDown className="absolute right-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-400 pointer-events-none" />
      </div>
      {open && filtered.length > 0 && (
        <ul className="absolute z-50 mt-1 w-full max-h-56 overflow-y-auto bg-white border border-slate-200 rounded-lg shadow-lg text-sm">
          {filtered.map(s => (
            <li key={s.label}>
              <button
                type="button"
                className={`w-full text-left px-3 py-2 hover:bg-blue-50 flex items-center justify-between gap-2 ${
                  s.label === value ? 'bg-blue-50 text-blue-800' : 'text-slate-700'
                }`}
                onClick={() => {
                  onChange(s.label);
                  setQuery(s.label);
                  setOpen(false);
                }}
              >
                <span className="truncate">{s.label}</span>
                {s.has_model ? (
                  <CheckCircle2 className="w-3.5 h-3.5 text-emerald-600 shrink-0" title="Модель обучена" />
                ) : (
                  <Circle className="w-3.5 h-3.5 text-slate-300 shrink-0" title="Нет модели" />
                )}
              </button>
            </li>
          ))}
        </ul>
      )}
      {open && query && filtered.length === 0 && (
        <div className="absolute z-50 mt-1 w-full bg-white border border-slate-200 rounded-lg shadow-lg px-3 py-2 text-xs text-slate-500">
          Ничего не найдено
        </div>
      )}
    </div>
  );
}
