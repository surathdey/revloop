import os
import asyncio
import logging
import csv
import io
import json
import hmac
import re
from datetime import timedelta
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse, RedirectResponse
from html import escape
from pydantic import BaseModel, Field
from typing import Optional, List
from pymongo.errors import DuplicateKeyError
from emergentintegrations.llm.chat import LlmChat, UserMessage
from core import db, now, new_id, origin, AppError, audit, platform_config
from policy import normalize_phone
from emailer import send_email
from messaging import _twilio
from auth import Ctx, super_ctx

router = APIRouter(prefix="/api")
STAGES = ["new", "contacted", "interested", "demo_booked", "won", "lost", "do_not_call"]
OUTCOME_STAGE = {"interested": "interested", "demo_booked": "demo_booked", "callback": "contacted", "not_interested": "lost",
                 "do_not_call": "do_not_call", "no_answer": "contacted", "voicemail": "contacted"}
TZ = ZoneInfo("America/Toronto")
DEFAULT_SCRIPT = {
    "first_message": "Hi, this is Riley, an AI assistant calling on behalf of RevLoop in Toronto. This call is recorded for quality. "
                     "If you'd prefer not to get calls from us, just say so and we'll remove you right away. Is this the owner of {business_name}?",
    "system_prompt": "You are Riley, a friendly, concise sales caller for RevLoop, software that fills service bays for independent GTA garages "
                     "with online booking, automated SMS reminders and Google review requests. Goal: book a 15-minute demo. Handle objections "
                     "(price, too busy, already have software) briefly and honestly. Never pressure. If the person asks not to be called, "
                     "asks to be removed, or says stop: apologise, confirm they will not be called again, and end the call immediately.",
    "opt_out_phrases": "do not call,don't call,stop calling,remove me,take me off,not interested in calls,unsubscribe",
}
GUARDRAILS = ("\n\nPLATFORM RULES (always apply, override anything above): You cannot send anything during the call and you cannot create calendar invites. "
              "Right after the call RevLoop automatically texts this phone number a short summary, and emails it too if the person gives an email address. "
              "If they want details or a demo, ask for their email, spell it back to confirm, and tell them they will get a text and an email with the details shortly after the call. "
              "For a demo, collect a preferred day and time and say a RevLoop team member will confirm it. Never say the demo is booked or confirmed. "
              "Never promise anything else (calendar invites, specific people calling, prices or discounts not stated above).")
EMAIL_RX = re.compile(r"^[^@\s]+@[^@\s]+\.[a-z]{2,}$", re.I)


async def sales_audit(action, entity_id, detail, actor="ai-caller"):
    t = await db.tenants.find_one({"is_platform": True}, {"id": 1})
    await audit(t["id"] if t else None, actor, action, entity_id, detail)


async def stage_change(p, to, by, reason=""):
    if p["stage"] == to or (p["stage"] == "do_not_call" and to != "do_not_call"):
        return
    await db.prospects.update_one({"id": p["id"]}, {"$set": {"stage": to, "updated_at": now()}})
    await db.stage_history.insert_one({"id": new_id(), "prospect_id": p["id"], "from": p["stage"], "to": to, "by": by, "reason": reason, "at": now()})


async def add_dnc(phone, reason, source):
    try:
        await db.dnc.insert_one({"id": new_id(), "phone": phone, "reason": reason, "source": source, "created_at": now()})
    except DuplicateKeyError:
        pass
    for p in await db.prospects.find({"phone": phone}, {"_id": 0}).to_list(10):
        await db.prospects.update_one({"id": p["id"]}, {"$set": {"dnc": True}})
        await stage_change(p, "do_not_call", source, reason)


async def get_script():
    s = await db.sales_scripts.find_one({"id": "default"}, {"_id": 0})
    return s or {"id": "default", **DEFAULT_SCRIPT}


# ---------- prospects ----------

class ImportIn(BaseModel):
    csv: str


