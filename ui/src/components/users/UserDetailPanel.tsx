import { useState } from 'react';
import { Copy, RefreshCw, X } from 'lucide-react';
import type {
  ChannelLinkTokenResponse,
  UserActivityRecord,
  UserDetailPayload,
  UserSessionRecord,
  WorkspaceResetRecord,
  ApiKeyRecord,
} from '../../types/api';

interface UserDetailPanelProps {
  user: UserDetailPayload;
  onClose: () => void;
  onRotateKey: () => void;
  rotateKeyPending: boolean;
  onResetWorkspace: () => void;
  resetWorkspacePending: boolean;
  workspaceFeedback: string | null;
  channelBotName: string;
  channelExpiry: number;
  channelFeedback: string | null;
  channelToken: ChannelLinkTokenResponse | null;
  copiedChannelToken: boolean;
  onCreateChannelToken: (userId: string, botName: string, expiresInMinutes: number) => void;
  createChannelTokenPending: boolean;
  revealedKey: string | null;
  revealedKeyUserId: string | null;
  copiedKeyTarget: string | null;
  onCopyKey: (keyId: string) => void;
  onCopyChannelToken: () => void;
}

const formatDate = (value?: string | null) => (value ? new Date(value).toLocaleString() : '—');

const copyToClipboard = async (value: string) => {
  if (navigator.clipboard?.writeText) {
    await navigator.clipboard.writeText(value);
    return;
  }
  const input = document.createElement('textarea');
  input.value = value;
  document.body.appendChild(input);
  input.select();
  document.execCommand('copy');
  document.body.removeChild(input);
};

