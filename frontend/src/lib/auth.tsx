import React, { createContext, useCallback, useContext, useEffect, useState } from "react";
import { api } from "./api";

export type Me = {
  user: { id: string; name: string; email: string; role: "superadmin" | "owner" | "staff" };
  tenant: any;
  impersonating: boolean;
  sms_mode: string;
};

type AuthValue = { me: Me | null | false; refresh: () => Promise<void>; logout: () => Promise<void> };
const AuthContext = createContext<AuthValue>({ me: null, refresh: async () => {}, logout: async () => {} });

export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [me, setMe] = useState<Me | null | false>(null);
  const refresh = useCallback(async () => {
    try {
      const { data } = await api.get("/auth/me");
      setMe(data);
    } catch {
      setMe(false);
    }
  }, []);
  const logout = useCallback(async () => {
    await api.post("/auth/logout").catch(() => null);
    setMe(false);
  }, []);
  useEffect(() => {
    // Returning from Google: AuthCallback exchanges session_id first.
    if (window.location.hash?.includes("session_id=")) return;
    refresh();
  }, [refresh]);
  return <AuthContext.Provider value={{ me, refresh, logout }}>{children}</AuthContext.Provider>;
}

export const useAuth = () => useContext(AuthContext);

export const isOwner = (me: Me) => me.user.role === "owner" || me.user.role === "superadmin";
