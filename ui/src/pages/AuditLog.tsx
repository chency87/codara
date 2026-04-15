import React, { useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { History, User, Search, Fingerprint, ChevronDown, ChevronUp, Code } from 'lucide-react';
import CursorPagination from '../components/CursorPagination';
import type { ApiEnvelope, AuditLogRecord } from '../types/api';

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

const formatState = (value?: string | null, emptyLabel = '// No state recorded') => {
  if (!value) return emptyLabel;
  try {
    return JSON.stringify(JSON.parse(value), null, 2);
  } catch {
    return value;
  }
};

const AuditRow = ({ log }: { log: AuditLogRecord }) => {
  const [expanded, setExpanded] = useState(false);

  return (
    <div className="border-b border-slate-800/40 last:border-0 group">
      <div 
        className="px-8 py-6 flex items-center justify-between cursor-pointer hover:bg-white/[0.01] transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className="flex items-center space-x-6">
          <div className="text-[10px] font-black text-slate-600 tabular-nums uppercase w-32">
            {new Date(log.timestamp * 1000).toLocaleString()}
          </div>
          <div className="flex items-center space-x-2 w-48">
            <User size={12} className="text-slate-500" />
            <span className="text-xs font-bold text-slate-300">{log.actor}</span>
          </div>
          <div className="w-48">
            <span className={`px-2.5 py-1 rounded-lg text-[10px] font-black uppercase tracking-widest border ${
              log.action.includes('removed') || log.action.includes('terminated') 
                ? 'bg-rose-500/10 text-rose-500 border-rose-500/20' 
                : 'bg-blue-500/10 text-blue-500 border-blue-500/20'
            }`}>
              {log.action}
            </span>
          </div>
          <div className="flex items-center space-x-2 text-slate-500">
            <Fingerprint size={12} />
            <span className="text-[10px] font-mono tabular-nums">{log.target_type}:{log.target_id}</span>
          </div>
        </div>
        {expanded ? <ChevronUp size={16} className="text-slate-600" /> : <ChevronDown size={16} className="text-slate-600" />}
      </div>

      {expanded && (
        <div className="px-8 pb-8 pt-2 grid grid-cols-2 gap-8 animate-in fade-in slide-in-from-top-2 duration-300">
          <div className="space-y-3">
            <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-widest flex items-center space-x-2">
              <Code size={12} />
              <span>Before State</span>
            </h4>
            <pre className="bg-black border border-slate-800 rounded-2xl p-4 text-[10px] font-mono text-slate-400 overflow-auto max-h-60">
              {formatState(log.before_state, '// No previous state recorded')}
            </pre>
          </div>
          <div className="space-y-3">
            <h4 className="text-[10px] font-black text-slate-500 uppercase tracking-widest flex items-center space-x-2">
              <Code size={12} className="text-blue-500" />
              <span>After State</span>
            </h4>
            <pre className="bg-black border border-slate-800 rounded-2xl p-4 text-[10px] font-mono text-blue-400 overflow-auto max-h-60 shadow-[0_0_30px_rgba(37,99,235,0.05)]">
              {formatState(log.after_state, '// No final state recorded')}
            </pre>
          </div>
        </div>
      )}
    </div>
  );
};

const AuditLog = () => {
  const [cursor, setCursor] = useState<string | null>(null);
  const [cursorHistory, setCursorHistory] = useState<Array<string | null>>([]);
  const [search, setSearch] = useState('');
  const [actor, setActor] = useState('');
  const [action, setAction] = useState('');
  const [targetType, setTargetType] = useState('');

  const resetPagination = () => {
    setCursor(null);
    setCursorHistory([]);
  };

  const { data, isLoading, error } = useQuery<ApiEnvelope<AuditLogRecord[]>>({
    queryKey: ['audit-logs', cursor, search, actor, action, targetType],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/audit', {
        params: {
          limit: PAGE_SIZE,
          after: cursor || undefined,
          search: search.trim() || undefined,
          actor: actor.trim() || undefined,
          action: action.trim() || undefined,
          target_type: targetType || undefined,
        },
      });
      return resp.data;
    }
  });

  const logs = useMemo(() => data?.data || [], [data]);
  const pageMeta = data?.meta?.page;
  const visibleSummary = useMemo(() => ({
    operator: logs.filter((log) => String(log.actor || '').startsWith('operator:')).length,
    system: logs.filter((log) => String(log.actor || '').startsWith('system:')).length,
    user: logs.filter((log) => String(log.actor || '').startsWith('user:')).length,
    sessions: logs.filter((log) => log.target_type === 'session').length,
  }), [logs]);
  const canGoBack = cursorHistory.length > 0;
  const canGoNext = logs.length === PAGE_SIZE && Boolean(pageMeta?.cursor);

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
      <header className="mb-12">
        <h2 className="text-4xl font-black tracking-tight text-white mb-2">Audit Control</h2>
        <p className="text-slate-500 font-medium">Immutable ledger of management mutations and operator interventions.</p>
      </header>

      {error && (
        <div className="mb-6 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          Audit loading failed: {getErrorMessage(error)}
        </div>
      )}

      <div className="mb-6 grid grid-cols-1 gap-4 md:grid-cols-4">
        {[
          ['Visible events', logs.length],
          ['Operator actions', visibleSummary.operator],
          ['System events', visibleSummary.system],
          ['Session events', visibleSummary.sessions],
        ].map(([label, value]) => (
          <div key={String(label)} className="rounded-3xl border border-slate-800 bg-slate-900/40 p-5">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">{label}</div>
            <div className="mt-2 text-2xl font-black text-white">{value as number}</div>
          </div>
        ))}
      </div>

      <div className="mb-6 grid grid-cols-1 gap-4 rounded-3xl border border-slate-800/60 bg-slate-900/40 p-6 md:grid-cols-4">
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Search</span>
          <div className="relative">
            <Search size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
            <input
              className="w-full rounded-xl border border-slate-800 bg-black py-3 pl-10 pr-4 text-sm text-white outline-none focus:border-blue-500"
              placeholder="action, target id, or payload text"
              value={search}
              onChange={(e) => {
                resetPagination();
                setSearch(e.target.value);
              }}
            />
          </div>
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Actor</span>
          <input
            className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
            placeholder="operator:svc-account-01"
            value={actor}
            onChange={(e) => {
              resetPagination();
              setActor(e.target.value);
            }}
          />
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Action</span>
          <input
            className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
            placeholder="usage.fetch.failed"
            value={action}
            onChange={(e) => {
              resetPagination();
              setAction(e.target.value);
            }}
          />
        </label>
        <label className="space-y-2">
          <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Target Type</span>
          <select
            className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white outline-none focus:border-blue-500"
            value={targetType}
            onChange={(e) => {
              resetPagination();
              setTargetType(e.target.value);
            }}
          >
            <option value="">All targets</option>
            <option value="account">account</option>
            <option value="usage">usage</option>
            <option value="session">session</option>
            <option value="user">user</option>
            <option value="api_key">api_key</option>
          </select>
        </label>
      </div>

      <div className="bg-slate-900/40 backdrop-blur-md border border-slate-800/60 rounded-3xl overflow-hidden shadow-2xl">
        <div className="bg-slate-800/30 border-b border-slate-800/60 px-8 py-4 flex items-center justify-between">
          <div className="flex items-center space-x-4">
            <History size={18} className="text-blue-500" />
            <span className="text-xs font-black text-slate-400 uppercase tracking-widest">Mutation Log</span>
          </div>
          <div className="text-[10px] font-bold text-slate-500 uppercase">{pageMeta?.total_count ?? logs.length} entries in current view</div>
        </div>
        
        <div className="divide-y divide-slate-800/40">
          {isLoading ? (
            <div className="px-8 py-24 text-center text-slate-600 font-bold uppercase tracking-widest text-xs animate-pulse">
              Synchronizing Ledger...
            </div>
          ) : logs?.length === 0 ? (
            <div className="px-8 py-24 text-center text-slate-600 font-bold uppercase tracking-widest text-xs">
              No mutations recorded in current epoch
            </div>
          ) : (
            logs.map((log) => (
              <AuditRow key={log.audit_id} log={log} />
            ))
          )}
        </div>
        <CursorPagination
          countLabel={`${pageMeta?.total_count ?? logs.length} total audit entries`}
          pageLabel={`${logs.length} shown`}
          canGoBack={canGoBack}
          canGoNext={canGoNext}
          onBack={goToPreviousPage}
          onNext={goToNextPage}
        />
      </div>
    </div>
  );
};

export default AuditLog;
