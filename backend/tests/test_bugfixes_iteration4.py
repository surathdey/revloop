"""RevLoop iteration-4 bug-fix regression tests (BUG-001..017).

Uses local backend (localhost:8001) so we can seed Mongo directly for
reset-token and idle-session scenarios. Preview URL is used by the Playwright
suite only.
"""
import os
import uuid
import hashlib
import pytest
import requests
from datetime import datetime, timedelta, timezone
from pymongo import MongoClient

BASE_URL = os.environ.get("BACKEND_INTERNAL_URL", "http://localhost:8001").rstrip("/")
API = f"{BASE_URL}/api"
WEB_H = {"X-RevLoop-Client": "web"}

SUPER = {"email": "surathpar@gmail.com", "password": "RevLoop-Admin-2026!"}
OWNER = {"email": "owner@demo.revloop.test", "password": "RevLoop-Demo-2026!"}
STAFF = {"email": "staff@demo.revloop.test", "password": "RevLoop-Demo-2026!"}

if not os.environ.get("MONGO_URL"):
    try:
        from dotenv import load_dotenv
        load_dotenv("/app/backend/.env")
    except Exception:
        pass

_MONGO = MongoClient(os.environ.get("MONGO_URL", "mongodb://localhost:27017"))
_DB = _MONGO[os.environ.get("DB_NAME", "test_database")]


def sha(s: str) -> str:
    return hashlib.sha256(s.encode()).hexdigest()


