import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import {
  AlertCircle,
  Activity,
  ScrollText,
} from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import { 
  TraceList, 
  LogList, 
  TimelineDetail, 
  ObservabilityFilterBar 
} from '../components/observability';
import type { ApiEnvelope, TraceEvent, TraceRecord } from '../types/api';

const TRACE_PAGE_SIZE = 25;

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

const formatJson = (value?: Record<string, unknown> | null) => {
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

const Observability = () => {
  const [tab, setTab] = useState<'traces' | 'logs'>('traces');
  const [selectedTraceId, setSelectedTraceId] = useState<string | null>(null);
  const [selectedLogKey, setSelectedLogKey] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [search, setSearch] = useState('');
  const [status, setStatus] = useState('');
  const [level, setLevel] = useState('');
  const [since, setSince] = useState('');
  const [until, setUntil] = useState('');

  const traceListQuery = useQuery<ApiEnvelope<TraceRecord[]>>({
    queryKey: ['explorer-traces', cursor, status, search, since, until],
    queryFn: async () => {
      if (tab !== 'traces') return { data: [], meta: {} };
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
    enabled: tab === 'traces',
  });

  const logListQuery = useQuery<ApiEnvelope<RuntimeLogRecord[]>>({
    queryKey: ['explorer-logs', cursor, level, search, since, until],
    queryFn: async () => {
      if (tab !== 'logs') return { data: [], meta: {} };
      const resp = await axios.get('/management/v1/logs', {
        params: {
          limit: TRACE_PAGE_SIZE,
          after: cursor || undefined,
          level: level || undefined,
          search: search.trim() || undefined,
          since: toIsoParam(since),
          until: toIsoParam(until),
        },
      });
      return resp.data;
    },
    enabled: tab === 'logs',
  });

  const traces = useMemo(() => traceListQuery.data?.data || [], [traceListQuery.data]);
  const logs = useMemo(() => logListQuery.data?.data || [], [logListQuery.data]);
  const cursorMeta = tab === 'traces' ? traceListQuery.data?.meta?.page : logListQuery.data?.meta?.page;

  const selectedTrace = useQuery<ApiEnvelope<TraceRecord>>({
    queryKey: ['trace-detail', selectedTraceId],
    queryFn: async () => {
      if (!selectedTraceId) {
        throw new Error('No trace selected');
      }
      const resp = await axios.get(`/management/v1/traces/${selectedTraceId}`);
      return resp.data;
    },
    enabled: !!selectedTraceId,
  });

  const timelineItems = useMemo(() => {
    const traceData = selectedTrace.data?.data;
    if (!traceData || !traceData.events) return [];

    const effectiveSortAt = (event: TraceEvent) => {
      if (event.kind === 'span.completed') {
        const endedAt = event.ended_at;
        if (typeof endedAt === 'number' && !Number.isNaN(endedAt)) return endedAt;
      }
      return Number(event.started_at);
    };

    const kindRank = (kind?: string | null) => {
      switch (kind) {
        case 'span.started':
          return 0;
        case 'event':
          return 1;
        case 'log':
          return 2;
        case 'span.completed':
          return 3;
        default:
          return 4;
      }
    };
    
    return traceData.events
      .map((event) => {
      const title = event.name;
      let subtitle = event.kind;
      
      if (event.kind === 'span.started') {
        subtitle = 'Started';
      } else if (event.kind === 'span.completed') {
        subtitle = 'Completed';
      } else if (event.kind === 'event') {
        subtitle = 'Event';
      }

      return {
        id: event.event_id,
        sortAt: effectiveSortAt(event),
        source: 'trace' as const,
        title,
        subtitle: `${event.component || 'system'} • ${subtitle}`,
        component: event.component,
        level: event.level || (event.status === 'error' ? 'ERROR' : 'INFO'),
        kind: event.kind,
        durationMs: event.duration_ms,
        requestId: event.request_id,
        traceId: event.trace_id,
        attributes: event.attributes,
      };
    })
      .sort((a, b) => {
        const primary = a.sortAt - b.sortAt;
        if (primary !== 0) return primary;
        const secondary = kindRank(a.kind) - kindRank(b.kind);
        if (secondary !== 0) return secondary;
        return a.id.localeCompare(b.id);
      });
  }, [selectedTrace.data]);

  const canGoBack = cursorHistory.length > 0;
  const canGoNext = (tab === 'traces' ? traces.length : logs.length) === TRACE_PAGE_SIZE && Boolean(cursorMeta?.cursor);

  const goToNextPage = () => {
    if (!cursorMeta?.cursor || !canGoNext) return;
    setCursorHistory((prev) => [...prev, cursor]);
    setCursor(cursorMeta.cursor);
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
    setCursor(null);
    setCursorHistory([]);
  };

  const clearTimeRange = () => {
    setSince('');
    setUntil('');
    setCursor(null);
    setCursorHistory([]);
  };

  const handleTabChange = (newTab: 'traces' | 'logs') => {
    setTab(newTab);
    setCursor(null);
    setCursorHistory([]);
    setSelectedTraceId(null);
    setSelectedLogKey(null);
  };

  useEffect(() => {
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setSelectedTraceId(null);
        setSelectedLogKey(null);
      }
    };
    window.addEventListener('keydown', handleKeyDown);
    return () => window.removeEventListener('keydown', handleKeyDown);
  }, []);

  const activeError = tab === 'traces' ? traceListQuery.error : logListQuery.error;

  return (
    <div className="min-h-screen bg-black p-6 sm:p-8 lg:p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-8 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6">
        <div>
          <h2 className="text-3xl sm:text-4xl font-black tracking-tight text-white mb-2">Explorer</h2>
          <p className="text-slate-500 font-medium">Inspect trace roots and runtime execution timelines.</p>
        </div>
        
        <div className="flex bg-slate-900/60 p-1 rounded-xl border border-slate-800 self-start sm:self-auto">
          <button
            onClick={() => handleTabChange('traces')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${
              tab === 'traces' ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/20' : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            <Activity size={16} />
            Traces
          </button>
          <button
            onClick={() => handleTabChange('logs')}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg text-sm font-bold transition-all ${
              tab === 'logs' ? 'bg-blue-600 text-white shadow-lg shadow-blue-600/20' : 'text-slate-500 hover:text-slate-300'
            }`}
          >
            <ScrollText size={16} />
            Logs
          </button>
        </div>
      </header>

      {activeError ? (
        <div className="mb-6 flex items-center gap-2 rounded-2xl border border-rose-500/20 bg-rose-500/10 p-4 text-sm font-medium text-rose-200">
          <AlertCircle size={16} />
          <span>{getErrorMessage(activeError)}</span>
        </div>
      ) : null}

      <ObservabilityFilterBar
        tab={tab}
        search={search}
        onSearchChange={(v) => { setSearch(v); setCursor(null); setCursorHistory([]); }}
        status={status}
        onStatusChange={(v) => { setStatus(v); setCursor(null); setCursorHistory([]); }}
        level={level}
        onLevelChange={(v) => { setLevel(v); setCursor(null); setCursorHistory([]); }}
        since={since}
        onSinceChange={(v) => { setSince(v); setCursor(null); setCursorHistory([]); }}
        until={until}
        onUntilChange={(v) => { setUntil(v); setCursor(null); setCursorHistory([]); }}
        onQuickRange={setQuickRange}
        onClear={clearTimeRange}
      />

      <div className="bg-slate-900/40 border border-slate-800/60 rounded-3xl overflow-hidden shadow-2xl">
        {tab === 'traces' ? (
          <TraceList
            traces={traces}
            isLoading={traceListQuery.isLoading}
            selectedTraceId={selectedTraceId}
            onSelectTrace={setSelectedTraceId}
            formatTime={formatTime}
            statusClass={statusClass}
          />
        ) : (
          <LogList
            logs={logs}
            isLoading={logListQuery.isLoading}
            selectedLogKey={selectedLogKey}
            onSelectLog={setSelectedLogKey}
            formatTime={formatTime}
            levelClass={levelClass}
          />
        )}

        <div className="border-t border-slate-800/60 px-6 py-4 flex items-center justify-between">
          <div className="text-sm text-slate-500">
            Showing <span className="text-white font-semibold">{tab === 'traces' ? traces.length : logs.length}</span> items
          </div>
          <CursorPagination
            countLabel={`${tab === 'traces' ? traces.length : logs.length} items`}
            pageLabel={`Page ${cursorHistory.length + 1}`}
            canGoBack={canGoBack}
            canGoNext={canGoNext}
            onBack={goToPreviousPage}
            onNext={goToNextPage}
          />
        </div>
      </div>

      {tab === 'traces' && (
        <TimelineDetail
          trace={selectedTrace.data?.data || null}
          timelineItems={timelineItems}
          isLoadingTimeline={selectedTrace.isLoading}
          onClose={() => setSelectedTraceId(null)}
          formatTimestamp={formatTimestamp}
          formatTime={formatTime}
          statusClass={statusClass}
          levelClass={levelClass}
          formatJson={formatJson}
        />
      )}
    </div>
  );
};

export default Observability;
