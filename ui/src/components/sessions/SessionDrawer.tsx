import { useEffect, useMemo, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Terminal, X, Database, Cpu, Hash, History } from 'lucide-react';
import type { ApiEnvelope, SessionDetail, SessionTurn, CliRunMeta, TaskRecord } from '../../types/api';
import { dashboardPollHeaders } from '../../api/dashboardPoll';
import TerminalOutput from '../observability/TerminalOutput';

interface SessionDrawerProps {
  sessionId: string;
  onClose: () => void;
}

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

const LiveCliOutput = ({ sessionId }: { sessionId: string }) => {
  const [activeStream, setActiveStream] = useState<'stdout' | 'stderr'>('stdout');
  const [output, setOutput] = useState<string>('');
  const [streamError, setStreamError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const outputRef = useRef<HTMLPreElement | null>(null);
  const pendingChunksRef = useRef<string[]>([]);
  const flushTimerRef = useRef<number | null>(null);

  const { data: runs } = useQuery({
    queryKey: ['cli-runs', sessionId],
    queryFn: async (): Promise<CliRunMeta[]> =>
      (await axios.get(`/management/v1/sessions/${sessionId}/cli-runs`, { params: { status: 'running', limit: 1 } })).data.data,
    refetchInterval: 2000,
  });

  const currentRun = useMemo(() => (runs && runs.length > 0 ? runs[0] : null), [runs]);
  const runKey = currentRun ? `${currentRun.provider}:${currentRun.run_id}` : null;

  useEffect(() => {
    setOutput('');
    setStreamError(null);
    abortRef.current?.abort();
    abortRef.current = null;
    pendingChunksRef.current = [];
    if (flushTimerRef.current !== null) {
      window.clearInterval(flushTimerRef.current);
      flushTimerRef.current = null;
    }

    if (!currentRun?.provider || !currentRun?.run_id) return;

    const controller = new AbortController();
    abortRef.current = controller;

    const token = sessionStorage.getItem('uag_token');
    const url = `/management/v1/sessions/${encodeURIComponent(sessionId)}/cli-runs/${encodeURIComponent(currentRun.provider)}/${encodeURIComponent(currentRun.run_id)}/${activeStream}/stream?tail_bytes=65536&follow=true&poll_ms=200`;

    (async () => {
      try {
        const flush = () => {
          if (pendingChunksRef.current.length === 0) return;
          const delta = pendingChunksRef.current.join('');
          pendingChunksRef.current.length = 0;
          setOutput(prev => {
            const next = prev + delta;
            return next.length > 200000 ? next.slice(next.length - 200000) : next;
          });
        };

        flushTimerRef.current = window.setInterval(flush, 100);

        const resp = await fetch(url, {
          method: 'GET',
          headers: {
            ...dashboardPollHeaders,
            ...(token ? { Authorization: `Bearer ${token}` } : {}),
          },
          signal: controller.signal,
        });
        if (!resp.ok) {
          const text = await resp.text();
          throw new Error(text || `Stream failed (${resp.status})`);
        }
        if (!resp.body) return;
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          if (!value) continue;
          const chunk = decoder.decode(value, { stream: true });
          if (chunk) {
            pendingChunksRef.current.push(chunk);
            if (pendingChunksRef.current.length >= 64) flush();
          }
        }

        const finalText = decoder.decode();
        if (finalText) pendingChunksRef.current.push(finalText);
        flush();
      } catch (err) {
        if (
          err &&
          typeof err === 'object' &&
          'name' in err &&
          typeof (err as { name?: unknown }).name === 'string' &&
          (err as { name: string }).name === 'AbortError'
        ) {
          return;
        }
        setStreamError(getErrorMessage(err));
      } finally {
        if (flushTimerRef.current !== null) {
          window.clearInterval(flushTimerRef.current);
          flushTimerRef.current = null;
        }
        pendingChunksRef.current.length = 0;
      }
    })();

    return () => {
      controller.abort();
      if (flushTimerRef.current !== null) {
        window.clearInterval(flushTimerRef.current);
        flushTimerRef.current = null;
      }
      pendingChunksRef.current.length = 0;
    };
  }, [runKey, activeStream, sessionId, currentRun]);

  useEffect(() => {
    if (!outputRef.current) return;
    outputRef.current.scrollTop = outputRef.current.scrollHeight;
  }, [output]);

  return (
    <div>
      <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-4 flex items-center space-x-2">
        <Terminal size={14} className="text-blue-500" />
        <span>Live CLI Output</span>
      </h4>

      {!currentRun ? (
        <div className="rounded-2xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs font-bold uppercase tracking-widest text-slate-600">
          No active CLI run for this session
        </div>
      ) : (
        <div className="space-y-3">
          <div className="flex items-center justify-between gap-3">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">
              {currentRun.provider} · {currentRun.run_id}
            </div>
            <div className="flex items-center gap-2">
              {(['stdout', 'stderr'] as const).map((tab) => (
                <button
                  key={tab}
                  onClick={() => setActiveStream(tab)}
                  className={`rounded-xl border px-3 py-1 text-[10px] font-black uppercase tracking-widest transition-all ${
                    activeStream === tab
                      ? 'border-blue-500/30 bg-blue-600/10 text-blue-300'
                      : 'border-slate-800 bg-black/20 text-slate-500 hover:text-white'
                  }`}
                >
                  {tab}
                </button>
              ))}
            </div>
          </div>

          {streamError && (
            <div className="rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-xs font-medium text-rose-200">
              Stream error: {streamError}
            </div>
          )}

          <pre
            ref={outputRef}
            className="h-56 overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-4 text-[11px] leading-relaxed text-slate-200"
          >
            {output || '…'}
          </pre>
        </div>
      )}
    </div>
  );
};

