#!/usr/bin/env python3
"""
Turso/libSQL helpers for JEE WAR ROOM.

  TURSO_DATABASE_URL=libsql://... TURSO_AUTH_TOKEN=... python tools/turso_io.py migrate
      -> creates the schema in the cloud DB and copies every row from the
         local data/warroom.db (accounts, chapters, settings, ...).

  TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... python tools/turso_io.py dump backups/warroom-dump.sql
      -> REDACTED plain-SQL dump of the cloud DB. This is the one the weekly
         GitHub backup commits: password hashes, salts, friend codes, session
         tokens, recovery-code digests and private message bodies are stripped,
         and the writer refuses to produce a file that still contains them.

  ... python tools/turso_io.py dump --full /tmp/private-dump.sql
      -> COMPLETE dump (restorable as-is). Refuses to write inside the git
         worktree. Keep it encrypted; never commit it.

Restoring from a REDACTED dump brings back all study data but nobody can sign
in — the admin then issues new passwords with tools/account_recovery.py.
"""
import os
import re
import sys
import sqlite3
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dbwrap
import server

LOCAL_DB = dbwrap.DB_PATH


def table_names(conn, remote):
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
    ).fetchall()
    return [r[0] for r in rows]


def cmd_migrate():
    if not dbwrap.CLOUD:
        sys.exit("Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN first.")
    print("Creating schema on remote database ...")
    server.init_db()

    src = sqlite3.connect(LOCAL_DB, timeout=30)
    dst = dbwrap.db()

    # tables the schema just created, then copy data from oldest (users) first
    tables = table_names(src, False)
    tables.sort(key=lambda t: 0 if t in ("users", "settings") else 1)
    for t in tables:
        cols = [r[1] for r in src.execute('PRAGMA table_info("%s")' % t)]
        rows = src.execute('SELECT "%s" FROM "%s"' % ('","'.join(cols), t)).fetchall()
        if not rows:
            print("  %-14s %5d rows (skipped)" % (t, 0))
            continue
        ph = ",".join("?" * len(cols))
        sql = 'INSERT OR REPLACE INTO "%s" ("%s") VALUES (%s)' % (t, '","'.join(cols), ph)
        dst.executemany(sql, rows)
        dst.commit()
        print("  %-14s %5d rows copied" % (t, len(rows)))

    # keep AUTOINCREMENT sequences so generated ids continue correctly
    try:
        seqs = src.execute("SELECT name, seq FROM sqlite_sequence").fetchall()
        for name, seq in seqs:
            dst.execute("INSERT OR REPLACE INTO sqlite_sequence(name, seq) VALUES (?,?)", (name, seq))
        dst.commit()
        print("  %-14s %5d rows copied" % ("sqlite_sequence", len(seqs)))
    except Exception as e:
        print("  sqlite_sequence skipped:", e)

    # verify
    print("\nVerification (remote row counts):")
    ok = True
    for t in tables:
        n_local = src.execute('SELECT count(*) FROM "%s"' % t).fetchone()[0]
        n_remote = dst.execute('SELECT count(*) FROM "%s"' % t).fetchone()[0]
        flag = "OK " if n_local == n_remote else "MISMATCH"
        if n_local != n_remote:
            ok = False
        print("  [%s] %-14s local=%d remote=%d" % (flag, t, n_local, n_remote))
    dst.close()
    src.close()
    print("\nMIGRATION", "COMPLETE" if ok else "FINISHED WITH MISMATCHES")


def sql_literal(v):
    if v is None:
        return "NULL"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, bytes):
        return "X'" + v.hex() + "'"
    return "'" + str(v).replace("'", "''") + "'"


# --------------------------------------------------------------------------
# Redaction. A dump that contains credentials is not a backup, it is a keyring.
#
# WHY THIS EXISTS: backups/warroom-dump.sql was committed to this repository
# weekly and contained every user's password hash, salt, friend code AND their
# live plaintext session tokens — i.e. anyone with read access to the repo could
# log in as any user (including the admin) without a password. The default dump
# is now safe to store in git; a FULL dump is only ever written outside the
# worktree (or as a private, expiring CI artifact).
#
#   table -> None              : skip the table entirely
#   table -> {col: replacement}: keep the row, replace those columns
# --------------------------------------------------------------------------
SKIP_TABLES = {
    # Bearer tokens are live logins; restoring them would be a security hole.
    # Sessions are short-lived by design, so nobody needs them in a backup.
    "sessions",
    # SHA-256 digests of one-time password-reset codes.
    "recovery_codes",
    # Transient brute-force counters; meaningless to restore.
    "auth_throttle",
    # Idempotency cache.
    "nonces",
}

