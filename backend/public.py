import re
import json
import hashlib
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Request, BackgroundTasks
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from core import db, now, new_id, AppError, audit, rate_limit, Scoped
from policy import normalize_phone, CONSENT_TEXT
from appointments import book_appointment, BookIn
from messaging import suppress_queued, process_queue

router = APIRouter(prefix="/api")
OPEN = ["scheduled", "confirmed"]


async def _tenant(slug):
    t = await db.tenants.find_one({"slug": slug, "status": "active", "is_platform": {"$ne": True}}, {"_id": 0})
    if not t:
        raise AppError("Garage not found.", 404)
    return t


@router.get("/public/garage/{slug}")
async def garage(slug: str):
    t = await _tenant(slug)
    services = await Scoped(t["id"]).find("services", sort=[("name", 1)], proj={"tenant_id": 0})
    return {"name": t["name"], "slug": t["slug"], "address": t["address"], "phone": t["phone"], "logo": t["logo"],
            "timezone": t["timezone"], "open_hour": t["open_hour"], "close_hour": t["close_hour"],
            "services": services, "consent_text": CONSENT_TEXT}


async def available_slots(slug, service_id, date):
    t = await _tenant(slug)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date or ""):
        raise AppError("Invalid date")
    s = Scoped(t["id"])
    service = await s.one("services", {"id": service_id})
    if not service:
        raise AppError("Service not found.", 404)
    users = await s.find("users", {"role": {"$in": ["owner", "staff"]}}, sort=[("created_at", 1)])
    z = ZoneInfo(t["timezone"])
    y, mo, d = map(int, date.split("-"))
    day_start = datetime(y, mo, d, tzinfo=z).astimezone(timezone.utc)
    busy = await s.find("appointments", {"status": {"$in": OPEN}, "start": {"$gte": day_start - timedelta(hours=12), "$lt": day_start + timedelta(hours=25)}})
    slots = []
    m = t["open_hour"] * 60
    while m + service["duration"] <= t["close_hour"] * 60:
        at = datetime(y, mo, d, m // 60, m % 60, tzinfo=z).astimezone(timezone.utc)
        end = at + timedelta(minutes=service["duration"])
        m += 30
        if at < now() + timedelta(minutes=60) or at > now() + timedelta(days=90):
            continue
        overlap = [a for a in busy if a["start"] < end and a["end"] > at]
        for bay in range(1, t["bays"] + 1):
            if any(a["bay"] == bay for a in overlap):
                continue
            staff = next((u for u in users if not any(a["staff_id"] == u["id"] for a in overlap)), None)
            if staff:
                slots.append({"time": at.isoformat().replace("+00:00", "Z"), "label": at.astimezone(z).strftime("%-I:%M %p"),
                              "bay": bay, "staff_id": staff["id"]})
                break
    return t, service, slots


@router.get("/public/slots")
async def slots(slug: str, service: str, date: str):
    _, _, sl = await available_slots(slug, service, date)
    return {"slots": [{"time": x["time"], "label": x["label"]} for x in sl]}


class PublicBookIn(BaseModel):
    slug: str
    service_id: str
    date: str
    start: str
    name: str = Field(min_length=2, max_length=100)
    phone: str
    make: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=50)
    year: int = Field(ge=1900, le=2100)
    plate: str = Field(min_length=1, max_length=20)
    consent: bool = False
    website: str = ""


