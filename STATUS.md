# JEE War Room — Status & Operations

## PERMANENT PRODUCTION (current)

- **URL:** https://jee-war-room-three.vercel.app
- **Host:** Vercel Hobby (free, no card), serverless Python — never sleeps;
  cold starts ~0.3-0.7 s, warm requests ~0.2 s.
- **Database:** Turso libSQL, group `useast` / region `aws-us-east-1`
  `libsql://jee-war-room-yash-4941692.aws-us-east-1.turso.io`
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

## Accounts / data

- id 2 Yash (admin, code ALYWAY), id 3 Anshkumar (user, code KQ8PIB)
- No friendships/messages yet — connect with friend codes in-app.
- Live credentials/tokens live OUTSIDE the repo in
  /home/user/secrets/jee-war-room/ (vercel-token, turso tokens, ngrok.yml).
- Production cloud DB verified clean: only the two real accounts, 123 chapters.

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
