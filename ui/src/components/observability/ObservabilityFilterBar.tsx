import React from 'react';

interface FilterBarProps {
  tab: 'traces' | 'logs';
  search: string;
  onSearchChange: (value: string) => void;
  status: string;
  onStatusChange: (value: string) => void;
  level: string;
  onLevelChange: (value: string) => void;
  since: string;
  onSinceChange: (value: string) => void;
  until: string;
  onUntilChange: (value: string) => void;
  onQuickRange: (hours: number) => void;
  onClear: () => void;
}

export const ObservabilityFilterBar: React.FC<FilterBarProps> = ({
  tab,
  search,
  onSearchChange,
  status,
  onStatusChange,
  level,
  onLevelChange,
  since,
  onSinceChange,
  until,
  onUntilChange,
  onQuickRange,
  onClear,
}) => {
  return (
    <div className="mb-4 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
      <div className="flex flex-col gap-4 lg:flex-row lg:items-center lg:flex-wrap">
        <div className="flex-1 min-w-[200px]">
          <input
            className="w-full rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-blue-500"
            placeholder={tab === 'traces' ? "Search traces..." : "Search logs..."}
            value={search}
            onChange={(e) => onSearchChange(e.target.value)}
          />
        </div>
        
        <div className="flex flex-col sm:flex-row gap-4 items-stretch sm:items-center">
          {tab === 'traces' ? (
            <select
              className="rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
              value={status}
              onChange={(e) => onStatusChange(e.target.value)}
            >
              <option value="">All statuses</option>
              <option value="ok">Success</option>
              <option value="error">Failed</option>
            </select>
          ) : (
            <select
              className="rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
              value={level}
              onChange={(e) => onLevelChange(e.target.value)}
            >
              <option value="">All levels</option>
              <option value="DEBUG">DEBUG</option>
              <option value="INFO">INFO</option>
              <option value="WARNING">WARNING</option>
              <option value="ERROR">ERROR</option>
            </select>
          )}

          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-slate-500 uppercase shrink-0">Since</span>
            <input
              type="datetime-local"
              className="flex-1 rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
              value={since}
              onChange={(e) => onSinceChange(e.target.value)}
            />
          </div>

          <div className="flex items-center gap-2">
            <span className="text-[10px] font-bold text-slate-500 uppercase shrink-0">Until</span>
            <input
              type="datetime-local"
              className="flex-1 rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
              value={until}
              onChange={(e) => onUntilChange(e.target.value)}
            />
          </div>

          <div className="flex gap-1 overflow-x-auto custom-scrollbar pb-1 sm:pb-0">
            {[
              ['1h', 1],
              ['6h', 6],
              ['24h', 24],
            ].map(([label, hours]) => (
              <button
                key={label}
                type="button"
                onClick={() => onQuickRange(Number(hours))}
                className="rounded-lg border border-slate-700 bg-black/50 px-2 py-2 text-xs font-medium text-slate-400 hover:border-blue-500/40 hover:text-blue-200"
              >
                {label}
              </button>
            ))}
            <button
              type="button"
              onClick={onClear}
              className="rounded-lg border border-slate-700 bg-slate-800 px-2 py-2 text-xs font-medium text-slate-500 hover:text-white"
            >
              Clear
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};
