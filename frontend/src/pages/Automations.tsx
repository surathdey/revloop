import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Play, RotateCcw } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { api, fmt, segments } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFetch, act } from "../lib/hooks";
import { Card, Label2, PageHeader, Spinner, Empty, inputCls } from "../components/common";

const NAMES: Record<string, string> = {
  confirmation: "Booking confirmation", reminder24: "24-hour reminder", reminder2: "2-hour reminder", serviceCompleted: "Service completed",
  review: "Review request", followup: "Review follow-up (3 days, only if not clicked)", serviceDue: "Service due", optout: "Opt-out confirmation",
};
const SAMPLE: Record<string, string> = {
  first_name: "Priya", business_name: "Your Garage", service_name: "Oil & filter change", appointment_date: "Mon, Oct 6",
  appointment_time: "9:30 AM", booking_link: "https://rvl.link/b/abc123", review_link: "https://rvl.link/r/xyz789",
};

const NO_APPT = ["serviceDue", "optout"];
const APPT_VARS = ["appointment_date", "appointment_time"];

function TemplateEditor({ t, vars, tenantName, slug }: any) {
  const [body, setBody] = useState(t.body);
  const [saved, setSaved] = useState(t.body);
  const missing = NO_APPT.includes(t.kind) ? APPT_VARS.filter((v) => body.includes(`{${v}}`)) : [];
  const preview = Object.entries(SAMPLE).reduce((s, [k, v]) => s.split(`{${k}}`).join(k === "business_name" ? tenantName : v), body);
  const full = t.kind === "optout" ? `${tenantName}: ${preview}` : `${tenantName}: ${preview}\nInfo: ${window.location.origin}/business/${slug}. Reply STOP to opt out.`;
  const seg = segments(full);
  return (
    <Card data-testid={`template-${t.kind}`}>
      <div className="mb-2 flex items-center justify-between"><h3 className="font-bold">{NAMES[t.kind]}</h3>
        <span data-testid={`segments-${t.kind}`} className={`font-mono-rl text-xs ${seg.count > 1 ? "text-amber-300" : "text-emerald-300"}`}>{seg.chars} chars · {seg.count} segment{seg.count > 1 ? "s" : ""}{seg.unicode ? " · unicode" : ""}</span></div>
      <Textarea data-testid={`template-body-${t.kind}`} value={body} onChange={(e: any) => setBody(e.target.value)} className={`${inputCls} min-h-[90px] font-mono-rl text-xs`} />
      <div className="mt-2 flex flex-wrap gap-1">
        {vars.map((v: string) => {
          const na = NO_APPT.includes(t.kind) && APPT_VARS.includes(v);
          return <button key={v} type="button" disabled={na} title={na ? "Not available: this message is not tied to an appointment" : ""} data-testid={`var-${t.kind}-${v}`} onClick={() => setBody(body + ` {${v}}`)}
            className={`rounded border px-1.5 py-0.5 font-mono-rl text-[10px] ${na ? "cursor-not-allowed border-white/5 text-slate-600 line-through" : "border-white/10 text-slate-400 hover:border-orange-500/50 hover:text-orange-300"}`}>{`{${v}}`}</button>;
        })}
      </div>
      <div className="mt-3 rounded-lg bg-[#0F172A] p-3 text-xs text-slate-300 whitespace-pre-wrap" data-testid={`template-preview-${t.kind}`}>{full}</div>
      <div className="mt-1 text-[11px] text-slate-500" data-testid={`template-state-${t.kind}`}>{body === saved ? "Preview matches the saved template" : "Unsaved changes — preview shows your edit"}</div>
      {missing.length > 0 && <div className="mt-1 text-[11px] text-amber-300" data-testid={`template-warning-${t.kind}`}>{missing.map((v) => `{${v}}`).join(", ")} will be blank in real texts — this message is not tied to an appointment.</div>}
      <div className="mt-3 flex gap-2">
        <Button data-testid={`template-save-${t.kind}`} size="sm" className="bg-orange-500 text-white hover:bg-orange-600" onClick={async () => { const r = await act(() => api.put(`/templates/${t.kind}`, { body }), "Template saved"); if (r) setSaved(body); }}>Save</Button>
        <Button size="sm" variant="ghost" data-testid={`template-reset-${t.kind}`} onClick={() => setBody(t.default)}><RotateCcw className="mr-1 h-3 w-3" />Default</Button>
      </div>
    </Card>
  );
}

function Reminders({ tz }: any) {
  const { data, loading, reload } = useFetch<any[]>("/reminders");
  if (loading && !data) return <Spinner />;
  if (!data?.length) return <Empty title="No service-due reminders" sub="Add one from a customer's vehicle card." />;
  return (
    <div className="space-y-2">
      {data.map((r) => (
        <Card key={r.id} className="flex flex-wrap items-center justify-between gap-2 p-3" data-testid={`auto-rule-${r.id}`}>
          <div><Link to={`/customers/${r.contact?.id}`} className="font-semibold hover:text-orange-300">{r.contact?.name}</Link> · {r.name}
            <div className="text-xs text-slate-400">{r.vehicle?.make} {r.vehicle?.model} · due {fmt(r.due_at, tz, { dateStyle: "medium" })} / {r.due_km?.toLocaleString()} km{r.snoozed_until ? ` · snoozed until ${fmt(r.snoozed_until, tz, { dateStyle: "medium" })}` : ""}</div></div>
          <div className="flex gap-1">
            <Button size="sm" variant="ghost" onClick={async () => { if (await act(() => api.post("/reminders", { id: r.id, operation: "done" }), "Marked done")) reload(); }}>Done</Button>
            <Button size="sm" variant="ghost" onClick={async () => { if (await act(() => api.post("/reminders", { id: r.id, operation: "snooze" }), "Snoozed")) reload(); }}>Snooze</Button>
          </div>
        </Card>
      ))}
    </div>
  );
}

export default function Automations() {
  const { me } = useAuth();
  const { data } = useFetch<any>("/templates");
  if (!me || !data) return <Spinner />;
  const runNow = async () => { const r = await act(() => api.post("/messages/process")); if (r) toast.success(`Queue processed: ${r.service_due_queued} service-due reminder(s) queued, ${r.results.length} message(s) handled`); };
  return (
    <div>
      <PageHeader title="Automations" sub="Every automated text includes your business name and opt-out. Quiet hours, 30-day review cap and consent checks are enforced at send time."
        actions={<Button data-testid="run-queue-btn" variant="outline" className="border-white/15 bg-white/5" onClick={runNow}><Play className="mr-1 h-4 w-4" />Send due messages now</Button>} />
      <Tabs defaultValue="templates">
        <TabsList className="bg-[#131B2A]">
          <TabsTrigger value="templates" data-testid="tab-templates">SMS templates</TabsTrigger>
          <TabsTrigger value="reminders" data-testid="tab-reminders">Service-due reminders</TabsTrigger>
        </TabsList>
        <TabsContent value="templates" className="mt-4">
          <div className="mb-3"><Label2>Variables</Label2></div>
          <div className="grid gap-4 lg:grid-cols-2">
            {data.templates.map((t: any) => <TemplateEditor key={t.kind} t={t} vars={data.variables} tenantName={me.tenant.name} slug={me.tenant.slug} />)}
          </div>
        </TabsContent>
        <TabsContent value="reminders" className="mt-4"><Reminders tz={me.tenant.timezone} /></TabsContent>
      </Tabs>
    </div>
  );
}
