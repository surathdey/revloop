import React, { useState } from "react";
import { useNavigate } from "react-router-dom";
import { Activity, Building2, KeyRound, Phone, ScrollText, Trash2 } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, fmt } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFetch, act } from "../lib/hooks";
import { Card, Label2, PageHeader, Pill, Spinner, inputCls } from "../components/common";

const TZ = "America/Toronto";

function Overview() {
  const { data } = useFetch<any>("/admin/overview");
  if (!data) return <Spinner />;
  const items = [["tenants", "Garages"], ["pending", "Awaiting approval"], ["suspended", "Suspended"], ["contacts", "Contacts"], ["queued", "SMS queued"],
    ["uncertain", "SMS uncertain"], ["sent_24h", "Sent 24h"], ["failed_24h", "Failed 24h"]];
  return (
    <div className="space-y-4">
      <div className="grid grid-cols-2 gap-3 md:grid-cols-4">
        {items.map(([k, l]) => <Card key={k} data-testid={`admin-stat-${k}`}><Label2>{l}</Label2><div className="mt-2 font-mono-rl text-3xl">{data[k]}</div></Card>)}
      </div>
      <Card className="flex flex-wrap gap-3 text-sm" data-testid="admin-health">
        <span>Database: <Pill value={data.db === "ok" ? "active" : "failed"} /></span>
        <span>Twilio: <Pill value={data.twilio_configured ? "active" : "pending"} /></span>
        <span>SMS mode: <Pill value={data.sms_mode} testId="admin-sms-mode" /></span>
        <span className="text-slate-400">Last cron run: {data.last_cron ? fmt(data.last_cron.created_at, TZ) : "never"}</span>
      </Card>
    </div>
  );
}

function Garages() {
  const nav = useNavigate();
  const { refresh } = useAuth();
  const { data, reload } = useFetch<any[]>("/admin/tenants");
  const { data: nums, reload: reloadNums } = useFetch<any[]>("/admin/numbers");
  const [imp, setImp] = useState<any>(null);
  const [reason, setReason] = useState("");
  const free = (nums || []).filter((n) => !n.tenant_id);
  const action = async (id: string, a: string) => { if (await act(() => api.post(`/admin/tenants/${id}/action`, { action: a }), `Garage ${a}d`)) reload(); };
  const assign = async (id: string, number_id: string) => { const r = await act(() => api.post(`/admin/tenants/${id}/assign-number`, { number_id })); if (r) { toast.success(`Number assigned. ${r.note}`); reload(); reloadNums(); } };
  const release = async (id: string) => { if (await act(() => api.post(`/admin/tenants/${id}/release-number`), "Number released")) { reload(); reloadNums(); } };
  const start = async () => { if (await act(() => api.post("/admin/impersonate", { tenant_id: imp.id, reason }), "Support session started")) { await refresh(); nav("/"); } };
  if (!data) return <Spinner />;
  return (
    <div className="space-y-3">
      {data.map((t) => (
        <Card key={t.id} data-testid={`tenant-${t.slug}`}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div>
              <div className="text-lg font-bold">{t.name}</div>
              <div className="text-xs text-slate-400">{t.owner?.email} · {t.contacts} contacts · created {fmt(t.created_at, TZ, { dateStyle: "medium" })}</div>
              <div className="mt-2 flex flex-wrap gap-2"><Pill value={t.approval_status} testId={`tenant-approval-${t.slug}`} /><Pill value={t.status} testId={`tenant-status-${t.slug}`} />
                <span className="font-mono-rl text-xs text-slate-300" data-testid={`tenant-number-${t.slug}`}>{t.twilio_number || "no number"}</span></div>
            </div>
            <div className="flex flex-wrap gap-2">
              {t.approval_status !== "approved" ? <Button size="sm" data-testid={`approve-${t.slug}`} className="bg-emerald-600 hover:bg-emerald-500" onClick={() => action(t.id, "approve")}>Approve</Button>
                : <Button size="sm" variant="ghost" onClick={() => action(t.id, "unapprove")}>Unapprove</Button>}
              {t.status === "active" ? <Button size="sm" variant="outline" data-testid={`suspend-${t.slug}`} className="border-red-500/40" onClick={() => action(t.id, "suspend")}>Suspend</Button>
                : <Button size="sm" variant="outline" data-testid={`activate-${t.slug}`} className="border-emerald-500/40" onClick={() => action(t.id, "activate")}>Activate</Button>}
              {t.status !== "cancelled" && <Button size="sm" variant="ghost" data-testid={`cancel-${t.slug}`} onClick={() => window.confirm(`Cancel ${t.name}?`) && action(t.id, "cancel")}>Cancel</Button>}
              <Button size="sm" variant="outline" data-testid={`impersonate-${t.slug}`} className="border-amber-500/40" onClick={() => { setImp(t); setReason(""); }}>Support login</Button>
            </div>
          </div>
          <div className="mt-3 flex flex-wrap items-center gap-2">
            {t.twilio_number ? <Button size="sm" variant="ghost" data-testid={`release-${t.slug}`} onClick={() => release(t.id)}>Release number</Button> : (
              <Select onValueChange={(v: string) => assign(t.id, v)}>
                <SelectTrigger data-testid={`assign-number-${t.slug}`} className={`${inputCls} h-8 w-56`}><SelectValue placeholder={free.length ? "Assign number from pool" : "No free numbers in pool"} /></SelectTrigger>
                <SelectContent>{free.map((n) => <SelectItem key={n.id} value={n.id}>{n.number} {n.label}</SelectItem>)}</SelectContent>
              </Select>
            )}
          </div>
        </Card>
      ))}
      <Dialog open={!!imp} onOpenChange={(o: boolean) => !o && setImp(null)}>
        <DialogContent className="border-white/10 bg-[#131B2A]" data-testid="impersonate-dialog">
          <DialogHeader><DialogTitle className="font-display text-xl">Support login · {imp?.name}</DialogTitle></DialogHeader>
          <p className="text-sm text-slate-400">The reason is written to the garage's audit log.</p>
          <Input data-testid="impersonate-reason" placeholder="Reason (10+ characters)" className={inputCls} value={reason} onChange={(e: any) => setReason(e.target.value)} />
          <Button data-testid="impersonate-start-btn" onClick={start} className="bg-amber-500 text-black hover:bg-amber-400">Start support session</Button>
        </DialogContent>
      </Dialog>
    </div>
  );
}

