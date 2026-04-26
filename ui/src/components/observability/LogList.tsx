import React from 'react';
import { ScrollText } from 'lucide-react';
import type { RuntimeLogRecord } from '../../types/api';

interface LogListProps {
  logs: RuntimeLogRecord[];
  selectedKey: string | null;
  onSelect: (key: string, log: RuntimeLogRecord) => void;
}

const levelClass = (value?: string | null) => {
  const level = String(value || '').toUpperCase();
  if (level === 'ERROR') return 'text-rose-300 border-rose-500/20 bg-rose-500/10';
  if (level === 'WARNING') return 'text-amber-200 border-amber-500/20 bg-amber-500/10';
  return 'text-blue-200 border-blue-500/20 bg-blue-500/10';
};

const formatTimestamp = (value?: string | null) => {
  if (!value) return '-';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleTimeString();
};

const LogList: React.FC<LogListProps> = ({
  logs,
  selectedKey,
  onSelect,
}) => {
  const formatLogKey = (log: RuntimeLogRecord, index: number) => `${log.timestamp}-${index}`;

  return (
    <div className="flex flex-col h-full">
      <div className="flex items-center justify-between border-b border-slate-800/60 px-6 py-3">
        <div className="flex items-center gap-2">
          <ScrollText size={14} className="text-amber-300" />
          <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">
            Runtime Logs
          </span>
        </div>
        <span className="text-[10px] text-slate-500">{logs.length} entries</span>
      </div>

      <div className="flex-1 overflow-y-auto divide-y divide-slate-800/40">
        {logs.length === 0 ? (
          <div className="px-6 py-24 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600">
            No runtime logs
          </div>
        ) : (
          logs.map((log, index) => {
            const rowKey = formatLogKey(log, index);
            const active = rowKey === selectedKey;
            return (
              <button
                type="button"
                key={rowKey}
                onClick={() => onSelect(rowKey, log)}
                className={`w-full px-6 py-4 text-left transition-colors ${
                  active ? 'bg-amber-500/10' : 'hover:bg-white/[0.02]'
                }`}
              >
                <div className="mb-2 flex items-center justify-between gap-4">
                  <span className="truncate text-sm font-bold text-white">{log.message}</span>
                  <span className={`shrink-0 rounded-lg border px-2 py-1 text-[9px] font-black uppercase tracking-widest ${levelClass(log.level)}`}>
                    {log.level}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-2 text-[10px] font-mono text-slate-500">
                  <div>{log.component || log.logger}</div>
                  <div>{formatTimestamp(log.timestamp)}</div>
                  <div>{log.request_id || '-'}</div>
                  <div>{log.trace_id || '-'}</div>
                </div>
              </button>
            );
          })
        )}
      </div>
    </div>
  );
};

export default LogList;