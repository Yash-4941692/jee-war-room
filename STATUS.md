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
  - `.github/workflows/db-backup.yml` — weekly Turso SQL dump committed to repo
    (`backups/warroom-dump.sql`), secrets TURSO_* in GitHub
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
  init_db() on cold start so new tables self-create on Turso.

## Accounts / data

- id 2 Yash (admin ALYWAY), id 3 Anshkumar (KQ8PIB), plus Yash-invited users
  id 4 shubh (KFYJRY), id 5 chiken (4N8AWV), id 6 Khimesh Patel (7L7YC5),
  id 7 Aman (I1Y8AB). Friendships: Yash↔shubh, Yash↔Khimesh, Yash↔Aman; chats live.
- Live credentials/tokens live OUTSIDE the repo in
  /home/user/secrets/jee-war-room/ (vercel-token, turso tokens, ngrok.yml).
- Password reset for users is available in-app via the admin Users panel
  (POST /api/admin/set-password); passwords themselves can never be retrieved.

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
