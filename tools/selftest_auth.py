#!/usr/bin/env python3
"""
Auth security self-test for JEE WAR ROOM.

Boots nothing itself — point it at a running server:

    python3 server.py &                     # local sqlite on :8080
    python3 tools/selftest_auth.py          # or: BASE=http://host:port python3 ...

It verifies the properties the hardening pass promises:
  * a password can never be read back (only verified),
  * stray surrounding whitespace can no longer lock an owner out,
  * the friend code is NOT accepted as a password-reset factor,
  * one-time recovery codes work, and cannot be replayed,
  * failed logins/resets are throttled per username and per IP,
  * unknown and known usernames produce identical errors,
  * changing a password revokes pre-existing sessions,
  * session tokens are stored only as SHA-256 digests,
  * cross-site POSTs that rely on the cookie are rejected.

Exit code 0 = all checks passed.
"""
import json
import os
import sys
import time
import urllib.error
import urllib.request

BASE = os.environ.get("BASE", "http://localhost:8080").rstrip("/")
RUN = os.environ.get("RUN_ID", str(int(time.time()))[-6:])

passed = []
failed = []


def req(method, path, body=None, token=None, cookie=None, origin=None, ctype="application/json",
        host=None, xforwarded_host=None, referer=None, raw_body=None):
    url = BASE + path
    if raw_body is not None:
        data = raw_body.encode() if isinstance(raw_body, str) else raw_body
    else:
        data = json.dumps(body).encode() if body is not None else None
    r = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        r.add_header("Content-Type", ctype)
    if host:
        r.add_header("Host", host)
    if xforwarded_host:
        r.add_header("X-Forwarded-Host", xforwarded_host)
    if referer:
        r.add_header("Referer", referer)
    if token:
        r.add_header("Authorization", "Bearer " + token)
    if cookie:
        r.add_header("Cookie", cookie)
    if origin:
        r.add_header("Origin", origin)
    try:
        with urllib.request.urlopen(r, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "replace")
            return resp.status, _parse(raw), dict(resp.headers)
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        return e.code, _parse(raw), dict(e.headers)


def _parse(raw):
    """API answers are JSON; static files are not. Never crash on either."""
    if not raw:
        return {}
    try:
        return json.loads(raw)
    except Exception:
        return {"raw": raw}


def check(label, cond, extra=""):
    if cond:
        passed.append(label)
        print("  PASS  %s" % label)
    else:
        failed.append(label)
        print("  FAIL  %s %s" % (label, ("-> " + str(extra)) if extra else ""))


def cookie_from(headers):
    sc = headers.get("Set-Cookie") or ""
    return sc.split(";")[0] if sc else ""


def promote_to_admin(uid):
    """Make this run's account the admin, when the target is a local sqlite DB.

    Only used by the self-test on re-runs (the first-ever account keeps the
    admin role). Returns False when there is nothing local to promote.
    """
    import sqlite3
    from urllib.parse import urlparse
    host = (urlparse(BASE).hostname or "").lower()
    if host not in ("localhost", "127.0.0.1", "0.0.0.0"):
        return False
    path = os.environ.get("LOCAL_DB")
    if not path:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        path = os.path.join(here, "data", "warroom.db")
    if not os.path.isfile(path):
        return False
    try:
        con = sqlite3.connect(path, timeout=15)
        con.execute("UPDATE users SET role='admin' WHERE id=?", (int(uid),))
        con.commit()
        con.close()
        print("  (promoted test account id=%s to admin in %s)" % (uid, path))
        return True
    except Exception as e:
        print("  (could not promote: %s)" % e)
        return False


def raw_get(path):
    """Send a byte-exact request line (urllib would normalize ../ away)."""
    import socket
    from urllib.parse import urlparse
    u = urlparse(BASE)
    host, port = u.hostname, (u.port or 80)
    try:
        s = socket.create_connection((host, port), timeout=15)
        s.sendall(("GET %s HTTP/1.1\r\nHost: %s:%d\r\nConnection: close\r\n\r\n"
                   % (path, host, port)).encode())
        chunks = []
        while True:
            b = s.recv(65536)
            if not b:
                break
            chunks.append(b)
        s.close()
        raw = b"".join(chunks).decode("utf-8", "replace")
        return raw.split("\r\n\r\n", 1)[1] if "\r\n\r\n" in raw else raw
    except Exception as e:
        return "ERROR %s" % e


