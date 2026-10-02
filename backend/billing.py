import os
import asyncio
from datetime import datetime, timedelta, timezone
import stripe
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field
from typing import Optional
from pymongo.errors import DuplicateKeyError
from core import db, now, AppError, audit, platform_config
from auth import Ctx, owner_ctx, super_ctx

router = APIRouter(prefix="/api")
stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
DEFAULT_PLANS = [
    {"key": "starter", "name": "Starter", "amount": 4900, "interval": "month", "quota": 300, "segments": 0},
    {"key": "growth", "name": "Growth", "amount": 9900, "interval": "month", "quota": 1000, "segments": 0},
    {"key": "pack", "name": "500 SMS pack", "amount": 1500, "interval": None, "quota": 0, "segments": 500},
]


def _s(fn, *a, **k):
    return asyncio.to_thread(fn, *a, **k)


async def sync_price(p: dict):
    """Ensure a Stripe product + CAD price exists for this plan; lookup_key follows the plan key."""
    prods = await _s(lambda: [x for x in stripe.Product.list(active=True, limit=100).auto_paging_iter()
                              if x.to_dict().get("metadata", {}).get("emergent_product_id") == f"revloop_{p['key']}"])
    prod = prods[0] if prods else await _s(stripe.Product.create, name=f"RevLoop {p['name']}", tax_code="txcd_10103001",
                                            metadata={"managed_by": "emergent", "emergent_product_id": f"revloop_{p['key']}"})
    if prods and prod.name != f"RevLoop {p['name']}":
        await _s(stripe.Product.modify, prod.id, name=f"RevLoop {p['name']}")
    lk = f"revloop_{p['key']}"
    ex = (await _s(stripe.Price.list, lookup_keys=[lk], active=True, limit=1)).data
    if ex and (ex[0].unit_amount != p["amount"] or ex[0].currency != "cad"):
        await _s(stripe.Price.modify, ex[0].id, active=False)
        ex = []
    if not ex:
        kw = dict(product=prod.id, unit_amount=p["amount"], currency="cad", lookup_key=lk, transfer_lookup_key=True)
        if p.get("interval"):
            kw["recurring"] = {"interval": p["interval"]}
        ex = [await _s(stripe.Price.create, **kw)]
    await db.plans.update_one({"key": p["key"]}, {"$set": {"stripe_price_id": ex[0].id}})


async def seed_plans():
    for p in DEFAULT_PLANS:
        await db.plans.update_one({"key": p["key"]}, {"$setOnInsert": {**p, "active": True, "created_at": now()}}, upsert=True)
    try:
        for p in await db.plans.find({}, {"_id": 0}).to_list(20):
            await sync_price(p)
    except stripe.error.StripeError:
        pass


async def plans():
    return await db.plans.find({}, {"_id": 0}).sort("amount", 1).to_list(20)


async def grace_days():
    return int((await platform_config()).get("BILLING_GRACE_DAYS") or 7)


async def apply_subscription(tenant_id, sub):
    t = await db.tenants.find_one({"id": tenant_id})
    price_id = sub["items"]["data"][0]["price"]["id"]
    plan = await db.plans.find_one({"stripe_price_id": price_id}) or await db.plans.find_one({"key": sub.get("metadata", {}).get("plan")})
    upd = {"stripe_subscription": sub["id"], "billing_status": sub["status"], "stripe_customer": sub["customer"]}
    if plan and plan.get("interval"):
        upd.update({"plan": plan["key"], "quota": plan["quota"]})
    if sub.get("trial_end"):
        upd["trial_ends"] = datetime.fromtimestamp(sub["trial_end"], timezone.utc)
    if sub["status"] == "past_due":
        upd["grace_until"] = t.get("grace_until") or now() + timedelta(days=await grace_days())
    elif sub["status"] in ("active", "trialing"):
        upd["grace_until"] = None
    if sub["status"] == "canceled":
        upd.update({"plan": "cancelled", "stripe_subscription": None})
    await db.tenants.update_one({"id": tenant_id}, {"$set": upd})
    await audit(tenant_id, "stripe", "billing.subscription", sub["id"], {"status": sub["status"], "plan": upd.get("plan")})


