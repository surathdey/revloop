import re
import secrets
from dataclasses import dataclass
from datetime import timedelta
import bcrypt
import httpx
from fastapi import APIRouter, Request, Response, Depends
from pydantic import BaseModel, EmailStr, Field
from core import db, now, new_id, sha, AppError, audit, rate_limit, Scoped, platform_config, origin
from policy import require_owner, DEFAULTS
from emailer import send_email, action_email

router = APIRouter(prefix="/api/auth")
COOKIE = "session_token"
SESSION_DAYS = 7
STAFF_TENANT_FIELDS = ["id", "name", "slug", "timezone", "bays", "open_hour", "close_hour", "status"]


def hash_password(p: str) -> str:
    return bcrypt.hashpw(p.encode(), bcrypt.gensalt()).decode()


def verify_password(p: str, h: str) -> bool:
    return bool(h) and bcrypt.checkpw(p.encode(), h.encode())


@dataclass
class Ctx:
    user: dict
    tenant: dict
    session_id: str
    impersonating: bool

    @property
    def tenant_id(self):
        return self.tenant["id"]

    @property
    def s(self):
        return Scoped(self.tenant["id"], self.user["id"])

    @property
    def role(self):
        return self.user["role"]


async def get_ctx(request: Request) -> Ctx:
    raw = request.cookies.get(COOKIE)
    if not raw:
        h = request.headers.get("Authorization", "")
        raw = h[7:] if h.startswith("Bearer ") else None
    if not raw:
        raise AppError("Please sign in.", 401)
    s = await db.sessions.find_one({"id": sha(raw)}, {"_id": 0})
    if not s or s["expires_at"] < now():
        raise AppError("Please sign in again.", 401)
    idle = int((await platform_config()).get("SESSION_IDLE_MINUTES") or 60)
    seen = s.get("last_seen_at") or s["created_at"]
    if (now() - seen).total_seconds() > idle * 60:
        await db.sessions.delete_one({"id": s["id"]})
        raise AppError("Session expired after inactivity. Please sign in again.", 401)
    if (now() - seen).total_seconds() > 60:
        await db.sessions.update_one({"id": s["id"]}, {"$set": {"last_seen_at": now()}})
    user = await db.users.find_one({"id": s["user_id"]}, {"_id": 0, "password_hash": 0})
    if not user:
        raise AppError("Please sign in again.", 401)
    imp = user["role"] == "superadmin" and s.get("impersonated_tenant_id")
    tenant = await db.tenants.find_one({"id": imp or user["tenant_id"]}, {"_id": 0})
    if not tenant:
        raise AppError("Workspace not found.", 401)
    if user["role"] != "superadmin" and tenant.get("status") in ("suspended", "cancelled"):
        raise AppError(f"This garage account is {tenant['status']}. Contact RevLoop support.", 403)
    return Ctx(user=user, tenant=tenant, session_id=s["id"], impersonating=bool(imp))


async def _deny(ctx: Ctx, request: Request, msg: str):
    await audit(ctx.tenant_id, ctx.user["id"], "access.denied", request.url.path,
                {"role": ctx.role, "path": request.url.path, "method": request.method, "outcome": "denied"})
    raise AppError(msg, 403)


async def owner_ctx(request: Request, ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if ctx.role not in ("owner", "superadmin"):
        await _deny(ctx, request, "Access denied. Only the owner can access this area.")
    return ctx


async def super_ctx(request: Request, ctx: Ctx = Depends(get_ctx)) -> Ctx:
    if ctx.role != "superadmin":
        await _deny(ctx, request, "Access denied.")
    return ctx


def tenant_view(t: dict, role: str):
    return {k: t.get(k) for k in STAFF_TENANT_FIELDS} if role == "staff" else t


async def create_session(user_id: str, response: Response):
    raw = secrets.token_hex(32)
    await db.sessions.insert_one({"id": sha(raw), "user_id": user_id, "expires_at": now() + timedelta(days=SESSION_DAYS),
                                  "impersonated_tenant_id": None, "created_at": now()})
    response.set_cookie(COOKIE, raw, httponly=True, secure=True, samesite="none", max_age=SESSION_DAYS * 86400, path="/")


def slugify(s: str):
    return (re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:40] or "garage") + "-" + secrets.token_hex(3)


