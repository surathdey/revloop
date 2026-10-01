import axios from "axios";

export const API = `${process.env.REACT_APP_BACKEND_URL}/api`;
export const api = axios.create({ baseURL: API, withCredentials: true });

export function errMsg(e: any): string {
  const d = e?.response?.data?.detail;
  if (d == null) return e?.message || "Something went wrong. Please try again.";
  if (typeof d === "string") return d;
  if (Array.isArray(d)) return d.map((x) => (x && typeof x.msg === "string" ? x.msg : JSON.stringify(x))).join(" ");
  return String(d);
}

export const CONSENT_TEXT =
  "I agree to receive appointment reminders, service reminders and review requests by text from this business. Consent is optional and can be withdrawn at any time by replying STOP. Message and data rates may apply.";

export const money = (cents: number) => `$${((cents || 0) / 100).toFixed(2)}`;

export function fmt(iso: string, tz: string, opts: Intl.DateTimeFormatOptions = { dateStyle: "medium", timeStyle: "short" }) {
  if (!iso) return "—";
  return new Intl.DateTimeFormat("en-CA", { timeZone: tz || "America/Toronto", ...opts }).format(new Date(iso));
}

export function tzParts(iso: string, tz: string) {
  const p = new Intl.DateTimeFormat("en-CA", {
    timeZone: tz, year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit", hourCycle: "h23",
  }).formatToParts(new Date(iso));
  const g = (t: string) => p.find((x) => x.type === t)?.value || "0";
  return { date: `${g("year")}-${g("month")}-${g("day")}`, h: Number(g("hour")), m: Number(g("minute")) };
}

export function todayIn(tz: string) {
  return tzParts(new Date().toISOString(), tz).date;
}

export function addDays(date: string, n: number) {
  const d = new Date(date + "T12:00:00Z");
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

const GSM = new Set(
  "@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà",
);
const EXT = new Set("^{}\\[~]|€");
export function segments(s: string) {
  let n = 0;
  for (const c of Array.from(s)) {
    if (GSM.has(c)) n++;
    else if (EXT.has(c)) n += 2;
    else return { count: Math.ceil(s.length / (s.length <= 70 ? 70 : 67)), unicode: true, chars: s.length };
  }
  return { count: Math.max(1, Math.ceil(n / (n <= 160 ? 160 : 153))), unicode: false, chars: n };
}
