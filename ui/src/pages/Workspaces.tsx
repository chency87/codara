import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Activity, Check, Copy, FolderCode, GitBranch, RefreshCw, Trash2, Users, X } from 'lucide-react';
import type { WorkspaceDetailPayload, WorkspaceRecord, WorkspaceSessionBinding, WorkspaceUserBinding } from '../types/api';
import { dashboardPollHeaders } from '../api/dashboardPoll';

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

const formatGitTime = (value?: string | null) => {
  if (!value) return 'unknown';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString();
};

const formatSessionTime = (value: number) => new Date(value * 1000).toLocaleString();

const scopeClass = (scope: string) => {
  if (scope === 'base') return 'border-blue-500/20 bg-blue-500/10 text-blue-300';
  if (scope === 'subworkspace') return 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300';
  return 'border-slate-700 bg-slate-800/50 text-slate-300';
};

const WorkspaceDrawer = ({
  workspaceId,
  onClose,
}: {
  workspaceId: string;
  onClose: () => void;
}) => {
  const queryClient = useQueryClient();
  const [copiedPath, setCopiedPath] = useState(false);

  const { data, isLoading, error } = useQuery<WorkspaceDetailPayload>({
    queryKey: ['workspace-detail', workspaceId],
    queryFn: async () => (await axios.get(`/management/v1/workspaces/${workspaceId}`)).data.data,
    enabled: Boolean(workspaceId),
  });

  const resetMutation = useMutation({
    mutationFn: () => axios.post(`/management/v1/workspaces/${workspaceId}/reset`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      await queryClient.invalidateQueries({ queryKey: ['workspace-detail', workspaceId] });
    },
  });

  const deleteMutation = useMutation({
    mutationFn: () => axios.delete(`/management/v1/workspaces/${workspaceId}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['workspaces'] });
      onClose();
    },
  });

  const copyPath = async () => {
    if (!data?.path) return;
    await navigator.clipboard.writeText(data.path);
    setCopiedPath(true);
    setTimeout(() => setCopiedPath(false), 1500);
  };

  const confirmReset = () => {
    if (window.confirm('Reset this workspace by wiping its bound sessions? Files on disk will be preserved.')) {
      resetMutation.mutate();
    }
  };

  const confirmDelete = () => {
    if (window.confirm('Delete this workspace directory and wipe its bound sessions? This removes files from disk.')) {
      deleteMutation.mutate();
    }
  };

  return (
    <div className="fixed inset-y-0 right-0 z-50 w-full max-w-3xl border-l border-slate-800 bg-slate-950 shadow-2xl">
      <div className="flex items-start justify-between border-b border-slate-800 bg-black/20 p-8">
        <div>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Workspace Detail</div>
          <h3 className="mt-2 text-2xl font-black text-white">{data?.relative_path || data?.name || workspaceId}</h3>
          {data?.path && (
            <button onClick={() => void copyPath()} className="mt-3 flex items-center gap-2 text-xs font-mono text-slate-500 hover:text-slate-300">
              <span>{data.path}</span>
              {copiedPath ? <Check size={12} className="text-emerald-400" /> : <Copy size={12} />}
            </button>
          )}
        </div>
        <button onClick={onClose} className="rounded-xl p-2 text-slate-500 transition-all hover:bg-white/5 hover:text-white">
          <X size={20} />
        </button>
      </div>

      <div className="flex items-center gap-3 border-b border-slate-800 px-8 py-4">
        <button
          onClick={confirmReset}
          disabled={resetMutation.isPending || deleteMutation.isPending}
          className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-white disabled:opacity-50"
        >
          <RefreshCw size={12} className={resetMutation.isPending ? 'animate-spin' : ''} />
          Reset sessions
        </button>
        <button
          onClick={confirmDelete}
          disabled={resetMutation.isPending || deleteMutation.isPending}
          className="inline-flex items-center gap-2 rounded-xl bg-rose-500/10 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-rose-300 disabled:opacity-50"
        >
          <Trash2 size={12} />
          Delete workspace
        </button>
        {(resetMutation.error || deleteMutation.error || error) && (
          <div className="text-xs font-medium text-rose-300">
            {getErrorMessage(resetMutation.error || deleteMutation.error || error)}
          </div>
        )}
      </div>

      <div className="h-[calc(100%-169px)] overflow-auto p-8">
        {isLoading || !data ? (
          <div className="text-xs font-bold uppercase tracking-widest text-slate-500">Loading workspace…</div>
        ) : (
          <div className="space-y-8">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
              <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Scope</div>
                <div className="mt-2 text-lg font-black text-white">{data.scope}</div>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Exists</div>
                <div className="mt-2 text-lg font-black text-white">{data.exists ? 'Yes' : 'No'}</div>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Bound users</div>
                <div className="mt-2 text-lg font-black text-white">{data.bound_users_count}</div>
              </div>
              <div className="rounded-2xl border border-slate-800 bg-slate-900/50 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Bound sessions</div>
                <div className="mt-2 text-lg font-black text-white">{data.bound_sessions_count}</div>
              </div>
            </div>

            <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
              <div className="mb-4 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
                <GitBranch size={14} className="text-blue-400" />
                Git
              </div>
              {data.git.is_git_repo ? (
                <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
                  <div className="rounded-2xl border border-slate-800 bg-black/30 p-4">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Branch</div>
                    <div className="mt-2 text-sm font-bold text-white">{data.git.branch || 'detached'}</div>
                  </div>
                  <div className="rounded-2xl border border-slate-800 bg-black/30 p-4">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Dirty</div>
                    <div className="mt-2 text-sm font-bold text-white">{data.git.dirty ? 'Yes' : 'No'}</div>
                  </div>
                  <div className="rounded-2xl border border-slate-800 bg-black/30 p-4 md:col-span-2">
                    <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">HEAD</div>
                    <div className="mt-2 flex flex-wrap items-center gap-3">
                      <span className="rounded-lg bg-slate-900 px-2.5 py-1 text-xs font-mono text-blue-300">{data.git.short_commit || 'unknown'}</span>
                      <span className="text-sm font-bold text-white">{data.git.head_summary || 'No commit subject'}</span>
                    </div>
                    <div className="mt-2 text-xs text-slate-500">{formatGitTime(data.git.head_committed_at)}</div>
                    {data.git.remote_url && <div className="mt-2 text-xs font-mono text-slate-600 break-all">{data.git.remote_url}</div>}
                  </div>
                </div>
              ) : (
                <div className="rounded-2xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs font-bold uppercase tracking-widest text-slate-600">
                  This workspace is not a git repository
                </div>
              )}
            </section>

            <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
              <div className="mb-4 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
                <Users size={14} className="text-blue-400" />
                Bound users
              </div>
              <div className="space-y-3">
                {data.users.length === 0 ? (
                  <div className="text-xs font-bold uppercase tracking-widest text-slate-600">No bound users</div>
                ) : (
                  data.users.map((user: WorkspaceUserBinding) => (
                    <div key={user.user_id} className="rounded-2xl border border-slate-800 bg-black/30 p-4">
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <div className="text-sm font-bold text-white">{user.display_name}</div>
                          <div className="mt-1 text-[10px] font-mono text-slate-500">{user.email}</div>
                        </div>
                        <div className="flex flex-wrap gap-2">
                          {user.owner && (
                            <span className="rounded-full border border-blue-500/20 bg-blue-500/10 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-blue-300">
                              Owner
                            </span>
                          )}
                          <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                            {user.active_sessions || 0} sessions
                          </span>
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
              <div className="mb-4 flex items-center gap-2 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
                <Activity size={14} className="text-blue-400" />
                Bound sessions
              </div>
              <div className="space-y-3">
                {data.sessions.length === 0 ? (
                  <div className="text-xs font-bold uppercase tracking-widest text-slate-600">No bound sessions</div>
                ) : (
                  data.sessions.map((session: WorkspaceSessionBinding) => (
                    <div key={session.client_session_id} className="rounded-2xl border border-slate-800 bg-black/30 p-4">
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <div className="text-sm font-mono text-white">{session.client_session_id}</div>
                          <div className="mt-1 text-[10px] text-slate-500">
                            {session.user_display_name || 'System / Unbound'} · {session.provider} · {session.api_key_label || 'No key binding'}
                          </div>
                        </div>
                        <div className="text-right">
                          <div className="text-[10px] font-black uppercase tracking-widest text-slate-400">{session.status}</div>
                          <div className="mt-1 text-[10px] text-slate-600">{formatSessionTime(session.updated_at)}</div>
                        </div>
                      </div>
                      <div className="mt-2 text-[10px] text-slate-500 break-all">{session.cwd_path}</div>
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>
        )}
      </div>
    </div>
  );
};

const Workspaces = () => {
  const [selectedWorkspaceId, setSelectedWorkspaceId] = useState<string | null>(null);
  const [search, setSearch] = useState('');

  const { data, isLoading, error } = useQuery<WorkspaceRecord[]>({
    queryKey: ['workspaces'],
    queryFn: async () => (await axios.get('/management/v1/workspaces', { headers: dashboardPollHeaders })).data.data,
    refetchInterval: 30000,
  });

  const workspaces = useMemo(() => data || [], [data]);
  const filtered = useMemo(() => {
    const needle = search.trim().toLowerCase();
    if (!needle) return workspaces;
    return workspaces.filter((workspace) =>
      [workspace.name, workspace.path, workspace.relative_path, workspace.scope, ...workspace.owners.map((owner) => owner.display_name)]
        .filter(Boolean)
        .some((value) => String(value).toLowerCase().includes(needle))
    );
  }, [search, workspaces]);

  const totals = useMemo(() => ({
    total: workspaces.length,
    git: workspaces.filter((workspace) => workspace.git.is_git_repo).length,
    dirty: workspaces.filter((workspace) => workspace.git.dirty).length,
    sessions: workspaces.reduce((sum, workspace) => sum + (workspace.bound_sessions_count || 0), 0),
  }), [workspaces]);

  if (isLoading) {
    return <div className="p-12 text-slate-500 font-bold uppercase tracking-widest text-xs animate-pulse">Loading workspaces…</div>;
  }

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-8 flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-4xl font-black tracking-tight text-white mb-2">Workspace Management</h2>
          <p className="text-slate-500 font-medium">Inspect provisioned workspaces, git state, bound users, and bound sessions.</p>
        </div>
        <div className="w-full max-w-sm">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search by path, scope, or owner"
            className="w-full rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-3 text-sm text-white placeholder:text-slate-500"
          />
        </div>
      </header>

      <div className="mb-8 grid grid-cols-1 gap-6 md:grid-cols-4">
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Workspaces</div>
          <div className="text-3xl font-black text-white">{totals.total}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Git repos</div>
          <div className="text-3xl font-black text-white">{totals.git}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Dirty repos</div>
          <div className="text-3xl font-black text-white">{totals.dirty}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Bound sessions</div>
          <div className="text-3xl font-black text-white">{totals.sessions}</div>
        </div>
      </div>

      {error && (
        <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          Workspace loading failed: {getErrorMessage(error)}
        </div>
      )}

      <div className="rounded-3xl border border-slate-800/60 bg-slate-900/40 overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full border-collapse text-left">
            <thead>
              <tr className="border-b border-slate-800/60 bg-slate-800/30">
                <th className="px-8 py-5 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Workspace</th>
                <th className="px-8 py-5 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Scope</th>
                <th className="px-8 py-5 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Git</th>
                <th className="px-8 py-5 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Bindings</th>
                <th className="px-8 py-5 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Owners</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40 text-slate-300">
              {filtered.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-8 py-24 text-center">
                    <div className="flex flex-col items-center justify-center space-y-4">
                      <div className="rounded-full bg-slate-800/50 p-4 text-slate-600">
                        <FolderCode size={32} />
                      </div>
                      <p className="text-xs font-bold uppercase tracking-widest text-slate-500">No workspaces matched</p>
                    </div>
                  </td>
                </tr>
              ) : (
                filtered.map((workspace) => (
                  <tr
                    key={workspace.workspace_id}
                    onClick={() => setSelectedWorkspaceId(workspace.workspace_id)}
                    className="cursor-pointer transition-colors hover:bg-white/[0.02]"
                  >
                    <td className="px-8 py-6">
                      <div className="flex items-center gap-4">
                        <div className="rounded-2xl bg-slate-800 p-3 text-blue-400">
                          <FolderCode size={20} />
                        </div>
                        <div>
                          <div className="font-black text-white">{workspace.relative_path || workspace.name}</div>
                          <div className="mt-1 text-[10px] font-mono text-slate-500">{workspace.path}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-8 py-6">
                      <span className={`inline-flex rounded-full border px-3 py-1 text-[10px] font-black uppercase tracking-widest ${scopeClass(workspace.scope)}`}>
                        {workspace.scope}
                      </span>
                    </td>
                    <td className="px-8 py-6">
                      {workspace.git.is_git_repo ? (
                        <div className="space-y-1">
                          <div className="inline-flex items-center gap-2 rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                            <GitBranch size={11} />
                            {workspace.git.branch || 'detached'}
                          </div>
                          <div className="text-[10px] font-mono text-slate-500">
                            {workspace.git.short_commit || 'no-head'} {workspace.git.dirty ? '• dirty' : ''}
                          </div>
                        </div>
                      ) : (
                        <div className="text-xs text-slate-600">non-git</div>
                      )}
                    </td>
                    <td className="px-8 py-6">
                      <div className="flex flex-wrap gap-2">
                        <span className="inline-flex items-center gap-1 rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                          <Users size={11} />
                          {workspace.bound_users_count}
                        </span>
                        <span className="inline-flex items-center gap-1 rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                          <Activity size={11} />
                          {workspace.bound_sessions_count}
                        </span>
                      </div>
                    </td>
                    <td className="px-8 py-6">
                      <div className="space-y-1">
                        {workspace.owners.length === 0 ? (
                          <div className="text-xs text-slate-600">No owner</div>
                        ) : (
                          workspace.owners.map((owner) => (
                            <div key={owner.user_id} className="text-sm font-bold text-white">{owner.display_name}</div>
                          ))
                        )}
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selectedWorkspaceId && (
        <WorkspaceDrawer workspaceId={selectedWorkspaceId} onClose={() => setSelectedWorkspaceId(null)} />
      )}
    </div>
  );
};

export default Workspaces;
