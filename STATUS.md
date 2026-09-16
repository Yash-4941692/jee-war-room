# JEE War Room — Status & Operations

## Live URL
https://matrimony-reminder-relic.ngrok-free.dev  →  127.0.0.1:8080
(ngrok static domain, free tier; tap "Visit Site" on the ngrok notice once per browser session)

## Accounts
- Yash — admin (id 2)
- Anshkumar — user (id 3)
- Buddy connection: not yet established — both users must log in, copy their friend code
  (Profile → My Code), and add each other. No chat ever existed in the DB prior to 2026-09-16;
  chat requires an accepted friendship.

## Always-on / self-healing (verified 2026-09-16)
Two independent layers bring the stack up and keep it up:
1. Managed workspace process: supervise.sh (infinite relaunch wrapper) → guardian.sh
2. Shell boot hooks in ~/.bashrc and ~/.profile (pgrep-guarded, setsid)

guardian.sh (flock-deduped, 5 s loop):
- ensures python3 server.py listens on :8080 (restarts it if dead)
- ensures ngrok runs and the public domain returns 200 (restarts tunnel if not)
- SANDBOX-REBOOT REPAIR (root cause of the 2026-09-16 outage): a workspace
  restore resets the ngrok binary to NON-executable and may drop files/config.
  ensure_ngrok() re-chmods the binary on every start attempt, re-downloads it
  if missing, and restores ~/.config/ngrok/ngrok.yml from deploy/ngrok.yml.
- POOLED BLUE/GREEN TUNNEL RESTARTS: ngrok runs with --pooling-enabled (free
  plan supports it). A replacement agent attaches to the domain BEFORE the old
  one is killed, so supervisor-side restarts have no ERR_NGROK_3200 window and
  overlapping agents no longer trigger ERR_NGROK_334.
- children started with the flock fd closed so the supervisor can always be replaced
- logs: data/logs/guardian.log, server.log, ngrok.log
- pidfiles: /tmp/jwr-app.pid, /tmp/jwr-ngrok.pid, lock /tmp/jwr-guardian.lock

Measured recovery (live kill tests 2026-09-16, continuous 3 s public probes):
- ngrok agent killed ……………… 1 failed probe (~7 s), then 200
- ngrok binary stripped of +x AND agent killed (post-reboot bug)
  … guardian re-chmoded + relaunched, ~9 s, then 200
- app server SIGKILL …………… 2 x 502 probes (~11 s), then 200
- guardian itself killed ………… respawned by wrapper within ~2 s
- EVERYTHING killed, then a shell starts … dotfile hook rebuilds full stack, 200 in ~6–15 s

Residual limitation (be honest): a full sandbox/machine reboot with no shell session
afterwards cannot be caught (no cron/root; the managed process does not survive a
machine reboot). Any later shell / arena action triggers the dotfile hook and recovery
is ~10–30 s. Observed reboots on 2026-09-16 also reset the ngrok binary's exec bit —
that failure is now auto-repaired (ensure_ngrok), and was verified by simulation.
If the ngrok page ever says endpoint offline, sending any chat message in Arena (which
runs a shell) or waiting ~1 minute and refreshing restores it automatically.

## Data & backups
- Live DB: data/warroom.db (SQLite, server-side; browser stores only opaque jwr_token)
- Backups: data/backups/warroom-YYYYmmdd-HHMMSS.db — taken at every boot and hourly,
  newest 24 kept (online SQLite backup API, safe while running).

## Deployed frontend version
index.html assets ?v=5 — offline-safe chat: red offline banner, optimistic bubbles,
"sending…", red "⚠ tap to retry", auto-retry on reconnect, append-only polling.

## Health checks
ss -ltn | grep -E ':8080|:4040'
curl -s -H 'ngrok-skip-browser-warning: true' -o /dev/null -w '%{http_code}\n' https://matrimony-reminder-relic.ngrok-free.dev/
tail -20 data/logs/guardian.log
