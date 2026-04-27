import type { ReactNode } from 'react';
import { useState } from 'react';
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
  ChevronLeft,
  ShieldCheck,
  LogOut,
  Cpu,
  FolderCode,
  Menu,
  X
} from 'lucide-react';
import axios from 'axios';
import Overview from './pages/Overview';
import Sessions from './pages/Sessions';
import Workspaces from './pages/Workspaces';
import UsersPage from './pages/Users';
import Providers from './pages/Providers';
import Observability from './pages/Observability';
import SessionHistory from './pages/SessionHistory';
import Playground from './pages/Playground';
import Login from './pages/Login';
import AuditLog from './pages/AuditLog';
import type { HealthStatusPayload } from './types/api';
import { dashboardPollHeaders } from './api/dashboardPoll';

// Axios interceptor for auth
axios.defaults.withCredentials = true;
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
  const { isLoading, isError } = useQuery({
    queryKey: ['operator-me'],
    queryFn: async () => {
      const resp = await axios.get('/management/v1/auth/me');
      return resp.data.data;
    },
    retry: false,
    staleTime: 30_000,
  });

  if (isLoading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center">
        <span className="text-slate-500">Checking auth...</span>
      </div>
    );
  }
  if (isError) {
    return <Navigate to="/login" replace />;
  }
  return <>{children}</>;
};

const SidebarItem = ({
  to,
  icon: Icon,
  label,
  collapsed,
  onClick,
}: {
  to: string;
  icon: LucideIcon;
  label: string;
  collapsed: boolean;
  onClick?: () => void;
}) => {
  const location = useLocation();
  const isActive = location.pathname === to;
  
  return (
    <Link 
      to={to} 
      title={collapsed ? label : undefined}
      onClick={onClick}
      className={`flex items-center justify-between px-4 py-3 rounded-xl transition-all duration-200 group ${
        isActive 
          ? 'bg-blue-600/10 text-blue-400 border border-blue-500/20 shadow-[0_0_15px_rgba(37,99,235,0.1)]' 
          : 'text-gray-400 hover:bg-white/5 hover:text-white border border-transparent'
      }`}
    >
      <div className={`flex items-center ${collapsed ? 'justify-center w-full' : 'space-x-3'}`}>
        <Icon size={18} className={isActive ? 'text-blue-400' : 'group-hover:text-blue-400'} />
        {!collapsed && <span className="text-sm font-semibold tracking-wide">{label}</span>}
      </div>
      {!collapsed && isActive && <ChevronRight size={14} className="text-blue-400" />}
    </Link>
  );
};

