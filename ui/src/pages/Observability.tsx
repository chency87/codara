import React, { useEffect, useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import {
  Activity,
  AlertCircle,
  Bug,
  ChevronRight,
  Clock3,
  Fingerprint,
  ScrollText,
  Search,
  TimerReset,
} from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import type { ApiEnvelope, ObservabilityPruneResult, RuntimeLogRecord, TraceEvent, TraceRecord } from '../types/api';

const TRACE_PAGE_SIZE = 25;
const LOG_PAGE_SIZE = 50;
const TRACE_TIMELINE_LOG_LIMIT = 100;

type ObservabilityTab = 'traces' | 'logs';

type TimelineItem = {
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
  if (value === null || value === undefined || value === '') return '-';
  const date = typeof value === 'number' ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleString();
};

const formatClock = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return '-';
  const date = typeof value === 'number' ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleTimeString();
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
  if (!value || Object.keys(value).length === 0) return '// No structured attributes recorded';
  return JSON.stringify(value, null, 2);
};

const levelClass = (value?: string | null) => {
  const level = String(value || '').toUpperCase();
  if (level === 'ERROR') return 'text-rose-300 border-rose-500/20 bg-rose-500/10';
  if (level === 'WARNING') return 'text-amber-200 border-amber-500/20 bg-amber-500/10';
  return 'text-blue-200 border-blue-500/20 bg-blue-500/10';
};

const statusClass = (value?: string | null) => {
  return value === 'error'
    ? 'text-rose-300 border-rose-500/20 bg-rose-500/10'
    : 'text-emerald-200 border-emerald-500/20 bg-emerald-500/10';
};

const TimelineRow = ({ item }: { item: TimelineItem }) => (
  <div className="relative pl-8">
    <div className="absolute left-2 top-6 h-full w-px bg-slate-800/70 last:hidden" />
    <div
      className={`absolute left-0 top-5 h-4 w-4 rounded-full border ${
        item.source === 'log'
          ? 'border-amber-400/40 bg-amber-500/20 shadow-[0_0_12px_rgba(245,158,11,0.15)]'
          : 'border-blue-400/40 bg-blue-500/20 shadow-[0_0_12px_rgba(59,130,246,0.15)]'
      }`}
    />
    <div className="rounded-2xl border border-slate-800/70 bg-black/40 p-4">
      <div className="mb-3 flex flex-wrap items-center gap-2">
        <span className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
          {formatClock(item.sortAt)}
        </span>
        <span className={`rounded-lg border px-2 py-1 text-[9px] font-black uppercase tracking-widest ${item.source === 'log' ? levelClass(item.level) : statusClass(item.level === 'ERROR' ? 'error' : 'ok')}`}>
          {item.source === 'log' ? `log:${item.level || 'INFO'}` : item.kind || 'trace'}
        </span>
        {item.durationMs ? (
          <span className="rounded-lg border border-slate-700 bg-slate-900/60 px-2 py-1 text-[9px] font-black uppercase tracking-widest text-slate-400">
            {item.durationMs} ms
          </span>
        ) : null}
      </div>
      <div className="text-sm font-bold text-white">{item.title}</div>
      <div className="mt-1 text-xs text-slate-400">{item.subtitle}</div>
      <div className="mt-3 flex flex-wrap items-center gap-3 text-[10px] font-mono text-slate-500">
        <span>{item.component || 'unknown-component'}</span>
        {item.requestId ? <span>{item.requestId}</span> : null}
        {item.traceId ? <span>{item.traceId}</span> : null}
      </div>
      {((item.attributes && Object.keys(item.attributes).length > 0) || item.exception) ? (
        <pre className="mt-4 max-h-48 overflow-auto rounded-xl border border-slate-800 bg-slate-950/80 p-3 text-[10px] font-mono text-slate-400">
          {item.exception || formatJson(item.attributes)}
        </pre>
      ) : null}
    </div>
  </div>
);

