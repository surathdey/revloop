import React, { useEffect, useState } from "react";
import { Navigate, Link, useNavigate, useSearchParams } from "react-router-dom";
import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { Wrench } from "lucide-react";
import { api, errMsg } from "../lib/api";
import { useAuth } from "../lib/auth";
import { inputCls } from "../components/common";

const HERO = "https://images.unsplash.com/photo-1618312980096-873bd19759a0?crop=entropy&cs=srgb&fm=jpg&q=85&w=1600";

function googleLogin(invite?: string) {
  if (invite) localStorage.setItem("rl_invite", invite);
  // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
  const redirectUrl = window.location.origin + "/";
  window.location.href = `https://auth.emergentagent.com/?redirect=${encodeURIComponent(redirectUrl)}`;
}

function Field({ id, label, ...rest }: any) {
  return (
    <div className="space-y-1.5">
      <Label htmlFor={id} className="text-slate-300">{label}</Label>
      <Input id={id} data-testid={`${id}-input`} className={inputCls} {...rest} />
    </div>
  );
}

export default function Login() {
  const { me, refresh } = useAuth();
  const nav = useNavigate();
  const [params] = useSearchParams();
  const invite = params.get("invite") || "";
  const [mode, setMode] = useState<"login" | "signup" | "invite">(invite ? "invite" : "login");
  const [f, setF] = useState({ name: "", business: "", email: "", password: "" });
  const [inv, setInv] = useState<any>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    if (!invite) return;
    api.get(`/auth/invite/${invite}`).then((r) => { setInv(r.data); setF((x) => ({ ...x, email: r.data.email })); })
      .catch((e) => toast.error(errMsg(e)));
  }, [invite]);

  if (me) return <Navigate to="/" replace />;
  const set = (k: string) => (e: any) => setF({ ...f, [k]: e.target.value });

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      if (mode === "login") await api.post("/auth/login", { email: f.email, password: f.password });
      else if (mode === "signup") await api.post("/auth/signup", f);
      else await api.post("/auth/invite/accept", { token: invite, name: f.name, email: f.email, password: f.password });
      await refresh();
      nav("/");
    } catch (err) {
      toast.error(errMsg(err));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="grid min-h-screen lg:grid-cols-[1.1fr_1fr]">
      <div className="relative hidden overflow-hidden lg:block">
        <img src={HERO} alt="Garage bay" className="absolute inset-0 h-full w-full object-cover opacity-60" />
        <div className="absolute inset-0 bg-gradient-to-t from-[#0B0F17] via-[#0B0F17]/50 to-transparent" />
        <div className="absolute bottom-12 left-12 right-12">
          <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-white/15 bg-black/40 px-3 py-1 text-xs uppercase tracking-[0.18em] text-orange-300 backdrop-blur">Built for GTA garages</div>
          <h1 className="text-5xl font-extrabold leading-[1.02] tracking-tight">Fill every bay.<br />Text every customer.<br /><span className="text-orange-400">Stay CASL-clean.</span></h1>
        </div>
      </div>
      <div className="flex items-center justify-center px-5 py-12 rl-grain">
        <div className="w-full max-w-md rl-rise">
          <div className="mb-10 flex items-center gap-2">
            <div className="grid h-9 w-9 place-items-center rounded-lg bg-orange-500"><Wrench className="h-4 w-4 text-white" /></div>
            <span className="font-display text-2xl font-extrabold">RevLoop</span>
          </div>
          <h2 className="text-3xl font-extrabold tracking-tight" data-testid="auth-heading">
            {mode === "login" ? "Sign in to your shop" : mode === "signup" ? "Start your 14-day trial" : `Join ${inv?.tenant_name || "your team"}`}
          </h2>
          <p className="mt-2 text-sm text-slate-400">
            {mode === "invite" ? "Create your team login to access the shop calendar, customers and inbox." : "Bookings, reminders and reviews — from your phone."}
          </p>
          {params.get("expired") && <div className="mt-4 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3 text-sm text-amber-200" data-testid="session-expired-notice">Your session expired after inactivity. Please sign in again.</div>}
          <Button data-testid="google-login-btn" type="button" variant="outline" onClick={() => googleLogin(mode === "invite" ? invite : undefined)}
            className="mt-8 w-full border-white/15 bg-white/5 py-5 font-semibold hover:bg-white/10">
            Continue with Google
          </Button>
          <div className="my-6 flex items-center gap-3 text-xs text-slate-500"><div className="h-px flex-1 bg-white/10" />or with email<div className="h-px flex-1 bg-white/10" /></div>
          <form onSubmit={submit} className="space-y-4" data-testid="auth-form">
            {mode !== "login" && <Field id="name" label="Your name" value={f.name} onChange={set("name")} required />}
            {mode === "signup" && <Field id="business" label="Garage name" value={f.business} onChange={set("business")} required />}
            <Field id="email" label="Email" type="email" value={f.email} onChange={set("email")} required readOnly={mode === "invite"} />
            <Field id="password" label={mode === "login" ? "Password" : "Password (12+ characters)"} type="password" value={f.password} onChange={set("password")} required minLength={mode === "login" ? 1 : 12} />
            <Button data-testid="auth-submit-btn" disabled={busy} className="w-full bg-orange-500 py-5 font-semibold text-white hover:bg-orange-600">
              {busy ? "Please wait…" : mode === "login" ? "Sign in" : mode === "signup" ? "Create account" : "Join team"}
            </Button>
          </form>
          {mode === "login" && <Link to="/forgot-password" className="mt-3 block text-sm text-orange-300 hover:text-orange-200" data-testid="forgot-password-link">Forgot password?</Link>}
          {mode !== "invite" && (
            <button data-testid="auth-mode-toggle" onClick={() => setMode(mode === "login" ? "signup" : "login")} className="mt-6 text-sm text-slate-400 hover:text-orange-300">
              {mode === "login" ? "New garage? Create an account" : "Already have an account? Sign in"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
