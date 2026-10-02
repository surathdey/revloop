"""Iteration 6 - Vapi AI cold-call pipeline verification.

Covers:
  (1) resolve_vapi(cfg) resolves phone-number string + credential name to real Vapi IDs,
      and raises AppError(400) on bogus input.
  (2) The real test call made by the main agent is in the final 'completed' state,
      with duration/outcome/summary and the prospect progressed to 'lost'.
  (3) /api/sales/calls/{id}/sync is idempotent.
  (4) /api/webhooks/vapi auth: 403 on wrong/missing secret, 200 with correct secret
      via X-Vapi-Secret OR Authorization: Bearer (only safe status-update bodies).
  (5) Code-review structural checks against sales.py / cron.py.

NEVER posts an end-of-call-report for the real call and NEVER triggers /api/sales/test-call.
"""
import os
import re
import sys
import asyncio
import inspect
import pytest
import requests

# Make backend importable
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

BASE_URL = os.environ["REACT_APP_BACKEND_URL"].rstrip("/")
API = f"{BASE_URL}/api"

REAL_CALL_LOG_ID = "8d71cb02de8f415399c5e73e0032e2cc"
REAL_VAPI_CALL_ID = "01a0fad8-8cdd-7229-bfd0-0bcab9ee4c30"
EXPECTED_PHONE_ID = "e0be2ce1-428a-452b-bdbe-d2a9480162ee"
EXPECTED_CRED_ID = "4bb3b1bc-cd64-4ee8-8bd7-934f8ca07c9a"
REAL_PROSPECT_PHONE = "+16476094072"

ADMIN_EMAIL = "surathpar@gmail.com"
ADMIN_PASSWORD = "RevLoop-Admin-2026!"


# -------- fixtures --------

@pytest.fixture(scope="module")
def admin_session():
    s = requests.Session()
    s.headers.update({"X-RevLoop-Client": "web"})
    r = s.post(f"{API}/auth/login", json={"email": ADMIN_EMAIL, "password": ADMIN_PASSWORD}, timeout=15)
    assert r.status_code == 200, f"login failed: {r.status_code} {r.text}"
    return s


@pytest.fixture(scope="module")
def platform_cfg():
    from core import platform_config
    return asyncio.get_event_loop().run_until_complete(platform_config())


# -------- (1) resolve_vapi unit test --------

class TestResolveVapi:
    def test_resolves_phone_and_credential_to_ids(self, platform_cfg):
        from sales import resolve_vapi
        phone_id, cred_id = asyncio.get_event_loop().run_until_complete(resolve_vapi(platform_cfg))
        assert phone_id == EXPECTED_PHONE_ID
        assert cred_id == EXPECTED_CRED_ID

    def test_bogus_phone_raises_apperror_400(self, platform_cfg):
        from sales import resolve_vapi
        from core import AppError
        bad = dict(platform_cfg)
        bad["VAPI_PHONE_NUMBER_ID"] = "+15555550000"
        with pytest.raises(AppError) as ei:
            asyncio.get_event_loop().run_until_complete(resolve_vapi(bad))
        assert ei.value.status_code == 400
        assert "Vapi" in ei.value.detail


# -------- (2) real test call final state --------

class TestRealCallFinalState:
    def test_call_log_completed_with_summary_and_outcome(self, admin_session):
        # Fetch via prospect endpoint (super_ctx scoped)
        p = admin_session.get(f"{API}/sales/prospects").json()
        pros = next((x for x in p if x["phone"] == REAL_PROSPECT_PHONE), None)
        assert pros, "prospect for +16476094072 not found"
        detail = admin_session.get(f"{API}/sales/prospects/{pros['id']}").json()
        logs = detail["calls"]
        log = next((c for c in logs if c["id"] == REAL_CALL_LOG_ID), None)
        assert log, f"call log {REAL_CALL_LOG_ID} not found on prospect"
        assert log["status"] == "completed"
        assert log["duration_s"] == 84
        assert log["outcome"] == "not_interested"
        assert log["vapi_call_id"] == REAL_VAPI_CALL_ID
        assert log.get("summary") and len(log["summary"]) > 20
        assert log.get("ended_reason") == "customer-ended-call"
        assert log.get("recording_url", "").startswith("http")

    def test_prospect_lost_with_ai_caller_history(self, admin_session):
        p = admin_session.get(f"{API}/sales/prospects").json()
        pros = next((x for x in p if x["phone"] == REAL_PROSPECT_PHONE), None)
        assert pros["stage"] == "lost"
        detail = admin_session.get(f"{API}/sales/prospects/{pros['id']}").json()
        hist = detail["history"]
        assert any(h["to"] == "lost" and h["by"] == "ai-caller" for h in hist), hist


# -------- (3) sync is idempotent --------

class TestSyncIdempotent:
    def test_sync_returns_already_processed(self, admin_session):
        r = admin_session.post(f"{API}/sales/calls/{REAL_CALL_LOG_ID}/sync", timeout=20)
        assert r.status_code == 200, r.text
        body = r.json()
        assert body.get("status") == "already processed", body


# -------- (4) webhook auth --------

class TestVapiWebhookAuth:
    def _body(self):
        return {"message": {"type": "status-update", "call": {"id": "nonexistent-test-call"}}}

    def test_missing_secret_is_403(self):
        r = requests.post(f"{API}/webhooks/vapi", json=self._body(), timeout=10)
        assert r.status_code == 403

    def test_wrong_secret_is_403(self):
        r = requests.post(f"{API}/webhooks/vapi", json=self._body(),
                          headers={"X-Vapi-Secret": "not-the-real-secret"}, timeout=10)
        assert r.status_code == 403

    def test_correct_secret_header_200(self, platform_cfg):
        sec = platform_cfg["VAPI_WEBHOOK_SECRET"]
        r = requests.post(f"{API}/webhooks/vapi", json=self._body(),
                          headers={"X-Vapi-Secret": sec}, timeout=10)
        assert r.status_code == 200, r.text

    def test_correct_secret_bearer_200(self, platform_cfg):
        sec = platform_cfg["VAPI_WEBHOOK_SECRET"]
        r = requests.post(f"{API}/webhooks/vapi", json=self._body(),
                          headers={"Authorization": f"Bearer {sec}"}, timeout=10)
        assert r.status_code == 200, r.text


# -------- (5) code review structural checks --------

class TestCodeReviewInvariants:
    def test_dial_marks_failed_on_resolve_error(self):
        import sales
        src = inspect.getsource(sales.dial)
        # resolve_vapi is wrapped in try/except that updates status=failed
        assert 'resolve_vapi' in src
        assert '"status": "failed"' in src
        # Also marks failed when Vapi returns >=300 (RuntimeError branch)
        assert "status_code >= 300" in src
        assert "502" in src  # test-call surfaces as 502

    def test_cron_tick_calls_sync_stale(self):
        import cron
        src = inspect.getsource(cron._tick)
        assert "sync_stale_calls" in src

    def test_sync_stale_window_is_10_minutes(self):
        import sales
        src = inspect.getsource(sales.sync_stale_calls)
        assert "minutes=10" in src
