import React, { useEffect, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Send, Terminal, Cpu, Folder, RefreshCcw, Code, AlertTriangle, FileJson } from 'lucide-react';
import axios from 'axios';
import type { ChatResponsePayload, ProviderModelsRecord } from '../types/api';

const PROVIDERS = [
  { value: 'codex', label: 'Codex' },
  { value: 'gemini', label: 'Gemini' },
  { value: 'opencode', label: 'OpenCode' },
];

const formatPayload = (value: unknown) => {
  if (value == null) return null;
  if (typeof value === 'string') return value;
  try {
    return JSON.stringify(value, null, 2);
  } catch {
    return String(value);
  }
};

const extractAssistantOutput = (response: ChatResponsePayload | null) => {
  const choice = response?.choices?.[0];
  if (typeof choice?.message?.content === 'string' && choice.message.content.trim()) {
    return choice.message.content;
  }
  if (Array.isArray(choice?.message?.content)) {
    return choice.message.content
      .map((part) => (typeof part === 'string' ? part : part?.text || part?.content || ''))
      .filter(Boolean)
      .join('\n\n');
  }
  return null;
};

const Playground = () => {
  const [provider, setProvider] = useState('codex');
  const [model, setModel] = useState('uag-codex');
  const [message, setMessage] = useState('');
  const [workspaceId, setWorkspaceId] = useState('default');
  const [sessionLabel, setSessionLabel] = useState('');
  const [loading, setLoading] = useState(false);
  const [response, setResponse] = useState<ChatResponsePayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [errorPayload, setErrorPayload] = useState<string | null>(null);

  const { data: providerModels } = useQuery<ProviderModelsRecord[]>({
    queryKey: ['provider-models'],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/providers/models');
      return resp.data.data || [];
    },
    refetchInterval: 30000,
  });

  const activeProviderModels = useMemo(
    () => providerModels?.find((item) => item.provider === provider) || null,
    [provider, providerModels],
  );
  const availableModels = useMemo(
    () => (activeProviderModels?.models?.length ? activeProviderModels.models : [`uag-${provider}`]),
    [activeProviderModels, provider],
  );

  useEffect(() => {
    if (!model || !availableModels.includes(model)) {
      setModel(activeProviderModels?.default_model || `uag-${provider}`);
    }
  }, [activeProviderModels, availableModels, model, provider]);

  const requestBody = useMemo(
    () => ({
      model,
      messages: [{ role: 'user', content: message }],
      uag_options: {
        provider,
        workspace_id: workspaceId.trim() || undefined,
        client_session_id: sessionLabel.trim() || undefined,
        manual_mode: false,
      },
    }),
    [message, model, provider, sessionLabel, workspaceId],
  );

  const sendRequest = async () => {
    setLoading(true);
    setError(null);
    setErrorPayload(null);
    try {
      const resp = await axios.post('/management/v1/playground/chat', requestBody);
      setResponse(resp.data);
    } catch (err: unknown) {
      if (axios.isAxiosError(err)) {
        setError(err.response?.data?.detail || err.message);
        setErrorPayload(formatPayload(err.response?.data) || err.message);
        setResponse(err.response?.data || null);
      } else if (err instanceof Error) {
        setError(err.message);
        setErrorPayload(err.message);
        setResponse(null);
      } else {
        setError('Request failed');
        setErrorPayload('Request failed');
        setResponse(null);
      }
    } finally {
      setLoading(false);
    }
  };

  const assistantOutput = extractAssistantOutput(response);
  const rawResponse = formatPayload(response);

  return (
    <div className="p-12 animate-in fade-in slide-in-from-bottom-4 duration-700">
      <header className="mb-12">
        <h2 className="text-4xl font-black tracking-tight text-white mb-2">Agent Playground</h2>
        <p className="text-slate-500 font-medium">Test system prompts, workspace isolation, and provider adapters.</p>
      </header>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-12">
        <div className="space-y-8">
          <div className="bg-slate-900/40 border border-slate-800 rounded-3xl p-8 space-y-6 shadow-2xl">
            <div className="flex items-center space-x-4">
              <div className="flex-1 space-y-2">
                <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Target Provider</label>
                <div className="relative">
                  <Cpu size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
                  <select
                    className="w-full bg-black border border-slate-800 rounded-xl pl-10 pr-4 py-3 text-sm focus:border-blue-500 outline-none text-white font-medium appearance-none"
                    value={provider}
                    onChange={(e) => setProvider(e.target.value)}
                  >
                    {PROVIDERS.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </div>
              </div>
              <div className="flex-1 space-y-2">
                <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Workspace ID</label>
                <div className="relative">
                  <Folder size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
                  <input
                    type="text"
                    placeholder="default or project-a/feature-x"
                    className="w-full bg-black border border-slate-800 rounded-xl pl-10 pr-4 py-3 text-sm focus:border-blue-500 outline-none text-white font-medium"
                    value={workspaceId}
                    onChange={(e) => setWorkspaceId(e.target.value)}
                  />
                </div>
              </div>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Target Model</label>
              <div className="relative">
                <Cpu size={14} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
                <select
                  className="w-full bg-black border border-slate-800 rounded-xl pl-10 pr-4 py-3 text-sm focus:border-blue-500 outline-none text-white font-medium appearance-none"
                  value={model}
                  onChange={(e) => setModel(e.target.value)}
                >
                  {availableModels.map((item: string) => (
                    <option key={item} value={item}>
                      {item}
                    </option>
                  ))}
                </select>
              </div>
              <p className="text-xs text-slate-500">
                {activeProviderModels?.detail || 'Using the provider default model alias until runtime inventory loads.'}
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Session Label</label>
              <input
                type="text"
                placeholder="optional stable session id"
                className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-sm focus:border-blue-500 outline-none text-white font-medium"
                value={sessionLabel}
                onChange={(e) => setSessionLabel(e.target.value)}
              />
              <p className="text-xs text-slate-500">
                Playground turns now run inside the dashboard admin user's provisioned workspace. Reuse the same label with the same workspace ID to exercise session resumption without pointing the dashboard at a broad absolute path.
              </p>
              <p className="text-xs text-slate-500">
                Normal user API keys use the same safer
                <code className="mx-1 rounded bg-slate-900 px-1.5 py-0.5 text-[11px] text-slate-300">uag_options.workspace_id</code>
                pattern under their provisioned workspace instead of absolute paths.
              </p>
            </div>

            <div className="space-y-2">
              <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">User Message</label>
              <textarea
                className="w-full h-40 bg-black border border-slate-800 rounded-2xl p-6 text-sm focus:border-blue-500 outline-none text-white font-medium resize-none"
                placeholder="Ask the agent to perform a task..."
                value={message}
                onChange={(e) => setMessage(e.target.value)}
              />
            </div>

            <button
              onClick={sendRequest}
              disabled={loading || !message.trim()}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:bg-slate-800 text-white py-4 rounded-2xl font-black text-sm transition-all shadow-xl shadow-blue-900/20 uppercase tracking-widest flex items-center justify-center space-x-3"
            >
              {loading ? <RefreshCcw size={18} className="animate-spin" /> : <Send size={18} />}
              <span>Execute Turn</span>
            </button>
          </div>

          {error && (
            <div className="rounded-2xl border border-rose-500/20 bg-rose-500/10 p-6">
              <div className="mb-2 flex items-center gap-2 text-[10px] font-black uppercase tracking-widest text-rose-300">
                <AlertTriangle size={12} />
                Request failed
              </div>
              <div className="text-sm font-medium text-rose-100">{error}</div>
              {errorPayload && (
                <pre className="mt-4 overflow-auto rounded-2xl border border-rose-500/10 bg-black p-4 text-[11px] text-rose-200">
                  {errorPayload}
                </pre>
              )}
            </div>
          )}
        </div>

        <div className="space-y-8">
          <div className="bg-slate-900/40 border border-slate-800 rounded-3xl p-8 h-full flex flex-col shadow-2xl">
            <div className="flex items-center space-x-3 mb-8">
              <Terminal className="text-emerald-500" size={20} />
              <h3 className="text-lg font-black text-white uppercase tracking-tight">Agent Response</h3>
            </div>

            <div className="flex-1 space-y-6">
              {!response && !error ? (
                <div className="h-full flex items-center justify-center text-slate-600 font-bold uppercase text-[10px] tracking-[0.2em] italic border border-dashed border-slate-800 rounded-2xl">
                  Awaiting Execution...
                </div>
              ) : (
                <>
                  <div className="space-y-2">
                    <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Assistant Output</label>
                    <div className="bg-black border border-slate-800 rounded-2xl p-6 text-sm text-slate-300 font-medium whitespace-pre-wrap min-h-32">
                      {assistantOutput || 'No assistant text was returned for this turn.'}
                    </div>
                  </div>

                  {response?.extensions?.diff && (
                    <div className="space-y-2">
                      <label className="text-[10px] font-black text-blue-500 uppercase tracking-widest ml-1 flex items-center space-x-2">
                        <Code size={12} />
                        <span>Workspace Patch</span>
                      </label>
                      <pre className="bg-black border border-blue-500/20 rounded-2xl p-6 text-[10px] font-mono text-blue-400 overflow-auto max-h-60">
                        {response.extensions.diff}
                      </pre>
                    </div>
                  )}

                  {rawResponse && (
                    <div className="space-y-2">
                      <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1 flex items-center gap-2">
                        <FileJson size={12} />
                        Raw payload
                      </label>
                      <pre className="bg-black border border-slate-800 rounded-2xl p-6 text-[10px] font-mono text-slate-400 overflow-auto max-h-72">
                        {rawResponse}
                      </pre>
                    </div>
                  )}

                  <div className="grid grid-cols-2 gap-4 mt-auto">
                    <div className="p-4 bg-slate-800/30 rounded-xl border border-slate-700/20 text-center">
                      <div className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1">Finish Reason</div>
                      <div className="text-xs font-black text-white">{response?.choices?.[0]?.finish_reason || 'error'}</div>
                    </div>
                    <div className="p-4 bg-slate-800/30 rounded-xl border border-slate-700/20 text-center">
                      <div className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1">Resolved Model</div>
                      <div className="text-xs font-black text-white font-mono">{model}</div>
                    </div>
                    <div className="p-4 bg-slate-800/30 rounded-xl border border-slate-700/20 text-center">
                      <div className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1">Session ID</div>
                      <div className="text-xs font-black text-white font-mono">
                        {response?.extensions?.client_session_id ? `${response.extensions.client_session_id.split('-')[0]}...` : 'n/a'}
                      </div>
                    </div>
                    <div className="p-4 bg-slate-800/30 rounded-xl border border-slate-700/20 text-center">
                      <div className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1">Bound User</div>
                      <div className="text-xs font-black text-white">{response?.extensions?.bound_user_display_name || 'dashboard admin'}</div>
                    </div>
                    <div className="p-4 bg-slate-800/30 rounded-xl border border-slate-700/20 text-center">
                      <div className="text-[10px] font-black text-slate-500 uppercase tracking-widest mb-1">Reported Context Tokens</div>
                      <div className="text-xs font-black text-white">{response?.extensions?.reported_context_tokens ?? 'n/a'}</div>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
};

export default Playground;
