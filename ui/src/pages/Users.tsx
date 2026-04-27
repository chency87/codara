import React, { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Copy, KeyRound, Plus, UserCog, Users as UsersIcon, X } from 'lucide-react';
import type {
  ChannelLinkTokenResponse,
  CreateUserResponse,
  RotateUserKeyResponse,
  UserDetailPayload,
  UserSummary,
} from '../types/api';
import { dashboardPollHeaders } from '../api/dashboardPoll';
import { CreateUserForm, UserDetailPanel } from '../components/users';

const badgeClass = (status: string) => {
  if (status === 'active') return 'bg-emerald-500/10 text-emerald-400 border-emerald-500/20';
  if (status === 'suspended') return 'bg-amber-500/10 text-amber-400 border-amber-500/20';
  return 'bg-rose-500/10 text-rose-400 border-rose-500/20';
};

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
  const [revealedKey, setRevealedKey] = useState<string | null>(null);
  const [revealedKeyOwner, setRevealedKeyOwner] = useState<string | null>(null);
  const [revealedKeyUserId, setRevealedKeyUserId] = useState<string | null>(null);
  const [copiedKey, setCopiedKey] = useState(false);
  const [copiedKeyTarget, setCopiedKeyTarget] = useState<string | null>(null);
  const [channelExpiry] = useState(30);
  const [channelBotName] = useState('codara-bot');
  const [revealedChannelToken, setRevealedChannelToken] = useState<ChannelLinkTokenResponse | null>(null);
  const [copiedChannelToken, setCopiedChannelToken] = useState(false);
  const [channelFeedback, setChannelFeedback] = useState<string | null>(null);
  const [workspaceFeedback, setWorkspaceFeedback] = useState<string | null>(null);

  const { data: users, isLoading } = useQuery<UserSummary[]>({
    queryKey: ['users'],
    queryFn: async () => (await axios.get('/management/v1/users', { headers: dashboardPollHeaders })).data.data,
    refetchInterval: 30000,
  });

  const selectedUser = useQuery<UserDetailPayload>({
    queryKey: ['user-detail', selectedUserId],
    queryFn: async () => (await axios.get(`/management/v1/users/${selectedUserId}`)).data.data,
    enabled: !!selectedUserId,
  });

  const provisionMutation = useMutation({
    mutationFn: (payload: { email: string; display_name: string; max_concurrency: number }) => axios.post('/management/v1/users', payload),
    onSuccess: (resp: { data: { data: CreateUserResponse } }) => {
      setRevealedKey(resp.data.data.api_key.raw_key);
      setRevealedKeyOwner(resp.data.data.display_name);
      setRevealedKeyUserId(resp.data.data.user_id);
      setCopiedKey(false);
      setCopiedKeyTarget(null);
      setShowCreate(false);
      queryClient.invalidateQueries({ queryKey: ['users'] });
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
  });

  const copyKey = async () => {
    if (revealedKey) {
      await copyToClipboard(revealedKey);
      setCopiedKey(true);
      setTimeout(() => setCopiedKey(false), 2000);
    }
  };

  const copyChannelToken = async () => {
    if (revealedChannelToken) {
      await copyToClipboard(revealedChannelToken.raw_token);
      setCopiedChannelToken(true);
      setTimeout(() => setCopiedChannelToken(false), 2000);
    }
  };

  if (isLoading) return (
    <div className="p-12 animate-pulse">
      <div className="h-8 w-48 bg-slate-800 rounded-lg mb-8"></div>
      <div className="h-64 bg-slate-900/50 rounded-3xl border border-slate-800"></div>
    </div>
  );

  return (
    <div className="p-6 sm:p-8 lg:p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-12 flex flex-col sm:flex-row sm:items-end sm:justify-between gap-6">
        <div>
          <h2 className="text-3xl sm:text-4xl font-black tracking-tight text-white mb-2">User Management</h2>
          <p className="text-slate-500 font-medium">Provision users, enforce one active API key, and inspect active sessions.</p>
        </div>
        <button
          onClick={() => setShowCreate(true)}
          className="group flex items-center justify-center space-x-2 bg-white text-black hover:bg-blue-500 hover:text-white px-6 py-3 rounded-2xl transition-all duration-300 font-bold text-sm shadow-xl shadow-white/5 self-start sm:self-auto w-full sm:w-auto"
        >
          <Plus size={18} className="group-hover:rotate-90 transition-transform duration-300" />
          <span>Provision User</span>
        </button>
      </header>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-6 mb-8">
        <div className="bg-slate-900/40 border border-slate-800 rounded-3xl p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Users</div>
          <div className="text-3xl font-black text-white">{users?.length || 0}</div>
        </div>
        <div className="bg-slate-900/40 border border-slate-800 rounded-3xl p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Active Keys</div>
          <div className="text-3xl font-black text-white">{users?.reduce((sum, user) => sum + (user.active_keys || 0), 0) || 0}</div>
        </div>
        <div className="bg-slate-900/40 border border-slate-800 rounded-3xl p-6">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2">Active Sessions</div>
          <div className="text-3xl font-black text-white">{users?.reduce((sum, user) => sum + (user.active_sessions || 0), 0) || 0}</div>
        </div>
      </div>

      {showCreate && (
        <CreateUserForm
          onSubmit={(user) => provisionMutation.mutate(user)}
          onClose={() => setShowCreate(false)}
          isPending={provisionMutation.isPending}
        />
      )}

      <div className="bg-slate-900/40 border border-slate-800/60 rounded-3xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-800/30 border-b border-slate-800/60">
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">User</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Keys</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Sessions</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Status</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] text-right">Actions</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40 text-slate-300">
              {users?.length === 0 ? (
                <tr>
                  <td colSpan={5} className="px-8 py-24 text-center">
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
        <UserDetailPanel
          user={selectedUser.data}
          onClose={() => setSelectedUserId(null)}
          onRotateKey={() => selectedUserId && rotateKeyMutation.mutate(selectedUserId)}
          rotateKeyPending={rotateKeyMutation.isPending}
          onResetWorkspace={() => selectedUserId && resetWorkspaceMutation.mutate(selectedUserId)}
          resetWorkspacePending={resetWorkspaceMutation.isPending}
          workspaceFeedback={workspaceFeedback}
          channelBotName={channelBotName}
          channelExpiry={channelExpiry}
          channelFeedback={channelFeedback}
          channelToken={revealedChannelToken}
          copiedChannelToken={copiedChannelToken}
          onCreateChannelToken={(userId, botName, expiresInMinutes) => {
            createChannelTokenMutation.mutate({ userId, botName, expiresInMinutes });
          }}
          createChannelTokenPending={createChannelTokenMutation.isPending}
          revealedKey={revealedKey}
          revealedKeyUserId={revealedKeyUserId}
          copiedKeyTarget={copiedKeyTarget}
          onCopyKey={(keyId) => {
            setCopiedKeyTarget(keyId);
            setTimeout(() => setCopiedKeyTarget(null), 2000);
          }}
          onCopyChannelToken={copyChannelToken}
        />
      )}

      {revealedKey && (
        <div className="absolute inset-0 z-50 flex items-center justify-center bg-black/70 p-6 backdrop-blur-sm">
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
              onClick={copyKey}
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
