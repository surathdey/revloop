import asyncio
import json
import logging
from datetime import timedelta
from zoneinfo import ZoneInfo
from fastapi import APIRouter, Depends, Request, Response, BackgroundTasks
from pydantic import BaseModel, Field
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from twilio.rest import Client as TwilioClient
from twilio.request_validator import RequestValidator
from core import db, now, new_id, new_token, origin, AppError, audit, rate_limit, platform_config
from policy import (can_send, next_allowed, review_within_cap, segments, normalize_phone, DEFAULTS, TEMPLATE_KINDS,
                    TEMPLATE_VARS, STOP_WORDS)
from auth import Ctx, get_ctx, owner_ctx

log = logging.getLogger("messaging")
router = APIRouter(prefix="/api")
REMINDER_KINDS = ("confirmation", "reminder24", "reminder2")
SENT_STATUSES = ["sent", "delivered", "simulated"]


async def enqueue(tenant_id, contact_id, kind, scheduled_at, dedupe_key, appointment_id=None, body=""):
    c = await db.contacts.find_one({"id": contact_id, "tenant_id": tenant_id})
    if not c:
        raise AppError("Customer not found.", 404)
    doc = {"id": new_id(), "tenant_id": tenant_id, "contact_id": contact_id, "appointment_id": appointment_id,
           "kind": kind, "direction": "outbound", "body": body, "recipient": c["phone"], "sender": "",
           "status": "queued", "scheduled_at": scheduled_at, "sent_at": None, "delivered_at": None, "clicked_at": None,
           "consent_event_id": None, "consent_snapshot": "", "provider_sid": None, "segments": 1, "error": "",
           "dedupe_key": dedupe_key, "token": new_token(), "created_at": now()}
    try:
        await db.messages.insert_one(doc)
    except DuplicateKeyError:
        return None
    return doc["id"]


async def suppress_queued(tenant_id, q, reason):
    await db.messages.update_many({**q, "tenant_id": tenant_id, "status": "queued"},
                                  {"$set": {"status": "suppressed", "error": reason}})


def _twilio(cfg):
    return TwilioClient(cfg["TWILIO_ACCOUNT_SID"], cfg["TWILIO_AUTH_TOKEN"])


def billing_ok(t, at):
    st = t.get("billing_status")
    if st not in ("active", "trialing", "past_due"):
        return False
    if st == "trialing" and t["trial_ends"] <= at:
        return False
    if st == "past_due" and (not t.get("grace_until") or t["grace_until"] <= at):
        return False
    return True