const Observability = () => {
  const queryClient = useQueryClient();
  const [tab, setTab] = useState<ObservabilityTab>('traces');
  const [search, setSearch] = useState('');
  const [component, setComponent] = useState('');
  const [requestId, setRequestId] = useState('');
  const [traceId, setTraceId] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');
  const [status, setStatus] = useState('');
  const [level, setLevel] = useState('');
  const [traceCursor, setTraceCursor] = useState<number | null>(null);
  const [traceCursorHistory, setTraceCursorHistory] = useState<Array<number | null>>([]);
  const [logCursor, setLogCursor] = useState<string | null>(null);
  const [logCursorHistory, setLogCursorHistory] = useState<Array<string | null>>([]);
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [selectedLogKey, setSelectedLogKey] = useState<string | null>(null);
  const [pruneFeedback, setPruneFeedback] = useState<string | null>(null);

  const pruneMutation = useMutation({
    mutationFn: async () => {
      const resp = await axios.post<ApiEnvelope<ObservabilityPruneResult>>('/management/v1/observability/prune');
      return resp.data.data;
    },
    onMutate: () => {
      setPruneFeedback(null);
    },
    onSuccess: async (result) => {
      setPruneFeedback(
        `Pruned ${result.runtime_logs.records_deleted} log records and ${result.traces.records_deleted} trace records.`,
      );
      await Promise.all([
        queryClient.invalidateQueries({ queryKey: ['observability-traces'] }),
        queryClient.invalidateQueries({ queryKey: ['observability-logs'] }),
        queryClient.invalidateQueries({ queryKey: ['observability-trace-detail'] }),
        queryClient.invalidateQueries({ queryKey: ['observability-trace-logs'] }),
      ]);
    },
    onError: (error) => {
      setPruneFeedback(getErrorMessage(error));
    },
  });

  const resetTracesPagination = () => {
    setTraceCursor(null);
    setTraceCursorHistory([]);
  };

  const resetLogsPagination = () => {
    setLogCursor(null);
    setLogCursorHistory([]);
  };

  const resetAllPagination = () => {
    resetTracesPagination();
    resetLogsPagination();
  };

  const traceListQuery = useQuery<ApiEnvelope<TraceRecord[]>>({
    queryKey: ['observability-traces', traceCursor, component, requestId, traceId, status, search, since, until],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/traces', {
        params: {
          limit: TRACE_PAGE_SIZE,
          after: traceCursor || undefined,
          component: component.trim() || undefined,
          request_id: requestId.trim() || undefined,
          trace_id: traceId.trim() || undefined,
          status: status || undefined,
          search: search.trim() || undefined,
          since: toIsoParam(since),
          until: toIsoParam(until),
        },
      });
      return resp.data;
    },
  });

  const runtimeLogQuery = useQuery<ApiEnvelope<RuntimeLogRecord[]>>({
    queryKey: ['observability-logs', logCursor, level, component, requestId, traceId, search, since, until],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/logs', {
        params: {
          limit: LOG_PAGE_SIZE,
          after: logCursor || undefined,
          level: level || undefined,
          component: component.trim() || undefined,
          request_id: requestId.trim() || undefined,
          trace_id: traceId.trim() || undefined,
          search: search.trim() || undefined,
          since: toIsoParam(since),
          until: toIsoParam(until),
        },
      });
      return resp.data;
    },
  });

  const traces = useMemo(() => traceListQuery.data?.data || [], [traceListQuery.data]);
  const logs = useMemo(() => runtimeLogQuery.data?.data || [], [runtimeLogQuery.data]);

  useEffect(() => {
    if (!traces.length) {
      setSelectedTraceId(null);
      return;
    }
    if (!selectedTraceId || !traces.some((row) => row.trace_id === selectedTraceId)) {
      setSelectedTraceId(traces[0].trace_id);
    }
  }, [traces, selectedTraceId]);

  useEffect(() => {
    if (!logs.length) {
      setSelectedLogKey(null);
      return;
    }
    if (!selectedLogKey || !logs.some((row, index) => `${row.timestamp}-${index}` === selectedLogKey)) {
      setSelectedLogKey(`${logs[0].timestamp}-0`);
    }
  }, [logs, selectedLogKey]);

  const selectedTrace = useMemo(
    () => traces.find((row) => row.trace_id === selectedTraceId) || null,
    [selectedTraceId, traces],
  );

  const selectedLog = useMemo(
    () => logs.find((row, index) => `${row.timestamp}-${index}` === selectedLogKey) || null,
    [logs, selectedLogKey],
  );

  const activeTraceId = tab === 'traces' ? selectedTrace?.trace_id || null : selectedLog?.trace_id || null;

  const traceDetailQuery = useQuery<ApiEnvelope<{ trace_id: string; events: TraceEvent[] }>>({
    queryKey: ['observability-trace-detail', activeTraceId],
    queryFn: async () => (await axios.get(`/management/v1/traces/${activeTraceId}`)).data,
    enabled: Boolean(activeTraceId),
  });

  const correlatedLogsQuery = useQuery<ApiEnvelope<RuntimeLogRecord[]>>({
    queryKey: ['observability-trace-logs', activeTraceId],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/logs', {
        params: {
          trace_id: activeTraceId,
          limit: TRACE_TIMELINE_LOG_LIMIT,
        },
      });
      return resp.data;
    },
    enabled: Boolean(activeTraceId),
  });

  const timelineItems = useMemo(() => {
    const events = traceDetailQuery.data?.data?.events || [];
    const correlatedLogs = correlatedLogsQuery.data?.data || [];
    const traceItems: TimelineItem[] = events.map((event) => ({
      id: event.event_id,
      sortAt: event.started_at,
      source: 'trace',
      title: event.name,
      subtitle: event.kind === 'span' ? `${event.component || 'unknown-component'} span` : `${event.component || 'unknown-component'} event`,
      component: event.component,
      level: event.level,
      kind: event.kind,
      durationMs: event.duration_ms,
      requestId: event.request_id,
      traceId: event.trace_id,
      attributes: event.attributes,
    }));
    const logItems: TimelineItem[] = correlatedLogs.map((row, index) => ({
      id: `${row.timestamp}-${index}`,
      sortAt: new Date(row.timestamp).getTime(),
      source: 'log',
      title: row.message,
      subtitle: `${row.component || row.logger} emitted a ${String(row.level || 'INFO').toUpperCase()} runtime message`,
      component: row.component || row.logger,
      level: row.level,
      kind: 'log',
      requestId: row.request_id,
      traceId: row.trace_id,
      attributes: row.attributes,
      exception: row.exception,
    }));
    return [...traceItems, ...logItems].sort((left, right) => left.sortAt - right.sortAt);
  }, [correlatedLogsQuery.data, traceDetailQuery.data]);

  const tracePageMeta = traceListQuery.data?.meta?.page;
  const logPageMeta = runtimeLogQuery.data?.meta?.page;
  const canGoTraceBack = traceCursorHistory.length > 0;
  const canGoTraceNext = traces.length === TRACE_PAGE_SIZE && Boolean(tracePageMeta?.cursor);
  const canGoLogBack = logCursorHistory.length > 0;
  const canGoLogNext = logs.length === LOG_PAGE_SIZE && Boolean(logPageMeta?.cursor);

  const goToNextTracePage = () => {
    if (!tracePageMeta?.cursor || !canGoTraceNext) return;
    setTraceCursorHistory((prev) => [...prev, traceCursor]);
    setTraceCursor(Number(tracePageMeta.cursor));
  };

  const goToPreviousTracePage = () => {
    if (!canGoTraceBack) return;
    const previousCursor = traceCursorHistory[traceCursorHistory.length - 1] ?? null;
    setTraceCursorHistory((prev) => prev.slice(0, -1));
    setTraceCursor(previousCursor);
  };

  const goToNextLogPage = () => {
    if (!logPageMeta?.cursor || !canGoLogNext) return;
    setLogCursorHistory((prev) => [...prev, logCursor]);
    setLogCursor(String(logPageMeta.cursor));
  };

  const goToPreviousLogPage = () => {
    if (!canGoLogBack) return;
    const previousCursor = logCursorHistory[logCursorHistory.length - 1] ?? null;
    setLogCursorHistory((prev) => prev.slice(0, -1));
    setLogCursor(previousCursor);
  };

  const traceErrors = traces.filter((row) => row.status === 'error' || row.level === 'ERROR').length;
  const logErrors = logs.filter((row) => String(row.level || '').toUpperCase() === 'ERROR').length;

  const setQuickRange = (hours: number) => {
    const now = new Date();
    const start = new Date(now.getTime() - hours * 60 * 60 * 1000);
    resetAllPagination();
    setSince(toLocalInputValue(start));
    setUntil(toLocalInputValue(now));
  };

  const clearTimeRange = () => {
    resetAllPagination();
    setSince('');
    setUntil('');
  };

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-10">
        <h2 className="text-4xl font-black tracking-tight text-white">Observability Explorer</h2>
        <p className="mt-2 max-w-3xl text-slate-500 font-medium">
          Search trace roots, inspect runtime messages, and reconstruct one execution timeline from file-backed trace and log shards.
        </p>
        <div className="mt-5 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => pruneMutation.mutate()}
            disabled={pruneMutation.isPending}
            className="inline-flex items-center gap-2 rounded-2xl border border-amber-500/20 bg-amber-500/10 px-4 py-3 text-xs font-black uppercase tracking-widest text-amber-200 disabled:opacity-50"
          >
            <TimerReset size={14} className={pruneMutation.isPending ? 'animate-spin' : ''} />
            Prune old shards
          </button>
          {pruneFeedback ? (
            <div className={`rounded-2xl border px-4 py-3 text-sm ${
              pruneFeedback.startsWith('Pruned ')
                ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-200'
                : 'border-rose-500/20 bg-rose-500/10 text-rose-200'
            }`}>
              {pruneFeedback}
            </div>
          ) : null}
        </div>
      </header>

      {(traceListQuery.error || runtimeLogQuery.error || traceDetailQuery.error || correlatedLogsQuery.error) ? (
        <div className="mb-6 flex items-center gap-3 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          <AlertCircle size={18} />
          <span>
            Observability query failed:{' '}
            {getErrorMessage(traceListQuery.error || runtimeLogQuery.error || traceDetailQuery.error || correlatedLogsQuery.error)}
          </span>
        </div>
      ) : null}

      <div className="mb-6 grid grid-cols-1 gap-4 xl:grid-cols-4">
        {[
          ['Visible traces', `${traces.length}`],
          ['Error traces', `${traceErrors}`],
          ['Visible logs', `${logs.length}`],
          ['Error logs', `${logErrors}`],
        ].map(([label, value]) => (
          <div key={label} className="rounded-3xl border border-slate-800/60 bg-slate-900/40 p-5">
            <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">{label}</div>
            <div className="mt-3 text-3xl font-black text-white">{value}</div>
          </div>
        ))}
      </div>

      <div className="mb-8 rounded-3xl border border-slate-800/60 bg-slate-900/40 p-6">
        <div className="mb-4 flex flex-wrap items-center gap-3">
          <button
            type="button"
            onClick={() => setTab('traces')}
            className={`rounded-xl border px-4 py-2 text-xs font-black uppercase tracking-widest ${
              tab === 'traces'
                ? 'border-blue-500/30 bg-blue-500/10 text-blue-200'
                : 'border-slate-800 bg-black/50 text-slate-400'
            }`}
          >
            Trace Explorer
          </button>
          <button
            type="button"
            onClick={() => setTab('logs')}
            className={`rounded-xl border px-4 py-2 text-xs font-black uppercase tracking-widest ${
              tab === 'logs'
                ? 'border-blue-500/30 bg-blue-500/10 text-blue-200'
                : 'border-slate-800 bg-black/50 text-slate-400'
            }`}
          >
            Runtime Messages
          </button>
        </div>

        <div className="grid grid-cols-1 gap-4 md:grid-cols-2 2xl:grid-cols-6">
          <label className="space-y-2 2xl:col-span-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Search</span>
            <div className="relative">
              <Search size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
              <input
                className="w-full rounded-xl border border-slate-800 bg-black py-3 pl-10 pr-4 text-sm text-white outline-none focus:border-blue-500"
                placeholder="trace name, message, request id, attributes..."
                value={search}
                onChange={(event) => {
                  resetAllPagination();
                  setSearch(event.target.value);
                }}
              />
            </div>
          </label>

          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Component</span>
            <input
              className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
              placeholder="gateway.http"
              value={component}
              onChange={(event) => {
                resetAllPagination();
                setComponent(event.target.value);
              }}
            />
          </label>

          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Request ID</span>
            <input
              className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
              placeholder="req_..."
              value={requestId}
              onChange={(event) => {
                resetAllPagination();
                setRequestId(event.target.value);
              }}
            />
          </label>

          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Trace ID</span>
            <input
              className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
              placeholder="trc_..."
              value={traceId}
              onChange={(event) => {
                resetAllPagination();
                setTraceId(event.target.value);
              }}
            />
          </label>

          {tab === 'traces' ? (
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Status</span>
              <select
                className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
                value={status}
                onChange={(event) => {
                  resetTracesPagination();
                  setStatus(event.target.value);
                }}
              >
                <option value="">All statuses</option>
                <option value="ok">Success</option>
                <option value="error">Failure</option>
              </select>
            </label>
          ) : (
            <label className="space-y-2">
              <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Level</span>
              <select
                className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
                value={level}
                onChange={(event) => {
                  resetLogsPagination();
                  setLevel(event.target.value);
                }}
              >
                <option value="">All levels</option>
                <option value="INFO">INFO</option>
                <option value="WARNING">WARNING</option>
                <option value="ERROR">ERROR</option>
              </select>
            </label>
          )}
        </div>

        <div className="mt-5 grid grid-cols-1 gap-4 xl:grid-cols-[1fr_1fr_1.4fr]">
          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Since</span>
            <input
              type="datetime-local"
              className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
              value={since}
              onChange={(event) => {
                resetAllPagination();
                setSince(event.target.value);
              }}
            />
          </label>
          <label className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Until</span>
            <input
              type="datetime-local"
              className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
              value={until}
              onChange={(event) => {
                resetAllPagination();
                setUntil(event.target.value);
              }}
            />
          </label>
          <div className="space-y-2">
            <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Quick Range</span>
            <div className="flex flex-wrap gap-2">
              {[
                ['1h', 1],
                ['6h', 6],
                ['24h', 24],
                ['7d', 24 * 7],
              ].map(([label, hours]) => (
                <button
                  key={label}
                  type="button"
                  onClick={() => setQuickRange(Number(hours))}
                  className="rounded-xl border border-slate-800 bg-black/50 px-3 py-3 text-[10px] font-black uppercase tracking-widest text-slate-400 transition-colors hover:border-blue-500/40 hover:text-blue-200"
                >
                  Last {label}
                </button>
              ))}
              <button
                type="button"
                onClick={clearTimeRange}
                className="rounded-xl border border-slate-800 bg-slate-900/50 px-3 py-3 text-[10px] font-black uppercase tracking-widest text-slate-500 transition-colors hover:border-slate-600 hover:text-white"
              >
                Clear
              </button>
            </div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-8 2xl:grid-cols-[0.95fr_1.35fr]">
        <section className="overflow-hidden rounded-3xl border border-slate-800/60 bg-slate-900/40">
          <div className="flex items-center justify-between border-b border-slate-800/60 px-6 py-4">
            <div className="flex items-center gap-3">
              {tab === 'traces' ? <Activity size={18} className="text-blue-400" /> : <ScrollText size={18} className="text-amber-300" />}
              <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">
                {tab === 'traces' ? 'Trace Roots' : 'Runtime Messages'}
              </span>
            </div>
            <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
              {tab === 'traces' ? 'Select a trace to build a timeline' : 'Select a message to inspect context'}
            </div>
          </div>

          <div className="divide-y divide-slate-800/40">
            {tab === 'traces' ? (
              traceListQuery.isLoading ? (
                <div className="px-6 py-24 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600 animate-pulse">
                  Scanning trace roots...
                </div>
              ) : traces.length === 0 ? (
                <div className="px-6 py-24 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600">
                  No traces found
                </div>
              ) : (
                traces.map((row) => {
                  const active = row.trace_id === selectedTraceId;
                  return (
                    <button
                      type="button"
                      key={row.trace_id}
                      onClick={() => setSelectedTraceId(row.trace_id)}
                      className={`w-full px-6 py-5 text-left transition-colors ${
                        active ? 'bg-blue-500/10' : 'hover:bg-white/[0.02]'
                      }`}
                    >
                      <div className="mb-3 flex items-center justify-between gap-4">
                        <span className="truncate text-sm font-bold text-white">{row.name}</span>
                        <span className={`shrink-0 rounded-lg border px-2 py-1 text-[9px] font-black uppercase tracking-widest ${statusClass(row.status)}`}>
                          {row.status || row.level || 'ok'}
                        </span>
                      </div>
                      <div className="grid grid-cols-1 gap-2 text-[10px] font-mono text-slate-500 md:grid-cols-2">
                        <div>{row.component}</div>
                        <div>{formatTimestamp(row.started_at)}</div>
                        <div>{row.request_id || '-'}</div>
                        <div>{row.duration_ms ? `${row.duration_ms} ms` : '-'}</div>
                      </div>
                    </button>
                  );
                })
              )
            ) : (
              runtimeLogQuery.isLoading ? (
                <div className="px-6 py-24 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600 animate-pulse">
                  Reading runtime shards...
                </div>
              ) : logs.length === 0 ? (
                <div className="px-6 py-24 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600">
                  No runtime messages found
                </div>
              ) : (
                logs.map((row, index) => {
                  const rowKey = `${row.timestamp}-${index}`;
                  const active = rowKey === selectedLogKey;
                  return (
                    <button
                      type="button"
                      key={rowKey}
                      onClick={() => setSelectedLogKey(rowKey)}
                      className={`w-full px-6 py-5 text-left transition-colors ${
                        active ? 'bg-amber-500/10' : 'hover:bg-white/[0.02]'
                      }`}
                    >
                      <div className="mb-3 flex items-center justify-between gap-4">
                        <span className="truncate text-sm font-bold text-white">{row.message}</span>
                        <span className={`shrink-0 rounded-lg border px-2 py-1 text-[9px] font-black uppercase tracking-widest ${levelClass(row.level)}`}>
                          {row.level}
                        </span>
                      </div>
                      <div className="grid grid-cols-1 gap-2 text-[10px] font-mono text-slate-500 md:grid-cols-2">
                        <div>{row.component || row.logger}</div>
                        <div>{formatTimestamp(row.timestamp)}</div>
                        <div>{row.request_id || '-'}</div>
                        <div>{row.trace_id || '-'}</div>
                      </div>
                    </button>
                  );
                })
              )
            )}
          </div>

          <CursorPagination
            countLabel={tab === 'traces' ? `${traces.length} traces in view` : `${logs.length} logs in view`}
            pageLabel={
              tab === 'traces'
                ? `page ${traceCursorHistory.length + 1}`
                : `page ${logCursorHistory.length + 1}`
            }
            canGoBack={tab === 'traces' ? canGoTraceBack : canGoLogBack}
            canGoNext={tab === 'traces' ? canGoTraceNext : canGoLogNext}
            onBack={tab === 'traces' ? goToPreviousTracePage : goToPreviousLogPage}
            onNext={tab === 'traces' ? goToNextTracePage : goToNextLogPage}
          />
        </section>

        <section className="overflow-hidden rounded-3xl border border-slate-800/60 bg-slate-900/40">
          <div className="flex items-center justify-between border-b border-slate-800/60 px-6 py-4">
            <div className="flex items-center gap-3">
              <TimerReset size={18} className="text-blue-400" />
              <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">Timeline Detail</span>
            </div>
            <div className="flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
              <ChevronRight size={12} />
              <span>{activeTraceId ? 'Correlated trace + runtime data' : 'Select a trace or log entry'}</span>
            </div>
          </div>

          <div className="space-y-6 p-6">
            {tab === 'traces' && selectedTrace ? (
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
                <div className="rounded-2xl border border-slate-800 bg-black/40 p-4">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Trace</div>
                  <div className="mt-3 flex items-center gap-2 text-sm font-bold text-white">
                    <Fingerprint size={14} className="text-blue-400" />
                    <span className="truncate">{selectedTrace.trace_id}</span>
                  </div>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-black/40 p-4">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Started</div>
                  <div className="mt-3 text-sm font-bold text-white">{formatTimestamp(selectedTrace.started_at)}</div>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-black/40 p-4">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Duration</div>
                  <div className="mt-3 text-sm font-bold text-white">{selectedTrace.duration_ms ? `${selectedTrace.duration_ms} ms` : '-'}</div>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-black/40 p-4">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Component</div>
                  <div className="mt-3 text-sm font-bold text-white">{selectedTrace.component}</div>
                </div>
              </div>
            ) : null}

            {tab === 'logs' && selectedLog ? (
              <div className="grid grid-cols-1 gap-4 xl:grid-cols-4">
                <div className="rounded-2xl border border-slate-800 bg-black/40 p-4 xl:col-span-2">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Message</div>
                  <div className="mt-3 text-sm font-bold text-white">{selectedLog.message}</div>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-black/40 p-4">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Level</div>
                  <div className="mt-3 text-sm font-bold text-white">{selectedLog.level}</div>
                </div>
                <div className="rounded-2xl border border-slate-800 bg-black/40 p-4">
                  <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Timestamp</div>
                  <div className="mt-3 text-sm font-bold text-white">{formatTimestamp(selectedLog.timestamp)}</div>
                </div>
              </div>
            ) : null}

            {activeTraceId ? (
              <div className="grid grid-cols-1 gap-6 xl:grid-cols-[1.45fr_0.85fr]">
                <div className="rounded-3xl border border-slate-800/60 bg-black/30 p-5">
                  <div className="mb-5 flex items-center justify-between gap-4">
                    <div className="flex items-center gap-3">
                      <Clock3 size={16} className="text-blue-400" />
                      <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">Execution Timeline</span>
                    </div>
                    <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-600">
                      {timelineItems.length} correlated items
                    </div>
                  </div>

                  {traceDetailQuery.isLoading || correlatedLogsQuery.isLoading ? (
                    <div className="py-20 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600 animate-pulse">
                      Reconstructing timeline...
                    </div>
                  ) : timelineItems.length === 0 ? (
                    <div className="py-20 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600">
                      No correlated events found for this trace
                    </div>
                  ) : (
                    <div className="space-y-4">
                      {timelineItems.map((item) => (
                        <TimelineRow key={item.id} item={item} />
                      ))}
                    </div>
                  )}
                </div>

                <div className="space-y-6">
                  <div className="rounded-3xl border border-slate-800/60 bg-black/30 p-5">
                    <div className="mb-4 flex items-center gap-3">
                      <Bug size={16} className="text-amber-300" />
                      <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">Trace Metadata</span>
                    </div>
                    <div className="space-y-3 text-[10px] font-mono text-slate-400">
                      <div>trace_id: {activeTraceId}</div>
                      <div>request_id: {selectedTrace?.request_id || selectedLog?.request_id || '-'}</div>
                      <div>component: {selectedTrace?.component || selectedLog?.component || selectedLog?.logger || '-'}</div>
                      <div>timeline_items: {timelineItems.length}</div>
                    </div>
                  </div>

                  <div className="rounded-3xl border border-slate-800/60 bg-black/30 p-5">
                    <div className="mb-4 flex items-center gap-3">
                      <ScrollText size={16} className="text-blue-400" />
                      <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">Attributes</span>
                    </div>
                    <pre className="max-h-[480px] overflow-auto rounded-2xl border border-slate-800 bg-slate-950/80 p-4 text-[10px] font-mono text-slate-400">
                      {tab === 'traces'
                        ? formatJson(selectedTrace?.attributes || null)
                        : selectedLog?.exception || formatJson(selectedLog?.attributes || null)}
                    </pre>
                  </div>
                </div>
              </div>
            ) : (
              <div className="rounded-3xl border border-dashed border-slate-800 bg-black/20 px-6 py-24 text-center">
                <div className="text-xs font-black uppercase tracking-[0.2em] text-slate-500">
                  Select a {tab === 'traces' ? 'trace root' : 'runtime message'} to inspect execution detail
                </div>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  );
};

export default Observability;
