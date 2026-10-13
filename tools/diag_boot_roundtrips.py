#!/usr/bin/env python3
"""
Round-trip diagnostic for the FIRST-LOAD path.

The first screen used to cost four parallel API calls (me + friends +
announcements + chapters), and /api/friends was ~18 SQL statements PER BUDDY.
On Vercel every statement is a separate HTTPS round trip to Turso (see
DEPLOY.md → round-trip budget), so with 10 buddies the buddy list alone was
160+ round trips and the boot burst ~190 — seconds of wall clock on a cold
start, which is exactly what "the first load takes too long" was.

Hard targets asserted here (see STATUS.md):
  * GET /api/me                        <= 12 statements
  * GET /api/friends (3 buddies)       <= 20 statements
  * GET /api/bootstrap (whole screen)  <= 30 statements, in ONE request
  * /api/friends must NOT grow per buddy any more: +5 buddies may cost at
    most 6 extra statements (the old code cost 15-18 each)
  * /api/bootstrap's sub-payloads must be byte-identical to the payloads of
    /api/me, /api/friends, /api/announcements and /api/chapters
  * the batched buddy list must equal the per-buddy reference implementation
  * nothing on the boot path may issue housekeeping DELETEs inline

Uses the throwaway local database data/warroom.db (gitignored) and rebuilds it.
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
ME_LIMIT = 12                   # steady state (snapshots already current)
ME_FIRST_LIMIT = 20             # first call also writes the backfilled snapshots
FRIENDS_LIMIT = 20
BOOT_LIMIT = 30
FRIENDS_GROWTH_LIMIT = 6        # statements allowed for 5 more buddies

STATEMENTS = []                 # (thread_name, sql)
COUNTING = [False]
_real_db = dbwrap.db

HK_DELETE_MARKERS = (
    "DELETE FROM sessions WHERE expires_at",
    "DELETE FROM auth_throttle WHERE updated_at",
    "DELETE FROM auth_events WHERE ts",
)


class _CountingConn:
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


def start_server():
    import http.server
    import socketserver

    class _Srv(socketserver.ThreadingMixIn, http.server.HTTPServer):
        daemon_threads = True
        allow_reuse_address = True

    srv = _Srv(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=srv.serve_forever, daemon=True, name="diag-http").start()
    return srv, srv.server_address[1]


def req(port, path, body=None, token=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=60)
    headers = {}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if token:
        headers["Authorization"] = "Bearer " + token
    conn.request("POST" if body is not None else "GET", path,
                 json.dumps(body) if body is not None else None, headers)
    r = conn.getresponse()
    raw = r.read().decode("utf-8", "replace")
    conn.close()
    try:
        return r.status, json.loads(raw)
    except Exception:
        return r.status, {"raw": raw}


def request_statements():
    return [s for (tn, s) in STATEMENTS if tn != "jwr-housekeeping"]


def measure(port, path, token, body=None):
    del STATEMENTS[:]
    COUNTING[0] = True
    try:
        st, d = req(port, path, body, token)
    finally:
        COUNTING[0] = False
    return st, d, request_statements()


def report(title, stmts):
    print("    --- %s: %d SQL statements ---" % (title, len(stmts)))
    for i, s in enumerate(stmts, 1):
        print("      %2d  %s" % (i, " ".join(s.split())[:104]))


# --------------------------------------------------------------- fixtures
def seed_owner_and_buddies():
    """owner(double, 3 buddies):
       full   — shares everything, real activity on the last 3 days
       quiet  — shares everything, has never logged anything
       hidden — connected, but every sharing switch is OFF
    """
    c = _real_db()
    now = server.now_iso()
    day_f = server.DAY_F
    today = server.datetime.now(server.IST).date()

    def add_user(name):
        salt = authsec.new_salt()
        c.execute("""INSERT INTO users(name,pass_hash,salt,code,exam_date,avatar_color,
                                        created_at,role,pw_changed_at)
                     VALUES(?,?,?,?,?,?,?,'user',?)""",
                  (name, authsec.hash_pw("diag pass", salt), salt,
                   authsec.gen_friend_code(6), "2027-01-24", "#22d3ee", now, now))
        return c.execute("SELECT id FROM users WHERE name=?", (name,)).fetchone()[0]

    owner = add_user("boot_owner")
    full = add_user("boot_full")
    quiet = add_user("boot_quiet")
    hidden = add_user("boot_hidden")
    for uid in (full, quiet, hidden):
        c.execute("INSERT INTO friendships(user_id,friend_id,created_at) VALUES(?,?,?)", (owner, uid, now))
        c.execute("INSERT INTO friendships(user_id,friend_id,created_at) VALUES(?,?,?)", (uid, owner, now))
    c.execute("INSERT INTO settings(user_id,json) VALUES(?,?)",
              (hidden, json.dumps({"sharing": {"studyTime": False, "targets": False, "questions": False,
                                               "streak": False, "xp": False, "score": False, "air": False,
                                               "mocks": False, "distractions": False}})))
    # boot_full shares every block (so the whole batched path is exercised,
    # distractions included — they are OFF by default)
    c.execute("INSERT INTO settings(user_id,json) VALUES(?,?)",
              (full, json.dumps({"sharing": {"studyTime": True, "targets": True, "questions": True,
                                             "streak": True, "xp": True, "score": True, "air": True,
                                             "mocks": True, "distractions": True}})))

    # boot_full: 3 consecutive active days ending today
    for back in (0, 1, 2):
        d = day_f(today - server.timedelta(days=back))
        c.execute("""INSERT INTO activities(user_id,type,custom_key,subject,chapter,amount,duration,
                                            extra,note,day,created_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (full, "study", None, "Physics", "Kinematics", 0, 60 if back == 0 else 30,
                   None, None, d, d + "T10:00:00Z"))
    c.execute("""INSERT INTO activities(user_id,type,custom_key,subject,chapter,amount,duration,
                                        extra,note,day,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (full, "dpp", None, "Chemistry", "Mole", 25, 45, None, None, day_f(today), day_f(today)))
    c.execute("""INSERT INTO activities(user_id,type,custom_key,subject,chapter,amount,duration,
                                        extra,note,day,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
              (full, "distraction", None, None, None, 0, 25, "YouTube", None, day_f(today), day_f(today)))
    for status in ("done", "missed"):
        c.execute("""INSERT INTO targets(user_id,day,kind,subject,chapter,title,amount,duration,
                                         status,created_at,completed_at)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (full, day_f(today), "dpp", "Physics", "Kinematics", "DPP", 10, 45, status, now, None))
    c.execute("""INSERT INTO mocks(user_id,test_type,total,score,phys,chem,math,attempted,correct,
                                   incorrect,day,note,created_at)
                 VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
              (full, "Full JEE Main", 300, 180.0, 60, 60, 60, 70, 60, 10, day_f(today), None, now))
    c.execute("INSERT INTO errors(user_id,subject,chapter,etype,qno,note,day,created_at) VALUES(?,?,?,?,?,?,?,?)",
              (full, "Physics", "Kinematics", "Concept", "7", None, day_f(today), now))
    c.execute("INSERT INTO snapshots(user_id,day,score,air) VALUES(?,?,?,?)",
              (full, day_f(today), 50.0, 300000))
    c.execute("INSERT INTO snapshots(user_id,day,score,air) VALUES(?,?,?,?)",
              (full, day_f(today - server.timedelta(days=7)), 20.0, 66666))
    c.execute("""INSERT INTO xp_events(user_id,amount,reason,ref_type,ref_id,day,created_at)
                 VALUES(?,?,?,?,?,?,?)""", (full, 300, "seed", "seed", "1", day_f(today), now))
    for _ in range(2):
        c.execute("INSERT INTO messages(sender,recipient,body,created_at,read_at) VALUES(?,?,?,?,?)",
                  (full, owner, "unread ping", now, None))
    c.commit()
    c.close()
    return owner, full, quiet, hidden


def reference_friend_view(handler, c, uid, pid):
    """The pre-batching per-buddy implementation, kept as the oracle.

    This is exactly what /api/friends used to compute for each buddy (one
    users row, one settings row, one unread count, one summary, one streak,
    one xp total, one snapshot pair, one latest mock and the week aggregates
    per buddy). friend_views_bulk() must return the same JSON.
    """
    views = handler._friend_view(c, uid, pid)
    return views


def main():
    print("Rebuilding throwaway DB at %s ..." % DB)
    fresh_db()
    owner, full, quiet, hidden = seed_owner_and_buddies()
    print("owner id=%d, buddies: full=%d quiet=%d hidden=%d\n" % (owner, full, quiet, hidden))

    server.Handler._LAST_HOUSEKEEPING[0] = 0.0
    server.Handler._HK_BUSY[0] = False

    srv, port = start_server()
    dbwrap.db = counting_db
    fails = []
    try:
        st, d = req(port, "/healthz")
        assert st == 200 and d.get("ok"), "warm-up /healthz failed: %s %s" % (st, d)
        st, login = req(port, "/api/auth/login", {"name": "boot_owner", "password": "diag pass"})
        assert st == 200 and login.get("token"), "login failed: %s %s" % (st, login)
        token = login["token"]

        # ---------------- per-endpoint round trips ----------------
        # First /api/me of a brand-new account also writes the snapshot rows it
        # backfills, so the interesting number is the steady-state one.
        st, me_first, me_first_stmts = measure(port, "/api/me", token)
        st, me, me_stmts = measure(port, "/api/me", token)
        st, friends, friends_stmts = measure(port, "/api/friends", token)
        st, announcements, ann_stmts = measure(port, "/api/announcements", token)
        st, chapters, chap_stmts = measure(port, "/api/chapters", token)
        st, boot, boot_stmts = measure(port, "/api/bootstrap", token)

        for st_, d_, what in ((st, boot, "/api/bootstrap"),):
            if st_ != 200 or not isinstance(d_, dict) or "me" not in d_:
                fails.append("%s did not answer a bootstrap payload: %s %s" % (what, st_, str(d_)[:200]))

        report("GET /api/me (first call, writes today's snapshot)", me_first_stmts)
        report("GET /api/me (steady state)", me_stmts)
        report("GET /api/friends (3 buddies)", friends_stmts)
        report("GET /api/announcements", ann_stmts)
        report("GET /api/chapters", chap_stmts)
        report("GET /api/bootstrap (me+friends+announcements+chapters)", boot_stmts)

        if len(me_first_stmts) > ME_FIRST_LIMIT:
            fails.append("/api/me (first call) used %d statements (limit %d)"
                         % (len(me_first_stmts), ME_FIRST_LIMIT))
        if len(me_stmts) > ME_LIMIT:
            fails.append("/api/me (steady state) used %d statements (limit %d)" % (len(me_stmts), ME_LIMIT))
        if len(friends_stmts) > FRIENDS_LIMIT:
            fails.append("/api/friends used %d statements (limit %d)" % (len(friends_stmts), FRIENDS_LIMIT))
        if len(boot_stmts) > BOOT_LIMIT:
            fails.append("/api/bootstrap used %d statements (limit %d)" % (len(boot_stmts), BOOT_LIMIT))
        for stmts, what in ((me_first_stmts, "/api/me"), (me_stmts, "/api/me"),
                            (friends_stmts, "/api/friends"), (boot_stmts, "/api/bootstrap")):
            for s in stmts:
                if any(m in s for m in HK_DELETE_MARKERS):
                    fails.append("%s runs housekeeping inline: %s" % (what, s[:80]))

        # ---------------- payload identity ----------------
        def same(a, b):
            return json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)

        for key, payload in (("me", me), ("friends", friends),
                             ("announcements", announcements), ("chapters", chapters)):
            if not same(boot.get(key), payload):
                fails.append("/api/bootstrap['%s'] differs from GET /api/%s" % (key, key))

        # ---------------- buddy-list growth (the old N+1) ----------------
        c = _real_db()
        extra_ids = []
        now = server.now_iso()
        for i in range(5):
            c.execute("""INSERT INTO users(name,pass_hash,salt,code,exam_date,avatar_color,
                                            created_at,role,pw_changed_at)
                         VALUES(?,?,?,?,?,?,?,'user',?)""",
                      ("boot_extra_%d" % i, authsec.hash_pw("diag pass", authsec.new_salt()),
                       authsec.new_salt(), authsec.gen_friend_code(6), "2027-01-24", "#22d3ee", now, now))
            uid = c.execute("SELECT id FROM users WHERE name=?", ("boot_extra_%d" % i,)).fetchone()[0]
            extra_ids.append(uid)
            c.execute("INSERT INTO friendships(user_id,friend_id,created_at) VALUES(?,?,?)", (owner, uid, now))
            c.execute("INSERT INTO friendships(user_id,friend_id,created_at) VALUES(?,?,?)", (uid, owner, now))
        c.commit()
        c.close()
        st, friends8, friends8_stmts = measure(port, "/api/friends", token)
        growth = len(friends8_stmts) - len(friends_stmts)
        print("    GET /api/friends with 5 more buddies: %d statements (+%d)"
              % (len(friends8_stmts), growth))
        if growth > FRIENDS_GROWTH_LIMIT:
            fails.append("/api/friends grew by %d statements for 5 buddies (limit +%d) — "
                         "the per-buddy N+1 is back" % (growth, FRIENDS_GROWTH_LIMIT))

        # ---------------- batched list == per-buddy oracle ----------------
        handler = object.__new__(server.Handler)
        c = _real_db()
        try:
            oracle = [v for v in (reference_friend_view(handler, c, owner, pid)
                                  for pid in server.friend_ids(c, owner)) if v]
        finally:
            c.close()
        c = _real_db()
        try:
            batched = handler.friend_views_bulk(c, owner, server.friend_ids(c, owner))
        finally:
            c.close()
        if not same(oracle, batched):
            fails.append("friend_views_bulk() no longer matches the per-buddy reference implementation")
            for a, b in zip(oracle, batched):
                if not same(a, b):
                    print("      differs for uid=%s\n        ref: %s\n        new: %s"
                          % (a.get("uid"), json.dumps(a, sort_keys=True)[:300],
                             json.dumps(b, sort_keys=True)[:300]))

        # ---------------- known values (hand-checked) ----------------
        fv = {f["uid"]: f for f in friends["friends"]}
        full_v = fv.get(full, {})
        checks = [
            ("boot_full summary minutes", full_v.get("summary", {}).get("minutes"), 60),
            ("boot_full summary questions", full_v.get("summary", {}).get("questions"), 25),
            ("boot_full summary execution", full_v.get("summary", {}).get("execution"), 50),
            ("boot_full summary mocks", full_v.get("summary", {}).get("mocks"), 1),
            ("boot_full summary errors", full_v.get("summary", {}).get("errors"), 1),
            ("boot_full streak", full_v.get("streak", {}).get("current"), 3),
            ("boot_full xp level", full_v.get("xp", {}).get("level"), 4),
            ("boot_full air", full_v.get("air"), 300000),
            ("boot_full unread", full_v.get("unread"), 2),
            ("boot_full distraction week", full_v.get("friendDistractWeek"), 25),
            ("hidden buddy has no summary", "summary" in fv.get(hidden, {}), False),
            ("hidden buddy has no air", "air" in fv.get(hidden, {}), False),
            ("quiet buddy air defaults", fv.get(quiet, {}).get("air"), 600000),
        ]
        for what, got, want in checks:
            status = "ok " if got == want else "FAIL"
            print("    %-32s %-24s (want %s)  %s" % (what, got, want, status))
            if got != want:
                fails.append("%s: got %r, want %r" % (what, got, want))
    finally:
        dbwrap.db = _real_db
        srv.shutdown()

    print()
    if fails:
        print("FAILED (%d):" % len(fails))
        for f in fails:
            print("  * " + f)
        sys.exit(1)
    print("OK — boot path is %d statements in ONE request; /api/friends is buddy-count independent."
          % len(boot_stmts))


if __name__ == "__main__":
    main()
