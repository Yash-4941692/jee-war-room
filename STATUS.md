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
- **Auth robustness (2026-10-13) — the "Not authenticated" bug:** `_auth()`
  tried the session COOKIE first and returned None if that session was gone,
  without ever checking the `Authorization: Bearer …` token the client sends in
  parallel. Any stale cookie (an older deployment's session, a session revoked
  on another device, a cookie left on the domain by a preview URL) therefore
  produced a random 401 — and app.js clears the stored token on a 401, so one
  bad cookie signed the user out permanently. Both credentials are now tried
  (`_auth_row()` per candidate); regression checks live in
  `tools/selftest_auth.py` ("stale cookie does NOT shadow a valid bearer
  token", "stale token does NOT shadow a valid session cookie", "a cookie-only
  stale session is still rejected") — the first one FAILS against the previous
  code, which is exactly what production was doing.
- **Cold-start / warmth (2026-10-13):** `.github/workflows/keepalive.yml` was
  **missing from the repo** even though DEPLOY.md/STATUS.md referenced it, so
  nothing kept the free-tier Turso database awake: the login POST was the first
  DB contact for a returning visitor and paid the ~15-20 s wake. Restored
  (twice an hour; ~1440 of 2000 free Actions minutes/month). The client also
  stopped *blocking* on `/healthz` before submitting the login form — the wake
  now starts at page load in the background (`warmUpServer()` fire-and-forget),
  so it overlaps with the user typing. A transient boot failure now shows a
  retry screen (with the session intact) instead of dumping the user on the
  login screen; only a real 401 clears the token.
- **SEO / discoverability (2026-10-13):** `robots.txt` and `sitemap.xml` were
  static files hard-coding **the wrong domain** (`jee-warfare.vercel.app`; the
  app runs on `jee-war-room-three.vercel.app`) — the sitemap was advertised on
  a host that does not exist and every URL in it belonged to another domain.
  They are now generated per request from the serving host (`site_base()`,
  override with `CANONICAL_HOST`), and `/llms.txt` was added for AI/agentic
  crawlers. The HTML pages carry the production base literally (`PROD_BASE`,
  `server.py`) rather than a placeholder, because Vercel's CDN serves
  `public/*.html` itself (filesystem before rewrites) and would otherwise ship
  an unsubstituted token to real visitors. `/` gained a crawlable landing block (features, how it works, FAQ)
  behind the login card, real meta description + canonical + Open Graph +
  Twitter tags, `WebApplication`/`WebSite`/`FAQPage` JSON-LD and an
  `og.png` preview card (1200x630, `tools/make_og_image.py`). `/tour.html` got
  the same meta treatment plus `WebPage`/`BreadcrumbList` JSON-LD, and its
  ~1 MB inline player+audio script moved to `public/tour.js` so the page itself
  is ~28 KB (the player is cached as an immutable `?v=` asset instead of being
  re-downloaded with the HTML on every visit). Missing assets now 404 instead
  of returning the HTML shell. See DEPLOY.md → "SEO / crawlers / agentic
  browsing" for the rules that keep this correct.
- **App icons + installability (2026-10-13):** the app had no usable icon
  anywhere except a browser tab — the favicon was an inline SVG *data URI*
  holding the 🎯 emoji. A data URI has no URL, so Chrome/Edge could not put an
  icon on an installed app (manifest icons must be fetchable files), iOS had
  nothing for the home screen, and Google Search, which needs a square
  crawlable icon at a multiple of 48px, showed a generic globe. Now `public/`
  ships real files drawn from the app palette by `python3 tools/make_icons.py`
  (deterministic Pillow render, supersampled; build-time only):
  `favicon.ico` (16+32+48), `favicon-96x96.png` (Google's size),
  `icon.svg`, `icon-192.png` / `icon-512.png` and `icon-maskable-192/512.png`
  (Android adaptive), `apple-touch-icon.png` (180, opaque). New
  `public/manifest.webmanifest` (standalone, `start_url /`, theme `#0a0d13`,
  192/512 `any` + `maskable`) makes the site installable as a real app, and
  `public/sw.js` — registered from `app.js`, inert by design, no `respondWith`,
  caches NOTHING — exists only because Chromium requires a service worker with
  a `fetch` handler before it offers the full install flow; caching stays the
  server's job so live study data is never served stale. `vercel.json` and
  `server.py`'s static handler both pin
  `Content-Type: application/manifest+json`. Guarded by
  `tools/js/icons_test.js` (no npm deps). Caveats: an already-installed app or
  home-screen shortcut keeps its old/blank icon until it is re-added, and
  Google re-reads favicons on its own crawl — days to weeks.
- **First-load budget (HARD CONSTRAINT, 2026-10-13):** the whole first screen
  (me + friends + announcements + chapters) is ONE request —
  `GET /api/bootstrap` — at **<= 30 SQL statements**; `GET /api/me` <= 12 and
  `GET /api/friends` <= 20, and the buddy list must NOT grow with the buddy
  count (verified by `tools/diag_boot_roundtrips.py`, which also proves the
  bootstrap payloads are byte-identical to the per-endpoint payloads and that
  the batched buddy list matches the old per-buddy code it replaced).
  Before: 4 parallel function invocations, `/api/me` 21 statements,
  `/api/friends` ~16 statements *per buddy* (164 with 10 buddies ≈ 4 s of
  Turso round trips on a warm connection). Now: 26 statements in one request.
  Rules that keep it there: `_begin_request()` resets a per-request memo
  (`app_settings` read once instead of once per buddy), `ensure_snapshots()`
  runs at most once per request, the buddy list uses `WHERE user_id IN (…)`
  bulk queries + one `streak_rows()` query for everyone, and counters are
  merged into `UNION ALL` statements. Serve with `/api/bootstrap` on the first
  load in the client (`loadBootstrap()`), with the old four-call fan-out as a
  fallback for a server that predates the endpoint (mid-deploy).
- **Assets/edge (2026-10-13):** `?v=N` versioned URLs are `immutable` for a
  year — **bump `?v=` in public/index.html and public/tour.html whenever
  styles.css/app.js change** (currently v24); HTML is `no-cache` + weak ETag
  (304s, bfcache) instead of `no-store`; the Docker/HF-Spaces path gzips text
  assets (app.js 145 KB → ~42 KB) and stops re-sending them on every visit.
  vercel.json mirrors the same Cache-Control rules for the edge-cached copies.
  Vercel Web Analytics is now the platform's own same-origin
  `/_vercel/insights/script.js` (deferred): the previous esm.sh ES-module
  import cost every first load a third-party DNS + TLS + module fetch and was
  blocked outright by the self-hosted CSP.
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
