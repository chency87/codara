import React from 'react';
import type { TraceRecord } from '../../types/api';

interface TraceListProps {
  traces: TraceRecord[];
  selectedTraceId: string | null;
  onSelect: (traceId: string) => void;
  isLoading: boolean;
}

const statusClass = (value?: string | null) => {
  return value === 'error' ? 'text-rose-300' : 'text-emerald-300';
};

const formatTimestamp = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return '-';
  const date = typeof value === 'number' ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleTimeString();
};

const TraceList: React.FC<TraceListProps> = ({
  traces,
  selectedTraceId,
  onSelect,
  isLoading,
}) => {
  if (isLoading) {
    return (
      <div className="p-6 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600 animate-pulse">
        Loading...
      </div>
    );
  }

  if (traces.length === 0) {
    return (
      <div className="p-6 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600">
        No traces
      </div>
    );
  }

  return (
    <div className="flex-1 overflow-auto custom-scrollbar">
      <table className="w-full text-left text-xs">
        <thead className="sticky top-0 bg-slate-900 z-10 shadow-sm">
          <tr className="text-[10px] font-black uppercase tracking-widest text-slate-500 border-b border-slate-800">
            <th className="px-4 py-3 w-20">Status</th>
            <th className="px-4 py-3">Name</th>
            <th className="px-4 py-3 w-28">Component</th>
            <th className="px-4 py-3 w-24">Started</th>
            <th className="px-4 py-3 w-20">Duration</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800/40">
          {traces.map((row) => {
            const active = row.trace_id === selectedTraceId;
            return (
              <tr
                key={row.trace_id}
                onClick={() => onSelect(row.trace_id)}
                className={`cursor-pointer transition-colors ${
                  active ? 'bg-blue-500/10' : 'hover:bg-white/[0.02]'
                }`}
              >
                <td className="px-4 py-2">
                  <span className={`text-[10px] font-bold uppercase ${statusClass(row.status)}`}>
                    {row.status || 'ok'}
                  </span>
                </td>
                <td className="px-4 py-2 text-white font-medium truncate max-w-[200px]">
                  {row.name}
                </td>
                <td className="px-4 py-2 text-slate-400 font-mono truncate">
                  {row.component}
                </td>
                <td className="px-4 py-2 text-slate-400 font-mono">
                  {formatTimestamp(row.started_at)}
                </td>
                <td className="px-4 py-2 text-slate-400 font-mono">
                  {row.duration_ms ? `${row.duration_ms} ms` : '-'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
};

export default TraceList;