import React, { useState } from 'react';
import {
  Clock3,
  Fingerprint,
  Bug,
  ChevronDown,
  ChevronRight,
  Circle,
} from 'lucide-react';
import type { TraceRecord, RuntimeLogRecord } from '../../types/api';

interface TimelineDetailProps {
  selectedTrace?: TraceRecord | null;
  selectedLog?: RuntimeLogRecord | null;
  timelineItems: Array<{
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
    attributes?: Record<string, unknown> | null;
    exception?: string | null;
  }>;
}

const formatTimestamp = (value?: string | number | null) => {
  if (value === null || value === undefined || value === '') return '-';
  const date = typeof value === 'number' ? new Date(value) : new Date(value);
  return Number.isNaN(date.getTime()) ? '-' : date.toLocaleTimeString();
};

const formatJson = (value?: Record<string, unknown> | null) => {
  if (!value || Object.keys(value).length === 0) return '// No structured attributes recorded';
  return JSON.stringify(value, null, 2);
};

const levelClass = (value?: string | null) => {
  const level = String(value || '').toUpperCase();
  if (level === 'ERROR') return 'text-rose-300';
  if (level === 'WARNING') return 'text-amber-300';
  return 'text-blue-300';
};

const TimelineItemRow = ({ item, index }: { item: TimelineDetailProps['timelineItems'][0]; index: number }) => {
  const [expanded, setExpanded] = useState(false);
  const hasDetails = (item.attributes && Object.keys(item.attributes).length > 0) || item.exception;

  return (
    <div className="relative">
      <div className="absolute left-3 top-4 w-px bg-slate-700 h-full hidden last:block" />
      <div className="relative flex items-start gap-3 py-2">
        <div className={`mt-2 shrink-0 w-2 h-2 rounded-full ring-4 ring-slate-900 ${
          item.source === 'log' ? 'bg-amber-400' : 'bg-blue-400'
        }`} />
        
        <div className="flex-1 min-w-0">
          <button
            type="button"
            onClick={() => setExpanded(!expanded)}
            className="w-full flex items-center gap-2 text-left hover:bg-white/5 rounded-lg p-1 -ml-1"
          >
            <span className="text-[10px] font-mono text-slate-500 shrink-0">
              {formatTimestamp(item.sortAt)}
            </span>
            <span className={`text-[10px] font-bold uppercase ${levelClass(item.level)}`}>
              {item.source === 'log' ? item.level : item.kind}
            </span>
            <span className="text-sm font-medium text-white truncate">{item.title}</span>
            {hasDetails && (
              expanded ? <ChevronDown size={14} className="text-slate-500 shrink-0" /> : <ChevronRight size={14} className="text-slate-500 shrink-0" />
            )}
          </button>
          
          {expanded && hasDetails && (
            <pre className="mt-2 max-h-40 overflow-auto rounded-lg border border-slate-800 bg-slate-950/80 p-3 text-[10px] font-mono text-slate-400">
              {item.exception || formatJson(item.attributes)}
            </pre>
          )}
        </div>
      </div>
    </div>
  );
};

const TimelineDetail: React.FC<TimelineDetailProps> = ({
  selectedTrace,
  selectedLog,
  timelineItems,
}) => {
  const activeTraceId = selectedTrace?.trace_id || selectedLog?.trace_id || null;

  if (!activeTraceId) {
    return (
      <div className="rounded-3xl border border-dashed border-slate-800 bg-black/20 px-6 py-24 text-center">
        <div className="text-xs font-black uppercase tracking-[0.2em] text-slate-500">
          Select a trace or log to view timeline
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3">
        <div className="rounded-xl border border-slate-800 bg-black/40 p-3">
          <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Trace ID</div>
          <div className="mt-1 text-xs font-mono text-white truncate">{selectedTrace?.trace_id || selectedLog?.trace_id}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-black/40 p-3">
          <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Started</div>
          <div className="mt-1 text-xs font-mono text-white">{formatTimestamp(selectedTrace?.started_at || selectedLog?.timestamp)}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-black/40 p-3">
          <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Duration</div>
          <div className="mt-1 text-xs font-mono text-white">{selectedTrace?.duration_ms ? `${selectedTrace.duration_ms} ms` : '-'}</div>
        </div>
        <div className="rounded-xl border border-slate-800 bg-black/40 p-3">
          <div className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Component</div>
          <div className="mt-1 text-xs font-mono text-white">{selectedTrace?.component || selectedLog?.component || '-'}</div>
        </div>
      </div>

      <div className="rounded-2xl border border-slate-800/60 bg-black/30 p-4">
        <div className="flex items-center justify-between mb-3">
          <div className="flex items-center gap-2">
            <Clock3 size={14} className="text-blue-400" />
            <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">Timeline ({timelineItems.length})</span>
          </div>
        </div>

        {timelineItems.length === 0 ? (
          <div className="py-8 text-center text-xs font-black uppercase tracking-[0.2em] text-slate-600">
            No correlated events
          </div>
        ) : (
          <div className="space-y-1 max-h-[400px] overflow-y-auto">
            {timelineItems.map((item, index) => (
              <TimelineItemRow key={item.id} item={item} index={index} />
            ))}
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-slate-800/60 bg-black/30 p-4">
        <div className="flex items-center gap-2 mb-2">
          <Bug size={14} className="text-amber-400" />
          <span className="text-xs font-black uppercase tracking-[0.2em] text-slate-400">Metadata</span>
        </div>
        <div className="space-y-1 text-[10px] font-mono text-slate-500">
          <div>request_id: {selectedTrace?.request_id || selectedLog?.request_id || '-'}</div>
        </div>
      </div>
    </div>
  );
};

export default TimelineDetail;