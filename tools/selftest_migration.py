#!/usr/bin/env python3
"""Migration + at-rest checks for the auth hardening (run against a throwaway DB).

1. Builds a database in the OLD shape (plaintext session tokens, no
   recovery_codes / auth_throttle / auth_events, no expires_at, no
   pw_changed_at) exactly like the live Turso database is today.
2. Runs server.init_db() and asserts the schema is upgraded, that the leaked
   plaintext sessions are destroyed, and that nothing else is lost.
3. Asserts that a token issued after the migration is stored ONLY as a digest.
4. Asserts the legacy-whitespace self-heal path works.
5. Regressions for the new-device "Request failed (500)" login bug:
   (a) login succeeds and returns a token even when recovery-code issuance
       raises (codes are best-effort; Settings can regenerate them);
   (b) no request path can produce a non-JSON error body;
   (c) a DB missing the hardened columns still serves /api/auth/login.
"""
import hashlib
import json
import os
import shutil
import sqlite3
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import authsec  # noqa: E402
import server   # noqa: E402

DB = os.path.join(ROOT, "data", "warroom.db")
# A legacy account's password, stored by the OLD signup exactly as typed —
# spaces included. The hardening must keep this owner able to get in.
LEGACY_PW = "  legacy pass with spaces  "

fails = []


def check(label, cond, extra=""):
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", label,
                          (" -> " + str(extra)) if (extra and not cond) else ""))
    if not cond:
        fails.append(label)


def build_old_db():
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    for suffix in ("-wal", "-shm"):
        p = DB + suffix
        if os.path.exists(p):
            os.remove(p)
    c = sqlite3.connect(DB)
    # --- the pre-hardening schema (subset that matters) ---
    c.executescript("""
    CREATE TABLE users(
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
      pass_hash TEXT NOT NULL, salt TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
      partner_id INTEGER, exam_date TEXT, avatar_color TEXT, created_at TEXT);
    CREATE TABLE sessions(
      token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT);
    CREATE TABLE settings(user_id INTEGER PRIMARY KEY, json TEXT NOT NULL);
    CREATE TABLE chapters(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT NOT NULL,
      name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'not_started',
      hidden INTEGER NOT NULL DEFAULT 0, sort INTEGER NOT NULL DEFAULT 0,
      custom INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE activities(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, type TEXT NOT NULL,
      custom_key TEXT, subject TEXT, chapter TEXT, amount INTEGER DEFAULT 0,
      duration INTEGER DEFAULT 0, extra TEXT, note TEXT, day TEXT NOT NULL,
      created_at TEXT NOT NULL);
    CREATE TABLE targets(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, day TEXT NOT NULL, kind TEXT,
      subject TEXT, chapter TEXT, title TEXT NOT NULL, amount INTEGER, duration INTEGER,
      status TEXT NOT NULL DEFAULT 'open', created_at TEXT, completed_at TEXT);
    CREATE TABLE timers(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT, chapter TEXT,
      started REAL NOT NULL, running INTEGER, ended_at TEXT, logged INTEGER DEFAULT 0);
    CREATE TABLE mocks(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, test_type TEXT, total INTEGER DEFAULT 300,
      score REAL, phys REAL, chem REAL, math REAL, attempted INTEGER, correct INTEGER,
      incorrect INTEGER, day TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
    CREATE TABLE errors(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT, chapter TEXT,
      etype TEXT, qno TEXT, note TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE xp_events(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
      reason TEXT, ref_type TEXT, ref_id TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE snapshots(
      user_id INTEGER NOT NULL, day TEXT NOT NULL, score REAL NOT NULL, air INTEGER NOT NULL,
      PRIMARY KEY(user_id, day));
    CREATE TABLE nonces(
      nonce TEXT PRIMARY KEY, user_id INTEGER, created_at REAL, result TEXT);
    CREATE TABLE app_settings(id INTEGER PRIMARY KEY CHECK (id=1), json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE friendships(
      user_id INTEGER NOT NULL, friend_id INTEGER NOT NULL, created_at TEXT,
      PRIMARY KEY(user_id, friend_id));
    CREATE TABLE messages(
      id INTEGER PRIMARY KEY, sender INTEGER NOT NULL, recipient INTEGER NOT NULL,
      body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
    CREATE TABLE announcements(
      id INTEGER PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE reports(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, body TEXT NOT NULL,
      created_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
    """)
    # a legacy account whose password hash was computed on an UNTRIMMED string
    salt = "oldsalt" * 4
    legacy_hash = hashlib.pbkdf2_hmac("sha256", LEGACY_PW.encode(), salt.encode(), 120_000).hex()
    c.execute("""INSERT INTO users(id,name,pass_hash,salt,code,exam_date,avatar_color,created_at)
                 VALUES(2,'Yash',?,?,'ALYWAY','2027-01-15','#22d3ee','2026-09-15T11:53:55Z')""",
              (legacy_hash, salt))
    # plaintext session tokens, exactly like the ones leaked in the git dump
    for i in range(19):
        c.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)",
                  (hashlib.sha256(("leaked-token-%d" % i).encode()).hexdigest(), 2,
                   "2026-09-16T02:25:03Z"))
    c.execute("INSERT INTO activities(id,user_id,type,amount,day,created_at) VALUES(1,2,'pyq',25,'2026-10-01','x')")
    c.execute("INSERT INTO messages(id,sender,recipient,body,created_at) VALUES(1,2,3,'hello','x')")
    c.commit()
    c.close()


