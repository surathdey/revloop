"""RevLoop Phase 3 (billing) and Phase 5 (AI sales) backend tests."""
import os
import time
import uuid
import pytest
import requests

BASE_URL = os.environ.get("REACT_APP_BACKEND_URL", "https://garage-sms-crm.preview.emergentagent.com").rstrip("/")
API = f"{BASE_URL}/api"

SUPER = {"email": "surathpar@gmail.com", "password": "RevLoop-Admin-2026!"}
OWNER = {"email": "owner@demo.revloop.test", "password": "RevLoop-Demo-2026!"}
STAFF = {"email": "staff@demo.revloop.test", "password": "RevLoop-Demo-2026!"}
WEB_H = {"X-RevLoop-Client": "web"}


def _login(creds):
    s = requests.Session()
    s.headers.update(WEB_H)
    r = s.post(f"{API}/auth/login", json=creds, timeout=20)
    assert r.status_code == 200, f"login {creds['email']}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def owner():
    return _login(OWNER)


@pytest.fixture(scope="module")
def staff():
    return _login(STAFF)


@pytest.fixture(scope="module")
def admin():
    return _login(SUPER)


# ========== Billing ==========
class TestBilling:
    def test_billing_owner_ok(self, owner):
        r = owner.get(f"{API}/billing")
        assert r.status_code == 200
        d = r.json()
        assert "plans" in d and "invoices" in d and "tenant" in d
        keys = {p["key"] for p in d["plans"]}
        assert {"starter", "growth", "pack"}.issubset(keys)
        # Validate CAD amounts
        starter = next(p for p in d["plans"] if p["key"] == "starter")
        growth = next(p for p in d["plans"] if p["key"] == "growth")
        assert starter["amount"] == 4900 and starter["quota"] == 300
        assert growth["amount"] == 9900 and growth["quota"] == 1000

    def test_billing_staff_forbidden(self, staff):
        assert staff.get(f"{API}/billing").status_code == 403

    def test_checkout_pack_without_sub_rejected(self, owner):
        # demo tenant has no stripe_subscription -> pack requires existing sub
        r = owner.post(f"{API}/billing/checkout",
                       json={"plan": "pack", "origin_url": "https://example.com"})
        assert r.status_code == 400, r.text

    def test_checkout_staff_forbidden(self, staff):
        r = staff.post(f"{API}/billing/checkout",
                       json={"plan": "starter", "origin_url": "https://example.com"})
        assert r.status_code == 403

    def test_checkout_unknown_plan_404(self, owner):
        r = owner.post(f"{API}/billing/checkout",
                       json={"plan": "nonexistent", "origin_url": "https://example.com"})
        assert r.status_code == 404

    def test_stripe_webhook_bad_signature(self):
        r = requests.post(f"{API}/stripe/webhook", data=b'{"id":"evt_test","type":"ping"}',
                          headers={"stripe-signature": "bad"})
        assert r.status_code == 400


# ========== Admin plans ==========
class TestAdminPlans:
    def test_admin_plans_list(self, admin):
        r = admin.get(f"{API}/admin/plans")
        assert r.status_code == 200
        plans = r.json()
        keys = {p["key"] for p in plans}
        assert {"starter", "growth", "pack"}.issubset(keys)
        growth = next(p for p in plans if p["key"] == "growth")
        assert growth["amount"] == 9900
        assert "stripe_price_id" in growth  # synced

    def test_admin_plans_owner_forbidden(self, owner):
        assert owner.get(f"{API}/admin/plans").status_code == 403

    def test_update_growth_price_and_restore(self, admin):
        # change to 109 then back to 99
        plans = admin.get(f"{API}/admin/plans").json()
        growth = next(p for p in plans if p["key"] == "growth")
        original_price_id = growth.get("stripe_price_id")
        try:
            r = admin.put(f"{API}/admin/plans/growth", json={
                "name": growth["name"], "amount": 10900, "quota": 1000, "segments": 0, "active": True
            })
            assert r.status_code == 200, r.text
            after = admin.get(f"{API}/admin/plans").json()
            g2 = next(p for p in after if p["key"] == "growth")
            assert g2["amount"] == 10900
            # new price id should be present (synced)
            assert g2.get("stripe_price_id")
            # price id may change since stripe immutable amounts
        finally:
            r2 = admin.put(f"{API}/admin/plans/growth", json={
                "name": growth["name"], "amount": 9900, "quota": 1000, "segments": 0, "active": True
            })
            assert r2.status_code == 200


