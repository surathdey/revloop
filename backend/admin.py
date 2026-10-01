import asyncio
from datetime import timedelta
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from typing import Literal, Optional
from pymongo.errors import DuplicateKeyError
from core import db, now, new_id, origin, AppError, audit, platform_config, save_setting, SETTING_KEYS
from policy import normalize_phone
from auth import Ctx, super_ctx, get_ctx
from messaging import _twilio

router = APIRouter(prefix="/api/admin")


def _mask(v: str):
    return ("•" * 6 + v[-4:]) if v and len(v) > 4 else "set"


@router.get("/settings")
async def get_settings(ctx: Ctx = Depends(super_ctx)):
    cfg = await platform_config()
    out = {}
    for k, secret in SETTING_KEYS.items():
        v = cfg.get(k, "")
        out[k] = {"set": bool(v), "value": _mask(v) if (secret and v) else v, "secret": secret}
    return out


class SettingsIn(BaseModel):
    values: dict


@router.put("/settings")
async def put_settings(d: SettingsIn, ctx: Ctx = Depends(super_ctx)):
    changed = []
    for k, v in d.values.items():
        if k not in SETTING_KEYS:
            raise AppError(f"Unknown setting {k}.")
        v = (v or "").strip()
        if k == "SMS_MODE" and v not in ("live", "simulated"):
            raise AppError("SMS mode must be live or simulated.")
        await save_setting(k, v)
        changed.append(k)
    await audit(ctx.tenant_id, ctx.user["id"], "platform.settings.updated", "platform", {"keys": changed})
    return {"ok": True, "changed": changed}


