import re
import math
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from core import AppError

TIMEZONES = ["America/Toronto", "America/Vancouver", "America/Edmonton", "America/Winnipeg", "America/Halifax", "America/St_Johns"]


def require_owner(role: str):
    if role not in ("owner", "superadmin"):
        raise AppError("Only the owner can access this area.", 403)


def normalize_phone(value: str) -> str:
    n = re.sub(r"\D", "", value or "")
    if len(n) == 10:
        n = "1" + n
    if not re.fullmatch(r"1[2-9]\d{9}", n):
        raise AppError("Enter a Canadian or US phone number with 10 digits.")
    return "+" + n


def can_send(c: dict, at: datetime) -> bool:
    if c["consent_status"] == "express":
        return True
    exp = c.get("consent_expires_at")
    return c["consent_status"] == "implied" and bool(exp) and exp > at


def next_allowed(at: datetime, tz: str, start: int, end: int) -> datetime:
    z = ZoneInfo(tz)
    local = at.astimezone(z)
    if start <= local.hour < end:
        return at
    d = local.date()
    if local.hour >= end:
        d += timedelta(days=1)
    return datetime(d.year, d.month, d.day, start, tzinfo=z).astimezone(timezone.utc)


def review_within_cap(last, at: datetime) -> bool:
    return bool(last) and (at - last).total_seconds() < 30 * 86400


_GSM = set("@£$¥èéùìòÇ\nØø\rÅåΔ_ΦΓΛΩΠΨΣΘΞÆæßÉ !\"#¤%&'()*+,-./0123456789:;<=>?¡ABCDEFGHIJKLMNOPQRSTUVWXYZÄÖÑÜ§¿abcdefghijklmnopqrstuvwxyzäöñüà")
_EXT = set("^{}\\[~]|€")


def segments(s: str) -> int:
    n = 0
    for ch in s:
        if ch in _GSM:
            n += 1
        elif ch in _EXT:
            n += 2
        else:
            return math.ceil(len(s) / (70 if len(s) <= 70 else 67))
    return max(1, math.ceil(n / (160 if n <= 160 else 153)))


DEFAULTS = {
    "serviceCompleted": "Hi {first_name}, your {service_name} is complete. Thank you for choosing {business_name}!",
    "review": "Hi {first_name}, thanks for visiting! How was your {service_name}? Leave a review: {review_link}",
    "followup": "Hi {first_name}, a quick reminder: we would appreciate your feedback. {review_link}",
    "confirmation": "Hi {first_name}, your {service_name} is booked for {appointment_date} at {appointment_time}. Reply YES to confirm. Cancel: {booking_link}",
    "reminder24": "Your {service_name} is tomorrow at {appointment_time}. Reply YES to confirm. Cancel: {booking_link}",
    "reminder2": "See you in 2 hours for {service_name} at {appointment_time}. Cancel: {booking_link}",
    "serviceDue": "Hi {first_name}, your {service_name} is due soon. Book a visit: {booking_link}",
    "optout": "You are unsubscribed. No more automated messages will be sent.",
}
TEMPLATE_KINDS = list(DEFAULTS.keys())
TEMPLATE_VARS = ["first_name", "business_name", "service_name", "appointment_date", "appointment_time", "booking_link", "review_link"]

CONSENT_TEXT = ("I agree to receive appointment reminders, service reminders and review requests by text from this business. "
                "Consent is optional and can be withdrawn at any time by replying STOP. Message and data rates may apply.")

STOP_WORDS = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
