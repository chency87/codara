import { useState, useEffect, useRef } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Trash2, FolderCode, Copy, Check, Terminal, RotateCcw } from 'lucide-react';
import type { SessionListItem, CliRunMeta } from '../../types/api';

interface SessionCardProps {
  session: SessionListItem;
  isSelected: boolean;
  copied: boolean;
  onCopy: () => void;
  onTerminate: () => void;
  onOpen: () => void;
}

type LogEvent = {
  type: string;
  timestamp: number;
  sessionID?: string;
  part?: {
    id?: string;
    type?: string;
    text?: string;
    tool?: string;
    state?: { status?: string };
    reason?: string;
    messageID?: string;
    snapshot?: string;
    tokens?: { total: number; input: number; output: number; reasoning: number };
    time?: { start: number; end: number };
  };
};

const statusDot = (status: string) => {
  const lowered = String(status || '').toLowerCase();
  if (lowered === 'active') return 'bg-blue-500 animate-pulse';
  if (lowered === 'dirty') return 'bg-amber-500';
  if (lowered === 'idle') return 'bg-emerald-500';
  if (lowered === 'expired') return 'bg-slate-500';
  return 'bg-slate-500';
};

const parseLogLine = (line: string): LogEvent | null => {
  try {
    const trimmed = line.trim();
    if (!trimmed) return null;
    return JSON.parse(trimmed) as LogEvent;
  } catch {
    return { type: 'raw', timestamp: Date.now(), part: { text: line } };
  }
};

const formatTimestamp = (ts: number) => {
  const date = new Date(ts);
  return date.toLocaleTimeString('en-US', { hour12: false });
};

const SessionLiveOutput = ({ sessionId }: { sessionId: string }) => {
  const [stream, setStream] = useState<'stdout' | 'stderr'>('stdout');
  const [output, setOutput] = useState<string[]>([]);
  const [connecting, setConnecting] = useState(false);
  const abortRef = useRef<AbortController | null>(null);
  const outputRef = useRef<HTMLDivElement | null>(null);
  const lineBufferRef = useRef<string>('');

  const { data: runs } = useQuery({
    queryKey: ['cli-runs-running', sessionId],
    queryFn: async (): Promise<CliRunMeta[]> => {
      const resp = await axios.get(`/management/v1/sessions/${sessionId}/cli-runs`, { params: { status: 'running', limit: 1 } });
      return resp.data.data;
    },
    refetchInterval: 3000,
  });

  const current = runs && runs.length > 0 ? runs[0] : null;

  useEffect(() => {
    if (!current?.provider || !current?.run_id) {
      setOutput([]);
      return;
    }

    setConnecting(true);
    lineBufferRef.current = '';
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;

    const connect = async () => {
      try {
        const url = `/management/v1/sessions/${sessionId}/cli-runs/${current.provider}/${current.run_id}/${stream}/stream?tail_bytes=8192&follow=true&poll_ms=100`;
        const resp = await fetch(url, { signal: controller.signal });
        
        if (!resp.ok) {
          setConnecting(false);
          return;
        }

        if (!resp.body) {
          setConnecting(false);
          return;
        }

        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          if (!value) continue;
          
          const chunk = decoder.decode(value, { stream: true });
          lineBufferRef.current += chunk;
          
          const lines = lineBufferRef.current.split('\n');
          lineBufferRef.current = lines.pop() || '';
          
          for (const line of lines) {
            setOutput(prev => {
              const newLines = [...prev, line];
              if (newLines.length > 100) {
                return newLines.slice(-100);
              }
              return newLines;
            });
          }
        }
      } catch (e) {
        if ((e as Error).name !== 'AbortError') {
          console.error('Stream error:', e);
        }
      } finally {
        setConnecting(false);
      }
    };

    connect();

    return () => {
      controller.abort();
    };
  }, [sessionId, current?.provider, current?.run_id, stream]);

  useEffect(() => {
    if (outputRef.current) {
      outputRef.current.scrollTop = outputRef.current.scrollHeight;
    }
  }, [output]);

