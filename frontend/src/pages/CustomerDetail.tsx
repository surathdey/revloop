import React, { useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ArrowLeft, Car, Plus, ShieldCheck, Wrench, Bell } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, fmt, money } from "../lib/api";
import { useAuth, isOwner } from "../lib/auth";
import { useFetch, act } from "../lib/hooks";
import { Card, Label2, PageHeader, Pill, Spinner, Empty, inputCls } from "../components/common";
import BookDialog from "../components/BookDialog";

function FormDialog({ title, open, onClose, fields, onSubmit, testId }: any) {
  const [f, setF] = useState<any>({});
  return (
    <Dialog open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <DialogContent className="border-white/10 bg-[#131B2A]" data-testid={testId}>
        <DialogHeader><DialogTitle className="font-display text-xl">{title}</DialogTitle></DialogHeader>
        <form className="space-y-3" onSubmit={async (e) => { e.preventDefault(); if (await onSubmit(f)) { setF({}); onClose(); } }}>
          {fields.map(([k, label, type, opts]: any) => (
            <div key={k} className="space-y-1">
              <div className="text-xs text-slate-300">{label}</div>
              {type === "select" ? (
                <Select value={f[k] || ""} onValueChange={(v: string) => setF({ ...f, [k]: v })}>
                  <SelectTrigger data-testid={`${testId}-${k}`} className={inputCls}><SelectValue placeholder="Choose" /></SelectTrigger>
                  <SelectContent>{opts.map((o: string) => <SelectItem key={o} value={o}>{o}</SelectItem>)}</SelectContent>
                </Select>
              ) : (
                <Input data-testid={`${testId}-${k}`} type={type || "text"} className={inputCls} value={f[k] || ""} onChange={(e: any) => setF({ ...f, [k]: e.target.value })} />
              )}
            </div>
          ))}
          <Button data-testid={`${testId}-submit`} className="w-full bg-orange-500 text-white hover:bg-orange-600">Save</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function VehicleCard({ v, tz, onRule, reload }: any) {
  const ruleAct = async (id: string, operation: string) => { if (await act(() => api.post("/reminders", { id, operation }), "Reminder updated")) reload(); };
  return (
    <Card data-testid={`vehicle-${v.id}`}>
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-lg font-bold"><Car className="h-4 w-4 text-orange-400" />{v.year} {v.make} {v.model}</div>
          <div className="mt-1 font-mono-rl text-xs text-slate-400">PLATE {v.plate} · {v.km.toLocaleString()} km {v.vin && `· VIN ${v.vin}`}</div>
        </div>
        <Button data-testid={`add-rule-${v.id}`} size="sm" variant="outline" onClick={() => onRule(v)} className="border-white/15 bg-white/5"><Bell className="mr-1 h-3 w-3" />Reminder</Button>
      </div>
      {v.rules.length > 0 && (
        <div className="mt-4 space-y-2">
          <Label2>Service-due reminders</Label2>
          {v.rules.map((r: any) => (
            <div key={r.id} className="flex flex-wrap items-center justify-between gap-2 rounded-lg bg-white/5 px-3 py-2 text-sm" data-testid={`rule-${r.id}`}>
              <span>{r.name} — due {fmt(r.due_at, tz, { dateStyle: "medium" })} or {r.due_km.toLocaleString()} km</span>
              <span className="flex gap-1">
                <Button size="sm" variant="ghost" data-testid={`rule-done-${r.id}`} onClick={() => ruleAct(r.id, "done")}>Done</Button>
                <Button size="sm" variant="ghost" data-testid={`rule-snooze-${r.id}`} onClick={() => ruleAct(r.id, "snooze")}>Snooze 30d</Button>
              </span>
            </div>
          ))}
        </div>
      )}
      <div className="mt-4"><Label2>Service history</Label2></div>
      {v.records.length === 0 ? <div className="mt-2 text-sm text-slate-500">No completed services yet.</div> : v.records.map((r: any) => (
        <div key={r.id} className="mt-2 flex items-center justify-between border-l-2 border-orange-500/50 pl-3 text-sm">
          <div><div className="font-semibold"><Wrench className="mr-1 inline h-3 w-3" />{r.type}</div><div className="text-xs text-slate-400">{fmt(r.date, tz, { dateStyle: "medium" })} · {r.technician} · {r.km.toLocaleString()} km</div></div>
          <div className="font-mono-rl">{money(r.amount)}</div>
        </div>
      ))}
    </Card>
  );
}

export default function CustomerDetail() {
  const { id } = useParams();
  const { me } = useAuth();
  const { data: c, loading, reload } = useFetch<any>(`/contacts/${id}`);
  const [dlg, setDlg] = useState<string>("");
  const [ruleVehicle, setRuleVehicle] = useState<any>(null);
  const [notes, setNotes] = useState<string | null>(null);
  if (!me || (loading && !c) || !c) return <Spinner />;
  const tz = me.tenant.timezone;
  return (
    <div>
      <Link to="/customers" className="mb-4 inline-flex items-center gap-1 text-sm text-slate-400 hover:text-white" data-testid="back-to-customers"><ArrowLeft className="h-4 w-4" />Customers</Link>
      <PageHeader title={c.name} sub={`${c.phone}${c.email ? " · " + c.email : ""}`}
        actions={<>
          <Pill value={c.consent_status} testId="customer-consent-status" />
          {isOwner(me) && <Button data-testid="change-consent-btn" size="sm" variant="outline" className="border-white/15 bg-white/5" onClick={() => setDlg("consent")}><ShieldCheck className="mr-1 h-4 w-4" />Consent</Button>}
          <Button data-testid="add-vehicle-btn" size="sm" variant="outline" className="border-white/15 bg-white/5" onClick={() => setDlg("vehicle")}><Plus className="mr-1 h-4 w-4" />Vehicle</Button>
          <Button data-testid="book-for-customer-btn" size="sm" className="bg-orange-500 text-white hover:bg-orange-600" onClick={() => setDlg("book")}>Book</Button>
        </>} />
      <Tabs defaultValue="vehicles">
        <TabsList className="bg-[#131B2A]">
          <TabsTrigger value="vehicles" data-testid="tab-vehicles">Vehicles</TabsTrigger>
          <TabsTrigger value="messages" data-testid="tab-messages">Messages</TabsTrigger>
          <TabsTrigger value="consent" data-testid="tab-consent">Consent log</TabsTrigger>
          <TabsTrigger value="notes" data-testid="tab-notes">Notes</TabsTrigger>
        </TabsList>
        <TabsContent value="vehicles" className="mt-4 grid gap-4 lg:grid-cols-2">
          {c.vehicles.map((v: any) => <VehicleCard key={v.id} v={v} tz={tz} reload={reload} onRule={(x: any) => { setRuleVehicle(x); setDlg("rule"); }} />)}
        </TabsContent>
        <TabsContent value="messages" className="mt-4 space-y-2">
          {c.messages.length === 0 ? <Empty title="No messages yet" /> : c.messages.map((m: any) => (
            <Card key={m.id} className={`p-3 ${m.direction === "inbound" ? "border-orange-500/30" : ""}`} data-testid={`customer-message-${m.id}`}>
              <div className="mb-1 flex flex-wrap items-center gap-2 text-xs text-slate-400"><Pill value={m.status} /><span className="uppercase">{m.kind}</span><span>{fmt(m.sent_at || m.scheduled_at, tz)}</span>{m.error && <span className="text-amber-300">· {m.error}</span>}</div>
              <div className="whitespace-pre-wrap text-sm">{m.body || <i className="text-slate-500">Rendered at send time</i>}</div>
            </Card>
          ))}
        </TabsContent>
        <TabsContent value="consent" className="mt-4 space-y-2" data-testid="consent-log">
          {c.consents.map((e: any) => (
            <Card key={e.id} className="p-3">
              <div className="flex flex-wrap items-center gap-2 text-sm"><Pill value={e.status} /><b>{e.source}</b><span className="text-xs text-slate-400">{fmt(e.created_at, tz)}</span>{e.expires_at && <span className="text-xs text-sky-300">expires {fmt(e.expires_at, tz, { dateStyle: "medium" })}</span>}</div>
              <div className="mt-1 break-words font-mono-rl text-[11px] text-slate-400">{e.evidence}</div>
            </Card>
          ))}
        </TabsContent>
        <TabsContent value="notes" className="mt-4">
          <Textarea data-testid="customer-notes-input" className={`${inputCls} min-h-[140px]`} value={notes ?? c.notes} onChange={(e: any) => setNotes(e.target.value)} />
          <Button data-testid="save-notes-btn" className="mt-3 bg-orange-500 text-white hover:bg-orange-600" onClick={async () => { if (await act(() => api.patch(`/contacts/${c.id}`, { notes: notes ?? c.notes }), "Notes saved")) reload(); }}>Save notes</Button>
        </TabsContent>
      </Tabs>
      <FormDialog testId="vehicle-dialog" title="Add vehicle" open={dlg === "vehicle"} onClose={() => setDlg("")}
        fields={[["make", "Make"], ["model", "Model"], ["year", "Year", "number"], ["plate", "Plate"], ["km", "Odometer km", "number"], ["vin", "VIN (optional)"]]}
        onSubmit={async (f: any) => { const r = await act(() => api.post("/vehicles", { ...f, contact_id: c.id, year: Number(f.year), km: Number(f.km || 0) }), "Vehicle added"); if (r) reload(); return r; }} />
      <FormDialog testId="rule-dialog" title={`Service reminder · ${ruleVehicle?.make || ""} ${ruleVehicle?.model || ""}`} open={dlg === "rule"} onClose={() => setDlg("")}
        fields={[["name", "Service name (match a service to auto-reset on completion)"], ["due_at", "Due date", "date"], ["interval_months", "Repeat every (months)", "number"], ["interval_km", "…or every (km)", "number"]]}
        onSubmit={async (f: any) => { const r = await act(() => api.post("/reminders", { operation: "create", vehicle_id: ruleVehicle.id, name: f.name, due_at: f.due_at, interval_months: Number(f.interval_months || 6), interval_km: Number(f.interval_km || 8000) }), "Reminder created"); if (r) reload(); return r; }} />
      <FormDialog testId="consent-dialog" title="Record consent change" open={dlg === "consent"} onClose={() => setDlg("")}
        fields={[["status", "New status", "select", ["express", "implied", "none", "opted-out"]], ["source", "Source (e.g. counter conversation)"], ["evidence", "Evidence / what the customer said"], ["expires_at", "Implied consent expiry", "date"]]}
        onSubmit={async (f: any) => { const r = await act(() => api.post(`/contacts/${c.id}/consent`, { ...f, expires_at: f.expires_at || null }), "Consent recorded"); if (r) reload(); return r; }} />
      <BookDialog open={dlg === "book"} onClose={() => setDlg("")} onDone={reload} contactId={c.id} />
    </div>
  );
}
