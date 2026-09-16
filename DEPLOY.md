# Permanent free deployment — GitHub + Hugging Face + Turso

Architecture: **GitHub** (private code repo, auto-deploy, keep-alive) →
**Hugging Face Space** (free Docker host, permanent `*.hf.space` URL) →
**Turso** (free cloud SQLite database, survives every rebuild).

- No credit card anywhere.
- Free CPU Space sleeps only after **48 h of zero traffic**; the GitHub Actions
  hourly pinger prevents that, so it stays warm.
- Container disk is ephemeral — all durable data lives in Turso.
- Space repositories are public on the free tier, so no secrets are ever
  committed: the Turso URL/token are Space **secrets** (runtime env vars).

## One-time setup (already automated by the agent via APIs/tokens)

1. GitHub: private repo `<user>/jee-war-room`, branch `main`.
2. Turso: database `warroom`, seeded from the local SQLite file; app token stored
   outside git.
3. Hugging Face: Docker Space `<user>/jee-war-room`, secrets
   `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`; port 7860 (`deploy/HF_README.md`
   carries the Space metadata).
4. GitHub repository:
   - secrets: `HF_TOKEN`, `TURSO_DATABASE_URL`, `TURSO_AUTH_TOKEN`
   - variables: `APP_URL`, `HF_USER`, `HF_SPACE`

## After setup

- `git push origin main` → `.github/workflows/deploy-hf.yml` auto-deploys.
- Hourly pinger: `.github/workflows/keepalive.yml`.
- Weekly DB dump: `.github/workflows/db-backup.yml`.

## Local development

    python3 server.py                      # local SQLite at data/warroom.db
    TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... python3 server.py   # cloud DB

## Migration / backup tooling

    python tools/turso_io.py migrate      # local SQLite -> Turso
    python tools/turso_io.py dump out.sql # Turso -> SQL dump
