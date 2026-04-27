import React from 'react';
import type { TraceRecord } from '../../types/api';

interface TraceListProps {
  traces: TraceRecord[];
  isLoading: boolean;
  selectedTraceId: string | null;
  onSelectTrace: (id: string) => void;
  formatTime: (value?: string | number | null) => string;
  statusClass: (value?: string | null) => string;
}

export const TraceList: React.FC<TraceListProps> = ({
  traces,
  isLoading,
  selectedTraceId,
  onSelectTrace,
  formatTime,
  statusClass,
}) => {
  return (
    <div className="overflow-auto max-h-[calc(100vh-340px)]">
      <table className="w-full">
        <thead className="sticky top-0 z-10 bg-slate-800/90 backdrop-blur">
          <tr className="text-left text-[10px] font-bold uppercase tracking-wider text-slate-500">
            <th className="px-4 py-3">ID</th>
            <th className="px-4 py-3">Name</th>
            <th className="px-4 py-3">Status</th>
            <th className="px-4 py-3">Started</th>
            <th className="px-4 py-3">Duration</th>
            <th className="px-4 py-3">Component</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {isLoading ? (
            <tr>
              <td colSpan={6} className="px-4 py-12 text-center text-sm text-slate-500 animate-pulse">
                Loading traces...
              </td>
            </tr>
          ) : traces.length === 0 ? (
            <tr>
              <td colSpan={6} className="px-4 py-12 text-center text-sm text-slate-500">
                No traces found
              </td>
            </tr>
          ) : (
            traces.map((row) => (
              <tr
                key={row.trace_id}
                onClick={() => onSelectTrace(row.trace_id)}
                className={`cursor-pointer transition-colors ${
                  selectedTraceId === row.trace_id
                    ? 'bg-blue-500/10'
                    : 'hover:bg-white/5'
                }`}
              >
                <td className="px-4 py-3 text-sm font-mono text-slate-400 max-w-[120px] truncate">
                  {row.trace_id}
                </td>
                <td className="px-4 py-3 text-sm font-medium text-white max-w-[200px] truncate">
                  {row.name}
                </td>
                <td className="px-4 py-3">
                  <span className={`inline-flex rounded px-2 py-1 text-[10px] font-bold uppercase ${statusClass(row.status)}`}>
                    {row.status || 'ok'}
                  </span>
                </td>
                <td className="px-4 py-3 text-sm text-slate-400">
                  {formatTime(row.started_at)}
                </td>
                <td className="px-4 py-3 text-sm text-slate-400">
                  {row.duration_ms ? `${row.duration_ms}ms` : '—'}
                </td>
                <td className="px-4 py-3 text-sm text-slate-400">
                  {row.component}
                </td>
              </tr>
            ))
          )}
        </tbody>
      </table>
    </div>
  );
};
