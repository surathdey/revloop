import React from "react";
import { Link } from "react-router-dom";
import { CalendarDays, CheckCircle2, MessageSquare, Star, Users, AlertTriangle, Circle, Gauge } from "lucide-react";
import { useFetch } from "../lib/hooks";
import { useAuth } from "../lib/auth";
import { fmt } from "../lib/api";
import { Card, Label2, PageHeader, Pill, Spinner, Empty } from "../components/common";

function Kpi({ label, value, icon: Icon, sub, id, wide }: any) {
  return (
    <Card className={`hover:border-orange-500/40 ${wide ? "md:col-span-2" : ""}`} data-testid={`kpi-${id}`}>
      <div className="flex items-start justify-between">
        <Label2>{label}</Label2>
        <Icon className="h-4 w-4 text-orange-400" />
      </div>
      <div className="mt-3 font-mono-rl text-3xl font-semibold sm:text-4xl" data-testid={`kpi-${id}-value`}>{value}</div>
      {sub && <div className="mt-1 text-xs text-slate-400">{sub}</div>}
    </Card>
  );
}

const SETUP = [
  ["approved", "Garage approved by RevLoop"], ["number", "Dedicated SMS number assigned"], ["address", "Business address added"],
  ["review_link", "Google review link added"], ["services", "Services configured"], ["customers", "First customer added"],
];

export default function Dashboard() {
  const { me } = useAuth();
  const { data, loading } = useFetch<any>("/dashboard");
  if (loading || !data || !me) return <Spinner />;
  const k = data.kpis;
  const tz = me.tenant.timezone;
  const pct = k.quota ? Math.min(100, Math.round((100 * k.sms_used) / k.quota)) : 0;
  return (
    <div>
      <PageHeader title={`Good day, ${me.user.name.split(" ")[0]}`} sub="Here is how your shop is tracking this month." />
      <div className="grid grid-cols-2 gap-3 sm:gap-4 md:grid-cols-4">
        <Kpi id="appointments" label="Appointments" value={k.appointments_month} icon={CalendarDays} sub={`${k.completed_month} completed`} />
        <Kpi id="noshow" label="No-show rate" value={`${k.no_show_rate}%`} icon={AlertTriangle} sub="of closed visits" />
        <Kpi id="sms" label="SMS sent" value={k.sms_sent_month} icon={MessageSquare} sub={`${k.queued} queued · ${k.uncertain} need review`} />
        <Kpi id="reviews" label="Review clicks" value={k.review_clicks_month} icon={Star} sub={`${k.review_requests_month} requests sent`} />
      </div>
      <div className="mt-4 grid gap-4 lg:grid-cols-3">
        <Card className="lg:col-span-2" data-testid="today-appointments">
          <div className="mb-4 flex items-center justify-between">
            <h3 className="text-lg font-bold">Today in the bays</h3>
            <Link to="/calendar" className="text-sm text-orange-300 hover:text-orange-200" data-testid="open-calendar-link">Open calendar →</Link>
          </div>
          {data.today.length === 0 ? <Empty title="No appointments today" sub="Share your booking link to fill the bays." testId="today-empty" /> : (
            <div className="divide-y divide-white/5">
              {data.today.map((a: any) => (
                <div key={a.id} className="flex items-center justify-between gap-3 py-3" data-testid={`today-appt-${a.id}`}>
                  <div className="min-w-0">
                    <div className="font-mono-rl text-sm text-orange-300">{fmt(a.start, tz, { timeStyle: "short" })} · Bay {a.bay}</div>
                    <div className="truncate font-semibold">{a.contact?.name} — {a.service?.name}</div>
                    <div className="truncate text-xs text-slate-400">{a.vehicle?.year} {a.vehicle?.make} {a.vehicle?.model} · {a.vehicle?.plate}</div>
                  </div>
                  <Pill value={a.status} />
                </div>
              ))}
            </div>
          )}
        </Card>
        <div className="space-y-4">
          <Card data-testid="sms-usage-card">
            <div className="flex items-center justify-between"><Label2>SMS segments this month</Label2><Gauge className="h-4 w-4 text-sky-400" /></div>
            <div className="mt-3 font-mono-rl text-2xl"><span data-testid="sms-used">{k.sms_used}</span><span className="text-slate-500"> / {k.quota}</span></div>
            <div className="mt-3 h-2 overflow-hidden rounded-full bg-white/5"><div className="h-full bg-orange-500 transition-all" style={{ width: `${pct}%` }} /></div>
            <div className="mt-2 text-xs text-slate-400">{k.consented} of {k.customers} customers have SMS consent</div>
          </Card>
          <Card data-testid="setup-checklist">
            <h3 className="mb-3 text-lg font-bold">Launch checklist</h3>
            <ul className="space-y-2 text-sm">
              {SETUP.map(([key, label]) => (
                <li key={key} className="flex items-center gap-2" data-testid={`setup-${key}`}>
                  {data.setup[key] ? <CheckCircle2 className="h-4 w-4 text-emerald-400" /> : <Circle className="h-4 w-4 text-slate-600" />}
                  <span className={data.setup[key] ? "text-slate-300" : "text-slate-400"}>{label}</span>
                </li>
              ))}
            </ul>
            <Link to="/settings" className="mt-4 inline-block text-sm text-orange-300" data-testid="setup-settings-link">Finish setup →</Link>
          </Card>
          {data.attention.length > 0 && (
            <Card className="border-amber-500/40" data-testid="attention-card">
              <h3 className="mb-2 flex items-center gap-2 font-bold"><Users className="h-4 w-4 text-amber-400" />Replies needing attention</h3>
              {data.attention.map((a: any) => (
                <Link key={a.id} to={`/customers/${a.entity_id}`} className="block py-1 text-sm text-amber-200 hover:underline">{JSON.parse(a.detail).body} — {fmt(a.created_at, tz)}</Link>
              ))}
            </Card>
          )}
        </div>
      </div>
    </div>
  );
}
