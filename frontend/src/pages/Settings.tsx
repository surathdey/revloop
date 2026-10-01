import React, { useEffect, useState } from "react";
import { QRCodeCanvas } from "qrcode.react";
import { Copy, Download, Phone, Send } from "lucide-react";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/ui/select";
import { api, money, fmt } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFetch, act } from "../lib/hooks";
import { Card, Label2, PageHeader, Pill, Spinner, inputCls } from "../components/common";

const TZS = ["America/Toronto", "America/Vancouver", "America/Edmonton", "America/Winnipeg", "America/Halifax", "America/St_Johns"];
const KEYS = ["name", "address", "phone", "review_link", "timezone", "quiet_start", "quiet_end", "review_delay", "open_hour", "close_hour", "bays", "logo"];
const NUM = ["quiet_start", "quiet_end", "review_delay", "open_hour", "close_hour", "bays"];

function F({ k, label, f, setF, type = "text", hint }: any) {
  return (
    <div className="space-y-1">
      <div className="text-xs text-slate-300">{label}</div>
      <Input data-testid={`settings-${k}`} type={type} className={inputCls} value={f[k] ?? ""} onChange={(e: any) => setF({ ...f, [k]: e.target.value })} />
      {hint && <div className="text-[11px] text-slate-500">{hint}</div>}
    </div>
  );
}

function Profile() {
  const { me, refresh } = useAuth();
  const [f, setF] = useState<any>(() => Object.fromEntries(KEYS.map((k) => [k, me ? me.tenant[k] : ""])));
  const save = async () => {
    const body = Object.fromEntries(KEYS.map((k) => [k, NUM.includes(k) ? Number(f[k]) : f[k] || ""]));
    if (await act(() => api.put("/settings", body), "Settings saved")) refresh();
  };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-3">
        <h3 className="font-bold">Business identification</h3>
        <F k="name" label="Business name (prefixes every SMS)" f={f} setF={setF} />
        <F k="address" label="Address" f={f} setF={setF} />
        <F k="phone" label="Shop phone" f={f} setF={setF} />
        <F k="logo" label="Logo URL (https)" f={f} setF={setF} />
        <F k="review_link" label="Google review link" f={f} setF={setF} hint="https://g.page/r/… or https://search.google.com/local/writereview?…" />
        <div className="space-y-1"><div className="text-xs text-slate-300">Time zone</div>
          <Select value={f.timezone} onValueChange={(v: string) => setF({ ...f, timezone: v })}>
            <SelectTrigger data-testid="settings-timezone" className={inputCls}><SelectValue /></SelectTrigger>
            <SelectContent>{TZS.map((z) => <SelectItem key={z} value={z}>{z}</SelectItem>)}</SelectContent>
          </Select></div>
      </Card>
      <Card className="space-y-3">
        <h3 className="font-bold">Hours & messaging rules</h3>
        <div className="grid grid-cols-2 gap-3">
          <F k="open_hour" label="Opens (hour 0-23)" type="number" f={f} setF={setF} />
          <F k="close_hour" label="Closes (hour 1-24)" type="number" f={f} setF={setF} />
          <F k="bays" label="Service bays" type="number" f={f} setF={setF} />
          <F k="review_delay" label="Review delay (minutes)" type="number" f={f} setF={setF} />
          <F k="quiet_start" label="Texts allowed from (9-19)" type="number" f={f} setF={setF} />
          <F k="quiet_end" label="Texts allowed until (10-20)" type="number" f={f} setF={setF} />
        </div>
        <p className="text-xs text-slate-500">Messages due outside the allowed window are held until the next allowed time in your time zone.</p>
        <Button data-testid="settings-save-btn" onClick={save} className="w-full bg-orange-500 text-white hover:bg-orange-600">Save settings</Button>
      </Card>
    </div>
  );
}

