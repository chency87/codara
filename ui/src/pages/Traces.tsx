import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Activity, Search, Fingerprint, ChevronDown, ChevronUp, Code, Cpu, AlertCircle, Clock } from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import type { ApiEnvelope, TraceRecord, TraceEvent } from '../types/api';

const PAGE_SIZE = 25;

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

const formatAttributes = (attributes?: Record<string, any> | null) => {
  if (!attributes) return '// No attributes recorded';
  return JSON.stringify(attributes, null, 2);
};

const TraceEventRow = ({ event }: { event: TraceEvent }) => {
  const isSpan = event.kind === 'span';
  return (
    <div className="flex items-start space-x-3 py-2 border-b border-slate-800/20 last:border-0">
      <div className={`mt-1 h-2 w-2 rounded-full shrink-0 ${
        event.level === 'ERROR' ? 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.4)]' : 
        event.level === 'WARNING' ? 'bg-amber-500' : 'bg-blue-500'
      }`} />
      <div className="flex-1 min-w-0">
        <div className="flex items-center justify-between mb-1">
          <span className={`text-[10px] font-black uppercase tracking-tight ${isSpan ? 'text-blue-400' : 'text-slate-300'}`}>
            {event.name}
          </span>
          <span className="text-[9px] font-mono text-slate-500">
            {new Date(event.started_at).toLocaleTimeString()} 
            {event.duration_ms ? ` • ${event.duration_ms}ms` : ''}
          </span>
        </div>
        {event.attributes && Object.keys(event.attributes).length > 0 && (
          <pre className="text-[9px] font-mono text-slate-500 bg-black/40 p-2 rounded-lg overflow-x-auto max-w-full">
            {formatAttributes(event.attributes)}
          </pre>
        )}
      </div>
    </div>
  );
};

