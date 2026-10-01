import React, { useState } from "react";
import { Link } from "react-router-dom";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, addDays, fmt, todayIn, tzParts } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFetch, act } from "../lib/hooks";
import { PageHeader, Pill, inputCls } from "../components/common";
import BookDialog from "../components/BookDialog";

const HOUR_PX = 64;

function ApptDialog({ a, tz, onClose, onDone }: any) {
  const [km, setKm] = useState("");
  const [notes, setNotes] = useState("");
  if (!a) return null;
  const open = ["scheduled", "confirmed"].includes(a.status);
  const go = async (status: string) => {
    const r = await act(() => api.post(`/appointments/${a.id}/status`, { status, notes, km: km ? Number(km) : null }), `Marked ${status}`);
    if (r) { onDone(); onClose(); }
  };
  return (
    <Dialog open={!!a} onOpenChange={(o: boolean) => !o && onClose()}>
      <DialogContent className="max-h-[92vh] overflow-y-auto border-white/10 bg-[#131B2A]" data-testid="appointment-dialog">
        <DialogHeader><DialogTitle className="font-display text-2xl">{a.service?.name}</DialogTitle></DialogHeader>
        <div className="space-y-1 text-sm">
          <div className="flex items-center gap-2"><Pill value={a.status} testId="appointment-status" /> <span className="font-mono-rl text-orange-300">{fmt(a.start, tz)} · Bay {a.bay}</span></div>
          <Link to={`/customers/${a.contact_id}`} className="block font-semibold hover:text-orange-300" data-testid="appointment-customer-link">{a.contact?.name} · {a.contact?.phone}</Link>
          <div className="text-slate-400">{a.vehicle?.year} {a.vehicle?.make} {a.vehicle?.model} · {a.vehicle?.plate} · {a.staff_name}</div>
          {a.notes && <div className="text-slate-400">“{a.notes}”</div>}
        </div>
        {!open && <p className="text-sm text-slate-400" data-testid="appointment-closed-note">This appointment is closed — no further status changes.</p>}
        {open && (
          <div className="space-y-3 pt-2">
            <div className="grid grid-cols-2 gap-2">
              <Input data-testid="complete-km-input" type="number" placeholder={`Odometer (${a.vehicle?.km} km)`} className={inputCls} value={km} onChange={(e: any) => setKm(e.target.value)} />
              <Input data-testid="complete-notes-input" placeholder="Work notes" className={inputCls} value={notes} onChange={(e: any) => setNotes(e.target.value)} />
            </div>
            <div className="grid grid-cols-2 gap-2">
              {a.status === "scheduled" && <Button data-testid="appt-confirm-btn" variant="outline" className="border-emerald-500/40 bg-emerald-500/10" onClick={() => go("confirmed")}>Confirm</Button>}
              <Button data-testid="appt-complete-btn" className="bg-orange-500 text-white hover:bg-orange-600" onClick={() => go("completed")}>Complete</Button>
              <Button data-testid="appt-noshow-btn" variant="outline" className="border-amber-500/40 bg-amber-500/10" onClick={() => go("no-show")}>No-show</Button>
              <Button data-testid="appt-cancel-btn" variant="outline" className="border-red-500/40 bg-red-500/10" onClick={() => go("cancelled")}>Cancel</Button>
            </div>
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function Calendar() {
  const { me } = useAuth();
  const tz = me ? me.tenant.timezone : "America/Toronto";
  const [date, setDate] = useState(todayIn(tz));
  const [book, setBook] = useState(false);
  const [sel, setSel] = useState<any>(null);
  const { data, reload } = useFetch<any[]>(`/appointments?start=${date}T00:00&end=${addDays(date, 1)}T00:00`);
  if (!me) return null;
  const { open_hour: oh, close_hour: ch, bays } = me.tenant;
  const hours = Array.from({ length: ch - oh }, (_, i) => oh + i);
  const label = new Date(date + "T12:00:00Z").toLocaleDateString("en-CA", { weekday: "long", month: "long", day: "numeric", timeZone: "UTC" });
  return (
    <div>
      <PageHeader title="Bay calendar" sub={label}
        actions={<>
          <Button data-testid="cal-prev-btn" variant="outline" size="icon" className="border-white/15 bg-white/5" onClick={() => setDate(addDays(date, -1))}><ChevronLeft className="h-4 w-4" /></Button>
          <Input data-testid="cal-date-input" type="date" value={date} onChange={(e: any) => e.target.value && setDate(e.target.value)} className={`${inputCls} w-40`} />
          <Button data-testid="cal-next-btn" variant="outline" size="icon" className="border-white/15 bg-white/5" onClick={() => setDate(addDays(date, 1))}><ChevronRight className="h-4 w-4" /></Button>
          <Button data-testid="cal-today-btn" variant="outline" className="border-white/15 bg-white/5" onClick={() => setDate(todayIn(tz))}>Today</Button>
          <Button data-testid="open-book-btn" className="bg-orange-500 text-white hover:bg-orange-600" onClick={() => setBook(true)}><Plus className="mr-1 h-4 w-4" />Book</Button>
        </>} />
      <div className="overflow-x-auto rounded-xl border border-white/10 bg-[#131B2A]" data-testid="bay-calendar">
        <div className="grid min-w-[560px]" style={{ gridTemplateColumns: `56px repeat(${bays}, minmax(160px, 1fr))` }}>
          <div className="border-b border-white/10" />
          {Array.from({ length: bays }, (_, b) => <div key={b} className="border-b border-l border-white/10 px-3 py-2 text-xs font-semibold uppercase tracking-[0.14em] text-slate-400">Bay {b + 1}</div>)}
          <div className="relative">
            {hours.map((h) => <div key={h} style={{ height: HOUR_PX }} className="border-b border-white/5 pr-2 pt-1 text-right font-mono-rl text-[11px] text-slate-500">{h}:00</div>)}
          </div>
          {Array.from({ length: bays }, (_, b) => (
            <div key={b} className="relative border-l border-white/10" style={{ height: hours.length * HOUR_PX }} data-testid={`bay-column-${b + 1}`}>
              {hours.map((h) => <div key={h} style={{ height: HOUR_PX }} className="border-b border-white/5" />)}
              {(data || []).filter((a) => a.bay === b + 1).map((a) => {
                const p = tzParts(a.start, tz);
                const top = ((p.h - oh) * 60 + p.m) * (HOUR_PX / 60);
                const h = ((new Date(a.end).getTime() - new Date(a.start).getTime()) / 60000) * (HOUR_PX / 60);
                const dim = ["cancelled", "no-show"].includes(a.status);
                return (
                  <button key={a.id} data-testid={`appt-block-${a.id}`} onClick={() => setSel(a)} style={{ top, height: Math.max(h - 2, 28) }}
                    className={`absolute inset-x-1 overflow-hidden rounded-lg border px-2 py-1 text-left text-xs transition-transform hover:-translate-y-0.5 ${dim ? "border-white/10 bg-white/5 opacity-50" : a.status === "completed" ? "border-slate-400/30 bg-slate-500/20" : a.status === "confirmed" ? "border-emerald-500/40 bg-emerald-500/15" : "border-orange-500/40 bg-orange-500/15"}`}>
                    <div className="font-semibold">{a.contact?.name}</div>
                    <div className="truncate text-slate-300">{a.service?.name} · {a.vehicle?.plate}</div>
                  </button>
                );
              })}
            </div>
          ))}
        </div>
      </div>
      <BookDialog open={book} onClose={() => setBook(false)} onDone={reload} date={date} />
      <ApptDialog a={sel} tz={tz} onClose={() => setSel(null)} onDone={reload} />
    </div>
  );
}
