import React from "react";
import { NavLink, useNavigate } from "react-router-dom";
import { LayoutDashboard, CalendarDays, Users, MessageSquare, Zap, Settings, ShieldCheck, LogOut, Wrench } from "lucide-react";
import { toast } from "sonner";
import { useAuth, isOwner } from "../lib/auth";
import { api, errMsg } from "../lib/api";

type Item = { to: string; label: string; icon: any; id: string };

function useNav(): Item[] {
  const { me } = useAuth();
  if (!me) return [];
  const garage: Item[] = [
    { to: "/", label: "Dashboard", icon: LayoutDashboard, id: "dashboard" },
    { to: "/calendar", label: "Calendar", icon: CalendarDays, id: "calendar" },
    { to: "/customers", label: "Customers", icon: Users, id: "customers" },
    { to: "/inbox", label: "Inbox", icon: MessageSquare, id: "inbox" },
    { to: "/automations", label: "Automations", icon: Zap, id: "automations" },
    { to: "/settings", label: "Settings", icon: Settings, id: "settings" },
  ];
  const admin: Item = { to: "/admin", label: "Platform", icon: ShieldCheck, id: "admin" };
  if (me.user.role === "superadmin") return me.impersonating ? [...garage, admin] : [admin];
  if (me.user.role === "staff") return garage.filter((i) => ["calendar", "customers", "inbox"].includes(i.id));
  return isOwner(me) ? garage : [];
}

function ImpersonationBanner() {
  const { me, refresh } = useAuth();
  const nav = useNavigate();
  if (!me || !me.impersonating) return null;
  const stop = async () => {
    try {
      await api.post("/admin/impersonate/stop");
      await refresh();
      nav("/admin");
    } catch (e) {
      toast.error(errMsg(e));
    }
  };
  return (
    <div data-testid="impersonation-banner" className="flex items-center justify-between gap-3 bg-amber-500 px-4 py-2 text-sm font-semibold text-black">
      <span>Support mode: viewing {me.tenant.name}. All actions are audited.</span>
      <button data-testid="stop-impersonation-btn" onClick={stop} className="rounded-md bg-black/80 px-3 py-1 text-xs text-white hover:bg-black">Exit</button>
    </div>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const { me, logout } = useAuth();
  const nav = useNavigate();
  const items = useNav();
  if (!me) return null;
  const link = ({ isActive }: { isActive: boolean }) =>
    `flex items-center gap-3 rounded-lg px-3 py-2 text-sm font-medium transition-colors duration-200 ${isActive ? "bg-orange-500/15 text-orange-300" : "text-slate-300 hover:bg-white/5 hover:text-white"}`;
  return (
    <div className="min-h-screen bg-[#0B0F17]">
      <ImpersonationBanner />
      <aside className="fixed inset-y-0 left-0 z-30 hidden w-60 flex-col border-r border-white/10 bg-[#0B0F17] p-4 lg:flex" style={{ top: me.impersonating ? 36 : 0 }}>
        <div className="mb-8 flex items-center gap-2 px-2">
          <div className="grid h-8 w-8 place-items-center rounded-lg bg-orange-500"><Wrench className="h-4 w-4 text-white" /></div>
          <div className="font-display text-xl font-extrabold tracking-tight">RevLoop</div>
        </div>
        <div className="mb-4 truncate px-2 text-xs uppercase tracking-[0.14em] text-slate-500" data-testid="sidebar-tenant-name">{me.tenant.name}</div>
        <nav className="flex flex-1 flex-col gap-1">
          {items.map((i) => (
            <NavLink key={i.id} to={i.to} end={i.to === "/"} className={link} data-testid={`nav-${i.id}`}>
              <i.icon className="h-4 w-4" /> {i.label}
            </NavLink>
          ))}
        </nav>
        <div className="border-t border-white/10 pt-3">
          <div className="px-2 text-sm font-semibold" data-testid="current-user-name">{me.user.name}</div>
          <div className="px-2 text-xs capitalize text-slate-500" data-testid="current-user-role">{me.user.role}</div>
          <button data-testid="logout-btn" onClick={async () => { await logout(); nav("/login"); }} className="mt-2 flex w-full items-center gap-2 rounded-lg px-2 py-2 text-sm text-slate-400 hover:bg-white/5 hover:text-white">
            <LogOut className="h-4 w-4" /> Sign out
          </button>
        </div>
      </aside>
      <header className="sticky top-0 z-20 flex items-center justify-between border-b border-white/10 bg-[#0B0F17]/80 px-4 py-3 backdrop-blur-md lg:hidden">
        <div className="flex items-center gap-2">
          <div className="grid h-7 w-7 place-items-center rounded-md bg-orange-500"><Wrench className="h-3.5 w-3.5 text-white" /></div>
          <div className="max-w-[55vw] truncate font-display text-lg font-extrabold">{me.tenant.name}</div>
        </div>
        <button data-testid="mobile-logout-btn" onClick={async () => { await logout(); nav("/login"); }} className="rounded-md p-2 text-slate-400 hover:text-white" aria-label="Sign out">
          <LogOut className="h-4 w-4" />
        </button>
      </header>
      <main className="px-4 pb-28 pt-6 sm:px-6 lg:ml-60 lg:px-10 lg:pb-12 lg:pt-10">{children}</main>
      <nav className="fixed inset-x-0 bottom-0 z-30 flex justify-around border-t border-white/10 bg-[#0B0F17]/90 px-1 pb-[env(safe-area-inset-bottom)] backdrop-blur-md lg:hidden">
        {items.map((i) => (
          <NavLink key={i.id} to={i.to} end={i.to === "/"} data-testid={`mobile-nav-${i.id}`}
            className={({ isActive }) => `flex flex-1 flex-col items-center gap-0.5 py-2 text-[10px] font-medium ${isActive ? "text-orange-400" : "text-slate-400"}`}>
            <i.icon className="h-5 w-5" /> {i.label}
          </NavLink>
        ))}
      </nav>
    </div>
  );
}
