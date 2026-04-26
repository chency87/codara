import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import {
  Activity,
  AlertCircle,
  Bug,
  ChevronRight,
  Clock3,
  Fingerprint,
  X,
} from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import { dashboardPollHeaders } from '../api/dashboardPoll';
import type { ApiEnvelope, RuntimeLogRecord, TraceEvent, TraceRecord } from '../types/api';

const TRACE_PAGE_SIZE = 25;
const TRACE_TIMELINE_LOG_LIMIT = 100;

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

const formatTimestamp = (value?: string | number | null) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
};

const formatTime = (value?: string | number | null) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleTimeString();
};

const toLocalInputValue = (date: Date) => {
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60_000);
  return local.toISOString().slice(0, 16);
};

const toIsoParam = (value: string) => {
  if (!value) return undefined;
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? undefined : date.toISOString();
};

const formatJson = (value?: Record<string, any> | null) => {
  if (!value || Object.keys(value).length === 0) return '// No attributes';
  return JSON.stringify(value, null, 2);
};

const statusClass = (value?: string | null) => {
  if (value === 'error') return 'text-rose-300 border-rose-500/20 bg-rose-500/10';
  return 'text-emerald-300 border-emerald-500/20 bg-emerald-500/10';
};

const levelClass = (value?: string | null) => {
  const level = String(value || '').toUpperCase();
  if (level === 'ERROR') return 'text-rose-300 border-rose-500/20 bg-rose-500/10';
  if (level === 'WARNING') return 'text-amber-200 border-amber-500/20 bg-amber-500/10';
  return 'text-blue-200 border-blue-500/20 bg-blue-500/10';
};

type TimelineEvent = {
  id: string;
  sortAt: number;
  source: 'trace' | 'log';
  title: string;
  subtitle: string;
  component?: string | null;
  level?: string | null;
  kind?: string | null;
  durationMs?: number | null;
  requestId?: string | null;
  traceId?: string | null;
  attributes?: Record<string, any> | null;
  exception?: string | null;
};

const TimelineRow = ({ item, isLast }: { item: TimelineEvent; isLast: boolean }) => (
  <div className="relative pl-6">
    {!isLast && <div className="absolute left-1.5 top-6 h-full w-px bg-slate-700" />}
    <div
      className={`absolute left-0 top-5 h-3 w-3 rounded-full border-2 ${
        item.source === 'log'
          ? 'border-amber-400 bg-amber-900/50'
          : 'border-blue-400 bg-blue-900/50'
      }`}
    />
    <div className="rounded-lg border border-slate-800 bg-slate-900/50 p-3">
      <div className="mb-2 flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-medium text-slate-500">
          {formatTime(item.sortAt)}
        </span>
        <span className={`rounded px-1.5 py-0.5 text-[9px] font-bold uppercase ${
          item.source === 'log' ? levelClass(item.level) : statusClass(item.level === 'ERROR' ? 'error' : 'ok')
        }`}>
          {item.source === 'log' ? `log:${item.level || 'INFO'}` : item.kind || item.level || 'ok'}
        </span>
        {item.durationMs ? (
          <span className="text-[9px] font-bold text-slate-400">{item.durationMs}ms</span>
        ) : null}
      </div>
      <div className="text-sm font-medium text-white">{item.title}</div>
      <div className="mt-1 text-xs text-slate-400">{item.subtitle}</div>
      {(item.attributes && Object.keys(item.attributes).length > 0) || item.exception ? (
        <pre className="mt-2 max-h-24 overflow-auto rounded bg-slate-950 p-2 text-[10px] font-mono text-slate-400">
          {item.exception || formatJson(item.attributes)}
        </pre>
      ) : null}
    </div>
  </div>
);

