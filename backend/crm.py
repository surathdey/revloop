import csv
import io
import re
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, Literal
from pymongo.errors import DuplicateKeyError
from core import db, now, new_id, new_token, sha, origin, AppError, audit, platform_config
from policy import normalize_phone, CONSENT_TEXT, TIMEZONES
from auth import Ctx, get_ctx, owner_ctx, tenant_view
from messaging import suppress_queued
from emailer import send_email, action_email

router = APIRouter(prefix="/api")


def contact_doc(name, phone, email="", notes="", tags="", consent=False, source=""):
    return {"name": name, "phone": phone, "email": email, "notes": notes, "tags": tags,
            "consent_status": "express" if consent else "none", "consent_source": source if consent else "",
            "consent_at": now() if consent else None, "consent_expires_at": None, "last_review_at": None}


class QuickAddIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    phone: str
    email: str = Field(default="", max_length=200)
    notes: str = Field(default="", max_length=3000)
    tags: str = Field(default="", max_length=200)
    consent: bool = False
    make: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=50)
    year: int = Field(ge=1900, le=2100)
    plate: str = Field(min_length=1, max_length=20)
    km: int = Field(default=0, ge=0, le=3000000)
    vin: str = Field(default="", max_length=17)


@router.get("/contacts")
async def list_contacts(q: str = "", ctx: Ctx = Depends(get_ctx)):
    filt = {}
    if q.strip():
        rx = {"$regex": re.escape(q.strip()), "$options": "i"}
        digits = re.sub(r"\D", "", q)
        plate_ids = [v["contact_id"] for v in await ctx.s.find("vehicles", {"$or": [{"plate": rx}, {"make": rx}, {"model": rx}, {"vin": rx}]}, proj={"contact_id": 1})]
        ors = [{"name": rx}, {"email": rx}, {"tags": rx}, {"notes": rx}, {"id": {"$in": plate_ids}}]
        if len(digits) >= 3:
            ors.append({"phone": {"$regex": digits}})
        filt = {"$or": ors}
    contacts = await ctx.s.find("contacts", filt, sort=[("name", 1)], limit=10000)
    ids = [c["id"] for c in contacts]
    vehicles = await ctx.s.find("vehicles", {"contact_id": {"$in": ids}})
    by = {}
    for v in vehicles:
        by.setdefault(v["contact_id"], []).append(v)
    for c in contacts:
        c["vehicles"] = by.get(c["id"], [])
    return contacts


@router.post("/contacts")
async def add_contact(d: QuickAddIn, ctx: Ctx = Depends(get_ctx)):
    phone = normalize_phone(d.phone)
    if await ctx.s.one("contacts", {"phone": phone}):
        raise AppError("This phone number already belongs to a customer.", 409)
    try:
        c = await ctx.s.insert("contacts", contact_doc(d.name, phone, d.email, d.notes, d.tags, d.consent, "staff attestation"))
    except DuplicateKeyError:
        raise AppError("This phone number already belongs to a customer.", 409)
    await ctx.s.insert("vehicles", {"contact_id": c["id"], "make": d.make, "model": d.model, "year": d.year,
                                    "plate": d.plate.upper(), "km": d.km, "vin": d.vin})
    await ctx.s.insert("consent_events", {"contact_id": c["id"], "status": c["consent_status"], "source": "staff attestation", "expires_at": None,
                                          "evidence": json.dumps({"actor": ctx.user["id"], "text": CONSENT_TEXT, "accepted": d.consent})})
    await audit(ctx.tenant_id, ctx.user["id"], "customer.created", c["id"], {"name": c["name"]})
    if d.consent:
        await audit(ctx.tenant_id, ctx.user["id"], "consent.opted-in", c["id"], {"source": "staff attestation", "status": "express"})
    return c


@router.get("/contacts/{cid}")
async def get_contact(cid: str, ctx: Ctx = Depends(get_ctx)):
    s = ctx.s
    c = await s.get("contacts", cid, "Customer not found.")
    vehicles = await s.find("vehicles", {"contact_id": cid})
    vids = [v["id"] for v in vehicles]
    records = await s.find("service_records", {"vehicle_id": {"$in": vids}}, sort=[("date", -1)])
    rules = await s.find("reminder_rules", {"vehicle_id": {"$in": vids}}, sort=[("due_at", 1)])
    for v in vehicles:
        v["records"] = [r for r in records if r["vehicle_id"] == v["id"]]
        v["rules"] = [r for r in rules if r["vehicle_id"] == v["id"]]
    consents = await s.find("consent_events", {"contact_id": cid}, sort=[("created_at", -1)])
    msgs = await s.find("messages", {"contact_id": cid}, sort=[("created_at", -1)], limit=200, proj={"consent_snapshot": 0, "token": 0})
    return {**c, "vehicles": vehicles, "consents": consents, "messages": msgs}


