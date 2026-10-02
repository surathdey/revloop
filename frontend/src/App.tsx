import React from "react";
import { BrowserRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { Toaster } from "@/components/ui/sonner";
import { AuthProvider, useAuth, isOwner } from "./lib/auth";
import { Spinner } from "./components/common";
import AppShell from "./components/AppShell";
import Login from "./pages/Login";
import AuthCallback from "./pages/AuthCallback";
import Dashboard from "./pages/Dashboard";
import Customers from "./pages/Customers";
import CustomerDetail from "./pages/CustomerDetail";
import Calendar from "./pages/Calendar";
import Conversations from "./pages/Conversations";
import Automations from "./pages/Automations";
import Settings from "./pages/Settings";
import Admin from "./pages/Admin";
import PublicBooking from "./pages/PublicBooking";
import CancelBooking from "./pages/CancelBooking";
import Business from "./pages/Business";
import Sales from "./pages/Sales";
import PaymentResult from "./pages/PaymentResult";
import AccessDenied from "./components/AccessDenied";
import { ForgotPassword, ResetPassword } from "./pages/PasswordReset";

function Protected({ children, need }: { children: React.ReactNode; need?: "owner" | "superadmin" }) {
  const { me } = useAuth();
  if (me === null) return <div className="p-8"><Spinner /></div>;
  if (me === false) return <Navigate to="/login" replace />;
  if (need === "owner" && !isOwner(me)) return <AppShell><AccessDenied /></AppShell>;
  if (need === "superadmin" && me.user.role !== "superadmin") return <AppShell><AccessDenied /></AppShell>;
  return <AppShell>{children}</AppShell>;
}

function Home() {
  const { me } = useAuth();
  if (me && me.user.role === "superadmin" && !me.impersonating) return <Navigate to="/admin" replace />;
  if (me && me.user.role === "staff") return <Navigate to="/calendar" replace />;
  return <Dashboard />;
}

function AppRouter() {
  const location = useLocation();
  if (location.hash?.includes("session_id=")) return <AuthCallback />;
  return (
    <Routes>
      <Route path="/login" element={<Login />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/templates" element={<Protected need="owner"><Automations /></Protected>} />
      <Route path="/billing" element={<Protected need="owner"><Navigate to="/settings?tab=billing" replace /></Protected>} />
      <Route path="/book/:slug" element={<PublicBooking />} />
      <Route path="/booking/:token" element={<CancelBooking />} />
      <Route path="/business/:slug" element={<Business />} />
      <Route path="/" element={<Protected><Home /></Protected>} />
      <Route path="/customers" element={<Protected><Customers /></Protected>} />
      <Route path="/customers/:id" element={<Protected><CustomerDetail /></Protected>} />
      <Route path="/calendar" element={<Protected><Calendar /></Protected>} />
      <Route path="/inbox" element={<Protected><Conversations /></Protected>} />
      <Route path="/automations" element={<Protected need="owner"><Automations /></Protected>} />
      <Route path="/settings" element={<Protected need="owner"><Settings /></Protected>} />
      <Route path="/admin" element={<Protected need="superadmin"><Admin /></Protected>} />
      <Route path="/sales" element={<Protected need="superadmin"><Sales /></Protected>} />
      <Route path="/payment/success" element={<PaymentResult />} />
      <Route path="/payment/cancel" element={<PaymentResult />} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  );
}

export default function App() {
  return (
    <BrowserRouter>
      <AuthProvider>
        <AppRouter />
        <Toaster position="top-center" theme="dark" richColors />
      </AuthProvider>
    </BrowserRouter>
  );
}
