import React, { useEffect, useState } from "react";
import { useParams } from "react-router-dom";
import { CheckCircle2, MapPin, Phone, Wrench } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Checkbox } from "@/components/ui/checkbox";
import { api, errMsg, money, addDays } from "../lib/api";
import { Card, Label2, Spinner, inputCls } from "../components/common";

const HERO = "https://images.unsplash.com/photo-1625047509248-ec889cbff17f?crop=entropy&cs=srgb&fm=jpg&q=80&w=1200";

export default function PublicBooking() {
  const { slug } = useParams();
  const [g, setG] = useState<any>(null);
  const [err, setErr] = useState("");
  const [svc, setSvc] = useState<any>(null);
  const [date, setDate] = useState(addDays(new Date().toISOString().slice(0, 10), 1));
  const [slots, setSlots] = useState<any[] | null>(null);
  const [slot, setSlot] = useState<any>(null);
  const [f, setF] = useState<any>({ name: "", phone: "", make: "", model: "", year: "", plate: "", consent: false, website: "" });
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState<any>(null);

  useEffect(() => { api.get(`/public/garage/${slug}`).then((r) => setG(r.data)).catch((e) => setErr(errMsg(e))); }, [slug]);
  useEffect(() => {
    if (!svc) return;
    setSlots(null); setSlot(null);
    api.get("/public/slots", { params: { slug, service: svc.id, date } }).then((r) => setSlots(r.data.slots)).catch((e) => setErr(errMsg(e)));
  }, [svc, date, slug]);

  if (err && !g) return <div className="p-10 text-center text-slate-400" data-testid="booking-error">{err}</div>;
  if (!g) return <Spinner />;
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true); setErr("");
    try {
      const r = await api.post("/public/book", { slug, service_id: svc.id, date, start: slot.time, ...f, year: Number(f.year) });
      setDone(r.data);
    } catch (x) { setErr(errMsg(x)); } finally { setBusy(false); }
  };
  if (done) return (
    <div className="mx-auto max-w-xl px-4 py-16 text-center rl-rise" data-testid="booking-success">
      <CheckCircle2 className="mx-auto h-14 w-14 text-emerald-400" />
      <h1 className="mt-4 text-4xl font-extrabold">You're booked!</h1>
      <p className="mt-2 text-slate-300">{done.service} at {g.name} — {date} at {done.when}.</p>
      {f.consent && <p className="mt-2 text-sm text-slate-400">We'll text you a confirmation and reminders. Reply STOP anytime to opt out.</p>}
    </div>
  );
  return (
    <div className="min-h-screen pb-16">
      <div className="relative h-44 overflow-hidden sm:h-56">
        <img src={HERO} alt="" className="absolute inset-0 h-full w-full object-cover opacity-50" />
        <div className="absolute inset-0 bg-gradient-to-t from-[#0B0F17] to-transparent" />
        <div className="absolute bottom-4 left-0 right-0 mx-auto max-w-xl px-4">
          <div className="flex items-center gap-3">
            {g.logo ? <img src={g.logo} alt="" className="h-12 w-12 rounded-lg object-cover" /> : <div className="grid h-12 w-12 place-items-center rounded-lg bg-orange-500"><Wrench className="h-5 w-5" /></div>}
            <div><h1 className="text-3xl font-extrabold tracking-tight" data-testid="garage-name">{g.name}</h1>
              <div className="flex flex-wrap gap-3 text-xs text-slate-300">{g.address && <span><MapPin className="mr-1 inline h-3 w-3" />{g.address}</span>}{g.phone && <span><Phone className="mr-1 inline h-3 w-3" />{g.phone}</span>}</div></div>
          </div>
        </div>
      </div>
      <form onSubmit={submit} className="mx-auto max-w-xl space-y-6 px-4 pt-6">
        <section className="space-y-2"><Label2>1 · Choose a service</Label2>
          {g.services.map((s: any) => (
            <button type="button" key={s.id} data-testid={`public-service-${s.id}`} onClick={() => setSvc(s)}
              className={`flex w-full items-center justify-between rounded-xl border px-4 py-3 text-left transition-colors ${svc?.id === s.id ? "border-orange-500 bg-orange-500/10" : "border-white/10 bg-[#131B2A] hover:border-white/25"}`}>
              <span><span className="font-semibold">{s.name}</span><span className="ml-2 text-xs text-slate-400">{s.duration} min</span></span><span className="font-mono-rl text-sm">{money(s.price)}</span>
            </button>
          ))}
        </section>
        {svc && <section className="space-y-2"><Label2>2 · Pick a date & time</Label2>
          <Input data-testid="public-date" type="date" min={new Date().toISOString().slice(0, 10)} value={date} onChange={(e: any) => e.target.value && setDate(e.target.value)} className={inputCls} />
          {slots === null ? <Spinner label="Finding open bays" /> : slots.length === 0 ? <div className="text-sm text-slate-400" data-testid="no-slots">No openings this day. Try another date.</div> : (
            <div className="grid grid-cols-3 gap-2 sm:grid-cols-4" data-testid="slot-grid">
              {slots.map((s) => <button type="button" key={s.time} data-testid={`slot-${s.label.replace(/[^0-9APM]/g, "")}`} onClick={() => setSlot(s)}
                className={`rounded-lg border py-2 font-mono-rl text-sm ${slot?.time === s.time ? "border-orange-500 bg-orange-500 text-white" : "border-white/10 bg-[#131B2A] hover:border-orange-500/50"}`}>{s.label}</button>)}
            </div>
          )}
        </section>}
        {slot && <section className="space-y-3"><Label2>3 · Your details</Label2>
          <Input data-testid="public-name" required placeholder="Full name" className={inputCls} value={f.name} onChange={set("name")} />
          <Input data-testid="public-phone" required placeholder="Mobile phone" inputMode="tel" className={inputCls} value={f.phone} onChange={set("phone")} />
          <div className="grid grid-cols-2 gap-2">
            <Input data-testid="public-make" required placeholder="Make" className={inputCls} value={f.make} onChange={set("make")} />
            <Input data-testid="public-model" required placeholder="Model" className={inputCls} value={f.model} onChange={set("model")} />
            <Input data-testid="public-year" required type="number" placeholder="Year" className={inputCls} value={f.year} onChange={set("year")} />
            <Input data-testid="public-plate" required placeholder="Plate" className={inputCls} value={f.plate} onChange={set("plate")} />
          </div>
          <input type="text" name="website" tabIndex={-1} autoComplete="off" value={f.website} onChange={set("website")} className="hidden" aria-hidden="true" />
          <Card className="p-3">
            <label className="flex items-start gap-3 text-xs text-slate-300" data-testid="public-consent-label">
              <Checkbox data-testid="public-consent" checked={f.consent} onCheckedChange={(v: boolean) => setF({ ...f, consent: !!v })} className="mt-0.5" />
              <span><b className="text-white">Optional:</b> {g.consent_text} — {g.name}{g.address ? `, ${g.address}` : ""}.</span>
            </label>
          </Card>
          <p className="text-[11px] text-slate-500">Booking does not require agreeing to texts.</p>
          {err && <div className="text-sm text-red-300" data-testid="public-book-error">{err}</div>}
          <Button data-testid="public-book-submit" disabled={busy} className="w-full bg-orange-500 py-6 text-base font-semibold text-white hover:bg-orange-600">{busy ? "Booking…" : `Book ${slot.label}`}</Button>
        </section>}
      </form>
    </div>
  );
}
