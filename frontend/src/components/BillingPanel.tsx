import React from "react";
import { toast } from "sonner";
import { CreditCard, ExternalLink } from "lucide-react";
import { Button } from "@/components/ui/button";
import { api, fmt, money } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFetch, act } from "../lib/hooks";
import { Card, Label2, Pill, Spinner } from "./common";

export default function BillingPanel() {
  const { me } = useAuth();
  const { data, reload } = useFetch<any>("/billing");
  const { refresh } = useAuth();
  const change = async (p: any) => {
    if (!window.confirm(`Switch to ${p.name} (${money(p.amount)} CAD/month)? Stripe charges or credits the prorated difference now.`)) return;
    if (await act(() => api.post("/billing/change-plan", { plan: p.key }), `Plan changed to ${p.name}`)) { reload(); refresh(); }
  };
  if (!data || !me) return <Spinner />;
  const t = data.tenant;
  const tz = me.tenant.timezone;
  const go = async (plan: string) => {
    const r = await act(() => api.post("/billing/checkout", { plan, origin_url: window.location.origin }));
    if (r?.checkout_url) window.location.href = r.checkout_url;
  };
  const portal = async () => { const r = await act(() => api.post("/billing/portal", { origin_url: window.location.origin })); if (r?.url) window.location.href = r.url; };
  const subs = data.plans.filter((p: any) => p.interval);
  const pack = data.plans.find((p: any) => !p.interval);
  return (
    <div className="space-y-4">
      <Card data-testid="billing-current">
        <div className="flex flex-wrap items-center gap-2"><CreditCard className="h-4 w-4 text-orange-400" /><h3 className="font-bold capitalize" data-testid="billing-plan">{t.plan} plan</h3><Pill value={t.billing_status === "trialing" ? "pending" : t.billing_status === "past_due" ? "uncertain" : t.billing_status} testId="billing-status" /></div>
        <div className="mt-2 text-sm text-slate-400">{t.billing_status === "trialing" && `Trial ends ${fmt(t.trial_ends, tz, { dateStyle: "medium" })} · `}{t.quota} SMS segments / month · {t.extra_segments} extra segments available</div>
        {t.billing_status === "past_due" && <div className="mt-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-200" data-testid="grace-banner">Payment failed. Texts keep sending until {fmt(t.grace_until, tz)} — update your card to avoid interruption.</div>}
        {t.stripe_subscription && <Button data-testid="billing-portal-btn" variant="outline" className="mt-3 border-white/15 bg-white/5" onClick={portal}>Manage subscription <ExternalLink className="ml-1 h-3 w-3" /></Button>}
      </Card>
      {!t.stripe_subscription && (
        <div className="grid gap-4 md:grid-cols-2">
          {subs.map((p: any) => (
            <Card key={p.key} className="hover:border-orange-500/40" data-testid={`plan-${p.key}`}>
              <Label2>{p.name}</Label2>
              <div className="mt-2 font-mono-rl text-3xl">{money(p.amount)}<span className="text-sm text-slate-400"> CAD / {p.interval}</span></div>
              <div className="mt-1 text-sm text-slate-400">{p.quota.toLocaleString()} SMS segments every month</div>
              <Button data-testid={`subscribe-${p.key}`} onClick={() => go(p.key)} className="mt-4 w-full bg-orange-500 text-white hover:bg-orange-600">Subscribe</Button>
            </Card>
          ))}
        </div>
      )}
      {t.stripe_subscription && (
        <div className="grid gap-4 md:grid-cols-2" data-testid="change-plan-section">
          {subs.filter((p: any) => p.key !== t.plan).map((p: any) => (
            <Card key={p.key} data-testid={`change-plan-${p.key}`}>
              <Label2>{p.amount > (subs.find((x: any) => x.key === t.plan)?.amount || 0) ? "Upgrade" : "Downgrade"} to {p.name}</Label2>
              <div className="mt-2 font-mono-rl text-2xl">{money(p.amount)}<span className="text-sm text-slate-400"> CAD / {p.interval}</span></div>
              <div className="mt-1 text-sm text-slate-400">{p.quota.toLocaleString()} SMS segments every month. Prorated difference is invoiced immediately.</div>
              <Button data-testid={`switch-plan-${p.key}`} onClick={() => change(p)} className="mt-4 w-full bg-orange-500 text-white hover:bg-orange-600">Switch to {p.name}</Button>
            </Card>
          ))}
        </div>
      )}
      {pack && t.stripe_subscription && (
        <Card className="flex flex-wrap items-center justify-between gap-3" data-testid="sms-pack-card">
          <div><div className="font-bold">{pack.name}</div><div className="text-sm text-slate-400">{pack.segments} extra segments · {money(pack.amount)} CAD one-time</div></div>
          <Button data-testid="buy-pack-btn" onClick={() => go(pack.key)} className="bg-orange-500 text-white hover:bg-orange-600">Buy pack</Button>
        </Card>
      )}
      <Card data-testid="invoice-list">
        <h3 className="mb-2 font-bold">Invoices</h3>
        {data.invoices.length === 0 ? <div className="text-sm text-slate-500">No invoices yet.</div> : data.invoices.map((i: any) => (
          <div key={i.id} className="flex items-center justify-between border-b border-white/5 py-2 text-sm">
            <span>{i.number || i.id} · {fmt(i.created, tz, { dateStyle: "medium" })} <Pill value={i.status === "paid" ? "delivered" : "pending"} /></span>
            <span className="flex items-center gap-3"><span className="font-mono-rl">{money(i.total)} {i.currency.toUpperCase()}</span>{i.url && <a href={i.url} target="_blank" rel="noreferrer" className="text-orange-300" onClick={() => toast.message("Opening invoice")}>View</a>}</span>
          </div>
        ))}
      </Card>
      <p className="text-xs text-slate-500">Payments run in Stripe test mode. Test card 4242 4242 4242 4242, any future expiry, any CVC.</p>
    </div>
  );
}