async def apply_session(s):
    tid = (s.get("metadata") or {}).get("tenant_id")
    if not tid or not (s.get("payment_status") == "paid" or s.get("status") == "complete"):
        return
    try:
        await db.webhook_events.insert_one({"id": f"checkout:{s['id']}", "provider": "stripe", "created_at": now()})
    except DuplicateKeyError:
        return
    await db.payment_transactions.update_one({"session_id": s["id"]}, {"$set": {"status": "completed", "payment_status": "paid", "updated_at": now()}})
    plan = await db.plans.find_one({"key": s["metadata"].get("plan")})
    if s.get("mode") == "payment" and plan:
        await db.tenants.update_one({"id": tid}, {"$inc": {"extra_segments": plan["segments"]}})
        await audit(tid, "stripe", "billing.pack.purchased", s["id"], {"segments": plan["segments"]})
    elif s.get("subscription"):
        sub = await _s(stripe.Subscription.retrieve, s["subscription"])
        await apply_subscription(tid, sub.to_dict())


class CheckoutIn(BaseModel):
    plan: str
    origin_url: str = Field(min_length=8)


@router.post("/billing/checkout")
async def checkout(d: CheckoutIn, ctx: Ctx = Depends(owner_ctx)):
    plan = await db.plans.find_one({"key": d.plan, "active": True})
    if not plan:
        raise AppError("This plan is not available.", 404)
    t = ctx.tenant
    if plan.get("interval") and t.get("stripe_subscription"):
        raise AppError("Use Manage subscription to change an existing plan.")
    if not plan.get("interval") and not t.get("stripe_subscription"):
        raise AppError("Choose a subscription before buying extra SMS.")
    prices = (await _s(stripe.Price.list, lookup_keys=[f"revloop_{plan['key']}"], active=True, limit=1)).data
    if not prices or prices[0].currency != "cad":
        raise AppError("This plan is not synced with Stripe yet.", 503)
    cust = t.get("stripe_customer")
    if not cust:
        cust = (await _s(stripe.Customer.create, email=ctx.user["email"], name=t["name"], metadata={"tenant_id": t["id"]},
                         idempotency_key=f"tenant:{t['id']}")).id
        await db.tenants.update_one({"id": t["id"]}, {"$set": {"stripe_customer": cust}})
    meta = {"tenant_id": t["id"], "plan": plan["key"]}
    kw = dict(customer=cust, line_items=[{"price": prices[0].id, "quantity": 1}], metadata=meta,
              mode="subscription" if plan.get("interval") else "payment",
              success_url=f"{d.origin_url}/payment/success?session_id={{CHECKOUT_SESSION_ID}}",
              cancel_url=f"{d.origin_url}/payment/cancel")
    if plan.get("interval"):
        kw["subscription_data"] = {"metadata": meta}
    try:
        session = await _s(stripe.checkout.Session.create, **kw, managed_payments={"enabled": True})
    except stripe.error.InvalidRequestError as e:
        msg = (e.user_message or str(e)).lower()
        if "managed" not in msg and "ineligible" not in msg and "customer" not in msg:
            raise AppError(f"Stripe error: {e.user_message or 'checkout failed'}", 502)
        session = await _s(stripe.checkout.Session.create, **kw, automatic_tax={"enabled": True},
                           customer_update={"address": "auto"}, billing_address_collection="required")
    await db.payment_transactions.insert_one({"session_id": session.id, "tenant_id": t["id"], "plan": plan["key"],
                                              "amount": plan["amount"] / 100.0, "currency": "cad", "status": "initiated",
                                              "payment_status": "pending", "created_at": now(), "updated_at": now()})
    await audit(t["id"], ctx.user["id"], "billing.checkout.started", session.id, {"plan": plan["key"]})
    return {"checkout_url": session.url, "session_id": session.id}


@router.get("/payments/status/{session_id}")
async def payment_status(session_id: str):
    rec = await db.payment_transactions.find_one({"session_id": session_id})
    if not rec:
        raise AppError("Transaction not found", 404)
    if rec.get("payment_status") != "paid":
        try:
            s = await _s(stripe.checkout.Session.retrieve, session_id)
            await apply_session(s.to_dict())
            rec = await db.payment_transactions.find_one({"session_id": session_id})
        except stripe.error.StripeError:
            pass
    return {"session_id": session_id, "status": rec["status"], "payment_status": rec["payment_status"]}


