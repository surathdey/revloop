import React, { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { Button } from "@/components/ui/button";
import { api, errMsg } from "../lib/api";
import { Card, Pill, Spinner } from "../components/common";

export default function CancelBooking() {
  const { token } = useParams();
  const [b, setB] = useState<any>(null);
  const [err, setErr] = useState("");
  const load = () => api.get(`/public/booking/${token}`).then((r) => setB(r.data)).catch((e) => setErr(errMsg(e)));
  useEffect(() => { load(); }, [token]); // eslint-disable-line react-hooks/exhaustive-deps
  if (err && !b) return <div className="p-10 text-center text-slate-400" data-testid="cancel-error">{err}</div>;
  if (!b) return <Spinner />;
  const cancel = async () => {
    if (!window.confirm("Cancel this appointment?")) return;
    try { await api.post(`/public/booking/${token}/cancel`); load(); } catch (e) { setErr(errMsg(e)); }
  };
  const open = ["scheduled", "confirmed"].includes(b.status);
  return (
    <div className="mx-auto max-w-md px-4 py-16 rl-rise">
      <Card className="space-y-3" data-testid="booking-card">
        <div className="text-xs uppercase tracking-[0.14em] text-slate-400">{b.garage}</div>
        <h1 className="text-3xl font-extrabold">{b.service}</h1>
        <div className="text-slate-300">{b.when}</div>
        <Pill value={b.status} testId="booking-status" />
        {err && <div className="text-sm text-red-300">{err}</div>}
        {open ? <Button data-testid="cancel-booking-btn" onClick={cancel} variant="outline" className="w-full border-red-500/40 bg-red-500/10">Cancel appointment</Button>
          : <p className="text-sm text-slate-400">This appointment is {b.status}. {b.garage_phone && `Call ${b.garage_phone} to rebook.`}</p>}
      </Card>
    </div>
  );
}
