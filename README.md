# JEE WAR ROOM

Private JEE Main accountability web app: PLAN → EXECUTE → LOG → ANALYZE → IMPROVE.
Python stdlib HTTP server + SQLite-compatible storage (local SQLite file or Turso
cloud database), 61 PCM chapters, deterministic Projected AIR (not a real rank
prediction), XP/streaks, targets, focus timer, duels, analytics, mocks, error
book, weekly report, rule-based coach, admin rulebook, and real-time 1:1 chat.

## Run locally

    python3 server.py            # http://localhost:8080, data in data/warroom.db

## Run against a cloud database (hosted mode)

Set two environment variables before starting:

    TURSO_DATABASE_URL=libsql://<db>-<org>.turso.io
    TURSO_AUTH_TOKEN=<token>

The same code then uses the cloud libSQL database instead of the local file.

## Deploy

See [DEPLOY.md](DEPLOY.md) for the free permanent deployment
(GitHub + Koyeb + Turso + GitHub Actions keep-alive/backups).

## Security

Passwords are one-way PBKDF2 hashes and can never be read back — not by the
admin, not from a backup. Account recovery uses **one-time recovery codes**;
the friend code links buddies and is deliberately *not* a credential.
Read [SECURITY.md](SECURITY.md) for the model, the incident that prompted the
hardening, and the admin to-do list.

    python3 tools/account_recovery.py --help    # recover/verify/reset an account
    python3 tools/selftest_auth.py              # 51 auth security checks
    python3 tools/selftest_migration.py         # old-schema migration checks

## Layout

- `server.py` — HTTP API + business logic
- `authsec.py` — password/session/recovery-code/throttling primitives
- `dbwrap.py` — database adapter (local sqlite3 / cloud libSQL)
- `syllabus.py` — 61 PCM chapters
- `public/` — single-page frontend (index.html, app.js, styles.css)
- `tools/turso_io.py` — cloud migration and SQL dump tooling (dumps are redacted
  by default; `--full` refuses to write inside the repo)
- `guardian.sh` / `supervise.sh` — interim sandbox keep-alive scripts
  (not used once the app is hosted on Koyeb)
