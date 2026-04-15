import type { ReactNode } from 'react';
import { BrowserRouter as Router, Routes, Route, Link, useLocation, Navigate, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import type { LucideIcon } from 'lucide-react';
import { 
  LayoutDashboard, 
  Layers, 
  Users, 
  Activity, 
  History,
  Terminal,
  ChevronRight,
  ShieldCheck,
  LogOut,
  BarChart3,
  Cpu,
  FolderCode
} from 'lucide-react';
import axios from 'axios';
import Overview from './pages/Overview';
import Sessions from './pages/Sessions';
import Accounts from './pages/Accounts';
import UsersPage from './pages/Users';
import Providers from './pages/Providers';
import Usage from './pages/Usage';
import AuditLog from './pages/AuditLog';
import Playground from './pages/Playground';
import Login from './pages/Login';
import Workspaces from './pages/Workspaces';
import type { HealthStatusPayload } from './types/api';

// Axios interceptor for auth
axios.interceptors.request.use(config => {
  const token = sessionStorage.getItem('uag_token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

axios.interceptors.response.use(
  response => response,
  error => {
    if (axios.isAxiosError(error) && error.response?.status === 401) {
      // Clear token and redirect to login if we get an unauthorized response
      sessionStorage.removeItem('uag_token');
      sessionStorage.removeItem('uag_refresh_token');
      // We can't use useNavigate here as we are outside a component
      window.location.href = '/dashboard/login';
    }
    return Promise.reject(error);
  }
);

const ProtectedRoute = ({ children }: { children: ReactNode }) => {
  const token = sessionStorage.getItem('uag_token');
  if (!token) return <Navigate to="/login" replace />;
  return <>{children}</>;
};

const SidebarItem = ({ to, icon: Icon, label }: { to: string; icon: LucideIcon; label: string }) => {
  const location = useLocation();
  const isActive = location.pathname === to;
  
  return (
    <Link 
      to={to} 
      className={`flex items-center justify-between px-4 py-3 rounded-xl transition-all duration-200 group ${
        isActive 
          ? 'bg-blue-600/10 text-blue-400 border border-blue-500/20 shadow-[0_0_15px_rgba(37,99,235,0.1)]' 
          : 'text-gray-400 hover:bg-white/5 hover:text-white border border-transparent'
      }`}
    >
      <div className="flex items-center space-x-3">
        <Icon size={18} className={isActive ? 'text-blue-400' : 'group-hover:text-blue-400'} />
        <span className="text-sm font-semibold tracking-wide">{label}</span>
      </div>
      {isActive && <ChevronRight size={14} className="text-blue-400" />}
    </Link>
  );
};

function AppLayout() {
  const navigate = useNavigate();
  const { data: health } = useQuery<HealthStatusPayload>({
    queryKey: ['sidebar-health'],
    queryFn: async () => (await axios.get('/management/v1/health')).data.data,
    refetchInterval: 30000,
  });

  const logout = () => {
    sessionStorage.removeItem('uag_token');
    sessionStorage.removeItem('uag_refresh_token');
    navigate('/login');
  };

  const healthStatus = String(health?.status || 'ok').toLowerCase();
  const badgeClass =
    healthStatus === 'ok'
      ? 'bg-emerald-500 shadow-[0_0_8px_rgba(16,185,129,0.5)]'
      : healthStatus === 'degraded'
        ? 'bg-amber-500 shadow-[0_0_8px_rgba(245,158,11,0.45)]'
        : 'bg-rose-500 shadow-[0_0_8px_rgba(244,63,94,0.45)]';
  const statusText = healthStatus.toUpperCase();

  return (
    <div className="flex h-screen bg-black text-slate-100 overflow-hidden font-sans selection:bg-blue-500/30">
      {/* Left Sidebar */}
      <div className="w-72 bg-slate-900/40 backdrop-blur-xl border-r border-slate-800/60 flex flex-col">
        <div className="p-8">
          <div className="flex items-center space-x-3 mb-2">
            <div className="p-2 bg-blue-600 rounded-lg shadow-lg shadow-blue-600/20">
              <Layers size={22} className="text-white" />
            </div>
            <span className="text-xl font-black tracking-tight bg-gradient-to-r from-white to-slate-400 bg-clip-text text-transparent">
              UAG OPERATOR
            </span>
          </div>
          <p className="text-[10px] text-slate-500 font-bold uppercase tracking-[0.2em] ml-1">Unified Agent Gateway</p>
        </div>
        
        <div className="px-6 mb-4">
          <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest px-4 mb-4">Management</div>
          <nav className="space-y-1">
            <SidebarItem to="/" icon={LayoutDashboard} label="Overview" />
            <SidebarItem to="/playground" icon={Terminal} label="Agent Playground" />
            <SidebarItem to="/sessions" icon={Activity} label="Active Sessions" />
            <SidebarItem to="/workspaces" icon={FolderCode} label="Workspaces" />
            <SidebarItem to="/accounts" icon={ShieldCheck} label="Account Pool" />
            <SidebarItem to="/users" icon={Users} label="Users" />
          </nav>
        </div>

        <div className="px-6 mb-4">
          <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest px-4 mb-4">Observability</div>
          <nav className="space-y-1">
            <SidebarItem to="/providers" icon={Cpu} label="Providers" />
            <SidebarItem to="/usage" icon={BarChart3} label="Usage Metrics" />
            <SidebarItem to="/audit" icon={History} label="Audit Logs" />
          </nav>
        </div>
        
        <div className="mt-auto p-6 space-y-4">
          <button 
            onClick={logout}
            className="w-full flex items-center space-x-3 px-4 py-3 rounded-xl text-slate-500 hover:text-white hover:bg-white/5 transition-all"
          >
            <LogOut size={18} />
            <span className="text-sm font-semibold tracking-wide">Deauthenticate</span>
          </button>

          <div className="p-4 bg-slate-800/30 rounded-2xl border border-slate-700/30">
            <div className="flex items-center justify-between mb-2">
              <span className="text-xs font-bold text-slate-400 uppercase tracking-wider">Gateway Status</span>
              <div className={`w-2 h-2 rounded-full ${badgeClass}`}></div>
            </div>
            <div className="flex items-center space-x-2">
              <ShieldCheck size={14} className={healthStatus === 'ok' ? 'text-emerald-500' : healthStatus === 'degraded' ? 'text-amber-500' : 'text-rose-500'} />
              <span className="text-xs font-bold text-slate-200">v0.1.0-alpha • {statusText}</span>
            </div>
          </div>
        </div>
      </div>

      {/* Main Content Area */}
      <main className="flex-1 overflow-auto relative bg-[radial-gradient(circle_at_top_right,_var(--tw-gradient-stops))] from-slate-900/20 via-black to-black">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/workspaces" element={<Workspaces />} />
          <Route path="/accounts" element={<Accounts />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/providers" element={<Providers />} />
          <Route path="/usage" element={<Usage />} />
          <Route path="/audit" element={<AuditLog />} />
        </Routes>
      </main>
    </div>
  );
}

function App() {
  return (
    <Router basename="/dashboard">
      <Routes>
        <Route path="/login" element={<Login />} />
        <Route path="/*" element={
          <ProtectedRoute>
            <AppLayout />
          </ProtectedRoute>
        } />
      </Routes>
    </Router>
  );
}

export default App;