const formatMs = (value?: number | null) => {
  if (!value) return '—';
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? '—' : date.toLocaleString();
};

const CommandTokens = ({ command }: { command?: string[] | null }) => {
  const parts = command || [];
  if (parts.length === 0) return <div className="text-xs text-slate-600">—</div>;
  return (
    <div className="overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-3 font-mono text-[10px] whitespace-pre break-all">
      {parts.map((part, idx) => {
        const isExe = idx === 0;
        const isFlag = part.startsWith('-');
        const className = isExe
          ? 'text-cyan-300 font-bold'
          : isFlag
            ? 'text-amber-300 font-semibold'
            : 'text-slate-200';
        return (
          <span key={`${idx}-${part}`} className={className}>
            {part}
            {idx < parts.length - 1 ? ' ' : ''}
          </span>
        );
      })}
    </div>
  );
};

const HistoryExplorer = ({ sessionId }: { sessionId: string }) => {
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedRunKey, setSelectedRunKey] = useState<string | null>(null);
  const [runStream, setRunStream] = useState<'stdout' | 'stderr' | 'prompt'>('stdout');

  const { data: tasksEnv, isLoading: tasksLoading } = useQuery<ApiEnvelope<TaskRecord[]>>({
    queryKey: ['session-tasks', sessionId],
    queryFn: async () => (await axios.get(`/management/v1/sessions/${sessionId}/tasks`)).data,
  });

  const { data: runsEnv, isLoading: runsLoading } = useQuery<ApiEnvelope<CliRunMeta[]>>({
    queryKey: ['session-cli-runs', sessionId],
    queryFn: async () =>
      (await axios.get(`/management/v1/sessions/${sessionId}/cli-runs`, { params: { limit: 50 } })).data,
    refetchInterval: 5000,
  });

  const tasks = useMemo(() => tasksEnv?.data || [], [tasksEnv]);
  const runs = useMemo(() => runsEnv?.data || [], [runsEnv]);

  const effectiveSelectedTaskId = selectedTaskId || (tasks.length > 0 ? tasks[tasks.length - 1].task_id : null);
  const effectiveSelectedRunKey = selectedRunKey || (runs.length > 0 ? `${runs[0].provider}:${runs[0].run_id}` : null);

  const selectedTask = useMemo(
    () => (effectiveSelectedTaskId ? tasks.find((t) => t.task_id === effectiveSelectedTaskId) || null : null),
    [effectiveSelectedTaskId, tasks],
  );

  const selectedRun = useMemo(() => {
    if (!effectiveSelectedRunKey) return null;
    const [provider, runId] = effectiveSelectedRunKey.split(':', 2);
    if (!provider || !runId) return null;
    return runs.find((r) => r.provider === provider && r.run_id === runId) || null;
  }, [effectiveSelectedRunKey, runs]);

  const { data: runText, isLoading: runTextLoading } = useQuery<string>({
    queryKey: ['session-cli-run-text', sessionId, selectedRun?.provider, selectedRun?.run_id, runStream],
    queryFn: async () => {
      if (!selectedRun?.provider || !selectedRun?.run_id) return '';
      if (runStream === 'prompt') {
        return (
          await axios.get(
            `/management/v1/sessions/${encodeURIComponent(sessionId)}/cli-runs/${encodeURIComponent(selectedRun.provider)}/${encodeURIComponent(selectedRun.run_id)}/prompt`,
          )
        ).data;
      }
      return (
        await axios.get(
          `/management/v1/sessions/${encodeURIComponent(sessionId)}/cli-runs/${encodeURIComponent(selectedRun.provider)}/${encodeURIComponent(selectedRun.run_id)}/${runStream}`,
          { params: { max_bytes: 200_000 } },
        )
      ).data;
    },
    enabled: Boolean(selectedRun?.provider && selectedRun?.run_id),
  });

  return (
    <div className="space-y-6">
      <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-2 flex items-center space-x-2">
        <History size={14} className="text-blue-500" />
        <span>History Explorer</span>
      </h4>

      <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
        <div className="rounded-2xl border border-slate-800 bg-black/20 p-4">
          <div className="mb-3 text-[10px] font-black uppercase tracking-widest text-slate-500">User Prompts</div>
          {tasksLoading ? (
            <div className="text-xs text-slate-600">Loading tasks…</div>
          ) : tasks.length === 0 ? (
            <div className="text-xs text-slate-600">No tasks captured for this session.</div>
          ) : (
            <div className="max-h-40 space-y-2 overflow-auto pr-1">
              {tasks.map((task) => {
                const isActive = task.task_id === effectiveSelectedTaskId;
                const snippet = (task.prompt || '').trim().split('\n')[0] || '(empty prompt)';
                return (
                  <button
                    key={task.task_id}
                    onClick={() => setSelectedTaskId(task.task_id)}
                    className={`w-full rounded-xl border px-3 py-2 text-left transition-all ${
                      isActive ? 'border-blue-500/30 bg-blue-600/10' : 'border-slate-800 bg-black/30 hover:bg-white/5'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="truncate text-[11px] font-bold text-white">{snippet}</div>
                      <div className="text-[9px] font-black uppercase tracking-widest text-slate-500">{task.status}</div>
                    </div>
                    <div className="mt-1 text-[10px] text-slate-600">{formatMs(task.created_at)}</div>
                  </button>
                );
              })}
            </div>
          )}
        </div>

        <div className="rounded-2xl border border-slate-800 bg-black/20 p-4">
          <div className="mb-3 text-[10px] font-black uppercase tracking-widest text-slate-500">CLI Runs</div>
          {runsLoading ? (
            <div className="text-xs text-slate-600">Loading runs…</div>
          ) : runs.length === 0 ? (
            <div className="text-xs text-slate-600">No CLI runs captured for this session.</div>
          ) : (
            <div className="max-h-40 space-y-2 overflow-auto pr-1">
              {runs.map((run) => {
                const key = `${run.provider}:${run.run_id}`;
                const isActive = key === effectiveSelectedRunKey;
                return (
                  <button
                    key={key}
                    onClick={() => setSelectedRunKey(key)}
                    className={`w-full rounded-xl border px-3 py-2 text-left transition-all ${
                      isActive ? 'border-blue-500/30 bg-blue-600/10' : 'border-slate-800 bg-black/30 hover:bg-white/5'
                    }`}
                  >
                    <div className="flex items-center justify-between gap-2">
                      <div className="truncate text-[11px] font-bold text-white">
                        {run.provider} · {run.run_id}
                      </div>
                      <div className="text-[9px] font-black uppercase tracking-widest text-slate-500">{run.status}</div>
                    </div>
                    <div className="mt-1 text-[10px] text-slate-600">{run.started_at || '—'}</div>
                  </button>
                );
              })}
            </div>
          )}
        </div>
      </div>

      <div className="rounded-2xl border border-slate-800 bg-black/20 p-4 space-y-4">
        <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Selected Task</div>
        {!selectedTask ? (
          <div className="text-xs text-slate-600">Select a task to inspect prompt and response.</div>
        ) : (
          <div className="space-y-3">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Prompt</div>
            <pre className="max-h-40 overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-4 text-[11px] leading-relaxed text-slate-200 whitespace-pre-wrap">
              {selectedTask.prompt || '—'}
            </pre>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">User Output</div>
            <pre className="max-h-56 overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-4 text-[11px] leading-relaxed text-slate-200 whitespace-pre-wrap">
              {selectedTask.result?.output || '—'}
            </pre>
          </div>
        )}
      </div>

      <div className="rounded-2xl border border-slate-800 bg-black/20 p-4 space-y-4">
        <div className="flex items-center justify-between gap-3">
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Selected CLI Run</div>
          <div className="flex items-center gap-2">
            {(['prompt', 'stdout', 'stderr'] as const).map((tab) => (
              <button
                key={tab}
                onClick={() => setRunStream(tab)}
                className={`rounded-xl border px-3 py-1 text-[10px] font-black uppercase tracking-widest transition-all ${
                  runStream === tab
                    ? 'border-blue-500/30 bg-blue-600/10 text-blue-300'
                    : 'border-slate-800 bg-black/20 text-slate-500 hover:text-white'
                }`}
              >
                {tab}
              </button>
            ))}
          </div>
        </div>

        {!selectedRun ? (
          <div className="text-xs text-slate-600">Select a captured run to inspect command and outputs.</div>
        ) : (
          <div className="space-y-3">
            <div>
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Command</div>
              <div className="mt-2">
                <CommandTokens command={selectedRun.command} />
              </div>
            </div>

            <div className="grid grid-cols-2 gap-3">
              <div className="rounded-2xl border border-slate-800 bg-black/40 p-3">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Model</div>
                <div className="mt-1 text-xs font-mono text-slate-200">{selectedRun.provider_model || '—'}</div>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-black/40 p-3">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Attempt</div>
                <div className="mt-1 text-xs font-mono text-slate-200">{selectedRun.attempt || '—'}</div>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-black/40 p-3">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Started</div>
                <div className="mt-1 text-xs font-mono text-slate-200">{selectedRun.started_at || '—'}</div>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-black/40 p-3">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Ended</div>
                <div className="mt-1 text-xs font-mono text-slate-200">{selectedRun.ended_at || '—'}</div>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-black/40 p-3">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Exit</div>
                <div className="mt-1 text-xs font-mono text-slate-200">
                  {selectedRun.exit_code === null || selectedRun.exit_code === undefined ? '—' : selectedRun.exit_code}
                </div>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-black/40 p-3">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Trace</div>
                <div className="mt-1 text-xs font-mono text-slate-200 truncate">{selectedRun.trace_id || '—'}</div>
              </div>
            </div>

            {runStream === 'prompt' ? (
              <pre className="max-h-56 overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-4 text-[11px] leading-relaxed text-slate-200 whitespace-pre-wrap">
                {runTextLoading ? 'Loading…' : runText || '—'}
              </pre>
            ) : (
              <div
                className={`max-h-72 overflow-auto rounded-2xl border bg-black/40 p-4 ${
                  runStream === 'stderr' ? 'border-rose-500/20' : 'border-slate-800'
                }`}
              >
                {runTextLoading ? (
                  <div className="text-xs text-slate-600">Loading…</div>
                ) : (
                  <TerminalOutput
                    content={runText || ''}
                    maxHeight="260px"
                    stream={runStream === 'stderr' ? 'stderr' : 'stdout'}
                  />
                )}
              </div>
            )}
            {selectedRun.error ? (
              <div className="rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-xs font-medium text-rose-200">
                Run error: {selectedRun.error}
              </div>
            ) : null}
          </div>
        )}
      </div>
    </div>
  );
};

export const SessionDrawer = ({ sessionId, onClose }: SessionDrawerProps) => {
  const { data: session } = useQuery({
    queryKey: ['session', sessionId],
    queryFn: async (): Promise<SessionDetail> => (await axios.get(`/management/v1/sessions/${sessionId}`)).data.data
  });

  const { data: turns } = useQuery({
    queryKey: ['turns', sessionId],
    queryFn: async (): Promise<SessionTurn[]> => (await axios.get(`/management/v1/sessions/${sessionId}/turns`)).data.data
  });

  if (!session) return null;

  return (
    <div className="fixed inset-y-0 right-0 w-full sm:w-2/3 lg:w-1/2 xl:w-1/3 bg-slate-900 border-l border-slate-800 shadow-2xl z-50 animate-in slide-in-from-right duration-300 flex flex-col">
      <div className="p-6 sm:p-8 border-b border-slate-800 flex justify-between items-center bg-black/20">
        <div className="min-w-0">
          <h3 className="text-xl font-black text-white tracking-tight">Thread Inspector</h3>
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mt-1 truncate">ID: {sessionId}</p>
        </div>
        <button onClick={onClose} className="p-2 hover:bg-white/5 rounded-xl text-slate-500 hover:text-white transition-all shrink-0">
          <X size={20} />
        </button>
      </div>

	      <div className="flex-1 overflow-auto p-6 sm:p-8 space-y-10 custom-scrollbar">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="p-4 bg-slate-800/30 rounded-2xl border border-slate-700/20">
            <div className="flex items-center space-x-2 text-slate-500 mb-1 uppercase text-[10px] font-black">
              <Database size={12} />
              <span>Provider</span>
            </div>
            <div className="text-sm font-bold text-white">{session.provider}</div>
          </div>
          <div className="p-4 bg-slate-800/30 rounded-2xl border border-slate-700/20">
            <div className="flex items-center space-x-2 text-slate-500 mb-1 uppercase text-[10px] font-black">
              <Cpu size={12} />
              <span>Bound User</span>
            </div>
            <div className="text-sm font-bold text-white">{session.user_display_name || 'System / Unbound'}</div>
            <div className="text-[10px] font-mono text-slate-500 mt-1 truncate">{session.user_email || session.api_key_label || 'No user key recorded'}</div>
          </div>
        </div>

        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <div className="p-4 bg-slate-800/30 rounded-2xl border border-slate-700/20">
            <div className="flex items-center space-x-2 text-slate-500 mb-1 uppercase text-[10px] font-black">
              <Cpu size={12} />
              <span>Backend ID</span>
            </div>
            <div className="text-sm font-mono text-blue-400 truncate">{session.backend_id || 'null'}</div>
          </div>
          <div className="p-4 bg-slate-800/30 rounded-2xl border border-slate-700/20">
            <div className="flex items-center space-x-2 text-slate-500 mb-1 uppercase text-[10px] font-black">
              <Hash size={12} />
              <span>API Key</span>
            </div>
            <div className="text-sm font-bold text-white">{session.api_key_label || 'No key binding'}</div>
            <div className="text-[10px] font-mono text-slate-500 mt-1">{session.api_key_prefix ? `${session.api_key_prefix}••••` : '—'}</div>
          </div>
        </div>

	        <div>
          <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] mb-6 flex items-center space-x-2">
            <History size={14} className="text-blue-500" />
            <span>Turn Execution Ledger</span>
          </h4>
          <div className="space-y-4">
            {turns?.length === 0 ? (
              <p className="text-xs text-slate-600 font-bold uppercase tracking-widest italic py-4">No turns recorded yet</p>
            ) : (
              turns?.map((turn, i: number) => (
                <div key={turn.turn_id} className="p-4 bg-black/40 border border-slate-800 rounded-2xl space-y-3">
                  <div className="flex justify-between items-center">
                    <span className="text-[10px] font-black text-slate-500 uppercase">Turn #{i + 1}</span>
                  </div>
                  <div className="text-xs text-slate-400 font-mono line-clamp-2 bg-slate-900/50 p-2 rounded-lg">
                    {turn.diff ? turn.diff.substring(0, 100) + '...' : 'No workspace modifications'}
                  </div>
                  <div className="flex justify-between items-center text-[10px] text-slate-600 font-bold uppercase">
                    <span>{new Date(turn.timestamp * 1000).toLocaleTimeString()}</span>
                    <span>{turn.finish_reason}</span>
                  </div>
                </div>
              ))
            )}
          </div>
	        </div>

	        <HistoryExplorer sessionId={sessionId} />

	        <LiveCliOutput sessionId={sessionId} />
	      </div>
	    </div>
	  );
};

export default SessionDrawer;
