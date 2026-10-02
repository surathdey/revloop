import React, { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { api, errMsg } from "../lib/api";
import { Card, inputCls } from "../components/common";

function Shell({ title, children }: any) {
  return (
    <div className="mx-auto max-w-md px-4 py-20 rl-rise">
      <Card className="space-y-4">
        <h1 className="text-3xl font-extrabold" data-testid="auth-page-title">{title}</h1>
        {children}
        <Link to="/login" className="block text-sm text-slate-400 hover:text-orange-300" data-testid="back-to-login">← Back to sign in</Link>
      </Card>
    </div>
  );
}

export function ForgotPassword() {
  const [email, setEmail] = useState("");
  const [msg, setMsg] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try { setMsg((await api.post("/auth/forgot-password", { email })).data.message); } catch (x) { toast.error(errMsg(x)); } finally { setBusy(false); }
  };
  return (
    <Shell title="Reset your password">
      {msg ? <p className="text-sm text-emerald-300" data-testid="forgot-sent">{msg}</p> : (
        <form onSubmit={submit} className="space-y-3">
          <p className="text-sm text-slate-400">Enter your account email and we'll send you a one-time reset link (valid 1 hour).</p>
          <Input data-testid="forgot-email-input" type="email" required className={inputCls} value={email} onChange={(e: any) => setEmail(e.target.value)} placeholder="you@garage.ca" />
          <Button data-testid="forgot-submit-btn" disabled={busy} className="w-full bg-orange-500 text-white hover:bg-orange-600">{busy ? "Sending…" : "Send reset link"}</Button>
        </form>
      )}
    </Shell>
  );
}

export function ResetPassword() {
  const [params] = useSearchParams();
  const nav = useNavigate();
  const [pw, setPw] = useState("");
  const [pw2, setPw2] = useState("");
  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (pw !== pw2) return toast.error("Passwords do not match.");
    try {
      await api.post("/auth/reset-password", { token: params.get("token") || "", password: pw });
      toast.success("Password updated. Please sign in.");
      nav("/login");
    } catch (x) { toast.error(errMsg(x)); }
  };
  return (
    <Shell title="Choose a new password">
      <form onSubmit={submit} className="space-y-3">
        <Input data-testid="reset-password-input" type="password" minLength={12} required className={inputCls} value={pw} onChange={(e: any) => setPw(e.target.value)} placeholder="New password (12+ characters)" />
        <Input data-testid="reset-password-confirm" type="password" minLength={12} required className={inputCls} value={pw2} onChange={(e: any) => setPw2(e.target.value)} placeholder="Confirm new password" />
        <Button data-testid="reset-submit-btn" className="w-full bg-orange-500 text-white hover:bg-orange-600">Set password</Button>
        <p className="text-xs text-slate-500">All other signed-in sessions will be signed out.</p>
      </form>
    </Shell>
  );
}