class ContactUpdateIn(BaseModel):
    name: Optional[str] = Field(default=None, min_length=2, max_length=100)
    phone: Optional[str] = None
    email: Optional[str] = Field(default=None, max_length=200)
    notes: Optional[str] = Field(default=None, max_length=3000)
    tags: Optional[str] = Field(default=None, max_length=200)


@router.patch("/contacts/{cid}")
async def update_contact(cid: str, d: ContactUpdateIn, ctx: Ctx = Depends(get_ctx)):
    c = await ctx.s.get("contacts", cid, "Customer not found.")
    upd = {k: v for k, v in d.model_dump().items() if v is not None}
    if d.email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", d.email):
        raise AppError("Enter a valid email address.")
    if d.phone is not None:
        upd["phone"] = normalize_phone(d.phone)
        if upd["phone"] != c["phone"] and await ctx.s.one("contacts", {"phone": upd["phone"]}):
            raise AppError("This phone number already belongs to another customer.", 409)
    try:
        await ctx.s.update("contacts", {"id": cid}, {"$set": upd})
    except DuplicateKeyError:
        raise AppError("This phone number already belongs to another customer.", 409)
    if upd.get("phone") and upd["phone"] != c["phone"]:
        await db.messages.update_many({"tenant_id": ctx.tenant_id, "contact_id": cid, "status": "queued"}, {"$set": {"recipient": upd["phone"]}})
    await audit(ctx.tenant_id, ctx.user["id"], "customer.updated", cid, {**upd, "previous_phone": c["phone"]})
    return {"ok": True}


class ConsentIn(BaseModel):
    status: Literal["express", "implied", "none", "opted-out"]
    source: str = Field(min_length=3)
    evidence: str = Field(min_length=5)
    expires_at: Optional[str] = None


@router.post("/contacts/{cid}/consent")
async def consent_change(cid: str, d: ConsentIn, ctx: Ctx = Depends(owner_ctx)):
    exp = None
    if d.status == "implied":
        try:
            exp = datetime.fromisoformat((d.expires_at or "").replace("Z", "+00:00"))
            exp = exp if exp.tzinfo else exp.replace(tzinfo=timezone.utc)
        except ValueError:
            exp = None
        if not exp or exp <= now():
            raise AppError("Implied consent needs a future expiry date.")
    await ctx.s.get("contacts", cid, "Customer not found.")
    await ctx.s.update("contacts", {"id": cid}, {"$set": {"consent_status": d.status, "consent_source": d.source,
                                                         "consent_at": now(), "consent_expires_at": exp}})
    await ctx.s.insert("consent_events", {"contact_id": cid, "status": d.status, "source": d.source, "evidence": d.evidence, "expires_at": exp})
    if d.status in ("opted-out", "none"):
        await suppress_queued(ctx.tenant_id, {"contact_id": cid}, "Consent withdrawn")
    await audit(ctx.tenant_id, ctx.user["id"], "consent.changed", cid, d.model_dump())
    return {"ok": True}


class ImportIn(BaseModel):
    csv: str


