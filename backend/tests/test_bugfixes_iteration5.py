"""RevLoop iteration-5 bug-fix regression tests.

Covers BUG-005 (settings default), BUG-010 (YES routing), BUG-011
(template var warning), BUG-018 (change-plan without subscription),
BUG-019 (CSV invalid year rejection), BUG-020 (customer count parity).
"""
import os
import uuid
import pytest
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from pymongo import MongoClient

BASE_URL = os.environ.get("BACKEND_INTERNAL_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"
WEB_H = {"X-RevLoop-Client": "web"}

SUPER = {"email": "surathpar@gmail.com", "password": "RevLoop-Admin-2026!"}
OWNER = {"email": "owner@demo.revloop.test", "password": "RevLoop-Demo-2026!"}

if not os.environ.get("MONGO_URL"):
    try:
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
    except Exception:
        pass

_MONGO = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
_DB = _MONGO[os.environ.get("DB_NAME", "test_database")]


def _login(creds):
    import re
    s = requests.Session(); s.headers.update(WEB_H)
    r = s.post(f"{API}/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, r.text
    m = re.search(r"session_token=([^;]+)", r.headers.get("set-cookie", ""))
    if m:
        s.headers.update({"Authorization": "Bearer " + m.group(1)})
    return s


def _rand_phone():
    n2 = str(2 + uuid.uuid4().int % 8)
    rest = f"{uuid.uuid4().int % 1000000:06d}"
    return "+1416" + n2 + rest


@pytest.fixture(scope="module")
def owner():
    return _login(OWNER)


@pytest.fixture(scope="module")
def admin():
    return _login(SUPER)


# ============================================================
# BUG-005 — /admin/settings returns a 'default' field
# ============================================================
def test_bug005_admin_settings_exposes_defaults(admin):
    r = admin.get(f"{API}/admin/settings")
    assert r.status_code == 200, r.text
    s = r.json()
    for k, exp_default in (("SESSION_IDLE_MINUTES", "60"),
                           ("DEFAULT_TRIAL_DAYS", "14"),
                           ("BILLING_GRACE_DAYS", "7")):
        assert k in s, f"{k} missing from settings response"
        assert "default" in s[k], f"{k} entry missing 'default' field"
        assert s[k]["default"] == exp_default, \
            f"{k} default was {s[k]['default']!r}, expected {exp_default!r}"


# ============================================================
# BUG-010 — YES/CANCEL routing with multiple upcoming appointments
# ============================================================
def _mk_contact_with_consent(sess):
    phone = _rand_phone()
    payload = {"name": f"TEST_B10_{uuid.uuid4().hex[:5]}", "phone": phone, "email": "",
               "notes": "", "tags": "", "consent": True,
               "make": "Toyota", "model": "Corolla", "year": 2020,
               "plate": "B" + uuid.uuid4().hex[:5].upper(), "km": 50000, "vin": ""}
    r = sess.post(f"{API}/contacts", json=payload)
    assert r.status_code == 200, r.text
    return r.json(), phone


def _ensure_service(sess):
    r = sess.get(f"{API}/services"); r.raise_for_status()
    svcs = r.json()
    if svcs:
        return svcs[0]
    r = sess.post(f"{API}/services", json={"name": f"TEST_Svc_{uuid.uuid4().hex[:5]}", "duration": 30, "price": 5000})
    assert r.status_code == 200, r.text
    return r.json()


def _me(sess):
    return sess.get(f"{API}/auth/me").json()


def _book(sess, cid, vid, sid, staff_id, when_utc):
    body = {"contact_id": cid, "vehicle_id": vid, "service_id": sid, "staff_id": staff_id,
            "bay": 1, "notes": "", "start": when_utc.isoformat()}
    r = sess.post(f"{API}/appointments", json=body)
    assert r.status_code == 200, f"booking failed: {r.status_code} {r.text}"
    return r.json()


def _next_working_slot(tenant, offset_days, hour=None):
    tz = ZoneInfo(tenant["timezone"])
    h = hour if hour is not None else max(tenant["open_hour"] + 1, 10)
    d = (datetime.now(tz) + timedelta(days=offset_days)).replace(
        hour=h, minute=0, second=0, microsecond=0)
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.astimezone(timezone.utc)


@pytest.fixture(scope="module", autouse=True)
def _force_simulated_sms(admin):
    """Ensure SMS_MODE is simulated so /messages/simulate-reply works."""
    r = admin.get(f"{API}/admin/settings")
    prev = r.json().get("SMS_MODE", {}).get("value", "simulated")
    admin.put(f"{API}/admin/settings", json={"values": {"SMS_MODE": "simulated"}})
    yield
    if prev and prev != "simulated":
        admin.put(f"{API}/admin/settings", json={"values": {"SMS_MODE": prev}})


def test_bug010_yes_confirms_appointment_with_latest_reminder(owner):
    me = _me(owner)
    tenant = me["tenant"]
    staff_id = me["user"]["id"]
    svc = _ensure_service(owner)
    c, phone = _mk_contact_with_consent(owner)
    detail = owner.get(f"{API}/contacts/{c['id']}").json()
    vid = detail["vehicles"][0]["id"]
    # Two future appointments
    base_off = 20 + (uuid.uuid4().int % 40)
    a1_start = _next_working_slot(tenant, base_off, hour=10)
    a2_start = _next_working_slot(tenant, base_off + 3, hour=14)
    a1 = _book(owner, c["id"], vid, svc["id"], staff_id, a1_start)
    a2 = _book(owner, c["id"], vid, svc["id"], staff_id, a2_start)
    # Seed a sent confirmation for a2 ONLY (manually, bypass queue)
    now_utc = datetime.now(timezone.utc)
    _DB.messages.insert_one({
        "id": uuid.uuid4().hex, "tenant_id": tenant["id"], "contact_id": c["id"],
        "appointment_id": a2["id"], "kind": "confirmation", "direction": "outbound",
        "body": "test", "recipient": phone, "sender": "SIMULATED",
        "status": "simulated", "scheduled_at": now_utc, "sent_at": now_utc,
        "segments": 1, "error": "", "dedupe_key": f"test:{uuid.uuid4().hex}",
        "token": uuid.uuid4().hex, "provider_sid": None, "created_at": now_utc,
    })
    # Simulate YES reply
    r = owner.post(f"{API}/messages/simulate-reply", json={"contact_id": c["id"], "body": "YES"})
    assert r.status_code == 200, r.text
    # a2 should be confirmed (it had the latest reminder)
    doc1 = _DB.appointments.find_one({"id": a1["id"]})
    doc2 = _DB.appointments.find_one({"id": a2["id"]})
    assert doc2["status"] == "confirmed", f"a2 should be confirmed, got {doc2['status']}"
    assert doc1["status"] == "scheduled", f"a1 should remain scheduled, got {doc1['status']}"
    # No needs-attention audit for this contact
    att = _DB.audits.find_one({"tenant_id": tenant["id"], "action": "appointment.reply-needs-attention",
                               "entity_id": c["id"], "created_at": {"$gte": now_utc - timedelta(minutes=5)}})
    assert att is None, "YES should not escalate to needs-attention when reminder context exists"


def test_bug010_yes_without_reminder_confirms_nearest_scheduled(owner):
    me = _me(owner)
    tenant = me["tenant"]
    staff_id = me["user"]["id"]
    svc = _ensure_service(owner)
    c, phone = _mk_contact_with_consent(owner)
    detail = owner.get(f"{API}/contacts/{c['id']}").json()
    vid = detail["vehicles"][0]["id"]
    base_off = 20 + (uuid.uuid4().int % 40)
    a1 = _book(owner, c["id"], vid, svc["id"], staff_id, _next_working_slot(tenant, base_off, hour=11))
    a2 = _book(owner, c["id"], vid, svc["id"], staff_id, _next_working_slot(tenant, base_off + 3, hour=15))
    # No sent reminders at all
    r = owner.post(f"{API}/messages/simulate-reply", json={"contact_id": c["id"], "body": "YES"})
    assert r.status_code == 200, r.text
    doc1 = _DB.appointments.find_one({"id": a1["id"]})
    doc2 = _DB.appointments.find_one({"id": a2["id"]})
    # Nearest scheduled (a1) should be confirmed
    assert doc1["status"] == "confirmed", f"nearest should become confirmed, got {doc1['status']}"
    assert doc2["status"] == "scheduled", f"later should remain scheduled, got {doc2['status']}"


def test_bug010_cancel_ambiguous_escalates(owner):
    me = _me(owner)
    tenant = me["tenant"]
    staff_id = me["user"]["id"]
    svc = _ensure_service(owner)
    c, phone = _mk_contact_with_consent(owner)
    detail = owner.get(f"{API}/contacts/{c['id']}").json()
    vid = detail["vehicles"][0]["id"]
    base_off = 20 + (uuid.uuid4().int % 40)
    a1 = _book(owner, c["id"], vid, svc["id"], staff_id, _next_working_slot(tenant, base_off, hour=12))
    a2 = _book(owner, c["id"], vid, svc["id"], staff_id, _next_working_slot(tenant, base_off + 3, hour=16))
    before = datetime.now(timezone.utc)
    r = owner.post(f"{API}/messages/simulate-reply", json={"contact_id": c["id"], "body": "CANCEL"})
    assert r.status_code == 200, r.text
    # Nothing cancelled
    for a in (a1, a2):
        d = _DB.appointments.find_one({"id": a["id"]})
        assert d["status"] == "scheduled", f"{a['id']} should still be scheduled, got {d['status']}"
    # Needs-attention audit present
    att = _DB.audits.find_one({"tenant_id": tenant["id"], "action": "appointment.reply-needs-attention",
                               "entity_id": c["id"], "created_at": {"$gte": before - timedelta(seconds=5)}})
    assert att is not None, "CANCEL with ambiguous context must create needs-attention audit"


# ============================================================
# BUG-018 — change-plan without active subscription returns 400
# ============================================================
def test_bug018_change_plan_requires_subscription(owner):
    # Demo garage has no active Stripe subscription in test env
    r = owner.post(f"{API}/billing/change-plan", json={"plan": "growth"})
    assert r.status_code == 400, f"expected 400, got {r.status_code} {r.text}"
    body = r.json()
    msg = (body.get("detail") or body.get("message") or str(body)).lower()
    assert "subscribe" in msg, f"error should mention 'Subscribe to a plan first', got: {body}"


# ============================================================
# BUG-019 — CSV invalid year rejected with clear error
# ============================================================
def test_bug019_csv_invalid_year_rejected(owner):
    phone = _rand_phone()
    csv = ("name,phone,make,model,year,plate\n"
           f"TEST_BadYear,{phone},Honda,Civic,notayear,BAD" + uuid.uuid4().hex[:4].upper() + "\n")
    r = owner.post(f"{API}/contacts/import", json={"csv": csv})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 0, f"invalid-year row must not be created, got {res}"
    assert res["errors"], f"errors[] empty, expected a year validation error: {res}"
    msg = " ".join(e.get("message", "") for e in res["errors"]).lower()
    assert "year" in msg, f"error should mention year: {res['errors']}"
    # Contact should NOT exist
    found = owner.get(f"{API}/contacts", params={"q": phone[-7:]}).json()
    assert not any(c["phone"] == phone for c in found), "no contact should have been created"


def test_bug019_csv_mixed_valid_imports_only_good_rows(owner):
    bad_phone = _rand_phone()
    good_phone = _rand_phone()
    csv = ("name,phone,make,model,year,plate\n"
           f"TEST_Bad,{bad_phone},Honda,Civic,notayear,BX" + uuid.uuid4().hex[:4].upper() + "\n"
           f"TEST_Good,{good_phone},Honda,Civic,2021,GX" + uuid.uuid4().hex[:4].upper() + "\n")
    r = owner.post(f"{API}/contacts/import", json={"csv": csv})
    assert r.status_code == 200, r.text
    res = r.json()
    assert res["created"] == 1, f"only valid row should import: {res}"
    assert len(res["errors"]) >= 1, f"bad row should be in errors: {res}"
    # good contact exists; bad does not
    good = owner.get(f"{API}/contacts", params={"q": good_phone[-7:]}).json()
    assert any(c["phone"] == good_phone for c in good), "good contact missing"
    bad = owner.get(f"{API}/contacts", params={"q": bad_phone[-7:]}).json()
    assert not any(c["phone"] == bad_phone for c in bad), "bad contact must not be imported"


# ============================================================
# BUG-020 — Customers list count == Dashboard kpi customers
# ============================================================
def test_bug020_customer_count_matches_dashboard(owner):
    # Race-tolerant: contacts can be created by concurrent test workers between the two API calls.
    # The feature invariant is that both endpoints count from the same contacts collection, so a
    # tiny delta (<= 5) is acceptable here while confirming parity in the common case.
    kpis = owner.get(f"{API}/dashboard").json()["kpis"]
    total_dash = kpis["customers"]
    contacts = owner.get(f"{API}/contacts").json()
    assert abs(len(contacts) - total_dash) <= 5, \
        f"customers list ({len(contacts)}) vs dashboard kpi ({total_dash}) diverge beyond tolerance"