@router.post("/sales/prospects/import")
async def import_prospects(d: ImportIn, ctx: Ctx = Depends(super_ctx)):
    rows = [{(k or "").strip().lower(): (v or "").strip() for k, v in r.items()} for r in csv.DictReader(io.StringIO(d.csv.lstrip("\ufeff")))]
    if len(rows) > 5000:
        raise AppError("Import up to 5,000 prospects at a time.")
    have = {p["phone"] async for p in db.prospects.find({}, {"phone": 1})}
    dnc = {x["phone"] async for x in db.dnc.find({}, {"phone": 1})}
    res, docs = {"created": 0, "duplicates": 0, "suppressed": 0, "errors": []}, []
    for i, r in enumerate(rows):
        try:
            phone = normalize_phone(r.get("phone", ""))
        except AppError as e:
            res["errors"].append({"row": i + 2, "message": e.detail})
            continue
        if phone in dnc:
            res["suppressed"] += 1
            continue
        if phone in have:
            res["duplicates"] += 1
            continue
        have.add(phone)
        docs.append({"id": new_id(), "business_name": r.get("business_name") or r.get("business") or r.get("name") or phone,
                     "contact_name": r.get("contact_name") or r.get("contact", ""), "phone": phone, "email": r.get("email", ""),
                     "city": r.get("city", ""), "website": r.get("website", ""), "notes": r.get("notes", ""), "stage": "new", "dnc": False,
                     "attempts": 0, "last_call_at": None, "next_call_at": None, "summary": "", "outcome": "", "created_at": now(), "updated_at": now()})
    if docs:
        await db.prospects.insert_many(docs)
    res["created"] = len(docs)
    await audit(ctx.tenant_id, ctx.user["id"], "sales.prospects.imported", "csv", {k: v if not isinstance(v, list) else len(v) for k, v in res.items()})
    return res


@router.get("/sales/prospects")
async def list_prospects(ctx: Ctx = Depends(super_ctx)):
    return await db.prospects.find({}, {"_id": 0}).sort("updated_at", -1).to_list(5000)


class ProspectIn(BaseModel):
    business_name: str = Field(min_length=2, max_length=120)
    contact_name: str = Field(default="", max_length=80)
    phone: str
    email: str = Field(default="", max_length=200)
    city: str = Field(default="", max_length=80)


@router.post("/sales/prospects")
async def add_prospect(d: ProspectIn, ctx: Ctx = Depends(super_ctx)):
    phone = normalize_phone(d.phone)
    if await db.dnc.find_one({"phone": phone}):
        raise AppError("This number is on the do-not-call list.", 409)
    p = {"id": new_id(), **d.model_dump(), "phone": phone, "website": "", "notes": "", "stage": "new", "dnc": False, "attempts": 0,
         "last_call_at": None, "next_call_at": None, "summary": "", "outcome": "", "created_at": now(), "updated_at": now()}
    try:
        await db.prospects.insert_one(dict(p))
    except DuplicateKeyError:
        raise AppError("A prospect with this phone already exists.", 409)
    await audit(ctx.tenant_id, ctx.user["id"], "sales.prospect.created", p["id"], {"business": d.business_name})
    return p


@router.get("/sales/prospects/{pid}")
async def get_prospect(pid: str, ctx: Ctx = Depends(super_ctx)):
    p = await db.prospects.find_one({"id": pid}, {"_id": 0})
    if not p:
        raise AppError("Prospect not found.", 404)
    p["calls"] = await db.call_logs.find({"prospect_id": pid}, {"_id": 0}).sort("created_at", -1).to_list(100)
    p["history"] = await db.stage_history.find({"prospect_id": pid}, {"_id": 0}).sort("at", -1).to_list(100)
    return p


class StageIn(BaseModel):
    stage: str
    reason: str = ""


@router.post("/sales/prospects/{pid}/stage")
async def set_stage(pid: str, d: StageIn, ctx: Ctx = Depends(super_ctx)):
    p = await db.prospects.find_one({"id": pid}, {"_id": 0})
    if not p or d.stage not in STAGES:
        raise AppError("Invalid prospect or stage.")
    if p["stage"] == "do_not_call":
        raise AppError("Do-not-call is permanent.")
    if d.stage == "do_not_call":
        await add_dnc(p["phone"], d.reason or "Marked by sales team", ctx.user["id"])
    else:
        await stage_change(p, d.stage, ctx.user["id"], d.reason)
    return {"ok": True}


# ---------- DNC ----------

class DncIn(BaseModel):
    phones: str = Field(min_length=10)
    reason: str = "Manual / National DNCL list"


