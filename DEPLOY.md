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

⚠️ `TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN` must be scoped to **every
environment that needs a database** (Vercel → Settings → Environment
Variables → Environments). Scoping them to Production only leaves Preview
deployments with no database at all — previously that killed the function at
import time (a platform 500, the bare "Request failed (500)" toast on every
login attempt). The code now degrades instead: pages load, every API call
answers a JSON 503, and `GET /healthz?detail=1` spells out the missing
variable (`bootOk: false`, `bootError: "TURSO_DATABASE_URL is not set for
this Vercel environment…"`).

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

## ⚠️ The login round-trip budget (hard constraint)

On Vercel each SQL statement is a separate HTTPS round trip to Turso inside
dbwrap's 7 s watchdog, and the function has a 30 s budget (`maxDuration: 30`
in api/index.py). The new-device login of a pre-existing account must stay at
**<= 8 statements, and a repeat login at <= 5** — anything more can blow the
budget on a waking free-tier database, after which Vercel kills the function
and the client sees a non-JSON platform 500 (the bare "Request failed (500)"
toast; the login never completes).

Keep it there by: batching every multi-row write into ONE statement (recovery
codes, chapter seeds, audit events), checking/clearing throttle keys with a
single `key IN (…)`, skipping `_auth()` on /api/auth/signup|login|reset-password,
and never running housekeeping inline on an auth path (it runs in a background
worker, once per worker-hour).

Measure before merging any change to the auth paths:

    python3 tools/diag_login_roundtrips.py    # prints the per-statement budget
    python3 tools/repro_login_500.py          # un-/half-migrated DB must still answer JSON

## ⚠️ Preview deployments share the PRODUCTION database

Vercel env vars (`TURSO_DATABASE_URL` / `TURSO_AUTH_TOKEN`) are set for
**production and preview**, and `api/index.py` runs `server.init_db()` at import
time. So the first cold start of *any* PR preview deployment executes schema
migrations against your **live** database — before you merge, and before you
announce anything to your users.

How the code defends against this (`server.py`):

- **Additive** steps (tables, indexes, columns) run everywhere — the new code
  needs them to work at all.
- **Destructive, one-way** steps are gated by `destructive_migrations_allowed()`,
  which returns False when `VERCEL_ENV=preview`. Override for a deliberate test
  with `ALLOW_PREVIEW_MIGRATION=1`.
- Each destructive step is recorded in the `schema_migrations` table, and the
  purge targets only rows the pre-hardening code could have written
  (`sessions.expires_at = 0`), so it is idempotent and can never sign out
  somebody who logged in after the upgrade.
- `init_db()`'s cloud fast path checks the migration record too, so a preview
  that created the new tables cannot make production skip the purge.

Verify after any deploy:

    curl -s "https://jee-war-room-three.vercel.app/healthz?detail=1"
    # bootOk: true                     <- init_db() succeeded (else bootError
    #                                     explains the failed/half migration;
    #                                     it is retried lazily per request)
    # pendingMigrations: []            <- nothing owed
    # destructiveMigrationsAllowed: true
    # vercelEnv: "production"

`api/index.py` runs `server.init_db()` on cold start, but no longer swallows a
failure: the outcome is recorded in `server.BOOT_INFO`, retried lazily on every
following request until it lands, and surfaced as `bootOk` / `bootError` at
`/healthz?detail=1` — a failed migration is visible without reading logs.

**Recommended permanent fix:** give preview deployments their own Turso
database, or scope `TURSO_*` to Production only in Vercel → Settings →
Environment Variables. Until then, treat every PR preview as a deployment that
touches live data.

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