async def create_tenant(email, name, business, password_hash=None, google_sub=None, role="owner", approved=False, slug=None):
    trial_days = int((await platform_config()).get("DEFAULT_TRIAL_DAYS") or 14)
    t = {
        "id": new_id(), "slug": slug or slugify(business), "name": business, "address": "", "phone": "",
        "timezone": "America/Toronto", "logo": "", "review_link": "", "status": "active",
        "approval_status": "approved" if approved else "pending", "approved_at": now() if approved else None,
        "provisioning_status": "unassigned", "twilio_number": None, "quiet_start": 9, "quiet_end": 20,
        "review_delay": 120, "plan": "trial", "quota": 100, "sms_used": 0, "sms_month": "", "extra_segments": 0,
        "billing_status": "trialing", "trial_ends": now() + timedelta(days=trial_days), "grace_until": None, "slot_capacity": 1,
        "open_hour": 8, "close_hour": 18, "bays": 3, "is_platform": role == "superadmin", "created_at": now(),
    }
    await db.tenants.insert_one(dict(t))
    user = {"id": new_id(), "email": email, "name": name, "password_hash": password_hash, "google_sub": google_sub,
            "role": role, "tenant_id": t["id"], "created_at": now()}
    await db.users.insert_one(dict(user))
    sc = Scoped(t["id"])
    for n, d, p in [("Oil & filter change", 45, 8995), ("Tire change", 60, 12000), ("Vehicle inspection", 60, 14900)]:
        await sc.insert("services", {"name": n, "duration": d, "price": p})
    for kind, body in DEFAULTS.items():
        await sc.insert("templates", {"kind": kind, "body": body})
    await audit(t["id"], user["id"], "tenant.created", t["id"], {"business": business})
    return user, t


class SignupIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    business: str = Field(min_length=2, max_length=100)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class LoginIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)
    password: str = Field(min_length=1, max_length=128)


class InviteAcceptIn(BaseModel):
    token: str = Field(min_length=20)
    name: str = Field(min_length=2, max_length=80)
    email: EmailStr
    password: str = Field(min_length=12, max_length=128)


class GoogleIn(BaseModel):
    session_id: str
    invite_token: str = ""


@router.post("/signup")
async def signup(d: SignupIn, request: Request, response: Response):
    email = d.email.lower()
    await rate_limit("signup:" + (request.client.host if request.client else "x"), 10, 3600)
    if await db.users.find_one({"email": email}):
        raise AppError("An account with this email already exists.", 409)
    user, _ = await create_tenant(email, d.name, d.business, hash_password(d.password))
    await create_session(user["id"], response)
    return {"ok": True}


@router.post("/login")
async def login(d: LoginIn, request: Request, response: Response):
    email = d.email.lower()
    ident = f"{request.client.host if request.client else 'x'}:{email}"
    att = await db.login_attempts.find_one({"identifier": ident})
    if att and att.get("count", 0) >= 5 and att["locked_until"] > now():
        raise AppError("Too many failed attempts. Try again in 15 minutes.", 429)
    user = await db.users.find_one({"email": email})
    if not user or not verify_password(d.password, user.get("password_hash") or ""):
        await db.login_attempts.update_one({"identifier": ident},
                                           {"$inc": {"count": 1}, "$set": {"locked_until": now() + timedelta(minutes=15)}}, upsert=True)
        if user:
            await audit(user["tenant_id"], user["id"], "auth.login.failed", user["id"], {"ip": request.client.host if request.client else ""})
        raise AppError("Email or password is incorrect.", 401)
    await db.login_attempts.delete_one({"identifier": ident})
    await create_session(user["id"], response)
    await audit(user["tenant_id"], user["id"], "auth.login", user["id"], {"method": "password", "role": user["role"]})
    return {"ok": True}


@router.post("/logout")
async def logout(request: Request, response: Response):
    raw = request.cookies.get(COOKIE)
    if raw:
        s = await db.sessions.find_one({"id": sha(raw)})
        if s:
            u = await db.users.find_one({"id": s["user_id"]})
            if u:
                await audit(u["tenant_id"], u["id"], "auth.logout", u["id"], {})
        await db.sessions.delete_one({"id": sha(raw)})
    response.delete_cookie(COOKIE, path="/", secure=True, samesite="none")
    return {"ok": True}


@router.get("/me")
async def me(ctx: Ctx = Depends(get_ctx)):
    from core import platform_config
    return {"user": {k: ctx.user.get(k) for k in ("id", "name", "email", "role")},
            "tenant": tenant_view(ctx.tenant, ctx.role), "impersonating": ctx.impersonating,
            "sms_mode": (await platform_config()).get("SMS_MODE")}


async def _claim_invite(token: str, email: str):
    inv = await db.invites.find_one({"token_hash": sha(token)})
    if not inv or inv.get("accepted_at") or inv["expires_at"] < now() or inv["email"] != email:
        raise AppError("This invitation is invalid or expired.")
    claim = await db.invites.update_one({"id": inv["id"], "accepted_at": None}, {"$set": {"accepted_at": now()}})
    if not claim.modified_count:
        raise AppError("Invitation already used.")
    return inv


@router.get("/invite/{token}")
async def invite_info(token: str):
    inv = await db.invites.find_one({"token_hash": sha(token)}, {"_id": 0})
    if not inv or inv.get("accepted_at") or inv["expires_at"] < now():
        raise AppError("This invitation is invalid or expired.", 404)
    t = await db.tenants.find_one({"id": inv["tenant_id"]})
    return {"email": inv["email"], "tenant_name": t["name"], "role": inv.get("role", "staff")}