export const UserDetailPanel = ({
  user,
  onClose,
  onRotateKey,
  rotateKeyPending,
  onResetWorkspace,
  resetWorkspacePending,
  workspaceFeedback,
  channelBotName,
  channelExpiry,
  channelFeedback,
  channelToken,
  copiedChannelToken,
  onCreateChannelToken,
  createChannelTokenPending,
  revealedKey,
  revealedKeyUserId,
  copiedKeyTarget,
  onCopyKey,
  onCopyChannelToken,
}: UserDetailPanelProps) => {
  const [copiedUserId, setCopiedUserId] = useState(false);

  const copyUserId = async () => {
    await copyToClipboard(user.user_id);
    setCopiedUserId(true);
    setTimeout(() => setCopiedUserId(false), 2000);
  };

  return (
    <div className="absolute inset-y-0 right-0 w-full max-w-2xl bg-slate-950 border-l border-slate-800 shadow-2xl z-50 overflow-auto">
      <div className="p-8 border-b border-slate-800 flex justify-between items-start">
        <div>
          <div className="text-[10px] font-black uppercase tracking-widest text-slate-500 mb-2">User Detail</div>
          <h3 className="text-2xl font-black text-white">{user.display_name}</h3>
          <div className="text-xs font-mono text-slate-500 mt-2">{user.email}</div>
          <button
            onClick={() => void copyUserId()}
            className="mt-3 inline-flex items-center gap-2 rounded-xl border border-slate-800 bg-black/30 px-3 py-2 text-[10px] font-mono text-slate-400 hover:border-slate-700 hover:text-white"
          >
            <span>{user.user_id}</span>
            <Copy size={12} />
            <span className="text-[9px] font-black uppercase tracking-widest text-emerald-400">
              {copiedUserId ? 'Copied' : 'Copy ID'}
            </span>
          </button>
        </div>
        <button onClick={onClose} className="text-slate-500 hover:text-white"><X size={18} /></button>
      </div>
      <div className="p-8 space-y-8">
        <div className="grid grid-cols-2 gap-4">
          <div className="p-4 rounded-2xl bg-slate-900/50 border border-slate-800">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">Base Workspace</div>
            <div className="text-sm font-mono text-white break-all mt-2">{user.workspace_path}</div>
            <div className="mt-2 text-xs text-slate-500">
              Clients can target sub-workspaces with
              <code className="mx-1 rounded bg-slate-950 px-1.5 py-0.5 text-[11px] text-slate-300">uag_options.workspace_id</code>
              and keep cache-friendly session reuse within that base path.
            </div>
          </div>
          <div className="p-4 rounded-2xl bg-slate-900/50 border border-slate-800">
            <div className="text-[10px] font-black uppercase tracking-widest text-slate-500">API Keys</div>
            <div className="text-2xl font-black text-white mt-2">{user.active_keys}</div>
            <div className="mt-2 text-xs text-slate-500">Single active key policy · max concurrency {user.max_concurrency}</div>
          </div>
        </div>

        <section>
          <div className="mb-4 flex items-center justify-between gap-4">
            <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">Channel Access</h4>
            <button
              onClick={onResetWorkspace}
              className="inline-flex items-center gap-2 rounded-xl bg-amber-500/10 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-amber-300 disabled:opacity-50"
              disabled={resetWorkspacePending}
            >
              <RefreshCw size={12} className={resetWorkspacePending ? 'animate-spin' : ''} />
              Reset workspace sessions
            </button>
          </div>
          {workspaceFeedback ? (
            <div className={`mb-4 rounded-2xl border px-4 py-3 text-sm ${
              workspaceFeedback === 'Workspace sessions reset'
                ? 'border-emerald-500/20 bg-emerald-500/10 text-emerald-200'
                : 'border-rose-500/20 bg-rose-500/10 text-rose-200'
            }`}>
              {workspaceFeedback}
            </div>
          ) : null}
          <div className="rounded-2xl border border-slate-800 bg-black/30 p-4">
            <div className="grid grid-cols-1 gap-4 md:grid-cols-3">
              <label className="space-y-2">
                <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Channel</span>
                <input
                  readOnly
                  value="telegram"
                  className="w-full rounded-xl border border-slate-800 bg-slate-950 px-4 py-3 text-sm text-slate-300"
                />
              </label>
              <label className="space-y-2">
                <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Bot Name</span>
                <input
                  value={channelBotName}
                  className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white"
                />
              </label>
              <label className="space-y-2">
                <span className="text-[10px] font-black uppercase tracking-widest text-slate-500">Expires In Minutes</span>
                <input
                  type="number"
                  min={1}
                  max={1440}
                  value={channelExpiry}
                  className="w-full rounded-xl border border-slate-800 bg-black px-4 py-3 text-sm text-white"
                />
              </label>
            </div>
            <div className="mt-4 flex items-center justify-between gap-4">
              <div className="text-xs text-slate-500">
                Create a one-time Telegram link token for this user, then send <code className="mx-1 rounded bg-slate-950 px-1.5 py-0.5 text-slate-300">/link &lt;token&gt;</code> in the bot chat.
              </div>
              <button
                onClick={() => onCreateChannelToken(user.user_id, channelBotName.trim(), channelExpiry)}
                disabled={!channelBotName.trim() || createChannelTokenPending}
                className="rounded-xl bg-blue-600 px-4 py-3 text-[10px] font-black uppercase tracking-widest text-white disabled:opacity-50"
              >
                {createChannelTokenPending ? 'Creating…' : 'Create Link Token'}
              </button>
            </div>
            {channelFeedback ? (
              <div className="mt-4 rounded-2xl border border-rose-500/20 bg-rose-500/10 px-4 py-3 text-sm text-rose-200">
                {channelFeedback}
              </div>
            ) : null}
            {channelToken && channelToken.user_id === user.user_id && (
              <div className="mt-4 rounded-2xl border border-emerald-500/20 bg-emerald-500/10 p-4">
                <div className="text-[10px] font-black uppercase tracking-widest text-emerald-300">One-time Link Token</div>
                <button
                  type="button"
                  onClick={onCopyChannelToken}
                  className="mt-3 flex w-full items-center gap-3 rounded-2xl border border-emerald-400/20 bg-black/30 p-4 text-left transition-colors hover:border-emerald-300/40"
                >
                  <input
                    readOnly
                    value={channelToken.raw_token}
                    className="w-full cursor-pointer bg-transparent font-mono text-sm text-white outline-none"
                  />
                  <Copy size={14} className="shrink-0 text-emerald-200" />
                </button>
                <div className="mt-3 flex items-center justify-between gap-4 text-xs text-slate-400">
                  <span>Expires {formatDate(channelToken.expires_at)}</span>
                  <span className="font-black uppercase tracking-widest text-emerald-300">
                    {copiedChannelToken ? 'Copied' : 'Copy now'}
                  </span>
                </div>
              </div>
            )}
          </div>
        </section>

        <section>
          <div className="mb-4 flex items-center justify-between gap-4">
            <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500">API Key</h4>
            <button
              onClick={onRotateKey}
              className="inline-flex items-center gap-2 rounded-xl bg-blue-600 px-3 py-2 text-[10px] font-black uppercase tracking-widest text-white disabled:opacity-50"
              disabled={rotateKeyPending}
            >
              <RefreshCw size={12} className={rotateKeyPending ? 'animate-spin' : ''} />
              Rotate key
            </button>
          </div>
          <div className="space-y-3">
            {user.api_keys.length === 0 ? (
              <div className="text-xs text-slate-600 font-bold uppercase tracking-widest">No active key</div>
            ) : (
              user.api_keys.map((key: ApiKeyRecord) => {
                const rawKeyVisible = revealedKeyUserId === user.user_id && !!revealedKey;
                const displayValue = rawKeyVisible ? revealedKey : `${key.key_prefix}••••`;
                return (
                  <button
                    key={key.key_id}
                    type="button"
                    onClick={() => onCopyKey(key.key_id)}
                    disabled={!rawKeyVisible}
                    className={`w-full p-4 rounded-2xl bg-black/40 border border-slate-800 flex items-center justify-between text-left transition-colors ${
                      rawKeyVisible ? 'cursor-copy hover:border-emerald-400/30' : 'cursor-not-allowed opacity-80'
                    }`}
                    title={rawKeyVisible ? 'Click to copy the current raw key' : 'Rotate the key to reveal a new raw API key'}
                  >
                    <div>
                      <div className="text-sm font-bold text-white">{key.label || 'Unnamed key'}</div>
                      <div className="mt-1 flex items-center gap-2 text-[10px] font-mono text-slate-500">
                        <span className="rounded bg-slate-950/70 px-2 py-1 text-slate-300">{displayValue}</span>
                        <Copy size={11} className="text-slate-400" />
                        <span className="text-[9px] font-black uppercase tracking-widest text-emerald-400">
                          {copiedKeyTarget === key.key_id ? 'Copied' : rawKeyVisible ? 'Copy raw key' : 'Masked preview only'}
                        </span>
                      </div>
                      <div className="mt-2 text-[10px] text-slate-600">
                        {rawKeyVisible ? 'Click to copy the current raw key.' : 'Raw API keys are only available immediately after provisioning or rotation. Rotate this key to reveal a fresh one-time value.'}
                      </div>
                    </div>
                    <div className={`text-[10px] font-black uppercase tracking-widest ${key.status === 'active' ? 'text-emerald-400' : 'text-rose-400'}`}>
                      {copiedKeyTarget === key.key_id ? 'copied' : key.status}
                    </div>
                  </button>
                );
              })
            )}
          </div>
        </section>

        <section>
          <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 mb-4">Sessions</h4>
          <div className="space-y-3">
            {user.sessions.length === 0 ? (
              <div className="text-xs text-slate-600 font-bold uppercase tracking-widest">No sessions recorded</div>
            ) : (
              user.sessions.map((session: UserSessionRecord) => (
                <div key={session.client_session_id} className="p-4 rounded-2xl bg-black/40 border border-slate-800">
                  <div className="flex items-center justify-between">
                    <div className="font-mono text-xs text-slate-300">{session.client_session_id}</div>
                    <span className="text-[10px] font-black uppercase tracking-widest text-slate-400">{session.status}</span>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                      {session.provider}
                    </span>
                    <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                      {session.api_key_label || 'No key binding'}
                    </span>
                  </div>
                  <div className="text-[10px] text-slate-500 mt-2">{session.cwd_path}</div>
                </div>
              ))
            )}
          </div>
        </section>

        <section>
          <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 mb-4">Recent Activity</h4>
          <div className="space-y-3">
            {user.recent_activity.length === 0 ? (
              <div className="text-xs text-slate-600 font-bold uppercase tracking-widest">No turns recorded yet</div>
            ) : (
              user.recent_activity.map((activity: UserActivityRecord) => (
                <div key={activity.turn_id} className="p-4 rounded-2xl bg-black/40 border border-slate-800">
                  <div className="flex items-center justify-between gap-4">
                    <div>
                      <div className="text-sm font-bold text-white">{activity.provider} turn</div>
                      <div className="text-[10px] font-mono text-slate-500 mt-1">{activity.client_session_id}</div>
                    </div>
                  </div>
                  <div className="mt-2 flex flex-wrap gap-2">
                    <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                      {activity.api_key_label || 'No key binding'}
                    </span>
                    <span className="rounded-full border border-slate-700 bg-slate-900 px-2.5 py-1 text-[10px] font-black uppercase tracking-widest text-slate-300">
                      {activity.finish_reason || 'unknown'}
                    </span>
                  </div>
                  <div className="mt-2 text-[10px] text-slate-500">
                    {formatDate(activity.timestamp)} · {activity.cwd_path || 'No workspace path'}
                  </div>
                </div>
              ))
            )}
          </div>
        </section>

        <section>
          <h4 className="text-[10px] font-black uppercase tracking-[0.2em] text-slate-500 mb-4">Resets</h4>
          <div className="space-y-3">
            {user.resets.length === 0 ? (
              <div className="text-xs text-slate-600 font-bold uppercase tracking-widest">No reset history</div>
            ) : (
              user.resets.map((reset: WorkspaceResetRecord) => (
                <div key={reset.reset_id} className="p-4 rounded-2xl bg-black/40 border border-slate-800 text-xs text-slate-400">
                  {reset.triggered_by} wiped {reset.sessions_wiped} sessions on {formatDate(reset.reset_at)}
                </div>
              ))
            )}
          </div>
        </section>
      </div>
    </div>
  );
};

export default UserDetailPanel;