@router.get("/sales/dnc")
async def list_dnc(ctx: Ctx = Depends(super_ctx)):
    return await db.dnc.find({}, {"_id": 0}).sort("created_at", -1).to_list(20000)


@router.post("/sales/dnc")
async def post_dnc(d: DncIn, ctx: Ctx = Depends(super_ctx)):
    n, bad = 0, 0
    for raw in re.split(r"[\s,;]+", d.phones):
        if not raw:
            continue
        try:
            await add_dnc(normalize_phone(raw), d.reason, ctx.user["id"])
            n += 1
        except AppError:
            bad += 1
    await audit(ctx.tenant_id, ctx.user["id"], "sales.dnc.added", "dnc", {"count": n})
    return {"added": n, "invalid": bad}


@router.get("/sales/calls/export")
async def export_calls(ctx: Ctx = Depends(super_ctx)):
    rows = await db.call_logs.find({}, {"_id": 0}).sort("created_at", -1).to_list(100000)
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["call_id", "created_at", "prospect", "phone", "campaign_id", "status", "outcome", "opt_out", "duration_s", "recording_url", "summary"])
    for c in rows:
        w.writerow([c["id"], c["created_at"].isoformat(), c.get("business_name", ""), c["phone"], c.get("campaign_id", ""), c["status"],
                    c.get("outcome", ""), c.get("opt_out", False), c.get("duration_s", ""), c.get("recording_url", ""), c.get("summary", "")])
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=call-attempts.csv"})


# ---------- script ----------

class ScriptIn(BaseModel):
    first_message: str = Field(min_length=20, max_length=1000)
    system_prompt: str = Field(min_length=20, max_length=8000)
    opt_out_phrases: str = Field(default="", max_length=1000)


@router.get("/sales/script")
async def read_script(ctx: Ctx = Depends(super_ctx)):
    return await get_script()


@router.put("/sales/script")
async def save_script(d: ScriptIn, ctx: Ctx = Depends(super_ctx)):
    low = d.first_message.lower()
    if "revloop" not in low or "record" not in low:
        raise AppError("The opening line must identify RevLoop and state that the call is recorded.")
    await db.sales_scripts.update_one({"id": "default"}, {"$set": {**d.model_dump(), "updated_at": now()}}, upsert=True)
    await audit(ctx.tenant_id, ctx.user["id"], "sales.script.updated", "default", {})
    return {"ok": True}


# ---------- campaigns ----------

class CampaignIn(BaseModel):
    name: str = Field(min_length=2, max_length=80)
    window_start: int = Field(default=10, ge=9, le=20)
    window_end: int = Field(default=17, ge=10, le=21)
    days: List[int] = [0, 1, 2, 3, 4]
    max_attempts: int = Field(default=3, ge=1, le=10)
    retry_hours: int = Field(default=48, ge=1, le=720)
    max_per_tick: int = Field(default=5, ge=1, le=50)
    prospect_ids: Optional[List[str]] = None


@router.get("/sales/campaigns")
async def campaigns(ctx: Ctx = Depends(super_ctx)):
    rows = await db.campaigns.find({}, {"_id": 0}).sort("created_at", -1).to_list(200)
    for c in rows:
        logs = await db.call_logs.find({"campaign_id": c["id"]}, {"outcome": 1, "status": 1}).to_list(100000)
        c["stats"] = {"calls": len(logs), "prospects": len(c.get("prospect_ids") or []),
                      **{o: sum(1 for x in logs if x.get("outcome") == o) for o in OUTCOME_STAGE}}
    return rows


@router.post("/sales/campaigns")
async def create_campaign(d: CampaignIn, ctx: Ctx = Depends(super_ctx)):
    if d.window_start >= d.window_end:
        raise AppError("Calling window start must be before end.")
    ids = d.prospect_ids or [p["id"] async for p in db.prospects.find({"stage": "new", "dnc": False}, {"id": 1})]
    c = {"id": new_id(), **d.model_dump(), "prospect_ids": ids, "status": "draft", "created_at": now()}
    await db.campaigns.insert_one(dict(c))
    c.pop("_id", None)
    return c


class CampaignStatusIn(BaseModel):
    status: str