@router.post("/contacts/import")
async def import_contacts(d: ImportIn, ctx: Ctx = Depends(get_ctx)):
    if len(d.csv) > 2_000_000:
        raise AppError("CSV must be under 2 MB.")
    reader = csv.DictReader(io.StringIO(d.csv.lstrip("\ufeff")))
    if not reader.fieldnames or "phone" not in [h.strip().lower() for h in reader.fieldnames]:
        raise AppError("CSV needs a header row with at least 'name' and 'phone' columns.")
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in reader]
    if len(rows) > 5000:
        raise AppError("Import up to 5,000 customers at a time.")
    existing = {c["phone"] for c in await ctx.s.find("contacts", proj={"phone": 1})}
    res = {"created": 0, "duplicates": [], "errors": []}
    contacts, vehicles, consents = [], [], []
    for i, r in enumerate(rows):
        row = i + 2
        try:
            name = r.get("name", "")
            if not 2 <= len(name) <= 100:
                raise AppError("Name must be 2-100 characters.")
            phone = normalize_phone(r.get("phone", ""))
        except AppError as e:
            res["errors"].append({"row": row, "message": e.detail})
            continue
        if phone in existing:
            res["duplicates"].append({"row": row, "phone": phone, "name": name})
            continue
        existing.add(phone)
        c = {"id": new_id(), "tenant_id": ctx.tenant_id, "created_at": now(),
             **contact_doc(name, phone, r.get("email", ""), r.get("notes", ""), r.get("tags", ""))}
        c["consent_source"] = "CSV import: consent not established"
        contacts.append(c)
        consents.append({"id": new_id(), "tenant_id": ctx.tenant_id, "contact_id": c["id"], "status": "none", "source": "CSV import",
                         "evidence": "Imported records require separate consent verification.", "expires_at": None, "created_at": now()})
        if r.get("make") or r.get("plate") or r.get("year"):
            yr = r.get("year", "")
            if not (yr.isdigit() and 1900 <= int(yr) <= 2100) or not r.get("make") or not r.get("plate"):
                existing.discard(phone)
                contacts.pop()
                consents.pop()
                res["errors"].append({"row": row, "message": f"Vehicle needs make, plate and a valid year (1900-2100); got year '{yr or 'blank'}'. Row not imported."})
                continue
            year = int(yr)
            vehicles.append({"id": new_id(), "tenant_id": ctx.tenant_id, "contact_id": c["id"], "make": r["make"][:40],
                             "model": r.get("model", "")[:50], "year": year, "plate": r["plate"].upper()[:20],
                             "km": int(r["km"]) if r.get("km", "").isdigit() else 0, "vin": r.get("vin", "")[:17], "created_at": now()})
    for coll, docs in (("contacts", contacts), ("consent_events", consents), ("vehicles", vehicles)):
        if docs:
            await db[coll].insert_many(docs)
    res["created"] = len(contacts)
    await audit(ctx.tenant_id, ctx.user["id"], "customers.imported", "csv",
                {"created": res["created"], "duplicates": len(res["duplicates"]), "errors": len(res["errors"])})
    return res


class VehicleIn(BaseModel):
    contact_id: str
    make: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=50)
    year: int = Field(ge=1900, le=2100)
    plate: str = Field(min_length=1, max_length=20)
    km: int = Field(default=0, ge=0)
    vin: str = Field(default="", max_length=17)


@router.post("/vehicles")
async def add_vehicle(d: VehicleIn, ctx: Ctx = Depends(get_ctx)):
    await ctx.s.get("contacts", d.contact_id, "Customer not found.")
    v = await ctx.s.insert("vehicles", {**d.model_dump(), "plate": d.plate.upper()})
    await audit(ctx.tenant_id, ctx.user["id"], "vehicle.created", v["id"], {"plate": v["plate"]})
    return v


class VehicleUpdateIn(BaseModel):
    make: str = Field(min_length=1, max_length=40)
    model: str = Field(min_length=1, max_length=50)
    year: int = Field(ge=1900, le=2100)
    plate: str = Field(min_length=1, max_length=20)
    km: int = Field(ge=0, le=3000000)
    vin: str = Field(default="", max_length=17)


@router.patch("/vehicles/{vid}")
async def update_vehicle(vid: str, d: VehicleUpdateIn, ctx: Ctx = Depends(get_ctx)):
    v = await ctx.s.get("vehicles", vid, "Vehicle not found.")
    upd = {**d.model_dump(), "plate": d.plate.upper()}
    await ctx.s.update("vehicles", {"id": vid}, {"$set": upd})
    await audit(ctx.tenant_id, ctx.user["id"], "vehicle.updated", vid, {**upd, "previous_km": v["km"]})
    return {**v, **upd}


class ReminderIn(BaseModel):
    operation: Literal["create", "done", "snooze"]
    id: Optional[str] = None
    vehicle_id: Optional[str] = None
    name: Optional[str] = Field(default=None, min_length=2)
    interval_months: int = Field(default=6, ge=1, le=60)
    interval_km: int = Field(default=8000, ge=1)
    due_at: Optional[str] = None
    due_km: Optional[int] = Field(default=None, ge=0)


@router.get("/reminders")
async def list_reminders(ctx: Ctx = Depends(get_ctx)):
    rules = await ctx.s.find("reminder_rules", sort=[("due_at", 1)], limit=1000)
    vehicles = {v["id"]: v for v in await ctx.s.find("vehicles", {"id": {"$in": [r["vehicle_id"] for r in rules]}})}
    contacts = {c["id"]: c for c in await ctx.s.find("contacts", {"id": {"$in": [v["contact_id"] for v in vehicles.values()]}}, proj={"id": 1, "name": 1})}
    for r in rules:
        v = vehicles.get(r["vehicle_id"], {})
        r["vehicle"], r["contact"] = v, contacts.get(v.get("contact_id"), {})
    return rules