# ========== Admin integrations surface Vapi/ElevenLabs ==========
class TestAdminIntegrations:
    def test_integration_keys_listed(self, admin):
        r = admin.get(f"{API}/admin/settings")
        assert r.status_code == 200
        settings = r.json()
        for key in ("VAPI_API_KEY", "VAPI_PHONE_NUMBER_ID", "VAPI_WEBHOOK_SECRET",
                    "ELEVENLABS_API_KEY", "ELEVENLABS_VOICE_ID", "BILLING_GRACE_DAYS"):
            assert key in settings, f"missing {key} in /admin/settings"


# ========== Sales access control ==========
SALES_ROUTES = [
    ("GET", "/sales/prospects"),
    ("GET", "/sales/analytics"),
    ("GET", "/sales/dnc"),
    ("GET", "/sales/script"),
    ("GET", "/sales/campaigns"),
    ("GET", "/sales/calls/export"),
]


class TestSalesAccess:
    @pytest.mark.parametrize("method,path", SALES_ROUTES)
    def test_owner_forbidden(self, owner, method, path):
        r = owner.request(method, f"{API}{path}")
        assert r.status_code == 403

    @pytest.mark.parametrize("method,path", SALES_ROUTES)
    def test_super_ok(self, admin, method, path):
        r = admin.request(method, f"{API}{path}")
        assert r.status_code == 200

    def test_vapi_webhook_no_secret(self):
        r = requests.post(f"{API}/webhooks/vapi", json={"message": {"type": "end-of-call-report"}})
        assert r.status_code == 403


# ========== Sales flow ==========
class TestSalesFlow:
    @pytest.fixture(scope="class")
    def test_phone(self):
        # 647-555-03xx range per instruction
        return f"+1647555{(int(time.time()) % 90 + 10) * 100 + 3:04d}"[:12]

    def test_import_prospects(self, admin):
        # Build unique phones using uuid to avoid collisions with prior runs
        rows = ["business_name,contact_name,phone,city"]
        phones = []
        for i in range(3):
            d = int(uuid.uuid4().hex[:3], 16) % 1000
            phone = f"647-777-{d:03d}{i}"  # distinct 10-digit style
            phones.append(phone)
            rows.append(f"TEST_Shop_{uuid.uuid4().hex[:6]},TEST Owner {i},{phone},Toronto")
        # Add duplicate row to validate dup detection
        rows.append(f"TEST_Shop_dup,TEST Owner d,{phones[0]},Toronto")
        csv_text = "\n".join(rows)
        r = admin.post(f"{API}/sales/prospects/import", json={"csv": csv_text})
        assert r.status_code == 200, r.text
        d = r.json()
        # 3 created + 1 dup (either in-batch dup against first row or against existing DB)
        assert d["created"] + d["duplicates"] + d["suppressed"] == 4
        assert d["duplicates"] >= 1

    def test_find_imported_prospect(self, admin):
        rows = admin.get(f"{API}/sales/prospects").json()
        # Find a TEST_ prospect in new stage
        p = next((x for x in rows if x["business_name"].startswith("TEST_Shop_") and x["stage"] == "new"), None)
        assert p is not None
        TestSalesFlow._pid = p["id"]

    def test_stage_change_history(self, admin):
        pid = TestSalesFlow._pid
        r = admin.post(f"{API}/sales/prospects/{pid}/stage", json={"stage": "contacted", "reason": "TEST manual"})
        assert r.status_code == 200
        d = admin.get(f"{API}/sales/prospects/{pid}").json()
        assert d["stage"] == "contacted"
        assert any(h["to"] == "contacted" for h in d["history"])

    def test_simulate_call_books_demo(self, admin):
        pid = TestSalesFlow._pid
        transcript = ("Riley: Hi, this is Riley from RevLoop, call is recorded. Is this the owner? "
                      "Owner: Yes speaking. Riley: Would you be open to a 15-minute demo? "
                      "Owner: Sure, let's book a demo for Thursday at 2pm. Riley: Great, I'll send a calendar invite. Thanks!")
        r = admin.post(f"{API}/sales/prospects/{pid}/simulate-call", json={"transcript": transcript})
        assert r.status_code == 200, r.text
        d = r.json()
        assert "summary" in d and d["summary"]
        assert d["outcome"] in ("demo_booked", "interested")
        p = admin.get(f"{API}/sales/prospects/{pid}").json()
        assert p["stage"] in ("demo_booked", "interested")

    def test_simulate_call_opt_out_moves_to_dnc(self, admin):
        # Create a fresh prospect for the opt-out test - use uuid-derived unique phone
        suffix = uuid.uuid4().hex[:4]
        d1 = int(uuid.uuid4().hex[:2], 16) % 10
        d2 = int(uuid.uuid4().hex[2:4], 16) % 100
        phone_raw = f"647-666-{d1}{d2:02d}0"  # 647-666-xxx0, outside 03xx test range
        tag = f"TEST_OptOut_{suffix}"
        csv_text = f"business_name,contact_name,phone,city\n{tag},TEST Owner,{phone_raw},Toronto"
        r = admin.post(f"{API}/sales/prospects/import", json={"csv": csv_text})
        assert r.status_code == 200, r.text
        imp = r.json()
        assert imp["created"] == 1, f"import did not create: {imp}"
        rows = admin.get(f"{API}/sales/prospects").json()
        p = next((x for x in rows if x["business_name"] == tag), None)
        assert p is not None, f"prospect {tag} not found; imp={imp}"
        pid = p["id"]
        phone = p["phone"]
        transcript = ("Riley: Hi, this is Riley from RevLoop, call recorded. "
                      "Owner: Stop calling me, remove me from your list. Riley: Of course, I apologise, you're off the list.")
        r = admin.post(f"{API}/sales/prospects/{pid}/simulate-call", json={"transcript": transcript})
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["outcome"] == "do_not_call"
        assert d["opt_out"] is True
        p2 = admin.get(f"{API}/sales/prospects/{pid}").json()
        assert p2["stage"] == "do_not_call"
        assert p2["dnc"] is True
        # DNC list contains the phone
        dnc_list = admin.get(f"{API}/sales/dnc").json()
        assert any(x["phone"] == phone for x in dnc_list)
        # Stage change API returns 400 after do_not_call (permanent)
        r3 = admin.post(f"{API}/sales/prospects/{pid}/stage", json={"stage": "contacted", "reason": "x"})
        assert r3.status_code == 400

    def test_dnc_import_rejects_phone(self, admin):
        base = (int(time.time()) % 20) + 80
        phone = f"647-555-03{base:02d}"
        r = admin.post(f"{API}/sales/dnc", json={"phones": phone, "reason": "TEST dncl"})
        assert r.status_code == 200
        assert r.json()["added"] == 1
        # Try to import a prospect with that phone -> suppressed
        csv_text = f"business_name,contact_name,phone,city\nTEST_Suppressed_{uuid.uuid4().hex[:4]},x,{phone},Toronto"
        r2 = admin.post(f"{API}/sales/prospects/import", json={"csv": csv_text})
        assert r2.status_code == 200
        d = r2.json()
        assert d["suppressed"] >= 1


