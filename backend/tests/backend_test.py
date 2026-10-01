"""RevLoop Phase 0/1/2 backend regression tests."""
import os
import io
import csv
import time
import uuid
import pytest
import requests
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://garage-sms-crm.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

SUPER = {"email": "surathpar@gmail.com", "password": "RevLoop-Admin-2026!"}
OWNER = {"email": "owner@demo.revloop.test", "password": "RevLoop-Demo-2026!"}
STAFF = {"email": "staff@demo.revloop.test", "password": "RevLoop-Demo-2026!"}
CRON_SECRET = "15ab46384ac464b88ce1d31da573dc0cce73d9e35c901551a1d276cddbea37b6"
TZ = ZoneInfo("America/Toronto")


# ---- session helpers ----

def _login(creds):
    s = requests.Session()
    r = s.post(f"{API}/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"login failed for {creds['email']}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="session")
def owner():
    return _login(OWNER)


@pytest.fixture(scope="session")
def staff():
    return _login(STAFF)


@pytest.fixture(scope="session")
def admin():
    return _login(SUPER)


# =========================
# Health & Auth
# =========================
class TestHealth:
    def test_health(self):
        r = requests.get(f"{API}/health", timeout=10)
        assert r.status_code == 200 and r.json().get("ok") is True


class TestAuth:
    def test_wrong_password(self):
        r = requests.post(f"{API}/auth/login", json={"email": OWNER["email"], "password": "bad-password-xyz"}, timeout=15)
        assert r.status_code == 401

    def test_me_owner(self, owner):
        r = owner.get(f"{API}/auth/me")
        assert r.status_code == 200
        d = r.json()
        assert d["user"]["role"] == "owner"
        assert d["tenant"]["slug"] == "demo-auto-toronto"
        assert d["sms_mode"] in ("simulated", "live")

    def test_me_staff(self, staff):
        r = staff.get(f"{API}/auth/me")
        assert r.status_code == 200
        assert r.json()["user"]["role"] == "staff"

    def test_me_super(self, admin):
        assert admin.get(f"{API}/auth/me").json()["user"]["role"] == "superadmin"

    def test_logout(self):
        s = _login(OWNER)
        assert s.post(f"{API}/auth/logout").status_code == 200
        assert s.get(f"{API}/auth/me").status_code == 401

    def test_signup_creates_pending_garage(self):
        email = f"test_{uuid.uuid4().hex[:10]}@example.com"
        s = requests.Session()
        r = s.post(f"{API}/auth/signup", json={
            "name": "TEST Owner", "business": f"TEST Garage {uuid.uuid4().hex[:6]}",
            "email": email, "password": "TestPassword123!"
        })
        assert r.status_code == 200, r.text
        me = s.get(f"{API}/auth/me").json()
        assert me["user"]["role"] == "owner"
        assert me["tenant"]["approval_status"] == "pending"
        # defaults seeded?
        svc = s.get(f"{API}/services")
        assert svc.status_code == 200 and len(svc.json()) >= 3
        # cannot read other tenant's data
        # try to access demo contact by id
        demo_owner = _login(OWNER)
        contacts = demo_owner.get(f"{API}/contacts").json()
        if contacts:
            cid = contacts[0]["id"]
            r = s.get(f"{API}/contacts/{cid}")
            assert r.status_code == 404, f"Tenant isolation leak: {r.status_code}"


# =========================
# Role enforcement
# =========================
class TestRoles:
    STAFF_FORBIDDEN = [
        ("GET", "/dashboard"),
        ("PUT", "/settings"),
        ("GET", "/settings/status"),
        ("GET", "/templates"),
        ("POST", "/services"),
        ("POST", "/team/invite"),
        ("POST", "/messages/process"),
        ("GET", "/admin/overview"),
        ("GET", "/admin/tenants"),
    ]

    @pytest.mark.parametrize("method,path", STAFF_FORBIDDEN)
    def test_staff_forbidden(self, staff, method, path):
        r = staff.request(method, f"{API}{path}", json={})
        assert r.status_code == 403, f"{method} {path} expected 403 got {r.status_code}"

    def test_owner_forbidden_admin(self, owner):
        r = owner.get(f"{API}/admin/overview")
        assert r.status_code == 403

    def test_staff_consent_change_forbidden(self, staff, owner):
        contacts = owner.get(f"{API}/contacts").json()
        if contacts:
            cid = contacts[0]["id"]
            r = staff.post(f"{API}/contacts/{cid}/consent",
                           json={"status": "express", "source": "staff", "evidence": "x"*10})
            assert r.status_code == 403


