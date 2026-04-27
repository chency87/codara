import { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { useNavigate, useParams } from 'react-router-dom';
import { ArrowLeft, History, Terminal, ScrollText } from 'lucide-react';
import TerminalOutput from '../components/observability/TerminalOutput';
import type { ApiEnvelope, CliRunMeta, SessionDetail, TaskRecord } from '../types/api';

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

type ArtifactTab = 'task' | 'cli';
type CliStreamTab = 'prompt' | 'stdout' | 'stderr';

const SessionHistory = () => {
  const navigate = useNavigate();
  const { sessionId } = useParams();

  const effectiveSessionId = sessionId ? decodeURIComponent(sessionId) : '';

  const [artifactTab, setArtifactTab] = useState<ArtifactTab>('task');
  const [selectedTaskId, setSelectedTaskId] = useState<string | null>(null);
  const [selectedRunKey, setSelectedRunKey] = useState<string | null>(null);
  const [cliStreamTab, setCliStreamTab] = useState<CliStreamTab>('stdout');

  const { data: session } = useQuery<SessionDetail>({
    queryKey: ['session', effectiveSessionId],
    queryFn: async () => (await axios.get(`/management/v1/sessions/${encodeURIComponent(effectiveSessionId)}`)).data.data,
    enabled: Boolean(effectiveSessionId),
  });

  const { data: tasksEnv, isLoading: tasksLoading } = useQuery<ApiEnvelope<TaskRecord[]>>({
    queryKey: ['session-tasks', effectiveSessionId],
    queryFn: async () => (await axios.get(`/management/v1/sessions/${encodeURIComponent(effectiveSessionId)}/tasks`)).data,
    enabled: Boolean(effectiveSessionId),
  });

  const { data: runsEnv, isLoading: runsLoading } = useQuery<ApiEnvelope<CliRunMeta[]>>({
    queryKey: ['session-cli-runs', effectiveSessionId],
    queryFn: async () =>
      (await axios.get(`/management/v1/sessions/${encodeURIComponent(effectiveSessionId)}/cli-runs`, { params: { limit: 200 } })).data,
    enabled: Boolean(effectiveSessionId),
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

  const { data: cliText, isLoading: cliTextLoading } = useQuery<string>({
    queryKey: ['session-cli-run-text', effectiveSessionId, selectedRun?.provider, selectedRun?.run_id, cliStreamTab],
    queryFn: async () => {
      if (!selectedRun?.provider || !selectedRun?.run_id) return '';
      if (cliStreamTab === 'prompt') {
        return (
          await axios.get(
            `/management/v1/sessions/${encodeURIComponent(effectiveSessionId)}/cli-runs/${encodeURIComponent(selectedRun.provider)}/${encodeURIComponent(selectedRun.run_id)}/prompt`,
          )
        ).data;
      }
      return (
        await axios.get(
          `/management/v1/sessions/${encodeURIComponent(effectiveSessionId)}/cli-runs/${encodeURIComponent(selectedRun.provider)}/${encodeURIComponent(selectedRun.run_id)}/${cliStreamTab}`,
          { params: { max_bytes: 500_000 } },
        )
      ).data;
    },
    enabled: Boolean(effectiveSessionId && selectedRun?.provider && selectedRun?.run_id),
  });

  if (!effectiveSessionId) {
    return (
      <div className="p-12">
        <div className="text-slate-400">Missing session id.</div>
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black p-6 sm:p-8 lg:p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-6 flex flex-col sm:flex-row sm:items-center sm:justify-between gap-6">
        <div className="flex flex-col sm:flex-row sm:items-center gap-4">
          <button
            onClick={() => navigate('/sessions')}
            className="inline-flex items-center justify-center gap-2 rounded-xl border border-slate-800 bg-slate-900/40 px-4 py-2 text-sm font-bold text-slate-300 hover:text-white hover:bg-white/5 transition-all self-start"
          >
            <ArrowLeft size={16} />
            Back
          </button>
          <div>
            <div className="flex items-center gap-2">
              <History size={18} className="text-blue-400" />
              <h2 className="text-2xl font-black tracking-tight text-white">Session History</h2>
            </div>
            <div className="mt-1 text-[11px] font-mono text-slate-500 break-all">
              {effectiveSessionId}
              {session?.provider ? ` · ${session.provider}` : ''}
            </div>
          </div>
        </div>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-[380px_1fr] gap-6">
        <aside className="rounded-3xl border border-slate-800 bg-slate-900/40 overflow-hidden lg:h-fit">
          <div className="flex items-center justify-between border-b border-slate-800 px-4 py-3">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Artifacts</div>
            <div className="flex items-center gap-2">
              <button
                onClick={() => setArtifactTab('task')}
                className={`rounded-xl border px-3 py-1 text-[10px] font-black uppercase tracking-widest transition-all ${
                  artifactTab === 'task'
                    ? 'border-blue-500/30 bg-blue-600/10 text-blue-300'
                    : 'border-slate-800 bg-black/20 text-slate-500 hover:text-white'
                }`}
              >
                Tasks
              </button>
              <button
                onClick={() => setArtifactTab('cli')}
                className={`rounded-xl border px-3 py-1 text-[10px] font-black uppercase tracking-widest transition-all ${
                  artifactTab === 'cli'
                    ? 'border-blue-500/30 bg-blue-600/10 text-blue-300'
                    : 'border-slate-800 bg-black/20 text-slate-500 hover:text-white'
                }`}
              >
                CLI Runs
              </button>
            </div>
          </div>

          <div className="max-h-[calc(100vh-220px)] overflow-auto p-4 space-y-2">
            {artifactTab === 'task' ? (
              tasksLoading ? (
                <div className="text-xs text-slate-600">Loading tasks…</div>
              ) : tasks.length === 0 ? (
                <div className="text-xs text-slate-600">No tasks captured for this session.</div>
              ) : (
                tasks.map((task) => {
                  const active = task.task_id === effectiveSelectedTaskId;
                  const snippet = (task.prompt || '').trim().split('\n')[0] || '(empty prompt)';
                  return (
                    <button
                      key={task.task_id}
                      onClick={() => setSelectedTaskId(task.task_id)}
                      className={`w-full rounded-2xl border px-4 py-3 text-left transition-all ${
                        active
                          ? 'border-blue-500/30 bg-blue-600/10'
                          : 'border-slate-800 bg-black/30 hover:bg-white/5'
                      }`}
                    >
                      <div className="flex items-start justify-between gap-3">
                        <div className="min-w-0">
                          <div className="truncate text-[12px] font-bold text-white">{snippet}</div>
                          <div className="mt-1 text-[10px] text-slate-600">{formatMs(task.created_at)}</div>
                        </div>
                        <div className="text-[9px] font-black uppercase tracking-widest text-slate-500">{task.status}</div>
                      </div>
                    </button>
                  );
                })
              )
            ) : runsLoading ? (
              <div className="text-xs text-slate-600">Loading runs…</div>
            ) : runs.length === 0 ? (
              <div className="text-xs text-slate-600">No CLI runs captured for this session.</div>
            ) : (
              runs.map((run) => {
                const key = `${run.provider}:${run.run_id}`;
                const active = key === effectiveSelectedRunKey;
                return (
                  <button
                    key={key}
                    onClick={() => setSelectedRunKey(key)}
                    className={`w-full rounded-2xl border px-4 py-3 text-left transition-all ${
                      active
                        ? 'border-blue-500/30 bg-blue-600/10'
                        : 'border-slate-800 bg-black/30 hover:bg-white/5'
                    }`}
                  >
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="truncate text-[12px] font-bold text-white">{run.run_id}</div>
                        <div className="mt-1 text-[10px] text-slate-600">{run.started_at || '—'}</div>
                      </div>
                      <div className="text-right">
                        <div className="text-[9px] font-black uppercase tracking-widest text-slate-500">{run.provider}</div>
                        <div className="mt-1 text-[9px] font-black uppercase tracking-widest text-slate-600">{run.status}</div>
                      </div>
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </aside>

        <main className="rounded-3xl border border-slate-800 bg-slate-900/40 overflow-hidden">
          <div className="border-b border-slate-800 px-6 py-4 flex items-center justify-between gap-4">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">
              {artifactTab === 'task' ? 'Task Detail' : 'CLI Run Detail'}
            </div>
          </div>

          <div className="p-6 space-y-6">
            {artifactTab === 'task' ? (
              !selectedTask ? (
                <div className="text-sm text-slate-500">Select a task.</div>
              ) : (
                <div className="space-y-6">
                  <div className="rounded-2xl border border-slate-800 bg-black/20 p-4">
                    <div className="mb-3 flex items-center gap-2">
                      <ScrollText size={14} className="text-blue-400" />
                      <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">User Prompt</div>
                    </div>
                    <pre className="max-h-[360px] overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-4 text-[12px] leading-relaxed text-slate-200 whitespace-pre-wrap">
                      {selectedTask.prompt || '—'}
                    </pre>
                  </div>

                  <div className="rounded-2xl border border-slate-800 bg-black/20 p-4">
                    <div className="mb-3 flex items-center gap-2">
                      <Terminal size={14} className="text-emerald-400" />
                      <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">User Output</div>
                    </div>
                    <pre className="max-h-[520px] overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-4 text-[12px] leading-relaxed text-slate-200 whitespace-pre-wrap">
                      {selectedTask.result?.output || '—'}
                    </pre>
                  </div>

                  {selectedTask.result?.diff ? (
                    <div className="rounded-2xl border border-slate-800 bg-black/20 p-4">
                      <div className="mb-3 text-[10px] font-black uppercase tracking-widest text-slate-500">Workspace Diff</div>
                      <pre className="max-h-[360px] overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-4 text-[11px] leading-relaxed text-slate-200 whitespace-pre-wrap">
                        {selectedTask.result.diff}
                      </pre>
                    </div>
                  ) : null}

                  {selectedTask.result?.actions && selectedTask.result.actions.length > 0 ? (
                    <div className="rounded-2xl border border-slate-800 bg-black/20 p-4">
                      <div className="mb-3 text-[10px] font-black uppercase tracking-widest text-slate-500">Actions</div>
                      <pre className="max-h-[360px] overflow-auto rounded-2xl border border-slate-800 bg-black/40 p-4 text-[11px] leading-relaxed text-slate-200 whitespace-pre-wrap">
                        {JSON.stringify(selectedTask.result.actions, null, 2)}
                      </pre>
                    </div>
                  ) : null}
                </div>
              )
            ) : !selectedRun ? (
              <div className="text-sm text-slate-500">Select a CLI run.</div>
            ) : (
              <div className="space-y-6">
                <div className="rounded-2xl border border-slate-800 bg-black/20 p-4">
                  <div className="mb-3 text-[10px] font-black uppercase tracking-widest text-slate-500">Command</div>
                  <CommandTokens command={selectedRun.command} />
                  <div className="mt-4 grid grid-cols-3 gap-3">
                    <div className="rounded-2xl border border-slate-800 bg-black/30 p-3">
                      <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Status</div>
                      <div className="mt-1 text-xs font-mono text-slate-200">{selectedRun.status || '—'}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-800 bg-black/30 p-3">
                      <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Model</div>
                      <div className="mt-1 text-xs font-mono text-slate-200">{selectedRun.provider_model || '—'}</div>
                    </div>
                    <div className="rounded-2xl border border-slate-800 bg-black/30 p-3">
                      <div className="text-[10px] font-black uppercase tracking-widest text-slate-600">Exit</div>
                      <div className="mt-1 text-xs font-mono text-slate-200">
                        {selectedRun.exit_code === null || selectedRun.exit_code === undefined ? '—' : selectedRun.exit_code}
                      </div>
                    </div>
                  </div>
                  {selectedRun.error ? (
                    <div className="mt-4 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-xs font-medium text-rose-200">
                      Run error: {selectedRun.error}
                    </div>
                  ) : null}
                </div>

                <div className="rounded-2xl border border-slate-800 bg-black/20 overflow-hidden">
                  <div className="flex items-center justify-between gap-3 border-b border-slate-800 px-4 py-3">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Output</div>
                    <div className="flex items-center gap-2">
                      {(['prompt', 'stdout', 'stderr'] as const).map((tab) => (
                        <button
                          key={tab}
                          onClick={() => setCliStreamTab(tab)}
                          className={`rounded-xl border px-3 py-1 text-[10px] font-black uppercase tracking-widest transition-all ${
                            cliStreamTab === tab
                              ? 'border-blue-500/30 bg-blue-600/10 text-blue-300'
                              : 'border-slate-800 bg-black/20 text-slate-500 hover:text-white'
                          }`}
                        >
                          {tab}
                        </button>
                      ))}
                    </div>
                  </div>

                  <div
                    className={`p-4 ${cliStreamTab === 'stderr' ? 'bg-rose-950/10' : ''}`}
                  >
                    {cliTextLoading ? (
                      <div className="text-xs text-slate-600">Loading…</div>
                    ) : (
                      <TerminalOutput
                        content={cliText || ''}
                        maxHeight="520px"
                        stream={cliStreamTab === 'stderr' ? 'stderr' : cliStreamTab === 'prompt' ? 'prompt' : 'stdout'}
                      />
                    )}
                  </div>
                </div>
              </div>
            )}
          </div>
        </main>
      </div>
    </div>
  );
};

export default SessionHistory;
