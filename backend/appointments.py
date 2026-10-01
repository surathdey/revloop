from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from dateutil.relativedelta import relativedelta
from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, Literal
from core import db, now, new_token, AppError, audit, Scoped
from policy import can_send, review_within_cap, next_allowed
from auth import Ctx, get_ctx
from messaging import enqueue, suppress_queued, process_queue

router = APIRouter(prefix="/api")
OPEN = ["scheduled", "confirmed"]


class BookIn(BaseModel):
    contact_id: str
    vehicle_id: str
    service_id: str
    start: str = Field(min_length=10)
    bay: int = Field(ge=1)
    staff_id: str
    notes: str = Field(default="", max_length=2000)


def parse_start(s: str, tz: str) -> datetime:
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        raise AppError("Choose a valid appointment time.")
    return (dt if dt.tzinfo else dt.replace(tzinfo=ZoneInfo(tz))).astimezone(timezone.utc)


async def book_appointment(t: dict, actor_id: str, d: BookIn):
    s = Scoped(t["id"])
    vehicle = await s.one("vehicles", {"id": d.vehicle_id, "contact_id": d.contact_id})
    service = await s.one("services", {"id": d.service_id})
    staff = await s.one("users", {"id": d.staff_id})
    if not vehicle or not service or not staff:
        raise AppError("Choose a customer, vehicle, service and team member from this shop.")
    start = parse_start(d.start, t["timezone"])
    end = start + timedelta(minutes=service["duration"])
    if start < now():
        raise AppError("Choose a future appointment time.")
    z = ZoneInfo(t["timezone"])
    ls, le = start.astimezone(z), end.astimezone(z)
    if ls.hour < t["open_hour"] or le.hour + le.minute / 60 > t["close_hour"] or ls.date() != le.date():
        raise AppError("Choose a time within shop hours.")
    if d.bay > t["bays"]:
        raise AppError("That bay does not exist.")
    conflict = await s.one("appointments", {"status": {"$in": OPEN}, "start": {"$lt": end}, "end": {"$gt": start},
                                            "$or": [{"bay": d.bay}, {"staff_id": d.staff_id}, {"vehicle_id": d.vehicle_id}]})
    if conflict:
        raise AppError("That bay, team member or vehicle is already booked at this time.", 409)
    a = await s.insert("appointments", {**d.model_dump(), "start": start, "end": end, "status": "scheduled", "token": new_token()})
    for kind, delay in (("confirmation", 0), ("reminder24", 24), ("reminder2", 2)):
        at = start - timedelta(hours=delay) if delay else now()
        if not delay or at > now():
            await enqueue(t["id"], d.contact_id, kind, at, f"{a['id']}:{kind}", a["id"])
    await audit(t["id"], actor_id, "appointment.booked", a["id"], {"start": start, "bay": d.bay})
    return a


async def enrich(s: Scoped, appts):
    cids = list({a["contact_id"] for a in appts})
    vids = list({a["vehicle_id"] for a in appts})
    contacts = {c["id"]: c for c in await s.find("contacts", {"id": {"$in": cids}}, proj={"id": 1, "name": 1, "phone": 1, "consent_status": 1})}
    vehicles = {v["id"]: v for v in await s.find("vehicles", {"id": {"$in": vids}})}
    services = {x["id"]: x for x in await s.find("services")}
    users = {u["id"]: u["name"] for u in await s.find("users", proj={"id": 1, "name": 1})}
    for a in appts:
        a["contact"] = contacts.get(a["contact_id"])
        a["vehicle"] = vehicles.get(a["vehicle_id"])
        a["service"] = services.get(a["service_id"])
        a["staff_name"] = users.get(a["staff_id"], "")
        a.pop("token", None)
    return appts


@router.get("/appointments")
async def list_appointments(start: str = "", end: str = "", contact_id: str = "", ctx: Ctx = Depends(get_ctx)):
    q = {}
    if start and end:
        q["start"] = {"$gte": parse_start(start, ctx.tenant["timezone"]), "$lt": parse_start(end, ctx.tenant["timezone"])}
    if contact_id:
        q["contact_id"] = contact_id
    return await enrich(ctx.s, await ctx.s.find("appointments", q, sort=[("start", 1)], limit=2000))


@router.post("/appointments")
async def create_appointment(d: BookIn, bg: BackgroundTasks, ctx: Ctx = Depends(get_ctx)):
    a = await book_appointment(ctx.tenant, ctx.user["id"], d)
    bg.add_task(process_queue, None, ctx.tenant_id)
    a.pop("token", None)
    return a


class StatusIn(BaseModel):
    status: Literal["confirmed", "completed", "cancelled", "no-show"]
    notes: str = Field(default="", max_length=2000)
    km: Optional[int] = Field(default=None, ge=0)


@router.post("/appointments/{aid}/status")
async def appointment_status(aid: str, d: StatusIn, bg: BackgroundTasks, ctx: Ctx = Depends(get_ctx)):
    s, t = ctx.s, ctx.tenant
    a = await s.get("appointments", aid, "Appointment not found.")
    if a["status"] == d.status:
        return {"ok": True}
    if a["status"] in ("cancelled", "completed", "no-show"):
        raise AppError("This appointment is already closed.")
    if d.status == "no-show" and a["start"] > now():
        raise AppError("Wait until the appointment has started before recording a no-show.")
    vehicle = await s.get("vehicles", a["vehicle_id"])
    km = d.km if d.km is not None else vehicle["km"]
    if d.status == "completed" and km < vehicle["km"]:
        raise AppError("Odometer cannot go backwards.")
    await s.update("appointments", {"id": aid}, {"$set": {"status": d.status}})
    if d.status in ("cancelled", "completed", "no-show"):
        await suppress_queued(ctx.tenant_id, {"appointment_id": aid}, "Appointment closed")
    if d.status == "completed":
        service = await s.get("services", a["service_id"])
        contact = await s.get("contacts", a["contact_id"])
        await s.update("vehicles", {"id": vehicle["id"]}, {"$set": {"km": km}})
        if not await s.one("service_records", {"appointment_id": aid}):
            await s.insert("service_records", {"vehicle_id": vehicle["id"], "appointment_id": aid, "date": now(), "type": service["name"],
                                               "amount": service["price"], "notes": d.notes, "technician": ctx.user["name"], "km": km})
        await enqueue(ctx.tenant_id, a["contact_id"], "serviceCompleted", now(), f"{aid}:serviceCompleted", aid)
        if can_send(contact, now()) and not review_within_cap(contact.get("last_review_at"), now()):
            at = next_allowed(now() + timedelta(minutes=t["review_delay"]), t["timezone"], t["quiet_start"], t["quiet_end"])
            await enqueue(ctx.tenant_id, a["contact_id"], "review", at, f"{aid}:review", aid)
        for rule in await s.find("reminder_rules", {"vehicle_id": vehicle["id"], "name": service["name"]}):
            await s.update("reminder_rules", {"id": rule["id"]}, {"$set": {
                "due_at": now() + relativedelta(months=rule["interval_months"]), "due_km": km + rule["interval_km"],
                "snoozed_until": None, "last_queued_at": None}})
    await audit(ctx.tenant_id, ctx.user["id"], f"appointment.{d.status}", aid, d.model_dump())
    bg.add_task(process_queue, None, ctx.tenant_id)
    return {"ok": True}