@router.post("/sales/campaigns/{cid}/status")
async def campaign_status(cid: str, d: CampaignStatusIn, ctx: Ctx = Depends(super_ctx)):
    if d.status not in ("running", "paused"):
        raise AppError("Status must be running or paused.")
    if d.status == "running":
        cfg = await platform_config()
        if not (cfg.get("VAPI_API_KEY") and cfg.get("VAPI_PHONE_NUMBER_ID") and cfg.get("VAPI_WEBHOOK_SECRET")):
            raise AppError("Vapi is not configured yet (API key, phone number ID, webhook secret). Add them in Platform admin → Integrations.", 503)
    await db.campaigns.update_one({"id": cid}, {"$set": {"status": d.status}})
    await audit(ctx.tenant_id, ctx.user["id"], f"sales.campaign.{d.status}", cid, {})
    return {"ok": True}


def in_window(c, at):
    local = at.astimezone(TZ)
    wd = local.weekday()
    # CRTC telemarketing hours: weekdays 9:00-21:30, weekends 10:00-18:00
    lo, hi = (9, 21.5) if wd < 5 else (10, 18)
    h = local.hour + local.minute / 60
    return wd in c["days"] and max(lo, c["window_start"]) <= h < min(hi, c["window_end"])


UUID_RX = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.I)


async def resolve_vapi(cfg):
    """Accept either Vapi IDs or the human values (phone number / credential name) and resolve them to IDs."""
    phone_ref, cred_ref = cfg["VAPI_PHONE_NUMBER_ID"].strip(), (cfg.get("VAPI_SERVER_CREDENTIAL_ID") or "").strip()
    h = {"Authorization": f"Bearer {cfg['VAPI_API_KEY']}"}
    async with httpx.AsyncClient(timeout=20) as cl:
        if not UUID_RX.match(phone_ref):
            digits = re.sub(r"\D", "", phone_ref)
            nums = (await cl.get("https://api.vapi.ai/phone-number", headers=h)).json()
            match = next((n for n in nums if re.sub(r"\D", "", n.get("number") or "") in (digits, "1" + digits) or n.get("name") == phone_ref), None)
            if not match:
                raise AppError(f"Vapi has no phone number matching '{phone_ref}'. Use the phone number's ID from Vapi → Phone Numbers.", 400)
            phone_ref = match["id"]
        if cred_ref and not UUID_RX.match(cred_ref):
            creds = (await cl.get("https://api.vapi.ai/credential", headers=h)).json()
            match = next((c for c in creds if c.get("name") == cred_ref), None)
            if not match:
                raise AppError(f"Vapi has no credential named '{cred_ref}'. Use the credential's ID from Vapi → Integrations.", 400)
            cred_ref = match["id"]
    return phone_ref, cred_ref


async def dial(p, campaign_id, cfg):
    if p.get("dnc") or await db.dnc.find_one({"phone": p["phone"]}):
        raise AppError("Number is on the do-not-call list.", 409)
    script = await get_script()
    log = {"id": new_id(), "prospect_id": p["id"], "business_name": p["business_name"], "phone": p["phone"], "campaign_id": campaign_id,
           "status": "dialing", "dncl_screened": True, "outcome": "", "summary": "", "transcript": "", "recording_url": "", "opt_out": False, "created_at": now()}
    await db.call_logs.insert_one(dict(log))
    first = script["first_message"].replace("{business_name}", p["business_name"])
    try:
        phone_id, cred_id = await resolve_vapi(cfg)
    except AppError as e:
        await db.call_logs.update_one({"id": log["id"]}, {"$set": {"status": "failed", "error": e.detail}})
        await sales_audit("sales.call.failed", log["id"], {"prospect": p["business_name"], "phone": p["phone"], "error": e.detail})
        raise
    body = {"phoneNumberId": phone_id, "customer": {"number": p["phone"], "name": p["business_name"][:40]},
            "metadata": {"call_log_id": log["id"], "prospect_id": p["id"]},
            "assistant": {"firstMessage": first, "model": {"provider": "openai", "model": "gpt-4o", "messages": [{"role": "system", "content": script["system_prompt"] + GUARDRAILS}]},
                          "voice": {"provider": "11labs", "voiceId": cfg.get("ELEVENLABS_VOICE_ID") or "burt"},
                          "artifactPlan": {"recordingEnabled": True},
                          "server": ({"url": f"{origin()}/api/webhooks/vapi", "credentialId": cred_id}
                                     if cred_id else
                                     {"url": f"{origin()}/api/webhooks/vapi", "secret": cfg["VAPI_WEBHOOK_SECRET"]})}}
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.post("https://api.vapi.ai/call", json=body, headers={"Authorization": f"Bearer {cfg['VAPI_API_KEY']}"})
        if r.status_code >= 300:
            raise RuntimeError(f"Vapi {r.status_code}: {r.text[:300]}")
        await db.call_logs.update_one({"id": log["id"]}, {"$set": {"vapi_call_id": r.json().get("id"), "status": "queued"}})
        await sales_audit("sales.call.placed", log["id"], {"prospect": p["business_name"], "phone": p["phone"], "campaign_id": campaign_id})
    except Exception as e:
        await db.call_logs.update_one({"id": log["id"]}, {"$set": {"status": "failed", "error": str(e)[:400]}})
        await sales_audit("sales.call.failed", log["id"], {"prospect": p["business_name"], "phone": p["phone"], "error": str(e)[:200]})
        if campaign_id is None:
            raise AppError(f"Call failed: {str(e)[:300]}", 502)
    await db.prospects.update_one({"id": p["id"]}, {"$inc": {"attempts": 1}, "$set": {"last_call_at": now(), "updated_at": now()}})
    await stage_change(p, "contacted", "ai-caller") if p["stage"] == "new" else None
    return log["id"]


