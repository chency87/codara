import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import axios from 'axios';
import { Trash2, Terminal, FolderCode, Hash, X, History, Database, Cpu, ChevronRight, Copy, Check } from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import type { ApiEnvelope, SessionDetail, SessionListItem, SessionTurn } from '../types/api';
import { dashboardPollHeaders } from '../api/dashboardPoll';

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

const SessionDrawer = ({ sessionId, onClose }: { sessionId: string, onClose: () => void }) => {
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
    <div className="fixed inset-y-0 right-0 w-1/3 bg-slate-900 border-l border-slate-800 shadow-2xl z-50 animate-in slide-in-from-right duration-300 flex flex-col">
      <div className="p-8 border-b border-slate-800 flex justify-between items-center bg-black/20">
        <div>
          <h3 className="text-xl font-black text-white tracking-tight">Thread Inspector</h3>
          <p className="text-[10px] font-bold text-slate-500 uppercase tracking-widest mt-1">ID: {sessionId}</p>
        </div>
        <button onClick={onClose} className="p-2 hover:bg-white/5 rounded-xl text-slate-500 hover:text-white transition-all">
          <X size={20} />
        </button>
      </div>

        <div className="flex-1 overflow-auto p-8 space-y-10">
          {/* Metadata Grid */}
          <div className="grid grid-cols-2 gap-4">
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
              <div className="text-[10px] font-mono text-slate-500 mt-1">{session.user_email || session.api_key_label || 'No user key recorded'}</div>
            </div>
          </div>

          <div className="grid grid-cols-2 gap-4">
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

        {/* Turn History */}
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
                    <span className="text-[10px] font-bold text-emerald-500 uppercase tabular-nums">+{turn.output_tokens} tokens</span>
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
      </div>
    </div>
  );
};

