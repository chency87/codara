import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Search, RefreshCw } from 'lucide-react';
import type { ApiEnvelope, AuditLogRecord } from '../types/api';

const PAGE_SIZE = 25;

const ACTION_COLORS: Record<string, string> = {
  'created': 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20',
  'updated': 'bg-blue-500/10 text-blue-400 border-blue-500/20',
  'deleted': 'bg-rose-500/10 text-rose-400 border-rose-500/20',
  'removed': 'bg-rose-500/10 text-rose-400 border-rose-500/20',
  'terminated': 'bg-rose-500/10 text-rose-400 border-rose-500/20',
  'suspended': 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  'failed': 'bg-rose-500/10 text-rose-400 border-rose-500/20',
  'rotated': 'bg-amber-500/10 text-amber-400 border-amber-500/20',
  'reset': 'bg-amber-500/10 text-amber-400 border-amber-500/20',
};

const actionColor = (action: string) => {
  const key = Object.keys(ACTION_COLORS).find(k => action.toLowerCase().includes(k));
  return key ? ACTION_COLORS[key] : 'bg-slate-800 text-slate-400 border-slate-700';
};

const formatTime = (ts: number) => {
  const d = new Date(ts * 1000);
  return d.toLocaleString('en-US', { 
    month: 'short', day: 'numeric', 
    hour: '2-digit', minute: '2-digit', second: '2-digit',
    hour12: false 
  });
};

const ActorBadge = ({ actor }: { actor: string }) => {
  if (actor.startsWith('operator:')) {
    return <span className="text-blue-400">operator</span>;
  }
  if (actor.startsWith('system:')) {
    return <span className="text-amber-400">system</span>;
  }
  if (actor.startsWith('user:')) {
    return <span className="text-emerald-400">user</span>;
  }
  return <span className="text-slate-400">{actor}</span>;
};

