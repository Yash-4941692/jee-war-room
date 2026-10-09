#!/usr/bin/env python3
"""
Round-trip diagnostic for the login path.

Simulates a NEW device (no cookie, no bearer token, cold worker) logging in
as a PRE-EXISTING account that has no recovery codes yet, and counts every
SQL statement the request issues. On Vercel each statement is a separate
HTTPS round trip to Turso inside dbwrap's 7 s watchdog, with a 30 s function
budget — so this count IS the login latency budget.

Hard targets (see STATUS.md):
  * first new-device login of a pre-existing account: <= 8 statements
  * repeat login (codes already issued):              <= 5 statements
  * ZERO repeated single-row recovery_codes INSERTs (batched multi-row only)
  * ZERO inline housekeeping DELETEs on the login path

Uses the throwaway local database data/warroom.db (gitignored) and rebuilds
it from scratch.
"""
import http.client
import json
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dbwrap    # noqa: E402
import server    # noqa: E402
import authsec   # noqa: E402

DB = dbwrap.DB_PATH
FIRST_LIMIT = 8
REPEAT_LIMIT = 5

STATEMENTS = []            # (thread_name, sql)
COUNTING = [False]
_real_db = dbwrap.db

HK_DELETE_MARKERS = (
    "DELETE FROM sessions WHERE expires_at",
    "DELETE FROM auth_throttle WHERE updated_at",
    "DELETE FROM auth_events WHERE ts",
)


class _CountingConn:
    """Transparent proxy that records every statement's SQL + thread."""

    def __init__(self, inner):
        self._inner = inner

    def _rec(self, sql):
        if COUNTING[0]:
            STATEMENTS.append((threading.current_thread().name, sql))

    def execute(self, sql, params=()):
        self._rec(sql)
        return self._inner.execute(sql, params)

    def executemany(self, sql, seq):
        self._rec("executemany: " + sql)
        return self._inner.executemany(sql, seq)

    def executescript(self, script):
        self._rec("executescript(%d chars)" % len(script))
        return self._inner.executescript(script)

    def commit(self):
        return self._inner.commit()

    def rollback(self):
        return self._inner.rollback()

    def close(self):
        return self._inner.close()

    def __getattr__(self, name):
        return getattr(self._inner, name)


def counting_db():
    return _CountingConn(_real_db())


def fresh_db():
    for suffix in ("", "-wal", "-shm"):
        p = DB + suffix
        if os.path.exists(p):
            os.remove(p)
    server.init_db()


def make_existing_account(name, pw):
    """A pre-hardening-style EXISTING user with NO recovery codes yet."""
    c = _real_db()
    salt = authsec.new_salt()
    now = server.now_iso()
    c.execute("""INSERT INTO users(name,pass_hash,salt,code,exam_date,avatar_color,
                                   created_at,role,pw_changed_at)
                 VALUES(?,?,?,?,?,?,?,'user',?)""",
              (name, authsec.hash_pw(pw, salt), salt, authsec.gen_friend_code(6),
               "2027-01-21", "#f97316", now, now))
    uid = c.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()[0]
    n = c.execute("SELECT COUNT(*) FROM recovery_codes WHERE user_id=?", (uid,)).fetchone()[0]
    assert n == 0, "test account must start without recovery codes"
    c.commit()
    c.close()
    return uid


def start_server():
    import http.server
    import socketserver

    class _Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = _Srv(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="diag-http").start()
    return srv, srv.server_address[1]


def do_login(port, name, pw):
    body = json.dumps({"name": name, "password": pw}).encode()
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    conn.request("POST", "/api/auth/login", body=body,
                 headers={"Content-Type": "application/json"})
    r = conn.getresponse()
    raw = r.read().decode("utf-8", "replace")
    conn.close()
    try:
        return r.status, json.loads(raw)
    except Exception:
        return r.status, {"raw": raw}