def test_preview_gating():
    """Vercel preview deployments share the production Turso database in this
    project, so they must not run the one-way auth migration against live data
    — but they must not cause production to SKIP it either."""
    print("\n--- preview-deployment gating ---")
    build_old_db()
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    legacy_before = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    c.close()

    os.environ["VERCEL_ENV"] = "preview"
    try:
        server.init_db()
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        scols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
        check("preview still applies ADDITIVE schema (code needs it)",
              "recovery_codes" in tables and "expires_at" in scols, sorted(tables)[-4:])
        n_after = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        check("preview does NOT destroy live sessions",
              n_after == legacy_before, (n_after, legacy_before))
        pend = server.pending_migrations(c)
        check("preview reports the purge as still pending",
              len(pend) == 1 and pend[0]["blockedBy"] == "VERCEL_ENV=preview", pend)
        check("preview counts the legacy sessions awaiting purge",
              pend and pend[0]["legacySessions"] == legacy_before, pend)
        check("destructive_migrations_allowed() is False on preview",
              server.destructive_migrations_allowed() is False)
        c.close()

        # Now the production deploy of the same code.
        os.environ["VERCEL_ENV"] = "production"
        server.init_db()
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        check("production purges the legacy plaintext sessions",
              c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0)
        check("production records the migration",
              server._migration_done(c, server.MIGRATION_LEGACY_SESSION_PURGE))
        check("no migrations pending after production run",
              server.pending_migrations(c) == [], server.pending_migrations(c))
        check("destructive_migrations_allowed() is True on production",
              server.destructive_migrations_allowed() is True)

        # A session created by the hardened code must survive later runs —
        # this is what makes the purge idempotent and prevents a second
        # unexpected mass sign-out.
        raw = server._make_session_row(c, 2, "post-migration-agent")
        c.close()
        server.init_db()
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        rows = [r["token"] for r in c.execute("SELECT token FROM sessions")]
        check("post-migration session survives a later init_db (no double sign-out)",
              len(rows) == 1 and rows[0] == authsec.hash_token(raw), rows)
        check("re-running init_db does not re-record or re-purge",
              server.pending_migrations(c) == [])
        c.close()
    finally:
        os.environ.pop("VERCEL_ENV", None)