async def _prepare(m, at, cfg, live):
    t = await db.tenants.find_one({"id": m["tenant_id"]}, {"_id": 0})
    c = await db.contacts.find_one({"id": m["contact_id"], "tenant_id": t["id"]}, {"_id": 0})
    appt = await db.appointments.find_one({"id": m["appointment_id"], "tenant_id": t["id"]}, {"_id": 0}) if m.get("appointment_id") else None

    async def defer(reason):
        await db.messages.update_one({"id": m["id"]}, {"$set": {"error": reason, "scheduled_at": at + timedelta(minutes=15)}})

    async def suppress(reason):
        await db.messages.update_one({"id": m["id"], "status": "queued"}, {"$set": {"status": "suppressed", "error": reason}})

    if t.get("status") != "active":
        return await defer("Garage account is not active")
    if t["approval_status"] != "approved":
        return await defer("Awaiting platform approval")
    if live and (t.get("provisioning_status") != "assigned" or not t.get("twilio_number")):
        return await defer("Awaiting phone number assignment")
    if not c or not can_send(c, at):
        return await suppress("No current consent")
    if m["kind"] in REMINDER_KINDS and (not appt or appt["status"] not in ("scheduled", "confirmed") or appt["start"] <= at):
        return await suppress("Appointment closed or already started")
    if m["kind"] == "review" and review_within_cap(c.get("last_review_at"), at):
        return await suppress("Review request sent within 30 days")
    if m["kind"] == "followup":
        parent = await db.messages.find_one({"tenant_id": t["id"], "appointment_id": m["appointment_id"], "kind": "review"})
        if not parent or not parent.get("sent_at") or parent.get("clicked_at"):
            return await suppress("Review link already clicked or initial request not sent")
    allowed = next_allowed(at, t["timezone"], t["quiet_start"], t["quiet_end"])
    if allowed > at:
        await db.messages.update_one({"id": m["id"]}, {"$set": {"scheduled_at": allowed, "error": "Deferred for quiet hours"}})
        return None
    if not billing_ok(t, at):
        return await defer("Subscription is not active")
    if live and not (cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN")):
        return await defer("Twilio configuration is incomplete")
    if m["kind"] in ("review", "followup") and not t.get("review_link"):
        return await suppress("Google review link is missing")
    tpl = await db.templates.find_one({"tenant_id": t["id"], "kind": m["kind"]})
    tz = ZoneInfo(t["timezone"])
    svc = await db.services.find_one({"id": appt["service_id"], "tenant_id": t["id"]}) if appt else None
    vars_ = {
        "first_name": c["name"].split(" ")[0], "business_name": t["name"],
        "review_link": f"{origin()}/api/r/{m['token']}",
        "booking_link": f"{origin()}/booking/{appt['token']}" if appt else f"{origin()}/book/{t['slug']}",
        "appointment_date": appt["start"].astimezone(tz).strftime("%a, %b %-d") if appt else "",
        "appointment_time": appt["start"].astimezone(tz).strftime("%-I:%M %p") if appt else "",
        "service_name": svc["name"] if svc else (m["body"] if m["kind"] == "serviceDue" else "service"),
    }
    raw = (tpl["body"] if tpl else None) or DEFAULTS.get(m["kind"]) or m["body"]
    if m["kind"] == "serviceDue" and not tpl:
        raw = DEFAULTS["serviceDue"]
    body = raw
    for k, v in vars_.items():
        body = body.replace("{" + k + "}", v)
    body = f"{t['name']}: {body}\nInfo: {origin()}/business/{t['slug']}. Reply STOP to opt out."
    count = segments(body)
    month = at.astimezone(tz).strftime("%Y-%m")
    if t.get("sms_month") != month:
        await db.tenants.update_one({"id": t["id"], "sms_month": {"$ne": month}}, {"$set": {"sms_month": month, "sms_used": 0}})
    u = await db.tenants.find_one({"id": t["id"]})
    extra_used = max(0, u["sms_used"] + count - u["quota"]) - max(0, u["sms_used"] - u["quota"])
    if extra_used > u["extra_segments"]:
        return await defer("SMS segment allowance exhausted")
    consent = await db.consent_events.find_one({"tenant_id": t["id"], "contact_id": c["id"]}, {"_id": 0}, sort=[("created_at", -1)])
    if not consent:
        return await suppress("Missing consent evidence")
    claimed = await db.messages.find_one_and_update(
        {"id": m["id"], "status": "queued"},
        {"$set": {"status": "sending", "error": "", "body": body, "segments": count,
                  "sender": t.get("twilio_number") or "SIMULATED", "consent_event_id": consent["id"],
                  "consent_snapshot": json.dumps(consent, default=str)}},
        projection={"_id": 0}, return_document=ReturnDocument.AFTER)
    if not claimed:
        return None
    await db.tenants.update_one({"id": t["id"]}, {"$inc": {"sms_used": count, "extra_segments": -extra_used}})
    if m["kind"] == "review":
        await db.contacts.update_one({"id": c["id"]}, {"$set": {"last_review_at": at}})
    return {**claimed, "tenant": t}


async def _deliver(r, at, cfg, live):
    fresh = await db.contacts.find_one({"id": r["contact_id"]})
    if not can_send(fresh, now()):
        await db.messages.update_one({"id": r["id"]}, {"$set": {"status": "suppressed", "error": "Consent withdrawn before provider send"}})
        return {"id": r["id"], "status": "suppressed"}
    g = await db.tenants.find_one({"id": r["tenant_id"]})
    if g.get("status") != "active" or g["approval_status"] != "approved" or g.get("twilio_number") != r["tenant"].get("twilio_number"):
        await db.messages.update_one({"id": r["id"]}, {"$set": {"status": "suppressed", "error": "Garage suspended or sender changed before send"}})
        return {"id": r["id"], "status": "suppressed"}
    try:
        if not live:
            sid, status = "SIM_" + r["id"], "simulated"
        else:
            kw = {"to": r["recipient"], "body": r["body"], "status_callback": f"{origin()}/api/webhooks/twilio/status?id={r['id']}"}
            if cfg.get("TWILIO_MESSAGING_SERVICE_SID"):
                kw["messaging_service_sid"] = cfg["TWILIO_MESSAGING_SERVICE_SID"]
            else:
                kw["from_"] = r["tenant"]["twilio_number"]
            sent = await asyncio.to_thread(lambda: _twilio(cfg).messages.create(**kw))
            sid, status = sent.sid, "sent"
    except Exception as e:  # provider outcome unknown: stop for operator reconciliation, never blind-retry
        log.warning("SMS send failed for %s: %s", r["id"], type(e).__name__)
        await db.messages.update_one({"id": r["id"]}, {"$set": {"status": "uncertain", "error": str(e)[:300]}})
        return {"id": r["id"], "status": "uncertain"}
    latest = await db.messages.find_one({"id": r["id"]})
    final = latest["status"] if latest["status"] in ("delivered", "undelivered", "failed") else status
    await db.messages.update_one({"id": r["id"]}, {"$set": {"provider_sid": sid, "status": final, "sent_at": now()}})
    await audit(r["tenant_id"], "worker", "sms.sent", r["id"], {"status": status, "sid": sid, "segments": r["segments"],
                                                                  "consent_event_id": r["consent_event_id"]})
    if r["kind"] == "review":
        await enqueue(r["tenant_id"], r["contact_id"], "followup", at + timedelta(days=3), f"{r['id']}:followup", r.get("appointment_id"))
    return {"id": r["id"], "status": final}


_queue_lock = asyncio.Lock()


async def process_queue(at=None, tenant_id=None):
    async with _queue_lock:
        at = at or now()
        cfg = await platform_config()
        live = cfg.get("SMS_MODE") == "live"
        q = {"status": "queued", "scheduled_at": {"$lte": at}}
        if tenant_id:
            q["tenant_id"] = tenant_id
        out = []
        for m in await db.messages.find(q, {"_id": 0}).sort("scheduled_at", 1).limit(50).to_list(50):
            r = await _prepare(m, at, cfg, live)
            if r:
                out.append(await _deliver(r, at, cfg, live))
        return out


async def scan_due(at=None, tenant_id=None):
    at = at or now()
    q = {"$or": [{"snoozed_until": None}, {"snoozed_until": {"$lte": at}}]}
    if tenant_id:
        q["tenant_id"] = tenant_id
    rules = await db.reminder_rules.find(q, {"_id": 0}).to_list(50000)
    queued = 0
    for r in rules:
        v = await db.vehicles.find_one({"id": r["vehicle_id"], "tenant_id": r["tenant_id"]})
        if not v or (r["due_at"] > at + timedelta(days=14) and v["km"] < r["due_km"]):
            continue
        sn = r["snoozed_until"].isoformat() if r.get("snoozed_until") else "initial"
        if await enqueue(r["tenant_id"], v["contact_id"], "serviceDue", at, f"rule:{r['id']}:{r['due_at'].isoformat()}:{sn}", None, r["name"]):
            queued += 1
        await db.reminder_rules.update_one({"id": r["id"]}, {"$set": {"last_queued_at": at}})
    return queued


async def opt_out(t, c, source, evidence, simulated):
    await db.contacts.update_one({"id": c["id"], "tenant_id": t["id"]}, {"$set": {
        "consent_status": "opted-out", "consent_source": source, "consent_at": now(), "consent_expires_at": None}})
    await db.consent_events.insert_one({"id": new_id(), "tenant_id": t["id"], "contact_id": c["id"], "status": "opted-out",
                                        "source": source, "evidence": json.dumps(evidence), "expires_at": None, "created_at": now()})
    await suppress_queued(t["id"], {"contact_id": c["id"]}, "Customer opted out")
    if simulated:  # live mode: Twilio Advanced Opt-Out sends the single confirmation; never duplicate it
        tpl = await db.templates.find_one({"tenant_id": t["id"], "kind": "optout"})
        await db.messages.insert_one({"id": new_id(), "tenant_id": t["id"], "contact_id": c["id"], "appointment_id": None,
                                      "kind": "optout", "direction": "outbound", "body": f"{t['name']}: {(tpl or {}).get('body') or DEFAULTS['optout']}",
                                      "recipient": c["phone"], "sender": "SIMULATED", "status": "simulated", "scheduled_at": now(),
                                      "sent_at": now(), "segments": 1, "error": "", "dedupe_key": f"optout:{new_id()}",
                                      "token": new_token(), "provider_sid": None, "created_at": now()})
    await audit(t["id"], "customer", "consent.opted-out", c["id"], {**evidence, "confirmation": "simulated" if simulated else "Twilio Advanced Opt-Out"})


async def inbound(params: dict, tenant=None, simulated=False):
    sid, frm, to, body = params.get("MessageSid"), params.get("From"), params.get("To"), params.get("Body", "")
    if not sid or not frm:
        raise AppError("Missing webhook parameters.")
    t = tenant or await db.tenants.find_one({"twilio_number": to}, {"_id": 0})
    if not t:
        return
    c = await db.contacts.find_one({"tenant_id": t["id"], "phone": frm}, {"_id": 0})
    if not c:
        return
    try:
        await db.webhook_events.insert_one({"id": sid, "provider": "twilio", "created_at": now()})
    except DuplicateKeyError:
        return
    await db.messages.insert_one({"id": new_id(), "tenant_id": t["id"], "contact_id": c["id"], "appointment_id": None,
                                  "kind": "reply", "direction": "inbound", "body": body, "recipient": to or "", "sender": frm,
                                  "status": "received", "scheduled_at": now(), "sent_at": now(), "provider_sid": sid,
                                  "segments": 1, "error": "", "dedupe_key": sid, "token": new_token(), "created_at": now()})
    kw = body.strip().upper()
    if params.get("OptOutType") == "STOP" or kw in STOP_WORDS:
        await opt_out(t, c, "inbound SMS", {"sid": sid, "body": body}, simulated)
    if params.get("OptOutType") == "START" or kw in ("START", "UNSTOP"):
        # START only removes the carrier block; it does not re-establish consent.
        await audit(t["id"], "customer", "carrier.unblocked", c["id"], {"sid": sid, "requires_consent_recapture": True})
    if params.get("OptOutType") == "HELP" or kw in ("HELP", "INFO"):
        await audit(t["id"], "customer", "sms.help", c["id"], {"sid": sid})
    if kw in ("YES", "CANCEL"):
        upcoming = await db.appointments.find({"tenant_id": t["id"], "contact_id": c["id"], "status": {"$in": ["scheduled", "confirmed"]},
                                               "start": {"$gte": now()}}, {"_id": 0}).sort("start", 1).to_list(50)
        # The reply refers to the appointment whose confirmation/reminder this customer received most recently.
        last = await db.messages.find_one({"tenant_id": t["id"], "contact_id": c["id"], "direction": "outbound",
                                           "kind": {"$in": list(REMINDER_KINDS)}, "sent_at": {"$ne": None},
                                           "appointment_id": {"$in": [a["id"] for a in upcoming]}}, sort=[("sent_at", -1)])
        target = next((a for a in upcoming if last and a["id"] == last["appointment_id"]), None) or (upcoming[0] if len(upcoming) == 1 else None)
        if target:
            a = target
            new = "confirmed" if kw == "YES" else "cancelled"
            await db.appointments.update_one({"id": a["id"], "tenant_id": t["id"]}, {"$set": {"status": new}})
            if kw == "CANCEL":
                await suppress_queued(t["id"], {"appointment_id": a["id"]}, "Appointment cancelled")
            await audit(t["id"], "customer", f"appointment.{new}", a["id"], {"via": "SMS"})
        elif len(upcoming) > 1:
            await audit(t["id"], "customer", "appointment.reply-needs-attention", c["id"],
                        {"body": body, "reason": "Multiple upcoming appointments; staff must resolve"})


async def _verified_form(request: Request):
    cfg = await platform_config()
    if not cfg.get("TWILIO_AUTH_TOKEN"):
        raise AppError("Twilio is not configured.", 503)
    form = dict(await request.form())
    url = origin() + request.url.path + (("?" + request.url.query) if request.url.query else "")
    if not RequestValidator(cfg["TWILIO_AUTH_TOKEN"]).validate(url, form, request.headers.get("X-Twilio-Signature", "")):
        raise AppError("Invalid Twilio signature.", 403)
    return form


TWIML_EMPTY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


@router.post("/webhooks/twilio/inbound")
async def twilio_inbound(request: Request):
    await rate_limit("wh-inbound:" + (request.client.host if request.client else "x"), 600, 60)
    form = await _verified_form(request)
    await inbound(form)
    return Response(TWIML_EMPTY, media_type="application/xml")


@router.post("/webhooks/twilio/status")
async def twilio_status(request: Request, id: str = ""):
    await rate_limit("wh-status:" + (request.client.host if request.client else "x"), 1200, 60)
    form = await _verified_form(request)
    st, sid = form.get("MessageStatus", ""), form.get("MessageSid", "")
    try:
        await db.webhook_events.insert_one({"id": f"{sid}:{st}", "provider": "twilio", "created_at": now()})
    except DuplicateKeyError:
        return {"ok": True}
    m = await db.messages.find_one({"id": id, "provider_sid": {"$in": [sid, None]}})
    if m and st in ("sent", "delivered", "undelivered", "failed") and m["status"] not in ("delivered", "undelivered", "failed"):
        upd = {"status": st, "provider_sid": sid}
        if st == "delivered":
            upd["delivered_at"] = now()
        if form.get("ErrorCode"):
            upd["error"] = f"Carrier error {form['ErrorCode']}"
        await db.messages.update_one({"id": m["id"]}, {"$set": upd})
    return {"ok": True}


# ---------- tenant-facing messaging routes ----------

async def _with_names(ctx, msgs):
    ids = list({m["contact_id"] for m in msgs})
    names = {c["id"]: c for c in await ctx.s.find("contacts", {"id": {"$in": ids}}, proj={"id": 1, "name": 1, "phone": 1, "consent_status": 1})}
    for m in msgs:
        c = names.get(m["contact_id"], {})
        m["contact_name"], m["contact_phone"], m["contact_consent"] = c.get("name", "Unknown"), c.get("phone", ""), c.get("consent_status", "")
        m.pop("consent_snapshot", None)
        m.pop("token", None)
    return msgs


@router.get("/messages")
async def list_messages(contact_id: str = "", status: str = "", ctx: Ctx = Depends(get_ctx)):
    q = {}
    if contact_id:
        q["contact_id"] = contact_id
    if status:
        q["status"] = status
    return await _with_names(ctx, await ctx.s.find("messages", q, sort=[("created_at", -1)], limit=500))


@router.get("/conversations")
async def conversations(ctx: Ctx = Depends(get_ctx)):
    pipe = [{"$match": {"tenant_id": ctx.tenant_id, "status": {"$ne": "suppressed"}}}, {"$sort": {"created_at": -1}},
            {"$group": {"_id": "$contact_id", "last": {"$first": "$$ROOT"}, "count": {"$sum": 1},
                        "inbound": {"$sum": {"$cond": [{"$eq": ["$direction", "inbound"]}, 1, 0]}}}},
            {"$sort": {"last.created_at": -1}}, {"$limit": 200}]
    rows = await db.messages.aggregate(pipe).to_list(200)
    lasts = [{**{k: v for k, v in r["last"].items() if k != "_id"}, "count": r["count"], "inbound": r["inbound"]} for r in rows]
    return await _with_names(ctx, lasts)


class ManualIn(BaseModel):
    contact_id: str
    body: str = Field(min_length=1, max_length=600)


@router.post("/messages/send")
async def send_manual(d: ManualIn, bg: BackgroundTasks, ctx: Ctx = Depends(get_ctx)):
    c = await ctx.s.get("contacts", d.contact_id, "Customer not found.")
    if not can_send(c, now()):
        raise AppError("This customer has no current SMS consent." if c["consent_status"] != "opted-out" else "This customer opted out. Messages are blocked.")
    mid = await enqueue(ctx.tenant_id, c["id"], "manual", now(), f"manual:{new_id()}", None, d.body)
    await audit(ctx.tenant_id, ctx.user["id"], "sms.manual.queued", mid, {})
    bg.add_task(process_queue, None, ctx.tenant_id)
    return {"id": mid}


class SimReplyIn(BaseModel):
    contact_id: str
    body: str = Field(min_length=1, max_length=1000)


@router.post("/messages/simulate-reply")
async def simulate_reply(d: SimReplyIn, ctx: Ctx = Depends(get_ctx)):
    if (await platform_config()).get("SMS_MODE") == "live":
        raise AppError("Simulated replies are disabled while SMS is live.", 403)
    c = await ctx.s.get("contacts", d.contact_id, "Customer not found.")
    await inbound({"MessageSid": "SIM_IN_" + new_id(), "From": c["phone"], "To": ctx.tenant.get("twilio_number") or "", "Body": d.body},
                  tenant=ctx.tenant, simulated=True)
    return {"ok": True}


@router.post("/messages/process")
async def process_now(ctx: Ctx = Depends(owner_ctx)):
    queued = await scan_due(None, ctx.tenant_id)
    return {"results": await process_queue(None, ctx.tenant_id), "service_due_queued": queued}


@router.get("/templates")
async def get_templates(ctx: Ctx = Depends(owner_ctx)):
    rows = {t["kind"]: t for t in await ctx.s.find("templates")}
    return {"templates": [{"kind": k, "body": rows.get(k, {}).get("body", DEFAULTS[k]), "default": DEFAULTS[k]} for k in TEMPLATE_KINDS],
            "variables": TEMPLATE_VARS, "footer": f"{ctx.tenant['name']}: …\\nInfo: {origin()}/business/{ctx.tenant['slug']}. Reply STOP to opt out."}


class TemplateIn(BaseModel):
    body: str = Field(min_length=10, max_length=1000)


@router.put("/templates/{kind}")
async def save_template(kind: str, d: TemplateIn, ctx: Ctx = Depends(owner_ctx)):
    if kind not in TEMPLATE_KINDS:
        raise AppError("Unknown template.", 404)
    await db.templates.update_one({"tenant_id": ctx.tenant_id, "kind": kind}, {"$set": {"body": d.body},
                                  "$setOnInsert": {"id": new_id(), "created_at": now()}}, upsert=True)
    await audit(ctx.tenant_id, ctx.user["id"], "template.saved", kind, {"body": d.body})
    return {"ok": True}


class TestSmsIn(BaseModel):
    phone: str
    consent: bool


@router.post("/phone/test")
async def test_sms(d: TestSmsIn, ctx: Ctx = Depends(owner_ctx)):
    if not d.consent:
        raise AppError("Confirm consent to receive the test message.")
    await rate_limit("test-sms:" + ctx.tenant_id, 3, 3600)
    phone = normalize_phone(d.phone)
    c = await ctx.s.one("contacts", {"phone": phone})
    if c and c["consent_status"] == "opted-out":
        raise AppError("This number is opted out. Recapture consent through the customer record first.")
    if not c:
        c = await ctx.s.insert("contacts", {"name": ctx.user["name"], "phone": phone, "email": "", "notes": "", "tags": "owner",
                                            "consent_status": "express", "consent_source": "Owner test request", "consent_at": now(),
                                            "consent_expires_at": None, "last_review_at": None})
    if c["consent_status"] != "express":
        raise AppError("Record express consent on this customer first.")
    await ctx.s.insert("consent_events", {"contact_id": c["id"], "status": "express", "source": "Owner test request", "expires_at": None,
                                          "evidence": "Owner requested a test SMS to their number and explicitly checked the consent box."})
    await enqueue(ctx.tenant_id, c["id"], "test", now(), "test:" + new_id(), None, "Your RevLoop connection is working.")
    return {"results": await process_queue(None, ctx.tenant_id)}
