import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { useNavigate } from 'react-router-dom';
import { Terminal, Search } from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import type { ApiEnvelope, SessionListItem } from '../types/api';
import { dashboardPollHeaders } from '../api/dashboardPoll';
import { SessionCard } from '../components/sessions';

const PAGE_SIZE = 25;

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

type StatusFilter = 'all' | 'active' | 'idle' | 'dirty' | 'expired';

const STATUS_OPTIONS: { value: StatusFilter; label: string }[] = [
  { value: 'all', label: 'All' },
  { value: 'active', label: 'Active' },
  { value: 'idle', label: 'Idle' },
  { value: 'dirty', label: 'Dirty' },
  { value: 'expired', label: 'Expired' },
];

const Sessions = () => {
  const queryClient = useQueryClient();
  const navigate = useNavigate();
  const [copied, setCopied] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [search, setSearch] = useState('');
  const [statusFilter, setStatusFilter] = useState<StatusFilter>('all');

  const { data, isLoading, error } = useQuery<ApiEnvelope<SessionListItem[]>>({
    queryKey: ['sessions', cursor],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/sessions', {
        headers: dashboardPollHeaders,
        params: { limit: PAGE_SIZE, after: cursor || undefined },
      });
      return resp.data;
    },
    refetchInterval: 15000
  });

  const sessions = data?.data || [];
  const pageMeta = data?.meta?.page;

  const filteredSessions = sessions.filter((session) => {
    const needle = search.toLowerCase().trim();
    if (needle) {
      const matchesSearch =
        session.client_session_id.toLowerCase().includes(needle) ||
        session.provider.toLowerCase().includes(needle) ||
        (session.user_display_name || '').toLowerCase().includes(needle) ||
        (session.user_email || '').toLowerCase().includes(needle) ||
        (session.cwd_path || '').toLowerCase().includes(needle);
      if (!matchesSearch) return false;
    }

    if (statusFilter !== 'all') {
      if (String(session.status || '').toLowerCase() !== statusFilter) return false;
    }
    return true;
  });

  const terminateMutation = useMutation({
    mutationFn: (id: string) => axios.delete(`/management/v1/sessions/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
    }
  });

  const copyId = (id: string) => {
    navigator.clipboard.writeText(id);
    setCopied(id);
    setTimeout(() => setCopied(null), 2000);
  };

  if (isLoading) return (
    <div className="p-12 animate-pulse">
      <div className="h-8 w-48 bg-slate-800 rounded-lg mb-8"></div>
      <div className="h-64 bg-slate-900/50 rounded-3xl border border-slate-800"></div>
    </div>
  );

  const canGoBack = cursorHistory.length > 0;
  const canGoNext = sessions.length === PAGE_SIZE && Boolean(pageMeta?.cursor);

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

  const activeCount = sessions.filter(s => String(s.status || '').toLowerCase() === 'active').length;
  const idleCount = sessions.filter(s => String(s.status || '').toLowerCase() === 'idle').length;
  const dirtyCount = sessions.filter(s => String(s.status || '').toLowerCase() === 'dirty').length;

  return (
    <div className="p-6 sm:p-8 lg:p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-8">
        <h2 className="text-3xl sm:text-4xl font-black tracking-tight text-white mb-2">Sessions</h2>
        <p className="text-slate-500 font-medium">Monitor and manage active CLI sessions.</p>
      </header>

      <div className="mb-6 flex flex-col lg:flex-row lg:items-center gap-4">
        <div className="flex items-center gap-2 bg-slate-900/60 border border-slate-800 rounded-xl px-3 py-2 self-start">
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full bg-blue-500 animate-pulse" />
            <span className="text-sm font-semibold text-blue-400">{activeCount}</span>
          </div>
          <div className="w-px h-4 bg-slate-800" />
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full bg-emerald-500" />
            <span className="text-sm font-semibold text-emerald-400">{idleCount}</span>
          </div>
          <div className="w-px h-4 bg-slate-800" />
          <div className="flex items-center gap-1.5">
            <div className="w-2 h-2 rounded-full bg-amber-500" />
            <span className="text-sm font-semibold text-amber-400">{dirtyCount}</span>
          </div>
        </div>

        <div className="lg:flex-1" />

        <div className="flex flex-col sm:flex-row items-stretch sm:items-center gap-3">
          <div className="relative flex-1 sm:flex-none">
            <Search size={16} className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              type="text"
              value={search}
              onChange={(e) => setSearch(e.target.value)}
              placeholder="Search sessions..."
              className="w-full sm:w-64 pl-9 pr-4 py-2 bg-slate-900/60 border border-slate-800 rounded-xl text-sm text-white placeholder:text-slate-500 focus:outline-none focus:border-blue-500/50"
            />
          </div>

          <div className="flex items-center gap-1 bg-slate-900/60 border border-slate-800 rounded-xl p-1 overflow-x-auto custom-scrollbar">
            {STATUS_OPTIONS.map(({ value, label }) => (
              <button
                key={value}
                onClick={() => setStatusFilter(value)}
                className={`px-3 py-1.5 rounded-lg text-xs font-semibold transition-colors whitespace-nowrap ${
                  statusFilter === value
                    ? 'bg-blue-600 text-white'
                    : 'text-slate-400 hover:text-white'
                }`}
              >
                {label}
              </button>
            ))}
          </div>
        </div>
      </div>

      {error && (
        <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          Session loading failed: {getErrorMessage(error)}
        </div>
      )}

      <div className="bg-slate-900/40 border border-slate-800/60 rounded-3xl overflow-hidden">
        {filteredSessions.length === 0 ? (
          <div className="px-8 py-24 text-center">
            <div className="flex flex-col items-center justify-center space-y-4">
              <div className="p-4 bg-slate-800/50 rounded-full text-slate-600">
                <Terminal size={32} />
              </div>
              <p className="text-slate-500 font-bold uppercase tracking-widest text-xs">
                {search || statusFilter !== 'all' ? 'No matching sessions' : 'No sessions'}
              </p>
            </div>
          </div>
        ) : (
          <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 2xl:grid-cols-4 gap-4 p-6">
            {filteredSessions.map((session) => (
              <SessionCard
                key={session.client_session_id}
                session={session}
                isSelected={false}
                copied={copied === session.client_session_id}
                onCopy={() => copyId(session.client_session_id)}
                onTerminate={() => terminateMutation.mutate(session.client_session_id)}
                onOpen={() => navigate(`/sessions/${encodeURIComponent(session.client_session_id)}/history`)}
              />
            ))}
          </div>
        )}

        <div className="border-t border-slate-800/60 px-6 py-4 flex items-center justify-between">
          <div className="text-sm text-slate-500">
            Showing <span className="text-white font-semibold">{filteredSessions.length}</span> of{' '}
            <span className="text-white font-semibold">{pageMeta?.total_count ?? sessions.length}</span> sessions
          </div>
          <CursorPagination
            countLabel={`${pageMeta?.total_count ?? sessions.length} total sessions`}
            pageLabel={`${sessions.length} shown`}
            canGoBack={canGoBack}
            canGoNext={canGoNext}
            onBack={goToPreviousPage}
            onNext={goToNextPage}
          />
        </div>
      </div>

    </div>
  );
};

export default Sessions;