function Services() {
  const { data, reload } = useFetch<any[]>("/services");
  const [f, setF] = useState<any>({ name: "", duration: "60", price: "" });
  const add = async () => { if (await act(() => api.post("/services", { name: f.name, duration: Number(f.duration), price: Math.round(Number(f.price) * 100) }), "Service added")) { setF({ name: "", duration: "60", price: "" }); reload(); } };
  return (
    <Card>
      <div className="space-y-2">{(data || []).map((s) => <div key={s.id} className="flex justify-between rounded-lg bg-white/5 px-3 py-2 text-sm" data-testid={`service-${s.id}`}><span>{s.name} · {s.duration} min</span><span className="font-mono-rl">{money(s.price)}</span></div>)}</div>
      <div className="mt-4 grid grid-cols-2 gap-2 sm:grid-cols-4">
        <Input data-testid="service-name-input" placeholder="Service name" className={`${inputCls} col-span-2`} value={f.name} onChange={(e: any) => setF({ ...f, name: e.target.value })} />
        <Input data-testid="service-duration-input" type="number" placeholder="Minutes" className={inputCls} value={f.duration} onChange={(e: any) => setF({ ...f, duration: e.target.value })} />
        <Input data-testid="service-price-input" type="number" placeholder="Price $" className={inputCls} value={f.price} onChange={(e: any) => setF({ ...f, price: e.target.value })} />
      </div>
      <Button data-testid="service-add-btn" onClick={add} className="mt-3 bg-orange-500 text-white hover:bg-orange-600">Add service</Button>
    </Card>
  );
}

function Team() {
  const { data, reload } = useFetch<any>("/team");
  const [email, setEmail] = useState("");
  const [url, setUrl] = useState("");
  const invite = async () => { const r = await act(() => api.post("/team/invite", { email, role: "staff" }), "Invite created"); if (r) { setUrl(r.url); setEmail(""); reload(); } };
  return (
    <Card>
      <div className="space-y-2">{(data?.users || []).map((u: any) => <div key={u.id} className="flex items-center justify-between rounded-lg bg-white/5 px-3 py-2 text-sm"><span>{u.name} · <span className="text-slate-400">{u.email}</span></span><Pill value={u.role === "owner" ? "active" : "scheduled"} /></div>)}</div>
      {(data?.invites || []).map((i: any) => <div key={i.id} className="mt-2 text-xs text-slate-400">Pending invite: {i.email}</div>)}
      <div className="mt-4 flex gap-2">
        <Input data-testid="invite-email-input" type="email" placeholder="staff@email.com" className={inputCls} value={email} onChange={(e: any) => setEmail(e.target.value)} />
        <Button data-testid="invite-btn" onClick={invite} className="bg-orange-500 text-white hover:bg-orange-600">Invite staff</Button>
      </div>
      {url && <div className="mt-3 rounded-lg border border-orange-500/30 bg-orange-500/5 p-3 text-xs" data-testid="invite-url">Share this one-time link (expires in 7 days):<div className="mt-1 break-all font-mono-rl text-orange-200">{url}</div>
        <Button size="sm" variant="ghost" className="mt-1" onClick={() => { navigator.clipboard.writeText(url); toast.success("Copied"); }}><Copy className="mr-1 h-3 w-3" />Copy</Button></div>}
      <p className="mt-3 text-xs text-slate-500">Staff can use Calendar, Customers and Inbox only. Settings, automations and billing are blocked on the server.</p>
    </Card>
  );
}

function PhoneSms() {
  const { data } = useFetch<any>("/settings/status");
  const [phone, setPhone] = useState("");
  const [consent, setConsent] = useState(false);
  if (!data) return <Spinner />;
  const test = async () => { const r = await act(() => api.post("/phone/test", { phone, consent })); if (r) toast.success(r.results.length ? `Test SMS ${r.results[0].status}` : "Test queued — check the Inbox for its status"); };
  return (
    <div className="grid gap-4 lg:grid-cols-2">
      <Card className="space-y-3" data-testid="phone-status-card">
        <h3 className="flex items-center gap-2 font-bold"><Phone className="h-4 w-4 text-orange-400" />SMS number</h3>
        <div className="font-mono-rl text-2xl" data-testid="assigned-number">{data.twilio_number || "Not assigned yet"}</div>
        <div className="flex flex-wrap gap-2 text-xs"><Pill value={data.approval_status} testId="approval-status" /><Pill value={data.sms_mode} testId="sms-mode" /></div>
        <p className="text-xs text-slate-400">RevLoop approves each garage and assigns a dedicated number. Provider credentials are managed by the platform and never shown here.</p>
      </Card>
      <Card className="space-y-3">
        <h3 className="flex items-center gap-2 font-bold"><Send className="h-4 w-4 text-orange-400" />Send a test SMS</h3>
        <Input data-testid="test-sms-phone" placeholder="Your mobile number" inputMode="tel" className={inputCls} value={phone} onChange={(e: any) => setPhone(e.target.value)} />
        <label className="flex items-start gap-2 text-xs text-slate-300"><Checkbox data-testid="test-sms-consent" checked={consent} onCheckedChange={(v: boolean) => setConsent(!!v)} className="mt-0.5" />I own this number and agree to receive a test text.</label>
        <Button data-testid="test-sms-btn" onClick={test} className="bg-orange-500 text-white hover:bg-orange-600">Send test</Button>
      </Card>
    </div>
  );
}

