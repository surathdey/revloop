import React, { useEffect } from "react";
import { Link, useLocation } from "react-router-dom";
import { ShieldX } from "lucide-react";
import { api } from "../lib/api";
import { Card } from "./common";

export default function AccessDenied() {
  const loc = useLocation();
  useEffect(() => { api.post("/auth/access-denied", { path: loc.pathname }).catch(() => null); }, [loc.pathname]);
  return (
    <div className="mx-auto max-w-md py-16 rl-rise">
      <Card className="space-y-3 text-center" data-testid="access-denied">
        <ShieldX className="mx-auto h-12 w-12 text-red-400" />
        <div className="font-mono-rl text-sm text-red-300">403</div>
        <h1 className="text-3xl font-extrabold">Access denied</h1>
        <p className="text-sm text-slate-400">Your role doesn't have permission to open <span className="font-mono-rl">{loc.pathname}</span>. This attempt has been logged.</p>
        <Link to="/" className="inline-block rounded-lg bg-orange-500 px-4 py-2 font-semibold text-white" data-testid="access-denied-home">Go to my workspace</Link>
      </Card>
    </div>
  );
}