def _start_local_server():
    """In-process HTTP server running the real Handler against data/warroom.db."""
    import http.server
    import socketserver
    import threading

    class _Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = _Srv(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def _http(port, method, path, body=None, cookie=None, token=None):
    import http.client
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    headers = {}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    if token:
        headers["Authorization"] = "Bearer " + token
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    raw = r.read().decode("utf-8", "replace")
    conn.close()
    return r.status, raw


def _json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return None


def _broken_gen(n=8):
    raise RuntimeError("simulated recovery-code issuance failure")


def test_login_resilience():
    """Regressions for the new-device 'Request failed (500)' login bug."""
    print("\n--- login resilience (new-device 500 regressions) ---")

    # ---- (b) + (c): serve the UNMIGRATED database, init_db() NOT run ----
    build_old_db()
    srv, port = _start_local_server()
    try:
        st, raw = _http(port, "POST", "/api/auth/login",
                        {"name": "Yash", "password": LEGACY_PW})
        d = _json(raw)
        check("(c) unmigrated DB serves /api/auth/login with a JSON body (HTTP %d)" % st,
              d is not None, raw[:120])
        check("(c) ... and the failure carries an `error` field (degrade, never hang)",
              d is not None and (d.get("ok") or isinstance(d.get("error"), str)), d)

        battery = [
            ("GET", "/api/me", None, None, None),
            ("GET", "/api/me", None, "jwr_sess=stale-cookie", None),
            ("GET", "/api/me", None, "jwr_sess=leaked-token-0", None),
            ("POST", "/api/auth/login", {"name": "Yash", "password": "wrong"}, None, None),
            ("POST", "/api/me", {"name": "x"}, None, None),
            ("POST", "/api/auth/reset-password",
             {"name": "Yash", "recoveryCode": "ABCD-EFGH-JKMN",
              "newPassword": "reset-pass-123"}, None, None),
        ]
        for (m, p, b, ck, tok) in battery:
            st, raw = _http(port, m, p, b, cookie=ck, token=tok)
            check("(b) %s %s -> JSON error body (HTTP %d)" % (m, p, st),
                  _json(raw) is not None, raw[:100])
        st, raw = _http(port, "PUT", "/api/me", {"name": "x"})
        check("(b) unsupported method -> JSON error body (HTTP %d)" % st,
              _json(raw) is not None, raw[:100])
    finally:
        srv.shutdown()

    # ---- migrate, then (a): broken issuance must not sink the login ----
    server.init_db()
    srv, port = _start_local_server()
    try:
        st, raw = _http(port, "POST", "/api/auth/login",
                        {"name": "Yash", "password": LEGACY_PW})
        d = _json(raw) or {}
        check("post-migration login succeeds and returns a token",
              st == 200 and bool(d.get("token")), raw[:150])
        check("post-migration first login returns 8 recovery codes",
              len(d.get("recoveryCodes") or []) == 8, sorted(d.keys()) if d else raw[:80])

        # Force re-issuance on the next login, then break issuance entirely.
        c = sqlite3.connect(DB)
        uid = c.execute("SELECT id FROM users WHERE name='Yash'").fetchone()[0]
        c.execute("DELETE FROM recovery_codes WHERE user_id=?", (uid,))
        c.commit()
        c.close()
        real_gen = authsec.gen_recovery_codes
        authsec.gen_recovery_codes = _broken_gen
        try:
            st, raw = _http(port, "POST", "/api/auth/login",
                            {"name": "Yash", "password": LEGACY_PW})
        finally:
            authsec.gen_recovery_codes = real_gen
        d = _json(raw) or {}
        check("(a) login SUCCEEDS even though recovery-code issuance raises",
              st == 200 and bool(d.get("token")), raw[:150])
        check("(a) broken issuance returns no recoveryCodes",
              isinstance(d, dict) and "recoveryCodes" not in d,
              sorted(d.keys()) if isinstance(d, dict) else raw[:80])
        st, raw = _http(port, "GET", "/api/me", token=d.get("token"))
        check("(a) the token from the degraded login authenticates", st == 200, raw[:100])
        c = sqlite3.connect(DB)
        c.row_factory = sqlite3.Row
        kinds = [r["kind"] for r in
                 c.execute("SELECT kind FROM auth_events ORDER BY id DESC LIMIT 8")]
        c.close()
        check("(a) the failed issuance is recorded in auth_events",
              "recovery_codes_failed" in kinds, kinds)
    finally:
        srv.shutdown()


def main():
    print("Building a database in the OLD (pre-hardening) shape ...")
    build_old_db()
    before = sqlite3.connect(DB)
    n_users = before.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    n_sessions = before.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
    n_act = before.execute("SELECT COUNT(*) FROM activities").fetchone()[0]
    n_msg = before.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
    before.close()
    print("  old db: %d users, %d plaintext sessions, %d activities, %d messages\n"
          % (n_users, n_sessions, n_act, n_msg))

    t0 = time.time()
    server.init_db()
    print("init_db() ran in %.2fs\n" % (time.time() - t0))

    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for t in ("recovery_codes", "auth_throttle", "auth_events"):
        check("migration created table %s" % t, t in tables, sorted(tables))
    ucols = {r[1] for r in c.execute("PRAGMA table_info(users)")}
    scols = {r[1] for r in c.execute("PRAGMA table_info(sessions)")}
    check("users.pw_changed_at added", "pw_changed_at" in ucols, sorted(ucols))
    check("sessions.expires_at added", "expires_at" in scols, sorted(scols))
    check("sessions.user_agent added", "user_agent" in scols, sorted(scols))
    check("leaked PLAINTEXT sessions destroyed",
          c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0)
    check("user rows preserved", c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == n_users)
    check("activity rows preserved", c.execute("SELECT COUNT(*) FROM activities").fetchone()[0] == n_act)
    check("message rows preserved", c.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == n_msg)
    check("password hash untouched by migration",
          bool(c.execute("SELECT pass_hash FROM users WHERE id=2").fetchone()[0]))

    # --- second run must be a no-op (idempotent) ---
    c.close()
    server.init_db()
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    check("init_db() is idempotent (users survive a second run)",
          c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == n_users)

    # --- a freshly issued session must be stored only as a digest ---
    raw = server._make_session_row(c, 2, "selftest-agent")
    stored = [r["token"] for r in c.execute("SELECT token FROM sessions")]
    check("new session token is NOT stored in plaintext", raw not in stored, stored)
    check("stored value is the SHA-256 digest of the token",
          authsec.hash_token(raw) in stored, stored)
    check("new session has a real expiry",
          all(r["expires_at"] > time.time() for r in c.execute("SELECT expires_at FROM sessions")))

    # --- legacy whitespace self-heal ---
    # The old signup stored a hash of the RAW string, spaces included. So the
    # password of a legacy account literally *is* "  legacy pass with spaces  ".
    u = dict(c.execute("SELECT * FROM users WHERE id=2").fetchone())
    ok_exact, legacy = authsec.verify_pw(LEGACY_PW, u["pass_hash"], u["salt"])
    check("legacy account: the exact stored password verifies and is flagged legacy",
          ok_exact and legacy, (ok_exact, legacy))
    ok_trimmed, _ = authsec.verify_pw(LEGACY_PW.strip(), u["pass_hash"], u["salt"])
    check("legacy account: the trimmed variant is NOT silently accepted (it was never the password)",
          not ok_trimmed, ok_trimmed)
    ok_wrong, _ = authsec.verify_pw("wrong password entirely", u["pass_hash"], u["salt"])
    check("legacy account: wrong password still rejected", not ok_wrong)

    # login() re-hashes the normalized form the moment a legacy match happens,
    # which is what permanently ends the "my correct password is rejected" bug.
    salt = authsec.new_salt()
    c.execute("UPDATE users SET pass_hash=?, salt=? WHERE id=2",
              (authsec.hash_pw(LEGACY_PW, salt), salt))
    c.commit()
    u = dict(c.execute("SELECT * FROM users WHERE id=2").fetchone())
    ok_after, still_legacy = authsec.verify_pw(LEGACY_PW.strip(), u["pass_hash"], u["salt"])
    check("self-heal: the trimmed password now verifies (no longer legacy)",
          ok_after and not still_legacy, (ok_after, still_legacy))
    ok_padded, _ = authsec.verify_pw("   %s   " % LEGACY_PW.strip(), u["pass_hash"], u["salt"])
    check("self-heal: stray whitespace is tolerated forever after", ok_padded, ok_padded)

    # --- recovery codes at rest ---
    codes = server.issue_recovery_codes(c, 2, "127.0.0.1")
    rows = [r["code_hash"] for r in c.execute("SELECT code_hash FROM recovery_codes WHERE user_id=2")]
    check("8 recovery codes issued", len(codes) == 8, len(codes))
    check("recovery codes stored ONLY as digests",
          all(code not in rows for code in codes) and all(len(r) == 64 for r in rows), rows[:1])
    check("a presented code is accepted exactly once",
          server.consume_recovery_code(c, 2, codes[0], "127.0.0.1") is True
          and server.consume_recovery_code(c, 2, codes[0], "127.0.0.1") is False)
    check("a code from another user is rejected",
          server.consume_recovery_code(c, 999, codes[1], "127.0.0.1") is False)
    check("codes are unambiguous (no 0/O/1/I)",
          all(ch not in "0O1I" for code in codes for ch in code), codes[:2])
    c.close()

    # Rebuilds the old-shape database again to exercise the preview gate.
    test_preview_gating()

    # Rebuilds it once more to exercise the un-/half-migrated login paths.
    test_login_resilience()

    print("\n%s" % ("MIGRATION + AT-REST CHECKS PASSED" if not fails
                    else "FAILURES: %s" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
