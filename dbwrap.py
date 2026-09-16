"""
Database adapter for JEE WAR ROOM.
- Local mode (default): stdlib sqlite3 file at data/warroom.db (dev / old setup).
- Cloud mode: when TURSO_DATABASE_URL (or LIBSQL_URL) is set, connects to a
  Turso/libSQL database over HTTPS using the `libsql` package (sqlite3-compatible
  wire protocol). The app's SQL is unchanged; this wrapper only restores the
  sqlite3.Row access style (rows by column name) that the app relies on.

Performance notes for cloud mode:
- The libsql client is created once per worker thread and reused across
  requests (a fresh TLS+Hrana handshake on every call was the main source of
  latency). A dead/frozen connection is transparently reconnected once.
- Each execute() is one network round-trip, so bulk inserts must use a single
  multi-row INSERT or executemany().
"""
import os, re, sqlite3, threading

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "warroom.db")
if not (os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL")):
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)

TURSO_URL = os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL") or ""
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("LIBSQL_AUTH_TOKEN") or ""
CLOUD = bool(TURSO_URL)

_tls = threading.local()


def _cloud_connect():
    import libsql
    return libsql.connect(
        TURSO_URL, auth_token=TURSO_TOKEN,
        _check_same_thread=False, timeout=30,
    )


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
    def __init__(self, cur, conn=None):
        self._cur = cur
        self._conn = conn

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
        # route through the connection so reconnection/retry logic lives in one place
        if self._conn is not None:
            return self._conn.execute(sql, params)
        self._cur.execute(_translate(sql), tuple(params))
        return self

    def executemany(self, sql, seq):
        if self._conn is not None:
            return self._conn.executemany(sql, seq)
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
    """Connection wrapper exposing the subset of the sqlite3 API the app uses.
    In cloud mode the underlying libsql client is cached per worker thread."""
    def __init__(self, inner):
        self._inner = inner

    def _reconnect(self):
        try: self._inner.close()
        except Exception: pass
        self._inner = _cloud_connect()
        _tls.inner = self._inner

    @staticmethod
    def _attempt(inner, sql, params, many):
        t = _translate(sql)
        if many:
            return inner.executemany(t, [tuple(p) for p in params])
        return inner.execute(t, tuple(params))

    def execute(self, sql, params=()):
        try:
            cur = self._attempt(self._inner, sql, params, False)
        except Exception:
            if not CLOUD: raise
            self._reconnect()
            cur = self._attempt(self._inner, sql, params, False)
        return _Cur(cur, self)

    def executemany(self, sql, seq):
        try:
            cur = self._attempt(self._inner, sql, seq, True)
        except Exception:
            if not CLOUD: raise
            self._reconnect()
            cur = self._attempt(self._inner, sql, seq, True)
        return _Cur(cur, self)

    def executescript(self, script):
        try:
            self._inner.executescript(script)
        except Exception:
            if not CLOUD: raise
            self._reconnect()
            self._inner.executescript(script)
        return self

    def commit(self):
        try:
            self._inner.commit()
        except Exception:
            if not CLOUD: raise
            self._reconnect(); self._inner.commit()

    def rollback(self):
        try:
            self._inner.rollback()
        except Exception:
            if not CLOUD: raise
            self._reconnect(); self._inner.rollback()

    def close(self):
        # In cloud mode keep the cached client alive for the next request on
        # this warm worker; only local sqlite connections close per request.
        if not CLOUD:
            try: self._inner.close()
            except Exception: pass

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
    """Open a database connection (one per request, same as the original app).
    In cloud mode the underlying client is reused across requests per thread."""
    if not CLOUD:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c
    inner = getattr(_tls, "inner", None)
    if inner is None:
        inner = _cloud_connect()
        _tls.inner = inner
    return _Conn(inner)
