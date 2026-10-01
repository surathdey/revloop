import React, { useState } from "react";
import { Download, PhoneCall, Upload } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Textarea } from "@/components/ui/textarea";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Sheet, SheetContent, SheetHeader, SheetTitle } from "@/components/ui/sheet";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, API, fmt } from "../lib/api";
import { useFetch, act } from "../lib/hooks";
import { Card, Label2, PageHeader, Pill, Spinner, Empty, inputCls } from "../components/common";

const TZ = "America/Toronto";
const STAGES = ["new", "contacted", "interested", "demo_booked", "won", "lost", "do_not_call"];
const label = (s: string) => s.replace(/_/g, " ");

function ProspectSheet({ id, onClose, onChange }: any) {
  const { data: p, reload } = useFetch<any>(id ? `/sales/prospects/${id}` : null, [id]);
  const [tx, setTx] = useState("");
  const [busy, setBusy] = useState(false);
  const move = async (stage: string) => { if (await act(() => api.post(`/sales/prospects/${id}/stage`, { stage }), "Stage updated")) { reload(); onChange(); } };
  const sim = async () => { setBusy(true); const r = await act(() => api.post(`/sales/prospects/${id}/simulate-call`, { transcript: tx }), "Call analysed"); setBusy(false); if (r) { setTx(""); reload(); onChange(); } };
  return (
    <Sheet open={!!id} onOpenChange={(o: boolean) => !o && onClose()}>
      <SheetContent className="w-full overflow-y-auto border-white/10 bg-[#131B2A] sm:max-w-lg" data-testid="prospect-sheet">
        {!p ? <Spinner /> : <>
          <SheetHeader><SheetTitle className="font-display text-2xl">{p.business_name}</SheetTitle></SheetHeader>
          <div className="mt-2 space-y-1 text-sm text-slate-300"><div className="font-mono-rl">{p.phone}</div><div>{p.contact_name} {p.city && `· ${p.city}`}</div><div className="flex gap-2"><Pill value={p.dnc ? "opted-out" : "scheduled"} testId="prospect-stage" /><span className="capitalize">{label(p.stage)}</span> · {p.attempts} attempts</div></div>
          {!p.dnc && <Select value={p.stage} onValueChange={move}><SelectTrigger data-testid="prospect-stage-select" className={`${inputCls} mt-3`}><SelectValue /></SelectTrigger><SelectContent>{STAGES.map((s) => <SelectItem key={s} value={s}>{label(s)}</SelectItem>)}</SelectContent></Select>}
          {p.summary && <Card className="mt-4 p-3"><Label2>Latest AI summary · {p.outcome}</Label2><div className="mt-1 text-sm" data-testid="prospect-summary">{p.summary}</div></Card>}
          {!p.dnc && <div className="mt-4 space-y-2 rounded-lg border border-violet-500/30 bg-violet-500/5 p-3">
            <Label2>Simulate a call (paste a transcript)</Label2>
            <Textarea data-testid="simulate-transcript" className={`${inputCls} min-h-[100px] text-xs`} value={tx} onChange={(e: any) => setTx(e.target.value)} placeholder="AI: Hi, this is Riley from RevLoop… Owner: Sure, book me a demo Tuesday." />
            <Button data-testid="simulate-call-btn" disabled={busy || tx.length < 5} onClick={sim} variant="outline" className="border-violet-500/40">{busy ? "Analysing with GPT…" : "Analyse transcript"}</Button>
          </div>}
          <div className="mt-4"><Label2>Call log</Label2></div>
          {p.calls.map((c: any) => <Card key={c.id} className="mt-2 p-3 text-xs" data-testid={`call-${c.id}`}><div className="flex flex-wrap gap-2"><Pill value={c.status === "completed" || c.status === "simulated" ? "delivered" : c.status === "failed" ? "failed" : "queued"} />{c.outcome}{c.simulated && " · simulated"}<span className="text-slate-500">{fmt(c.created_at, TZ)}</span></div>
            {c.summary && <div className="mt-1 text-slate-300">{c.summary}</div>}{c.recording_url && <a href={c.recording_url} target="_blank" rel="noreferrer" className="text-orange-300">Recording</a>}{c.error && <div className="text-red-300">{c.error}</div>}</Card>)}
          <div className="mt-4"><Label2>Stage history</Label2></div>
          {p.history.map((h: any) => <div key={h.id} className="mt-1 text-xs text-slate-400">{fmt(h.at, TZ)} · {label(h.from)} → <b className="text-slate-200">{label(h.to)}</b> · {h.by}</div>)}
        </>}
      </SheetContent>
    </Sheet>
  );
}