async def run_campaigns():
    cfg = await platform_config()
    if not (cfg.get("VAPI_API_KEY") and cfg.get("VAPI_PHONE_NUMBER_ID") and cfg.get("VAPI_WEBHOOK_SECRET")):
        return 0
    n = 0
    for c in await db.campaigns.find({"status": "running"}, {"_id": 0}).to_list(50):
        if not in_window(c, now()):
            continue
        q = {"id": {"$in": c["prospect_ids"]}, "dnc": False, "stage": {"$in": ["new", "contacted"]}, "attempts": {"$lt": c["max_attempts"]},
             "$or": [{"next_call_at": None}, {"next_call_at": {"$lte": now()}}]}
        for p in await db.prospects.find(q, {"_id": 0}).limit(c["max_per_tick"]).to_list(c["max_per_tick"]):
            await db.prospects.update_one({"id": p["id"]}, {"$set": {"next_call_at": now() + timedelta(hours=c["retry_hours"])}})
            await dial(p, c["id"], cfg)
            n += 1
    return n


class TestCallIn(BaseModel):
    phone: str
    business_name: str = "Test Garage"


@router.post("/sales/test-call")
async def test_call(d: TestCallIn, ctx: Ctx = Depends(super_ctx)):
    cfg = await platform_config()
    if not (cfg.get("VAPI_API_KEY") and cfg.get("VAPI_PHONE_NUMBER_ID") and cfg.get("VAPI_WEBHOOK_SECRET")):
        raise AppError("Vapi is not configured yet. Add it in Platform admin → Integrations.", 503)
    phone = normalize_phone(d.phone)
    p = await db.prospects.find_one({"phone": phone}, {"_id": 0})
    if not p:
        p = {"id": new_id(), "business_name": d.business_name, "contact_name": "", "phone": phone, "email": "", "city": "", "website": "", "notes": "test call",
             "stage": "new", "dnc": False, "attempts": 0, "last_call_at": None, "next_call_at": None, "summary": "", "outcome": "", "created_at": now(), "updated_at": now()}
        await db.prospects.insert_one(dict(p))
    return {"call_log_id": await dial(p, None, cfg)}


# ---------- call results ----------