def _login(creds):
    s = requests.Session()
    s.headers.update(WEB_H)
    r = s.post(f"{API}/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, r.text
    # extract session_token from Set-Cookie and use Bearer (works on http localhost)
    import re
    m = re.search(r"session_token=([^;]+)", r.headers.get("set-cookie", ""))
    if m:
        s.headers.update({"Authorization": "Bearer " + m.group(1)})
    return s


def _signup(email, pwd, name="TEST U", business=None):
    s = requests.Session(); s.headers.update(WEB_H)
    r = s.post(f"{API}/auth/signup", json={
        "name": name, "business": business or f"TEST Biz {uuid.uuid4().hex[:6]}",
        "email": email, "password": pwd}, timeout=20)
    assert r.status_code == 200, r.text
    import re
    m = re.search(r"session_token=([^;]+)", r.headers.get("set-cookie", ""))
    if m:
        s.headers.update({"Authorization": "Bearer " + m.group(1)})
    return s


def _rand_phone():
    # Must satisfy ^1[2-9]\d{9}$ after stripping non-digits.
    # Format: +1 416 N XX XXXX where N in 2-9. Total 10 digits after '1'.
    n2 = str(2 + uuid.uuid4().int % 8)
    rest = f"{uuid.uuid4().int % 1000000:06d}"
    return "+1416" + n2 + rest


def _create_contact(sess, name_prefix="TEST_C"):
    """POST /contacts (QuickAddIn: requires make/model/year/plate)."""
    phone = _rand_phone()
    payload = {"name": f"{name_prefix}_{uuid.uuid4().hex[:5]}", "phone": phone, "email": "",
               "notes": "", "tags": "", "consent": False,
               "make": "Toyota", "model": "Corolla", "year": 2020,
               "plate": "T" + uuid.uuid4().hex[:5].upper(), "km": 50000, "vin": ""}
    r = sess.post(f"{API}/contacts", json=payload)
    assert r.status_code == 200, r.text
    return r.json(), phone


@pytest.fixture(scope="module")
def owner():
    return _login(OWNER)


@pytest.fixture(scope="module")
def staff():
    return _login(STAFF)


@pytest.fixture(scope="module")
def admin():
    return _login(SUPER)


# ============================================================
# BUG-001 / BUG-002 — staff 403 on owner-only; audit entry
# ============================================================
OWNER_ONLY = ["/settings/status", "/templates", "/billing", "/admin/overview"]


@pytest.mark.parametrize("path", OWNER_ONLY)
def test_bug001_staff_owner_only_returns_403(staff, path):
    r = staff.get(f"{API}{path}")
    assert r.status_code == 403, f"{path} returned {r.status_code}"


def test_bug002_denial_creates_audit_entry(staff):
    r = staff.get(f"{API}/templates")
    assert r.status_code == 403
    me = staff.get(f"{API}/auth/me").json()["user"]["id"]
    found = list(_DB.audits.find({"action": "access.denied", "actor": me}).sort("created_at", -1).limit(20))
    assert found, "No access.denied audit entry recorded"
    assert any("templates" in (a.get("entity_id") or "") for a in found), \
        f"audits found but path not recorded: {[a.get('entity_id') for a in found[:5]]}"


# ============================================================
# BUG-003 — cross-tenant GET /contacts/{id} = 403; random = 404
# ============================================================
def test_bug003_cross_tenant_returns_403_and_404(owner):
    email = f"TEST_bug3_{uuid.uuid4().hex[:6]}@example.com"
    other = _signup(email, "TestPassword123!", name="TEST Owner B",
                    business=f"TEST Garage B {uuid.uuid4().hex[:6]}")
    c, _ = _create_contact(other, "TEST_Other")
    # owner A tries to access it
    r = owner.get(f"{API}/contacts/{c['id']}")
    assert r.status_code == 403, f"cross-tenant should be 403, got {r.status_code}"
    # non-existent id
    r = owner.get(f"{API}/contacts/does-not-exist-{uuid.uuid4().hex}")
    assert r.status_code == 404


# ============================================================
# BUG-004 — forgot/reset
# ============================================================
def test_bug004_forgot_password_generic_response_unknown():
    r = requests.post(f"{API}/auth/forgot-password",
                      json={"email": f"TEST_noone_{uuid.uuid4().hex}@nowhere.test"},
                      headers=WEB_H, timeout=15)
    assert r.status_code == 200
    assert "If an account" in r.json().get("message", "")


def test_bug004_reset_flow_end_to_end():
    s = requests.Session(); s.headers.update(WEB_H)
    email = f"TEST_reset_{uuid.uuid4().hex[:6]}@example.com"
    pwd0 = "InitialPwd123!"
    r = s.post(f"{API}/auth/signup", json={
        "name": "TEST Reset", "business": f"TEST Reset {uuid.uuid4().hex[:6]}",
        "email": email, "password": pwd0}, timeout=20)
    assert r.status_code == 200
    user = _DB.users.find_one({"email": email.lower()})
    assert user, f"user {email} missing in DB"
    raw = "testtoken_" + uuid.uuid4().hex + uuid.uuid4().hex
    _DB.password_reset_tokens.insert_one({
        "id": sha(raw), "user_id": user["id"], "used": False,
        "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        "created_at": datetime.now(timezone.utc)})
    new_pwd = "NewStrongPwd456!"
    r = requests.post(f"{API}/auth/reset-password", json={"token": raw, "password": new_pwd}, headers=WEB_H, timeout=15)
    assert r.status_code == 200, r.text
    # old password rejected
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": pwd0}, headers=WEB_H, timeout=15)
    assert r.status_code == 401
    # new password works
    r = requests.post(f"{API}/auth/login", json={"email": email, "password": new_pwd}, headers=WEB_H, timeout=15)
    assert r.status_code == 200
    # token reuse fails
    r = requests.post(f"{API}/auth/reset-password", json={"token": raw, "password": "AnotherPwd789!"}, headers=WEB_H, timeout=15)
    assert r.status_code >= 400


def test_bug004_forgot_signup_resend_delivered_safe():
    s = requests.Session(); s.headers.update(WEB_H)
    email = f"TEST_delivered_{uuid.uuid4().hex[:6]}@example.com"
    r = s.post(f"{API}/auth/signup", json={
        "name": "TEST Delivered", "business": f"TEST Delivered {uuid.uuid4().hex[:6]}",
        "email": email, "password": "DeliveredPwd123!"}, timeout=20)
    assert r.status_code == 200
    r = requests.post(f"{API}/auth/forgot-password", json={"email": email}, headers=WEB_H, timeout=15)
    assert r.status_code == 200


# ============================================================
# BUG-005 — session idle timeout
# ============================================================
def test_bug005_session_idle_timeout():
    email = f"TEST_idle_{uuid.uuid4().hex[:6]}@example.com"
    s = _signup(email, "IdlePwd123456!", name="TEST Idle",
                business=f"TEST Idle {uuid.uuid4().hex[:6]}")
    user = _DB.users.find_one({"email": email.lower()})
    assert user
    long_ago = datetime.now(timezone.utc) - timedelta(hours=2)
    _DB.sessions.update_many({"user_id": user["id"]}, {"$set": {"last_seen_at": long_ago, "created_at": long_ago}})
    r = s.get(f"{API}/auth/me")
    assert r.status_code == 401
    assert "inactiv" in r.text.lower(), r.text


# ============================================================
# BUG-006 — admin overview exposes health & free_numbers
# ============================================================
def test_bug006_admin_overview_has_health_fields(admin):
    r = admin.get(f"{API}/admin/overview")
    assert r.status_code == 200
    s = str(r.json())
    for k in ("vapi", "elevenlabs", "free_numbers"):
        assert k in s, f"health field '{k}' missing in overview"


# ============================================================
# BUG-007 — customer update: phone change, duplicate 409, invalid 400
# ============================================================
def test_bug007_customer_update_phone(owner):
    a, phone_a = _create_contact(owner, "TEST_A")
    b, phone_b = _create_contact(owner, "TEST_B")
    phone_c = _rand_phone()
    r = owner.patch(f"{API}/contacts/{a['id']}", json={"phone": phone_c})
    assert r.status_code == 200, r.text
    # search by new phone finds A
    r = owner.get(f"{API}/contacts", params={"q": phone_c[-7:]})
    assert r.status_code == 200
    assert any(c["id"] == a["id"] for c in r.json()), "contact not findable by new phone"
    # duplicate phone -> 409
    r = owner.patch(f"{API}/contacts/{a['id']}", json={"phone": phone_b})
    assert r.status_code == 409
    # invalid phone
    r = owner.patch(f"{API}/contacts/{a['id']}", json={"phone": "abc"})
    assert r.status_code >= 400


# ============================================================
# BUG-008 — public: double booking rejected; slot disappears
# ============================================================
def _pick_future_date_slot():
    # try several weekdays within the next 2 weeks
    svcs = requests.get(f"{API}/public/garage/demo-auto-toronto", timeout=15).json().get("services", [])
    assert svcs, "no public services for demo garage"
    sid = svcs[0]["id"]
    for offset in range(2, 21):
        d = (datetime.now() + timedelta(days=offset)).date()
        if d.weekday() >= 5:
            continue
        iso = d.isoformat()
        r = requests.get(f"{API}/public/slots", params={"slug": "demo-auto-toronto", "service": sid, "date": iso}, timeout=15)
        if r.status_code != 200:
            continue
        slots = r.json().get("slots", [])
        if slots:
            return sid, iso, slots[0]["time"]
    pytest.skip("No open public slot in next 3 weeks")


def test_bug008_double_booking_rejected():
    sid, date, start = _pick_future_date_slot()
    def payload(phone):
        return {"slug": "demo-auto-toronto", "service_id": sid, "date": date, "start": start,
                "name": f"TEST_Book_{uuid.uuid4().hex[:4]}", "phone": phone,
                "make": "Honda", "model": "Civic", "year": 2019, "plate": "P" + uuid.uuid4().hex[:5].upper(),
                "consent": True}
    r1 = requests.post(f"{API}/public/book", json=payload(_rand_phone()), headers=WEB_H, timeout=20)
    assert r1.status_code == 200, f"first booking failed: {r1.status_code} {r1.text}"
    r2 = requests.post(f"{API}/public/book", json=payload(_rand_phone()), headers=WEB_H, timeout=20)
    assert r2.status_code == 409, f"second booking should 409, got {r2.status_code}: {r2.text}"
    msg = r2.text.lower()
    assert ("no longer available" in msg) or ("just booked" in msg), f"unexpected message: {r2.text}"
    # slot removed from grid
    r = requests.get(f"{API}/public/slots", params={"slug": "demo-auto-toronto", "service": sid, "date": date}, timeout=15)
    assert r.status_code == 200
    times = [s["time"] for s in r.json().get("slots", [])]
    assert start not in times, "booked slot should disappear from grid"


# ============================================================
# BUG-009 — booking without consent => no outbound reminders queued
# ============================================================
def test_bug009_public_no_consent_no_reminders(owner):
    sid, date, start = _pick_future_date_slot()
    phone = _rand_phone()
    payload = {"slug": "demo-auto-toronto", "service_id": sid, "date": date, "start": start,
               "name": f"TEST_NoConsent_{uuid.uuid4().hex[:4]}", "phone": phone,
               "make": "Honda", "model": "Civic", "year": 2019, "plate": "N" + uuid.uuid4().hex[:5].upper(),
               "consent": False}
    r = requests.post(f"{API}/public/book", json=payload, headers=WEB_H, timeout=20)
    assert r.status_code == 200, r.text
    # owner finds contact and inspects messages
    c = owner.get(f"{API}/contacts", params={"q": phone[-7:]}).json()
    assert c, "contact not created"
    cid = c[0]["id"]
    detail = owner.get(f"{API}/contacts/{cid}").json()
    msgs = detail.get("messages") or []
    bad = [m for m in msgs if m.get("direction") == "outbound" and m.get("kind") in ("confirmation", "reminder24", "reminder2")]
    assert not bad, f"Expected zero outbound reminders without consent, got {bad}"


# ============================================================
# BUG-012 — /messages/process also runs service-due scan
# ============================================================
def test_bug012_process_now_includes_service_due(owner):
    r = owner.post(f"{API}/messages/process")
    assert r.status_code == 200, r.text
    body = r.json()
    assert "service_due_queued" in body, f"expected service_due_queued key, got {list(body.keys())}"
    assert isinstance(body["service_due_queued"], int)


# ============================================================
# BUG-013 — vehicle update
# ============================================================
def test_bug013_vehicle_update_odometer(owner):
    c, _ = _create_contact(owner, "TEST_V")
    detail = owner.get(f"{API}/contacts/{c['id']}").json()
    v0 = detail["vehicles"][0]
    r = owner.patch(f"{API}/vehicles/{v0['id']}", json={
        "make": v0["make"], "model": v0["model"], "year": v0["year"],
        "plate": v0["plate"], "km": 123456, "vin": v0.get("vin", "")
    })
    assert r.status_code == 200, r.text
    detail2 = owner.get(f"{API}/contacts/{c['id']}").json()
    km_values = [v.get("km") for v in detail2["vehicles"]]
    assert 123456 in km_values, f"km not persisted: {km_values}"


# ============================================================
# BUG-014 — audit filters (actor / date_from / date_to)
# ============================================================
def test_bug014_audit_filters(admin):
    _login(OWNER)  # fresh auth.login row
    r = admin.get(f"{API}/admin/audits", params={"actor": OWNER["email"]})
    assert r.status_code == 200
    rows = r.json()
    assert isinstance(rows, list)
    assert any(a["action"] == "auth.login" for a in rows), "no auth.login row for owner"
    # out-of-range date filter -> empty
    r = admin.get(f"{API}/admin/audits", params={"date_from": "2020-01-01", "date_to": "2020-01-02"})
    assert r.status_code == 200
    assert len(r.json()) == 0


# ============================================================
# BUG-017 — DEFAULT_TRIAL_DAYS cycle via /admin/settings
# ============================================================
def test_bug017_default_trial_days_cycle(admin):
    try:
        r = admin.put(f"{API}/admin/settings", json={"values": {"DEFAULT_TRIAL_DAYS": "21"}})
        assert r.status_code == 200, r.text
        email = f"TEST_trial_{uuid.uuid4().hex[:6]}@example.com"
        s = _signup(email, "TrialPwd12345!", name="TEST Trial",
                    business=f"TEST Trial {uuid.uuid4().hex[:6]}")
        me = s.get(f"{API}/auth/me").json()
        trial = me["tenant"].get("trial_ends") or me["tenant"].get("trial_ends_at")
        assert trial, f"trial_ends missing: {me['tenant']}"
        end = datetime.fromisoformat(trial.replace("Z", "+00:00"))
        days = (end - datetime.now(timezone.utc)).days
        assert 19 <= days <= 22, f"expected ~21 days trial, got {days}"
    finally:
        admin.put(f"{API}/admin/settings", json={"values": {"DEFAULT_TRIAL_DAYS": ""}})


# ============================================================
# Sales: dup phone -> 409, DNC phone -> 409
# ============================================================
def test_sales_add_prospect_dup_and_dnc(admin):
    phone = f"+1647{(uuid.uuid4().int % 8 + 2)}{uuid.uuid4().int % 10**6:06d}"
    r = admin.post(f"{API}/sales/prospects", json={
        "business_name": f"TEST_Shop_{uuid.uuid4().hex[:5]}", "phone": phone,
        "contact_name": "", "email": "", "city": ""})
    assert r.status_code == 200, r.text
    r = admin.post(f"{API}/sales/prospects", json={
        "business_name": f"TEST_Shop_{uuid.uuid4().hex[:5]}", "phone": phone,
        "contact_name": "", "email": "", "city": ""})
    assert r.status_code == 409
    dnc_phone = f"+1647{(uuid.uuid4().int % 8 + 2)}{uuid.uuid4().int % 10**6:06d}"
    _DB.dnc.insert_one({"phone": dnc_phone, "reason": "TEST_"})
    try:
        r = admin.post(f"{API}/sales/prospects", json={
            "business_name": f"TEST_Shop_{uuid.uuid4().hex[:5]}", "phone": dnc_phone,
            "contact_name": "", "email": "", "city": ""})
        assert r.status_code == 409
    finally:
        _DB.dnc.delete_one({"phone": dnc_phone})