const renderEvent = (line: string) => {
    const event = parseLogLine(line);
    
    if (!event || event.type === 'raw') {
      return (
        <div className="py-1">
          <span className="text-orange-400">[stderr]</span>
          <span className="text-slate-300 ml-2">{line.slice(0, 200)}</span>
        </div>
      );
    }

    const { type, timestamp, sessionID, part } = event;
    const time = formatTimestamp(timestamp);
    
    switch (type) {
      case 'text':
        if (!part?.text) return null;
        const textPreview = part.text.length > 300 ? part.text.slice(0, 300) + '...' : part.text;
        return (
          <div className="py-1">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">[{time}]</span>
              <span className="text-blue-400 font-medium">TEXT</span>
            </div>
            <div className="text-emerald-300 mt-1 whitespace-pre-wrap ml-16 text-xs leading-relaxed">
              {textPreview}
            </div>
          </div>
        );
        
      case 'tool_use':
        const tool = part?.tool || 'unknown';
        const status = part?.state?.status || 'unknown';
        return (
          <div className="py-1">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">[{time}]</span>
              <span className="text-amber-400 font-medium">TOOL</span>
              <span className="text-white">{tool}</span>
              <span className={`${status === 'completed' ? 'text-green-400' : 'text-blue-400'}`}>
                [{status}]
              </span>
            </div>
          </div>
        );
        
      case 'step_start':
        return (
          <div className="py-1">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">[{time}]</span>
              <span className="text-purple-400 font-medium">STEP START</span>
            </div>
          </div>
        );
        
      case 'step_finish':
        const tokens = part?.tokens;
        const reason = part?.reason || 'done';
        return (
          <div className="py-1">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">[{time}]</span>
              <span className="text-green-400 font-medium">STEP END</span>
              <span className="text-slate-400">({reason})</span>
            </div>
            {tokens && tokens.total > 0 && (
              <div className="text-xs text-slate-400 mt-0.5 ml-16">
                {tokens.total} tokens (in: {tokens.input}, out: {tokens.output})
              </div>
            )}
          </div>
        );
        
      case 'message_delta':
        const deltaText = part?.part?.text || '';
        const deltaType = part?.part?.type || '';
        if (!deltaText) return null;
        const deltaPreview = deltaText.length > 500 ? deltaText.slice(0, 500) + '...' : deltaText;
        return (
          <div className="py-1">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">[{time}]</span>
              <span className="text-cyan-400 font-medium">RESPONSE</span>
              <span className="text-slate-500 text-xs">({deltaType})</span>
            </div>
            <div className="text-emerald-300 mt-1 whitespace-pre-wrap ml-16 text-xs leading-relaxed">
              {deltaPreview}
            </div>
          </div>
        );
        
      case 'message_complete':
        const finalTokens = part?.tokens;
        const stopReason = part?.reason;
        return (
          <div className="py-1">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">[{time}]</span>
              <span className="text-green-400 font-medium">DONE</span>
              {stopReason && <span className="text-slate-400">({stopReason})</span>}
            </div>
            {finalTokens && finalTokens.total > 0 && (
              <div className="text-xs text-slate-400 mt-0.5 ml-16">
                {finalTokens.total} tokens
              </div>
            )}
          </div>
        );
        
      case 'error':
        const errMsg = part?.part?.text || 'Unknown error';
        return (
          <div className="py-1">
            <div className="flex items-center gap-2">
              <span className="text-slate-500">[{time}]</span>
              <span className="text-red-400 font-medium">ERROR</span>
            </div>
            <div className="text-red-300 mt-1 whitespace-pre-wrap ml-16 text-xs">
              {errMsg.slice(0, 300)}
            </div>
          </div>
        );
        
      case 'ping':
        return null;
        
      default:
        return null;
    }
  };

  if (!current) {
    return null;
  }

  return (
    <div className="mt-3 rounded-lg overflow-hidden" style={{ backgroundColor: '#0a0a0a' }}>
      <div className="flex items-center gap-2 px-3 py-1.5" style={{ backgroundColor: '#1e1e1e' }}>
        <div className="flex gap-1.5">
          <button
            className="w-2.5 h-2.5 rounded-full hover:bg-red-400 transition-colors"
            style={{ backgroundColor: '#ff5f56' }}
            title="Close"
          />
          <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: '#ffbd2e' }} title="Minimize" />
          <div className="w-2.5 h-2.5 rounded-full" style={{ backgroundColor: '#27c93f' }} title="Maximize" />
        </div>
        <div className="flex-1 text-center">
          <span className="text-[10px] font-mono text-slate-400">
            {current.provider} / {current.run_id.slice(0, 8)}...
          </span>
        </div>
        {connecting && <RotateCcw size={10} className="animate-spin text-blue-400" />}
        <div className="flex gap-1">
          {(['stdout', 'stderr'] as const).map(s => (
            <button
              key={s}
              onClick={() => setStream(s)}
              className={`text-[9px] px-2 py-0.5 rounded font-mono ${
                stream === s 
                  ? 'bg-blue-600 text-white' 
                  : 'text-slate-500 hover:text-slate-300'
              }`}
            >
              {s}
            </button>
          ))}
        </div>
      </div>
      <div
        ref={outputRef}
        className="h-56 overflow-x-auto overflow-y-auto p-3 font-mono text-[10px] leading-relaxed"
        style={{ backgroundColor: '#0a0a0a' }}
      >
        {connecting && output.length === 0 ? (
          <span style={{ color: '#6b7280' }}>Connecting to stream...</span>
        ) : output.length === 0 ? (
          <span style={{ color: '#6b7280' }}>(waiting for output...)</span>
        ) : (
          output.map((line, i) => (
            <div key={i}>
              {renderEvent(line)}
            </div>
          ))
        )}
      </div>
    </div>
  );
};

