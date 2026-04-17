import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Copy, KeyRound, Plus, RefreshCw, UserCog, Users as UsersIcon, X } from 'lucide-react';
import type {
  ChannelLinkTokenResponse,
  CreateUserResponse,
  RotateUserKeyResponse,
  UsageSummaryPayload,
  UserActivityRecord,
  UserDetailPayload,
  UserSessionRecord,
  UserSummary,
  WorkspaceResetRecord,
  ApiKeyRecord,
} from '../types/api';
import { dashboardPollHeaders } from '../api/dashboardPoll';

const badgeClass = (status: string) => {
  if (status === 'active') return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
  if (status === 'suspended') return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
  return 'bg-rose-500/10 text-rose-400 border-rose-500/20';
};

const formatDate = (value?: string | null) => (value ? new Date(value).toLocaleString() : '—');

const copyToClipboard = async (value: string) => {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement('textarea');
  input.value = value;
  document.body.appendChild(input);
  input.select();
  document.execCommand('copy');
  document.body.removeChild(input);
};

const Users = () => {
  const queryClient = useQueryClient();
  const [selectedUserId, setSelectedUserId] = useState<string | null>(null);
  const [showCreate, setShowCreate] = useState(false);
  const [newUser, setNewUser] = useState({ email: '', display_name: '', max_concurrency: 3 });
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [revealedKeyOwner, setRevealedKeyOwner] = useState<string | null>(null);
  const [revealedKeyUserId, setRevealedKeyUserId] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedKeyTarget, setCopiedKeyTarget] = useState<string | null>(null);
  const [channelExpiry, setChannelExpiry] = useState(30);
  const [channelBotName, setChannelBotName] = useState('engineering-bot');
  const [revealedChannelToken, setRevealedChannelToken] = useState<ChannelLinkTokenResponse | null>(null);
  const [copiedChannelToken, setCopiedChannelToken] = useState(false);
  const [channelFeedback, setChannelFeedback] = useState<string | null>(null);
  const [workspaceFeedback, setWorkspaceFeedback] = useState<string | null>(null);

  const { data: users, isLoading } = useQuery<UserSummary[]>({
    queryKey: ['users'],
    queryFn: async () => (await axios.get('/management/v1/users', { headers: dashboardPollHeaders })).data.data,
    refetchInterval: 30000,
  });

  const { data: usage } = useQuery<UsageSummaryPayload>({
    queryKey: ['management-usage'],
    queryFn: async () => (await axios.get('/management/v1/usage', { headers: dashboardPollHeaders })).data.data,
    refetchInterval: 30000,
  });

  const selectedUser = useQuery<UserDetailPayload>({
    queryKey: ['user-detail', selectedUserId],
    queryFn: async () => (await axios.get(`/management/v1/users/${selectedUserId}`)).data.data,
    enabled: !!selectedUserId,
  });

  const provisionMutation = useMutation({
    mutationFn: (payload: typeof newUser) => axios.post('/management/v1/users', payload),
    onSuccess: (resp: { data: { data: CreateUserResponse } }) => {
      setRevealedKey(resp.data.data.api_key.raw_key);
      setRevealedKeyOwner(resp.data.data.display_name);
      setRevealedKeyUserId(resp.data.data.user_id);
      setCopiedKey(false);
      setCopiedKeyTarget(null);
      setShowCreate(false);
      setNewUser({ email: '', display_name: '', max_concurrency: 3 });
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.invalidateQueries({ queryKey: ['management-usage'] });
    },
  });

  const rotateKeyMutation = useMutation({
    mutationFn: (userId: string) => axios.post(`/management/v1/users/${userId}/keys/rotate`, { label: 'rotated' }),
    onSuccess: (resp: { data: { data: RotateUserKeyResponse } }) => {
      setRevealedKey(resp.data.data.raw_key);
      setRevealedKeyOwner(selectedUser.data?.display_name || 'User');
      setRevealedKeyUserId(selectedUserId);
      setCopiedKey(false);
      setCopiedKeyTarget(null);
      queryClient.invalidateQueries({ queryKey: ['users'] });
      if (selectedUserId) {
        queryClient.invalidateQueries({ queryKey: ['user-detail', selectedUserId] });
      }
    },
  });

  const suspendMutation = useMutation({
    mutationFn: (userId: string) => axios.post(`/management/v1/users/${userId}/suspend`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  });

  const unsuspendMutation = useMutation({
    mutationFn: (userId: string) => axios.post(`/management/v1/users/${userId}/unsuspend`),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ['users'] }),
  });

  const deleteMutation = useMutation({
    mutationFn: (userId: string) => axios.delete(`/management/v1/users/${userId}`),
    onSuccess: () => {
      setSelectedUserId(null);
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
  });

  const createChannelTokenMutation = useMutation({
    mutationFn: (payload: { userId: string; botName: string; expiresInMinutes: number }) =>
      axios.post(`/management/v1/users/${payload.userId}/channels/link-token`, {
        channel: 'telegram',
        bot_name: payload.botName,
        expires_in_minutes: payload.expiresInMinutes,
      }),
    onMutate: () => {
      setChannelFeedback(null);
    },
    onSuccess: (resp: { data: { data: ChannelLinkTokenResponse } }) => {
      setRevealedChannelToken(resp.data.data);
      setCopiedChannelToken(false);
      setChannelFeedback(null);
    },
    onError: (error) => {
      const detail = axios.isAxiosError(error)
        ? error.response?.data?.detail || error.message
        : error instanceof Error
          ? error.message
          : 'Failed to create link token';
      setChannelFeedback(detail);
    },
  });

  const resetWorkspaceMutation = useMutation({
    mutationFn: (userId: string) => axios.post(`/management/v1/users/${userId}/workspace/reset`),
    onMutate: () => {
      setWorkspaceFeedback(null);
    },
    onSuccess: async () => {
      if (selectedUserId) {
        await queryClient.invalidateQueries({ queryKey: ['user-detail', selectedUserId] });
      }
      await queryClient.invalidateQueries({ queryKey: ['users'] });
      setWorkspaceFeedback('Workspace sessions reset');
    },
    onError: (error) => {
      const detail = axios.isAxiosError(error)
        ? error.response?.data?.detail || error.message
        : error instanceof Error
          ? error.message
          : 'Failed to reset workspace';
      setWorkspaceFeedback(detail);
    },
  });

  const markCopied = (target: string) => {
    setCopiedKey(true);
    setCopiedKeyTarget(target);
    setTimeout(() => setCopiedKey(false), 1500);
    setTimeout(() => setCopiedKeyTarget(null), 1500);
  };

  const copyKey = async () => {
    if (!revealedKey) return;
    await copyToClipboard(revealedKey);
    markCopied('revealed-modal');
  };

  const copyRevealedKeyForUser = async (userId: string, target: string) => {
    if (!revealedKey || revealedKeyUserId !== userId) return;
    await copyToClipboard(revealedKey);
    markCopied(target);
  };

  const copyUserId = async (userId: string) => {
    await copyToClipboard(userId);
    markCopied(`user-id:${userId}`);
  };

  const copyChannelToken = async () => {
    if (!revealedChannelToken?.raw_token) return;
    await copyToClipboard(revealedChannelToken.raw_token);
    setCopiedChannelToken(true);
    setTimeout(() => setCopiedChannelToken(false), 1500);
  };

  const usageSummary = useMemo(() => {
    const totals = usage?.provider_totals || [];
    return {
      totalAccounts: totals.reduce((sum, row) => sum + (row.accounts || 0), 0),
      totalTokens: totals.reduce((sum, row) => sum + (row.total_tokens || 0), 0),
      totalSessions: totals.reduce((sum, row) => sum + (row.active_sessions || 0), 0),
    };
  }, [usage]);

  if (isLoading) {
    return <div className="p-12 text-slate-500 font-bold uppercase tracking-widest text-xs animate-pulse">Loading users...</div>;
  }

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-12 flex justify-between items-end">
        <div>
          <h2 className="text-4xl font-black tracking-tight text-white mb-2">User Management</h2>
          <p className="text-slate-500 font-medium">Provision users, enforce one active API key, and inspect usage with live data.</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="group flex items-center space-x-2 bg-white text-black hover:bg-blue-500 hover:text-white px-6 py-3 rounded-2xl transition-all duration-300 font-bold text-sm shadow-xl shadow-white/5"
        >
          <Plus size={18} className="group-hover:rotate-90 transition-transform duration-300" />
          <span>Provision User</span>
        </button>
      </header>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-8">
        <div className="bg-slate-900/40 border border-slate-800 rounded-3xl p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Users</div>
          <div className="text-3xl font-black text-white">{users?.length || 0}</div>
        </div>
        <div className="bg-slate-900/40 border border-slate-800 rounded-3xl p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Active Keys</div>
          <div className="text-3xl font-black text-white">{users?.reduce((sum, user) => sum + (user.active_keys || 0), 0) || 0}</div>
        </div>
        <div className="bg-slate-900/40 border border-slate-800 rounded-3xl p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Tokens (30d)</div>
          <div className="text-3xl font-black text-white">{usageSummary.totalTokens.toLocaleString()}</div>
        </div>
      </div>

      {showCreate && (
        <div className="mb-8 bg-slate-900/50 border border-slate-800 rounded-3xl p-8">
          <div className="flex justify-between items-center mb-6">
            <h3 className="text-xl font-black text-white">Provision New User</h3>
            <button onClick={() => setShowCreate(false)} className="text-slate-500 hover:text-white"><X size={18} /></button>
          </div>
          <p className="mb-6 text-xs text-slate-500">
            Codara always keeps one active API key per user and reveals the raw key once after provisioning or rotation.
          </p>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
            <div className="space-y-2">
              <label className="text-[10px] font-black uppercase tracking-widest text-slate-500">Email</label>
              <input className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-white text-sm" placeholder="alice@example.com" value={newUser.email} onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} />
            </div>
            <div className="space-y-2">
              <label className="text-[10px] font-black uppercase tracking-widest text-slate-500">Display Name</label>
              <input className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-white text-sm" placeholder="Alice" value={newUser.display_name} onChange={(e) => setNewUser({ ...newUser, display_name: e.target.value })} />
            </div>
            <div className="space-y-2">
              <label className="text-[10px] font-black uppercase tracking-widest text-slate-500">Max Concurrency</label>
              <input
                type="number"
                min={1}
                max={20}
                className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-white text-sm"
                placeholder="3"
                value={newUser.max_concurrency}
                onChange={(e) => setNewUser({ ...newUser, max_concurrency: Number(e.target.value) || 1 })}
              />
            </div>
            <button
              className="mt-7 bg-blue-600 hover:bg-blue-500 text-white rounded-xl py-3 font-black text-sm uppercase tracking-widest disabled:opacity-40"
              disabled={!newUser.email || !newUser.display_name}
              onClick={() => provisionMutation.mutate(newUser)}
            >
              Create
            </button>
          </div>
        </div>
      )}

      <div className="bg-slate-900/40 border border-slate-800/60 rounded-3xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-800/30 border-b border-slate-800/60">
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">User</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Keys</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Sessions</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Tokens (30d)</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Status</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40 text-slate-300">
              {users?.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-8 py-24 text-center">
                    <div className="flex flex-col items-center justify-center space-y-4">
                      <div className="p-4 bg-slate-800/50 rounded-full text-slate-600">
                        <UsersIcon size={32} />
                      </div>
                      <p className="text-slate-500 font-bold uppercase tracking-widest text-xs">No users provisioned yet</p>
                    </div>
                  </td>
                </tr>
              ) : (
                users?.map((user) => (
                  <tr key={user.user_id} className="group hover:bg-white/[0.02] transition-colors cursor-pointer" onClick={() => setSelectedUserId(user.user_id)}>
                    <td className="px-8 py-6">
                      <div className="flex items-center space-x-4">
                        <div className="p-3 bg-slate-800 rounded-2xl text-blue-400">
                          <UserCog size={22} />
                        </div>
                        <div>
                          <div className="font-black text-white">{user.display_name}</div>
                          <div className="text-[10px] font-mono text-slate-500">{user.email}</div>
                          <div className="mt-1 text-[10px] font-mono text-slate-600">{user.user_id}</div>
                        </div>
                      </div>
                    </td>
                    <td className="px-8 py-6 text-sm font-bold text-slate-200">{user.active_keys}</td>
                    <td className="px-8 py-6 text-sm font-bold text-slate-200">{user.active_sessions}</td>
                    <td className="px-8 py-6 text-sm font-bold text-slate-200">{(user.total_tokens_30d || 0).toLocaleString()}</td>
                    <td className="px-8 py-6">
                      <span className={`inline-flex px-3 py-1 rounded-full border text-[10px] font-black uppercase tracking-widest ${badgeClass(user.status)}`}>
                        {user.status}
                      </span>
                    </td>
                    <td className="px-8 py-6 text-right">
                      <div className="flex justify-end space-x-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        {user.status === 'active' ? (
                          <button
                            onClick={(e) => { e.stopPropagation(); suspendMutation.mutate(user.user_id); }}
                            className="px-3 py-2 rounded-xl bg-amber-500/10 text-amber-400 text-xs font-black uppercase tracking-widest"
                          >
                            Suspend
                          </button>
                        ) : (
                          <button
                            onClick={(e) => { e.stopPropagation(); unsuspendMutation.mutate(user.user_id); }}
                            className="px-3 py-2 rounded-xl bg-emerald-500/10 text-emerald-400 text-xs font-black uppercase tracking-widest"
                          >
                            Unsuspend
                          </button>
                        )}
                        <button
                          onClick={(e) => { e.stopPropagation(); deleteMutation.mutate(user.user_id); }}
                          className="px-3 py-2 rounded-xl bg-rose-500/10 text-rose-400 text-xs font-black uppercase tracking-widest"
                        >
                          Delete
                        </button>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>

      {selectedUserId && selectedUser.data && (
        <div className="fixed inset-y-0 right-0 w-full max-w-2xl bg-slate-950 border-l border-slate-800 shadow-2xl z-50 overflow-auto">
          <div className="p-8 border-b border-slate-800 flex justify-between items-start">
            <div>
              <div className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2">User Detail</div>
              <h3 className="text-2xl font-black text-white">{selectedUser.data.display_name}</h3>
              <div className="text-xs font-mono text-slate-500 mt-2">{selectedUser.data.email}</div>
              <button
                onClick={() => void copyUserId(selectedUser.data.user_id)}
                className="mt-3 inline-flex items-center gap-2 rounded-xl border border-slate-800 bg-black/30 px-3 py-2 text-[10px] font-mono text-slate-400 hover:border-slate-700 hover:text-white"
              >
                <span>{selectedUser.data.user_id}</span>
                <Copy size={12} />
                <span className="text-[9px] font-black uppercase tracking-widest text-emerald-400">
                  {copiedKeyTarget === `user-id:${selectedUser.data.user_id}` ? 'Copied' : 'Copy ID'}
                </span>
              </button>
            </div>
            <button onClick={() => setSelectedUserId(null)} className="text-slate-500 hover:text-white"><X size={18} /></button>
          </div>
          <div className="p-8 space-y-8">
            <div className="grid grid-cols-2 gap-4">
              <div className="p-4 rounded-2xl bg-slate-900/50 border border-slate-800">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Base Workspace</div>
                <div className="text-sm font-mono text-white break-all mt-2">{selectedUser.data.workspace_path}</div>
                <div className="mt-2 text-xs text-slate-500">
                  Clients can target sub-workspaces with
                  <code className="mx-1 rounded bg-slate-950 px-1.5 py-0.5 text-[11px] text-slate-300">uag_options.workspace_id</code>
                  and keep cache-friendly session reuse within that base path.
                </div>
              </div>
              <div className="p-4 rounded-2xl bg-slate-900/50 border border-slate-800">
                <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">API Keys</div>
                <div className="text-2xl font-black text-white mt-2">{selectedUser.data.active_keys}</div>
                <div className="mt-2 text-xs text-slate-500">Single active key policy · max concurrency {selectedUser.data.max_concurrency}</div>
              </div>
            </div>

            <section>
              <div className="mb-4 flex items-center justify-between gap-4">
                <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Channel Access</h4>
                <button
                  onClick={() => selectedUserId && resetWorkspaceMutation.mutate(selectedUserId)}
                  className="inline-flex items-center gap-2 rounded-xl bg-amber-500/10 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-amber-300 disabled:opacity-50"
                  disabled={resetWorkspaceMutation.isPending}
                >
                  <RefreshCw size={12} className={resetWorkspaceMutation.isPending ? 'animate-spin' : ''} />
                  Reset workspace sessions
                </button>
              </div>
              {workspaceFeedback ? (
                <div className={`mb-4 rounded-2xl border px-4 py-3 text-sm ${
                  workspaceFeedback === 'Workspace sessions reset'
                    ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-200'
                    : 'border-rose-500/20 bg-rose-500/10 text-rose-200'
                }`}>
                  {workspaceFeedback}
                </div>
              ) : null}
              <div className="rounded-2xl border border-slate-800 bg-black/30 p-4">
                <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
                  <label className="space-y-2">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Channel</span>
                    <input
                      readOnly
                      value="telegram"
                      className="w-full rounded-xl border border-slate-800 bg-slate-950 px-4 py-3 text-sm text-slate-300"
                    />
                  </label>
                  <label className="space-y-2">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Bot Name</span>
                    <input
                      value={channelBotName}
                      onChange={(event) => setChannelBotName(event.target.value)}
                      className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white"
                    />
                  </label>
                  <label className="space-y-2">
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Expires In Minutes</span>
                    <input
                      type="number"
                      min={1}
                      max={1440}
                      value={channelExpiry}
                      onChange={(event) => setChannelExpiry(Number(event.target.value) || 1)}
                      className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white"
                    />
                  </label>
                </div>
                <div className="mt-4 flex items-center justify-between gap-4">
                  <div className="text-xs text-slate-500">
                    Create a one-time Telegram link token for this user, then send <code className="mx-1 rounded bg-slate-950 px-1.5 py-0.5 text-slate-300">/link &lt;token&gt;</code> in the bot chat.
                  </div>
                  <button
                    onClick={() => {
                      if (!selectedUserId) return;
                      createChannelTokenMutation.mutate({
                        userId: selectedUserId,
                        botName: channelBotName.trim(),
                        expiresInMinutes: channelExpiry,
                      });
                    }}
                    disabled={!selectedUserId || !channelBotName.trim() || createChannelTokenMutation.isPending}
                    className="rounded-xl bg-blue-600 px-4 py-3 text-[10px] font-black uppercase tracking-widest text-white disabled:opacity-50"
                  >
                    {createChannelTokenMutation.isPending ? 'Creating…' : 'Create Link Token'}
                  </button>
                </div>
                {channelFeedback ? (
                  <div className="mt-4 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
                    {channelFeedback}
                  </div>
                ) : null}
                {revealedChannelToken && revealedChannelToken.user_id === selectedUser.data.user_id && (
                  <div className="mt-4 rounded-2xl border border-emerald-500/20 bg-emerald-500/10 p-4">
                    <div className="text-[10px] font-black uppercase tracking-widest text-emerald-300">One-time Link Token</div>
                    <button
                      type="button"
                      onClick={() => void copyChannelToken()}
                      className="mt-3 flex w-full items-center gap-3 rounded-2xl border border-emerald-400/20 bg-black/30 p-4 text-left transition-colors hover:border-emerald-300/40"
                    >
                      <input
                        readOnly
                        value={revealedChannelToken.raw_token}
                        className="w-full cursor-pointer bg-transparent font-mono text-sm text-white outline-none"
                      />
                      <Copy size={14} className="shrink-0 text-emerald-200" />
                    </button>
                    <div className="mt-3 flex items-center justify-between gap-4 text-xs text-slate-400">
                      <span>Expires {formatDate(revealedChannelToken.expires_at)}</span>
                      <span className="font-black uppercase tracking-widest text-emerald-300">
                        {copiedChannelToken ? 'Copied' : 'Copy now'}
                      </span>
                    </div>
                  </div>
                )}
              </div>
            </section>

            <section>
              <div className="mb-4 flex items-center justify-between gap-4">
                <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">API Key</h4>
                <button
                  onClick={() => {
                    if (selectedUserId) rotateKeyMutation.mutate(selectedUserId);
                  }}
                  className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-white disabled:opacity-50"
                  disabled={rotateKeyMutation.isPending}
                >
                  <RefreshCw size={12} className={rotateKeyMutation.isPending ? 'animate-spin' : ''} />
                  Rotate key
                </button>
              </div>
              <div className="space-y-3">
                {selectedUser.data.api_keys.length === 0 ? (
                  <div className="text-xs text-slate-600 font-bold uppercase tracking-widest">No active key</div>
                ) : (
                  selectedUser.data.api_keys.map((key: ApiKeyRecord) => {
                    const rawKeyVisible = revealedKeyUserId === selectedUser.data.user_id && !!revealedKey;
                    const displayValue = rawKeyVisible ? revealedKey : `${key.key_prefix}••••`;
                    return (
                      <button
                        key={key.key_id}
                        type="button"
                        onClick={() => void copyRevealedKeyForUser(selectedUser.data.user_id, key.key_id)}
                        disabled={!rawKeyVisible}
                        className={`w-full p-4 rounded-2xl bg-black/40 border border-slate-800 flex items-center justify-between text-left transition-colors ${
                          rawKeyVisible ? 'cursor-copy hover:border-emerald-400/30' : 'cursor-not-allowed opacity-80'
                        }`}
                        title={rawKeyVisible ? 'Click to copy the current raw key' : 'Rotate the key to reveal a new raw API key'}
                      >
                        <div>
                          <div className="text-sm font-bold text-white">{key.label || 'Unnamed key'}</div>
                          <div className="mt-1 flex items-center gap-2 text-[10px] font-mono text-slate-500">
                            <span className="rounded bg-slate-950/70 px-2 py-1 text-slate-300">{displayValue}</span>
                            <Copy size={11} className="text-slate-400" />
                            <span className="text-[9px] font-black uppercase tracking-widest text-emerald-400">
                              {copiedKeyTarget === key.key_id ? 'Copied' : rawKeyVisible ? 'Copy raw key' : 'Masked preview only'}
                            </span>
                          </div>
                          <div className="mt-2 text-[10px] text-slate-600">
                            {rawKeyVisible ? 'Click to copy the current raw key.' : 'Raw API keys are only available immediately after provisioning or rotation. Rotate this key to reveal a fresh one-time value.'}
                          </div>
                        </div>
                        <div className={`text-[10px] font-black uppercase tracking-widest ${key.status === 'active' ? 'text-emerald-400' : 'text-rose-400'}`}>
                          {copiedKeyTarget === key.key_id ? 'copied' : key.status}
                        </div>
                      </button>
                    );
                  })
                )}
              </div>
            </section>

            <section>
              <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 mb-4">Usage</h4>
              <div className="p-4 rounded-2xl bg-black/40 border border-slate-800">
                <div className="text-sm text-slate-300 font-bold">
                  Total tokens: {(selectedUser.data.total_tokens_30d || 0).toLocaleString()}
                </div>
                <div className="text-xs text-slate-500 mt-2">
                  Input: {selectedUser.data.total_input_tokens_30d.toLocaleString()} | Output: {selectedUser.data.total_output_tokens_30d.toLocaleString()} | Cache hits: {selectedUser.data.total_cache_hit_tokens_30d.toLocaleString()}
                </div>
              </div>
            </section>

            <section>
              <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 mb-4">Sessions</h4>
              <div className="space-y-3">
                {selectedUser.data.sessions.length === 0 ? (
                  <div className="text-xs text-slate-600 font-bold uppercase tracking-widest">No sessions recorded</div>
                ) : (
                  selectedUser.data.sessions.map((session: UserSessionRecord) => (
                    <div key={session.client_session_id} className="p-4 rounded-2xl bg-black/40 border border-slate-800">
                      <div className="flex items-center justify-between">
                        <div className="font-mono text-xs text-slate-300">{session.client_session_id}</div>
                        <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">{session.status}</span>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                          {session.provider}
                        </span>
                        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                          {session.api_key_label || 'No key binding'}
                        </span>
                      </div>
                      <div className="text-[10px] text-slate-500 mt-2">{session.cwd_path}</div>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section>
              <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 mb-4">Recent Activity</h4>
              <div className="space-y-3">
                {selectedUser.data.recent_activity.length === 0 ? (
                  <div className="text-xs text-slate-600 font-bold uppercase tracking-widest">No turns recorded yet</div>
                ) : (
                  selectedUser.data.recent_activity.map((activity: UserActivityRecord) => (
                    <div key={activity.turn_id} className="p-4 rounded-2xl bg-black/40 border border-slate-800">
                      <div className="flex items-center justify-between gap-4">
                        <div>
                          <div className="text-sm font-bold text-white">{activity.provider} turn</div>
                          <div className="text-[10px] font-mono text-slate-500 mt-1">{activity.client_session_id}</div>
                        </div>
                        <div className="text-right">
                          <div className="text-[10px] font-black uppercase tracking-widest text-emerald-400">
                            +{(activity.output_tokens || 0).toLocaleString()} out
                          </div>
                          <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">
                            {(activity.input_tokens || 0).toLocaleString()} in
                          </div>
                        </div>
                      </div>
                      <div className="mt-2 flex flex-wrap gap-2">
                        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                          {activity.api_key_label || 'No key binding'}
                        </span>
                        <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                          {activity.finish_reason || 'unknown'}
                        </span>
                      </div>
                      <div className="mt-2 text-[10px] text-slate-500">
                        {formatDate(activity.timestamp)} · {activity.cwd_path || 'No workspace path'}
                      </div>
                    </div>
                  ))
                )}
              </div>
            </section>

            <section>
              <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 mb-4">Resets</h4>
              <div className="space-y-3">
                {selectedUser.data.resets.length === 0 ? (
                  <div className="text-xs text-slate-600 font-bold uppercase tracking-widest">No reset history</div>
                ) : (
                  selectedUser.data.resets.map((reset: WorkspaceResetRecord) => (
                    <div key={reset.reset_id} className="p-4 rounded-2xl bg-black/40 border border-slate-800 text-xs text-slate-400">
                      {reset.triggered_by} wiped {reset.sessions_wiped} sessions on {formatDate(reset.reset_at)}
                    </div>
                  ))
                )}
              </div>
            </section>
          </div>
        </div>
      )}

      {revealedKey && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-6 backdrop-blur-sm">
          <div className="w-full max-w-2xl rounded-3xl border border-emerald-500/20 bg-slate-950 p-8 shadow-2xl">
            <div className="flex items-start justify-between gap-4">
              <div>
                <div className="mb-2 text-[10px] font-black uppercase tracking-widest text-emerald-400">Copy this API key now</div>
                <h3 className="text-2xl font-black text-white">{revealedKeyOwner || 'User'} API key ready</h3>
                <p className="mt-2 text-sm text-slate-400">This raw key is shown once and will not be recoverable later. Copy it before closing this dialog.</p>
              </div>
              <button
                  onClick={() => {
                    setRevealedKey(null);
                    setRevealedKeyOwner(null);
                    setRevealedKeyUserId(null);
                    setCopiedKey(false);
                    setCopiedKeyTarget(null);
                  }}
                className="text-slate-500 transition-colors hover:text-white"
              >
                <X size={18} />
              </button>
            </div>
            <button
              type="button"
              onClick={() => void copyKey()}
              className="mt-6 w-full rounded-2xl border border-emerald-400/20 bg-black/30 p-4 text-left transition-colors hover:border-emerald-300/40"
            >
              <div className="flex items-center gap-3">
                <KeyRound size={16} className="shrink-0 text-emerald-300" />
                <input
                  readOnly
                  value={revealedKey}
                  onFocus={(event) => event.currentTarget.select()}
                  className="w-full cursor-pointer bg-transparent font-mono text-sm text-white outline-none"
                  aria-label="Revealed API key"
                />
                <Copy size={14} className="shrink-0 text-emerald-200" />
              </div>
            </button>
            <div className="mt-6 flex items-center justify-between gap-4">
              <div className="text-xs text-slate-500">Auto-generated by Codara. One active key per user.</div>
              <button onClick={copyKey} className="inline-flex items-center justify-center gap-2 rounded-2xl bg-white px-5 py-3 text-xs font-black uppercase tracking-widest text-black">
                <Copy size={14} />
                {copiedKey ? 'Copied' : 'Copy key'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};

export default Users;