function AppLayout() {
  const navigate = useNavigate();
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const { data: health } = useQuery<HealthStatusPayload>({
    queryKey: ['sidebar-health'],
    queryFn: async () => (await axios.get('/management/v1/health', { headers: dashboardPollHeaders })).data.data,
    refetchInterval: 30000,
  });

  const logout = () => {
    axios.post('/management/v1/auth/logout').catch(() => {});
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

  const sidebarContent = (
    <>
      <div className={sidebarCollapsed ? 'p-6' : 'p-8'}>
        <div className="flex items-center justify-between lg:block">
          <div className={`flex items-center ${sidebarCollapsed ? 'justify-center' : 'space-x-3'} mb-2`}>
            <div className="p-2 bg-blue-600 rounded-lg shadow-lg shadow-blue-600/20">
              <Layers size={22} className="text-white" />
            </div>
            {!sidebarCollapsed && (
              <span className="text-xl font-black tracking-tight bg-gradient-to-r from-white to-slate-400 bg-clip-text text-transparent">
                UAG OPERATOR
              </span>
            )}
          </div>
          <button 
            onClick={() => setMobileMenuOpen(false)}
            className="lg:hidden p-2 text-slate-400 hover:text-white"
          >
            <X size={24} />
          </button>
        </div>
        {!sidebarCollapsed && (
          <p className="hidden lg:block text-[10px] text-slate-500 font-bold uppercase tracking-[0.2em] ml-1">Unified Agent Gateway</p>
        )}

        <button
          onClick={() => setSidebarCollapsed((v) => !v)}
          className={`hidden lg:inline-flex mt-6 items-center justify-center rounded-xl border border-slate-800 bg-black/20 p-2 text-slate-500 hover:text-white hover:bg-white/5 transition-all ${
            sidebarCollapsed ? 'mx-auto' : ''
          }`}
          title={sidebarCollapsed ? 'Expand sidebar' : 'Collapse sidebar'}
        >
          <ChevronLeft size={16} className={sidebarCollapsed ? 'rotate-180 transition-transform' : 'transition-transform'} />
        </button>
      </div>
      
      <div className={sidebarCollapsed ? 'px-4 mb-4' : 'px-6 mb-4'}>
        {!sidebarCollapsed && (
          <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest px-4 mb-4">Management</div>
        )}
        <nav className="space-y-1">
          <SidebarItem to="/" icon={LayoutDashboard} label="Overview" collapsed={sidebarCollapsed} onClick={() => setMobileMenuOpen(false)} />
          <SidebarItem to="/playground" icon={Terminal} label="Agent Playground" collapsed={sidebarCollapsed} onClick={() => setMobileMenuOpen(false)} />
          <SidebarItem to="/sessions" icon={Activity} label="Active Sessions" collapsed={sidebarCollapsed} onClick={() => setMobileMenuOpen(false)} />
          <SidebarItem to="/workspaces" icon={FolderCode} label="Workspaces" collapsed={sidebarCollapsed} onClick={() => setMobileMenuOpen(false)} />
          <SidebarItem to="/users" icon={Users} label="Users" collapsed={sidebarCollapsed} onClick={() => setMobileMenuOpen(false)} />
        </nav>
      </div>

      <div className={sidebarCollapsed ? 'px-4 mb-4' : 'px-6 mb-4'}>
        {!sidebarCollapsed && (
          <div className="text-[10px] font-bold text-slate-500 uppercase tracking-widest px-4 mb-4">System</div>
        )}
        <nav className="space-y-1">
          <SidebarItem to="/providers" icon={Cpu} label="Providers" collapsed={sidebarCollapsed} onClick={() => setMobileMenuOpen(false)} />
          <SidebarItem to="/observability" icon={Activity} label="Observability" collapsed={sidebarCollapsed} onClick={() => setMobileMenuOpen(false)} />
          <SidebarItem to="/audit" icon={History} label="Audit Logs" collapsed={sidebarCollapsed} onClick={() => setMobileMenuOpen(false)} />
        </nav>
      </div>
      
      <div className={`mt-auto ${sidebarCollapsed ? 'p-4' : 'p-6'} space-y-4`}>
        <button 
          onClick={logout}
          className="w-full flex items-center space-x-3 px-4 py-3 rounded-xl text-slate-500 hover:text-white hover:bg-white/5 transition-all"
        >
          <LogOut size={18} />
          {!sidebarCollapsed && <span className="text-sm font-semibold tracking-wide">Deauthenticate</span>}
        </button>

        {!sidebarCollapsed && (
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
        )}
      </div>
    </>
  );

  return (
    <div className="flex h-screen bg-black text-slate-100 overflow-hidden font-sans selection:bg-blue-500/30">
      {/* Mobile Header */}
      <div className="lg:hidden fixed top-0 left-0 right-0 h-16 bg-slate-900/60 backdrop-blur-xl border-b border-slate-800/60 z-40 flex items-center justify-between px-6">
        <div className="flex items-center space-x-3">
          <div className="p-2 bg-blue-600 rounded-lg shadow-lg shadow-blue-600/20">
            <Layers size={18} className="text-white" />
          </div>
          <span className="text-lg font-black tracking-tight bg-gradient-to-r from-white to-slate-400 bg-clip-text text-transparent">
            UAG
          </span>
        </div>
        <button 
          onClick={() => setMobileMenuOpen(true)}
          className="p-2 text-slate-400 hover:text-white"
        >
          <Menu size={24} />
        </button>
      </div>

      {/* Mobile Sidebar Overlay */}
      {mobileMenuOpen && (
        <div 
          className="lg:hidden fixed inset-0 bg-black/60 backdrop-blur-sm z-50"
          onClick={() => setMobileMenuOpen(false)}
        >
          <div 
            className="w-72 h-full bg-slate-900 border-r border-slate-800 flex flex-col"
            onClick={e => e.stopPropagation()}
          >
            {sidebarContent}
          </div>
        </div>
      )}

      {/* Desktop Sidebar */}
      <div
        className={`hidden lg:flex ${
          sidebarCollapsed ? 'w-20' : 'w-72'
        } bg-slate-900/40 backdrop-blur-xl border-r border-slate-800/60 flex-col transition-[width] duration-200`}
      >
        {sidebarContent}
      </div>

      {/* Main Content Area */}
      <main className="flex-1 overflow-auto relative bg-[radial-gradient(circle_at_top_right,_var(--tw-gradient-stops))] from-slate-900/20 via-black to-black pt-16 lg:pt-0">
        <Routes>
          <Route path="/" element={<Overview />} />
          <Route path="/playground" element={<Playground />} />
          <Route path="/sessions" element={<Sessions />} />
          <Route path="/sessions/:sessionId/history" element={<SessionHistory />} />
          <Route path="/workspaces" element={<Workspaces />} />
          <Route path="/users" element={<UsersPage />} />
          <Route path="/providers" element={<Providers />} />
          <Route path="/observability" element={<Observability />} />
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
