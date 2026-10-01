# Auth testing notes
- Session cookie `session_token` (httpOnly, Secure, SameSite=None). Bearer header fallback works.
- Password login: POST /api/auth/login. Brute force: 5 failures per ip:email → 15 min lock.
- Google (Emergent Auth): frontend redirects to https://auth.emergentagent.com/?redirect=<origin>/ ; callback hash #session_id=... → POST /api/auth/google.
- To test an auth-gated page in a browser without password: insert into `sessions` {id: sha256(raw_token), user_id, expires_at, impersonated_tenant_id: null} and set cookie session_token=raw_token.
- Roles: superadmin, owner, staff. Staff blocked server-side (403) from /api/dashboard, /api/settings*, /api/templates*, /api/services POST, /api/team/invite, /api/contacts/{id}/consent, /api/messages/process, /api/phone/test, /api/admin/*.
