# RevLoop — PRD

## Original problem statement (customer's words, condensed headings kept verbatim)
"RevLoop — Multi-Tenant Garage SaaS + AI Sales Platform. Web application (mobile-responsive; garage owners work primarily from phones). A multi-tenant SaaS for independent GTA auto garages: lightweight CRM, appointment booking, CASL-compliant automated SMS (confirmations, reminders, review requests, service-due), Stripe billing in CAD — plus an internal platform admin and an AI cold-calling sales module for the RevLoop team."
Stack: React + TypeScript, FastAPI, MongoDB, ported from github.com/surathdey/revloop (Next.js/Prisma, the behavioural reference). Integrations: Twilio, Stripe, Google OAuth, Vapi (later), GPT 5.6 summaries (later), Object storage. Phases 0–6 as specified (migration spine, garage core, messaging+CASL, billing+settings, platform admin, AI sales module, hardening). Hardening rules: webhook signature verification + idempotency, tenant isolation on every query, env/encrypted secrets, CASL, CRTC/DNCL, rate limiting.
User choices: deliver phase-wise; Twilio keys provided ("all information should be configurable, not hardcoded"); Vapi and AI caller ID configured later; GPT 5.6 for summaries.

## Architecture
- backend/: server.py (routers, X-RevLoop-Client CSRF guard, startup seed), core.py (db, Scoped tenant helper, Fernet-encrypted platform_settings, audit, rate limit), policy.py (phone/consent/quiet-hours/segments/templates), auth.py (sessions, password + Emergent Google, invites, roles), crm.py, appointments.py, messaging.py (queue, Twilio send, inbound/status webhooks), public.py, admin.py, cron.py, seed.py
- Session cookie `session_token` (hashed in DB, 7 days), with impersonation stored on the session
- Platform crons in .emergent/crons.yml: queue tick every 15 min, service-due scan nightly at 3am Toronto
- frontend/src: TypeScript pages (Login, Dashboard, Customers, CustomerDetail, Calendar, Conversations, Automations, Settings, Admin, PublicBooking, CancelBooking, Business)

## Personas
Super-admin (RevLoop team), Garage owner, Garage staff (Calendar/Customers/Inbox only, enforced server-side)

## Implemented (2026-10-01)
- Phase 0: auth (password + Google), roles, tenant scoping, audit log, encrypted settings, cron endpoints with bearer secret + idempotency
- Phase 1: CRM, quick-add, CSV import with duplicate flagging (tested at 500 rows), search, bay calendar, booking with conflict checks, status changes with service record, public booking with unbundled optional consent, honeypot and rate limits, cancel link, QR, dashboard KPIs
- Phase 2: templates with variables, live preview and segment counter; confirmation and 24h/2h reminders; review request with click tracking (/api/r/{token}) and a 3-day follow-up; service-due scan; quiet hours; 30-day review cap; dedupe keys; STOP/START/HELP/YES/CANCEL handling; consent snapshot on every send; Twilio signature-verified webhooks
- Minimal Phase 4: tenant approve/suspend/activate/cancel, number pool + assign (sets the Twilio inbound webhook automatically), Twilio creds encrypted (masked in UI), SMS mode live/simulated, audited support impersonation, cross-tenant audit log
- Tests: iteration_2 — backend 44/44, frontend all critical flows pass

## Backlog
- P0 Phase 3: Stripe CAD subscriptions, quota/overage packs, grace period, invoices, billing UI
- P0: switch SMS_MODE to live, assign +12896721172 to the user's real garage, run a live STOP test
- P1 Phase 4 rest: plan management + feature flags, auto-provision numbers, global defaults
- P1 Phase 5: AI sales module (prospects, Kanban, DNC, Vapi campaigns, GPT 5.6 summaries) — needs a Vapi account
- P2 Phase 6: acceptance run, backup docs, delivery notes, email for invites (Resend)

## Next tasks
Phase 3 Stripe billing.