@router.post("/reminders")
async def reminder_action(d: ReminderIn, ctx: Ctx = Depends(get_ctx)):
    s = ctx.s
    if d.operation == "create":
        v = await s.get("vehicles", d.vehicle_id or "", "Vehicle not found.")
        try:
            due = datetime.fromisoformat(d.due_at or "")
        except ValueError:
            due = None
        if not d.name or not due:
            raise AppError("Enter the service name and due date.")
        due = due if due.tzinfo else due.replace(tzinfo=ZoneInfo(ctx.tenant["timezone"]))
        return await s.insert("reminder_rules", {"vehicle_id": v["id"], "name": d.name, "interval_months": d.interval_months,
                                                 "interval_km": d.interval_km, "due_at": due.astimezone(timezone.utc),
                                                 "due_km": d.due_km if d.due_km is not None else v["km"] + d.interval_km,
                                                 "snoozed_until": None, "last_queued_at": None})
    rule = await s.get("reminder_rules", d.id or "", "Reminder not found.")
    v = await s.get("vehicles", rule["vehicle_id"])
    await db.messages.update_many({"tenant_id": ctx.tenant_id, "dedupe_key": {"$regex": f"^rule:{rule['id']}:"}, "status": "queued"},
                                  {"$set": {"status": "suppressed", "error": "Reminder updated"}})
    upd = {"snoozed_until": now() + timedelta(days=30)} if d.operation == "snooze" else {
        "due_at": now() + relativedelta(months=rule["interval_months"]), "due_km": v["km"] + rule["interval_km"],
        "snoozed_until": None, "last_queued_at": None}
    await s.update("reminder_rules", {"id": rule["id"]}, {"$set": upd})
    await audit(ctx.tenant_id, ctx.user["id"], f"reminder.{d.operation}", rule["id"], {})
    return {"ok": True}


@router.get("/services")
async def list_services(ctx: Ctx = Depends(get_ctx)):
    return await ctx.s.find("services", sort=[("name", 1)])


class ServiceIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    duration: int = Field(ge=15, le=480)
    price: int = Field(ge=0, le=1000000)


@router.post("/services")
async def add_service(d: ServiceIn, ctx: Ctx = Depends(owner_ctx)):
    sv = await ctx.s.insert("services", d.model_dump())
    await audit(ctx.tenant_id, ctx.user["id"], "service.created", sv["id"], d.model_dump())
    return sv


@router.get("/team")
async def team(ctx: Ctx = Depends(get_ctx)):
    users = await ctx.s.find("users", {"role": {"$in": ["owner", "staff"]}}, proj={"id": 1, "name": 1, "role": 1, "email": 1})
    invites = []
    if ctx.role != "staff":
        invites = await ctx.s.find("invites", {"accepted_at": None, "expires_at": {"$gt": now()}}, proj={"id": 1, "email": 1, "role": 1, "expires_at": 1})
    return {"users": users, "invites": invites}


class InviteIn(BaseModel):
    email: EmailStr
    role: Literal["staff", "owner"] = "staff"


@router.post("/team/invite")
async def invite(d: InviteIn, ctx: Ctx = Depends(owner_ctx)):
    email = d.email.lower()
    if await db.users.find_one({"email": email}):
        raise AppError("A user with this email already exists.", 409)
    raw = new_token() + new_token()
    await ctx.s.insert("invites", {"email": email, "role": d.role, "token_hash": sha(raw), "expires_at": now() + timedelta(days=7), "accepted_at": None})
    await audit(ctx.tenant_id, ctx.user["id"], "invite.created", email, {"role": d.role})
    url = f"{origin()}/login?invite={raw}"
    await send_email(to=email, subject=f"You're invited to {ctx.tenant['name']} on RevLoop", html=action_email(
        email.split("@")[0], f"{ctx.user['name']} invited you to join {ctx.tenant['name']} on RevLoop as {d.role}.",
        "Accept invitation", url, "This one-time link expires in 7 days."))
    return {"url": url, "sent": True}


class SettingsIn(BaseModel):
    name: str = Field(min_length=2, max_length=100)
    address: str = Field(default="", max_length=200)
    phone: str = Field(default="", max_length=30)
    review_link: str = Field(default="", max_length=500)
    timezone: str
    quiet_start: int = Field(ge=9, le=19)
    quiet_end: int = Field(ge=10, le=20)
    review_delay: int = Field(ge=0, le=10080)
    open_hour: int = Field(ge=0, le=23)
    close_hour: int = Field(ge=1, le=24)
    bays: int = Field(ge=1, le=30)
    slot_capacity: int = Field(default=1, ge=1, le=30)
    logo: str = Field(default="", max_length=1000)


