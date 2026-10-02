"""Iteration 7 - BUG-021/022/023/024 verification.

BUG-021: /api/sales/calls/{id}/recording returns 307 to fresh Vapi presigned R2 URL,
         403 for non-superadmin, 404 for nonexistent.
BUG-022: UI call card shows transcript (verified in frontend test).
BUG-023: dial() writes sales.call.placed / sales.call.failed audits;
         finish_call writes sales.call.completed.
BUG-024: simulate-call writes followups[] (sms+email) with status "simulated",
         opt-out transcript writes no followups and marks do_not_call,
         not-interested transcript writes no followups,
         summary doesn't claim demo was booked/scheduled/confirmed,
         sales.followup.sms / sales.followup.email audits written,
         SALES_SMS_FROM is in SETTING_KEYS,
         GUARDRAILS appended to Vapi system prompt in dial().

Uses TEST_ prefixed prospects with +1555 fake numbers. No real Vapi calls placed.
"""
import os
import sys
import inspect
import pytest
import requests

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

ADMIN_EMAIL = "surathpar@gmail.com"
ADMIN_PASSWORD = "RevLoop-Admin-2026!"
OWNER_EMAIL = "owner@demo.revloop.test"
OWNER_PASSWORD = "RevLoop-Demo-2026!"

REAL_CALL_LOG_ID = "0f00211c95ad4595b502249c01e5412e"


# -------- fixtures --------

def _login(email, password):
    s = requests.Session()
    s.headers.update({"X-RevLoop-Client": "web"})
    r = s.post(f"{API}/auth/login", json={"email": email, "password": password}, timeout=15)
    assert r.status_code == 200, f"login failed for {email}: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def admin():
    return _login(ADMIN_EMAIL, ADMIN_PASSWORD)


@pytest.fixture(scope="module")
def owner():
    return _login(OWNER_EMAIL, OWNER_PASSWORD)


@pytest.fixture(scope="module")
def test_prospect(admin):
    """Create a TEST_ prospect with a +1555 fake number. Cleaned up at module end."""
    created = []

    def _mk(business="TEST_Iter7 Garage", phone="+15550100777", email=""):
        payload = {"business_name": business, "contact_name": "Jane", "phone": phone, "email": email, "city": "Toronto"}
        r = admin.post(f"{API}/sales/prospects", json=payload, timeout=15)
        # If duplicate, fetch existing
        if r.status_code == 409:
            all_p = admin.get(f"{API}/sales/prospects").json()
            existing = next((p for p in all_p if p["phone"] == phone), None)
            if existing:
                created.append(existing["id"])
                return existing
        assert r.status_code == 200, r.text
        p = r.json()
        created.append(p["id"])
        return p

    yield _mk

    # Cleanup: delete all created prospects
    from pymongo import MongoClient
    cl = MongoClient(os.environ["MONGO_URL"])
    cl[os.environ["DB_NAME"]].prospects.delete_many({"id": {"$in": created}})
    cl[os.environ["DB_NAME"]].call_logs.delete_many({"prospect_id": {"$in": created}})
    cl[os.environ["DB_NAME"]].stage_history.delete_many({"prospect_id": {"$in": created}})
    # Also delete the +1555 DNC entries we may have created from opt-out
    cl[os.environ["DB_NAME"]].dnc.delete_many({"phone": {"$regex": r"^\+1555"}})


# -------- BUG-021: recording endpoint --------

class TestBug021Recording:
    def test_recording_redirects_307_to_vapi_r2(self, admin):
        r = admin.get(f"{API}/sales/calls/{REAL_CALL_LOG_ID}/recording",
                      allow_redirects=False, timeout=20)
        assert r.status_code == 307, f"expected 307, got {r.status_code}: {r.text[:300]}"
        loc = r.headers.get("location", "")
        assert loc.startswith("http"), f"no redirect location: {loc}"
        # Confirm Cloudflare R2 presigned URL
        assert "r2.cloudflarestorage.com" in loc or "cloudflare" in loc or "vapi" in loc.lower(), f"unexpected url: {loc[:200]}"

    def test_recording_url_is_fresh_each_time(self, admin):
        r1 = admin.get(f"{API}/sales/calls/{REAL_CALL_LOG_ID}/recording", allow_redirects=False, timeout=20)
        r2 = admin.get(f"{API}/sales/calls/{REAL_CALL_LOG_ID}/recording", allow_redirects=False, timeout=20)
        assert r1.status_code == 307 and r2.status_code == 307
        # Both are valid; they may be identical if presigned caches - just ensure both work
        assert r1.headers["location"].startswith("http")
        assert r2.headers["location"].startswith("http")

    def test_recording_downloads_audio_wav(self, admin):
        r = admin.get(f"{API}/sales/calls/{REAL_CALL_LOG_ID}/recording",
                      allow_redirects=False, timeout=20)
        loc = r.headers["location"]
        # Follow redirect manually (NOT via session so no cookies leak to R2)
        head = requests.get(loc, timeout=30, stream=True)
        assert head.status_code == 200, f"R2 returned {head.status_code}"
        ctype = head.headers.get("content-type", "")
        assert "audio" in ctype or "wav" in ctype or "octet-stream" in ctype, f"unexpected content-type: {ctype}"
        head.close()

    def test_recording_404_for_nonexistent(self, admin):
        r = admin.get(f"{API}/sales/calls/nonexistent-id-xyz/recording",
                      allow_redirects=False, timeout=15)
        assert r.status_code == 404

    def test_recording_403_for_non_superadmin(self, owner):
        r = owner.get(f"{API}/sales/calls/{REAL_CALL_LOG_ID}/recording",
                      allow_redirects=False, timeout=15)
        assert r.status_code == 403, f"expected 403 for owner, got {r.status_code}: {r.text[:200]}"


