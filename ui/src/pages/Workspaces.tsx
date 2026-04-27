import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Activity, FolderCode, GitBranch, Users } from 'lucide-react';
import type { WorkspaceRecord } from '../types/api';
import { dashboardPollHeaders } from '../api/dashboardPoll';
import { WorkspaceDrawer } from '../components/workspaces/WorkspaceDrawer';

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

const scopeClass = (scope: string) => {
  if (scope === 'base') return 'border-blue-500/20 bg-blue-500/10 text-blue-300';
  if (scope === 'subworkspace') return 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300';
  return 'border-slate-700 bg-slate-800/50 text-slate-300';
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
    <div className="p-6 sm:p-8 lg:p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-8 flex flex-col gap-6 lg:flex-row lg:items-end lg:justify-between">
        <div className="flex-1">
          <h2 className="text-3xl sm:text-4xl font-black tracking-tight text-white mb-2">Workspace Management</h2>
          <p className="text-slate-500 font-medium">Inspect provisioned workspaces, git state, bound users, and bound sessions.</p>
        </div>
        <div className="w-full lg:max-w-sm">
          <input
            value={search}
            onChange={(event) => setSearch(event.target.value)}
            placeholder="Search by path, scope, or owner"
            className="w-full rounded-2xl border border-slate-800 bg-slate-900/60 px-4 py-3 text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500/50"
          />
        </div>
      </header>

      <div className="mb-8 grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
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