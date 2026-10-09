#!/usr/bin/env python3
"""
Repro for the new-device "Request failed (500)" login bug.

Builds a database in the OLD (pre-hardening) shape — exactly the state the
Turso database is in when a migration failed or never ran — then serves it
WITHOUT running init_db() and hammers the auth endpoints.

Invariant asserted here: every /api/* response is a JSON body — failures
carry an `error` field on ANY status code, and successes carry `ok`. Before
the fix, exceptions raised outside the handler try/except escaped
BaseHTTPRequestHandler entirely (no response sent, socket closed) and the
Vercel proxy answered with a non-JSON 500; api() in public/app.js then fell
back to the bare "Request failed (500)" toast and the login never completed.

Note: every handler now runs the lazy bootstrap (_ensure_boot) before
routing, so the FIRST request against the unmigrated DB self-heals the
schema additively and the login succeeds outright — strictly better than the
original acceptance bar of "a JSON error body". Both outcomes pass here; a
non-JSON body never does.

Uses the throwaway local database data/warroom.db (gitignored) and rebuilds
it from scratch.
"""
import hashlib
import http.client
import http.server
import json
import os
import socket
import socketserver
import sys
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import dbwrap    # noqa: E402
import server    # noqa: E402

DB = dbwrap.DB_PATH
LEGACY_PW = "  legacy pass with spaces  "

fails = []


def check(label, cond, extra=""):
    print("  %s  %s%s" % ("PASS" if cond else "FAIL", label,
                          (" -> " + str(extra)[:200]) if (extra and not cond) else ""))
    if not cond:
        fails.append(label)


def build_unmigrated_db():
    """Pre-hardening shape: no recovery_codes/auth_throttle/auth_events,
    no sessions.expires_at, no users.pw_changed_at/role."""
    import sqlite3
    for suffix in ("", "-wal", "-shm"):
        p = DB + suffix
        if os.path.exists(p):
            os.remove(p)
    c = sqlite3.connect(DB)
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
    """)
    salt = "oldsalt" * 4
    legacy_hash = hashlib.pbkdf2_hmac("sha256", LEGACY_PW.encode(), salt.encode(),
                                      120_000).hex()
    c.execute("""INSERT INTO users(id,name,pass_hash,salt,code,exam_date,avatar_color,created_at)
                 VALUES(2,'Yash',?,?,'ALYWAY','2027-01-15','#22d3ee','2026-09-15T11:53:55Z')""",
              (legacy_hash, salt))
    # plaintext session tokens, exactly like the pre-hardening rows
    for i in range(3):
        c.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)",
                  ("leaked-token-%d" % i, 2, "2026-09-16T02:25:03Z"))
    c.commit()
    c.close()


class _Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def start_server():
    srv = _Srv(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def http_req(port, method, path, body=None, cookie=None, raw_body=None,
             ctype="application/json"):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    headers = {}
    data = None
    if raw_body is not None:
        data = raw_body.encode()
        headers["Content-Type"] = ctype
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    if cookie:
        headers["Cookie"] = cookie
    conn.request(method, path, body=data, headers=headers)
    r = conn.getresponse()
    raw = r.read().decode("utf-8", "replace")
    conn.close()
    return r.status, raw


def assert_json_error(label, st, raw, ok_codes=(200,)):
    """Every API failure must be JSON with an `error` field — never a non-JSON
    body (that is what produced the bare 'Request failed (500)' toast)."""
    try:
        d = json.loads(raw)
        is_json = True
    except Exception:
        d, is_json = {}, False
    if st in ok_codes:
        check("%s -> JSON ok (HTTP %d)" % (label, st), is_json and d.get("ok"), raw[:120])
    else:
        check("%s -> JSON body WITH error field (HTTP %d)" % (label, st),
              is_json and isinstance(d.get("error"), str), raw[:120])


def raw_request(port, payload):
    """Byte-exact request (for a malformed request line)."""
    s = socket.create_connection(("127.0.0.1", port), timeout=30)
    s.sendall(payload)
    chunks = []
    while True:
        b = s.recv(65536)
        if not b:
            break
        chunks.append(b)
    s.close()
    raw = b"".join(chunks).decode("utf-8", "replace")
    return raw.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in raw else raw


def main():
    print("Building an UNMIGRATED (pre-hardening) database and serving it "
          "without init_db() ...\n")
    build_unmigrated_db()
    srv, port = start_server()
    try:
        # THE bug: new device (no cookie, no bearer) logging into an existing
        # account against a half-migrated schema. Must answer JSON either way.
        st, raw = http_req(port, "POST", "/api/auth/login",
                           {"name": "Yash", "password": LEGACY_PW})
        assert_json_error("login on unmigrated DB", st, raw)

        st, raw = http_req(port, "POST", "/api/auth/login",
                           {"name": "Yash", "password": "wrong password"})
        assert_json_error("login with wrong password", st, raw)

        st, raw = http_req(port, "GET", "/api/me")
        assert_json_error("GET /api/me without a token", st, raw)

        # Device with a stale cookie: _auth() must DEGRADE on the missing
        # columns, never escape the handler.
        st, raw = http_req(port, "GET", "/api/me", cookie="jwr_sess=garbage-token")
        assert_json_error("GET /api/me with a stale cookie", st, raw)

        # A leaked PLAINTEXT pre-hardening token must not authenticate
        # (sessions are keyed by SHA-256 digest now) — still as JSON.
        st, raw = http_req(port, "GET", "/api/me", cookie="jwr_sess=leaked-token-0")
        assert_json_error("leaked plaintext token rejected", st, raw)

        st, raw = http_req(port, "POST", "/api/auth/signup",
                           {"name": "newkid", "password": "fresh-pass-123"})
        assert_json_error("signup on unmigrated DB", st, raw)

        st, raw = http_req(port, "POST", "/api/auth/reset-password",
                           {"name": "Yash", "recoveryCode": "ABCD-EFGH-JKMN",
                            "newPassword": "reset-pass-123"})
        assert_json_error("reset-password on unmigrated DB", st, raw)

        st, raw = http_req(port, "POST", "/api/me", {"name": "x"})
        assert_json_error("authenticated POST without token", st, raw)

        st, raw = http_req(port, "PUT", "/api/me", {"name": "x"})
        assert_json_error("unsupported method (PUT)", st, raw)

        st, raw = http_req(port, "GET", "/api/no/such/endpoint",
                           cookie="jwr_sess=garbage-token")
        assert_json_error("unknown API endpoint", st, raw)

        raw = raw_request(port, b"BROKEN REQUEST LINE\r\n\r\n")
        try:
            d = json.loads(raw)
            ok = isinstance(d.get("error"), str)
        except Exception:
            ok = False
        check("malformed request line -> JSON error body", ok, raw[:120])
    finally:
        srv.shutdown()

    print("\n%s" % ("REPRO FIXED: every response was JSON with an error field"
                    if not fails else "FAILURES: %s" % fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
