import React, { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { Layers, ShieldAlert, Key } from 'lucide-react';
import axios from 'axios';

const Login = () => {
  const [passkey, setPasskey] = useState('');
  const [error, setError] = useState('');
  const navigate = useNavigate();

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    setError('');
    try {
      // Use a clean axios call that doesn't use the global interceptors
      // to avoid sending stale tokens that might trigger 401s on the auth endpoint
      const resp = await axios({
        method: 'post',
        url: '/management/v1/auth/token',
        data: { operator_secret: passkey },
        transformRequest: [(data) => JSON.stringify(data)],
        headers: { 'Content-Type': 'application/json' }
      });
      
      // Extract tokens from the standard envelope .data field
      const authData = resp.data.data;
      sessionStorage.setItem('uag_token', authData.access_token);
      if (authData.refresh_token) {
        sessionStorage.setItem('uag_refresh_token', authData.refresh_token);
      }
      navigate('/');
    } catch {
      setError('Authentication failed. Use the API_TOKEN value from your .env file.');
    }
  };

  return (
    <div className="h-screen w-screen bg-black flex items-center justify-center font-sans selection:bg-blue-500/30">
      <div className="w-full max-w-md p-12 bg-slate-900/40 backdrop-blur-xl border border-slate-800/60 rounded-3xl shadow-2xl">
        <div className="flex flex-col items-center mb-12">
          <div className="p-4 bg-blue-600 rounded-2xl shadow-lg shadow-blue-600/20 mb-6">
            <Layers size={32} className="text-white" />
          </div>
          <h1 className="text-3xl font-black tracking-tight text-white mb-2">UAG OPERATOR</h1>
          <p className="text-xs font-bold text-slate-500 uppercase tracking-widest">Management Control Plane</p>
        </div>

        <form onSubmit={handleLogin} className="space-y-6">
          <div className="space-y-2">
            <label className="text-[10px] font-black text-slate-500 uppercase tracking-widest ml-1">Operator Passkey</label>
            <div className="relative">
              <Key size={16} className="absolute left-4 top-1/2 -translate-y-1/2 text-slate-500" />
              <input 
                type="password" 
                className="w-full bg-black border border-slate-800 rounded-xl pl-12 pr-4 py-4 text-sm focus:border-blue-500 outline-none transition-colors text-white font-medium"
                placeholder="Enter API_TOKEN from .env"
                value={passkey}
                onChange={(e) => setPasskey(e.target.value)}
              />
            </div>
            <p className="text-[11px] text-slate-500">
              The dashboard operator passkey is the <code className="rounded bg-slate-950 px-1.5 py-0.5 text-slate-300">API_TOKEN</code> value from your local <code className="rounded bg-slate-950 px-1.5 py-0.5 text-slate-300">.env</code>.
            </p>
          </div>

          {error && (
            <div className="flex items-center space-x-2 text-rose-500 bg-rose-500/10 p-4 rounded-xl border border-rose-500/20">
              <ShieldAlert size={16} />
              <span className="text-xs font-bold uppercase tracking-wide">{error}</span>
            </div>
          )}

          <button 
            type="submit"
            className="w-full bg-white text-black hover:bg-blue-500 hover:text-white py-4 rounded-xl font-black text-sm transition-all shadow-xl shadow-white/5 uppercase tracking-widest"
          >
            Authenticate
          </button>
        </form>

        <p className="mt-12 text-center text-[10px] font-bold text-slate-600 uppercase tracking-tighter">
          v0.1.0-alpha • Restricted Access System
        </p>
      </div>
    </div>
  );
};

export default Login;
