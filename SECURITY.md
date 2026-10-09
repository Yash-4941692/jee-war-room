# SECURITY

How authentication works in JEE WAR ROOM, what was wrong with it, and what was
fixed. Read the **Action required** section at the bottom first if you are the
admin — two items there need doing by hand.

---

## 1. "Can you tell me my password?" — no, and that is the correct answer

Passwords are hashed with **PBKDF2-HMAC-SHA256, 120,000 iterations**, each with
its own 16-byte random salt (`authsec.hash_pw`). The database stores only the
digest. There is no plaintext copy anywhere — not in the database, not in a
config file, not in a backup. Hashing is one-way, so nobody (including the
developer, including anyone with full database access) can read a password back
out. The only operations possible are:

* **verify** a candidate password (hash it the same way, compare in constant
  time), and
* **replace** it with a new one.

`tools/account_recovery.py` does both, locally, without ever transmitting a
password:

```bash
export TURSO_DATABASE_URL=libsql://<db>-<org>.turso.io
export TURSO_AUTH_TOKEN=<token>

python3 tools/account_recovery.py verify --name Yash        # type guesses at a prompt
python3 tools/account_recovery.py set-password --name Yash  # set a new one
python3 tools/account_recovery.py codes --name Yash         # issue fresh recovery codes
python3 tools/account_recovery.py revoke --all              # sign every device out
```

Never paste a real password into a chat, issue tracker or email. Run the tool
yourself; the guesses stay on your machine.

### Why a remembered password can be rejected

Before this hardening the flows disagreed about whitespace:

| flow | old behaviour |
|---|---|
| `signup` | stored the password **exactly as typed** |
| `login` | compared the password **exactly as typed** |
| `reset-password` | stored it **trimmed** |
| `change-password` | compared and stored it **trimmed** |
| `admin set-password` | stored it **trimmed** |

So a password set through reset / change / admin-reset was stored without its
surrounding spaces, while login compared the raw string — one stray space from
autofill or a mobile keyboard and the correct password was rejected forever.
The reverse case (a trailing space typed at signup) was just as sticky.

Now **every** flow normalizes through `authsec.normalize_pw`, and
`authsec.verify_pw` additionally accepts an old un-trimmed hash, reports it as
`legacy`, and login re-hashes it into the normalized form on the spot — those
accounts repair themselves the first time their owner signs in.

Note what this does *not* do: it cannot recover a password that was typed with
spaces at signup and is now being typed without them. That string was never the
stored password. For those accounts use a recovery code or the CLI above.

---

## 2. Incident: live credentials were committed to this repository

`backups/warroom-dump.sql` was committed by `.github/workflows/db-backup.yml`
every Monday. It was a complete database dump, which means it contained:

* every user's **PBKDF2 hash and salt** (offline cracking material),
* every user's **friend code** — which the old reset endpoint accepted as proof
  of identity, so this was effectively a password-equivalent for all 14 accounts,
* **57 live plaintext session tokens**, 19 of them for the admin account.

`_auth()` accepted a token from an `Authorization: Bearer` or `X-Auth-Token`
header and looked it up in plaintext. Anyone with read access to the repo could
therefore sign in as any user — including the admin — with no password at all:

```
curl -H "Authorization: Bearer <token-from-the-dump>" https://<host>/api/me
```

**Fixed by:**

1. deleting the file and ignoring `backups/` and `*.sql` in `.gitignore`;
2. making the weekly workflow upload **private, expiring artifacts** instead of
   committing anything, and writing the *redacted* dump by default
   (`tools/turso_io.py` strips hashes, salts, codes, tokens and message bodies,
   then refuses to write a file that still contains a 64-hex digest);
3. storing session tokens **only as SHA-256 digests**, so a future leak of the
   sessions table is not a pile of working logins;