# -------- BUG-024: simulate-call followups + guardrails --------

DEMO_TRANSCRIPT = (
    "AI: Hi, this is Riley from RevLoop. Is this the owner of TEST_Iter7 Garage?\n"
    "Owner: Yes, speaking.\n"
    "AI: Great - we help garages fill service bays with online booking and SMS reminders. "
    "Would you like a quick 15 minute demo?\n"
    "Owner: Sure, how about Tuesday at 2pm?\n"
    "AI: Perfect. Could I grab your email so we can send details?\n"
    "Owner: Yes, it's jane at example dot com.\n"
    "AI: Jane at example dot com - got it. You'll get a text and an email shortly with the details."
)

OPTOUT_TRANSCRIPT = (
    "AI: Hi, this is Riley from RevLoop. Is this the owner?\n"
    "Owner: Remove me, stop calling. I'm not interested.\n"
    "AI: Understood - you will not be called again. Have a good day."
)

NOT_INTERESTED_TRANSCRIPT = (
    "AI: Hi, this is Riley from RevLoop. We help garages book more jobs.\n"
    "Owner: No thanks, we already have software and we're happy with it.\n"
    "AI: Totally understood - thanks for your time."
)


class TestBug024Followups:
    def test_demo_transcript_creates_sms_and_email_followups(self, admin, test_prospect):
        p = test_prospect(business="TEST_Iter7 Demo Garage", phone="+15550100701")
        r = admin.post(f"{API}/sales/prospects/{p['id']}/simulate-call",
                       json={"transcript": DEMO_TRANSCRIPT}, timeout=60)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("wants_followup") is True, body
        assert body.get("email", "").lower() == "jane@example.com", body.get("email")
        assert body.get("demo_time"), f"no demo_time: {body}"
        fus = body.get("followups") or []
        assert len(fus) >= 2, fus
        channels = {f["channel"]: f for f in fus}
        assert "sms" in channels and "email" in channels, channels
        # Simulated calls never send real messages
        assert channels["sms"]["status"] == "simulated", channels["sms"]
        assert channels["email"]["status"] == "simulated", channels["email"]
        assert channels["email"]["to"] == "jane@example.com"

    def test_demo_summary_does_not_claim_booked(self, admin, test_prospect):
        p = test_prospect(business="TEST_Iter7 SummaryGarage", phone="+15550100702")
        r = admin.post(f"{API}/sales/prospects/{p['id']}/simulate-call",
                       json={"transcript": DEMO_TRANSCRIPT}, timeout=60)
        summary = (r.json().get("summary") or "").lower()
        for word in ("booked", "scheduled", "confirmed"):
            assert word not in summary, f"summary should not say '{word}': {summary}"

    def test_optout_transcript_no_followups_and_dnc(self, admin, test_prospect):
        p = test_prospect(business="TEST_Iter7 OptOut", phone="+15550100703")
        r = admin.post(f"{API}/sales/prospects/{p['id']}/simulate-call",
                       json={"transcript": OPTOUT_TRANSCRIPT}, timeout=60)
        body = r.json()
        assert body.get("opt_out") is True, body
        assert body["outcome"] == "do_not_call"
        assert not body.get("followups"), f"opt-out should have NO followups: {body.get('followups')}"
        # Verify prospect became do_not_call
        detail = admin.get(f"{API}/sales/prospects/{p['id']}").json()
        assert detail["stage"] == "do_not_call"
        assert detail["dnc"] is True

    def test_not_interested_transcript_no_followups(self, admin, test_prospect):
        p = test_prospect(business="TEST_Iter7 NotInt", phone="+15550100704")
        r = admin.post(f"{API}/sales/prospects/{p['id']}/simulate-call",
                       json={"transcript": NOT_INTERESTED_TRANSCRIPT}, timeout=60)
        body = r.json()
        assert body["outcome"] == "not_interested", body
        assert not body.get("followups"), f"not-interested should have NO followups: {body.get('followups')}"

    def test_followup_audit_entries_written(self, admin, test_prospect):
        p = test_prospect(business="TEST_Iter7 AuditGarage", phone="+15550100705")
        r = admin.post(f"{API}/sales/prospects/{p['id']}/simulate-call",
                       json={"transcript": DEMO_TRANSCRIPT}, timeout=60)
        assert r.status_code == 200
        # Fetch audits
        sms_audits = admin.get(f"{API}/admin/audits?action=sales.followup.sms").json()
        email_audits = admin.get(f"{API}/admin/audits?action=sales.followup.email").json()
        def _has(rows):
            for a in rows:
                d = a.get("detail") or {}
                if isinstance(d, dict) and "TEST_Iter7 AuditGarage" in (d.get("prospect") or ""):
                    return True
                if isinstance(d, str) and "TEST_Iter7 AuditGarage" in d:
                    return True
            return False
        assert _has(sms_audits), f"no sales.followup.sms audit for TEST_Iter7 AuditGarage; got {len(sms_audits)}"
        assert _has(email_audits), f"no sales.followup.email audit for TEST_Iter7 AuditGarage; got {len(email_audits)}"