@router.post("/public/book")
async def public_book(d: PublicBookIn, request: Request, bg: BackgroundTasks):
    if d.website:
        raise AppError("Unable to book.")
    phone = normalize_phone(d.phone)
    await rate_limit("public-ip:" + (request.client.host if request.client else "x"), 20, 3600)
    await rate_limit("public:" + hashlib.sha256(phone.encode()).hexdigest(), 4, 3600)
    await rate_limit("shop-public:" + d.slug, 80, 3600)
    t, service, sl = await available_slots(d.slug, d.service_id, d.date)
    slot = next((x for x in sl if x["time"] == d.start), None)
    if not slot:
        raise AppError("That time is no longer available. Choose another.", 409)
    s = Scoped(t["id"])
    c = await s.one("contacts", {"phone": phone})
    evidence = json.dumps({"text": CONSENT_TEXT, "business": t["name"], "address": t["address"], "accepted": d.consent,
                           "submission": new_id(), "ip_hash": hashlib.sha256((request.client.host if request.client else "").encode()).hexdigest()[:16]})
    if not c:
        c = await s.insert("contacts", {"name": d.name, "phone": phone, "email": "", "notes": "", "tags": "online booking",
                                        "consent_status": "express" if d.consent else "none", "consent_source": "Public booking form",
                                        "consent_at": now() if d.consent else None, "consent_expires_at": None, "last_review_at": None})
        await s.insert("consent_events", {"contact_id": c["id"], "status": c["consent_status"], "source": "Public booking form",
                                          "evidence": evidence, "expires_at": None})
    elif d.consent and c["consent_status"] in ("none", "implied"):
        # Fresh, unbundled opt-in from the customer. An explicit opt-out is never overridden silently by a form.
        await s.update("contacts", {"id": c["id"]}, {"$set": {"consent_status": "express", "consent_source": "Public booking form", "consent_at": now(), "consent_expires_at": None}})
        await s.insert("consent_events", {"contact_id": c["id"], "status": "express", "source": "Public booking form", "evidence": evidence, "expires_at": None})
    v = await s.one("vehicles", {"contact_id": c["id"], "plate": d.plate.upper()})
    if not v:
        v = await s.insert("vehicles", {"contact_id": c["id"], "make": d.make, "model": d.model, "year": d.year, "plate": d.plate.upper(), "km": 0, "vin": ""})
    await book_appointment(t, "customer", BookIn(contact_id=c["id"], vehicle_id=v["id"], service_id=service["id"], start=d.start,
                                                bay=slot["bay"], staff_id=slot["staff_id"], notes="Public booking"))
    bg.add_task(process_queue, None, t["id"])
    return {"ok": True, "when": slot["label"], "service": service["name"]}


@router.get("/public/booking/{token}")
async def booking_info(token: str):
    a = await db.appointments.find_one({"token": token}, {"_id": 0})
    if not a:
        raise AppError("Booking not found.", 404)
    t = await db.tenants.find_one({"id": a["tenant_id"]})
    svc = await db.services.find_one({"id": a["service_id"], "tenant_id": t["id"]})
    z = ZoneInfo(t["timezone"])
    return {"status": a["status"], "service": svc["name"] if svc else "", "garage": t["name"], "garage_phone": t["phone"],
            "when": a["start"].astimezone(z).strftime("%A, %B %-d at %-I:%M %p"), "slug": t["slug"]}


@router.post("/public/booking/{token}/cancel")
async def booking_cancel(token: str, request: Request):
    await rate_limit("cancel-ip:" + (request.client.host if request.client else "x"), 30, 3600)
    a = await db.appointments.find_one({"token": token})
    if not a:
        raise AppError("Booking not found.", 404)
    if a["status"] not in OPEN:
        raise AppError("This appointment is already closed.")
    await db.appointments.update_one({"id": a["id"], "tenant_id": a["tenant_id"]}, {"$set": {"status": "cancelled"}})
    await suppress_queued(a["tenant_id"], {"appointment_id": a["id"]}, "Cancelled through booking link")
    await audit(a["tenant_id"], "customer", "appointment.cancelled", a["id"], {"via": "booking link"})
    return {"ok": True}


@router.get("/public/business/{slug}")
async def business(slug: str):
    t = await _tenant(slug)
    return {"name": t["name"], "address": t["address"], "phone": t["phone"], "logo": t["logo"], "slug": t["slug"],
            "open_hour": t["open_hour"], "close_hour": t["close_hour"]}


@router.get("/r/{token}")
async def review_redirect(token: str):
    m = await db.messages.find_one({"token": token, "kind": {"$in": ["review", "followup"]}})
    if not m:
        raise AppError("Link not found.", 404)
    t = await db.tenants.find_one({"id": m["tenant_id"]})
    if not m.get("clicked_at"):
        await db.messages.update_one({"id": m["id"]}, {"$set": {"clicked_at": now()}})
        if m["kind"] == "review":
            await suppress_queued(t["id"], {"appointment_id": m.get("appointment_id"), "kind": "followup"}, "Review link clicked")
        await audit(t["id"], "customer", "review.clicked", m["id"], {})
    if not t.get("review_link"):
        raise AppError("Review link not configured.", 404)
    return RedirectResponse(t["review_link"], status_code=302)
