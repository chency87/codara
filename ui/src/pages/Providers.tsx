import React from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { ShieldCheck, Bot } from 'lucide-react';
import type { ProviderHealthRecord } from '../types/api';
import { dashboardPollHeaders } from '../api/dashboardPoll';

const statusClass = (status?: string) => {
  if (status === 'ready') return 'text-emerald-400 border-emerald-500/20 bg-emerald-500/10';
  if (status === 'unavailable') return 'text-rose-400 border-rose-500/20 bg-rose-500/10';
  return 'text-amber-400 border-amber-500/20 bg-amber-500/10';
};

const ProviderCard = ({ provider }: { provider: ProviderHealthRecord }) => (
  <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-8">
    <div className="mb-6 flex items-start justify-between gap-4">
      <div>
        <h3 className="text-2xl font-black text-white uppercase">{provider.provider}</h3>
        <p className="mt-1 text-xs text-slate-500">
          {provider.active_sessions} active sessions
        </p>
      </div>
      <span className={`inline-flex items-center gap-2 rounded-full border px-3 py-1 text-[10px] font-black uppercase tracking-widest ${statusClass(provider.status)}`}>
        <ShieldCheck size={12} />
        {provider.status}
      </span>
    </div>

    <div className="grid grid-cols-1 gap-4">
      <div className="rounded-2xl border border-slate-800 bg-black/30 p-4">
        <div className="mb-1 flex items-center gap-2 text-slate-500">
          <Bot size={12} />
          <span className="text-[10px] font-black uppercase tracking-widest">Default Model</span>
        </div>
        <div className="text-sm font-black text-white break-all">{provider.default_model || 'n/a'}</div>
      </div>
      <div className="rounded-2xl border border-slate-800 bg-black/30 p-4">
        <div className="mb-1 flex items-center gap-2 text-slate-500">
          <ShieldCheck size={12} />
          <span className="text-[10px] font-black uppercase tracking-widest">Runtime Ready</span>
        </div>
        <div className="text-2xl font-black text-white">{provider.runtime_available ? 'Yes' : 'No'}</div>
      </div>
    </div>

    <div className="mt-6 grid grid-cols-2 gap-4 text-xs text-slate-400">
      <div className="rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3">
        <span className="block text-slate-500">Model inventory</span>
        <span className="text-sm font-bold text-white">
          {provider.model_count || 0} models · {provider.models_source || 'n/a'}
        </span>
      </div>
      <div className="rounded-2xl border border-slate-800 bg-slate-950/60 px-4 py-3">
        <span className="block text-slate-500">Runtime detail</span>
        <span className="text-sm font-bold text-white">
          {provider.runtime_detail || provider.models_status || 'Healthy'}
        </span>
      </div>
    </div>

    <div className="mt-6 flex items-center justify-between border-t border-slate-800 pt-4 text-[10px] font-black uppercase tracking-widest text-slate-500">
      <span>Health check: {provider.latency_ms ?? 'n/a'} ms</span>
      <span>{provider.checked_at ? new Date(provider.checked_at).toLocaleTimeString() : 'n/a'}</span>
    </div>
  </div>
);

const Providers = () => {
  const { data: stats, isLoading } = useQuery<ProviderHealthRecord[]>({
    queryKey: ['provider-stats'],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/health/providers', { headers: dashboardPollHeaders });
      return resp.data.data || [];
    },
    refetchInterval: 30000,
  });

  const providers = stats || [];
  const totalSessions = providers.reduce((sum, item) => sum + (item.active_sessions || 0), 0);

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700 space-y-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-4xl font-black tracking-tight text-white mb-2">Provider Control</h2>
          <p className="text-slate-500 font-medium">Provider health, local runtime readiness, and model inventory.</p>
        </div>
        <div className="grid grid-cols-1 gap-3 text-center">
          <div className="rounded-2xl border border-slate-800 bg-slate-900/40 px-5 py-4 min-w-[120px]">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Sessions</div>
            <div className="mt-1 text-2xl font-black text-white">{totalSessions}</div>
          </div>
        </div>
      </header>

      {isLoading ? (
        <div className="text-slate-500 font-bold uppercase tracking-widest text-xs animate-pulse">Loading providers…</div>
      ) : (
        <div className="grid grid-cols-1 gap-8 xl:grid-cols-3">
          {providers.map((provider) => (
            <ProviderCard key={provider.provider} provider={provider} />
          ))}
        </div>
      )}
    </div>
  );
};

export default Providers;
