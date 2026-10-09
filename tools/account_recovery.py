#!/usr/bin/env python3
"""
JEE WAR ROOM — account recovery for the administrator.

Run this on a machine that has the database credentials, NOT in a chat window:

    export TURSO_DATABASE_URL=libsql://<db>-<org>.turso.io
    export TURSO_AUTH_TOKEN=<token>
    python3 tools/account_recovery.py --help

Works against the cloud database when those variables are set, otherwise
against the local data/warroom.db (dbwrap decides, exactly like server.py).

WHY THIS EXISTS
    Passwords are PBKDF2-HMAC-SHA256 hashes with a per-user random salt. They
    are one-way: nobody — not you, not the admin, not this script — can read a
    password back out of the database. What you CAN do is
      * test a candidate you remember (`verify`), which never leaves this
        machine, and
      * set a new one (`set-password`) when the candidate does not match.

COMMANDS
    list                                  every account (id, name, role)
    status   --name Yash                  one account: codes left, sessions, age
    verify   --name Yash                  type candidates at a prompt (getpass)
    verify   --name Yash --file words.txt test a list of candidates
    verify   --hash <h> --salt <s> --candidate "..."   offline check, no DB
    set-password --name Yash              set a new password (prompts twice)
    codes    --name Yash                  issue + print 8 fresh recovery codes
    revoke   --name Yash | --all          destroy sessions (forces re-login)

Every write is logged to the auth_events audit trail, visible to the admin in
the app.
"""
import argparse
import getpass
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import authsec          # noqa: E402
import dbwrap           # noqa: E402
import server           # noqa: E402

MAX_CANDIDATES = 5000


def connect():
    if not dbwrap.CLOUD:
        print("note: TURSO_DATABASE_URL is not set — using the LOCAL database at %s"
              % dbwrap.DB_PATH)
    else:
        print("note: using the CLOUD database")
    server.init_db()
    return dbwrap.db()