function Numbers() {
  const { data, reload } = useFetch<any[]>("/admin/numbers");
  const [f, setF] = useState({ number: "", label: "" });
  const add = async () => { if (await act(() => api.post("/admin/numbers", f), "Number added")) { setF({ number: "", label: "" }); reload(); } };
  return (
    <Card>
      <div className="space-y-2">{(data || []).map((n) => (
        <div key={n.id} className="flex items-center justify-between rounded-lg bg-white/5 px-3 py-2 text-sm" data-testid={`pool-number-${n.number}`}>
          <span className="font-mono-rl">{n.number} <span className="text-slate-400">{n.label}</span></span>
          <span className="flex items-center gap-2">{n.tenant_name ? <span className="text-xs text-emerald-300">{n.tenant_name}</span> : <span className="text-xs text-slate-500">available</span>}
            {!n.tenant_id && <button onClick={async () => { if (await act(() => api.delete(`/admin/numbers/${n.id}`), "Removed")) reload(); }} className="text-slate-500 hover:text-red-400"><Trash2 className="h-4 w-4" /></button>}</span>
        </div>))}</div>
      <div className="mt-4 flex flex-wrap gap-2">
        <Input data-testid="pool-number-input" placeholder="+1 289 555 0100" className={`${inputCls} w-52`} value={f.number} onChange={(e: any) => setF({ ...f, number: e.target.value })} />
        <Input data-testid="pool-label-input" placeholder="Label" className={`${inputCls} w-40`} value={f.label} onChange={(e: any) => setF({ ...f, label: e.target.value })} />
        <Button data-testid="pool-add-btn" onClick={add} className="bg-orange-500 text-white hover:bg-orange-600">Add to pool</Button>
      </div>
      <p className="mt-3 text-xs text-slate-500">Numbers must exist in the connected Twilio account. Assigning one sets its inbound SMS webhook automatically.</p>
    </Card>
  );
}

