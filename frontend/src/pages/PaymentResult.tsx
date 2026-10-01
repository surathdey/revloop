import React, { useEffect, useState } from "react";
import { Link, useLocation } from "react-router-dom";
import { CheckCircle2, Loader2, XCircle } from "lucide-react";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { Card } from "../components/common";

export default function PaymentResult() {
  const loc = useLocation();
  const { refresh } = useAuth();
  const cancelled = loc.pathname.endsWith("cancel");
  const sid = new URLSearchParams(loc.search).get("session_id");
  const [state, setState] = useState(cancelled ? "cancelled" : "checking");
  useEffect(() => {
    if (cancelled || !sid) return;
    let n = 0;
    const poll = async () => {
      try {
        const { data } = await api.get(`/payments/status/${sid}`);
        if (data.payment_status === "paid" || data.status === "completed") { setState("paid"); refresh(); return; }
      } catch { /* keep polling */ }
      if (++n < 15) setTimeout(poll, 2000); else setState("pending");
    };
    poll();
  }, [cancelled, sid, refresh]);
  return (
    <div className="mx-auto max-w-md px-4 py-20 rl-rise">
      <Card className="space-y-3 text-center" data-testid="payment-result">
        {state === "checking" && <><Loader2 className="mx-auto h-10 w-10 animate-spin text-orange-400" /><h1 className="text-2xl font-extrabold">Confirming payment…</h1></>}
        {state === "paid" && <><CheckCircle2 className="mx-auto h-12 w-12 text-emerald-400" /><h1 className="text-2xl font-extrabold" data-testid="payment-success">Payment confirmed</h1><p className="text-sm text-slate-400">Your plan and SMS allowance are updated.</p></>}
        {state === "pending" && <><h1 className="text-2xl font-extrabold">Still processing</h1><p className="text-sm text-slate-400">Stripe has not confirmed yet. Check Billing again in a minute.</p></>}
        {state === "cancelled" && <><XCircle className="mx-auto h-12 w-12 text-slate-400" /><h1 className="text-2xl font-extrabold" data-testid="payment-cancelled">Checkout cancelled</h1><p className="text-sm text-slate-400">No charge was made.</p></>}
        <Link to="/settings?tab=billing" className="inline-block rounded-lg bg-orange-500 px-4 py-2 font-semibold text-white" data-testid="back-to-billing">Back to billing</Link>
      </Card>
    </div>
  );
}