async def summarize(transcript: str, opt_phrases: str):
    chat = LlmChat(api_key=os.environ["EMERGENT_LLM_KEY"], session_id=f"call-{new_id()}",
                   system_message="You analyse sales call transcripts for RevLoop (garage software). Reply ONLY with JSON: "
                                  '{"summary": "2-3 sentences", "outcome": one of ["interested","demo_booked","callback","not_interested","do_not_call","no_answer","voicemail"], '
                                  '"opt_out": true if the prospect asked not to be called again, "email": the email address the prospect gave (fix spelled-out forms like \\"john at gmail dot com\\") or "", '
                                  '"wants_followup": true if they asked for details, a demo, a text or an email, "demo_time": preferred demo day/time they gave or ""}. '
                                  'In the summary never say a demo is booked/scheduled/confirmed - say "demo requested" with the preferred time.').with_model("openai", os.environ["SALES_LLM_MODEL"])
    raw = await chat.send_message(UserMessage(text=transcript[:30000] or "(no transcript - call not answered)"))
    m = re.search(r"\{.*\}", raw if isinstance(raw, str) else str(raw), re.S)
    out = json.loads(m.group(0)) if m else {"summary": str(raw)[:500], "outcome": "callback", "opt_out": False}
    low = transcript.lower()
    if any(ph.strip() and ph.strip() in low for ph in opt_phrases.split(",")):
        out["opt_out"] = True
    if out.get("opt_out"):
        out["outcome"] = "do_not_call"
    if out.get("outcome") not in OUTCOME_STAGE:
        out["outcome"] = "callback"
    return out


async def sms_sender(cfg):
    if cfg.get("SALES_SMS_FROM"):
        return cfg["SALES_SMS_FROM"].strip()
    phone_id, _ = await resolve_vapi(cfg)
    async with httpx.AsyncClient(timeout=20) as cl:
        n = (await cl.get(f"https://api.vapi.ai/phone-number/{phone_id}", headers={"Authorization": f"Bearer {cfg['VAPI_API_KEY']}"})).json()
    return n.get("number") if n.get("provider") == "twilio" else None


async def _followup_sms(p, demo_time, simulated):
    body = (f"Hi from RevLoop! Thanks for chatting with Riley today. {'Demo requested for ' + demo_time + '. ' if demo_time else ''}"
            "A RevLoop team member will confirm the details with you shortly. Questions? Just reply to this text. Reply STOP to opt out.")
    out = {"channel": "sms", "to": p["phone"], "body": body, "at": now()}
    cfg = await platform_config()
    if simulated or cfg.get("SMS_MODE") != "live":
        return {**out, "status": "simulated"}
    try:
        sender = await sms_sender(cfg)
        if not sender or not cfg.get("TWILIO_ACCOUNT_SID"):
            return {**out, "status": "skipped", "error": "No sales SMS number - set SALES_SMS_FROM in Platform admin → Integrations."}
        msg = await asyncio.to_thread(lambda: _twilio(cfg).messages.create(to=p["phone"], from_=sender, body=body))
        return {**out, "status": "sent", "from": sender, "sid": msg.sid}
    except Exception as e:
        return {**out, "status": "failed", "error": str(e)[:300]}


async def _followup_email(p, email, summary, demo_time, simulated):
    out = {"channel": "email", "to": email, "at": now()}
    if simulated:
        return {**out, "status": "simulated"}
    name = p.get("contact_name") or p["business_name"]
    html = (f'<table role="presentation" width="100%"><tr><td style="padding:24px;font-family:Arial,sans-serif;color:#111">'
            f'<p>Hi {escape(name)},</p><p>Thanks for speaking with Riley from RevLoop today. Here is a recap of the call:</p>'
            f'<p style="background:#f4f4f5;padding:12px;border-radius:6px">{escape(summary)}</p>'
            + (f'<p><b>Demo requested:</b> {escape(demo_time)}. A RevLoop team member will confirm the time with you shortly.</p>' if demo_time else
               '<p>A RevLoop team member will follow up with you shortly.</p>')
            + '<p>RevLoop fills service bays for independent garages with online booking, automated SMS reminders and Google review requests.</p>'
            '<p style="font-size:12px;color:#888">You received this because you asked for details during a call with RevLoop. Reply to let us know if you do not want further emails.</p></td></tr></table>')
    try:
        await send_email(to=email, subject="Your RevLoop call recap" + (" & demo request" if demo_time else ""), html=html)
        return {**out, "status": "sent"}
    except Exception as e:
        return {**out, "status": "failed", "error": str(getattr(e, "detail", e))[:300]}


