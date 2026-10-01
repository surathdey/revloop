import os
import random
from datetime import timedelta
from core import db, now, new_id, Scoped
from auth import create_tenant, hash_password, verify_password

INDEXES = [
    ("users", [("email", 1)], {"unique": True}), ("users", [("tenant_id", 1)], {}),
    ("tenants", [("slug", 1)], {"unique": True}), ("tenants", [("id", 1)], {"unique": True}),
    ("tenants", [("twilio_number", 1)], {"unique": True, "partialFilterExpression": {"twilio_number": {"$type": "string"}}}),
    ("sessions", [("id", 1)], {"unique": True}), ("sessions", [("expires_at", 1)], {"expireAfterSeconds": 0}),
    ("contacts", [("tenant_id", 1), ("phone", 1)], {"unique": True}), ("contacts", [("tenant_id", 1), ("name", 1)], {}),
    ("contacts", [("name", "text"), ("email", "text"), ("notes", "text"), ("tags", "text")], {}),
    ("vehicles", [("tenant_id", 1), ("contact_id", 1)], {}), ("vehicles", [("tenant_id", 1), ("plate", 1)], {}),
    ("services", [("tenant_id", 1)], {}),
    ("appointments", [("tenant_id", 1), ("start", 1), ("end", 1)], {}), ("appointments", [("token", 1)], {"unique": True}),
    ("service_records", [("tenant_id", 1), ("vehicle_id", 1)], {}),
    ("consent_events", [("tenant_id", 1), ("contact_id", 1), ("created_at", -1)], {}),
    ("messages", [("dedupe_key", 1)], {"unique": True}), ("messages", [("token", 1)], {"unique": True}),
    ("messages", [("status", 1), ("scheduled_at", 1)], {}), ("messages", [("tenant_id", 1), ("contact_id", 1)], {}),
    ("reminder_rules", [("tenant_id", 1), ("due_at", 1)], {}), ("templates", [("tenant_id", 1), ("kind", 1)], {"unique": True}),
    ("audits", [("tenant_id", 1), ("created_at", -1)], {}), ("invites", [("token_hash", 1)], {"unique": True}),
    ("webhook_events", [("id", 1)], {"unique": True}), ("rate_limits", [("key", 1)], {"unique": True}),
    ("rate_limits", [("reset_at", 1)], {"expireAfterSeconds": 0}), ("platform_settings", [("key", 1)], {"unique": True}),
    ("login_attempts", [("identifier", 1)], {}), ("phone_numbers", [("number", 1)], {"unique": True}),
    ("cron_runs", [("id", 1)], {"unique": True}),
]

FIRST = ["Aisha", "Ben", "Carlos", "Diana", "Ethan", "Fatima", "Gurpreet", "Hannah", "Ivan", "Jasmine", "Kevin", "Lina", "Marco",
         "Nadia", "Omar", "Priya", "Quinn", "Rosa", "Sanjay", "Tara"]
LAST = ["Khan", "Martin", "Silva", "Nguyen", "Brown", "Ali", "Singh", "Lee", "Petrov", "Chen", "Wilson", "Haddad", "Rossi",
        "Patel", "Farah", "Sharma", "Taylor", "Lopez", "Gill", "Roy"]
CARS = [("Honda", "Civic"), ("Toyota", "RAV4"), ("Ford", "F-150"), ("Hyundai", "Elantra"), ("Mazda", "CX-5"), ("Nissan", "Rogue"),
        ("Chevrolet", "Equinox"), ("Kia", "Sportage"), ("Subaru", "Outback"), ("Volkswagen", "Jetta")]


async def ensure_indexes():
    for coll, keys, opts in INDEXES:
        await db[coll].create_index(keys, **opts)


async def seed_admin():
    email, pw = os.environ["ADMIN_EMAIL"].lower(), os.environ["ADMIN_PASSWORD"]
    u = await db.users.find_one({"email": email})
    if not u:
        await create_tenant(email, "RevLoop Admin", "RevLoop HQ", hash_password(pw), role="superadmin", approved=True, slug="revloop-hq")
    elif not verify_password(pw, u.get("password_hash") or ""):
        await db.users.update_one({"email": email}, {"$set": {"password_hash": hash_password(pw)}})


async def seed_demo():
    if os.environ.get("SEED_DEMO") != "true" or await db.tenants.find_one({"slug": "demo-auto-toronto"}):
        return
    pw = hash_password(os.environ["DEMO_PASSWORD"])
    owner, t = await create_tenant("owner@demo.revloop.test", "Dev Patel", "Demo Auto Toronto", pw, approved=True, slug="demo-auto-toronto")
    await db.tenants.update_one({"id": t["id"]}, {"$set": {"address": "123 Queen St W, Toronto, ON", "phone": "+14165550100",
                                                          "review_link": "https://g.page/r/demo-auto-toronto/review"}})
    staff = {"id": new_id(), "email": "staff@demo.revloop.test", "name": "Sam Rivera", "password_hash": pw, "google_sub": None,
             "role": "staff", "tenant_id": t["id"], "created_at": now()}
    await db.users.insert_one(dict(staff))
    s = Scoped(t["id"])
    rnd = random.Random(7)
    for i in range(20):
        consent = i % 4 != 3
        c = await s.insert("contacts", {"name": f"{FIRST[i]} {LAST[i]}", "phone": f"+1416555{1000 + i:04d}", "email": f"{FIRST[i].lower()}@example.com",
                                        "notes": "", "tags": "regular" if i % 3 == 0 else "", "consent_status": "express" if consent else "none",
                                        "consent_source": "staff attestation" if consent else "", "consent_at": now() if consent else None,
                                        "consent_expires_at": None, "last_review_at": None})
        await s.insert("consent_events", {"contact_id": c["id"], "status": c["consent_status"], "source": "seed", "expires_at": None,
                                          "evidence": "Demo data: consent recorded at counter."})
        make, model = CARS[i % len(CARS)]
        v = await s.insert("vehicles", {"contact_id": c["id"], "make": make, "model": model, "year": 2014 + i % 10,
                                        "plate": f"C{rnd.randint(100, 999)}X{rnd.randint(10, 99)}", "km": 40000 + i * 5000, "vin": ""})
        await s.insert("service_records", {"vehicle_id": v["id"], "appointment_id": None, "date": now() - timedelta(days=60 + i * 5),
                                           "type": "Oil & filter change", "amount": 8995, "notes": "", "technician": "Sam Rivera", "km": v["km"] - 3000})
        if i < 6:
            await s.insert("reminder_rules", {"vehicle_id": v["id"], "name": "Oil & filter change", "interval_months": 6, "interval_km": 8000,
                                              "due_at": now() + timedelta(days=i * 4), "due_km": v["km"] + 2000, "snoozed_until": None, "last_queued_at": None})


async def run_seed():
    await ensure_indexes()
    await seed_admin()
    await seed_demo()