const TraceRow = ({ trace }: { trace: TraceRecord }) => {
  const [expanded, setExpanded] = useState(false);
  const { data: detailData, isLoading: detailLoading } = useQuery<ApiEnvelope<{ events: TraceEvent[] }>>({
    queryKey: ['trace-detail', trace.trace_id],
    queryFn: async () => (await axios.get(`/management/v1/traces/${trace.trace_id}`)).data,
    enabled: expanded,
  });

  const events = detailData?.data?.events || [];

  return (
    <div className="border-b border-slate-800/40 last:border-0 group">
      <div 
        className="px-8 py-6 flex items-center justify-between cursor-pointer hover:bg-white/[0.01] transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center space-x-6 overflow-hidden">
          <div className="text-[10px] font-black text-slate-600 tabular-nums uppercase w-32 shrink-0">
            {new Date(trace.started_at).toLocaleString()}
          </div>
          <div className="flex items-center space-x-2 w-48 shrink-0">
            <Cpu size={12} className="text-slate-500" />
            <span className="text-xs font-bold text-slate-300 truncate">{trace.component}</span>
          </div>
          <div className="w-64 shrink-0 overflow-hidden">
            <span className={`text-xs font-bold text-slate-100 truncate block`}>
              {trace.name}
            </span>
          </div>
          <div className="w-24 shrink-0">
            <span className={`px-2 py-0.5 rounded-lg text-[9px] font-black uppercase tracking-widest border ${
              trace.status === 'error' || trace.level === 'ERROR'
                ? 'bg-rose-500/10 text-rose-500 border-rose-500/20 shadow-[0_0_10px_rgba(244,63,94,0.1)]' 
                : 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20'
            }`}>
              {trace.status || trace.level || 'OK'}
            </span>
          </div>
          <div className="flex items-center space-x-2 text-slate-500 w-32 shrink-0">
            <Clock size={12} />
            <span className="text-[10px] font-mono tabular-nums">{trace.duration_ms ? `${trace.duration_ms}ms` : '-'}</span>
          </div>
          <div className="flex items-center space-x-2 text-slate-500 hidden xl:flex">
            <Fingerprint size={12} />
            <span className="text-[10px] font-mono tabular-nums">{trace.trace_id.split('_')[1]}</span>
          </div>
        </div>
        <div className="flex items-center space-x-4">
          {trace.request_id && (
            <span className="text-[9px] font-mono text-slate-600 uppercase tracking-tighter hidden 2xl:block">
              REQ: {trace.request_id.split('_')[1]}
            </span>
          )}
          {expanded ? <ChevronUp size={16} className="text-slate-600" /> : <ChevronDown size={16} className="text-slate-600" />}
        </div>
      </div>

      {expanded && (
        <div className="px-8 pb-8 pt-2 animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="grid grid-cols-1 lg:grid-cols-3 gap-8">
            <div className="lg:col-span-2 space-y-4">
              <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-widest flex items-center space-x-2">
                <Activity size={12} className="text-blue-500" />
                <span>Trace Events & Spans</span>
              </h4>
              <div className="bg-black/40 border border-slate-800/60 rounded-2xl p-4 overflow-hidden">
                {detailLoading ? (
                  <div className="py-12 text-center text-[10px] font-bold text-slate-600 uppercase tracking-widest animate-pulse">
                    Collecting Trace Data...
                  </div>
                ) : events.length === 0 ? (
                  <div className="py-12 text-center text-[10px] font-bold text-slate-600 uppercase tracking-widest">
                    No individual events recorded for this trace
                  </div>
                ) : (
                  <div className="space-y-1">
                    {events.map((event) => (
                      <TraceEventRow key={event.event_id} event={event} />
                    ))}
                  </div>
                )}
              </div>
            </div>
            <div className="space-y-4">
              <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-widest flex items-center space-x-2">
                <Code size={12} />
                <span>Root Attributes</span>
              </h4>
              <pre className="bg-black/60 border border-slate-800 rounded-2xl p-4 text-[10px] font-mono text-slate-400 overflow-auto max-h-[400px]">
                {formatAttributes(trace.attributes)}
              </pre>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

const Traces = () => {
  const [cursor, setCursor] = useState<number | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<number | null>>([]);
  const [component, setComponent] = useState('');
  const [status, setStatus] = useState('');
  const [requestId, setRequestId] = useState('');

  const resetPagination = () => {
    setCursor(null);
    setCursorHistory([]);
  };

  const { data, isLoading, error } = useQuery<ApiEnvelope<TraceRecord[]>>({
    queryKey: ['traces', cursor, component, status, requestId],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/traces', {
        params: {
          limit: PAGE_SIZE,
          after: cursor || undefined,
          component: component.trim() || undefined,
          status: status || undefined,
          request_id: requestId.trim() || undefined,
        },
      });
      return resp.data;
    }
  });

  const traces = useMemo(() => data?.data || [], [data]);
  const pageMeta = data?.meta?.page;
  
  const canGoBack = cursorHistory.length > 0;
  const canGoNext = traces.length === PAGE_SIZE && Boolean(pageMeta?.cursor);

  const goToNextPage = () => {
    if (!pageMeta?.cursor || !canGoNext) return;
    setCursorHistory(prev => [...prev, cursor]);
    setCursor(Number(pageMeta.cursor));
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
        <h2 className="text-4xl font-black tracking-tight text-white mb-2">Observability Traces</h2>
        <p className="text-slate-500 font-medium">Live inspection of cross-component spans and structured telemetry events.</p>
      </header>

      {error && (
        <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200 flex items-center space-x-3">
          <AlertCircle size={18} />
          <span>Trace synchronization failed: {getErrorMessage(error)}</span>
        </div>
      )}

      <div className="mb-6 grid grid-cols-1 gap-4 rounded-3xl border border-slate-800/60 bg-slate-900/40 p-6 md:grid-cols-3">
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Component</span>
          <div className="relative">
            <Search size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              className="w-full rounded-xl border border-slate-800 bg-black py-3 pl-10 pr-4 text-sm text-white outline-none focus:border-blue-500"
              placeholder="gateway.http, orchestrator.engine..."
              value={component}
              onChange={(e) => {
                resetPagination();
                setComponent(e.target.value);
              }}
            />
          </div>
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Request ID</span>
          <input
            className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
            placeholder="req_..."
            value={requestId}
            onChange={(e) => {
              resetPagination();
              setRequestId(e.target.value);
            }}
          />
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Status</span>
          <select
            className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
            value={status}
            onChange={(e) => {
              resetPagination();
              setStatus(e.target.value);
            }}
          >
            <option value="">All statuses</option>
            <option value="ok">Success (ok)</option>
            <option value="error">Failure (error)</option>
          </select>
        </label>
      </div>

      <div className="bg-slate-900/40 backdrop-blur-md border border-slate-800/60 rounded-3xl overflow-hidden shadow-2xl">
        <div className="bg-slate-800/30 border-b border-slate-800/60 px-8 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <Activity size={18} className="text-blue-500" />
            <span className="text-xs font-black text-slate-400 uppercase tracking-widest">Trace Roots</span>
          </div>
          <div className="text-[10px] font-bold text-slate-500 uppercase">Recent Spans</div>
        </div>
        
        <div className="divide-y divide-slate-800/40">
          {isLoading ? (
            <div className="px-8 py-24 text-center text-slate-600 font-bold uppercase tracking-widest text-xs animate-pulse">
              Reconstructing Distributed Traces...
            </div>
          ) : traces?.length === 0 ? (
            <div className="px-8 py-24 text-center text-slate-600 font-bold uppercase tracking-widest text-xs">
              No traces found matching your criteria
            </div>
          ) : (
            traces.map((trace) => (
              <TraceRow key={trace.trace_id} trace={trace} />
            ))
          )}
        </div>
        <CursorPagination
          countLabel={`${traces.length} traces in view`}
          pageLabel={`Page ${cursorHistory.length + 1}`}
          canGoBack={canGoBack}
          canGoNext={canGoNext}
          onBack={goToPreviousPage}
          onNext={goToNextPage}
        />
      </div>
    </div>
  );
};

export default Traces;
