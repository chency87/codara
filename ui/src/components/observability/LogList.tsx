import React from 'react';
import type { RuntimeLogRecord } from '../../types/api';

interface LogListProps {
  logs: RuntimeLogRecord[];
  isLoading: boolean;
  selectedLogKey: string | null;
  onSelectLog: (key: string) => void;
  formatTime: (value?: string | number | null) => string;
  levelClass: (value?: string | null) => string;
}

export const LogList: React.FC<LogListProps> = ({
  logs,
  isLoading,
  selectedLogKey,
  onSelectLog,
  formatTime,
  levelClass,
}) => {
  return (
    <div className="overflow-auto max-h-[calc(100vh-340px)]">
      <table className="w-full">
        <thead className="sticky top-0 z-10 bg-slate-800/90 backdrop-blur">
          <tr className="text-left text-[10px] font-bold uppercase tracking-wider text-slate-500">
            <th className="px-4 py-3">Timestamp</th>
            <th className="px-4 py-3">Level</th>
            <th className="px-4 py-3">Component</th>
            <th className="px-4 py-3">Message</th>
            <th className="px-4 py-3">Request ID</th>
          </tr>
        </thead>
        <tbody className="divide-y divide-slate-800">
          {isLoading ? (
            <tr>
              <td colSpan={5} className="px-4 py-12 text-center text-sm text-slate-500 animate-pulse">
                Loading logs...
              </td>
            </tr>
          ) : logs.length === 0 ? (
            <tr>
              <td colSpan={5} className="px-4 py-12 text-center text-sm text-slate-500">
                No logs found
              </td>
            </tr>
          ) : (
            logs.map((row, idx) => {
              const key = `${row.timestamp}-${idx}`;
              return (
                <tr
                  key={key}
                  onClick={() => onSelectLog(key)}
                  className={`cursor-pointer transition-colors ${
                    selectedLogKey === key
                      ? 'bg-blue-500/10'
                      : 'hover:bg-white/5'
                  }`}
                >
                  <td className="px-4 py-3 text-sm text-slate-400">
                    {formatTime(row.timestamp)}
                  </td>
                  <td className="px-4 py-3">
                    <span className={`inline-flex rounded px-2 py-1 text-[10px] font-bold uppercase ${levelClass(row.level)}`}>
                      {row.level || 'INFO'}
                    </span>
                  </td>
                  <td className="px-4 py-3 text-sm text-slate-400">
                    {row.component || row.logger}
                  </td>
                  <td className="px-4 py-3 text-sm font-medium text-white max-w-[400px] truncate">
                    {row.message}
                  </td>
                  <td className="px-4 py-3 text-sm font-mono text-slate-500 max-w-[120px] truncate">
                    {row.request_id || '—'}
                  </td>
                </tr>
              );
            })
          )}
        </tbody>
      </table>
    </div>
  );
};