def find_user(c, name):
    u = c.execute("SELECT id,name,role,code,created_at,pw_changed_at,pass_hash,salt "
                  "FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone()
    if not u:
        sys.exit("No account named %r. Try: account_recovery.py list" % name)
    return u


def test_candidate(pw, pass_hash, salt):
    t0 = time.time()
    ok, legacy = authsec.verify_pw(pw, pass_hash, salt)
    return ok, legacy, time.time() - t0


# ------------------------------------------------------------------ commands
def cmd_list(args):
    c = connect()
    rows = c.execute("SELECT id,name,role,code,created_at FROM users ORDER BY id").fetchall()
    print("\n%-4s %-22s %-6s %-8s %s" % ("ID", "NAME", "ROLE", "CODE", "JOINED"))
    for r in rows:
        print("%-4s %-22s %-6s %-8s %s" % (r["id"], r["name"], r["role"] or "user",
                                           r["code"], (r["created_at"] or "")[:10]))
    print("\n%d accounts. Friend codes link buddies only — they cannot sign anyone in."
          % len(rows))
    c.close()


def cmd_status(args):
    c = connect()
    u = find_user(c, args.name)
    st = server.recovery_status(c, u["id"])
    sessions = server.active_sessions(c, u["id"])
    print("\nAccount      : %s (id %s, role %s)" % (u["name"], u["id"], u["role"]))
    print("Friend code  : %s  (buddy linking only — NOT a credential)" % u["code"])
    print("Joined       : %s" % (u["created_at"] or "?"))
    print("Password set : %s" % (u["pw_changed_at"] or "before tracking (pre-hardening)"))
    print("Recovery     : %d of %d codes unused%s"
          % (st["remaining"], st["total"],
             "" if st["total"] else "  <-- none issued yet; they appear on next login"))
    print("Sessions     : %d active" % len(sessions))
    for s in sessions[:5]:
        print("               - created %s, expires %s, %s"
              % (s["createdAt"], time.strftime("%Y-%m-%d", time.gmtime(s["expiresAt"] or 0)),
                 (s["userAgent"] or "?")[:40]))
    print("\nPassword itself is unrecoverable by design (PBKDF2, one-way).")
    print("To get back in: `verify` a candidate, or `set-password` a new one.")
    c.close()


def cmd_verify(args):
    # Offline mode: check against a hash/salt you already have (e.g. an old
    # dump) without touching any database.
    if args.hash and args.salt:
        candidates = []
        if args.candidate:
            candidates.append(args.candidate)
        if args.file:
            candidates += read_file(args.file)
        if not candidates:
            candidates = [getpass.getpass("Candidate password (typed locally, never sent): ")]
        return report(candidates, args.hash, args.salt, None, None)

    c = connect()
    u = find_user(c, args.name)
    candidates = []
    if args.candidate:
        candidates.append(args.candidate)
    if args.file:
        candidates += read_file(args.file)
    if not candidates:
        print("Type the passwords you think it might be. Nothing is transmitted;"
              " each one is hashed right here and compared.\n"
              "(empty line to finish)")
        while True:
            try:
                pw = getpass.getpass("candidate: ")
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not pw:
                break
            candidates.append(pw)
    if not candidates:
        sys.exit("Nothing to test.")
    report(candidates, u["pass_hash"], u["salt"], c, u)


def read_file(path):
    if not os.path.isfile(path):
        sys.exit("No such file: %s" % path)
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        out = [ln.rstrip("\r\n") for ln in f if ln.strip()]
    if len(out) > MAX_CANDIDATES:
        sys.exit("Refusing to test %d candidates (limit %d). This tool is for "
                 "recovering YOUR account, not for brute-forcing."
                 % (len(out), MAX_CANDIDATES))
    return out


def report(candidates, pass_hash, salt, c, u):
    print("\nTesting %d candidate(s) — PBKDF2 does %d iterations each, "
          "so this takes ~0.1 s per guess…" % (len(candidates), authsec.PBKDF2_ITERS))
    matched = None
    for i, pw in enumerate(candidates, 1):
        ok, legacy, dt = test_candidate(pw, pass_hash, salt)
        mark = "MATCH" if ok else "no"
        shown = ("the %d-space-padded form" % (len(pw) - len(pw.strip()))) if (legacy and ok) else ""
        print("  [%3d/%3d] %-6s %s%s" % (i, len(candidates), mark,
                                         "*" * min(len(pw), 24), ("  <- " + shown) if shown else ""))
        if ok:
            matched = (pw, legacy)
            break
    if not matched:
        print("\nNo match. The stored password is none of those.")
        print("Next step: set a new one with  account_recovery.py set-password --name %s"
              % (u["name"] if u else "<name>"))
        return 1
    pw, legacy = matched
    print("\nFOUND IT. That is the password for %s." % (u["name"] if u else "this hash"))
    if legacy:
        print("NOTE: it only matched with surrounding whitespace — this is the legacy")
        print("      account shape. Sign in once and the server re-hashes it trimmed,")
        print("      after which stray spaces stop mattering.")
    print("For safety, change it now:  account_recovery.py set-password --name %s"
          % (u["name"] if u else "<name>"))
    if c is not None and u is not None:
        authsec.log_event(c, "pw_verified_by_cli", user_id=u["id"], name=u["name"],
                          ip="local-cli", detail="admin recovery tool")
        c.close()
    return 0


def cmd_set_password(args):
    c = connect()
    u = find_user(c, args.name)
    pw = args.password
    if pw is None:
        pw = getpass.getpass("New password for %s (min %d chars, typed locally): "
                             % (u["name"], authsec.PW_MIN))
        again = getpass.getpass("Type it again: ")
        if pw != again:
            sys.exit("The two entries did not match. Nothing was changed.")
    err = authsec.password_policy_error(pw, u["name"])
    if err:
        sys.exit("Rejected: " + err)
    # Same rotation + revocation the API performs (server._set_password is a
    # request-handler method, so the identical steps are repeated here).
    salt = authsec.new_salt()
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    c.execute("UPDATE users SET pass_hash=?, salt=?, pw_changed_at=? WHERE id=?",
              (authsec.hash_pw(pw, salt), salt, now, u["id"]))
    c.commit()
    server.revoke_sessions(c, u["id"])
    authsec.log_event(c, "admin_cli_set_password", user_id=u["id"], name=u["name"],
                      ip="local-cli", detail="account_recovery.py")
    print("\nPassword updated for %s." % u["name"])
    print("All their sessions were destroyed — every device must sign in again.")
    left = server.recovery_status(c, u["id"])["remaining"]
    if left == 0:
        print("They have no recovery codes left; issue some with:")
        print("  python3 tools/account_recovery.py codes --name %s" % u["name"])
    c.close()


def cmd_codes(args):
    c = connect()
    u = find_user(c, args.name)
    codes = server.issue_recovery_codes(c, u["id"], "local-cli")
    print("\nRecovery codes for %s — shown ONCE, the database keeps only digests."
          % u["name"])
    print("Old unused codes were destroyed.\n")
    for i in range(0, len(codes), 2):
        print("   " + "    ".join(codes[i:i + 2]))
    print("\nGive these to %s over a private channel (they reset a password once each)."
          % u["name"])
    c.close()


def cmd_revoke(args):
    c = connect()
    if args.all:
        n = c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0]
        c.execute("DELETE FROM sessions")
        c.commit()
        authsec.log_event(c, "admin_cli_revoke_all", ip="local-cli",
                          detail="%d sessions destroyed" % n)
        print("Destroyed %d session(s). Every user must sign in again." % n)
    else:
        if not args.name:
            sys.exit("Pass --name <user> or --all")
        u = find_user(c, args.name)
        before = c.execute("SELECT COUNT(*) FROM sessions WHERE user_id=?", (u["id"],)).fetchone()[0]
        server.revoke_sessions(c, u["id"])
        authsec.log_event(c, "admin_cli_revoke", user_id=u["id"], name=u["name"],
                          ip="local-cli", detail="%d sessions destroyed" % before)
        print("Destroyed %d session(s) for %s." % (before, u["name"]))
    c.close()


