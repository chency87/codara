import React from 'react';

interface TerminalOutputProps {
  content: string;
  maxHeight?: string;
  stream?: 'stdout' | 'stderr' | 'prompt';
}

interface LogEvent {
  type: string;
  timestamp?: number;
  sessionID?: string;
  part?: {
    id?: string;
    type?: string;
    text?: string;
    tool?: string;
    callID?: string;
    messageID?: string;
    snapshot?: string;
    reason?: string;
    cost?: number;
    time?: { start?: number; end?: number };
    state?: {
      status?: string;
      input?: unknown;
      output?: unknown;
      metadata?: Record<string, unknown>;
    };
    tokens?: {
      total?: number;
      input?: number;
      output?: number;
      reasoning?: number;
      cache?: { write?: number; read?: number };
    };
  };
}

const streamAccent = (stream: TerminalOutputProps['stream']) => {
  if (stream === 'stderr') return { prefix: 'text-rose-400', border: 'border-rose-500/20', bg: 'bg-rose-950/10' };
  if (stream === 'prompt') return { prefix: 'text-cyan-300', border: 'border-slate-800', bg: 'bg-slate-950/30' };
  return { prefix: 'text-emerald-400', border: 'border-slate-800', bg: 'bg-slate-950/30' };
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

const truncate = (value: string, limit: number) => {
  if (value.length <= limit) return value;
  return value.slice(0, limit) + '…';
};

const safeStringify = (value: unknown) => {
  if (value === null || value === undefined) return '';
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const looksLikePlaintext = (value: unknown) => typeof value === 'string';

const toolInputSummary = (input: unknown) => {
  if (!input || typeof input !== 'object') return '';
  const maybe = input as Record<string, unknown>;
  const command = typeof maybe.command === 'string' ? maybe.command : '';
  const description = typeof maybe.description === 'string' ? maybe.description : '';
  if (command && description) return `${description} · ${command}`;
  if (command) return command;
  if (description) return description;
  return '';
};

const toolOutputText = (state?: LogEvent['part']['state']) => {
  if (!state) return '';
  const direct = state.output;
  if (looksLikePlaintext(direct)) return direct;
  const metaOut = state.metadata?.output;
  if (looksLikePlaintext(metaOut)) return metaOut;
  if (direct !== undefined) return safeStringify(direct);
  if (metaOut !== undefined) return safeStringify(metaOut);
  return '';
};

const formatTimestamp = (ts?: number) => {
  if (!ts) return '';
  const date = new Date(ts);
  return date.toLocaleTimeString('en-US', { hour12: false });
};

const TerminalOutput: React.FC<TerminalOutputProps> = ({ content, maxHeight = '250px', stream = 'stdout' }) => {
  const lines = content.split('\n').filter(l => l.trim());
  const accent = streamAccent(stream);

  const renderLine = (line: string, idx: number) => {
    const event = parseLogLine(line);
    
    if (!event) return null;
    
    if (event.type === 'raw' || !event.part) {
      return (
        <div key={idx} className="py-0.5 flex gap-2">
          <span className={`${accent.prefix} select-none`}>│</span>
          <span className="text-slate-200 whitespace-pre-wrap break-words">{String(event.part?.text ?? line).slice(0, 2000)}</span>
        </div>
      );
    }

    const { type, timestamp, part } = event;
    const time = formatTimestamp(timestamp);
    
    switch (type) {
      case 'text': {
        if (!part?.text) return null;
        const textPreview = part.text.length > 2000 ? part.text.slice(0, 2000) + '…' : part.text;
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-blue-400 text-[10px] font-medium">TEXT</span>
            </div>
            <div className="text-emerald-300 whitespace-pre-wrap ml-0 mt-0.5 text-xs leading-relaxed break-words">{textPreview}</div>
          </div>
        );
      }
        
      case 'tool_use': {
        const tool = part?.tool || 'unknown';
        const status = part?.state?.status || 'unknown';
        const inputSummary = toolInputSummary(part?.state?.input);
        const output = toolOutputText(part?.state);
        const outputPreview = output ? truncate(output.replace(/\s+$/g, ''), 400) : '';
        return (
          <div key={idx} className="py-1">
            <div className="flex items-center gap-2 flex-wrap">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-amber-400 text-[10px] font-medium">TOOL</span>
              <span className="text-white text-xs break-all">{tool}</span>
              <span className={`text-[10px] ${status === 'completed' ? 'text-green-400' : 'text-blue-400'}`}>[{status}]</span>
              {inputSummary ? <span className="text-slate-400 text-[11px] break-all">· {truncate(inputSummary, 160)}</span> : null}
            </div>
            {(part?.state?.input || output) ? (
              <details className="mt-1 rounded-xl border border-slate-800 bg-black/20 px-3 py-2">
                <summary className="cursor-pointer select-none text-[10px] font-bold uppercase tracking-widest text-slate-500">
                  details
                </summary>
                {part?.state?.input !== undefined ? (
                  <div className="mt-2">
                    <div className="text-[10px] font-bold uppercase tracking-widest text-slate-600">input</div>
                    <pre className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-200">
                      {safeStringify(part.state?.input)}
                    </pre>
                  </div>
                ) : null}
                {output ? (
                  <div className="mt-3">
                    <div className="text-[10px] font-bold uppercase tracking-widest text-slate-600">output</div>
                    <pre className="mt-1 whitespace-pre-wrap break-words text-[11px] leading-relaxed text-slate-200">
                      {output}
                    </pre>
                  </div>
                ) : outputPreview ? (
                  <div className="mt-2 text-[11px] text-slate-400 whitespace-pre-wrap break-words">{outputPreview}</div>
                ) : null}
              </details>
            ) : null}
          </div>
        );
      }
        
      case 'step_start': {
        const stepId = part?.id ? truncate(part.id, 10) : '';
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-purple-400 text-[10px] font-medium">STEP START</span>
              {stepId ? <span className="text-slate-500 text-[10px] font-mono">{stepId}</span> : null}
            </div>
          </div>
        );
      }
        
      case 'step_finish': {
        const tokens = part?.tokens;
        const reason = part?.reason || part?.state?.status || 'done';
        const total = tokens?.total ?? 0;
        const input = tokens?.input ?? 0;
        const output = tokens?.output ?? 0;
        const reasoning = tokens?.reasoning ?? 0;
        const cacheRead = tokens?.cache?.read ?? 0;
        const cacheWrite = tokens?.cache?.write ?? 0;
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-green-400 text-[10px] font-medium">STEP END</span>
              <span className="text-slate-400 text-xs">({reason})</span>
              {total ? <span className="text-slate-500 text-[10px] font-mono">· {total} tok</span> : null}
            </div>
            {total ? (
              <details className="mt-1 rounded-xl border border-slate-800 bg-black/20 px-3 py-2">
                <summary className="cursor-pointer select-none text-[10px] font-bold uppercase tracking-widest text-slate-500">
                  tokens
                </summary>
                <div className="mt-2 text-[11px] text-slate-300 font-mono whitespace-pre-wrap break-words">
                  {`total=${total} input=${input} output=${output} reasoning=${reasoning} cache.read=${cacheRead} cache.write=${cacheWrite}`}
                </div>
              </details>
            ) : null}
          </div>
        );
      }
        
      case 'response': {
        const detail = part?.text ? truncate(part.text, 400) : '';
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-cyan-400 text-[10px] font-medium">RESPONSE</span>
              {detail ? <span className="text-slate-400 text-[11px] break-words">· {detail}</span> : null}
            </div>
          </div>
        );
      }
        
      case 'done': {
        const reason = part?.reason || part?.state?.status || '';
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-green-400 text-[10px] font-medium">DONE</span>
              {reason ? <span className="text-slate-400 text-xs">({reason})</span> : null}
            </div>
          </div>
        );
      }
        
      case 'error': {
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-rose-400 text-[10px] font-medium">ERROR</span>
            </div>
            {part?.text && <div className="text-rose-300 text-xs mt-0.5 whitespace-pre-wrap break-words">{part.text}</div>}
          </div>
        );
      }
        
      default:
        return (
          <div key={idx} className="py-0.5 flex">
            <span className="text-slate-500 text-[10px] mr-2">[{type}]</span>
            <span className="text-slate-300 text-xs break-words">{line.slice(0, 500)}</span>
          </div>
        );
    }
  };

  if (!content || lines.length === 0) {
    return <div className="text-slate-500 text-xs italic">No output</div>;
  }

  return (
    <div
      className={`font-mono text-xs rounded-2xl border ${accent.border} ${accent.bg} p-4`}
      style={{ maxHeight, overflow: 'auto' }}
    >
      {lines.map((line, idx) => renderLine(line, idx))}
    </div>
  );
};

export default TerminalOutput;