export const SessionCard = ({
  session,
  isSelected,
  copied,
  onCopy,
  onTerminate,
  onOpen,
}: SessionCardProps) => {
  const [isHovered, setIsHovered] = useState(false);
  const isActive = String(session.status || '').toLowerCase() === 'active';

  return (
    <div
      className={`group relative rounded-xl overflow-hidden transition-all cursor-pointer ${
        isSelected
          ? 'ring-2 ring-blue-500/50'
          : 'hover:ring-1 hover:ring-slate-600'
      }`}
      style={{ backgroundColor: '#0f0f0f' }}
      onClick={onOpen}
      onMouseEnter={() => setIsHovered(true)}
      onMouseLeave={() => setIsHovered(false)}
    >
      <div className="flex items-center gap-2 px-3 py-2" style={{ backgroundColor: '#1e1e1e' }}>
        <div className="flex gap-1.5">
          <button
            onClick={(e) => { e.stopPropagation(); onTerminate(); }}
            className="w-3 h-3 rounded-full hover:bg-red-400 transition-colors"
            style={{ backgroundColor: '#ff5f56' }}
            title="Terminate"
          />
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: '#ffbd2e' }} title="Minimize" />
          <div className="w-3 h-3 rounded-full" style={{ backgroundColor: '#27c93f' }} title="Maximize" />
        </div>
        <div className="flex-1 flex items-center justify-center">
          <div className="flex items-center gap-2 px-3 py-1 rounded-lg" style={{ backgroundColor: '#2a2a2a' }}>
            <Terminal size={10} className="text-slate-400" />
            <span className="text-[10px] font-mono text-slate-400 truncate max-w-[150px]">
              {session.client_session_id.slice(0, 16)}...
            </span>
          </div>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={(e) => { e.stopPropagation(); onCopy(); }}
            className={`p-1.5 rounded transition-all ${
              copied ? 'text-emerald-400' : 'text-slate-500 opacity-0 group-hover:opacity-100 hover:text-white'
            }`}
            title="Copy session id"
          >
            {copied ? <Check size={12} /> : <Copy size={12} />}
          </button>
        </div>
      </div>

      <div className="p-4">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2 min-w-0">
            <div className={`shrink-0 w-2 h-2 rounded-full ${statusDot(session.status)}`} />
            <span className="text-sm font-semibold text-white">{session.provider}</span>
            <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium uppercase ${
              isActive 
                ? 'bg-blue-500/20 text-blue-400' 
                : session.status?.toLowerCase() === 'dirty'
                  ? 'bg-amber-500/20 text-amber-400'
                  : session.status?.toLowerCase() === 'idle'
                    ? 'bg-emerald-500/20 text-emerald-400'
                    : 'bg-slate-800 text-slate-400'
            }`}>
              {session.status}
            </span>
          </div>
        </div>

        <div className="mt-3 space-y-1.5">
          <div className="flex items-center gap-2 text-xs text-slate-400">
            <span className="text-slate-300">{session.user_display_name || 'System / Unbound'}</span>
            <span className="text-slate-600">·</span>
            <span className="text-slate-500">{session.user_email || session.api_key_label || 'No key'}</span>
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <FolderCode size={10} />
            <span className="font-mono text-slate-500 truncate">{session.cwd_path}</span>
          </div>
        </div>
      </div>

      {isActive && (
        <div className="px-4 pb-4">
          <SessionLiveOutput sessionId={session.client_session_id} />
        </div>
      )}
    </div>
  );
};

export default SessionCard;