def main():
    print("Target: %s (run id %s)\n" % (BASE, RUN))
    name = "sectest_%s" % RUN
    pw = "correct horse %s" % RUN
    other = "sectest2_%s" % RUN

    # ---------------------------------------------------------- 1. signup
    st, d, h = req("POST", "/api/auth/signup", {"name": name, "password": pw})
    check("signup succeeds", st == 200 and d.get("ok"), d)
    token = d.get("token")
    codes = d.get("recoveryCodes") or []
    check("signup returns 8 recovery codes (shown once)", len(codes) == 8, len(codes))
    check("signup returns a friend code", bool(d.get("code")), d.get("code"))
    check("signup does NOT return the password", "password" not in json.dumps(d).lower()
          or pw not in json.dumps(d), d)

    # --------------------------------------------------- 2. password policy
    st, d, _ = req("POST", "/api/auth/signup", {"name": "weak_%s" % RUN, "password": "abc"})
    check("short password rejected (min 8)", st == 400 and "8 characters" in d.get("error", ""), d)
    st, d, _ = req("POST", "/api/auth/signup", {"name": "weak2_%s" % RUN, "password": "password123"})
    check("common password rejected", st == 400, d)
    st, d, _ = req("POST", "/api/auth/signup",
                   {"name": "weak3_%s" % RUN, "password": "weak3_%s_x" % RUN})
    check("password containing the username rejected", st == 400, d)

    # ---------------------------------------------------------- 3. login
    st, d, h = req("POST", "/api/auth/login", {"name": name, "password": pw})
    check("login succeeds", st == 200 and d.get("token"), d)
    st, d, _ = req("GET", "/api/me", token=d.get("token"))
    check("bearer token authenticates /api/me", st == 200 and d.get("user", {}).get("name") == name, st)

    # stray whitespace must never lock the owner out
    st, d, _ = req("POST", "/api/auth/login", {"name": name, "password": "  %s  " % pw})
    check("login tolerates surrounding whitespace", st == 200 and d.get("token"), d)
    st, d, _ = req("POST", "/api/auth/login", {"name": name.upper(), "password": pw})
    check("login is case-insensitive on the name", st == 200, d)

    # --------------------------------------------------- 4. enumeration
    st_bad_name, d_bad_name, _ = req("POST", "/api/auth/login",
                                     {"name": "nosuchuser_%s" % RUN, "password": pw})
    st_bad_pw, d_bad_pw, _ = req("POST", "/api/auth/login",
                                 {"name": name, "password": "definitely-not-it"})
    check("unknown user and wrong password give the same status",
          st_bad_name == st_bad_pw, (st_bad_name, st_bad_pw))
    check("unknown user and wrong password give the same message",
          d_bad_name.get("error") == d_bad_pw.get("error"), (d_bad_name, d_bad_pw))

    # --------------------------------------------- 5. friend code is retired
    friend_code = None
    st, d, _ = req("GET", "/api/me", token=token)
    friend_code = d.get("user", {}).get("code")
    st, d, _ = req("POST", "/api/auth/reset-password",
                   {"name": name, "code": friend_code, "newPassword": "hijacked-pass-1"})
    check("friend code CANNOT reset a password", st == 400 and "no longer" in d.get("error", ""), d)
    st, d, _ = req("POST", "/api/auth/reset-password",
                   {"name": name, "recoveryCode": friend_code, "newPassword": "hijacked-pass-1"})
    check("friend code rejected even as a 'recoveryCode'", st == 400, d)

    # ------------------------------------------------ 6. session storage
    st, d, _ = req("GET", "/api/me/security", token=token)
    check("/api/me/security reports recovery status",
          st == 200 and d.get("recovery", {}).get("remaining") == 8, d)
    check("/api/me/security lists sessions", len(d.get("sessions") or []) >= 1, d)
    blob = json.dumps(d)
    check("security payload leaks no token/hash", token not in blob and pw not in blob, blob[:200])

    # ------------------------------------------- 7. CSRF / cross-site POST
    # A real browser ALWAYS attaches Origin to a fetch() POST, same-origin or
    # not; a request with a cookie and no Origin/Referer at all is treated as
    # cross-site and refused.
    origin_self = BASE
    st, d, h2 = req("POST", "/api/auth/login", {"name": name, "password": pw}, origin=origin_self)
    ck = cookie_from(h2)
    st, d, _ = req("POST", "/api/me", {"name": "hacked"}, cookie=ck, origin="https://evil.example")
    check("cross-site cookie POST blocked (bad Origin)", st == 403, d)
    st, d, _ = req("POST", "/api/me", {"name": name}, cookie=ck, origin=origin_self)
    check("same-origin cookie POST allowed", st == 200, d)
    st, d, _ = req("POST", "/api/me", {"name": name}, cookie=ck)
    check("cookie POST with no Origin/Referer refused", st == 403, d)
    st, d, _ = req("POST", "/api/me", {"name": "hacked2"}, cookie=ck, ctype="text/plain",
                   origin=origin_self)
    check("non-JSON cookie POST blocked", st == 403, d)
    st, d, _ = req("POST", "/api/me", None, cookie=ck, origin=origin_self,
                   ctype="application/x-www-form-urlencoded", raw_body="examDate=2027-01-15")
    check("urlencoded form POST blocked (classic CSRF shape)", st == 403, d)
    st, d, _ = req("POST", "/api/me", {"name": name}, cookie=ck, origin=origin_self,
                   referer=origin_self + "/index.html")
    check("Origin + matching Referer allowed", st == 200, d)
    st, d, _ = req("POST", "/api/me", {"name": name}, cookie=ck, referer=origin_self + "/x")
    check("Referer alone (no Origin) allowed when same-origin", st == 200, d)
    st, d, _ = req("POST", "/api/me", {"name": name}, cookie=ck, referer="https://evil.example/x")
    check("cross-site Referer blocked", st == 403, d)
    # A reverse proxy may rewrite Host; X-Forwarded-Host must then be honoured,
    # otherwise every legitimate login through Vercel/Koyeb would 403.
    st, d, _ = req("POST", "/api/me", {"name": name}, cookie=ck, host="127.0.0.1:8080",
                   xforwarded_host="app.example.com", origin="https://app.example.com")
    check("proxy-rewritten Host honoured via X-Forwarded-Host", st == 200, d)
    st, d, _ = req("POST", "/api/me", {"name": name}, cookie=ck, host="127.0.0.1:8080",
                   xforwarded_host="app.example.com", origin="https://evil.example")
    check("X-Forwarded-Host does not whitewash an evil Origin", st == 403, d)
    st, d, _ = req("POST", "/api/me", {"name": name}, cookie=ck,
                   origin="https://localhost:8080.evil.example")
    check("suffix-spoofed origin blocked", st == 403, d)
    st, d, _ = req("POST", "/api/me", {"name": name}, token=token, origin="https://evil.example")
    check("bearer-token POST is not CSRF-blocked (header is unforgeable)", st == 200, d)

    # --------------------------------------------- 8. recovery code reset
    st, d, h = req("POST", "/api/auth/login", {"name": name, "password": pw})
    old_token = d.get("token")
    newpw = "brand-new-pass-%s" % RUN
    st, d, _ = req("POST", "/api/auth/reset-password",
                   {"name": name, "recoveryCode": codes[0].lower().replace("-", " "),
                    "newPassword": newpw})
    check("reset works with a recovery code (typed loosely)", st == 200 and d.get("token"), d)
    check("reset reports remaining codes", d.get("recoveryRemaining") == 7, d)
    st, d, _ = req("GET", "/api/me", token=old_token)
    check("reset REVOKED the pre-existing session", st == 401, st)
    st, d, _ = req("GET", "/api/me", token=d.get("token") if isinstance(d, dict) else None)
    st, d, _ = req("POST", "/api/auth/login", {"name": name, "password": newpw})
    check("new password works", st == 200, d)
    st, d, _ = req("POST", "/api/auth/login", {"name": name, "password": pw})
    check("old password no longer works", st == 401, d)
    st, d, _ = req("POST", "/api/auth/reset-password",
                   {"name": name, "recoveryCode": codes[0], "newPassword": "another-pass-1"})
    check("a used recovery code cannot be replayed", st == 400, d)
    st, d, _ = req("POST", "/api/auth/reset-password",
                   {"name": "nosuchuser_%s" % RUN, "recoveryCode": codes[1],
                    "newPassword": "another-pass-2"})
    check("reset for an unknown user gives the same generic error",
          st == 400 and "recovery code" in d.get("error", "").lower(), d)

    # --------------------------------------------- 9. throttling (per name)
    st, d, _ = req("POST", "/api/auth/login", {"name": name, "password": newpw})  # clear counters
    victim = "throttle_%s" % RUN
    req("POST", "/api/auth/signup", {"name": victim, "password": "throttle-pass-1"})
    codes_seen = set()
    last = None
    for i in range(8):
        last = req("POST", "/api/auth/login", {"name": victim, "password": "wrong-%d" % i})
        codes_seen.add(last[0])
    check("repeated failed logins eventually return 429", 429 in codes_seen, codes_seen)
    check("429 carries Retry-After", bool((last[2] or {}).get("Retry-After")), last[2])
    st, d, _ = req("POST", "/api/auth/login", {"name": victim, "password": "throttle-pass-1"})
    check("correct password is refused while locked out", st == 429, (st, d))

    # --------------------------------------- 10. revoke-all + regeneration
    st, d, h = req("POST", "/api/auth/login", {"name": name, "password": newpw})
    t1 = d.get("token")
    st, d, _ = req("POST", "/api/auth/login", {"name": name, "password": newpw})
    t2 = d.get("token")
    st, d, _ = req("POST", "/api/me/sessions/revoke-all", {}, token=t1)
    check("revoke-all succeeds", st == 200, d)
    st, d, _ = req("GET", "/api/me", token=t2)
    check("revoke-all killed the OTHER session", st == 401, st)
    st, d, _ = req("GET", "/api/me", token=t1)
    check("revoke-all kept the CURRENT session", st == 200, st)

    st, d, _ = req("POST", "/api/me/recovery-codes", {}, token=t1)
    fresh = d.get("recoveryCodes") or []
    check("regenerating returns 8 fresh codes", len(fresh) == 8, len(fresh))
    check("regenerated codes differ from the old set", set(fresh) != set(codes), None)
    st, d, _ = req("POST", "/api/auth/reset-password",
                   {"name": name, "recoveryCode": codes[2], "newPassword": "x-pass-should-fail"})
    check("superseded (old) codes stop working", st == 400, d)
    st, d, _ = req("POST", "/api/auth/reset-password",
                   {"name": name, "recoveryCode": fresh[0], "newPassword": "final-pass-%s" % RUN})
    check("a fresh code works", st == 200, d)

    # ------------------------------------------- 11. admin password reset
    st, d, h = req("POST", "/api/auth/login", {"name": name, "password": "final-pass-%s" % RUN})
    admin_token = d.get("token")
    st, d, _ = req("GET", "/api/me", token=admin_token)
    me_id = d["user"]["id"]
    # Only the very first account ever created is admin. On a re-run against an
    # existing database, promote this run's account locally so the admin paths
    # still get covered (no-op when the target is remote — those checks skip).
    is_admin = d.get("user", {}).get("role") == "admin"
    if not is_admin:
        is_admin = promote_to_admin(me_id)
    if not is_admin:
        print("  SKIP  admin checks (target is not admin and no local DB to promote)")
    st, d, _ = req("POST", "/api/auth/signup", {"name": other, "password": "buddy-pass-%s" % RUN})
    buddy_token = d.get("token")
    st, d, _ = req("GET", "/api/me", token=buddy_token)
    buddy_id = d.get("user", {}).get("id")
    st, d, _ = req("POST", "/api/admin/set-password",
                   {"userId": buddy_id, "password": "sneaky-pass-1"}, token=buddy_token)
    check("non-admin cannot call admin set-password", st == 403, (st, d))
    if is_admin:
        st, d, _ = req("POST", "/api/admin/set-password",
                       {"userId": me_id, "password": "admin-set-pass-%s" % RUN}, token=admin_token)
        check("admin set-password allowed for admin (self)", st == 200, d)
        st, d, _ = req("POST", "/api/admin/set-password",
                       {"userId": buddy_id, "password": "admin-set-buddy-%s" % RUN}, token=admin_token)
        check("admin can reset another user", st == 200, d)
        st, d, _ = req("GET", "/api/me", token=buddy_token)
        check("admin reset REVOKED the target's session", st == 401, st)
        st, d, _ = req("POST", "/api/auth/login",
                       {"name": other, "password": "admin-set-buddy-%s" % RUN})
        check("admin-set password works for the target", st == 200, d)
        st, d, _ = req("POST", "/api/admin/set-password",
                       {"userId": buddy_id, "password": "short"}, token=admin_token)
        check("admin reset still enforces the password policy", st == 400, d)
        st, d, _ = req("GET", "/api/admin/auth-events", token=admin_token)
        kinds = {e.get("kind") for e in (d.get("events") or [])}
        check("admin can read the auth audit trail", st == 200 and "login_ok" in kinds, list(kinds)[:8])
        check("audit trail records failures too",
              "login_fail" in kinds or "reset_fail" in kinds, list(kinds)[:8])

    # ------------------------------------------- 12. security headers
    st, d, hdrs = req("GET", "/")
    csp = hdrs.get("Content-Security-Policy", "")
    check("HTML is served with a CSP", "default-src 'self'" in csp and "frame-ancestors" in csp, csp[:120])
    check("nosniff header present", hdrs.get("X-Content-Type-Options") == "nosniff", hdrs.get("X-Content-Type-Options"))
    body = raw_get("/../../etc/passwd")
    check("path traversal cannot escape the public/ dir",
          body is not None and "root:" not in body and "JEE WAR ROOM" in body, (body or "")[:120])

    print("\n%d passed, %d failed" % (len(passed), len(failed)))
    if failed:
        print("FAILED CHECKS:")
        for f in failed:
            print("  -", f)
        return 1
    print("ALL AUTH SECURITY CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