function Integrations() {
  const { data, reload } = useFetch<any>("/admin/settings");
  const [vals, setVals] = useState<any>({});
  const [check, setCheck] = useState<any>(null);
  if (!data) return <Spinner />;
  const save = async (values: any) => { if (await act(() => api.put("/admin/settings", { values }), "Saved (encrypted)")) { setVals({}); reload(); } };
  const keys = Object.keys(data).filter((k) => k !== "SMS_MODE");
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-3">
        <h3 className="flex items-center gap-2 font-bold"><KeyRound className="h-4 w-4 text-orange-400" />Twilio</h3>
        {keys.map((k) => (
          <div key={k} className="space-y-1">
            <div className="flex justify-between text-xs text-slate-300"><span className="font-mono-rl">{k}</span><span className="text-slate-500">{data[k].set ? data[k].value : "not set"}</span></div>
            <Input data-testid={`setting-${k}`} type={data[k].secret ? "password" : "text"} placeholder={data[k].set ? "Leave blank to keep" : "Enter value"} className={inputCls}
              value={vals[k] || ""} onChange={(e: any) => setVals({ ...vals, [k]: e.target.value })} />
          </div>
        ))}
        <div className="flex gap-2">
          <Button data-testid="settings-save-integrations" onClick={() => save(Object.fromEntries(Object.entries(vals).filter(([, v]) => v)))} className="bg-orange-500 text-white hover:bg-orange-600">Save</Button>
          <Button data-testid="twilio-check-btn" variant="outline" className="border-white/15 bg-white/5" onClick={async () => setCheck(await act(() => api.post("/admin/twilio/check")))}>Test connection</Button>
        </div>
        {check && <div className="rounded-lg bg-emerald-500/10 p-3 text-xs" data-testid="twilio-check-result">Account {check.friendly_name} · {check.status}<br />Numbers: {check.numbers.map((n: any) => n.number).join(", ")}</div>}
      </Card>
      <Card className="space-y-3">
        <h3 className="font-bold">SMS delivery mode</h3>
        <div className="flex items-center gap-2"><Pill value={data.SMS_MODE.value} testId="current-sms-mode" /></div>
        <p className="text-sm text-slate-400">Simulated mode runs every rule (consent, quiet hours, caps, quota) but never contacts Twilio. Live sends real SMS from each garage's assigned number.</p>
        <div className="flex gap-2">
          <Button data-testid="sms-mode-live" disabled={data.SMS_MODE.value === "live"} onClick={() => window.confirm("Send REAL text messages to customers?") && save({ SMS_MODE: "live" })} className="bg-emerald-600 hover:bg-emerald-500">Go live</Button>
          <Button data-testid="sms-mode-simulated" disabled={data.SMS_MODE.value !== "live"} variant="outline" className="border-white/15 bg-white/5" onClick={() => save({ SMS_MODE: "simulated" })}>Simulate</Button>
        </div>
      </Card>
    </div>
  );
}

function Audits() {
  const [q, setQ] = useState("");
  const { data } = useFetch<any[]>(`/admin/audits?action=${encodeURIComponent(q)}`);
  return (
    <Card>
      <Input data-testid="audit-filter" placeholder="Filter by action (e.g. consent, impersonation)" className={`${inputCls} mb-3`} value={q} onChange={(e: any) => setQ(e.target.value)} />
      <div className="divide-y divide-white/5" data-testid="audit-list">
        {(data || []).map((a) => (
          <div key={a.id} className="py-2 text-xs">
            <div className="flex flex-wrap gap-2"><span className="font-mono-rl text-orange-300">{a.action}</span><span className="text-slate-300">{a.tenant_name}</span><span className="text-slate-500">{fmt(a.created_at, TZ)} · {a.actor}</span></div>
            <div className="mt-0.5 break-all font-mono-rl text-[10px] text-slate-500">{a.detail}</div>
          </div>
        ))}
      </div>
    </Card>
  );
}

export default function Admin() {
  return (
    <div>
      <PageHeader title="Platform admin" sub="RevLoop team console — garages, numbers, integrations and audit." />
      <Tabs defaultValue="overview">
        <TabsList className="flex h-auto flex-wrap justify-start bg-[#131B2A]">
          <TabsTrigger value="overview" data-testid="admin-tab-overview"><Activity className="mr-1 h-3 w-3" />Health</TabsTrigger>
          <TabsTrigger value="garages" data-testid="admin-tab-garages"><Building2 className="mr-1 h-3 w-3" />Garages</TabsTrigger>
          <TabsTrigger value="numbers" data-testid="admin-tab-numbers"><Phone className="mr-1 h-3 w-3" />Number pool</TabsTrigger>
          <TabsTrigger value="integrations" data-testid="admin-tab-integrations"><KeyRound className="mr-1 h-3 w-3" />Integrations</TabsTrigger>
          <TabsTrigger value="audits" data-testid="admin-tab-audits"><ScrollText className="mr-1 h-3 w-3" />Audit log</TabsTrigger>
        </TabsList>
        <TabsContent value="overview" className="mt-4"><Overview /></TabsContent>
        <TabsContent value="garages" className="mt-4"><Garages /></TabsContent>
        <TabsContent value="numbers" className="mt-4"><Numbers /></TabsContent>
        <TabsContent value="integrations" className="mt-4"><Integrations /></TabsContent>
        <TabsContent value="audits" className="mt-4"><Audits /></TabsContent>
      </Tabs>
    </div>
  );
}