function BookingQr() {
  const { data } = useFetch<any>("/settings/status");
  if (!data) return <Spinner />;
  const download = () => { const c = document.getElementById("booking-qr") as HTMLCanvasElement; const a = document.createElement("a"); a.href = c.toDataURL("image/png"); a.download = "booking-qr.png"; a.click(); };
  return (
    <Card className="flex flex-col items-start gap-4 sm:flex-row">
      <div className="rounded-xl bg-white p-3"><QRCodeCanvas id="booking-qr" value={data.booking_url} size={180} /></div>
      <div className="min-w-0 space-y-3">
        <Label2>Public booking page</Label2>
        <a href={data.booking_url} target="_blank" rel="noreferrer" className="block break-all font-mono-rl text-sm text-orange-300" data-testid="booking-url">{data.booking_url}</a>
        <div className="flex gap-2">
          <Button size="sm" variant="outline" className="border-white/15 bg-white/5" data-testid="copy-booking-url" onClick={() => { navigator.clipboard.writeText(data.booking_url); toast.success("Copied"); }}><Copy className="mr-1 h-3 w-3" />Copy link</Button>
          <Button size="sm" variant="outline" className="border-white/15 bg-white/5" data-testid="download-qr" onClick={download}><Download className="mr-1 h-3 w-3" />Download QR</Button>
        </div>
        <p className="text-xs text-slate-500">Print it for the counter or windshield tags.</p>
      </div>
    </Card>
  );
}

function Plan() {
  const { me } = useAuth();
  if (!me) return null;
  const t = me.tenant;
  return (
    <Card className="space-y-2" data-testid="plan-card">
      <div className="flex items-center gap-2"><h3 className="font-bold capitalize">{t.plan} plan</h3><Pill value={t.billing_status === "trialing" ? "pending" : t.billing_status} /></div>
      <div className="text-sm text-slate-400">Trial ends {fmt(t.trial_ends, t.timezone, { dateStyle: "medium" })} · {t.quota} SMS segments / month · {t.extra_segments} extra</div>
      <p className="text-xs text-slate-500">Stripe billing in CAD (plans, SMS packs, invoices) ships in Phase 3.</p>
    </Card>
  );
}

export default function Settings() {
  const [tab, setTab] = useState("profile");
  useEffect(() => { const p = new URLSearchParams(window.location.search).get("tab"); if (p) setTab(p); }, []);
  return (
    <div>
      <PageHeader title="Settings" sub="Owner-only. Changes are audited." />
      <Tabs value={tab} onValueChange={setTab}>
        <TabsList className="flex h-auto flex-wrap justify-start bg-[#131B2A]">
          {[["profile", "Business"], ["services", "Services"], ["team", "Team"], ["phone", "Phone & SMS"], ["qr", "Booking QR"], ["plan", "Plan"]].map(([v, l]) =>
            <TabsTrigger key={v} value={v} data-testid={`settings-tab-${v}`}>{l}</TabsTrigger>)}
        </TabsList>
        <TabsContent value="profile" className="mt-4"><Profile /></TabsContent>
        <TabsContent value="services" className="mt-4"><Services /></TabsContent>
        <TabsContent value="team" className="mt-4"><Team /></TabsContent>
        <TabsContent value="phone" className="mt-4"><PhoneSms /></TabsContent>
        <TabsContent value="qr" className="mt-4"><BookingQr /></TabsContent>
        <TabsContent value="plan" className="mt-4"><Plan /></TabsContent>
      </Tabs>
    </div>
  );
}