const Explorer = () => {
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [cursor, setCursor] = useState<number | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<number | null>>([]);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');

  const traceListQuery = useQuery<ApiEnvelope<TraceRecord[]>>({
    queryKey: ['explorer-traces', cursor, status, search, since, until],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/traces', {
        params: {
          limit: TRACE_PAGE_SIZE,
          after: cursor || undefined,
          status: status || undefined,
          search: search.trim() || undefined,
          since: toIsoParam(since),
          until: toIsoParam(until),
        },
      });
      return resp.data;
    },
  });

  const traces = useMemo(() => traceListQuery.data?.data || [], [traceListQuery.data]);
  const cursorMeta = traceListQuery.data?.meta?.page;

  const selectedTrace = useQuery<TraceRecord>({
    queryKey: ['trace-detail', selectedTraceId],
    queryFn: async () => {
      const resp = await axios.get(`/management/v1/traces/${selectedTraceId}`);
      return resp.data;
    },
    enabled: !!selectedTraceId,
  });

  const correlatedLogsQuery = useQuery<ApiEnvelope<RuntimeLogRecord[]>>({
    queryKey: ['trace-correlated-logs', selectedTraceId],
    queryFn: async () => {
      const trace = selectedTrace.data;
      if (!trace?.request_id) return { data: [], meta: {} };
      const resp = await axios.get('/management/v1/logs', {
        params: {
          limit: TRACE_TIMELINE_LOG_LIMIT,
          request_id: trace.request_id,
        },
      });
      return resp.data;
    },
    enabled: !!selectedTraceId && !!selectedTrace.data?.request_id,
  });

  const timelineItems = useMemo(() => {
    if (!selectedTrace.data) return [];
    const traceEvents: TimelineEvent[] = [
      {
        id: `trace-${selectedTrace.data.trace_id}`,
        sortAt: selectedTrace.data.started_at,
        source: 'trace',
        title: selectedTrace.data.name,
        subtitle: `Trace started`,
        component: selectedTrace.data.component,
        level: selectedTrace.data.level,
        kind: 'trace',
        durationMs: selectedTrace.data.duration_ms,
        requestId: selectedTrace.data.request_id,
        traceId: selectedTrace.data.trace_id,
        attributes: selectedTrace.data.attributes,
      },
    ];
    const logEvents: TimelineEvent[] = (correlatedLogsQuery.data?.data || []).map((row, idx) => ({
      id: `log-${row.timestamp}-${idx}`,
      sortAt: new Date(row.timestamp).getTime(),
      source: 'log',
      title: row.message.slice(0, 100),
      subtitle: `${row.component || row.logger} logged`,
      component: row.component || row.logger,
      level: row.level,
      kind: 'log',
      requestId: row.request_id,
      traceId: row.trace_id,
      attributes: row.attributes,
      exception: row.exception,
    }));
    return [...traceEvents, ...logEvents].sort((a, b) => a.sortAt - b.sortAt);
  }, [selectedTrace.data, correlatedLogsQuery.data]);

  const canGoBack = cursorHistory.length > 0;
  const canGoNext = traces.length === TRACE_PAGE_SIZE && Boolean(cursorMeta?.cursor);

  const goToNextPage = () => {
    if (!cursorMeta?.cursor || !canGoNext) return;
    setCursorHistory((prev) => [...prev, cursor]);
    setCursor(Number(cursorMeta.cursor));
  };

  const goToPreviousPage = () => {
    if (!canGoBack) return;
    const prev = cursorHistory[cursorHistory.length - 1];
    setCursorHistory((prev) => prev.slice(0, -1));
    setCursor(prev);
  };

  const setQuickRange = (hours: number) => {
    const now = new Date();
    const start = new Date(now.getTime() - hours * 60 * 60 * 1000);
    setSince(toLocalInputValue(start));
    setUntil(toLocalInputValue(now));
  };

  const clearTimeRange = () => {
    setSince('');
    setUntil('');
  };

  useEffect(() => {
    if (!traces.length) {
      setSelectedTraceId(null);
      return;
    }
    if (!selectedTraceId || !traces.some((row) => row.trace_id === selectedTraceId)) {
      setSelectedTraceId(traces[0].trace_id);
    }
  }, [traces, selectedTraceId]);

