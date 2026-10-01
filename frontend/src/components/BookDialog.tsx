import React, { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, todayIn } from "../lib/api";
import { useAuth } from "../lib/auth";
import { act } from "../lib/hooks";
import { inputCls } from "./common";

function Pick({ id, label, value, onChange, items }: any) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-slate-300">{label}</div>
      <Select value={value} onValueChange={onChange}>
        <SelectTrigger data-testid={`book-${id}`} className={inputCls}><SelectValue placeholder="Choose" /></SelectTrigger>
        <SelectContent>{items.map((i: any) => <SelectItem key={i.value} value={i.value} data-testid={`book-${id}-option-${i.value}`}>{i.label}</SelectItem>)}</SelectContent>
      </Select>
    </div>
  );
}

export default function BookDialog({ open, onClose, onDone, contactId, date }: any) {
  const { me: auth } = useAuth();
  const me = auth || null;
  const tz = me ? me.tenant.timezone : "America/Toronto";
  const [opts, setOpts] = useState<any>({ contacts: [], services: [], users: [] });
  const [f, setF] = useState<any>({});
  useEffect(() => {
    if (!open) return;
    Promise.all([api.get("/contacts"), api.get("/services"), api.get("/team")]).then(([c, s, t]) =>
      setOpts({ contacts: c.data, services: s.data, users: t.data.users }));
    setF({ contact_id: contactId || "", date: date || todayIn(tz), time: "09:00", bay: "1", staff_id: me?.user.id });
  }, [open, contactId, date, tz, me]);
  const contact = opts.contacts.find((c: any) => c.id === f.contact_id);
  const set = (k: string) => (v: any) => setF((x: any) => ({ ...x, [k]: v, ...(k === "contact_id" ? { vehicle_id: "" } : {}) }));
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    const r = await act(() => api.post("/appointments", { contact_id: f.contact_id, vehicle_id: f.vehicle_id, service_id: f.service_id,
      start: `${f.date}T${f.time}`, bay: Number(f.bay), staff_id: f.staff_id, notes: f.notes || "" }), "Appointment booked — confirmation queued");
    if (r) { onDone?.(); onClose(); }
  };
  const bays = Array.from({ length: me?.tenant.bays || 1 }, (_, i) => ({ value: String(i + 1), label: `Bay ${i + 1}` }));
  return (
    <Dialog open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <DialogContent className="max-h-[92vh] overflow-y-auto border-white/10 bg-[#131B2A] sm:max-w-lg" data-testid="book-dialog">
        <DialogHeader><DialogTitle className="font-display text-2xl">Book appointment</DialogTitle></DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <Pick id="contact" label="Customer" value={f.contact_id} onChange={set("contact_id")} items={opts.contacts.map((c: any) => ({ value: c.id, label: `${c.name} · ${c.phone}` }))} />
          <Pick id="vehicle" label="Vehicle" value={f.vehicle_id || ""} onChange={set("vehicle_id")} items={(contact?.vehicles || []).map((v: any) => ({ value: v.id, label: `${v.year} ${v.make} ${v.model} · ${v.plate}` }))} />
          <Pick id="service" label="Service" value={f.service_id || ""} onChange={set("service_id")} items={opts.services.map((s: any) => ({ value: s.id, label: `${s.name} (${s.duration} min)` }))} />
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1"><div className="text-xs text-slate-300">Date</div><Input data-testid="book-date" type="date" className={inputCls} value={f.date || ""} onChange={(e: any) => set("date")(e.target.value)} /></div>
            <div className="space-y-1"><div className="text-xs text-slate-300">Time</div><Input data-testid="book-time" type="time" step={900} className={inputCls} value={f.time || ""} onChange={(e: any) => set("time")(e.target.value)} /></div>
            <Pick id="bay" label="Bay" value={f.bay || ""} onChange={set("bay")} items={bays} />
            <Pick id="staff" label="Technician" value={f.staff_id || ""} onChange={set("staff_id")} items={opts.users.map((u: any) => ({ value: u.id, label: u.name }))} />
          </div>
          <Input data-testid="book-notes" placeholder="Notes (optional)" className={inputCls} value={f.notes || ""} onChange={(e: any) => set("notes")(e.target.value)} />
          <Button data-testid="book-submit" className="w-full bg-orange-500 text-white hover:bg-orange-600">Book</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}