@router.post("/invite/accept")
async def invite_accept(d: InviteAcceptIn, response: Response):
    email = d.email.lower()
    if await db.users.find_one({"email": email}):
        raise AppError("An account with this email already exists.", 409)
    inv = await _claim_invite(d.token, email)
    user = {"id": new_id(), "email": email, "name": d.name, "password_hash": hash_password(d.password),
            "google_sub": None, "role": inv.get("role", "staff"), "tenant_id": inv["tenant_id"], "created_at": now()}
    await db.users.insert_one(dict(user))
    await audit(inv["tenant_id"], user["id"], "invite.accepted", inv["id"], {"email": email})
    await create_session(user["id"], response)
    await audit(user["tenant_id"], user["id"], "auth.login", user["id"], {"method": "invite", "role": user["role"]})
    return {"ok": True}


class AccessDeniedIn(BaseModel):
    path: str = Field(max_length=300)


@router.post("/access-denied")
async def access_denied(d: AccessDeniedIn, ctx: Ctx = Depends(get_ctx)):
    await audit(ctx.tenant_id, ctx.user["id"], "access.denied", d.path, {"role": ctx.role, "path": d.path, "via": "page", "outcome": "denied"})
    return {"ok": True}


class ForgotIn(BaseModel):
    email: str = Field(min_length=3, max_length=200)


@router.post("/forgot-password")
async def forgot_password(d: ForgotIn, request: Request):
    email = d.email.lower().strip()
    await rate_limit("forgot:" + (request.client.host if request.client else "x"), 10, 3600)
    await rate_limit("forgot-email:" + sha(email), 3, 3600)
    user = await db.users.find_one({"email": email})
    if user:
        raw = secrets.token_urlsafe(32)
        await db.password_reset_tokens.insert_one({"id": sha(raw), "user_id": user["id"], "used": False,
                                                   "expires_at": now() + timedelta(hours=1), "created_at": now()})
        await send_email(to=email, subject="Reset your RevLoop password", html=action_email(
            user["name"], "We received a request to reset your RevLoop password.", "Choose a new password",
            f"{origin()}/reset-password?token={raw}", "This link works once and expires in 1 hour. If you did not ask for this, ignore this email."))
        await audit(user["tenant_id"], user["id"], "auth.password-reset.requested", user["id"], {})
    return {"ok": True, "message": "If an account exists for that email, a reset link has been sent."}


class ResetIn(BaseModel):
    token: str = Field(min_length=20)
    password: str = Field(min_length=12, max_length=128)


@router.post("/reset-password")
async def reset_password(d: ResetIn):
    t = await db.password_reset_tokens.find_one_and_update(
        {"id": sha(d.token), "used": False, "expires_at": {"$gt": now()}}, {"$set": {"used": True, "used_at": now()}})
    if not t:
        raise AppError("This reset link is invalid or has expired. Request a new one.")
    user = await db.users.find_one({"id": t["user_id"]})
    await db.users.update_one({"id": user["id"]}, {"$set": {"password_hash": hash_password(d.password)}})
    await db.sessions.delete_many({"user_id": user["id"]})
    await audit(user["tenant_id"], user["id"], "auth.password-reset.completed", user["id"], {"sessions_revoked": True})
    return {"ok": True}


@router.post("/google")
async def google_session(d: GoogleIn, response: Response):
    async with httpx.AsyncClient(timeout=15) as c:
        r = await c.get("https://demobackend.emergentagent.com/auth/v1/env/oauth/session-data",
                        headers={"X-Session-ID": d.session_id})
    if r.status_code != 200:
        raise AppError("Google sign-in failed. Please try again.", 401)
    g = r.json()
    email = g["email"].lower()
    user = await db.users.find_one({"email": email}, {"_id": 0})
    if not user and d.invite_token:
        inv = await _claim_invite(d.invite_token, email)
        user = {"id": new_id(), "email": email, "name": g.get("name") or email, "password_hash": None,
                "google_sub": g.get("id"), "role": inv.get("role", "staff"), "tenant_id": inv["tenant_id"], "created_at": now()}
        await db.users.insert_one(dict(user))
    elif not user:
        name = g.get("name") or email.split("@")[0]
        user, _ = await create_tenant(email, name, f"{name}'s Garage", google_sub=g.get("id"))
    elif not user.get("google_sub"):
        await db.users.update_one({"id": user["id"]}, {"$set": {"google_sub": g.get("id")}})
    await create_session(user["id"], response)
    await audit(user["tenant_id"], user["id"], "auth.login", user["id"], {"method": "google", "role": user["role"]})
    return {"ok": True}