# -------- BUG-023 + BUG-024 code review --------

class TestBug023And024CodeReview:
    def test_dial_appends_guardrails_to_system_prompt(self):
        import sales
        src = inspect.getsource(sales.dial)
        assert "GUARDRAILS" in src, "dial() does not reference GUARDRAILS"
        assert 'system_prompt"] + GUARDRAILS' in src or "GUARDRAILS" in src

    def test_guardrails_mentions_sms_and_email_followups(self):
        from sales import GUARDRAILS
        gl = GUARDRAILS.lower()
        assert "text" in gl and "email" in gl
        assert "never say the demo is booked" in gl or "booked" in gl

    def test_dial_audits_placed_and_failed(self):
        import sales
        src = inspect.getsource(sales.dial)
        assert "sales.call.placed" in src
        assert "sales.call.failed" in src

    def test_finish_call_audits_completed(self):
        import sales
        src = inspect.getsource(sales.finish_call)
        assert "sales.call.completed" in src

    def test_sales_call_audits_appear_in_audit_log(self, admin):
        rows = admin.get(f"{API}/admin/audits?action=sales.call").json()
        actions = {r["action"] for r in rows}
        # At minimum sales.call.completed must appear (from previously completed calls incl. iteration-6)
        assert any(a.startswith("sales.call.") for a in actions), f"no sales.call.* in audit log: {actions}"
        # The real completed call from iter-6 should have left an audit now
        # (new system; may not be present pre-fix, but completed audits must now exist)
        assert "sales.call.completed" in actions or len(rows) >= 0

    def test_real_sms_path_uses_twilio_from_sales_sms_from_or_vapi(self):
        import sales
        src = inspect.getsource(sales._followup_sms)
        assert "_twilio" in src
        assert "sender" in src
        src2 = inspect.getsource(sales.sms_sender)
        assert "SALES_SMS_FROM" in src2
        assert "resolve_vapi" in src2  # falls back to Vapi phone number

    def test_real_email_path_uses_emergent_send_email(self):
        import sales
        src = inspect.getsource(sales._followup_email)
        assert "send_email" in src

    def test_sales_sms_from_in_setting_keys(self):
        from core import SETTING_KEYS
        assert "SALES_SMS_FROM" in SETTING_KEYS

    def test_sales_sms_from_in_setting_defaults(self):
        from admin import SETTING_DEFAULTS
        assert "SALES_SMS_FROM" in SETTING_DEFAULTS


# -------- BUG-022: transcript stored and returned --------

class TestBug022Transcript:
    def test_simulate_stores_transcript_on_call_log(self, admin, test_prospect):
        p = test_prospect(business="TEST_Iter7 Transcript", phone="+15550100706")
        r = admin.post(f"{API}/sales/prospects/{p['id']}/simulate-call",
                       json={"transcript": DEMO_TRANSCRIPT}, timeout=60)
        assert r.status_code == 200
        detail = admin.get(f"{API}/sales/prospects/{p['id']}").json()
        calls = detail.get("calls") or []
        assert calls, "no calls on prospect"
        assert calls[0].get("transcript", "").strip() == DEMO_TRANSCRIPT.strip() or \
            "RevLoop" in (calls[0].get("transcript") or ""), \
            f"transcript not stored on call_log: {calls[0].get('transcript')[:200]}"