def request_statements():
    """Statements issued by request-handling threads (not the background
    housekeeping worker — that one is off the user-facing path by design)."""
    return [s for (tn, s) in STATEMENTS if tn != "jwr-housekeeping"]


def report(title, stmts):
    print("    --- %s: %d SQL statements ---" % (title, len(stmts)))
    for i, s in enumerate(stmts, 1):
        print("      %2d  %s" % (i, " ".join(s.split())[:100]))


def main():
    print("Rebuilding throwaway DB at %s ..." % DB)
    fresh_db()
    stamp = int(time.time())
    name = "diag_user_%d" % stamp
    pw = "diag pass %d" % stamp
    uid = make_existing_account(name, pw)
    print("Pre-existing account id=%d (%s), no recovery codes.\n" % (uid, name))

    # Cold worker: housekeeping is "due", exactly like a fresh Vercel instance.
    server.Handler._LAST_HOUSEKEEPING[0] = 0.0
    server.Handler._HK_BUSY[0] = False

    srv, port = start_server()
    dbwrap.db = counting_db
    fails = []
    try:
        # ---------------- first new-device login ----------------
        COUNTING[0] = True
        del STATEMENTS[:]
        t0 = time.time()
        st, d = do_login(port, name, pw)
        ms = int((time.time() - t0) * 1000)
        first = request_statements()
        bg = [s for (tn, s) in STATEMENTS if tn == "jwr-housekeeping"]
        COUNTING[0] = False

        print("    POST /api/auth/login -> HTTP %d (%d ms locally on sqlite)" % (st, ms))
        print("    recoveryCodes returned: %s" % len(d.get("recoveryCodes") or []))
        report("first new-device login (pre-existing account, no codes)", first)
        if bg:
            print("    (background housekeeping worker issued %d statement(s), off-path)"
                  % len(bg))

        if st != 200 or not d.get("token"):
            fails.append("first login did not succeed: HTTP %s %s" % (st, d))
        if len(d.get("recoveryCodes") or []) != 8:
            fails.append("first login did not return 8 recovery codes")
        if len(first) > FIRST_LIMIT:
            fails.append("first login issued %d statements (limit %d)" % (len(first), FIRST_LIMIT))
        rc_inserts = [s for s in first if "INSERT" in s.upper() and "recovery_codes" in s]
        if len(rc_inserts) > 1:
            fails.append("%d separate recovery_codes INSERTs (must be one multi-row)"
                         % len(rc_inserts))
        for s in first:
            if any(m in s for m in HK_DELETE_MARKERS):
                fails.append("inline housekeeping DELETE on the login path: %s" % s[:80])

        # ---------------- repeat login (codes already issued) ----------------
        COUNTING[0] = True
        del STATEMENTS[:]
        st, d = do_login(port, name, pw)
        second = request_statements()
        COUNTING[0] = False

        print("\n    POST /api/auth/login (repeat) -> HTTP %d" % st)
        report("repeat login (codes already issued)", second)

        if st != 200 or not d.get("token"):
            fails.append("repeat login did not succeed: HTTP %s %s" % (st, d))
        if "recoveryCodes" in d:
            fails.append("repeat login re-issued recovery codes")
        if len(second) > REPEAT_LIMIT:
            fails.append("repeat login issued %d statements (limit %d)"
                         % (len(second), REPEAT_LIMIT))
        for s in second:
            if any(m in s for m in HK_DELETE_MARKERS):
                fails.append("inline housekeeping DELETE on the repeat login path: %s" % s[:80])

        print("\n    TOTAL: first login %d statement(s) (limit %d), "
              "repeat login %d (limit %d)"
              % (len(first), FIRST_LIMIT, len(second), REPEAT_LIMIT))
    finally:
        srv.shutdown()
        dbwrap.db = _real_db

    if fails:
        print("\nDIAG FAILED:")
        for f in fails:
            print("  -", f)
        return 1
    print("\nLOGIN ROUND-TRIP BUDGET OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
