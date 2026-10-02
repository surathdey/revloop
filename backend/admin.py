import asyncio
import secrets
import httpx
from datetime import timedelta, datetime, timezone
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, EmailStr
from typing import Literal, Optional
from pymongo.errors import DuplicateKeyError
from core import db, now, new_id, sha, origin, AppError, audit, platform_config, save_setting, SETTING_KEYS
from policy import normalize_phone
from auth import Ctx, super_ctx, get_ctx, create_tenant
from messaging import _twilio
from emailer import send_email, action_email

router = APIRouter(prefix="/api/admin")


def _mask(v: str):
    return ("•" * 6 + v[-4:]) if v and len(v) > 4 else "set"


SETTING_DEFAULTS = {"SMS_MODE": "simulated", "BILLING_GRACE_DAYS": "7", "SESSION_IDLE_MINUTES": "60", "DEFAULT_TRIAL_DAYS": "14", "ELEVENLABS_VOICE_ID": "burt (Vapi default)"}


@router.get("/settings")
async def get_settings(ctx: Ctx = Depends(super_ctx)):
    cfg = await platform_config()
    out = {}
    for k, secret in SETTING_KEYS.items():
        v = cfg.get(k, "")
        out[k] = {"set": bool(v), "value": _mask(v) if (secret and v) else v, "secret": secret, "default": SETTING_DEFAULTS.get(k, "")}
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


async def _probe(url, headers, key):
    if not key:
        return "not set"
    try:
        async with httpx.AsyncClient(timeout=8) as c:
            r = await c.get(url, headers=headers)
        return "ok" if r.status_code < 300 else f"error {r.status_code}"
    except Exception as e:
        return f"unreachable ({type(e).__name__})"


@router.get("/overview")
async def overview(ctx: Ctx = Depends(super_ctx)):
    cfg = await platform_config()
    tq = {"is_platform": {"$ne": True}}
    return {
        "vapi": await _probe("https://api.vapi.ai/phone-number?limit=1", {"Authorization": f"Bearer {cfg.get('VAPI_API_KEY')}"}, cfg.get("VAPI_API_KEY")),
        "elevenlabs": await _probe("https://api.elevenlabs.io/v1/user", {"xi-api-key": cfg.get("ELEVENLABS_API_KEY") or ""}, cfg.get("ELEVENLABS_API_KEY")),
        "free_numbers": await db.phone_numbers.count_documents({"tenant_id": None}),
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


class NewTenantIn(BaseModel):
    business: str = Field(min_length=2, max_length=100)
    owner_name: str = Field(min_length=2, max_length=80)
    owner_email: EmailStr


@router.post("/tenants")
async def create_garage(d: NewTenantIn, ctx: Ctx = Depends(super_ctx)):
    email = d.owner_email.lower()
    if await db.users.find_one({"email": email}):
        raise AppError("A user with this email already exists.", 409)
    user, t = await create_tenant(email, d.owner_name, d.business, approved=True)
    raw = secrets.token_urlsafe(32)
    await db.password_reset_tokens.insert_one({"id": sha(raw), "user_id": user["id"], "used": False,
                                               "expires_at": now() + timedelta(days=7), "created_at": now()})
    url = f"{origin()}/reset-password?token={raw}"
    await send_email(to=email, subject=f"Your RevLoop account for {d.business}", html=action_email(
        d.owner_name, f"RevLoop created an account for {d.business}. Set your password to sign in.", "Set my password", url,
        "This one-time link expires in 7 days. You can also sign in with Google using this email."))
    await audit(t["id"], ctx.user["id"], "admin.tenant.created", t["id"], {"business": d.business, "owner": email})
    return {"ok": True, "tenant_id": t["id"], "setup_url": url}


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


class BuyIn(BaseModel):
    area_code: int = Field(ge=200, le=999)


@router.post("/numbers/buy")
async def buy_number(d: BuyIn, ctx: Ctx = Depends(super_ctx)):
    cfg = await platform_config()
    if not (cfg.get("TWILIO_ACCOUNT_SID") and cfg.get("TWILIO_AUTH_TOKEN")):
        raise AppError("Twilio credentials are not saved yet.")
    try:
        def run():
            cl = _twilio(cfg)
            avail = cl.available_phone_numbers("CA").local.list(area_code=d.area_code, sms_enabled=True, limit=1)
            if not avail:
                return None
            return cl.incoming_phone_numbers.create(phone_number=avail[0].phone_number, sms_url=f"{origin()}/api/webhooks/twilio/inbound", sms_method="POST")
        n = await asyncio.to_thread(run)
    except Exception as e:
        raise AppError(f"Twilio could not provision a number ({type(e).__name__}: {str(e)[:120]}).", 502)
    if not n:
        raise AppError(f"No SMS-capable numbers available in area code {d.area_code}. Try another.", 404)
    await db.phone_numbers.insert_one({"id": new_id(), "number": n.phone_number, "sid": n.sid, "label": f"Bought {d.area_code}", "tenant_id": None, "created_at": now()})
    await audit(ctx.tenant_id, ctx.user["id"], "admin.number.purchased", n.phone_number, {"sid": n.sid})
    return {"ok": True, "number": n.phone_number}


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
async def audits(tenant_id: str = "", action: str = "", actor: str = "", date_from: str = "", date_to: str = "", ctx: Ctx = Depends(super_ctx)):
    q = {}
    if tenant_id:
        q["tenant_id"] = tenant_id
    if action:
        q["action"] = {"$regex": action, "$options": "i"}
    if actor:
        ids = [u["id"] async for u in db.users.find({"$or": [{"email": {"$regex": actor, "$options": "i"}}, {"role": actor.lower()}]}, {"id": 1})]
        q["actor"] = {"$in": ids + [actor]}
    rng = {}
    for k, v, op in (("from", date_from, "$gte"), ("to", date_to, "$lt")):
        if v:
            try:
                dt = datetime.fromisoformat(v).replace(tzinfo=timezone.utc)
            except ValueError:
                raise AppError("Dates must be YYYY-MM-DD.")
            rng[op] = dt + (timedelta(days=1) if k == "to" else timedelta())
    if rng:
        q["created_at"] = rng
    rows = await db.audits.find(q, {"_id": 0}).sort("created_at", -1).limit(500).to_list(500)
    names = {t["id"]: t["name"] async for t in db.tenants.find({}, {"id": 1, "name": 1})}
    users = {u["id"]: f"{u['email']} ({u['role']})" async for u in db.users.find({}, {"id": 1, "email": 1, "role": 1})}
    for r in rows:
        r["tenant_name"] = names.get(r["tenant_id"])
        r["actor_label"] = users.get(r["actor"], r["actor"])
    return rows