const Sessions = () => {
  const queryClient = useQueryClient();
  const [selectedSession, setSelectedSession] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);

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

  const terminateMutation = useMutation({
    mutationFn: (id: string) => axios.delete(`/management/v1/sessions/${id}`),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['sessions'] });
      setSelectedSession(null);
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

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-12 flex justify-between items-end">
        <div>
          <h2 className="text-4xl font-black tracking-tight text-white mb-2">Session Registry</h2>
          <p className="text-slate-500 font-medium">Monitor and manage active agent conversation threads.</p>
        </div>
        <div className="bg-blue-600/10 border border-blue-500/20 px-4 py-2 rounded-xl">
          <span className="text-xs font-black text-blue-400 uppercase tracking-widest">{pageMeta?.total_count ?? sessions.length} Total Sessions</span>
        </div>
      </header>

      {error && (
        <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          Session loading failed: {getErrorMessage(error)}
        </div>
      )}

      <div className="bg-slate-900/40 backdrop-blur-md border border-slate-800/60 rounded-3xl overflow-hidden shadow-2xl">
        <div className="overflow-x-auto">
          <table className="w-full text-left border-collapse">
            <thead>
              <tr className="bg-slate-800/30 border-b border-slate-800/60">
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Identification</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Context</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Owner</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">State</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em]">Workspace</th>
                <th className="px-8 py-5 text-[10px] font-black text-slate-500 uppercase tracking-[0.2em] text-right">Operations</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/40 text-slate-300">
              {sessions?.length === 0 ? (
                <tr>
                  <td colSpan={6} className="px-8 py-24 text-center">
                    <div className="flex flex-col items-center justify-center space-y-4">
                      <div className="p-4 bg-slate-800/50 rounded-full text-slate-600">
                        <Terminal size={32} />
                      </div>
                      <p className="text-slate-500 font-bold uppercase tracking-widest text-xs">No active execution threads</p>
                    </div>
                  </td>
                </tr>
              ) : (
                sessions.map((session) => (
                  <tr 
                    key={session.client_session_id} 
                    className={`group hover:bg-white/[0.02] transition-colors cursor-pointer ${selectedSession === session.client_session_id ? 'bg-white/[0.03]' : ''}`}
                    onClick={() => setSelectedSession(session.client_session_id)}
                  >
                    <td className="px-8 py-6">
                      <div className="flex items-center space-x-3">
                        <div className="p-2 bg-slate-800 rounded-lg text-slate-400 group-hover:text-blue-400 transition-colors">
                          <Hash size={16} />
                        </div>
                        <div>
                          <div className="flex items-center space-x-2">
                            <div className="text-sm font-black text-white font-mono leading-none tabular-nums truncate max-w-[120px]">
                              {session.client_session_id.split('-')[0]}...
                            </div>
                            <button 
                              onClick={(e) => { e.stopPropagation(); copyId(session.client_session_id); }}
                              className="text-slate-600 hover:text-slate-400 transition-colors"
                            >
                              {copied === session.client_session_id ? <Check size={12} className="text-emerald-500" /> : <Copy size={12} />}
                            </button>
                          </div>
                          <div className="text-[10px] font-bold text-slate-500 uppercase tracking-tighter tabular-nums mt-1">
                            Last Active: {new Date(session.updated_at * 1000).toLocaleTimeString()}
                          </div>
                        </div>
                      </div>
                    </td>
                    <td className="px-8 py-6">
                      <div className="flex items-center space-x-2">
                        <div className="px-2.5 py-1 bg-slate-800 border border-slate-700 rounded-lg text-[10px] font-black text-slate-300 uppercase tracking-widest">
                          {session.provider}
                        </div>
                      </div>
                    </td>
                    <td className="px-8 py-6">
                      <div className="space-y-1">
                        <div className="text-sm font-bold text-white">
                          {session.user_display_name || 'System / Unbound'}
                        </div>
                        <div className="text-[10px] font-mono text-slate-500">
                          {session.api_key_label || session.user_email || 'No user key'}
                        </div>
                      </div>
                    </td>
                    <td className="px-8 py-6">
                      <span className={`inline-flex items-center space-x-1.5 px-3 py-1 rounded-full text-[10px] font-black uppercase tracking-widest border ${
                        session.status === 'idle' 
                          ? 'bg-emerald-500/10 text-emerald-500 border-emerald-500/20' 
                          : session.status === 'active' 
                            ? 'bg-blue-500/10 text-blue-500 border-blue-500/20'
                            : 'bg-rose-500/10 text-rose-500 border-rose-500/20'
                      }`}>
                        <div className={`w-1 h-1 rounded-full ${
                          session.status === 'idle' ? 'bg-emerald-500' : session.status === 'active' ? 'bg-blue-500' : 'bg-rose-500'
                        } ${session.status === 'active' ? 'animate-pulse' : ''}`}></div>
                        <span>{session.status}</span>
                      </span>
                    </td>
                    <td className="px-8 py-6">
                      <div className="flex items-center space-x-2 max-w-xs overflow-hidden">
                        <FolderCode size={14} className="text-slate-600 flex-shrink-0" />
                        <span className="text-xs font-bold text-slate-500 truncate hover:text-slate-300 transition-colors cursor-help" title={session.cwd_path}>
                          {session.cwd_path.split('/').pop()}
                        </span>
                      </div>
                    </td>
                    <td className="px-8 py-6 text-right">
                      <div className="flex justify-end space-x-2 opacity-0 group-hover:opacity-100 transition-opacity">
                        <button 
                          onClick={(e) => { e.stopPropagation(); terminateMutation.mutate(session.client_session_id); }}
                          className="p-2.5 text-slate-500 hover:text-rose-500 hover:bg-rose-500/10 rounded-xl transition-all border border-transparent hover:border-rose-500/20"
                          title="Evict Session"
                        >
                          <Trash2 size={18} />
                        </button>
                        <div className="p-2.5 text-slate-500">
                          <ChevronRight size={18} />
                        </div>
                      </div>
                    </td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
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

      {selectedSession && (
        <SessionDrawer sessionId={selectedSession} onClose={() => setSelectedSession(null)} />
      )}
    </div>
  );
};

export default Sessions;
