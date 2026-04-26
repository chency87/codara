import React from 'react';

interface TerminalOutputProps {
  content: string;
  maxHeight?: string;
}

interface LogEvent {
  type: string;
  timestamp?: number;
  sessionID?: string;
  part?: {
    text?: string;
    tool?: string;
    state?: { status?: string };
    tokens?: { total: number; input: number; output: number };
  };
}

const parseLogLine = (line: string): LogEvent | null => {
  try {
    const trimmed = line.trim();
    if (!trimmed) return null;
    return JSON.parse(trimmed) as LogEvent;
  } catch {
    return { type: 'raw', timestamp: Date.now(), part: { text: line } };
  }
};

const formatTimestamp = (ts?: number) => {
  if (!ts) return '';
  const date = new Date(ts);
  return date.toLocaleTimeString('en-US', { hour12: false });
};

const TerminalOutput: React.FC<TerminalOutputProps> = ({ content, maxHeight = '250px' }) => {
  const lines = content.split('\n').filter(l => l.trim());

  const renderLine = (line: string, idx: number) => {
    const event = parseLogLine(line);
    
    if (!event) return null;
    
    if (event.type === 'raw' || !event.part) {
      return (
        <div key={idx} className="py-0.5 flex">
          <span className="text-amber-400 select-none mr-2">$</span>
          <span className="text-slate-300">{line.slice(0, 500)}</span>
        </div>
      );
    }

    const { type, timestamp, part } = event;
    const time = formatTimestamp(timestamp);
    
    switch (type) {
      case 'text':
        if (!part?.text) return null;
        const textPreview = part.text.length > 500 ? part.text.slice(0, 500) + '...' : part.text;
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-blue-400 text-[10px] font-medium">TEXT</span>
            </div>
            <div className="text-emerald-300 whitespace-pre-wrap ml-0 mt-0.5 text-xs leading-relaxed">{textPreview}</div>
          </div>
        );
        
      case 'tool_use':
        const tool = part?.tool || 'unknown';
        const status = part?.state?.status || 'unknown';
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2 flex-wrap">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-amber-400 text-[10px] font-medium">TOOL</span>
              <span className="text-white text-xs">{tool}</span>
              <span className={`text-[10px] ${status === 'completed' ? 'text-green-400' : 'text-blue-400'}`}>[{status}]</span>
            </div>
          </div>
        );
        
      case 'step_start':
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-purple-400 text-[10px] font-medium">STEP START</span>
            </div>
          </div>
        );
        
      case 'step_finish':
        const tokens = part?.tokens;
        const reason = part?.state?.status || 'done';
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-green-400 text-[10px] font-medium">STEP END</span>
              <span className="text-slate-400 text-xs">({reason})</span>
            </div>
            {tokens && tokens.total > 0 && (
              <div className="text-[10px] text-slate-500 ml-0 mt-0.5">
                {tokens.total} tokens (in: {tokens.input}, out: {tokens.output})
              </div>
            )}
          </div>
        );
        
      case 'response':
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-cyan-400 text-[10px] font-medium">RESPONSE</span>
            </div>
          </div>
        );
        
      case 'done':
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-green-400 text-[10px] font-medium">DONE</span>
            </div>
          </div>
        );
        
      case 'error':
        return (
          <div key={idx} className="py-0.5">
            <div className="flex items-center gap-2">
              {time && <span className="text-slate-600 text-[10px]">[{time}]</span>}
              <span className="text-rose-400 text-[10px] font-medium">ERROR</span>
            </div>
            {part?.text && <div className="text-rose-300 text-xs mt-0.5">{part.text}</div>}
          </div>
        );
        
      default:
        return (
          <div key={idx} className="py-0.5 flex">
            <span className="text-slate-500 text-[10px] mr-2">[{type}]</span>
            <span className="text-slate-300 text-xs">{line.slice(0, 200)}</span>
          </div>
        );
    }
  };

  if (!content || lines.length === 0) {
    return <div className="text-slate-500 text-xs italic">No output</div>;
  }

  return (
    <div className="font-mono text-xs" style={{ maxHeight, overflow: 'auto' }} >
      {lines.map((line, idx) => renderLine(line, idx))}
    </div>
  );
};

export default TerminalOutput;