@router.post("/twilio/check")
async def twilio_check(ctx: Ctx = Depends(super_ctx)):
    cfg = await platform_config()
    if not (cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN")):
        raise AppError("Twilio credentials are not saved yet.")
    try:
        acct = await asyncio.to_thread(lambda: _twilio(cfg).api.accounts(cfg["TWILIO_ACCOUNT_SID"]).fetch())
        nums = await asyncio.to_thread(lambda: _twilio(cfg).incoming_phone_numbers.list(limit=50))
    except Exception as e:
        raise AppError(f"Twilio rejected the credentials ({type(e).__name__}).", 502)
    return {"ok": True, "status": acct.status, "friendly_name": acct.friendly_name,
            "numbers": [{"number": n.phone_number, "sid": n.sid, "sms_url": n.sms_url} for n in nums]}


@router.get("/overview")
async def overview(ctx: Ctx = Depends(super_ctx)):
    cfg = await platform_config()
    tq = {"is_platform": {"$ne": True}}
    return {
        "tenants": await db.tenants.count_documents(tq),
        "pending": await db.tenants.count_documents({**tq, "approval_status": "pending"}),
        "suspended": await db.tenants.count_documents({**tq, "status": "suspended"}),
        "contacts": await db.contacts.count_documents({}),
        "queued": await db.messages.count_documents({"status": "queued"}),
        "uncertain": await db.messages.count_documents({"status": "uncertain"}),
        "sent_24h": await db.messages.count_documents({"status": {"$in": ["sent", "delivered", "simulated"]}, "sent_at": {"$gte": now() - timedelta(days=1)}}),
        "failed_24h": await db.messages.count_documents({"status": {"$in": ["failed", "undelivered"]}, "created_at": {"$gte": now() - timedelta(days=1)}}),
        "sms_mode": cfg.get("SMS_MODE"), "twilio_configured": bool(cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN")),
        "last_cron": await db.cron_runs.find_one({}, {"_id": 0}, sort=[("created_at", -1)]),
        "db": "ok",
    }


@router.get("/tenants")
async def tenants(ctx: Ctx = Depends(super_ctx)):
    rows = await db.tenants.find({"is_platform": {"$ne": True}}, {"_id": 0}).sort("created_at", -1).to_list(2000)
    for t in rows:
        owner = await db.users.find_one({"tenant_id": t["id"], "role": "owner"}, {"_id": 0, "email": 1, "name": 1})
        t["owner"] = owner
        t["contacts"] = await db.contacts.count_documents({"tenant_id": t["id"]})
    return rows


class TenantActionIn(BaseModel):
    action: Literal["approve", "unapprove", "suspend", "activate", "cancel"]
    reason: str = Field(default="", max_length=500)


@router.post("/tenants/{tid}/action")
async def tenant_action(tid: str, d: TenantActionIn, ctx: Ctx = Depends(super_ctx)):
    t = await db.tenants.find_one({"id": tid, "is_platform": {"$ne": True}})
    if not t:
        raise AppError("Tenant not found.", 404)
    upd = {"approve": {"approval_status": "approved", "approved_at": now(), "approved_by": ctx.user["id"]},
           "unapprove": {"approval_status": "pending"}, "suspend": {"status": "suspended"},
           "activate": {"status": "active"}, "cancel": {"status": "cancelled"}}[d.action]
    await db.tenants.update_one({"id": tid}, {"$set": upd})
    if d.action in ("suspend", "cancel"):
        await db.sessions.delete_many({"user_id": {"$in": [u["id"] async for u in db.users.find({"tenant_id": tid}, {"id": 1})]}})
    await audit(tid, ctx.user["id"], f"admin.tenant.{d.action}", tid, {"reason": d.reason})
    return {"ok": True}


@router.get("/numbers")
async def numbers(ctx: Ctx = Depends(super_ctx)):
    rows = await db.phone_numbers.find({}, {"_id": 0}).sort("created_at", 1).to_list(500)
    names = {t["id"]: t["name"] async for t in db.tenants.find({}, {"id": 1, "name": 1})}
    for r in rows:
        r["tenant_name"] = names.get(r.get("tenant_id"))
    return rows


class NumberIn(BaseModel):
    number: str
    label: str = Field(default="", max_length=80)


@router.post("/numbers")
async def add_number(d: NumberIn, ctx: Ctx = Depends(super_ctx)):
    n = normalize_phone(d.number)
    try:
        await db.phone_numbers.insert_one({"id": new_id(), "number": n, "label": d.label, "tenant_id": None, "created_at": now()})
    except DuplicateKeyError:
        raise AppError("This number is already in the pool.", 409)
    await audit(ctx.tenant_id, ctx.user["id"], "admin.number.added", n, {})
    return {"ok": True}


@router.delete("/numbers/{nid}")
async def remove_number(nid: str, ctx: Ctx = Depends(super_ctx)):
    r = await db.phone_numbers.find_one({"id": nid})
    if not r:
        raise AppError("Number not found.", 404)
    if r.get("tenant_id"):
        raise AppError("Release the number from its garage first.")
    await db.phone_numbers.delete_one({"id": nid})
    return {"ok": True}


async def _configure_webhook(number: str):
    cfg = await platform_config()
    if not (cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN")):
        return "Twilio not configured; inbound webhook not set."
    try:
        def run():
            cl = _twilio(cfg)
            found = cl.incoming_phone_numbers.list(phone_number=number, limit=1)
            if not found:
                return "Number not found in the Twilio account; set its SMS webhook manually."
            cl.incoming_phone_numbers(found[0].sid).update(sms_url=f"{origin()}/api/webhooks/twilio/inbound", sms_method="POST")
            return "Inbound SMS webhook configured on Twilio."
        return await asyncio.to_thread(run)
    except Exception as e:
        return f"Could not configure Twilio webhook ({type(e).__name__})."


class AssignIn(BaseModel):
    number_id: str


@router.post("/tenants/{tid}/assign-number")
async def assign_number(tid: str, d: AssignIn, ctx: Ctx = Depends(super_ctx)):
    t = await db.tenants.find_one({"id": tid, "is_platform": {"$ne": True}})
    if not t:
        raise AppError("Tenant not found.", 404)
    if t.get("twilio_number"):
        raise AppError("Release the current number first.")
    n = await db.phone_numbers.find_one_and_update({"id": d.number_id, "tenant_id": None}, {"$set": {"tenant_id": tid}})
    if not n:
        raise AppError("That number is not available.", 409)
    await db.tenants.update_one({"id": tid}, {"$set": {"twilio_number": n["number"], "provisioning_status": "assigned"}})
    note = await _configure_webhook(n["number"])
    await audit(tid, ctx.user["id"], "admin.number.assigned", n["number"], {"note": note})
    return {"ok": True, "note": note}


@router.post("/tenants/{tid}/release-number")
async def release_number(tid: str, ctx: Ctx = Depends(super_ctx)):
    t = await db.tenants.find_one({"id": tid})
    if not t or not t.get("twilio_number"):
        raise AppError("No number assigned.")
    await db.phone_numbers.update_one({"number": t["twilio_number"]}, {"$set": {"tenant_id": None}})
    await db.tenants.update_one({"id": tid}, {"$set": {"twilio_number": None, "provisioning_status": "unassigned"}})
    await audit(tid, ctx.user["id"], "admin.number.released", t["twilio_number"], {})
    return {"ok": True}


class ImpersonateIn(BaseModel):
    tenant_id: str
    reason: str = Field(min_length=10, max_length=500)


@router.post("/impersonate")
async def impersonate(d: ImpersonateIn, ctx: Ctx = Depends(super_ctx)):
    t = await db.tenants.find_one({"id": d.tenant_id, "is_platform": {"$ne": True}})
    if not t:
        raise AppError("Tenant not found.", 404)
    await db.sessions.update_one({"id": ctx.session_id}, {"$set": {"impersonated_tenant_id": t["id"]}})
    await audit(t["id"], ctx.user["id"], "support.impersonation.started", t["id"], {"reason": d.reason})
    return {"ok": True}


@router.post("/impersonate/stop")
async def stop_impersonate(ctx: Ctx = Depends(get_ctx)):
    if ctx.role != "superadmin":
        raise AppError("Access denied.", 403)
    await db.sessions.update_one({"id": ctx.session_id}, {"$set": {"impersonated_tenant_id": None}})
    await audit(ctx.tenant_id, ctx.user["id"], "support.impersonation.stopped", ctx.tenant_id, {})
    return {"ok": True}


@router.get("/audits")
async def audits(tenant_id: str = "", action: str = "", ctx: Ctx = Depends(super_ctx)):
    q = {}
    if tenant_id:
        q["tenant_id"] = tenant_id
    if action:
        q["action"] = {"$regex": action, "$options": "i"}
    rows = await db.audits.find(q, {"_id": 0}).sort("created_at", -1).limit(300).to_list(300)
    names = {t["id"]: t["name"] async for t in db.tenants.find({}, {"id": 1, "name": 1})}
    for r in rows:
        r["tenant_name"] = names.get(r["tenant_id"])
    return rows
