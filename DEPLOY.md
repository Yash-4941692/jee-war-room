# Deploying JEE WAR ROOM to the permanent free cloud

Architecture: **GitHub** (code) → **Koyeb** free web service (runs the site,
auto-restarts, auto-redeploys on every code push) → **Turso** free cloud database
(SQLite-compatible, survives every redeploy) → **GitHub Actions** (pings the site
every 20 minutes so the free instance never sleeps; dumps the database weekly).

---

## Part 1 — Turso (the database), ~2 minutes

1. Open https://turso.tech and click **Sign in / Sign up with GitHub**
   (authorize the Turso app).
2. Click **Create Database**.
   - Name: `warroom`
   - Location/Region: **Frankfurt (fra)**
   - Leave other options default, confirm.
3. Open the database and copy its **Database URL** — it looks like
   `libsql://warroom-<yourname>.turso.io`.
4. Open the **Tokens** (or "Create Auth Token") section:
   - permission: **Full Access (Read & Write)**
   - expiration: **No expiration** (or the longest offered)
   - click **Create Token**, copy the long token shown once.
5. Send both values in the Arena chat:
   - `TURSO_DATABASE_URL = ...`
   - `TURSO_AUTH_TOKEN = ...`

The agent will then: create the cloud schema, migrate both accounts (Yash/Ansh,
including logins), push the code to GitHub, and wire up automatic backups.

## Part 2 — Koyeb (the host), ~3 minutes

1. Open https://app.koyeb.com/auth/signup → **Continue with GitHub**
   (authorize the Koyeb GitHub app; if asked which repositories, allow
   **jee-war-room**).
2. Click **Create Web Service**.
3. Source: **GitHub** → repository **jee-war-room**, branch **main**.
4. Builder: choose **Dockerfile** (it auto-detects `/Dockerfile`).
5. Region: **Frankfurt (Fra)**.
6. Instance: **Free (eco/nano)**.
7. Exposing the service (Networking section):
   - Port **8080**, protocol HTTP
   - Health check path: **/healthz**
   - public service path/name: **jee-war-room**
8. Environment Variables — add exactly two:
   - `TURSO_DATABASE_URL` = the Database URL from Part 1
   - `TURSO_AUTH_TOKEN` = the token from Part 1
9. Click **Deploy**. Build takes 1–3 minutes; the service gets a permanent URL
   like `https://jee-war-room-<yourorg>.koyeb.app`.

Send that URL in chat. The agent then enables the keep-alive and backup
automations pointing at it, and runs a full verification (signup/login/chat).

## After deployment

- The URL is permanent and never depends on the sandbox or ngrok again.
- Every code push to `main` auto-deploys (~1–2 min).
- GitHub pings `/healthz` every 20 minutes (free instance stay-awake) and
  commits a database dump every Monday (restore path, even if Turso hiccups).
- If the site is ever very briefly slow after a long quiet period, it is just
  the free instance waking (a few seconds); the pinger normally prevents it.
