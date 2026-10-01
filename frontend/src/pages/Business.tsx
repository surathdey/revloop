import React from "react";
import { Link, useParams } from "react-router-dom";
import { MapPin, Phone, Wrench } from "lucide-react";
import { useFetch } from "../lib/hooks";
import { Card, Spinner } from "../components/common";

export default function Business() {
  const { slug } = useParams();
  const { data: b } = useFetch<any>(`/public/business/${slug}`);
  if (!b) return <Spinner />;
  return (
    <div className="mx-auto max-w-md px-4 py-16 rl-rise">
      <Card className="space-y-4" data-testid="business-card">
        <div className="flex items-center gap-3">
          {b.logo ? <img src={b.logo} alt="" className="h-12 w-12 rounded-lg object-cover" /> : <div className="grid h-12 w-12 place-items-center rounded-lg bg-orange-500"><Wrench className="h-5 w-5" /></div>}
          <h1 className="text-3xl font-extrabold" data-testid="business-name">{b.name}</h1>
        </div>
        {b.address && <div className="flex items-center gap-2 text-slate-300"><MapPin className="h-4 w-4 text-orange-400" />{b.address}</div>}
        {b.phone && <div className="flex items-center gap-2 text-slate-300"><Phone className="h-4 w-4 text-orange-400" />{b.phone}</div>}
        <div className="text-sm text-slate-400">Open {b.open_hour}:00 – {b.close_hour}:00</div>
        <p className="rounded-lg bg-white/5 p-3 text-xs text-slate-400">You receive texts from {b.name} because you gave consent. Reply STOP to any message to unsubscribe, or HELP for help. Message and data rates may apply.</p>
        <Link to={`/book/${b.slug}`} className="block rounded-lg bg-orange-500 py-3 text-center font-semibold text-white hover:bg-orange-600" data-testid="business-book-link">Book an appointment</Link>
      </Card>
    </div>
  );
}
