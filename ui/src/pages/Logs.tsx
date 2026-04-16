import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Search, ScrollText, AlertCircle, ChevronDown, ChevronUp, Fingerprint, Bug } from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import type { ApiEnvelope, RuntimeLogRecord } from '../types/api';

const PAGE_SIZE = 50;

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

const LogRow = ({ row }: { row: RuntimeLogRecord }) => {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = Boolean(row.attributes || row.exception || row.trace_id || row.request_id);
  const color =
    row.level === 'ERROR' ? 'text-rose-400 border-rose-500/20 bg-rose-500/10' :
    row.level === 'WARNING' ? 'text-amber-400 border-amber-500/20 bg-amber-500/10' :
    'text-blue-300 border-blue-500/20 bg-blue-500/10';

  return (
    <div className="border-b border-slate-800/40 last:border-0">
      <div className="px-8 py-5 flex items-center justify-between cursor-pointer hover:bg-white/[0.01]" onClick={() => hasDetails && setExpanded(!expanded)}>
        <div className="flex items-center space-x-5 overflow-hidden">
          <div className="text-[10px] font-black text-slate-600 tabular-nums uppercase w-36 shrink-0">
            {new Date(row.timestamp).toLocaleString()}
          </div>
          <div className={`px-2 py-1 rounded-lg border text-[10px] font-black uppercase tracking-widest w-20 text-center shrink-0 ${color}`}>
            {row.level}
          </div>
          <div className="w-48 shrink-0 text-xs font-bold text-slate-300 truncate">{row.component || row.logger}</div>
          <div className="min-w-0 text-sm text-slate-200 truncate">{row.message}</div>
        </div>
        {hasDetails ? (expanded ? <ChevronUp size={16} className="text-slate-600" /> : <ChevronDown size={16} className="text-slate-600" />) : null}
      </div>
      {expanded && (
        <div className="px-8 pb-8 grid grid-cols-1 lg:grid-cols-2 gap-6">
          <div className="space-y-3">
            <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-widest">Context</h4>
            <div className="rounded-2xl border border-slate-800 bg-black/50 p-4 text-[10px] font-mono text-slate-400 space-y-2">
              <div>logger: {row.logger}</div>
              <div>event_name: {row.event_name || '-'}</div>
              <div>trace_id: {row.trace_id || '-'}</div>
              <div>request_id: {row.request_id || '-'}</div>
              <div>span_id: {row.span_id || '-'}</div>
            </div>
          </div>
          <div className="space-y-3">
            <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-widest">Attributes</h4>
            <pre className="rounded-2xl border border-slate-800 bg-black/50 p-4 text-[10px] font-mono text-slate-400 overflow-auto max-h-80">
              {row.exception || JSON.stringify(row.attributes || {}, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};

const Logs = () => {
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [level, setLevel] = useState('');
  const [component, setComponent] = useState('');
  const [requestId, setRequestId] = useState('');
  const [traceId, setTraceId] = useState('');
  const [search, setSearch] = useState('');

  const resetPagination = () => {
    setCursor(null);
    setCursorHistory([]);
  };

  const { data, isLoading, error } = useQuery<ApiEnvelope<RuntimeLogRecord[]>>({
    queryKey: ['runtime-logs', cursor, level, component, requestId, traceId, search],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/logs', {
        params: {
          limit: PAGE_SIZE,
          after: cursor || undefined,
          level: level || undefined,
          component: component.trim() || undefined,
          request_id: requestId.trim() || undefined,
          trace_id: traceId.trim() || undefined,
          search: search.trim() || undefined,
        },
      });
      return resp.data;
    }
  });

  const rows = useMemo(() => data?.data || [], [data]);
  const pageMeta = data?.meta?.page;
  const canGoBack = cursorHistory.length > 0;
  const canGoNext = rows.length === PAGE_SIZE && Boolean(pageMeta?.cursor);

  const goToNextPage = () => {
    if (!pageMeta?.cursor || !canGoNext) return;
    setCursorHistory(prev => [...prev, cursor]);
    setCursor(String(pageMeta.cursor));
  };

  const goToPreviousPage = () => {
    if (!canGoBack) return;
    const previousCursor = cursorHistory[cursorHistory.length - 1] ?? null;
    setCursorHistory(prev => prev.slice(0, -1));
    setCursor(previousCursor);
  };

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-12">
        <h2 className="text-4xl font-black tracking-tight text-white mb-2">Runtime Logs</h2>
        <p className="text-slate-500 font-medium">Structured JSONL runtime logs stored in datetime shards.</p>
      </header>

      {error && (
        <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200 flex items-center space-x-3">
          <AlertCircle size={18} />
          <span>Runtime log query failed: {getErrorMessage(error)}</span>
        </div>
      )}

      <div className="mb-6 grid grid-cols-1 gap-4 rounded-3xl border border-slate-800/60 bg-slate-900/40 p-6 md:grid-cols-3 xl:grid-cols-6">
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Search</span>
          <div className="relative">
            <Search size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
            <input className="w-full rounded-xl border border-slate-800 bg-black py-3 pl-10 pr-4 text-sm text-white outline-none focus:border-blue-500" value={search} onChange={(e) => { resetPagination(); setSearch(e.target.value); }} />
          </div>
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Level</span>
          <select className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500" value={level} onChange={(e) => { resetPagination(); setLevel(e.target.value); }}>
            <option value="">All levels</option>
            <option value="INFO">INFO</option>
            <option value="WARNING">WARNING</option>
            <option value="ERROR">ERROR</option>
          </select>
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Component</span>
          <input className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500" value={component} onChange={(e) => { resetPagination(); setComponent(e.target.value); }} />
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Request ID</span>
          <input className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500" value={requestId} onChange={(e) => { resetPagination(); setRequestId(e.target.value); }} />
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Trace ID</span>
          <div className="relative">
            <Fingerprint size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
            <input className="w-full rounded-xl border border-slate-800 bg-black py-3 pl-10 pr-4 text-sm text-white outline-none focus:border-blue-500" value={traceId} onChange={(e) => { resetPagination(); setTraceId(e.target.value); }} />
          </div>
        </label>
        <div className="rounded-2xl border border-slate-800 bg-black/40 px-4 py-3 flex items-center gap-3">
          <ScrollText size={16} className="text-blue-400" />
          <div>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Visible Logs</div>
            <div className="text-xl font-black text-white">{rows.length}</div>
          </div>
        </div>
      </div>

      <div className="bg-slate-900/40 backdrop-blur-md border border-slate-800/60 rounded-3xl overflow-hidden shadow-2xl">
        <div className="bg-slate-800/30 border-b border-slate-800/60 px-8 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <Bug size={18} className="text-blue-500" />
            <span className="text-xs font-black text-slate-400 uppercase tracking-widest">Runtime Event Stream</span>
          </div>
        </div>
        <div className="divide-y divide-slate-800/40">
          {isLoading ? (
            <div className="px-8 py-24 text-center text-slate-600 font-bold uppercase tracking-widest text-xs animate-pulse">
              Reading Datetime Shards...
            </div>
          ) : rows.length === 0 ? (
            <div className="px-8 py-24 text-center text-slate-600 font-bold uppercase tracking-widest text-xs">
              No logs found
            </div>
          ) : (
            rows.map((row, index) => <LogRow key={`${row.timestamp}-${index}`} row={row} />)
          )}
        </div>
        <CursorPagination
          countLabel={`${rows.length} logs in view`}
          pageLabel={rows.length ? `latest ${rows[0].level} records` : 'no records'}
          canGoBack={canGoBack}
          canGoNext={canGoNext}
          onBack={goToPreviousPage}
          onNext={goToNextPage}
        />
      </div>
    </div>
  );
};

export default Logs;
