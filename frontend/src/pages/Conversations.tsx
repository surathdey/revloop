import React, { useState } from "react";
import { Link } from "react-router-dom";
import { Send, FlaskConical } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, fmt, segments } from "../lib/api";
import { useAuth } from "../lib/auth";
import { useFetch, act } from "../lib/hooks";
import { Card, Empty, PageHeader, Pill, Spinner, inputCls } from "../components/common";

function Thread({ contactId, tz, sim }: any) {
  const { data, loading, reload } = useFetch<any[]>(`/messages?contact_id=${contactId}`);
  const [body, setBody] = useState("");
  const [reply, setReply] = useState("");
  const msgs = (data || []).slice().reverse();
  const c = data?.[0];
  const send = async () => { if (await act(() => api.post("/messages/send", { contact_id: contactId, body }), "Message queued")) { setBody(""); setTimeout(reload, 800); } };
  const simulate = async () => { if (await act(() => api.post("/messages/simulate-reply", { contact_id: contactId, body: reply }), "Reply simulated")) { setReply(""); reload(); } };
  return (
    <Card className="flex min-h-[60vh] flex-col p-0" data-testid="conversation-thread">
      <div className="flex items-center justify-between border-b border-white/10 px-4 py-3">
        <Link to={`/customers/${contactId}`} className="font-semibold hover:text-orange-300" data-testid="thread-customer-link">{c?.contact_name || "Customer"}</Link>
        {c && <Pill value={c.contact_consent} testId="thread-consent" />}
      </div>
      <div className="flex-1 space-y-3 overflow-y-auto p-4">
        {loading && !data ? <Spinner /> : msgs.map((m) => (
          <div key={m.id} className={`flex ${m.direction === "inbound" ? "justify-start" : "justify-end"}`} data-testid={`thread-msg-${m.id}`}>
            <div className={`max-w-[85%] rounded-2xl px-3 py-2 text-sm ${m.direction === "inbound" ? "rounded-bl-sm bg-white/10" : "rounded-br-sm bg-orange-500/15 border border-orange-500/25"}`}>
              <div className="whitespace-pre-wrap">{m.body || <i className="text-slate-400">({m.kind} — rendered at send)</i>}</div>
              <div className="mt-1 flex flex-wrap items-center gap-1.5 text-[10px] text-slate-400"><Pill value={m.status} />{m.kind} · {fmt(m.sent_at || m.scheduled_at, tz)}{m.error && ` · ${m.error}`}</div>
            </div>
          </div>
        ))}
      </div>
      <div className="space-y-2 border-t border-white/10 p-3">
        <div className="flex gap-2">
          <Input data-testid="compose-input" value={body} onChange={(e: any) => setBody(e.target.value)} placeholder="Type a message…" className={inputCls} />
          <Button data-testid="compose-send-btn" disabled={!body.trim()} onClick={send} className="bg-orange-500 text-white hover:bg-orange-600"><Send className="h-4 w-4" /></Button>
        </div>
        <div className="text-[11px] text-slate-500">{segments(body).count} segment(s) before footer · business name + “Reply STOP to opt out” added automatically</div>
        {sim && (
          <div className="flex gap-2 rounded-lg border border-violet-500/30 bg-violet-500/5 p-2">
            <FlaskConical className="mt-2 h-4 w-4 shrink-0 text-violet-300" />
            <Input data-testid="simulate-reply-input" value={reply} onChange={(e: any) => setReply(e.target.value)} placeholder="Simulate customer reply (YES, STOP, CANCEL…)" className={inputCls} />
            <Button data-testid="simulate-reply-btn" disabled={!reply.trim()} variant="outline" onClick={simulate} className="border-violet-500/40">Simulate</Button>
          </div>
        )}
      </div>
    </Card>
  );
}

export default function Conversations() {
  const { me } = useAuth();
  const { data, loading } = useFetch<any[]>("/conversations");
  const [sel, setSel] = useState<string>("");
  if (!me) return null;
  const tz = me.tenant.timezone;
  const sim = me.sms_mode !== "live";
  return (
    <div>
      <PageHeader title="Inbox" sub={sim ? "SMS is in SIMULATED mode — nothing is sent to real phones." : "Live SMS via your dedicated number."} />
      <div className="grid gap-4 lg:grid-cols-[340px_1fr]">
        <div className={`space-y-2 ${sel ? "hidden lg:block" : ""}`} data-testid="conversation-list">
          {loading && !data ? <Spinner /> : !data?.length ? <Empty title="No conversations yet" sub="Messages appear here once customers are booked or texted." testId="inbox-empty" /> : data.map((c) => (
            <button key={c.contact_id} data-testid={`conversation-${c.contact_id}`} onClick={() => setSel(c.contact_id)}
              className={`w-full rounded-xl border px-4 py-3 text-left transition-colors ${sel === c.contact_id ? "border-orange-500/50 bg-orange-500/10" : "border-white/10 bg-[#131B2A] hover:border-white/20"}`}>
              <div className="flex items-center justify-between gap-2"><span className="font-semibold">{c.contact_name}</span><span className="text-[10px] text-slate-500">{fmt(c.created_at, tz, { dateStyle: "short", timeStyle: "short" })}</span></div>
              <div className="mt-1 truncate text-xs text-slate-400">{c.direction === "inbound" ? "↩ " : ""}{c.body || `(${c.kind})`}</div>
              {c.inbound > 0 && <div className="mt-1 text-[10px] text-orange-300">{c.inbound} customer repl{c.inbound === 1 ? "y" : "ies"}</div>}
            </button>
          ))}
        </div>
        {sel ? (
          <div>
            <button className="mb-2 text-sm text-slate-400 lg:hidden" onClick={() => setSel("")} data-testid="thread-back-btn">← All conversations</button>
            <Thread key={sel} contactId={sel} tz={tz} sim={sim} />
          </div>
        ) : <div className="hidden lg:block"><Empty title="Pick a conversation" /></div>}
      </div>
    </div>
  );
}
