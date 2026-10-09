# JEE War Room — Status & Operations

## PERMANENT PRODUCTION (current)

- **URL:** https://jee-war-room-three.vercel.app
- **Host:** Vercel Hobby (free, no card), serverless Python — never sleeps;
  pinned to **bom1 (Mumbai)** via vercel.json `"regions": ["bom1"]` so data
  calls for Indian users stay inside India (warm ~0.2-0.5 s).
- **Database:** Turso libSQL, group `default` / region `aws-ap-south-1` (Mumbai)
  `libsql://jee-war-room-mum-yash-4941692.aws-ap-south-1.turso.io`
  Migrated 2026-09-16 (v8): full data copied; old US database `jee-war-room`
  (group useast, aws-us-east-1) left intact as a frozen rollback. Old
  credentials kept in secrets/libsql-*.useast.txt.
- **v8 performance work:** libsql client is cached per warm worker (no TLS
  handshake per request) with auto-reconnect; signup/reset insert 61 chapters
  in ONE batched multi-row INSERT (signup 22-30 s on US → ~0.6 s in Mumbai).
- **Auth hardening (see SECURITY.md):** one-time recovery codes replace the
  friend code for password reset; session tokens stored only as SHA-256 digests
  with a 30-day expiry; DB-backed throttling on login/signup/reset; identical
  errors for unknown-user and wrong-password; password changes revoke other
  sessions; CSRF origin check on cookie-authenticated POSTs; CSP + nosniff +
  Referrer-Policy headers; auth_events audit trail (admin-only);
  `tools/selftest_auth.py` (58 checks) and `tools/selftest_migration.py`.
- **Login round-trip budget (HARD CONSTRAINT):** on Vercel every SQL statement
  is a separate HTTPS round trip to Turso inside dbwrap's 7 s watchdog, under
  the function's 30 s budget (`maxDuration: 30`). A new-device login of a
  pre-existing account may cost **<= 8 statements; a repeat login <= 5**
  (verified by `tools/diag_login_roundtrips.py`; the pre-fix path cost 21/8
  and blew the budget on a waking free-tier DB — Vercel killed the function
  and the client saw a non-JSON platform 500 = the bare "Request failed (500)"
  toast). Rules that keep it there:
  * recovery codes are issued in ONE multi-row INSERT (like signup's 61
    chapter rows), never per-code INSERTs;
  * throttle gate = one `key IN (…)` SELECT; a successful auth clears all its
    counters with one `key IN (…)` DELETE; audit events are one batched
    multi-row INSERT per request;
  * housekeeping (expired sessions / stale counters / old events) NEVER runs
    inline on a login — it is kicked into a once-per-worker-hour background
    thread (`jwr-housekeeping`) from login/api_get;
  * recovery-code issuance is **best-effort / non-fatal**: if it fails, login
    still returns the token and a `recovery_codes_failed` audit event; the
    user regenerates codes in Settings → Account Security.
  * `_csrf_ok()`/`_auth()` run INSIDE the handler try/except and `_auth()` is
    skipped entirely on /api/auth/signup|login|reset-password; even stdlib
    low-level errors (400/501) answer JSON — no request path may produce a
    non-JSON error body (`tools/repro_login_500.py`).
  * `_auth()`/`recovery_status()` are schema-tolerant: on a half-migrated DB
    they DEGRADE to the pre-hardening query shape (cached detection) instead
    of 500-ing — and a transient DB failure must stay a 500, never a 401
    (app.js clears the token on 401 and would silently sign everyone out).
  * `init_db()` failures are no longer swallowed: the migration is retried
    lazily and the outcome is reported at `/healthz?detail=1` as `bootOk` /
    `bootError` (also on the 503 path, so a dead DB cannot hide the boot
    state). The lazy bootstrap lives in `server._ensure_boot()` and runs at
    the top of EVERY do_GET/do_POST, because Vercel's rewrite routing may
    hand a request to the root `server` function instead of `api/index` —
    whichever one serves the request must guarantee the schema was checked.
  * dbwrap NEVER reuses a client it has closed: the old `_reconnect()` handed
    the just-closed libsql client back out, and the next statement panicked
    inside the Rust binding (`PanicException` is a `BaseException`, so it
    escaped every handler as a response-less platform 500 — the other half of
    the "Request failed (500)" bug). All panics are now converted to ordinary
    `RuntimeError`s and poisoned clients replaced. Cloud `executescript`
    strips `--` comments before splitting (a `;` inside the recovery_codes
    comment used to break the DDL with "incomplete input").
- **v8 features:** admin-only 📢 Announce composer + per-user unread banner on
  Home/Today (`announcements` table; read state in settings.seenAnnouncements);
  admin 👥 Registered Users panel with password RESET (passwords are PBKDF2
  one-way hashes — never viewable/plaintext); 🗑️ Delete-chat clears the
  conversation on YOUR device only (messages.hidden comma-id list, buddy keeps
  theirs). Vercel preview deployments: SSO protection disabled so previews are
  testable before `vercel alias set` promotion (instant atomic swap).
- **Code:** private GitHub repo https://github.com/Yash-4941692/jee-war-room
- Vercel project: `jee-war-room` (env vars TURSO_DATABASE_URL/TURSO_AUTH_TOKEN set)
- Serverless entry: Vercel auto-detects `server.Handler`; vercel.json rewrites
  all paths with ?__path so routing is unchanged.