function Funnel() {
  const { data, reload } = useFetch<any[]>("/sales/prospects");
  const [sel, setSel] = useState("");
  const upload = async (f: File) => { const r = await act(async () => api.post("/sales/prospects/import", { csv: await f.text() })); if (r) { toast.success(`Imported ${r.created} · ${r.duplicates} duplicates · ${r.suppressed} DNC-suppressed · ${r.errors.length} errors`); reload(); } };
  if (!data) return <Spinner />;
  return (
    <div>
      <label className="mb-4 inline-flex cursor-pointer items-center gap-2 rounded-lg border border-white/15 bg-white/5 px-3 py-2 text-sm" data-testid="prospect-import-label">
        <Upload className="h-4 w-4" />Upload prospects CSV (business_name, contact_name, phone, email, city)
        <input data-testid="prospect-import-input" type="file" accept=".csv" className="hidden" onChange={(e: any) => e.target.files?.[0] && upload(e.target.files[0])} />
      </label>
      <div className="flex gap-3 overflow-x-auto pb-3" data-testid="kanban">
        {STAGES.map((s) => { const items = data.filter((p) => p.stage === s); return (
          <div key={s} className="w-64 shrink-0 rounded-xl border border-white/10 bg-[#0F172A] p-2" data-testid={`kanban-col-${s}`}>
            <div className="mb-2 flex justify-between px-1 text-xs font-semibold uppercase tracking-[0.14em] text-slate-400"><span>{label(s)}</span><span>{items.length}</span></div>
            <div className="space-y-2">{items.slice(0, 100).map((p) => (
              <button key={p.id} onClick={() => setSel(p.id)} data-testid={`prospect-card-${p.id}`} className="w-full rounded-lg border border-white/10 bg-[#131B2A] p-2 text-left text-sm transition-colors hover:border-orange-500/40">
                <div className="truncate font-semibold">{p.business_name}</div><div className="font-mono-rl text-[11px] text-slate-400">{p.phone}</div>{p.outcome && <div className="mt-1 text-[10px] text-orange-300">{p.outcome}</div>}
              </button>))}</div>
          </div>); })}
      </div>
      <ProspectSheet id={sel} onClose={() => setSel("")} onChange={reload} />
    </div>
  );
}

function Campaigns() {
  const { data, reload } = useFetch<any[]>("/sales/campaigns");
  const [f, setF] = useState<any>({ name: "", window_start: 10, window_end: 17, max_attempts: 3, retry_hours: 48 });
  const create = async () => { if (await act(() => api.post("/sales/campaigns", { ...f, window_start: Number(f.window_start), window_end: Number(f.window_end), max_attempts: Number(f.max_attempts), retry_hours: Number(f.retry_hours) }), "Campaign created with all NEW prospects")) reload(); };
  const status = async (id: string, s: string) => { if (await act(() => api.post(`/sales/campaigns/${id}/status`, { status: s }), `Campaign ${s}`)) reload(); };
  return (
    <div className="space-y-4">
      <Card className="grid grid-cols-2 gap-2 sm:grid-cols-6">
        <Input data-testid="campaign-name" placeholder="Campaign name" className={`${inputCls} col-span-2`} value={f.name} onChange={(e: any) => setF({ ...f, name: e.target.value })} />
        {[["window_start", "From hr"], ["window_end", "To hr"], ["max_attempts", "Max tries"], ["retry_hours", "Retry hrs"]].map(([k, l]) =>
          <Input key={k} data-testid={`campaign-${k}`} type="number" title={l} placeholder={l} className={inputCls} value={f[k]} onChange={(e: any) => setF({ ...f, [k]: e.target.value })} />)}
        <Button data-testid="campaign-create-btn" onClick={create} className="col-span-2 bg-orange-500 text-white hover:bg-orange-600 sm:col-span-6">Create campaign (Mon–Fri, Toronto time, CRTC hours enforced)</Button>
      </Card>
      {!data?.length ? <Empty title="No campaigns yet" /> : data.map((c) => (
        <Card key={c.id} data-testid={`campaign-${c.id}`} className="flex flex-wrap items-center justify-between gap-3">
          <div><div className="flex items-center gap-2 font-bold">{c.name}<Pill value={c.status === "running" ? "active" : c.status === "paused" ? "suspended" : "pending"} /></div>
            <div className="text-xs text-slate-400">{c.window_start}:00–{c.window_end}:00 · max {c.max_attempts} tries · retry {c.retry_hours}h · {c.stats.prospects} prospects · {c.stats.calls} calls · {c.stats.interested} interested · {c.stats.demo_booked} demos · {c.stats.do_not_call} opt-outs</div></div>
          {c.status !== "running" ? <Button size="sm" data-testid={`campaign-start-${c.id}`} className="bg-emerald-600 hover:bg-emerald-500" onClick={() => status(c.id, "running")}>Start</Button>
            : <Button size="sm" variant="outline" data-testid={`campaign-pause-${c.id}`} onClick={() => status(c.id, "paused")}>Pause</Button>}
        </Card>
      ))}
    </div>
  );
}