const showPanel = selectedTraceId && selectedTrace.data;

  // Close panel on ESC key
  useEffect(() => {
    if (!showPanel) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setSelectedTraceId(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, [showPanel]);

  return (
    <div className="min-h-screen bg-black p-6">
      <header className="mb-6">
        <h2 className="text-3xl font-black tracking-tight text-white">Explorer</h2>
        <p className="mt-1 text-sm text-slate-500">
          Inspect trace roots and runtime execution timelines.
        </p>
      </header>

      {traceListQuery.error ? (
        <div className="mb-4 flex items-center gap-2 rounded-lg border border-rose-500/20 bg-rose-500/10 p-3 text-sm text-rose-200">
          <AlertCircle size={16} />
          <span>{getErrorMessage(traceListQuery.error)}</span>
        </div>
      ) : null}

      <div className="mb-4 rounded-xl border border-slate-800 bg-slate-900/50 p-4">
        <div className="flex flex-wrap gap-4">
          <div className="flex-1 min-w-[200px]">
            <input
              className="w-full rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white placeholder-slate-500 outline-none focus:border-blue-500"
              placeholder="Search traces..."
              value={search}
              onChange={(e) => {
                setCursor(null);
                setCursorHistory([]);
                setSearch(e.target.value);
              }}
            />
          </div>
          <select
            className="rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            value={status}
            onChange={(e) => {
              setCursor(null);
              setCursorHistory([]);
              setStatus(e.target.value);
            }}
          >
            <option value="">All statuses</option>
            <option value="ok">Success</option>
            <option value="error">Failed</option>
          </select>
          <input
            type="datetime-local"
            className="rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            value={since}
            onChange={(e) => {
              setCursor(null);
              setCursorHistory([]);
              setSince(e.target.value);
            }}
          />
          <input
            type="datetime-local"
            className="rounded-lg border border-slate-700 bg-black px-3 py-2 text-sm text-white outline-none focus:border-blue-500"
            value={until}
            onChange={(e) => {
              setCursor(null);
              setCursorHistory([]);
              setUntil(e.target.value);
            }}
          />
          <div className="flex gap-1">
            {[
              ['1h', 1],
              ['6h', 6],
              ['24h', 24],
            ].map(([label, hours]) => (
              <button
                key={label}
                type="button"
                onClick={() => setQuickRange(Number(hours))}
                className="rounded-lg border border-slate-700 bg-black/50 px-2 py-2 text-xs font-medium text-slate-400 hover:border-blue-500/40 hover:text-blue-200"
              >
                {label}
              </button>
            ))}
            <button
              type="button"
              onClick={clearTimeRange}
              className="rounded-lg border border-slate-700 bg-slate-800 px-2 py-2 text-xs font-medium text-slate-500 hover:text-white"
            >
              Clear
            </button>
          </div>
        </div>
      </div>

      <div className="rounded-xl border border-slate-800 bg-slate-900">
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
              {traceListQuery.isLoading ? (
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
                    onClick={() => setSelectedTraceId(row.trace_id)}
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

        <CursorPagination
          countLabel={`${traces.length} traces`}
          pageLabel={`Page ${cursorHistory.length + 1}`}
          canGoBack={canGoBack}
          canGoNext={canGoNext}
          onBack={goToPreviousPage}
          onNext={goToNextPage}
        />
      </div>

      {showPanel && (
        <div
          className="fixed inset-0 z-40 bg-black/60"
          onClick={() => setSelectedTraceId(null)}
        >
          <div
            className="absolute right-0 top-0 bottom-0 w-[480px] bg-slate-900 border-l border-slate-800 shadow-2xl overflow-auto animate-in slide-in-from-right duration-300"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-800 bg-slate-900 px-6 py-4">
              <div className="flex items-center gap-3">
                <Fingerprint size={18} className="text-blue-400" />
                <div>
                  <div className="text-sm font-bold text-white">{selectedTrace.data.name}</div>
                  <div className="text-[10px] font-mono text-slate-500">{selectedTraceId}</div>
                </div>
              </div>
              <button
                onClick={() => setSelectedTraceId(null)}
                className="p-2 rounded-lg text-slate-500 hover:text-white hover:bg-white/10 transition-colors"
              >
                <X size={20} />
              </button>
            </div>

            <div className="p-6 space-y-6">
              <div className="grid grid-cols-2 gap-3">
                <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-3">
                  <div className="text-[10px] font-bold uppercase text-slate-500">Status</div>
                  <div className="mt-1">
                    <span className={`inline-flex rounded px-2 py-1 text-xs font-bold uppercase ${statusClass(selectedTrace.data.status)}`}>
                      {selectedTrace.data.status || 'ok'}
                    </span>
                  </div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-3">
                  <div className="text-[10px] font-bold uppercase text-slate-500">Duration</div>
                  <div className="mt-1 text-sm font-bold text-white">
                    {selectedTrace.data.duration_ms ? `${selectedTrace.data.duration_ms}ms` : '—'}
                  </div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-3">
                  <div className="text-[10px] font-bold uppercase text-slate-500">Started</div>
                  <div className="mt-1 text-sm font-bold text-white">
                    {formatTimestamp(selectedTrace.data.started_at)}
                  </div>
                </div>
                <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-3">
                  <div className="text-[10px] font-bold uppercase text-slate-500">Component</div>
                  <div className="mt-1 text-sm font-bold text-white">{selectedTrace.data.component}</div>
                </div>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-4">
                <div className="mb-4 flex items-center gap-2">
                  <Clock3 size={14} className="text-blue-400" />
                  <span className="text-xs font-bold uppercase text-slate-400">Timeline</span>
                  <span className="text-[10px] text-slate-600">({timelineItems.length} events)</span>
                </div>
                {correlatedLogsQuery.isLoading ? (
                  <div className="py-8 text-center text-xs text-slate-500 animate-pulse">
                    Loading timeline...
                  </div>
                ) : timelineItems.length === 0 ? (
                  <div className="py-8 text-center text-xs text-slate-500">
                    No timeline events
                  </div>
                ) : (
                  <div className="space-y-4">
                    {timelineItems.map((item, idx) => (
                      <TimelineRow
                        key={item.id}
                        item={item}
                        isLast={idx === timelineItems.length - 1}
                      />
                    ))}
                  </div>
                )}
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-4">
                <div className="mb-3 flex items-center gap-2">
                  <Bug size={14} className="text-amber-400" />
                  <span className="text-xs font-bold uppercase text-slate-400">Metadata</span>
                </div>
                <div className="space-y-2 text-[10px] font-mono text-slate-400">
                  <div className="flex justify-between">
                    <span className="text-slate-600">trace_id:</span>
                    <span className="text-slate-300">{selectedTrace.data.trace_id}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-600">span_id:</span>
                    <span className="text-slate-300">{selectedTrace.data.span_id}</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-slate-600">request_id:</span>
                    <span className="text-slate-300">{selectedTrace.data.request_id || '—'}</span>
                  </div>
                </div>
              </div>

              <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-4">
                <div className="mb-3 flex items-center gap-2">
                  <ChevronRight size={14} className="text-blue-400" />
                  <span className="text-xs font-bold uppercase text-slate-400">Attributes</span>
                </div>
                <pre className="max-h-[200px] overflow-auto rounded bg-slate-950 p-3 text-[10px] font-mono text-slate-400">
                  {formatJson(selectedTrace.data.attributes)}
                </pre>
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Explorer;