def main():
    p = argparse.ArgumentParser(
        description="Recover access to a JEE WAR ROOM account (admin tool).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("COMMANDS")[-1])
    sub = p.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("list", help="list every account")
    s.set_defaults(fn=cmd_list)

    s = sub.add_parser("status", help="show one account's security state")
    s.add_argument("--name", required=True)
    s.set_defaults(fn=cmd_status)

    s = sub.add_parser("verify", help="test candidate passwords locally (never sent anywhere)")
    s.add_argument("--name", help="account to test against")
    s.add_argument("--candidate", help="a single candidate (else you are prompted)")
    s.add_argument("--file", help="file of candidates, one per line (max %d)" % MAX_CANDIDATES)
    s.add_argument("--hash", help="offline: pass_hash to test against")
    s.add_argument("--salt", help="offline: salt to test against")
    s.set_defaults(fn=cmd_verify)

    s = sub.add_parser("set-password", help="set a new password and revoke all sessions")
    s.add_argument("--name", required=True)
    s.add_argument("--password", help="new password (else prompted twice, hidden)")
    s.set_defaults(fn=cmd_set_password)

    s = sub.add_parser("codes", help="issue and print fresh recovery codes")
    s.add_argument("--name", required=True)
    s.set_defaults(fn=cmd_codes)

    s = sub.add_parser("revoke", help="destroy sessions (force re-login)")
    s.add_argument("--name")
    s.add_argument("--all", action="store_true")
    s.set_defaults(fn=cmd_revoke)

    args = p.parse_args()
    try:
        return args.fn(args) or 0
    except KeyboardInterrupt:
        print("\ninterrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