@router.post("/stripe/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    try:
        event = stripe.Webhook.construct_event(payload, request.headers.get("stripe-signature", ""), os.environ["STRIPE_WEBHOOK_SECRET"])
    except (stripe.error.SignatureVerificationError, ValueError):
        raise AppError("Invalid signature", 400)
    try:
        await db.webhook_events.insert_one({"id": event["id"], "provider": "stripe", "created_at": now()})
    except DuplicateKeyError:
        return {"status": "duplicate"}
    obj, typ = event["data"]["object"], event["type"]
    if typ in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        await apply_session(obj)
    elif typ.startswith("customer.subscription."):
        t = await db.tenants.find_one({"stripe_customer": obj["customer"]})
        if t:
            await apply_subscription(t["id"], obj)
    elif typ == "invoice.payment_failed":
        t = await db.tenants.find_one({"stripe_customer": obj.get("customer")})
        if t:
            await db.tenants.update_one({"id": t["id"]}, {"$set": {"billing_status": "past_due",
                                        "grace_until": t.get("grace_until") or now() + timedelta(days=await grace_days())}})
            await audit(t["id"], "stripe", "billing.payment_failed", obj["id"], {})
    return {"status": "ok"}


@router.get("/billing")
async def billing(ctx: Ctx = Depends(owner_ctx)):
    t = ctx.tenant
    invoices = []
    if t.get("stripe_customer"):
        try:
            inv = await _s(stripe.Invoice.list, customer=t["stripe_customer"], limit=24)
            invoices = [{"id": i.id, "number": i.number, "status": i.status, "total": i.total, "currency": i.currency,
                         "created": datetime.fromtimestamp(i.created, timezone.utc), "url": i.hosted_invoice_url, "pdf": i.invoice_pdf} for i in inv.data]
        except stripe.error.StripeError:
            pass
    return {"plans": [p for p in await plans() if p.get("active")], "invoices": invoices,
            "tenant": {k: t.get(k) for k in ("plan", "quota", "sms_used", "extra_segments", "billing_status", "trial_ends", "grace_until", "stripe_subscription")}}


class ChangePlanIn(BaseModel):
    plan: str


@router.post("/billing/change-plan")
async def change_plan(d: ChangePlanIn, ctx: Ctx = Depends(owner_ctx)):
    t = ctx.tenant
    if not t.get("stripe_subscription"):
        raise AppError("Subscribe to a plan first.")
    plan = await db.plans.find_one({"key": d.plan, "active": True, "interval": {"$ne": None}})
    if not plan:
        raise AppError("This plan is not available.", 404)
    if plan["key"] == t.get("plan"):
        raise AppError("You are already on this plan.")
    prices = (await _s(stripe.Price.list, lookup_keys=[f"revloop_{plan['key']}"], active=True, limit=1)).data
    if not prices:
        raise AppError("This plan is not synced with Stripe yet.", 503)
    sub = await _s(stripe.Subscription.retrieve, t["stripe_subscription"])
    try:
        sub = await _s(stripe.Subscription.modify, sub.id, items=[{"id": sub["items"]["data"][0]["id"], "price": prices[0].id}],
                       proration_behavior="always_invoice", metadata={"tenant_id": t["id"], "plan": plan["key"]})
    except stripe.error.StripeError as e:
        raise AppError(f"Stripe could not change the plan: {e.user_message or type(e).__name__}", 502)
    old = t.get("plan")
    await apply_subscription(t["id"], sub.to_dict())
    await audit(t["id"], ctx.user["id"], "billing.plan.changed", sub.id, {"from": old, "to": plan["key"], "proration": "always_invoice"})
    return {"ok": True, "plan": plan["key"]}


class PortalIn(BaseModel):
    origin_url: str


@router.post("/billing/portal")
async def portal(d: PortalIn, ctx: Ctx = Depends(owner_ctx)):
    if not ctx.tenant.get("stripe_customer"):
        raise AppError("No Stripe subscription yet.")
    try:
        s = await _s(stripe.billing_portal.Session.create, customer=ctx.tenant["stripe_customer"], return_url=f"{d.origin_url}/settings?tab=billing")
    except stripe.error.StripeError as e:
        raise AppError(f"Billing portal unavailable: {e.user_message or 'configure the customer portal in Stripe'}", 502)
    return {"url": s.url}


@router.get("/admin/plans")
async def admin_plans(ctx: Ctx = Depends(super_ctx)):
    return await plans()


class PlanIn(BaseModel):
    name: str = Field(min_length=2, max_length=60)
    amount: int = Field(ge=100, le=1000000)
    quota: int = Field(default=0, ge=0, le=1000000)
    segments: int = Field(default=0, ge=0, le=1000000)
    active: bool = True


@router.put("/admin/plans/{key}")
async def save_plan(key: str, d: PlanIn, ctx: Ctx = Depends(super_ctx)):
    p = await db.plans.find_one({"key": key}, {"_id": 0})
    if not p:
        raise AppError("Plan not found.", 404)
    await db.plans.update_one({"key": key}, {"$set": d.model_dump()})
    try:
        await sync_price({**p, **d.model_dump()})
    except stripe.error.StripeError as e:
        raise AppError(f"Saved, but Stripe sync failed: {e.user_message or type(e).__name__}", 502)
    await audit(ctx.tenant_id, ctx.user["id"], "admin.plan.updated", key, d.model_dump())
    return {"ok": True}