function Script() {
  const { data } = useFetch<any>("/sales/script");
  const [f, setF] = useState<any>(null);
  const [phone, setPhone] = useState("");
  if (!data) return <Spinner />;
  const v = f || data;
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-3">
        <Label2>Opening line (must identify RevLoop + recording notice)</Label2>
        <Textarea data-testid="script-first-message" className={`${inputCls} min-h-[90px]`} value={v.first_message} onChange={(e: any) => setF({ ...v, first_message: e.target.value })} />
        <Label2>Agent prompt (objection handling, goal)</Label2>
        <Textarea data-testid="script-system-prompt" className={`${inputCls} min-h-[200px] text-xs`} value={v.system_prompt} onChange={(e: any) => setF({ ...v, system_prompt: e.target.value })} />
        <Label2>Opt-out phrases (comma separated)</Label2>
        <Input data-testid="script-opt-out" className={inputCls} value={v.opt_out_phrases} onChange={(e: any) => setF({ ...v, opt_out_phrases: e.target.value })} />
        <Button data-testid="script-save-btn" onClick={() => act(() => api.put("/sales/script", v), "Script saved")} className="bg-orange-500 text-white hover:bg-orange-600">Save script</Button>
      </Card>
      <Card className="space-y-3">
        <h3 className="flex items-center gap-2 font-bold"><PhoneCall className="h-4 w-4 text-orange-400" />Test AI call</h3>
        <p className="text-sm text-slate-400">Places one real Vapi call to your phone. Requires Vapi keys in Platform admin → Integrations.</p>
        <Input data-testid="test-call-phone" placeholder="Your phone" className={inputCls} value={phone} onChange={(e: any) => setPhone(e.target.value)} />
        <Button data-testid="test-call-btn" onClick={() => act(() => api.post("/sales/test-call", { phone }), "Call placed")} className="bg-orange-500 text-white hover:bg-orange-600">Call me</Button>
      </Card>
    </div>
  );
}

function Dnc() {
  const { data, reload } = useFetch<any[]>("/sales/dnc");
  const [phones, setPhones] = useState("");
  const add = async () => { const r = await act(() => api.post("/sales/dnc", { phones }), "Added to do-not-call"); if (r) { setPhones(""); reload(); } };
  return (
    <Card className="space-y-3">
      <p className="text-sm text-slate-400">Numbers here are never dialled — permanently. Paste your National DNCL download or individual numbers. In-call opt-outs are added automatically.</p>
      <Textarea data-testid="dnc-input" className={inputCls} value={phones} onChange={(e: any) => setPhones(e.target.value)} placeholder="+14165550100, 647-555-0101 …" />
      <div className="flex flex-wrap gap-2">
        <Button data-testid="dnc-add-btn" onClick={add} className="bg-orange-500 text-white hover:bg-orange-600">Add numbers</Button>
        <a data-testid="export-calls-btn" href={`${API}/sales/calls/export`} className="inline-flex items-center gap-1 rounded-md border border-white/15 bg-white/5 px-3 py-2 text-sm"><Download className="h-4 w-4" />Export call-attempt log (CSV)</a>
      </div>
      <div className="max-h-80 overflow-y-auto font-mono-rl text-xs" data-testid="dnc-list">{(data || []).map((d) => <div key={d.id} className="border-b border-white/5 py-1">{d.phone} <span className="text-slate-500">· {d.reason} · {fmt(d.created_at, TZ, { dateStyle: "medium" })}</span></div>)}</div>
    </Card>
  );
}

function Stats() {
  const { data } = useFetch<any>("/sales/analytics");
  if (!data) return <Spinner />;
  return (
    <div className="grid grid-cols-2 gap-3 md:grid-cols-4" data-testid="sales-analytics">
      <Card><Label2>Calls</Label2><div className="mt-2 font-mono-rl text-3xl">{data.calls}</div></Card>
      <Card><Label2>Interested</Label2><div className="mt-2 font-mono-rl text-3xl">{data.stages.interested}</div></Card>
      <Card><Label2>Demos booked</Label2><div className="mt-2 font-mono-rl text-3xl">{data.stages.demo_booked}</div></Card>
      <Card><Label2>Do-not-call</Label2><div className="mt-2 font-mono-rl text-3xl">{data.dnc}</div></Card>
    </div>
  );
}

export default function Sales() {
  return (
    <div>
      <PageHeader title="AI Sales" sub="Prospect funnel, AI cold-call campaigns (Vapi + ElevenLabs), GPT call summaries, CRTC do-not-call compliance." />
      <Stats />
      <Tabs defaultValue="funnel" className="mt-6">
        <TabsList className="flex h-auto flex-wrap justify-start bg-[#131B2A]">
          {[["funnel", "Funnel"], ["campaigns", "Campaigns"], ["script", "Agent script"], ["dnc", "Do-not-call"]].map(([v, l]) => <TabsTrigger key={v} value={v} data-testid={`sales-tab-${v}`}>{l}</TabsTrigger>)}
        </TabsList>
        <TabsContent value="funnel" className="mt-4"><Funnel /></TabsContent>
        <TabsContent value="campaigns" className="mt-4"><Campaigns /></TabsContent>
        <TabsContent value="script" className="mt-4"><Script /></TabsContent>
        <TabsContent value="dnc" className="mt-4"><Dnc /></TabsContent>
      </Tabs>
    </div>
  );
}
