import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Terminal } from 'lucide-react';
import type { CliRunMeta } from '../../types/api';

interface SessionCardLiveOutputProps {
  sessionId: string;
}

const getErrorMessage = (error: unknown) => {
  if (axios.isAxiosError(error)) {
    return error.response?.data?.detail || error.response?.data?.message || error.message;
  }
  if (error instanceof Error) {
    return error.message;
  }
  return 'Request failed';
};

export const SessionCardLiveOutput = ({ sessionId }: SessionCardLiveOutputProps) => {
  const [stream, setStream] = useState<'stdout' | 'stderr'>('stdout');

  const { data: runs } = useQuery({
    queryKey: ['cli-runs-running', sessionId],
    queryFn: async (): Promise<CliRunMeta[]> =>
      (await axios.get(`/management/v1/sessions/${sessionId}/cli-runs`, { params: { status: 'running', limit: 1 } })).data.data,
    refetchInterval: 2000,
  });

  const current = runs && runs.length > 0 ? runs[0] : null;

  const { data: output, isLoading, error } = useQuery({
    queryKey: ['cli-run-tail', sessionId, current?.provider, current?.run_id, stream],
    enabled: Boolean(current?.provider && current?.run_id),
    queryFn: async (): Promise<string> => {
      const resp = await axios.get(
        `/management/v1/sessions/${sessionId}/cli-runs/${current!.provider}/${current!.run_id}/${stream}`,
        { params: { tail_bytes: 8192 } },
      );
      return String(resp.data || '');
    },
    refetchInterval: 2000,
  });

  if (!current) {
    return (
      <div className="rounded-2xl border border-dashed border-slate-800 bg-black/20 px-4 py-4 text-[10px] font-black uppercase tracking-widest text-slate-600">
        No active CLI output
      </div>
    );
  }

  return (
    <div className="rounded-2xl border border-slate-800 bg-black/60 overflow-hidden">
      <div className="flex items-center justify-between gap-2 border-b border-slate-800 px-3 py-2 bg-black/40">
        <div className="text-[10px] font-black uppercase tracking-widest text-slate-500 flex items-center gap-2">
          <Terminal size={12} className="text-blue-400" />
          <span>CLI TAIL</span>
        </div>
        <div className="flex items-center gap-2">
          {(['stdout', 'stderr'] as const).map((tab) => (
            <button
              key={tab}
              onClick={(e) => { e.stopPropagation(); setStream(tab); }}
              className={`rounded-lg border px-2 py-1 text-[10px] font-black uppercase tracking-widest transition-all ${
                stream === tab
                  ? 'border-blue-500/30 bg-blue-600/10 text-blue-300'
                  : 'border-slate-800 bg-black/10 text-slate-500 hover:text-white'
              }`}
            >
              {tab}
            </button>
          ))}
        </div>
      </div>
      <div className="px-3 py-2">
        {error && (
          <div className="mb-2 rounded-xl border border-rose-500/20 bg-rose-500/10 px-3 py-2 text-xs font-medium text-rose-200">
            {getErrorMessage(error)}
          </div>
        )}
        <pre className="h-44 overflow-auto rounded-xl bg-black/80 p-3 text-[12px] leading-relaxed font-mono text-emerald-200 border border-slate-800">
          {isLoading ? '$ tail -f …' : (output || '$ (no output yet)')}
        </pre>
      </div>
    </div>
  );
};

export default SessionCardLiveOutput;