4. **destroying all pre-existing sessions** in the schema migration
   (`_auth_migrate`), which invalidates every token in that dump. Each user
   signs in once more after deploy — that is deliberate and required.

> The old dump still exists in git history on `main`. Item 3 and 4 make its
> contents useless, but see **Action required** for purging it properly.

---

## 3. The friend code was a password-reset factor

The old `/api/auth/reset-password` took `{name, code, newPassword}` where `code`
was the **friend code** — the value the app instructs every user to share with
up to 10 buddies (`index.html`: *"Your private friend code — share it with the
buddies you want to connect with"*). Anyone holding that code could set a new
password and take over the account. It was also:

* 6 characters from a 34-symbol alphabet, generated with `random.choices`
  (Mersenne Twister — **not** cryptographically secure; its state can be
  recovered from a handful of outputs),
* checked with **no rate limiting**, so it could be brute-forced,
* and it leaked account existence (`404 "No account found with that name"` vs
  `400 "Incorrect friend code"`).

### Replacement: one-time recovery codes

* 8 codes per user, format `XXXX-XXXX-XXXX`, from an unambiguous alphabet
  (no `0/O/1/I`), generated with `secrets`.
* Only **SHA-256 digests** are stored. Plaintext is returned exactly once —
  at signup, or on first login for accounts that predate the feature — and the
  UI shows a save/copy/download screen that must be acknowledged.
* Each code is **consumed on use**. Resetting signs out every other session.
* Users can generate a new set in Settings → Account Security; doing so
  destroys the previous unused codes, so a lost printout cannot be replayed.
* The friend code still exists for what it is good at — linking buddies — and is
  now generated with a CSPRNG. It is **never** accepted as a credential: the
  endpoint rejects a too-short code with an explanation before touching the
  database, so nothing about the account is revealed.
* Lost all your codes? The admin resets the password from Settings → Registered
  Users, or via the CLI.
* **Issuance is best-effort.** Codes are a convenience, not a credential: if
  writing them fails (e.g. a Turso blip mid-issuance), signup/login still
  succeed and return the session token, and a `recovery_codes_failed` event is
  recorded in the audit trail. The user simply generates a new set in
  Settings → Account Security. A code write can never block a legitimate
  sign-in.

---

## 4. Everything else that was hardened

| Area | Before | After |
|---|---|---|
| Brute force | none | DB-backed throttling, shared across serverless instances: **5 failures per username → 60 s, doubling to 1 h**; **30 per IP / 15 min → 15 min block**; 10 signups per IP per hour; 20 friend-code lookups per 10 min. `429` + `Retry-After`. |
| User enumeration | distinct messages/statuses | one identical message and status for unknown-user and wrong-password; a dummy PBKDF2 run equalizes response **timing** |
| Session lifetime | never expired, `Max-Age` 90 days | 30-day absolute expiry, `expires_at` enforced in `_auth()`, hourly purge in a background worker (never inline on the login path) |
| Session revocation | *"existing sessions for that user stay valid"* | every password change/reset/admin-reset revokes all other sessions; `users.pw_changed_at` makes `_auth()` reject any session minted before it as a second line of defence |
| "Log out everywhere" | absent | `POST /api/me/sessions/revoke-all` (keeps the calling device) |
| Password policy | min 4 chars | min 8, max 128 (512 hard reject), blocklist of common passwords, cannot contain the username, cannot be a single repeated run, must differ from the current one |
| CSRF | none; production cookie is `SameSite=None` | state-changing POSTs authenticated **by cookie only** must be `application/json` *and* same-origin (`Origin`/`Referer` vs `Host`). Bearer/`X-Auth-Token` requests are exempt — a cross-site page cannot set those without a CORS preflight, which is never granted |
| Error leakage | `"Server error: %s" % e` returned to the client, plus DB details from `/healthz` | generic message to the client, traceback to the log; client disconnects are not logged as errors |
| Security headers | none | CSP (`default-src 'self'`, `object-src 'none'`, `base-uri 'self'`, `frame-ancestors`), `X-Content-Type-Options`, `Referrer-Policy`, `Permissions-Policy`, `Cross-Origin-Opener-Policy`, `X-Frame-Options` on the default same-origin config |
| Randomness for secrets | `random.choices`, `sha256(time.time())` | `secrets` everywhere a credential or code is minted |
| Static file serving | `fn.startswith(STATIC)` (accepts sibling dirs like `…/jee-war-room-secrets/x`) | `startswith(STATIC + os.sep)` |
| Audit trail | none | `auth_events` records signups, successes, failures, lockouts, resets, password changes, code issuance and revocations; admin-only at `GET /api/admin/auth-events` |
| Backups in git | full dump committed weekly | redacted dump + private expiring artifacts; nothing committed |

---

## 5. New and changed API surface

```
POST /api/auth/signup              -> + recoveryCodes[] (once)
POST /api/auth/login               -> + recoveryCodes[] (once, for pre-existing accounts)
POST /api/auth/reset-password      {name, recoveryCode, newPassword}   (was: {name, code, ...})
GET  /api/me/security              recovery status + active sessions
POST /api/me/recovery-codes        regenerate; returns new plaintext once
POST /api/me/sessions/revoke-all   log out every other device
GET  /api/admin/auth-events        admin-only audit trail
POST /api/admin/set-password       now enforces policy + revokes the target's sessions
```

`recoveryCodes` are only ever present in the response that created them —
when issuance succeeds; it is best-effort (see §3), so a response without them
is not an error, and Settings → Account Security regenerates a fresh set.

---

## 6. Deploy notes

* **Preview deployments share the production database.** Vercel env vars are set
  for production *and* preview, and `api/index.py` runs `init_db()` at import —
  so opening a PR executes migrations against live data on the preview's first
  cold start. Destructive one-way steps are therefore gated on
  `VERCEL_ENV != "preview"`, recorded in `schema_migrations`, and scoped to rows
  only the pre-hardening code could have written (`sessions.expires_at = 0`) so
  they are idempotent. `init_db()`'s fast path checks the migration record, so a
  preview cannot make production *skip* the purge. See DEPLOY.md for the
  permanent fix (a separate preview database).
* **Schema migration is automatic and idempotent** (`server.init_db()` on cold
  start). It creates `recovery_codes`, `auth_throttle`, `auth_events`,
  `schema_migrations`, adds `sessions.expires_at` / `sessions.user_agent` /
  `users.pw_changed_at`, and clears the old plaintext sessions. Verified by
  `tools/selftest_migration.py`, which builds a database in the exact old shape
  first — including a preview-then-production sequence. A failed `init_db()`
  is no longer swallowed: `api/index.py` records it in `server.BOOT_INFO`,
  retries lazily on every request, and reports it at `/healthz?detail=1`
  (`bootOk` / `bootError`).
* **Half-migrated databases degrade, they don't deny.** `_auth()` and
  `recovery_status()` detect missing columns/tables once (cached) and fall
  back to the pre-hardening query shape instead of 500-ing on every request.
  A *transient* DB failure still surfaces as a 500 — deliberately: turning it
  into a 401 would make the frontend drop its stored token and silently sign
  out every user on a passing blip.
* **Confirm the purge ran** on production after merging:
  `curl -s "https://<host>/healthz?detail=1"` →
  `bootOk: true`, `pendingMigrations: []`,
  `destructiveMigrationsAllowed: true`, `vercelEnv: "production"`.
* **Every user signs in once more** after this deploy. Expected: their old
  tokens were in the committed dump.
* **Announce the change** to your 13 users before deploying: password reset now
  needs a recovery code, and each of them gets 8 codes on their first login
  after the upgrade. If they dismiss that screen without saving, they can
  generate a new set in Settings → Account Security.
* Set `FRAME_ANCESTORS` in the Vercel environment **only if** something needs
  to embed the app in an iframe (default `'self'`). It feeds CSP
  `frame-ancestors`.
* Existing 4-character passwords keep working until changed; the 8-character
  minimum applies to every new or changed password.

### Testing

```bash
python3 server.py &                     # local sqlite on :8080
python3 tools/selftest_auth.py          # 58 HTTP-level checks
python3 tools/selftest_migration.py     # old-schema migration + at-rest checks
                                        # + login-resilience regressions:
                                        #   (a) login survives broken code issuance
                                        #   (b) no path answers a non-JSON error
                                        #   (c) an unmigrated DB still serves login
python3 tools/diag_login_roundtrips.py  # login round-trip budget (<=8 / <=5)
python3 tools/repro_login_500.py        # un-/half-migrated DB must answer JSON
```

Also enforced here: the **login round-trip budget** — every statement on the
auth path is a Turso HTTPS round trip inside a 7 s watchdog under a 30 s
function budget, so a new-device login costs <= 8 statements and a repeat
login <= 5 (batched multi-row writes, single `key IN (…)` throttle queries,
housekeeping in a background worker). Exceeding it is what produced the
platform 500 behind the bare "Request failed (500)" login toast. See
STATUS.md and DEPLOY.md.

`selftest_auth.py` proves the properties that matter: the friend code cannot
reset a password, recovery codes work once and cannot be replayed, failures are
throttled with `Retry-After`, unknown and known usernames are
indistinguishable, resets revoke prior sessions, tokens are stored only as
digests, and cross-site cookie POSTs are refused.

---

## 7. Known residual risks

* **CSP allows `'unsafe-inline'` for scripts and styles.** The frontend is one
  hand-written file with ~2,400 lines of inline `onclick=` handlers and
  template strings, so a strict `script-src 'self'` would need that rewritten.
  Inline handlers are the reason an XSS bug here would be serious — the token
  lives in `localStorage`. Removing `'unsafe-inline'` is the highest-value
  remaining hardening step.
* **No email/SMS recovery.** Recovery codes plus admin reset are the only paths.
  A user who loses both their password and their codes must contact the admin.
* **One admin, no second factor.** The first account ever created holds the
  admin role and can reset anyone's password. Consider adding TOTP for admin
  actions, and a second admin, so a lost account is not a dead end.
* **Throttle state lives in the database**, so it survives restarts and is
  shared across instances — but a determined attacker rotating IPs can still
  attempt ~5 passwords per username per hour. The per-username lockout is the
  real limit; the password policy is what makes guessing unproductive.
* **Message bodies and reports are private data.** They are stripped from the
  committed dump, but the `--full` dump contains them. Treat a full dump as
  sensitive as the database itself.

---

## 8. Action required (admin, by hand)

1. **Rotate your own password** and re-issue your recovery codes:
   ```bash
   python3 tools/account_recovery.py set-password --name Yash
   python3 tools/account_recovery.py codes --name Yash
   ```
   Your old hash, salt, friend code and 19 session tokens were in the dump.
2. **Purge the old dump from git history.** The file is deleted from `HEAD`, but
   every weekly commit still contains it. When you can afford a force-push:
   ```bash
   git filter-repo --path backups/warroom-dump.sql --invert-paths
   git push --force origin main
   ```
   Everyone must re-clone afterwards, and open PRs/links break. Sessions are
   already invalidated and hashes are salted PBKDF2, so this is cleanup rather
   than an emergency — but do it.
3. **Check the Turso database URL and auth token** were never committed
   (`git log -p | grep -i turso`). They are referenced as GitHub secrets in the
   workflows, which is correct; if any real value ever landed in a commit,
   rotate it in the Turso dashboard and update the Vercel + GitHub secrets.
4. **Tell your users** that friend codes no longer reset passwords, and that
   they should save the recovery codes they are shown on their next login.
