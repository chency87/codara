import React, { useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Activity, CalendarRange, LineChart, Users } from 'lucide-react';
import type { TopUserRecord, UsageSummaryPayload, UsageTimeseriesPoint } from '../types/api';

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

const buildLinePath = (values: number[], width: number, height: number) => {
  if (!values.length) return '';
  const max = Math.max(...values, 1);
  return values
    .map((value, index) => {
      const x = values.length === 1 ? width / 2 : (index / (values.length - 1)) * width;
      const y = height - (value / max) * height;
      return `${index === 0 ? 'M' : 'L'} ${x.toFixed(2)} ${y.toFixed(2)}`;
    })
    .join(' ');
};

const Usage = () => {
  const usageQuery = useQuery<UsageSummaryPayload>({
    queryKey: ['usage-metrics'],
    queryFn: async () => (await axios.get('/management/v1/usage')).data.data,
    refetchInterval: 30000,
  });

  const seriesQuery = useQuery<UsageTimeseriesPoint[]>({
    queryKey: ['usage-timeseries'],
    queryFn: async () => (await axios.get('/management/v1/usage/timeseries')).data.data,
    refetchInterval: 30000,
  });

  const error = usageQuery.error || seriesQuery.error;
  const isLoading = usageQuery.isLoading || seriesQuery.isLoading;
  const usage = usageQuery.data || {};
  const timeseries = useMemo(() => seriesQuery.data || [], [seriesQuery.data]);
  const topUsers = usage.top_users || [];

  const chart = useMemo(() => {
    const tokenValues = timeseries.map((row) => Number(row.input_tokens || 0) + Number(row.output_tokens || 0));
    const requestValues = timeseries.map((row) => Number(row.request_count || 0));
    return {
      tokenPath: buildLinePath(tokenValues, 640, 180),
      requestPath: buildLinePath(requestValues, 640, 180),
      totalTokens: tokenValues.reduce((sum, value) => sum + value, 0),
      totalRequests: requestValues.reduce((sum, value) => sum + value, 0),
      maxTokens: Math.max(...tokenValues, 0),
    };
  }, [timeseries]);

  if (isLoading) {
    return <div className="p-12 text-slate-500 font-bold uppercase tracking-widest text-xs animate-pulse">Loading usage analytics…</div>;
  }

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700 space-y-8">
      <header className="flex flex-col gap-4 lg:flex-row lg:items-end lg:justify-between">
        <div>
          <h2 className="text-4xl font-black tracking-tight text-white mb-2">Usage Analytics</h2>
          <p className="text-slate-500 font-medium">Time-series traffic and user activity instead of duplicated account-pool quota tables.</p>
        </div>
      </header>

      {error && (
        <div className="rounded-2xl border border-rose-500/20 bg-rose-500/10 px-5 py-4 text-sm font-medium text-rose-200">
          Usage loading failed: {getErrorMessage(error)}
        </div>
      )}

      <div className="grid grid-cols-1 gap-6 md:grid-cols-4">
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500"><CalendarRange size={12} /> Days Tracked</div>
          <div className="text-3xl font-black text-white">{timeseries.length}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500"><LineChart size={12} /> Tokens (30d)</div>
          <div className="text-3xl font-black text-white">{chart.totalTokens.toLocaleString()}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500"><Activity size={12} /> Requests (30d)</div>
          <div className="text-3xl font-black text-white">{chart.totalRequests.toLocaleString()}</div>
        </div>
        <div className="rounded-3xl border border-slate-800 bg-slate-900/40 p-6">
          <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-slate-500"><Users size={12} /> Top users</div>
          <div className="text-3xl font-black text-white">{topUsers.length}</div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-8 xl:grid-cols-[1.4fr_1fr]">
        <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-8">
          <div className="mb-6">
            <h3 className="text-xl font-black text-white">Daily traffic</h3>
            <p className="mt-1 text-xs text-slate-500">Blue = tokens per day, emerald = requests per day.</p>
          </div>
          <div className="rounded-3xl border border-slate-800 bg-black/30 p-6">
            {timeseries.length === 0 ? (
              <div className="py-16 text-center text-xs font-bold uppercase tracking-widest text-slate-600">No daily usage recorded yet</div>
            ) : (
              <>
                <svg viewBox="0 0 640 200" className="w-full">
                  <path d={chart.tokenPath} fill="none" stroke="#3b82f6" strokeWidth="4" strokeLinecap="round" />
                  <path d={chart.requestPath} fill="none" stroke="#10b981" strokeWidth="3" strokeLinecap="round" strokeDasharray="6 6" />
                </svg>
                <div className="mt-4 flex justify-between text-[10px] font-black uppercase tracking-widest text-slate-500">
                  <span>{timeseries[0]?.period}</span>
                  <span>Peak {chart.maxTokens.toLocaleString()} tokens/day</span>
                  <span>{timeseries[timeseries.length - 1]?.period}</span>
                </div>
              </>
            )}
          </div>
        </section>

        <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-8">
          <div className="mb-6">
            <h3 className="text-xl font-black text-white">Top users by tokens</h3>
            <p className="mt-1 text-xs text-slate-500">Aggregated from recorded user usage.</p>
          </div>
          <div className="space-y-3">
            {topUsers.length === 0 ? (
              <div className="rounded-2xl border border-dashed border-slate-800 px-4 py-12 text-center text-xs font-bold uppercase tracking-widest text-slate-600">
                No user usage recorded yet
              </div>
            ) : topUsers.map((user: TopUserRecord) => (
              <div key={user.user_id} className="rounded-2xl border border-slate-800 bg-black/30 px-4 py-3">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <div className="text-sm font-black text-white">{user.display_name || user.user_id}</div>
                    <div className="text-[10px] font-mono text-slate-500 mt-1">{user.email || user.user_id}</div>
                  </div>
                  <div className="text-sm font-black text-white">{(user.total_tokens || 0).toLocaleString()}</div>
                </div>
                <div className="mt-2 text-xs text-slate-500">
                  {(user.request_count || 0).toLocaleString()} requests · {(user.cache_hit_tokens || 0).toLocaleString()} cache-hit tokens
                </div>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="rounded-3xl border border-slate-800 bg-slate-900/40 p-8">
        <div className="mb-6">
          <h3 className="text-xl font-black text-white">Daily ledger</h3>
          <p className="mt-1 text-xs text-slate-500">Raw daily totals across all user activity.</p>
        </div>
        <div className="overflow-x-auto">
          <table className="min-w-full text-left">
            <thead className="border-b border-slate-800 text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">
              <tr>
                <th className="px-4 py-3">Date</th>
                <th className="px-4 py-3">Input Tokens</th>
                <th className="px-4 py-3">Output Tokens</th>
                <th className="px-4 py-3">Cache Hits</th>
                <th className="px-4 py-3">Requests</th>
                <th className="px-4 py-3">Total Tokens</th>
              </tr>
            </thead>
            <tbody className="divide-y divide-slate-800/60">
              {timeseries.map((row) => {
                const totalTokens = Number(row.input_tokens || 0) + Number(row.output_tokens || 0);
                return (
                  <tr key={row.period}>
                    <td className="px-4 py-4 text-sm font-black text-white">{row.period}</td>
                    <td className="px-4 py-4 text-sm text-slate-300">{Number(row.input_tokens || 0).toLocaleString()}</td>
                    <td className="px-4 py-4 text-sm text-slate-300">{Number(row.output_tokens || 0).toLocaleString()}</td>
                    <td className="px-4 py-4 text-sm text-slate-300">{Number(row.cache_hit_tokens || 0).toLocaleString()}</td>
                    <td className="px-4 py-4 text-sm text-slate-300">{Number(row.request_count || 0).toLocaleString()}</td>
                    <td className="px-4 py-4 text-sm font-black text-white">{totalTokens.toLocaleString()}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
};

export default Usage;
