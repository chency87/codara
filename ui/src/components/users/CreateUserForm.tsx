import { useState } from 'react';
import { X, Plus } from 'lucide-react';
import type { CreateUserResponse } from '../../types/api';

interface CreateUserFormProps {
  onSubmit: (user: { email: string; display_name: string; max_concurrency: number }) => void;
  onClose: () => void;
  isPending: boolean;
}

export const CreateUserForm = ({ onSubmit, onClose, isPending }: CreateUserFormProps) => {
  const [newUser, setNewUser] = useState({ email: '', display_name: '', max_concurrency: 3 });

  return (
    <div className="mb-8 bg-slate-900/50 border border-slate-800 rounded-3xl p-8">
      <div className="flex justify-between items-center mb-6">
        <h3 className="text-xl font-black text-white">Provision New User</h3>
        <button onClick={onClose} className="text-slate-500 hover:text-white"><X size={18} /></button>
      </div>
      <p className="mb-6 text-xs text-slate-500">
        Codara always keeps one active API key per user and reveals the raw key once after provisioning or rotation.
      </p>
      <div className="grid grid-cols-1 gap-4 md:grid-cols-4">
        <div className="space-y-2">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500">Email</label>
          <input className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-white text-sm" placeholder="alice@example.com" value={newUser.email} onChange={(e) => setNewUser({ ...newUser, email: e.target.value })} />
        </div>
        <div className="space-y-2">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500">Display Name</label>
          <input className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-white text-sm" placeholder="Alice" value={newUser.display_name} onChange={(e) => setNewUser({ ...newUser, display_name: e.target.value })} />
        </div>
        <div className="space-y-2">
          <label className="text-[10px] font-black uppercase tracking-widest text-slate-500">Max Concurrency</label>
          <input
            type="number"
            min={1}
            max={20}
            className="w-full bg-black border border-slate-800 rounded-xl px-4 py-3 text-white text-sm"
            placeholder="3"
            value={newUser.max_concurrency}
            onChange={(e) => setNewUser({ ...newUser, max_concurrency: Number(e.target.value) || 1 })}
          />
        </div>
        <button
          className="mt-7 bg-blue-600 hover:bg-blue-500 text-white rounded-xl py-3 font-black text-sm uppercase tracking-widest disabled:opacity-40"
          disabled={!newUser.email || !newUser.display_name}
          onClick={() => onSubmit(newUser)}
        >
          Create
        </button>
      </div>
    </div>
  );
};

export default CreateUserForm;