const AuditLog = () => {
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<string[]>([]);
  const [search, setSearch] = useState('');
  const [refreshKey, setRefreshKey] = useState(0);

  const { data, isLoading, error, refetch } = useQuery<ApiEnvelope<AuditLogRecord[]>>({
    queryKey: ['audit-logs', cursor, search, refreshKey],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/audit', {
        params: {
          limit: PAGE_SIZE,
          after: cursor || undefined,
          search: search.trim() || undefined,
        },
      });
      return resp.data;
    },
  });

  const logs = data?.data || [];
  const pageMeta = data?.meta?.page;
  const canGoBack = cursorHistory.length > 0;
  const canGoNext = logs.length === PAGE_SIZE && Boolean(pageMeta?.cursor);

  const goToNextPage = () => {
    if (!pageMeta?.cursor || !canGoNext) return;
    setCursorHistory(prev => [...prev, cursor]);
    setCursor(pageMeta.cursor);
  };

  const goToPreviousPage = () => {
    if (!canGoBack) return;
    const previousCursor = cursorHistory[cursorHistory.length - 1] ?? null;
    setCursorHistory(prev => prev.slice(0, -1));
    setCursor(previousCursor);
  };

  const handleRefresh = () => {
    setCursor(null);
    setCursorHistory([]);
    setRefreshKey(prev => prev + 1);
  };

  const handleSearch = (value: string) => {
    setCursor(null);
    setCursorHistory([]);
    setSearch(value);
  };

  if (error) {
    return (
      <div className="p-12">
        <div className="rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          Failed to load audit logs
        </div>
      </div>
    );
  }

  return (
    <div className="p-6 sm:p-8 lg:p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-8">
        <h2 className="text-3xl sm:text-4xl font-black tracking-tight text-white mb-2">Audit Log</h2>
        <p className="text-slate-500 font-medium">Track all management actions and system changes.</p>
      </header>

      <div className="mb-6 flex flex-col sm:flex-row sm:items-center gap-4">
        <div className="relative flex-1 sm:max-w-sm">
          <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
          <input
            type="text"
            value={search}
            onChange={(e) => handleSearch(e.target.value)}
            placeholder="Search logs..."
            className="w-full pl-9 pr-4 py-2.5 bg-slate-900/60 border border-slate-800 rounded-xl text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500/50"
          />
        </div>
        <button
          type="button"
          onClick={handleRefresh}
          disabled={isLoading}
          className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-700 bg-slate-800/50 px-4 py-2.5 text-sm text-slate-300 hover:text-white hover:border-slate-600 transition-colors disabled:opacity-50"
        >
          <RefreshCw size={16} className={isLoading ? 'animate-spin' : ''} />
          Refresh
        </button>
        <div className="text-sm text-slate-500 sm:ml-auto">
          {pageMeta?.total_count ?? logs.length} entries
        </div>
      </div>

      <div className="bg-slate-900/40 border border-slate-800/60 rounded-3xl overflow-hidden">
        <div className="overflow-x-auto custom-scrollbar">
          <div className="min-w-[800px]">
            <div className="grid grid-cols-12 gap-4 px-6 py-3 bg-slate-800/30 border-b border-slate-800/60 text-[10px] font-bold text-slate-500 uppercase tracking-widest">
              <div className="col-span-2">Time</div>
              <div className="col-span-2">Actor</div>
              <div className="col-span-2">Action</div>
              <div className="col-span-4">Target</div>
              <div className="col-span-2 text-right">Changes</div>
            </div>

            <div className="divide-y divide-slate-800/40 max-h-[600px] overflow-y-auto">
              {isLoading ? (
                <div className="px-6 py-12 text-center text-slate-600 font-bold uppercase tracking-widest text-xs animate-pulse">
                  Loading...
                </div>
              ) : logs.length === 0 ? (
                <div className="px-6 py-12 text-center text-slate-600 font-bold uppercase tracking-widest text-xs">
                  No audit entries
                </div>
              ) : (
                logs.map((log) => (
                  <div 
                    key={log.audit_id}
                    className="grid grid-cols-12 gap-4 px-6 py-4 items-center hover:bg-white/[0.02] cursor-pointer transition-colors"
                  >
                    <div className="col-span-2 text-xs font-mono text-slate-400">
                      {formatTime(log.timestamp)}
                    </div>
                    <div className="col-span-2">
                      <ActorBadge actor={log.actor} />
                    </div>
                    <div className="col-span-2">
                      <span className={`inline-flex px-2 py-1 rounded-lg text-[10px] font-semibold uppercase border ${actionColor(log.action)}`}>
                        {log.action}
                      </span>
                    </div>
                    <div className="col-span-4">
                      <span className="text-xs text-slate-400 font-mono">
                        {log.target_type}:{log.target_id}
                      </span>
                    </div>
                    <div className="col-span-2 text-right">
                      <span className="text-[10px] text-slate-500">
                        {log.before_state ? 'state changed' : 'new'}
                      </span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        <div className="px-6 py-4 border-t border-slate-800/60 flex items-center justify-between">
          <div className="text-sm text-slate-500">
            {pageMeta?.total_count ?? logs.length} entries
          </div>
          <div className="flex items-center gap-2">
            <button
              type="button"
              onClick={goToPreviousPage}
              disabled={!canGoBack}
              className="rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-1.5 text-xs text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Previous
            </button>
            <span className="text-xs text-slate-500 px-2">
              {canGoBack ? cursorHistory.length + 1 : 1}
            </span>
            <button
              type="button"
              onClick={goToNextPage}
              disabled={!canGoNext}
              className="rounded-lg border border-slate-700 bg-slate-800/50 px-3 py-1.5 text-xs text-slate-400 hover:text-white disabled:opacity-30 disabled:cursor-not-allowed"
            >
              Next
            </button>
          </div>
        </div>
      </div>
    </div>
  );
};

export default AuditLog;