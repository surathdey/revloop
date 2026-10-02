import React, { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { Plus, Upload, Search, Car, ChevronRight } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Checkbox } from "@/components/ui/checkbox";
import { Dialog, DialogContent, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { api, CONSENT_TEXT } from "../lib/api";
import { useFetch, act } from "../lib/hooks";
import { Card, Empty, PageHeader, Pill, Spinner, inputCls } from "../components/common";

const BLANK = { name: "", phone: "", email: "", make: "", model: "", year: "", plate: "", km: "", consent: false };

function QuickAdd({ open, onClose, onDone }: any) {
  const [f, setF] = useState<any>(BLANK);
  const [busy, setBusy] = useState(false);
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    const r = await act(() => api.post("/contacts", { ...f, year: Number(f.year), km: Number(f.km || 0) }), "Customer saved");
    setBusy(false);
    if (r) { setF(BLANK); onDone(); onClose(); }
  };
  const fld = (k: string, label: string, props: any = {}) => (
    <div className="space-y-1"><Label className="text-xs text-slate-300">{label}</Label><Input data-testid={`quick-add-${k}`} className={inputCls} value={f[k]} onChange={set(k)} {...props} /></div>
  );
  return (
    <Dialog open={open} onOpenChange={(o: boolean) => !o && onClose()}>
      <DialogContent className="max-h-[92vh] overflow-y-auto border-white/10 bg-[#131B2A] sm:max-w-lg" data-testid="quick-add-dialog">
        <DialogHeader><DialogTitle className="font-display text-2xl">Quick-add customer</DialogTitle></DialogHeader>
        <form onSubmit={submit} className="space-y-3">
          <div className="grid grid-cols-2 gap-3">
            {fld("name", "Full name", { required: true, className: `${inputCls} col-span-2` })}
            {fld("phone", "Mobile phone", { required: true, inputMode: "tel", placeholder: "416 555 0123" })}
            {fld("email", "Email (optional)", { type: "email" })}
          </div>
          <div className="pt-1 text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">Vehicle</div>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
            {fld("make", "Make", { required: true })}
            {fld("model", "Model", { required: true })}
            {fld("year", "Year", { required: true, inputMode: "numeric", type: "number" })}
            {fld("plate", "Plate", { required: true })}
            {fld("km", "Odometer km", { inputMode: "numeric", type: "number" })}
          </div>
          <label className="flex items-start gap-3 rounded-lg border border-white/10 bg-[#0F172A] p-3 text-xs text-slate-300" data-testid="quick-add-consent-label">
            <Checkbox data-testid="quick-add-consent" checked={f.consent} onCheckedChange={(v: boolean) => setF({ ...f, consent: !!v })} className="mt-0.5" />
            <span><b className="text-white">Customer gave express consent to texts.</b> Read to customer: “{CONSENT_TEXT}”</span>
          </label>
          <Button data-testid="quick-add-submit" disabled={busy} className="w-full bg-orange-500 text-white hover:bg-orange-600">{busy ? "Saving…" : "Save customer"}</Button>
        </form>
      </DialogContent>
    </Dialog>
  );
}