# ========== Script + Campaigns ==========
class TestScriptCampaigns:
    def test_get_default_script(self, admin):
        r = admin.get(f"{API}/sales/script")
        assert r.status_code == 200
        d = r.json()
        assert "first_message" in d and "system_prompt" in d

    def test_save_script_requires_revloop_and_record(self, admin):
        # Bad opening - missing both keywords
        bad = {"first_message": "Hello this is a sales call about garage software please listen",
               "system_prompt": "You are a helpful sales caller for a garage SaaS platform.",
               "opt_out_phrases": "stop,remove me"}
        r = admin.put(f"{API}/sales/script", json=bad)
        assert r.status_code == 400, r.text

    def test_save_script_valid(self, admin):
        good = {"first_message": "Hi, this is Riley from RevLoop. This call is recorded for quality. Is this the owner?",
                "system_prompt": "You are Riley, a friendly sales caller for RevLoop garage software.",
                "opt_out_phrases": "stop,remove me,do not call"}
        r = admin.put(f"{API}/sales/script", json=good)
        assert r.status_code == 200

    def test_create_campaign_and_start_vapi_not_configured(self, admin):
        r = admin.post(f"{API}/sales/campaigns", json={"name": f"TEST Campaign {uuid.uuid4().hex[:5]}"})
        assert r.status_code == 200, r.text
        cid = r.json()["id"]
        # Appears in list with stats
        lst = admin.get(f"{API}/sales/campaigns").json()
        c = next(x for x in lst if x["id"] == cid)
        assert "stats" in c and "calls" in c["stats"]
        # Start -> expect 503 Vapi not configured
        r2 = admin.post(f"{API}/sales/campaigns/{cid}/status", json={"status": "running"})
        assert r2.status_code == 503, r2.text

    def test_test_call_vapi_not_configured(self, admin):
        r = admin.post(f"{API}/sales/test-call", json={"phone": "647-555-0399", "business_name": "TEST"})
        assert r.status_code == 503


# ========== CSV Export ==========
class TestExport:
    def test_calls_export_csv(self, admin):
        r = admin.get(f"{API}/sales/calls/export")
        assert r.status_code == 200
        assert "text/csv" in r.headers.get("content-type", "")
        body = r.text
        assert body.startswith("call_id,created_at,prospect,phone,")


# ========== Regression smoke ==========
class TestRegression:
    def test_owner_dashboard(self, owner):
        r = owner.get(f"{API}/dashboard")
        assert r.status_code == 200

    def test_owner_no_ai_sales(self, owner):
        # /api/sales/* must all be 403 for owner (covered above)
        pass