# =========================
# CRM / Customers
# =========================
class TestCRM:
    def test_quick_add_and_duplicate(self, owner):
        suffix = f"01{int(time.time()) % 100:02d}"
        phone = f"647-555-{suffix}"
        payload = {
            "name": f"TEST Customer {suffix}", "phone": phone, "consent": True,
            "make": "Toyota", "model": "Camry", "year": 2020, "plate": f"TEST{suffix}",
            "km": 50000, "vin": "",
        }
        r = owner.post(f"{API}/contacts", json=payload)
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        # verify persistence via GET
        got = owner.get(f"{API}/contacts/{cid}").json()
        assert got["phone"].endswith(phone[-4:])
        assert got["consent_status"] == "express"
        assert len(got["vehicles"]) == 1
        # duplicate phone
        r2 = owner.post(f"{API}/contacts", json=payload)
        assert r2.status_code == 409

    def test_search(self, owner):
        r = owner.get(f"{API}/contacts", params={"q": "TEST"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_csv_import_and_dup_flag(self, owner):
        # build CSV with 10 TEST rows (keep small for speed)
        rows = ["name,phone,make,model,year,plate,km"]
        for i in range(10):
            rows.append(f"TEST_CSV_{i}_{uuid.uuid4().hex[:4]},647-555-02{i:02d},Honda,Civic,2019,TCSV{i}{uuid.uuid4().hex[:3]},1000")
        csv_text = "\n".join(rows)
        r = owner.post(f"{API}/contacts/import", json={"csv": csv_text})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["created"] == 10
        assert len(d["duplicates"]) == 0
        # re-import same -> all dup
        r2 = owner.post(f"{API}/contacts/import", json={"csv": csv_text})
        d2 = r2.json()
        assert d2["created"] == 0
        assert len(d2["duplicates"]) == 10


# =========================
# Appointments / Calendar
# =========================
class TestAppointments:
    def _future_slot(self, tenant):
        local = datetime.now(TZ) + timedelta(days=1)
        local = local.replace(hour=max(tenant["open_hour"] + 1, 10), minute=0, second=0, microsecond=0)
        return local.astimezone(timezone.utc).isoformat()

    def test_booking_conflict(self, owner):
        me = owner.get(f"{API}/auth/me").json()
        tenant = me["tenant"]
        contacts = owner.get(f"{API}/contacts").json()
        services = owner.get(f"{API}/services").json()
        team = owner.get(f"{API}/team").json()
        assert contacts and services and team["users"]
        # need a contact with a vehicle
        c = next((c for c in contacts if c.get("vehicles")), contacts[0])
        contact_detail = owner.get(f"{API}/contacts/{c['id']}").json()
        if not contact_detail.get("vehicles"):
            pytest.skip("No vehicle on test contact")
        vid = contact_detail["vehicles"][0]["id"]
        start_iso = self._future_slot(tenant)
        payload = {
            "contact_id": c["id"], "vehicle_id": vid, "service_id": services[0]["id"],
            "start": start_iso, "bay": 1, "staff_id": team["users"][0]["id"], "notes": "TEST"
        }
        r = owner.post(f"{API}/appointments", json=payload)
        assert r.status_code in (200, 409), r.text
        # try again - should conflict
        r2 = owner.post(f"{API}/appointments", json=payload)
        assert r2.status_code == 409

    def test_booking_past_rejected(self, owner):
        me = owner.get(f"{API}/auth/me").json()
        contacts = owner.get(f"{API}/contacts").json()
        services = owner.get(f"{API}/services").json()
        team = owner.get(f"{API}/team").json()
        c = next((c for c in contacts if c.get("vehicles")), contacts[0])
        detail = owner.get(f"{API}/contacts/{c['id']}").json()
        if not detail.get("vehicles"):
            pytest.skip("No vehicle")
        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        r = owner.post(f"{API}/appointments", json={
            "contact_id": c["id"], "vehicle_id": detail["vehicles"][0]["id"],
            "service_id": services[0]["id"], "start": past, "bay": 1,
            "staff_id": team["users"][0]["id"], "notes": "past"
        })
        assert r.status_code == 400


# =========================
# Settings & Templates
# =========================
class TestSettings:
    def test_settings_status(self, owner):
        r = owner.get(f"{API}/settings/status")
        assert r.status_code == 200
        d = r.json()
        assert "sms_mode" in d and "booking_url" in d

    def test_templates_get(self, owner):
        r = owner.get(f"{API}/templates")
        assert r.status_code == 200
        d = r.json()
        assert "templates" in d and "variables" in d and len(d["templates"]) > 0

    def test_settings_invalid_review_link(self, owner):
        me = owner.get(f"{API}/auth/me").json()["tenant"]
        payload = {
            "name": me["name"], "address": me.get("address", ""), "phone": me.get("phone", ""),
            "review_link": "https://evil.example.com/x", "timezone": me["timezone"],
            "quiet_start": me["quiet_start"], "quiet_end": me["quiet_end"],
            "review_delay": me.get("review_delay", 120), "open_hour": me["open_hour"],
            "close_hour": me["close_hour"], "bays": me["bays"], "logo": "",
        }
        r = owner.put(f"{API}/settings", json=payload)
        assert r.status_code == 400

    def test_settings_quiet_order(self, owner):
        me = owner.get(f"{API}/auth/me").json()["tenant"]
        payload = {
            "name": me["name"], "address": me.get("address", ""), "phone": me.get("phone", ""),
            "review_link": "", "timezone": me["timezone"],
            "quiet_start": 18, "quiet_end": 10, "review_delay": 120,
            "open_hour": me["open_hour"], "close_hour": me["close_hour"], "bays": me["bays"], "logo": "",
        }
        r = owner.put(f"{API}/settings", json=payload)
        assert r.status_code == 400

    def test_invite(self, owner):
        r = owner.post(f"{API}/team/invite", json={
            "email": f"test_invite_{uuid.uuid4().hex[:8]}@example.com", "role": "staff"
        })
        assert r.status_code == 200
        assert "url" in r.json() and "/login?invite=" in r.json()["url"]


# =========================
# Public Booking
# =========================
class TestPublic:
    def test_public_garage(self):
        r = requests.get(f"{API}/public/garage/demo-auto-toronto")
        assert r.status_code == 200
        d = r.json()
        assert d["slug"] == "demo-auto-toronto"
        assert len(d["services"]) > 0

    def test_public_business(self):
        r = requests.get(f"{API}/public/business/demo-auto-toronto")
        assert r.status_code == 200

    def test_public_slots(self):
        tomorrow = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        g = requests.get(f"{API}/public/garage/demo-auto-toronto").json()
        r = requests.get(f"{API}/public/slots", params={"slug": "demo-auto-toronto", "service": g["services"][0]["id"], "date": tomorrow})
        assert r.status_code == 200
        assert "slots" in r.json()

    def test_public_booking_no_consent(self):
        # create a booking without consent box checked
        g = requests.get(f"{API}/public/garage/demo-auto-toronto").json()
        tomorrow = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        slots = requests.get(f"{API}/public/slots", params={
            "slug": "demo-auto-toronto", "service": g["services"][0]["id"], "date": tomorrow
        }).json()["slots"]
        if not slots:
            pytest.skip("No slots available")
        suffix = uuid.uuid4().hex[:3]
        payload = {
            "slug": "demo-auto-toronto", "service_id": g["services"][0]["id"],
            "date": tomorrow, "start": slots[0]["time"],
            "name": f"TEST Public {suffix}", "phone": f"647-555-03{int(time.time()) % 100:02d}",
            "make": "Ford", "model": "F150", "year": 2021, "plate": f"TPB{suffix}", "consent": False, "website": "",
        }
        r = requests.post(f"{API}/public/book", json=payload)
        # Could be 409 if slot taken; 200 is success
        assert r.status_code in (200, 409), r.text

    def test_public_honeypot(self):
        g = requests.get(f"{API}/public/garage/demo-auto-toronto").json()
        tomorrow = (datetime.now(TZ) + timedelta(days=1)).strftime("%Y-%m-%d")
        slots = requests.get(f"{API}/public/slots", params={
            "slug": "demo-auto-toronto", "service": g["services"][0]["id"], "date": tomorrow
        }).json()["slots"]
        if not slots:
            pytest.skip("No slots")
        payload = {
            "slug": "demo-auto-toronto", "service_id": g["services"][0]["id"],
            "date": tomorrow, "start": slots[0]["time"],
            "name": "BOT", "phone": "647-555-9999",
            "make": "X", "model": "Y", "year": 2020, "plate": "BOT1", "consent": False,
            "website": "spam.com",
        }
        r = requests.post(f"{API}/public/book", json=payload)
        assert r.status_code == 400


# =========================
# Admin / Platform
# =========================
class TestAdmin:
    def test_overview(self, admin):
        r = admin.get(f"{API}/admin/overview")
        assert r.status_code == 200
        d = r.json()
        assert "tenants" in d and "sms_mode" in d

    def test_tenants_list(self, admin):
        r = admin.get(f"{API}/admin/tenants")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_settings_masked(self, admin):
        r = admin.get(f"{API}/admin/settings")
        assert r.status_code == 200
        s = r.json()
        for k, v in s.items():
            if v.get("secret") and v.get("set"):
                # masked value starts with bullet or is 'set'
                assert "•" in v["value"] or v["value"] == "set"

    def test_numbers(self, admin):
        r = admin.get(f"{API}/admin/numbers")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_audits(self, admin):
        r = admin.get(f"{API}/admin/audits")
        assert r.status_code == 200


# =========================
# Cron auth
# =========================
class TestCron:
    def test_cron_unauth(self):
        r = requests.post(f"{API}/cron/tick", headers={"X-Webhook-Id": "test-1"})
        assert r.status_code == 401

    def test_cron_tick_ok_and_dup(self):
        wid = f"itest-{uuid.uuid4().hex[:8]}"
        hdr = {"Authorization": f"Bearer {CRON_SECRET}", "X-Webhook-Id": wid}
        r1 = requests.post(f"{API}/cron/tick", headers=hdr)
        assert r1.status_code == 200
        assert r1.json().get("accepted") is True
        assert r1.json().get("duplicate") is False
        r2 = requests.post(f"{API}/cron/tick", headers=hdr)
        assert r2.status_code == 200
        assert r2.json().get("duplicate") is True

    def test_cron_nightly_ok(self):
        wid = f"itest-nightly-{uuid.uuid4().hex[:8]}"
        hdr = {"Authorization": f"Bearer {CRON_SECRET}", "X-Webhook-Id": wid}
        r = requests.post(f"{API}/cron/nightly", headers=hdr)
        assert r.status_code == 200


# =========================
# Twilio webhook signature
# =========================
class TestWebhooks:
    def test_inbound_bad_sig(self):
        r = requests.post(f"{API}/webhooks/twilio/inbound", data={"MessageSid": "SM1", "From": "+16475551234", "Body": "HELLO"})
        # 403 (invalid signature) or 503 if twilio not configured
        assert r.status_code in (403, 503)


# =========================
# Messaging (owner)
# =========================
class TestMessaging:
    def test_conversations(self, owner):
        r = owner.get(f"{API}/conversations")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_manual_send_blocked_no_consent(self, owner):
        # find a contact with no consent (opted-out or none)
        contacts = owner.get(f"{API}/contacts").json()
        target = next((c for c in contacts if c.get("consent_status") in ("opted-out", "none")), None)
        if not target:
            pytest.skip("No opted-out contact")
        r = owner.post(f"{API}/messages/send", json={"contact_id": target["id"], "body": "Test blocked"})
        assert r.status_code == 400
