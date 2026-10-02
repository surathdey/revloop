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

## Implemented (2026-10-02, iteration 4 - retest round 2: BUG-005/010/011/018/019/020)
- Settings show their defaults in Integrations; a YES reply confirms the nearest unconfirmed appointment when no reminder links it; appointment variables greyed out on Service due templates; plan upgrade/downgrade with proration (POST /api/billing/change-plan); CSV rejects invalid vehicle years; customer count shown
- Sheet: /app/frontend/public/RevLoop_Defect_Resolution_v3.xlsx; tests iteration_5 108/108

## Implemented (2026-10-02, iteration 3 - customer QA defects BUG-001..017)
- Access denied page + audited 403 for role and cross-garage access; forgot/reset password by email (Emergent Resend); idle session timeout; customer and vehicle edit; online bookings per slot (rejects double booking); no reminders queued without consent; YES/CANCEL applies to the right appointment; template saved/unsaved indicator; manual send also runs the service-due check; audit of logins and consent opt-ins with actor/date filters; Vapi/ElevenLabs health; default trial days; admin create garage; buy Twilio number; manual add prospect
- Resolution sheet: /app/frontend/public/RevLoop_Defect_Resolution.xlsx
- Tests: iteration_4 - 100/100 backend; UI selectors verified

## Implemented (2026-10-01, iteration 2)
- Phase 3 Stripe billing (claimable Stripe test sandbox, CA account): plans live in the `plans` collection and super-admins edit them in Platform admin → Plans, which re-syncs CAD prices by lookup_key (Starter $49/300, Growth $99/1000, 500-SMS pack $15). Includes checkout (subscription + one-time pack), status polling, a signature-verified idempotent webhook at /api/stripe/webhook, grace period (BILLING_GRACE_DAYS, default 7), invoices list, customer portal, and an owner Billing tab
- Phase 5 AI sales module (/sales, super-admin only): prospect CSV import with dedupe and DNC suppression, a 7-stage Kanban with stage history, a permanent do-not-call list (bulk paste for National DNCL), exportable call-attempt CSV, an agent script editor (opening line must identify RevLoop and give the recording notice), campaigns (calling window clamped to CRTC hours, attempt caps, retry spacing), and Vapi dialing from the cron tick. The Vapi webhook is secret-verified; GPT summaries come from gpt-5.6-terra (Universal Key). In-call opt-outs go to DNC automatically, and outcomes update the funnel stage. There's also a simulated-call endpoint for testing without Vapi
- Tests: iteration_3 — 81/81 backend, all critical frontend flows pass

## Implemented (2026-10-01, iteration 1)
- Phase 0: auth (password + Google), roles, tenant scoping, audit log, encrypted settings, cron endpoints with bearer secret + idempotency
- Phase 1: CRM, quick-add, CSV import with duplicate flagging (tested at 500 rows), search, bay calendar, booking with conflict checks, status changes with service record, public booking with unbundled optional consent, honeypot and rate limits, cancel link, QR, dashboard KPIs
- Phase 2: templates with variables, live preview and segment counter; confirmation and 24h/2h reminders; review request with click tracking (/api/r/{token}) and a 3-day follow-up; service-due scan; quiet hours; 30-day review cap; dedupe keys; STOP/START/HELP/YES/CANCEL handling; consent snapshot on every send; Twilio signature-verified webhooks
- Minimal Phase 4: tenant approve/suspend/activate/cancel, number pool + assign (sets the Twilio inbound webhook automatically), Twilio creds encrypted (masked in UI), SMS mode live/simulated, audited support impersonation, cross-tenant audit log
- Tests: iteration_2 — backend 44/44, frontend all critical flows pass

## Implemented (2026-10-02, iteration 7) — QA tracker BUG-021..024
- BUG-021: call recordings play via GET /api/sales/calls/{id}/recording (superadmin; redirects to a fresh Vapi presigned URL); audio player in call card
- BUG-022: transcript viewer in each call card
- BUG-023: audit entries sales.call.placed / failed / completed + sales.followup.sms / email
- BUG-024: GUARDRAILS appended to Vapi system prompt; after interested/demo/details-requested calls, recap SMS (Twilio, from SALES_SMS_FROM or the Vapi Twilio calling number) and recap email (if the prospect gave one) are really sent; simulated calls record follow-ups as 'simulated'; summary says 'demo requested'
- Tests: iteration_7 — 20/20 backend, frontend pass

## Backlog
- P1: rename the 'Demo booked' stage label to 'Demo requested'
- P0: Vapi account → add VAPI_API_KEY / VAPI_PHONE_NUMBER_ID / VAPI_WEBHOOK_SECRET (+ ElevenLabs voice) in Integrations, then run a test AI call; choose the AI caller ID
- P0: claim the Stripe sandbox to go live; switch SMS_MODE to live
- P1: copy call recordings into object storage; Phase 4 rest (feature flags, auto-provision numbers)
- P2 Phase 6: acceptance run, backup docs, delivery notes
## Old backlog
- P0 Phase 3: Stripe CAD subscriptions, quota/overage packs, grace period, invoices, billing UI
- P0: switch SMS_MODE to live, assign +12896721172 to the user's real garage, run a live STOP test
- P1 Phase 4 rest: plan management + feature flags, auto-provision numbers, global defaults
- P1 Phase 5: AI sales module (prospects, Kanban, DNC, Vapi campaigns, GPT 5.6 summaries) — needs a Vapi account
- P2 Phase 6: acceptance run, backup docs, delivery notes, email for invites (Resend)

## Next tasks
Phase 3 Stripe billing.