REVIEW_HOSTS = ("g.page", "search.google.com", "maps.google.com", "www.google.com", "maps.app.goo.gl", "g.co")


@router.put("/settings")
async def save_settings(d: SettingsIn, ctx: Ctx = Depends(owner_ctx)):
    if d.timezone not in TIMEZONES:
        raise AppError("Choose a Canadian time zone.")
    if d.review_link and not re.match(r"^https://(" + "|".join(re.escape(h) for h in REVIEW_HOSTS) + r")/", d.review_link):
        raise AppError("Use an HTTPS Google review link.")
    if d.logo and not d.logo.startswith("https://"):
        raise AppError("Use an HTTPS logo URL.")
    if d.quiet_start >= d.quiet_end or d.open_hour >= d.close_hour:
        raise AppError("Opening time must be before closing time.")
    await db.tenants.update_one({"id": ctx.tenant_id}, {"$set": d.model_dump()})
    await audit(ctx.tenant_id, ctx.user["id"], "settings.updated", ctx.tenant_id, d.model_dump())
    return tenant_view(await db.tenants.find_one({"id": ctx.tenant_id}, {"_id": 0}), ctx.role)


@router.get("/settings/status")
async def settings_status(ctx: Ctx = Depends(owner_ctx)):
    cfg = await platform_config()
    t = ctx.tenant
    return {"sms_mode": cfg.get("SMS_MODE"), "twilio_connected": bool(cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN")),
            "approval_status": t["approval_status"], "provisioning_status": t.get("provisioning_status"), "twilio_number": t.get("twilio_number"),
            "booking_url": f"{origin()}/book/{t['slug']}", "business_url": f"{origin()}/business/{t['slug']}"}


@router.get("/audits")
async def tenant_audits(ctx: Ctx = Depends(owner_ctx)):
    return await ctx.s.find("audits", sort=[("created_at", -1)], limit=200)


@router.get("/dashboard")
async def dashboard(ctx: Ctx = Depends(owner_ctx)):
    s, t = ctx.s, ctx.tenant
    z = ZoneInfo(t["timezone"])
    local = now().astimezone(z)
    m0 = local.replace(day=1, hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    d0 = local.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc)
    month_appts = await s.find("appointments", {"start": {"$gte": m0, "$lt": m0 + relativedelta(months=1)}}, proj={"status": 1})
    completed = sum(1 for a in month_appts if a["status"] == "completed")
    noshow = sum(1 for a in month_appts if a["status"] == "no-show")
    sent = await s.count("messages", {"direction": "outbound", "status": {"$in": ["sent", "delivered", "simulated"]}, "sent_at": {"$gte": m0}})
    reviews_sent = await s.count("messages", {"kind": "review", "status": {"$in": ["sent", "delivered", "simulated"]}, "sent_at": {"$gte": m0}})
    clicks = await s.count("messages", {"kind": {"$in": ["review", "followup"]}, "clicked_at": {"$gte": m0}})
    from appointments import enrich
    today = await enrich(s, await s.find("appointments", {"start": {"$gte": d0, "$lt": d0 + timedelta(days=1)}}, sort=[("start", 1)]))
    attention = await s.find("audits", {"action": "appointment.reply-needs-attention", "created_at": {"$gte": now() - timedelta(days=7)}}, sort=[("created_at", -1)], limit=10)
    return {
        "kpis": {"appointments_month": len(month_appts), "completed_month": completed,
                 "no_show_rate": round(100 * noshow / (completed + noshow), 1) if completed + noshow else 0,
                 "sms_sent_month": sent, "review_requests_month": reviews_sent, "review_clicks_month": clicks,
                 "sms_used": t.get("sms_used", 0) if t.get("sms_month") == local.strftime("%Y-%m") else 0, "quota": t["quota"],
                 "customers": await s.count("contacts"), "consented": await s.count("contacts", {"consent_status": {"$in": ["express", "implied"]}}),
                 "queued": await s.count("messages", {"status": "queued"}), "uncertain": await s.count("messages", {"status": "uncertain"})},
        "today": today, "attention": attention,
        "setup": {"approved": t["approval_status"] == "approved", "number": bool(t.get("twilio_number")), "review_link": bool(t.get("review_link")),
                  "address": bool(t.get("address")), "services": await s.count("services") > 0, "customers": await s.count("contacts") > 0},
    }
