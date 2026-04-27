import React from 'react';
import { Fingerprint, X, Clock3, ChevronRight } from 'lucide-react';
import type { TraceRecord } from '../../types/api';

interface TimelineEvent {
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
}

interface TimelineDetailProps {
  trace: TraceRecord | null;
  timelineItems: TimelineEvent[];
  isLoadingTimeline: boolean;
  onClose: () => void;
  formatTimestamp: (value?: string | number | null) => string;
  formatTime: (value?: string | number | null) => string;
  statusClass: (value?: string | null) => string;
  levelClass: (value?: string | null) => string;
  formatJson: (value?: Record<string, unknown> | null) => string;
}

const TimelineRow = ({ 
  item, 
  isLast, 
  formatTime, 
  levelClass, 
  statusClass, 
  formatJson 
}: { 
  item: TimelineEvent; 
  isLast: boolean;
  formatTime: (v?: string | number | null) => string;
  levelClass: (v?: string | null) => string;
  statusClass: (v?: string | null) => string;
  formatJson: (v?: Record<string, unknown> | null) => string;
}) => (
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
          {item.source === 'log' ? `${item.level || 'INFO'}` : item.level || 'ok'}
        </span>
        {item.durationMs !== null && item.durationMs !== undefined ? (
          <span className="text-[9px] font-bold text-slate-400">{item.durationMs}ms</span>
        ) : null}
      </div>
      <div className="text-sm font-medium text-white break-words">{item.title}</div>
      <div className="mt-1 text-xs text-slate-400">{item.subtitle}</div>
      {(item.attributes && Object.keys(item.attributes).length > 0) || item.exception ? (
        <pre className="mt-2 max-h-48 overflow-auto rounded bg-slate-950 p-2 text-[10px] font-mono text-slate-400 whitespace-pre-wrap break-all">
          {item.exception || formatJson(item.attributes)}
        </pre>
      ) : null}
    </div>
  </div>
);

export const TimelineDetail: React.FC<TimelineDetailProps> = ({
  trace,
  timelineItems,
  isLoadingTimeline,
  onClose,
  formatTimestamp,
  formatTime,
  statusClass,
  levelClass,
  formatJson,
}) => {
  if (!trace) return null;

  return (
    <div
      className="absolute inset-0 z-40 bg-black/60"
      onClick={onClose}
    >
      <div
        className="absolute right-0 top-0 bottom-0 w-[480px] bg-slate-900 border-l border-slate-800 shadow-2xl overflow-auto animate-in slide-in-from-right duration-300"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="sticky top-0 z-10 flex items-center justify-between border-b border-slate-800 bg-slate-900 px-6 py-4">
          <div className="flex items-center gap-3">
            <Fingerprint size={18} className="text-blue-400" />
            <div>
              <div className="text-sm font-bold text-white">
                {trace.name || 'Trace Detail'}
              </div>
              <div className="text-[10px] font-mono text-slate-500">{trace.trace_id}</div>
            </div>
          </div>
          <button
            onClick={onClose}
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
                <span className={`inline-flex rounded px-2 py-1 text-xs font-bold uppercase ${statusClass(trace.status)}`}>
                  {trace.status || 'ok'}
                </span>
              </div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-3">
              <div className="text-[10px] font-bold uppercase text-slate-500">Duration</div>
              <div className="mt-1 text-sm font-bold text-white">
                {trace.duration_ms !== null && trace.duration_ms !== undefined ? `${trace.duration_ms}ms` : '—'}
              </div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-3">
              <div className="text-[10px] font-bold uppercase text-slate-500">Started</div>
              <div className="mt-1 text-sm font-bold text-white">
                {formatTimestamp(trace.started_at)}
              </div>
            </div>
            <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-3">
              <div className="text-[10px] font-bold uppercase text-slate-500">Component</div>
              <div className="mt-1 text-sm font-bold text-white">{trace.component}</div>
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-4">
            <div className="mb-4 flex items-center gap-2">
              <Clock3 size={14} className="text-blue-400" />
              <span className="text-xs font-bold uppercase text-slate-400">Timeline</span>
              <span className="text-[10px] text-slate-600">({timelineItems.length} events)</span>
            </div>
            {isLoadingTimeline ? (
              <div className="py-8 text-center text-xs text-slate-500 animate-pulse">
                Loading timeline...
              </div>
            ) : timelineItems.length === 0 ? (
              <div className="py-8 text-center text-xs text-slate-500">
                No timeline events
              </div>
            ) : (
              <div className="space-y-4 max-h-[400px] overflow-auto pr-2">
                {timelineItems.map((item, idx) => (
                  <TimelineRow
                    key={item.id}
                    item={item}
                    isLast={idx === timelineItems.length - 1}
                    formatTime={formatTime}
                    levelClass={levelClass}
                    statusClass={statusClass}
                    formatJson={formatJson}
                  />
                ))}
              </div>
            )}
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-4">
            <div className="mb-3 flex items-center gap-2">
              <ChevronRight size={14} className="text-amber-400" />
              <span className="text-xs font-bold uppercase text-slate-400">Metadata</span>
            </div>
            <div className="space-y-2 text-[10px] font-mono text-slate-400">
              <div className="flex justify-between">
                <span className="text-slate-600">trace_id:</span>
                <span className="text-slate-300">{trace.trace_id}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600">span_id:</span>
                <span className="text-slate-300">{trace.span_id}</span>
              </div>
              <div className="flex justify-between">
                <span className="text-slate-600">request_id:</span>
                <span className="text-slate-300">{trace.request_id || '—'}</span>
              </div>
            </div>
          </div>

          <div className="rounded-lg border border-slate-800 bg-slate-800/30 p-4">
            <div className="mb-3 flex items-center gap-2">
              <ChevronRight size={14} className="text-blue-400" />
              <span className="text-xs font-bold uppercase text-slate-400">Attributes</span>
            </div>
            <pre className="max-h-[200px] overflow-auto rounded bg-slate-950 p-3 text-[10px] font-mono text-slate-400">
              {formatJson(trace.attributes)}
            </pre>
          </div>
        </div>
      </div>
    </div>
  );
};
