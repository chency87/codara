import React from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Activity, Cpu, Database, Server, ShieldCheck, Users, Wallet, History } from 'lucide-react';
import type { LucideIcon } from 'lucide-react';
import type { AuditLogRecord, OverviewPayload, ProviderHealthRecord } from '../types/api';

const statusClass = (status?: string) => {
  if (status === 'ok') return 'text-emerald-400 border-emerald-500/20 bg-emerald-500/10';
  if (status === 'down') return 'text-rose-400 border-rose-500/20 bg-rose-500/10';
  return 'text-amber-400 border-amber-500/20 bg-amber-500/10';
};

const MetricCard = ({ title, value, hint, icon: Icon }: { title: string; value: string; hint: string; icon: LucideIcon }) => (
  <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
    <div className="mb-4 flex items-center justify-between">
      <div className="rounded-2xl bg-slate-800/60 p-3 text-blue-400">
        <Icon size={18} />
      </div>
      <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">{title}</span>
    </div>
    <div className="text-3xl font-black text-white">{value}</div>
    <div className="mt-2 text-xs text-slate-500">{hint}</div>
  </div>
);

const Overview = () => {
  const { data, isLoading, error } = useQuery<OverviewPayload>({
    queryKey: ['overview-summary'],
    queryFn: async () => (await axios.get('/management/v1/overview')).data.data,
    refetchInterval: 30000,
  });

  if (isLoading) {
    return <div className="p-12 text-slate-500 font-bold uppercase tracking-widest text-xs animate-pulse">Loading overview…</div>;
  }

  if (error) {
    return <div className="p-12 text-rose-300 font-medium">Overview loading failed.</div>;
  }

  const summary = data?.summary || {};
  const health = data?.health || {};
  const providers = data?.providers || [];
  const recentAudit = data?.recent_audit || [];
  const runtime = data?.runtime || {};

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700 space-y-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-4xl font-black tracking-tight text-white mb-2">System Overview</h2>
          <p className="text-slate-500 font-medium">Essential runtime, session, provider, and operator activity signals from the live control plane.</p>
        </div>
        <div className={`inline-flex items-center gap-2 rounded-full border px-4 py-2 text-xs font-black uppercase tracking-widest ${statusClass(health.status)}`}>
          <ShieldCheck size={14} />
          {health.status || 'unknown'}
        </div>
      </header>

      <div className="grid grid-cols-1 gap-6 md:grid-cols-2 xl:grid-cols-4">
        <MetricCard title="Sessions" value={`${summary.active_sessions || 0} active`} hint={`${summary.dirty_sessions || 0} dirty / ${summary.sessions_total || 0} total`} icon={Activity} />
        <MetricCard title="Accounts" value={`${summary.accounts_available || 0} ready`} hint={`${summary.cooldown_accounts || 0} cooldown · ${summary.expired_accounts || 0} expired`} icon={Wallet} />
        <MetricCard title="Users" value={`${summary.active_users || 0} active`} hint={`${summary.active_keys || 0} active keys · ${summary.users_total || 0} total users`} icon={Users} />
        <MetricCard title="Usage 30d" value={(summary.total_tokens_30d || 0).toLocaleString()} hint={`${summary.total_requests_30d || 0} requests across all users`} icon={History} />
      </div>

      <div className="grid grid-cols-1 gap-8 xl:grid-cols-[1.4fr_1fr]">
        <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-8">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h3 className="text-xl font-black text-white">Component Health</h3>
              <p className="text-xs text-slate-500 mt-1">Measured control-plane checks instead of placeholder latency values.</p>
            </div>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">
              {health.checked_at ? new Date(health.checked_at).toLocaleTimeString() : 'n/a'}
            </div>
          </div>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
            {[
              { key: 'gateway', label: 'Gateway', icon: Server },
              { key: 'orchestrator', label: 'Orchestrator', icon: Cpu },
              { key: 'state_store', label: 'State Store', icon: Database },
            ].map(({ key, label, icon: Icon }) => {
              const component = health.components?.[key] || {};
              return (
                <div key={key} className="rounded-2xl border border-slate-800 bg-black/30 p-5">
                  <div className="mb-3 flex items-center justify-between">
                    <div className="flex items-center gap-2 text-white">
                      <Icon size={16} className="text-blue-400" />
                      <span className="font-black">{label}</span>
                    </div>
                    <span className={`rounded-full border px-2 py-1 text-[10px] font-black uppercase tracking-widest ${statusClass(component.status)}`}>
                      {component.status || 'unknown'}
                    </span>
                  </div>
                  <div className="text-2xl font-black text-white">{component.latency_ms ?? 'n/a'} ms</div>
                  <div className="mt-2 text-xs text-slate-500">Lightweight runtime health check latency</div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-8">
          <h3 className="text-xl font-black text-white mb-6">Runtime Config</h3>
          <div className="space-y-4">
            {[
              ['Workspaces Root', runtime.workspaces_root || 'n/a'],
              ['Max Concurrency', runtime.max_concurrency ?? 'n/a'],
              ['Session TTL', runtime.session_ttl_hours != null ? `${runtime.session_ttl_hours}h` : 'n/a'],
              ['Compression Threshold', runtime.compression_threshold ?? 'n/a'],
              ['Codex Usage Endpoints', Array.isArray(runtime.codex_usage_endpoints) ? runtime.codex_usage_endpoints.length : 'n/a'],
            ].map(([label, value]) => (
              <div key={String(label)} className="flex items-center justify-between rounded-2xl border border-slate-800 bg-black/30 px-4 py-3">
                <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">{label}</span>
                <span className="text-sm font-bold text-white text-right max-w-[55%] break-words">{value}</span>
              </div>
            ))}
          </div>
        </section>
      </div>

      <div className="grid grid-cols-1 gap-8 xl:grid-cols-[1.2fr_1fr]">
        <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-8">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h3 className="text-xl font-black text-white">Provider Footprint</h3>
              <p className="text-xs text-slate-500 mt-1">Configured providers, account coverage, and session load.</p>
            </div>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">{providers.length} providers</div>
          </div>
          <div className="space-y-4">
            {providers.map((provider: ProviderHealthRecord) => (
              <div key={provider.provider} className="rounded-2xl border border-slate-800 bg-black/30 p-5">
                <div className="mb-4 flex items-center justify-between">
                  <div>
                    <div className="text-lg font-black text-white uppercase">{provider.provider}</div>
                    <div className="text-xs text-slate-500 mt-1">
                      {provider.accounts_total} accounts · {provider.active_sessions} active sessions · {(provider.total_tokens || 0).toLocaleString()} tokens
                    </div>
                  </div>
                  <span className={`rounded-full border px-3 py-1 text-[10px] font-black uppercase tracking-widest ${statusClass(provider.status)}`}>
                    {provider.status}
                  </span>
                </div>
                <div className="grid grid-cols-2 gap-3 text-xs text-slate-400 md:grid-cols-4">
                  <div className="rounded-xl bg-slate-900/60 px-3 py-2"><span className="block text-slate-500">Ready</span><span className="font-black text-white">{provider.accounts_available || 0}</span></div>
                  <div className="rounded-xl bg-slate-900/60 px-3 py-2"><span className="block text-slate-500">Cooldown</span><span className="font-black text-white">{provider.accounts_in_cooldown || 0}</span></div>
                  <div className="rounded-xl bg-slate-900/60 px-3 py-2"><span className="block text-slate-500">Expired</span><span className="font-black text-white">{provider.accounts_expired || 0}</span></div>
                  <div className="rounded-xl bg-slate-900/60 px-3 py-2"><span className="block text-slate-500">CLI Primary</span><span className="font-black text-white">{provider.cli_primary_accounts || 0}</span></div>
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-8">
          <div className="mb-6 flex items-center justify-between">
            <div>
              <h3 className="text-xl font-black text-white">Recent Audit Activity</h3>
              <p className="text-xs text-slate-500 mt-1">Latest control-plane changes and system events.</p>
            </div>
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">{recentAudit.length} items</div>
          </div>
          <div className="space-y-3">
            {recentAudit.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-slate-800 px-4 py-8 text-center text-xs font-bold uppercase tracking-widest text-slate-600">
                No recent audit entries
              </div>
            ) : recentAudit.map((log: AuditLogRecord) => (
              <div key={log.audit_id} className="rounded-2xl border border-slate-800 bg-black/30 px-4 py-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-black text-white">{log.action}</div>
                    <div className="mt-1 text-xs text-slate-500">{log.actor} · {log.target_type}:{log.target_id}</div>
                  </div>
                  <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">
                    {new Date(log.timestamp * 1000).toLocaleTimeString()}
                  </div>
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  );
};

export default Overview;
