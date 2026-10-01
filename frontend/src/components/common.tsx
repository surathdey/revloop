import React from "react";
import { Loader2 } from "lucide-react";

const STATUS: Record<string, string> = {
  queued: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  sending: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  sent: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  delivered: "bg-emerald-500/20 text-emerald-200 border-emerald-500/40",
  simulated: "bg-violet-500/15 text-violet-300 border-violet-500/30",
  received: "bg-orange-500/15 text-orange-300 border-orange-500/30",
  suppressed: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  failed: "bg-red-500/15 text-red-300 border-red-500/30",
  undelivered: "bg-red-500/15 text-red-300 border-red-500/30",
  uncertain: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  scheduled: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  confirmed: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  completed: "bg-slate-400/15 text-slate-200 border-slate-400/30",
  cancelled: "bg-red-500/10 text-red-300 border-red-500/25",
  "no-show": "bg-amber-500/15 text-amber-300 border-amber-500/30",
  express: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  implied: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  none: "bg-slate-500/15 text-slate-300 border-slate-500/30",
  "opted-out": "bg-red-500/15 text-red-300 border-red-500/30",
  approved: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  pending: "bg-amber-500/15 text-amber-300 border-amber-500/30",
  active: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  suspended: "bg-red-500/15 text-red-300 border-red-500/30",
  live: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
};

export function Pill({ value, testId }: { value: string; testId?: string }) {
  const label = value === "none" ? "no consent" : value;
  return (
    <span data-testid={testId} className={`inline-flex items-center rounded-md border px-2 py-0.5 text-[11px] font-semibold uppercase tracking-wide ${STATUS[value] || STATUS.none}`}>
      {label}
    </span>
  );
}

export function PageHeader({ title, sub, actions }: { title: string; sub?: string; actions?: React.ReactNode }) {
  return (
    <div className="mb-6 flex flex-col gap-3 sm:flex-row sm:items-end sm:justify-between rl-rise">
      <div>
        <h1 className="text-3xl font-extrabold tracking-tight sm:text-4xl" data-testid="page-title">{title}</h1>
        {sub && <p className="mt-1 text-sm text-slate-400">{sub}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Card({ children, className = "", ...rest }: React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div {...rest} className={`rounded-xl border border-white/10 bg-[#131B2A] p-4 sm:p-5 transition-colors duration-200 ${className}`}>
      {children}
    </div>
  );
}

export function Label2({ children }: { children: React.ReactNode }) {
  return <div className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">{children}</div>;
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div className="flex items-center gap-2 py-10 text-sm text-slate-400" data-testid="loading-indicator">
      <Loader2 className="h-4 w-4 animate-spin text-orange-400" /> {label}…
    </div>
  );
}

export function Empty({ title, sub, testId }: { title: string; sub?: string; testId?: string }) {
  return (
    <div data-testid={testId} className="rounded-xl border border-dashed border-white/10 p-8 text-center">
      <div className="font-display text-lg font-bold">{title}</div>
      {sub && <div className="mt-1 text-sm text-slate-400">{sub}</div>}
    </div>
  );
}

export const inputCls = "bg-[#0F172A] border-slate-700 focus-visible:ring-orange-500 text-white";
