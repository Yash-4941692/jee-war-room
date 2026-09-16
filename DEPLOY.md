# Permanent free deployment — GitHub + Vercel + Turso

Architecture: **GitHub** (private code repo) → **Vercel Hobby** (free serverless
Python host; functions never "sleep", permanent HTTPS URL) → **Turso** (free
cloud SQLite/libSQL database in aws-us-east-1, next to Vercel's free region).

- No credit card anywhere.
- Data lives entirely in Turso; the function container is stateless.
- Each push to `main` auto-deploys via `.github/workflows/deploy-vercel.yml`.
- GitHub pings `/healthz` every 30 minutes (warmth + uptime): `keepalive.yml`.
- Weekly plain-SQL dump of the database is committed to this private repo:
  `db-backup.yml`.

## Live endpoints

- App: https://jee-war-room-three.vercel.app
- Health: https://jee-war-room-three.vercel.app/healthz

## Repository configuration (already set once)

Secrets: `VERCEL_TOKEN`, `VERCEL_ORG_ID`, `VERCEL_PROJECT_ID`,
`TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
Variables: `APP_URL`

## How the serverless port works

- `server.Handler` is a stdlib `http.server.BaseHTTPRequestHandler`; Vercel's
  Python runtime invokes that class directly (framework-less functions).
- `vercel.json` rewrites every path to `/api/index?__path=...`; the handler
  restores the original path in `Handler._restore_vercel_path()`.
- `dbwrap.py` connects to Turso when `TURSO_DATABASE_URL` is set, otherwise to
  the local SQLite file (dev).
- Static files are served by the same handler; nothing else is needed.

## Local development

    python3 server.py                       # local SQLite at data/warroom.db
    TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... python3 server.py   # cloud DB

## Database tooling

    python tools/turso_io.py migrate        # local SQLite -> Turso
    python tools/turso_io.py dump out.sql   # Turso -> SQL dump