async def send_followups(log, p, r, simulated):
    if r.get("opt_out") or not (r.get("wants_followup") or r["outcome"] in ("interested", "demo_booked")):
        return []
    demo_time = (r.get("demo_time") or "").strip()[:80]
    email = (r.get("email") or "").strip().lower()
    email = email if EMAIL_RX.match(email) else (p.get("email") or "")
    if email and not p.get("email"):
        await db.prospects.update_one({"id": p["id"]}, {"$set": {"email": email}})
    res = [await _followup_sms(p, demo_time, simulated)]
    if email:
        res.append(await _followup_email(p, email, r["summary"], demo_time, simulated))
    else:
        res.append({"channel": "email", "to": "", "status": "skipped", "error": "No email address given on the call.", "at": now()})
    for f in res:
        await sales_audit(f"sales.followup.{f['channel']}", log["id"], {"prospect": p["business_name"], "to": f["to"], "status": f["status"], "error": f.get("error", "")})
    return res


async def finish_call(log, transcript, recording_url="", duration=None, ended_reason=""):
    script = await get_script()
    r = await summarize(transcript, script.get("opt_out_phrases", ""))
    await db.call_logs.update_one({"id": log["id"]}, {"$set": {"status": "completed", "transcript": transcript, "recording_url": recording_url,
                                  "duration_s": duration, "ended_reason": ended_reason, "summary": r["summary"], "outcome": r["outcome"],
                                  "opt_out": bool(r.get("opt_out")), "ended_at": now()}})
    p = await db.prospects.find_one({"id": log["prospect_id"]}, {"_id": 0})
    await sales_audit("sales.call.completed", log["id"], {"prospect": log.get("business_name"), "phone": log.get("phone"), "outcome": r["outcome"],
                                                       "duration_s": duration, "ended_reason": ended_reason, "simulated": bool(log.get("simulated"))})
    if not p:
        return r
    await db.prospects.update_one({"id": p["id"]}, {"$set": {"summary": r["summary"], "outcome": r["outcome"], "updated_at": now()}})
    if r["outcome"] == "do_not_call":
        await add_dnc(p["phone"], "Opted out during AI call", "ai-caller")
    else:
        await stage_change(p, OUTCOME_STAGE[r["outcome"]], "ai-caller", r["summary"][:200])
    r["followups"] = await send_followups(log, p, r, bool(log.get("simulated")))
    await db.call_logs.update_one({"id": log["id"]}, {"$set": {"followups": r["followups"]}})
    return r


class SimIn(BaseModel):
    transcript: str = Field(min_length=5, max_length=30000)


@router.post("/sales/prospects/{pid}/simulate-call")
async def simulate_call(pid: str, d: SimIn, ctx: Ctx = Depends(super_ctx)):
    p = await db.prospects.find_one({"id": pid}, {"_id": 0})
    if not p:
        raise AppError("Prospect not found.", 404)
    if p.get("dnc"):
        raise AppError("Number is on the do-not-call list.", 409)
    log = {"id": new_id(), "prospect_id": pid, "business_name": p["business_name"], "phone": p["phone"], "campaign_id": None, "status": "simulated",
           "dncl_screened": True, "simulated": True, "created_at": now()}
    await db.call_logs.insert_one(dict(log))
    await db.prospects.update_one({"id": pid}, {"$inc": {"attempts": 1}, "$set": {"last_call_at": now()}})
    return await finish_call(log, d.transcript, ended_reason="simulated")


async def sync_call(log):
    """Pull a call's final result from Vapi's API (fallback when the end-of-call webhook did not arrive)."""
    cfg = await platform_config()
    async with httpx.AsyncClient(timeout=20) as cl:
        r = await cl.get(f"https://api.vapi.ai/call/{log['vapi_call_id']}", headers={"Authorization": f"Bearer {cfg['VAPI_API_KEY']}"})
    if r.status_code >= 300:
        raise AppError(f"Vapi {r.status_code}: {r.text[:200]}", 502)
    call = r.json()
    if call.get("status") != "ended":
        return {"status": call.get("status")}
    try:
        await db.webhook_events.insert_one({"id": f"vapi:{call['id']}:eocr", "provider": "vapi", "created_at": now()})
    except DuplicateKeyError:
        return {"status": "already processed"}
    art = call.get("artifact") or {}
    dur = None
    if call.get("startedAt") and call.get("endedAt"):
        from datetime import datetime
        dur = round((datetime.fromisoformat(call["endedAt"].replace("Z", "+00:00")) - datetime.fromisoformat(call["startedAt"].replace("Z", "+00:00"))).total_seconds())
    return await finish_call(log, art.get("transcript") or call.get("transcript") or "", art.get("recordingUrl") or call.get("recordingUrl") or "",
                             dur, call.get("endedReason", ""))


