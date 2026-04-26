import React from 'react';
import { Search, Clock3 } from 'lucide-react';

interface ObservabilityFilterBarProps {
  search: string;
  onSearchChange: (value: string) => void;
  selectedTraceId: string | null;
  onSelectTrace: (id: string | null) => void;
  tab: 'traces' | 'logs';
}

const ObservabilityFilterBar: React.FC<ObservabilityFilterBarProps> = ({
  search,
  onSearchChange,
  selectedTraceId,
  onSelectTrace,
  tab,
}) => {
  const showTraceSelector = tab === 'traces';

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Search</span>
          <div className="relative">
            <Search size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              className="w-full rounded-xl border border-slate-800 bg-black py-3 pl-10 pr-4 text-sm text-white outline-none focus:border-blue-500"
              placeholder="trace name, message, request id..."
              value={search}
              onChange={(e) => onSearchChange(e.target.value)}
            />
          </div>
        </label>

        {showTraceSelector && (
          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Filter by Trace ID</span>
            <div className="relative">
              <Clock3 size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                className="w-full rounded-xl border border-slate-800 bg-black py-3 pl-10 pr-4 text-sm text-white outline-none focus:border-blue-500"
                placeholder="Filter by trace ID..."
                value={selectedTraceId || ''}
                onChange={(e) => onSelectTrace(e.target.value || null)}
              />
            </div>
          </label>
        )}
      </div>
    </div>
  );
};

export default ObservabilityFilterBar;