# Permanent free deployment — GitHub + Vercel + Turso

Architecture: **GitHub** (private code repo) → **Vercel Hobby** (free serverless
Python host; functions never "sleep", permanent HTTPS URL) → **Turso** (free
cloud SQLite/libSQL database in aws-us-east-1, next to Vercel's free region).

- No credit card anywhere.
- Data lives entirely in Turso; the function container is stateless.
- Each push to `main` auto-deploys via `.github/workflows/deploy-vercel.yml`.
- GitHub pings `/healthz` every 30 minutes (warmth + uptime): `keepalive.yml`.
- Weekly database dump: `db-backup.yml` uploads **private, expiring workflow
  artifacts** (redacted 90 days, full 30 days). It deliberately no longer
  commits a dump to the repo — a committed dump previously leaked password
  hashes, friend codes and live session tokens. See SECURITY.md §2.

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
    python tools/turso_io.py dump out.sql   # Turso -> REDACTED dump (git-safe)
    python tools/turso_io.py dump --full /tmp/private.sql   # complete, restorable;
                                            # refuses to write inside the worktree

## Account recovery

    python tools/account_recovery.py list                 # every account
    python tools/account_recovery.py verify --name Yash   # test a remembered password
                                                          # locally (never transmitted)
    python tools/account_recovery.py set-password --name Yash
    python tools/account_recovery.py codes --name Yash    # fresh recovery codes
    python tools/account_recovery.py revoke --all         # sign every device out

Needs TURSO_DATABASE_URL + TURSO_AUTH_TOKEN in the environment (falls back to the
local data/warroom.db without them). Passwords are one-way PBKDF2 hashes and can
never be read back — only verified or replaced. See SECURITY.md.

## Security checks

    python3 server.py &                   # local sqlite on :8080
    python3 tools/selftest_auth.py        # 58 HTTP-level auth/CSRF/throttle checks
    python3 tools/selftest_migration.py   # old-schema migration + at-rest checks
