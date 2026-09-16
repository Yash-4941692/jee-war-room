#!/usr/bin/env python3
"""
Turso/libSQL helpers for JEE WAR ROOM.

  TURSO_DATABASE_URL=libsql://... TURSO_AUTH_TOKEN=... python tools/turso_io.py migrate
      -> creates the schema in the cloud DB and copies every row from the
         local data/warroom.db (accounts, chapters, settings, ...).

  TURSO_DATABASE_URL=... TURSO_AUTH_TOKEN=... python tools/turso_io.py dump backups/warroom-dump.sql
      -> plain-SQL dump of the cloud DB (used by the weekly GitHub backup).
"""
import os
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


def cmd_dump(out_path):
    if not dbwrap.CLOUD:
        sys.exit("Set TURSO_DATABASE_URL and TURSO_AUTH_TOKEN first.")
    c = dbwrap.db()
    lines = [
        "-- JEE WAR ROOM database dump",
        "-- generated " + datetime.now(timezone.utc).isoformat(),
        "PRAGMA foreign_keys=OFF;",
        "BEGIN;",
        "",
    ]
    objs = c.execute(
        "SELECT type, name, sql FROM sqlite_master "
        "WHERE sql IS NOT NULL AND name NOT LIKE 'sqlite_%' ORDER BY type='index', name"
    ).fetchall()
    for typ, name, sql in objs:
        lines.append("DROP %s IF EXISTS %s;" % ("TABLE" if typ == "table" else typ.upper(), name))
        lines.append(sql.strip() + ";")
    lines.append("")
    tables = [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
    for t in tables:
        cols = [r["name"] for r in c.execute('PRAGMA table_info("%s")' % t)]
        n = 0
        for r in c.execute('SELECT * FROM "%s"' % t).fetchall():
            vals = ",".join(sql_literal(r[i]) for i in range(len(cols)))
            lines.append('INSERT OR REPLACE INTO "%s" VALUES (%s);' % (t, vals))
            n += 1
        print("  %-14s %5d rows" % (t, n))
    lines += ["COMMIT;", "PRAGMA foreign_keys=ON;", ""]
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    with open(out_path, "w") as f:
        f.write("\n".join(lines))
    c.close()
    print("Dump written:", out_path)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "migrate":
        cmd_migrate()
    elif len(sys.argv) >= 3 and sys.argv[1] == "dump":
        cmd_dump(sys.argv[2])
    else:
        sys.exit(__doc__)