async def sync_stale_calls():
    for log in await db.call_logs.find({"status": "queued", "vapi_call_id": {"$ne": None}, "created_at": {"$lt": now() - timedelta(minutes=10)}}, {"_id": 0}).to_list(50):
        try:
            await sync_call(log)
        except Exception as e:
            logging.getLogger("vapi").warning("Call sync failed for %s: %s", log["id"], type(e).__name__)


@router.get("/sales/calls/{log_id}/recording")
async def call_recording(log_id: str, ctx: Ctx = Depends(super_ctx)):
    log = await db.call_logs.find_one({"id": log_id}, {"_id": 0})
    if not log or not log.get("vapi_call_id"):
        raise AppError("Recording not found.", 404)
    cfg = await platform_config()
    async with httpx.AsyncClient(timeout=20) as cl:
        r = await cl.get(f"https://api.vapi.ai/call/{log['vapi_call_id']}", headers={"Authorization": f"Bearer {cfg['VAPI_API_KEY']}"})
    if r.status_code >= 300:
        raise AppError(f"Vapi {r.status_code}: {r.text[:200]}", 502)
    art = r.json().get("artifact") or {}
    url = art.get("presignedMonoUrl") or art.get("presignedStereoUrl")
    if not url:
        raise AppError("Vapi has no recording for this call.", 404)
    return RedirectResponse(url, status_code=307)


@router.post("/sales/calls/{log_id}/sync")
async def sync_call_route(log_id: str, ctx: Ctx = Depends(super_ctx)):
    log = await db.call_logs.find_one({"id": log_id}, {"_id": 0})
    if not log or not log.get("vapi_call_id"):
        raise AppError("Call not found.", 404)
    return await sync_call(log)


@router.post("/webhooks/vapi")
async def vapi_webhook(request: Request):
    cfg = await platform_config()
    secret = cfg.get("VAPI_WEBHOOK_SECRET", "")
    auth = request.headers.get("authorization", "")
    provided = request.headers.get("x-vapi-secret", "") or (auth[7:] if auth.lower().startswith("bearer ") else auth)
    if not secret or not hmac.compare_digest(provided.strip(), secret.strip()):
        logging.getLogger("vapi").warning("Vapi webhook rejected: x-vapi-secret=%s authorization=%s secret_len=%s provided_len=%s",
                                          "x-vapi-secret" in request.headers, "authorization" in request.headers, len(secret), len(provided))
        raise AppError("Invalid Vapi signature.", 403)
    msg = (await request.json()).get("message", {})
    call = msg.get("call") or {}
    if msg.get("type") != "end-of-call-report":
        return {"ok": True}
    try:
        await db.webhook_events.insert_one({"id": f"vapi:{call.get('id')}:eocr", "provider": "vapi", "created_at": now()})
    except DuplicateKeyError:
        return {"ok": True}
    log = await db.call_logs.find_one({"$or": [{"vapi_call_id": call.get("id")}, {"id": (call.get("metadata") or {}).get("call_log_id")}]}, {"_id": 0})
    if not log:
        return {"ok": True}
    art = msg.get("artifact") or {}
    await finish_call(log, art.get("transcript") or msg.get("transcript") or "", art.get("recordingUrl") or msg.get("recordingUrl") or "",
                      msg.get("durationSeconds"), msg.get("endedReason", ""))
    return {"ok": True}


@router.get("/sales/analytics")
async def analytics(ctx: Ctx = Depends(super_ctx)):
    stages = {s: await db.prospects.count_documents({"stage": s}) for s in STAGES}
    logs = await db.call_logs.find({}, {"outcome": 1}).to_list(100000)
    outcomes = {o: sum(1 for x in logs if x.get("outcome") == o) for o in OUTCOME_STAGE}
    return {"stages": stages, "outcomes": outcomes, "calls": len(logs), "dnc": await db.dnc.count_documents({})}