REDACT_COLUMNS = {
    "users": {
        "pass_hash": "",              # PBKDF2 digest — not restorable, by design
        "salt": "",
        # Friend codes are no longer credentials, but they are still account
        # identifiers used to look people up; blanked to a unique placeholder
        # so the UNIQUE constraint keeps the dump loadable.
        "code": "@@CODE@@",
    },
    "messages": {"body": "[redacted private message]"},
    "reports": {"body": "[redacted report]"},
    "auth_events": {"ip": "[redacted]"},
}

# Anything matching this in a "safe" dump means redaction regressed.
SECRET_PATTERNS = [
    (re.compile(r"\b[0-9a-f]{64}\b"), "64-hex digest (password hash or session token)"),
]


def _redacted_value(table, col, value, row_id):
    spec = REDACT_COLUMNS.get(table)
    if not spec or col not in spec:
        return value
    repl = spec[col]
    if repl == "@@CODE@@":
        return "REDACTED-%s" % (row_id if row_id is not None else "x")
    return repl


def audit_dump(text, out_path):
    """Fail loudly if a supposedly-safe dump still contains secret material."""
    problems = []
    for pat, label in SECRET_PATTERNS:
        hits = pat.findall(text)
        if hits:
            problems.append("%s x%d (e.g. %s…)" % (label, len(hits), hits[0][:12]))
    for table, spec in REDACT_COLUMNS.items():
        for col, repl in spec.items():
            if repl == "":
                continue
    if problems:
        raise SystemExit(
            "REFUSING TO WRITE %s — the dump still contains secrets:\n  %s\n"
            "Redaction in tools/turso_io.py has regressed; fix it before dumping."
            % (out_path, "\n  ".join(problems)))


def inside_repo(path):
    try:
        root = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
        return os.path.abspath(path).startswith(root + os.sep) or os.path.abspath(path) == root
    except Exception:
        return False


def cmd_dump(out_path, full=False, conn=None):
    """Write a SQL dump. Passing `conn` dumps that connection (local dev DB /
    tests); with no connection the cloud database is required."""
    own = conn is None
    if own and not dbwrap.CLOUD:
        sys.exit("Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN first.")
    if full:
        if inside_repo(out_path) and os.environ.get("ALLOW_FULL_DUMP_IN_REPO") != "1":
            sys.exit("Refusing to write a FULL (unredacted) dump inside the git worktree.\n"
                     "  Write it outside the repo (e.g. /tmp or an encrypted volume), or\n"
                     "  set ALLOW_FULL_DUMP_IN_REPO=1 if you really mean it.")
        print("!! FULL dump: contains password hashes, salts and friend codes.")
        print("!! Store it encrypted and never commit it.")
    c = conn if conn is not None else dbwrap.db()
    lines = [
        "-- JEE WAR ROOM database dump (%s)" % ("FULL — DO NOT COMMIT" if full else "REDACTED — safe for git"),
        "-- generated " + datetime.now(timezone.utc).isoformat(),
        "-- credentials/sessions/private message bodies are %s"
        % ("included" if full else "stripped; restoring needs an admin password reset"),
        "PRAGMA foreign_keys=OFF;",
        "BEGIN;",
        "",
    ]
    objs = c.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type='index', name"
    ).fetchall()
    for typ, name, sql in objs:
        if typ == "table" and not full and name in SKIP_TABLES:
            continue
        lines.append("DROP %s IF EXISTS %s;" % ("TABLE" if typ == "table" else typ.upper(), name))
        lines.append(sql.strip() + ";")
    lines.append("")
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        if not full and t in SKIP_TABLES:
            print("  %-14s %5s rows (skipped: secrets)" % (t, "-"))
            continue
        cols = [r["name"] for r in c.execute('PRAGMA table_info("%s")' % t)]
        id_col = cols.index("id") if "id" in cols else None
        n = 0
        for r in c.execute('SELECT * FROM "%s"' % t).fetchall():
            row_id = r[id_col] if id_col is not None else None
            vals = ",".join(
                sql_literal(r[i] if full else _redacted_value(t, cols[i], r[i], row_id))
                for i in range(len(cols)))
            lines.append('INSERT OR REPLACE INTO "%s" VALUES (%s);' % (t, vals))
            n += 1
        tag = "" if full else (" (redacted)" if t in REDACT_COLUMNS else "")
        print("  %-14s %5d rows%s" % (t, n, tag))
    lines += ["COMMIT;", "PRAGMA foreign_keys=ON;", ""]
    text = "\n".join(lines)
    if not full:
        audit_dump(text, out_path)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        f.write(text)
    if own:
        c.close()
    print("Dump written:", out_path, "(FULL)" if full else "(REDACTED)")


if __name__ == "__main__":
    args = sys.argv[1:]
    if args and args[0] == "migrate":
        cmd_migrate()
    elif args and args[0] == "dump":
        rest = args[1:]
        full = "--full" in rest
        paths = [a for a in rest if not a.startswith("--")]
        if not paths:
            sys.exit("usage: turso_io.py dump [--full] <output.sql>")
        cmd_dump(paths[0], full=full)
    else:
        sys.exit(__doc__)