- Automations (verified 2026-09-16):
  - `.github/workflows/keepalive.yml` — pings /healthz twice an hour
  - `.github/workflows/db-backup.yml` — weekly Turso SQL dump uploaded as
    PRIVATE, EXPIRING workflow artifacts (redacted 90 d / full 30 d). It no
    longer commits anything to the repo: the old committed dump leaked password
    hashes, friend codes and live session tokens (see SECURITY.md §2).
    secrets TURSO_* in GitHub
  - `.github/workflows/deploy-vercel.yml` — MANUAL fallback only (CLI deploys
    from Actions stall; native Vercel Git connect is the recommended toggle:
    Vercel project → Settings → Git → connect Yash-4941692/jee-war-room, branch main)
- Deploying without Git connect: `bash /home/user/secrets/jee-war-room/vercel-deploy.sh`
- ZERO-DOWNTIME method (preferred): `vercel deploy` (preview, never --prod) →
  smoke-test the preview URL → catch-up sync any DB writes if data moved →
  `vercel alias set <preview-url> jee-war-room-three.vercel.app` (instant atomic
  swap; users never see a half-built app) → warm the new function with a few
  /healthz calls before announcing. The alias URL never changes.
- v8 API additions: GET /api/announcements; POST /api/announcements (admin);
  POST /api/announcements/read; POST /api/announcements/<id>/delete (admin);
  GET /api/admin/users (admin); POST /api/admin/set-password (admin);
  POST /api/messages/clear (per-user chat clear). api/index.py runs idempotent
  init_db() on cold start (background daemon thread since v8.3) so new tables
  self-create on Turso without stalling a cold boot.
- v8.1–v8.3 cold-start hardening (fixed intermittent 12–300 s hangs after idle):
  * DB URL uses stateless `https://` Hrana (no half-open WebSocket after freeze)
  * dbwrap: per-thread cached client replaced after 20 s idle; EVERY remote
    statement runs under a 7 s watchdog (fresh connect allowed ~14 s for a
    waking DB); poisoned clients are abandoned; reads retry once, writes fail
    fast (nonce-protected POSTs can safely resend)
  * cloud cold boot does a 1-query schema check, not the full DDL script
  * `/healthz?detail=1` reports connect_ms/query_ms/region for diagnosis
  * keepalive every 10 min with retry-through-wake loop
- Git/Vercel: GitHub repo IS connected (Settings→Git done 2026-09-16); pushes to
  main auto-deploy in ~15-30 s, atomically. Commits MUST be authored as
  Yash's GitHub identity or Vercel blocks the deployment:
  `git config user.email 304053128+Yash-4941692@users.noreply.github.com`
  (name Yash-4941692). vercel.json ignoreCommand cancels builds for commits
  containing `[skip deploy]` (used by the weekly db-backup bot commit).
- CLI zero-downtime alternative still works: `vercel deploy` (preview, env
  vars exist for production+preview; SSO protection is OFF) then
  `vercel alias set <preview> jee-war-room-three.vercel.app`.

## Accounts / data

- id 2 Yash (admin), id 3 Anshkumar, plus Yash-invited users id 4 shubh,
  id 5 chiken, id 6 Khimesh Patel, id 7 Aman. Friendships: Yash↔shubh,
  Yash↔Khimesh, Yash↔Aman; chats live.
  Friend codes are deliberately NOT listed here any more — they used to be
  treated as semi-public identifiers and the old reset endpoint accepted one as
  proof of identity. They are still visible to the admin in-app
  (Settings → Registered Users) and via `tools/account_recovery.py list`.
- Live credentials/tokens live OUTSIDE the repo in
  /home/user/secrets/jee-war-room/ (vercel-token, turso tokens, ngrok.yml).
- Passwords can NEVER be retrieved (PBKDF2-HMAC-SHA256, 120k iterations,
  per-user salt). Recovery paths, in order:
    1. the user's own one-time recovery codes (POST /api/auth/reset-password);
    2. the admin Users panel in-app (POST /api/admin/set-password);
    3. `tools/account_recovery.py` on a machine with the TURSO_* env vars
       (`verify` tests a remembered password locally, `set-password` replaces it,
       `codes` issues fresh recovery codes, `revoke --all` signs everyone out).
- Self-service reset no longer accepts a friend code — see SECURITY.md.
- After the auth-hardening deploy every user signs in once more (all pre-existing
  session tokens were destroyed because they had leaked into the committed dump)
  and receives 8 recovery codes on first login.

## Legacy interim stack (sandbox, best-effort — no longer primary)

- guardian.sh/supervise.sh keep a local server + ngrok tunnel alive while the
  sandbox runs: https://matrimony-reminder-relic.ngrok-free.dev
- It dies with sandbox reboots; production no longer depends on it.
- Binaries expected under /tmp/tools (ngrok, gh, turso, vercel) — outside the
  workspace snapshot; guardian re-downloads ngrok itself.

## Health checks

curl -s https://jee-war-room-three.vercel.app/healthz
vercel logs https://jee-war-room-three.vercel.app   (CLI, needs token)
turso db shell jee-war-room 'select name,role from users;'