function Import({ open, onClose, onDone }: any) {
  const [res, setRes] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const upload = async (file: File) => {
    setBusy(true);
    const r = await act(async () => api.post("/contacts/import", { csv: await file.text() }));
    setBusy(false);
    if (r) { setRes(r); onDone(); }
  };
  return (
    <Dialog open={open} onOpenChange={(o: boolean) => { if (!o) { setRes(null); onClose(); } }}>
      <DialogContent className="max-h-[90vh] overflow-y-auto border-white/10 bg-[#131B2A]" data-testid="import-dialog">
        <DialogHeader><DialogTitle className="font-display text-2xl">Import customers (CSV)</DialogTitle></DialogHeader>
        <p className="text-sm text-slate-400">Columns: <span className="font-mono-rl text-slate-300">name, phone, email, notes, tags, make, model, year, plate, km</span>. Imported customers start with <b>no SMS consent</b> — record consent before texting them.</p>
        <Input data-testid="import-file-input" type="file" accept=".csv,text/csv" disabled={busy} className={inputCls} onChange={(e: any) => e.target.files?.[0] && upload(e.target.files[0])} />
        {busy && <Spinner label="Importing" />}
        {res && (
          <div className="space-y-2 text-sm" data-testid="import-result">
            <div className="grid grid-cols-3 gap-2">
              <Card className="p-3"><div className="font-mono-rl text-2xl text-emerald-300" data-testid="import-created">{res.created}</div>created</Card>
              <Card className="p-3"><div className="font-mono-rl text-2xl text-amber-300" data-testid="import-duplicates">{res.duplicates.length}</div>duplicates</Card>
              <Card className="p-3"><div className="font-mono-rl text-2xl text-red-300" data-testid="import-errors">{res.errors.length}</div>errors</Card>
            </div>
            {res.duplicates.slice(0, 20).map((d: any) => <div key={d.row} className="text-xs text-amber-200">Row {d.row}: {d.phone} already exists — flagged, not imported</div>)}
            {res.errors.slice(0, 20).map((d: any) => <div key={d.row} className="text-xs text-red-300">Row {d.row}: {d.message}</div>)}
          </div>
        )}
      </DialogContent>
    </Dialog>
  );
}

export default function Customers() {
  const [q, setQ] = useState("");
  const [dq, setDq] = useState("");
  const [add, setAdd] = useState(false);
  const [imp, setImp] = useState(false);
  useEffect(() => { const t = setTimeout(() => setDq(q), 250); return () => clearTimeout(t); }, [q]);
  const { data, loading, reload } = useFetch<any[]>(`/contacts?q=${encodeURIComponent(dq)}`);
  return (
    <div>
      <PageHeader title="Customers" sub="Search by name, phone, plate, VIN or tag."
        actions={<>
          <Button data-testid="open-import-btn" variant="outline" onClick={() => setImp(true)} className="border-white/15 bg-white/5"><Upload className="mr-2 h-4 w-4" />Import CSV</Button>
          <Button data-testid="open-quick-add-btn" onClick={() => setAdd(true)} className="bg-orange-500 text-white hover:bg-orange-600"><Plus className="mr-2 h-4 w-4" />Quick add</Button>
        </>} />
      <div className="relative mb-4">
        <Search className="absolute left-3 top-3 h-4 w-4 text-slate-500" />
        <Input data-testid="customer-search-input" value={q} onChange={(e: any) => setQ(e.target.value)} placeholder="Search customers…" className={`${inputCls} pl-9`} />
      </div>
      {loading && !data ? <Spinner /> : !data?.length ? <Empty title="No customers found" sub="Add one in under a minute with Quick add." testId="customers-empty" /> : (
        <>
        <div className="mb-2 text-xs text-slate-400" data-testid="customer-count">{dq ? `${data.length} match${data.length === 1 ? "" : "es"}` : `${data.length} customer${data.length === 1 ? "" : "s"}`}</div>
        <div className="grid gap-2" data-testid="customer-list">
          {data.map((c) => (
            <Link key={c.id} to={`/customers/${c.id}`} data-testid={`customer-row-${c.id}`}
              className="group flex items-center gap-4 rounded-xl border border-white/10 bg-[#131B2A] px-4 py-3 transition-colors hover:border-orange-500/40">
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2"><span className="font-semibold">{c.name}</span><Pill value={c.consent_status} /></div>
                <div className="mt-0.5 font-mono-rl text-xs text-slate-400">{c.phone}</div>
              </div>
              <div className="hidden min-w-0 text-right text-xs text-slate-400 sm:block">
                {c.vehicles.slice(0, 2).map((v: any) => <div key={v.id} className="flex items-center justify-end gap-1"><Car className="h-3 w-3" />{v.year} {v.make} {v.model} · <span className="font-mono-rl">{v.plate}</span></div>)}
              </div>
              <ChevronRight className="h-4 w-4 text-slate-600 group-hover:text-orange-400" />
            </Link>
          ))}
        </div>
        </>
      )}
      <QuickAdd open={add} onClose={() => setAdd(false)} onDone={reload} />
      <Import open={imp} onClose={() => setImp(false)} onDone={reload} />
    </div>
  );
}
