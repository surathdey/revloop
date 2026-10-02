import os
import csv
import io
import json
import hmac
import re
from datetime import timedelta
from zoneinfo import ZoneInfo
import httpx
from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from typing import Optional, List
from pymongo.errors import DuplicateKeyError
from emergentintegrations.llm.chat import LlmChat, UserMessage
from core import db, now, new_id, origin, AppError, audit, platform_config
from policy import normalize_phone
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


async def dial(p, campaign_id, cfg):
    if p.get("dnc") or await db.dnc.find_one({"phone": p["phone"]}):
        raise AppError("Number is on the do-not-call list.", 409)
    script = await get_script()
    log = {"id": new_id(), "prospect_id": p["id"], "business_name": p["business_name"], "phone": p["phone"], "campaign_id": campaign_id,
           "status": "dialing", "dncl_screened": True, "outcome": "", "summary": "", "transcript": "", "recording_url": "", "opt_out": False, "created_at": now()}
    await db.call_logs.insert_one(dict(log))
    first = script["first_message"].replace("{business_name}", p["business_name"])
    body = {"phoneNumberId": cfg["VAPI_PHONE_NUMBER_ID"], "customer": {"number": p["phone"], "name": p["business_name"][:40]},
            "metadata": {"call_log_id": log["id"], "prospect_id": p["id"]},
            "assistant": {"firstMessage": first, "model": {"provider": "openai", "model": "gpt-4o", "messages": [{"role": "system", "content": script["system_prompt"]}]},
                          "voice": {"provider": "11labs", "voiceId": cfg.get("ELEVENLABS_VOICE_ID") or "burt"},
                          "artifactPlan": {"recordingEnabled": True},
                          "server": ({"url": f"{origin()}/api/webhooks/vapi", "credentialId": cfg["VAPI_SERVER_CREDENTIAL_ID"]}
                                     if cfg.get("VAPI_SERVER_CREDENTIAL_ID") else
                                     {"url": f"{origin()}/api/webhooks/vapi", "secret": cfg["VAPI_WEBHOOK_SECRET"]})}}
    try:
        async with httpx.AsyncClient(timeout=20) as cl:
            r = await cl.post("https://api.vapi.ai/call", json=body, headers={"Authorization": f"Bearer {cfg['VAPI_API_KEY']}"})
        r.raise_for_status()
        await db.call_logs.update_one({"id": log["id"]}, {"$set": {"vapi_call_id": r.json().get("id"), "status": "queued"}})
    except Exception as e:
        await db.call_logs.update_one({"id": log["id"]}, {"$set": {"status": "failed", "error": str(e)[:300]}})
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
                                  '"opt_out": true if the prospect asked not to be called again}').with_model("openai", os.environ["SALES_LLM_MODEL"])
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


async def finish_call(log, transcript, recording_url="", duration=None, ended_reason=""):
    script = await get_script()
    r = await summarize(transcript, script.get("opt_out_phrases", ""))
    await db.call_logs.update_one({"id": log["id"]}, {"$set": {"status": "completed", "transcript": transcript, "recording_url": recording_url,
                                  "duration_s": duration, "ended_reason": ended_reason, "summary": r["summary"], "outcome": r["outcome"],
                                  "opt_out": bool(r.get("opt_out")), "ended_at": now()}})
    p = await db.prospects.find_one({"id": log["prospect_id"]}, {"_id": 0})
    if not p:
        return r
    await db.prospects.update_one({"id": p["id"]}, {"$set": {"summary": r["summary"], "outcome": r["outcome"], "updated_at": now()}})
    if r["outcome"] == "do_not_call":
        await add_dnc(p["phone"], "Opted out during AI call", "ai-caller")
    else:
        await stage_change(p, OUTCOME_STAGE[r["outcome"]], "ai-caller", r["summary"][:200])
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


@router.post("/webhooks/vapi")
async def vapi_webhook(request: Request):
    cfg = await platform_config()
    secret = cfg.get("VAPI_WEBHOOK_SECRET", "")
    if not secret or not hmac.compare_digest(request.headers.get("x-vapi-secret", ""), secret):
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
