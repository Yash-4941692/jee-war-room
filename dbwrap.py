"""
Database adapter for JEE WAR ROOM.
- Local mode (default): stdlib sqlite3 file at data/warroom.db (dev / old setup).
- Cloud mode: when TURSO_DATABASE_URL (or LIBSQL_URL) is set, connects to a
  Turso/libSQL database over HTTPS using the `libsql` package (sqlite3-compatible
  wire protocol). The app's SQL is unchanged; this wrapper only restores the
  sqlite3.Row access style (rows by column name) that the app relies on.
"""
import os, re, sqlite3

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "warroom.db")
if not (os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL")):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

TURSO_URL = os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL") or ""
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("LIBSQL_AUTH_TOKEN") or ""
CLOUD = bool(TURSO_URL)


class Row:
    """Minimal sqlite3.Row-compatible row: index by name or position."""
    __slots__ = ("_cols", "_vals")

    def __init__(self, cols, vals):
        self._cols = list(cols)
        self._vals = list(vals)

    def keys(self):
        return list(self._cols)

    def __getitem__(self, k):
        if isinstance(k, (int, slice)):
            return self._vals[k]
        try:
            return self._vals[self._cols.index(k)]
        except ValueError:
            raise KeyError(k)

    def get(self, k, default=None):
        try:
            return self[k]
        except (KeyError, IndexError):
            return default

    def __iter__(self):
        return iter(self._vals)

    def __len__(self):
        return len(self._vals)

    def __eq__(self, other):
        return tuple(self._vals) == tuple(other)

    def __hash__(self):
        return hash(tuple(self._vals))

    def __repr__(self):
        return "Row(%s)" % dict(zip(self._cols, self._vals))


class _Cur:
    """Cursor wrapper turning libsql tuple rows into Row objects."""
    def __init__(self, cur):
        self._cur = cur

    @property
    def description(self):
        return self._cur.description

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    @staticmethod
    def _colnames(desc):
        return [d[0] for d in (desc or [])]

    def _wrap(self, t):
        return Row(self._colnames(self._cur.description), t) if t is not None else None

    def execute(self, sql, params=()):
        self._cur.execute(_translate(sql), tuple(params))
        return self

    def executemany(self, sql, seq):
        self._cur.executemany(_translate(sql), [tuple(p) for p in seq])
        return self

    def executescript(self, script):
        self._cur.executescript(script)
        return self

    def fetchone(self):
        return self._wrap(self._cur.fetchone())

    def fetchmany(self, n=-1):
        rows = self._cur.fetchmany() if n < 0 else self._cur.fetchmany(n)
        cn = self._colnames(self._cur.description)
        return [Row(cn, t) for t in rows]

    def fetchall(self):
        rows = self._cur.fetchall()
        cn = self._colnames(self._cur.description)
        return [Row(cn, t) for t in rows]

    def __iter__(self):
        return iter(self.fetchall())

    def close(self):
        self._cur.close()


class _Conn:
    """Connection wrapper exposing the subset of the sqlite3 API the app uses."""
    def __init__(self, inner):
        self._inner = inner

    def execute(self, sql, params=()):
        return _Cur(self._inner.execute(_translate(sql), tuple(params)))

    def executemany(self, sql, seq):
        self._inner.executemany(_translate(sql), [tuple(p) for p in seq])
        return self

    def executescript(self, script):
        self._inner.executescript(script)
        return self

    def commit(self):
        self._inner.commit()

    def rollback(self):
        self._inner.rollback()

    def close(self):
        try:
            self._inner.close()
        except Exception:
            pass

    # PRAGMA table_info shim used by the migration code
    def table_columns(self, tbl):
        cur = self.execute("SELECT name FROM pragma_table_info(?)", (tbl,))
        return {r[0] for r in cur.fetchall()}

    @property
    def row_factory(self):
        return Row

    @row_factory.setter
    def row_factory(self, _v):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        if a[0] is None:
            self.commit()
        self.close()


_PTI = re.compile(r"""^\s*PRAGMA\s+table_info\s*\(\s*['"]?([A-Za-z_][A-Za-z0-9_]*)['"]?\s*\)\s*$""", re.I)

def _translate(sql):
    """Remote libSQL rejects connection PRAGMAs and understands table_info as
    a table-valued function; rewrite both so identical app SQL works anywhere."""
    if not CLOUD:
        return sql
    s = sql.strip()
    if s.upper().startswith("PRAGMA"):
        m = _PTI.match(sql)
        if m:
            return 'SELECT cid, name, type, "notnull", dflt_value, pk FROM pragma_table_info(\'%s\')' % m.group(1)
        return "SELECT 1 WHERE 1=0"   # journal_mode / foreign_keys: no-op remotely
    return sql


def db():
    """Open a database connection (one per request, same as the original app)."""
    if not CLOUD:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c
    import libsql
    inner = libsql.connect(
        TURSO_URL, auth_token=TURSO_TOKEN,
        _check_same_thread=False, timeout=30,
    )
    return _Conn(inner)
