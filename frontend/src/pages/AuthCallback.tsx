import React, { useEffect, useRef } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import { api, errMsg } from "../lib/api";
import { useAuth } from "../lib/auth";

export default function AuthCallback() {
  const location = useLocation();
  const nav = useNavigate();
  const { refresh } = useAuth();
  const done = useRef(false);
  useEffect(() => {
    if (done.current) return;
    done.current = true;
    const sid = new URLSearchParams(location.hash.slice(1)).get("session_id") || "";
    const invite = localStorage.getItem("rl_invite") || "";
    (async () => {
      try {
        await api.post("/auth/google", { session_id: sid, invite_token: invite });
        localStorage.removeItem("rl_invite");
        await refresh();
        nav("/", { replace: true });
      } catch (e) {
        toast.error(errMsg(e));
        nav("/login", { replace: true });
      }
    })();
  }, [location.hash, nav, refresh]);
  return null;
}
