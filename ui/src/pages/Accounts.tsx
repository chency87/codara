import React, { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { ArrowRightLeft, Key, Plus, RefreshCw, Shield, Tag, Trash2, X } from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import type { AccountRecord, ApiEnvelope } from '../types/api';

const formatNumber = (value?: number | null) => Number(value || 0).toLocaleString();
const PAGE_SIZE = 25;

const formatTime = (value?: string | null) => {
  if (!value) return 'unknown';
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) return value;
  return parsed.toLocaleString([], {
    month: 'short',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
};

const formatDuration = (seconds?: number | null) => {
  if (seconds == null || Number.isNaN(Number(seconds))) return 'unknown';
  const total = Math.max(0, Math.floor(Number(seconds)));
  const days = Math.floor(total / 86400);
  const hours = Math.floor((total % 86400) / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  if (days > 0) return `${days}d ${hours}h`;
  if (hours > 0) return `${hours}h ${minutes}m`;
  return `${minutes}m`;
};

const clampPercent = (value?: number | null) => {
  if (value == null || Number.isNaN(Number(value))) return null;
  return Math.max(0, Math.min(100, Number(value)));
};

const getWindowUsage = (account: AccountRecord, windowKey: 'hourly' | 'weekly') => {
  const usedPctKey = windowKey === 'hourly' ? 'hourly_used_pct' : 'weekly_used_pct';
  const leftPctKey = windowKey === 'hourly' ? 'hourly_left_pct' : 'weekly_left_pct';
  const resetAtKey = windowKey === 'hourly' ? 'hourly_reset_at' : 'weekly_reset_at';
  const resetAfterKey = windowKey === 'hourly' ? 'hourly_reset_after_seconds' : 'weekly_reset_after_seconds';
  const limitKey = windowKey === 'hourly' ? 'hourly_limit' : 'weekly_limit';
  const leftKey = windowKey === 'hourly' ? 'hourly_left' : 'weekly_left';

  const usedPct = clampPercent(account[usedPctKey]);
  const leftPct = usedPct == null ? clampPercent(account[leftPctKey]) : Math.max(0, 100 - usedPct);
  const resetAt = account[resetAtKey];
  const resetAfter = account[resetAfterKey];
  const observed = Boolean(account.usage_observed);
  const hasWhamShape = observed && (usedPct != null || resetAfter != null);

  return {
    observed,
    hasWhamShape,
    usedPct,
    leftPct,
    resetAt,
    resetAfter,
    limit: account[limitKey],
    left: account[leftKey],
  };
};

const isWhamAccount = (account: AccountRecord) => account.usage_source === 'wham';

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

const statusClass = (status?: string) => {
  switch (String(status || '').toLowerCase()) {
    case 'active':
    case 'ready':
      return 'border-emerald-500/20 bg-emerald-500/10 text-emerald-300';
    case 'cooldown':
      return 'border-amber-500/20 bg-amber-500/10 text-amber-300';
    case 'expired':
    case 'disabled':
      return 'border-rose-500/20 bg-rose-500/10 text-rose-300';
    case 'error':
      return 'border-red-500/20 bg-red-500/10 text-red-300';
    default:
      return 'border-slate-700 bg-slate-800/50 text-slate-300';
  }
};

const Accounts = () => {
  const queryClient = useQueryClient();
  const [showAdd, setShowAdd] = useState(false);
  const [refreshFeedback, setRefreshFeedback] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [newAccount, setNewAccount] = useState({
    account_id: '',
    provider: 'codex',
    auth_type: 'API_KEY',
    label: '',
  });
  const [credentialSource, setCredentialSource] = useState<'text' | 'file'>('text');
  const [credentialText, setCredentialText] = useState('');
  const [credentialFile, setCredentialFile] = useState<File | null>(null);

  const { data, isLoading, error } = useQuery<ApiEnvelope<AccountRecord[]>>({
    queryKey: ['management-accounts', cursor],
    queryFn: async () =>
      (
        await axios.get('/management/v1/accounts', {
          params: { limit: PAGE_SIZE, after: cursor || undefined },
        })
      ).data,
    refetchInterval: 30000,
  });

  const refreshMutation = useMutation({
    mutationFn: () => axios.post('/management/v1/usage/refresh'),
    onMutate: () => {
      setRefreshFeedback(null);
    },
    onSuccess: async () => {
      setRefreshFeedback(null);
      await queryClient.invalidateQueries({ queryKey: ['management-accounts'] });
    },
    onError: error => {
      setRefreshFeedback(getErrorMessage(error));
    },
  });

  const addMutation = useMutation({
    mutationFn: () => {
      const form = new FormData();
      form.set('provider', newAccount.provider);
      form.set('auth_type', newAccount.auth_type);
      form.set('label', newAccount.label);
      if (newAccount.account_id.trim()) {
        form.set('account_id', newAccount.account_id.trim());
      }
      if (credentialSource === 'text') {
        form.set('credential_text', credentialText);
      } else if (credentialFile) {
        form.set('credential_file', credentialFile);
      }
      return axios.post('/management/v1/accounts/upload', form, {
        headers: { 'Content-Type': 'multipart/form-data' },
      });
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['management-accounts'] });
      setShowAdd(false);
      setNewAccount({ account_id: '', provider: 'codex', auth_type: 'API_KEY', label: '' });
      setCredentialSource('text');
      setCredentialText('');
      setCredentialFile(null);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: (id: string) => axios.delete(`/management/v1/accounts/${id}`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['management-accounts'] });
    },
  });

  const selectMutation = useMutation({
    mutationFn: (id: string) => axios.post(`/management/v1/accounts/${id}/select`),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['management-accounts'] });
    },
  });

  const accounts = useMemo(() => data?.data || [], [data]);
  const pageMeta = data?.meta?.page;

  const totals = useMemo(() => {
    return {
      totalAccounts: pageMeta?.total_count ?? accounts.length,
      selectedByProvider: accounts.filter((row) => row.cli_primary).length,
      readyAccounts: accounts.filter((row) => String(row.status || '').toLowerCase() === 'ready').length,
      cooldownAccounts: accounts.filter((row) => String(row.status || '').toLowerCase() === 'cooldown').length,
    };
  }, [accounts, pageMeta?.total_count]);

  const sortedAccounts = useMemo(() => {
    return [...accounts].sort((a, b) => {
      if (a.provider === b.provider) {
        return Number(b.cli_primary) - Number(a.cli_primary);
      }
      return String(a.provider).localeCompare(String(b.provider));
    });
  }, [accounts]);

  if (isLoading) {
    return <div className="p-12 text-slate-500 font-bold uppercase tracking-widest text-xs animate-pulse">Loading Identity Pool...</div>;
  }

  const tableError = error ? getErrorMessage(error) : null;
  const canGoBack = cursorHistory.length > 0;
  const canGoNext = accounts.length === PAGE_SIZE && Boolean(pageMeta?.cursor);

  const goToNextPage = () => {
    if (!pageMeta?.cursor || !canGoNext) return;
    setCursorHistory(prev => [...prev, cursor]);
    setCursor(pageMeta.cursor);
  };

  const goToPreviousPage = () => {
    if (!canGoBack) return;
    const previousCursor = cursorHistory[cursorHistory.length - 1] ?? null;
    setCursorHistory(prev => prev.slice(0, -1));
    setCursor(previousCursor);
  };

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-8 flex justify-between items-end gap-6">
        <div>
          <h2 className="text-4xl font-black tracking-tight text-white mb-2">Account Pool</h2>
          <p className="text-slate-500 font-medium">Switch CLI-primary identity, upload credentials, and refresh usage state.</p>
        </div>
        <div className="flex items-center gap-3">
          <button
            onClick={() => refreshMutation.mutate()}
            disabled={refreshMutation.isPending}
            className="group flex items-center space-x-2 bg-slate-900/60 border border-slate-800 hover:border-slate-700 px-5 py-3 rounded-2xl transition-all duration-300 font-bold text-sm text-slate-200"
          >
            <RefreshCw size={16} className={refreshMutation.isPending ? 'animate-spin' : 'group-hover:rotate-90 transition-transform duration-300'} />
            <span>Refresh Usage</span>
          </button>
          <button
            onClick={() => setShowAdd(true)}
            className="group flex items-center space-x-2 bg-white text-black hover:bg-blue-500 hover:text-white px-6 py-3 rounded-2xl transition-all duration-300 font-bold text-sm shadow-xl shadow-white/5"
          >
            <Plus size={18} className="group-hover:rotate-90 transition-transform duration-300" />
            <span>Register Identity</span>
          </button>
        </div>
      </header>

      {refreshFeedback && (
        <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          Refresh usage failed: {refreshFeedback}
        </div>
      )}

      {tableError && (
        <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          Account pool loading failed: {tableError}
        </div>
      )}

      <div className="mb-8 rounded-3xl border border-blue-500/20 bg-blue-500/10 p-5 text-sm text-blue-100">
        <div className="mb-2 text-[10px] font-black uppercase tracking-[0.2em] text-blue-300">Automatic allocation policy</div>
        Vault accounts are the inventory source. Provider auth files only mirror the active CLI account. New work is routed to the CLI-primary account while it still has healthy headroom; otherwise UAG falls back to the ready account with the most remaining quota.
      </div>

      <div className="grid grid-cols-1 md:grid-cols-4 gap-4 mb-8">
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-5">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2 flex items-center gap-2">
            <Shield size={12} /> Accounts
          </div>
          <div className="text-3xl font-black text-white">{totals.totalAccounts}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-5">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2 flex items-center gap-2">
            <ArrowRightLeft size={12} /> CLI Selections
          </div>
          <div className="text-3xl font-black text-white">{totals.selectedByProvider}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-5">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2 flex items-center gap-2">
            <Key size={12} /> Ready
          </div>
          <div className="text-3xl font-black text-white">{totals.readyAccounts}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-5">
          <div className="text-[10px] uppercase tracking-widest text-slate-500 font-black mb-2 flex items-center gap-2">
            <RefreshCw size={12} /> Cooldown
          </div>
          <div className="text-3xl font-black text-white">{totals.cooldownAccounts}</div>
        </div>
      </div>

      {showAdd && (
        <div className="mb-8 p-8 bg-slate-900 border border-blue-500/30 rounded-3xl relative overflow-hidden shadow-2xl shadow-blue-900/10">
          <div className="absolute top-0 right-0 p-4">
            <button onClick={() => setShowAdd(false)} className="text-slate-500 hover:text-white transition-colors">
              <X size={20} />
            </button>
          </div>
          <h3 className="text-xl font-black text-white mb-8">Identity Registration</h3>
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-6">
            <div className="space-y-2">
              <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Account ID</label>
              <div className="relative">
                <Shield size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  type="text"
                  placeholder="optional; auto-generated when blank"
                  className="w-full bg-black border border-slate-800 rounded-xl pl-10 pr-4 py-3 text-sm focus:border-blue-500 outline-none transition-colors text-white font-medium"
                  value={newAccount.account_id}
                  onChange={e => setNewAccount({ ...newAccount, account_id: e.target.value })}
                />
              </div>
            </div>
            <div className="space-y-2">
              <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">CLI Name</label>
              <select
                className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-sm focus:border-blue-500 outline-none transition-colors text-white font-medium appearance-none"
                value={newAccount.provider}
                onChange={e => setNewAccount({ ...newAccount, provider: e.target.value })}
              >
                <option value="codex">Codex</option>
                <option value="gemini">Gemini</option>
                <option value="opencode">OpenCode</option>
              </select>
            </div>
            <div className="space-y-2">
              <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Auth Method</label>
              <select
                className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-sm focus:border-blue-500 outline-none transition-colors text-white font-medium appearance-none"
                value={newAccount.auth_type}
                onChange={e => setNewAccount({ ...newAccount, auth_type: e.target.value })}
              >
                <option value="API_KEY">API KEY</option>
                <option value="OAUTH_SESSION">OAUTH SESSION</option>
              </select>
            </div>
            <div className="space-y-2">
              <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Display Label</label>
              <div className="relative">
                <Tag size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
                <input
                  type="text"
                  placeholder="Internal Label"
                  className="w-full bg-black border border-slate-800 rounded-xl pl-10 pr-4 py-3 text-sm focus:border-blue-500 outline-none transition-colors text-white font-medium"
                  value={newAccount.label}
                  onChange={e => setNewAccount({ ...newAccount, label: e.target.value })}
                />
              </div>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mt-6">
            <div className="space-y-2">
              <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Credential Source</label>
              <select
                className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-sm focus:border-blue-500 outline-none transition-colors text-white font-medium appearance-none"
                value={credentialSource}
                onChange={e => {
                  const next = e.target.value as 'text' | 'file';
                  setCredentialSource(next);
                  setCredentialText('');
                  setCredentialFile(null);
                }}
              >
                <option value="text">Paste Credential</option>
                <option value="file">Upload File</option>
              </select>
            </div>
            {credentialSource === 'file' ? (
              <div className="space-y-2 md:col-span-2">
                <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Credential File</label>
                <input
                  type="file"
                  className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-sm focus:border-blue-500 outline-none transition-colors text-white font-medium"
                  accept=".json,.txt"
                  onChange={e => setCredentialFile(e.target.files?.[0] || null)}
                />
              </div>
            ) : (
              <div className="space-y-2 md:col-span-2">
                <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Credential Text</label>
                <textarea
                  className="w-full h-24 bg-black border border-slate-800 rounded-xl px-4 py-3 text-sm focus:border-blue-500 outline-none transition-colors text-white font-medium"
                  placeholder="Paste API key, auth.json content, or oauth_creds.json content"
                  value={credentialText}
                  onChange={e => setCredentialText(e.target.value)}
                />
              </div>
            )}
          </div>

          <div className="mt-6 flex justify-end">
            <button
              onClick={() => addMutation.mutate()}
              className="bg-blue-600 hover:bg-blue-500 text-white rounded-xl px-8 py-3 text-sm font-black transition-all shadow-lg shadow-blue-600/20 uppercase tracking-widest disabled:opacity-50"
              disabled={!newAccount.label || (credentialSource === 'text' ? !credentialText.trim() : !credentialFile)}
            >
              Authorize
            </button>
          </div>
        </div>
      )}

      <div className="bg-slate-900/40 border border-slate-800 rounded-3xl overflow-hidden shadow-2xl shadow-black/20">
        <div className="overflow-x-auto">
          <table className="min-w-full text-left">
            <thead className="bg-black/20">
              <tr className="text-[10px] uppercase tracking-[0.2em] text-slate-500 font-black">
                <th className="px-6 py-4">CLI Name</th>
                <th className="px-6 py-4">Credential ID</th>
                <th className="px-6 py-4">Auth Index</th>
                <th className="px-6 py-4">5h Limit</th>
                <th className="px-6 py-4">Weekly Limit</th>
                <th className="px-6 py-4">Credits</th>
                <th className="px-6 py-4">Auth Method</th>
                <th className="px-6 py-4">Allocation</th>
                <th className="px-6 py-4">Status</th>
                <th className="px-6 py-4 text-right">Action</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/70">
              {sortedAccounts.length === 0 ? (
                <tr>
                  <td colSpan={10} className="px-6 py-24 text-center text-slate-600 font-bold uppercase tracking-widest text-xs">
                    Identity pool is currently empty
                  </td>
                </tr>
              ) : (
                sortedAccounts.map((account) => {
                  const isSelected = Boolean(account.cli_primary);
                  const remainingCredits = account.remaining_compute_hours ?? account.compute_hours_left;
                  const hourly = getWindowUsage(account, 'hourly');
                  const weekly = getWindowUsage(account, 'weekly');
                  return (
                    <tr key={account.account_id} className={isSelected ? 'bg-blue-500/5' : 'hover:bg-slate-800/20'}>
                      <td className="px-6 py-5">
                        <div className="font-black text-white tracking-tight uppercase">{String(account.cli_name || account.provider).toUpperCase()}</div>
                        <div className="text-[10px] text-slate-500 uppercase tracking-widest mt-1">{account.label}</div>
                        <div className="text-[10px] text-slate-600 mt-1 font-mono">{account.account_id}</div>
                      </td>
                      <td className="px-6 py-5">
                        <div className="text-sm font-black text-white font-mono">{account.credential_id || '—'}</div>
                        <div className="text-[10px] text-slate-500 mt-1">pool identity</div>
                      </td>
                      <td className="px-6 py-5">
                        <div className="text-sm font-black text-white font-mono">{account.auth_index || '—'}</div>
                        <div className="text-[10px] text-slate-500 mt-1">
                          {account.last_seen_at ? `seen ${formatTime(account.last_seen_at)}` : 'never observed'}
                        </div>
                      </td>
                      <td className="px-6 py-5">
                        <div className="text-sm font-black text-white">
                          {hourly.observed && hourly.leftPct != null ? `${hourly.leftPct.toFixed(0)}% left` : 'unknown'}
                        </div>
                        {!hourly.observed ? (
                          <div className="text-[10px] text-slate-600 mt-1">usage not reported yet</div>
                        ) : hourly.hasWhamShape ? (
                          <>
                            <div className="text-[10px] text-slate-500 tabular-nums mt-1">
                              {hourly.usedPct != null ? `${hourly.usedPct.toFixed(0)}% used in WHAM 5h window` : 'WHAM 5h window'}
                            </div>
                            <div className="text-[10px] text-slate-600 mt-1">
                              resets in {formatDuration(hourly.resetAfter)}
                            </div>
                            <div className="text-[10px] text-slate-600 mt-1">
                              at {formatTime(hourly.resetAt)}
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="text-[10px] text-slate-500 tabular-nums mt-1">
                              {hourly.left != null && hourly.limit != null ? `${formatNumber(hourly.left)} / ${formatNumber(hourly.limit)}` : 'limit not reported'}
                            </div>
                            <div className="text-[10px] text-slate-600 mt-1">resets {formatTime(hourly.resetAt)}</div>
                          </>
                        )}
                      </td>
                      <td className="px-6 py-5">
                        <div className="text-sm font-black text-white">
                          {weekly.observed && weekly.leftPct != null ? `${weekly.leftPct.toFixed(0)}% left` : 'unknown'}
                        </div>
                        {!weekly.observed ? (
                          <div className="text-[10px] text-slate-600 mt-1">usage not reported yet</div>
                        ) : weekly.hasWhamShape ? (
                          <>
                            <div className="text-[10px] text-slate-500 tabular-nums mt-1">
                              {weekly.usedPct != null ? `${weekly.usedPct.toFixed(0)}% used in WHAM weekly window` : 'WHAM weekly window'}
                            </div>
                            <div className="text-[10px] text-slate-600 mt-1">
                              resets in {formatDuration(weekly.resetAfter)}
                            </div>
                            <div className="text-[10px] text-slate-600 mt-1">
                              at {formatTime(weekly.resetAt)}
                            </div>
                          </>
                        ) : (
                          <>
                            <div className="text-[10px] text-slate-500 tabular-nums mt-1">
                              {weekly.left != null && weekly.limit != null ? `${formatNumber(weekly.left)} / ${formatNumber(weekly.limit)}` : 'limit not reported'}
                            </div>
                            <div className="text-[10px] text-slate-600 mt-1">resets {formatTime(weekly.resetAt)}</div>
                          </>
                        )}
                      </td>
                      <td className="px-6 py-5">
                        <div className="text-sm font-black text-white">{remainingCredits != null ? Number(remainingCredits).toFixed(1) : 'unknown'}</div>
                        <div className="text-[10px] text-slate-500 mt-1">
                          {remainingCredits != null
                            ? account.hourly_used_pct != null || account.weekly_used_pct != null
                              ? 'WHAM credits balance'
                              : `${Math.round((Number(remainingCredits) / 5.0) * 100)}% of 5h budget`
                            : 'credits not reported'}
                        </div>
                        {isWhamAccount(account) && (
                          <>
                            <div className="text-[10px] text-slate-600 mt-1">
                              {account.credits_unlimited ? 'unlimited credits' : account.credits_has_credits ? 'credits available' : 'no credits'}
                            </div>
                            <div className="text-[10px] text-slate-600 mt-1">
                              {account.credits_overage_limit_reached ? 'overage limit reached' : 'no overage limit reached'}
                            </div>
                          </>
                        )}
                      </td>
                      <td className="px-6 py-5">
                        <div className="text-sm font-black text-white flex items-center gap-2">
                          <Key size={12} className="text-blue-400" />
                          {String(account.auth_type || '').replace(/_/g, ' ')}
                        </div>
                        {isWhamAccount(account) && (
                          <div className="text-[10px] text-slate-500 mt-1">
                            {account.plan_type ? `plan ${account.plan_type}` : 'WHAM session'}
                          </div>
                        )}
                        <div className="text-[10px] text-slate-500 mt-1">
                          {account.access_token_expires_at ? `token expires ${formatTime(account.access_token_expires_at)}` : 'no token expiry metadata'}
                        </div>
                      </td>
                      <td className="px-6 py-5">
                        <span
                          className={`inline-flex items-center px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border ${
                            isSelected ? 'border-blue-500/30 bg-blue-500/10 text-blue-300' : 'border-slate-700 bg-slate-800/50 text-slate-300'
                          }`}
                        >
                          {account.allocation || (isSelected ? 'cli-primary' : 'pool')}
                        </span>
                      </td>
                      <td className="px-6 py-5">
                        <span className={`inline-flex items-center px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border ${statusClass(account.status)}`}>
                          {account.status}
                        </span>
                        <div className="text-[10px] text-slate-500 uppercase tracking-widest mt-2">
                          {account.cli_primary ? 'CLI primary' : 'pool member'}
                        </div>
                        {isWhamAccount(account) && (
                          <div className="text-[10px] text-slate-600 mt-1">
                            {account.rate_limit_reached ? 'limit reached' : account.rate_limit_allowed ? 'usage allowed' : 'usage blocked'}
                          </div>
                        )}
                        <div className="text-[10px] text-slate-600 mt-1">
                          {account.last_used_at ? `last used ${formatTime(account.last_used_at)}` : 'not used yet'}
                        </div>
                        <div className="text-[10px] text-slate-600 mt-1">
                          {account.cooldown_until ? `cooldown until ${formatTime(account.cooldown_until)}` : 'no cooldown'}
                        </div>
                      </td>
                      <td className="px-6 py-5 text-right">
                        {isWhamAccount(account) && (
                          <div className="mb-3 text-[10px] text-slate-500">
                            local msgs {account.approx_local_messages_min ?? '—'}-{account.approx_local_messages_max ?? '—'} | cloud msgs {account.approx_cloud_messages_min ?? '—'}-{account.approx_cloud_messages_max ?? '—'}
                          </div>
                        )}
                        <div className="flex justify-end gap-3">
                          <button
                            onClick={() => selectMutation.mutate(account.account_id)}
                            disabled={isSelected || selectMutation.isPending}
                            className={`inline-flex items-center gap-2 px-4 py-2 rounded-xl text-xs font-black uppercase tracking-widest transition-all ${
                              isSelected
                                ? 'bg-emerald-500/10 text-emerald-300 border border-emerald-500/20 cursor-default'
                                : 'bg-white text-black hover:bg-blue-500 hover:text-white border border-transparent'
                            } disabled:opacity-50`}
                          >
                            <ArrowRightLeft size={14} />
                            {isSelected ? 'Selected' : 'Use for CLI'}
                          </button>
                          <button
                            onClick={() => deleteMutation.mutate(account.account_id)}
                            className="p-2 text-slate-500 hover:text-rose-500 transition-colors"
                            title="Delete account"
                          >
                            <Trash2 size={18} />
                          </button>
                        </div>
                      </td>
                    </tr>
                  );
                })
              )}
            </tbody>
          </table>
        </div>
        <CursorPagination
          countLabel={`${totals.totalAccounts} total accounts`}
          pageLabel={`${accounts.length} shown`}
          canGoBack={canGoBack}
          canGoNext={canGoNext}
          onBack={goToPreviousPage}
          onNext={goToNextPage}
        />
      </div>
    </div>
  );
};

export default Accounts;
