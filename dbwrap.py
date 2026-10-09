"""
Database adapter for JEE WAR ROOM.
- Local mode (default): stdlib sqlite3 file at data/warroom.db (dev / old setup).
- Cloud mode: when TURSO_DATABASE_URL (or LIBSQL_URL) is set, connects to a
  Turso/libSQL database over HTTPS using the `libsql` package.

Latency/hang defences for serverless (important!):
- The libsql client is cached per worker thread and reused, but after a
  serverless freeze (or an NAT idle-kill of the underlying socket) a cached
  Hrana connection can hang for MINUTES. We therefore:
    * proactively replace the client after 20 s idle (wall clock jumps during
      a freeze make this fire right after wake);
    * run EVERY remote statement inside a watchdog with a hard 7 s deadline —
      on timeout the poisoned client is abandoned and a fresh one opened, so
      a request fails fast (and the client retries) instead of occupying the
      function for the platform's 300 s task limit;
    * any other exception also triggers one transparent reconnect+retry.
- Use an https:// database URL in production (stateless Hrana HTTP: no
  long-lived WebSocket that can be half-open).
"""
import os, re, sqlite3, threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeout

BASE = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(BASE, "data", "warroom.db")
if not (os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL")):
    try:
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    except OSError:
        pass  # read-only deploy bundles (Vercel): a missing Turso URL must not
              # kill the function at IMPORT time — requests then degrade to a
              # clean JSON 503 with a bootError at /healthz?detail=1 instead of
              # a platform-level "Request failed (500)"

TURSO_URL = os.environ.get("TURSO_DATABASE_URL") or os.environ.get("LIBSQL_URL") or ""
TURSO_TOKEN = os.environ.get("TURSO_AUTH_TOKEN") or os.environ.get("LIBSQL_AUTH_TOKEN") or ""
CLOUD = bool(TURSO_URL)
DB_TIMEOUT = float(os.environ.get("DB_TIMEOUT", "7"))   # hard per-statement deadline, seconds

_tls = threading.local()
# Watchdog pool: libsql native calls release the GIL while waiting on IO, so a
# future thread can enforce the deadline even when the call itself never would.
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="dbio")
# Cold database wake-up is serialized per worker: when a suspended Turso
# database is waking, concurrent requests wait for the FIRST connection rather
# than each opening their own and prolonging the wake (the old 4 s chat poll
# used to pile ~15 connections onto a waking DB).
_connect_lock = threading.Lock()
_last_wake = [0.0]   # epoch of the last successful cold-wake (1-element "cell")


def _cloud_connect():
    import libsql
    return libsql.connect(
        TURSO_URL, auth_token=TURSO_TOKEN,
        _check_same_thread=False, timeout=DB_TIMEOUT,
    )


def _fresh_after_connect(deadline=None):
    """Create a client and verify it with SELECT 1. Serialized by a process
    lock so one waking database is only opened once per worker; threads that
    arrive while a wake is in progress wait (briefly) for the shared client."""
    import time
    # Fast path: another thread already finished the wake.
    existing = getattr(_tls, "inner", None)
    if existing is not None:
        return existing
    with _connect_lock:
        existing = getattr(_tls, "inner", None)
        if existing is not None:
            return existing
        # A sibling thread may have woken the database seconds ago; then our
        # own connect will be quick and doesn't need the long wake deadline.
        woke = _last_wake[0]
        wait_deadline = DB_TIMEOUT if (time.time() - woke) < 30 else (deadline or (DB_TIMEOUT + 18))
        inner = _cloud_connect()
        f = _pool.submit(inner.execute, "SELECT 1")
        try:
            # suspended databases can take ~20-30 s to wake the first time
            f.result(timeout=wait_deadline)
        except BaseException as e:
            try: inner.close()
            except BaseException: pass
            if isinstance(e, Exception):
                raise
            raise _client_crash(e)   # never leak a PanicException upward
        _tls.inner = inner
        _tls.last_used = time.time()
        _last_wake[0] = time.time()
        return inner

_READ_OK = ("SELECT", "PRAGMA", "WITH", "CREATE", "INSERT OR REPLACE", "INSERT OR IGNORE")
def _is_safe_to_retry(sql):
    head = sql.lstrip().upper()
    return head.startswith(_READ_OK)


def _client_crash(e):
    """The libsql binding raises Rust-side panics as pyo3 PanicException — a
    BaseException, NOT an Exception. Letting one escape kills the request with
    no response at all (Vercel answers a non-JSON platform 500: the bare
    'Request failed (500)'). Every such crash is converted to a plain
    RuntimeError here so the normal error handling (JSON 500, retry-safe
    reconnect) applies. A crashed client is always poisoned: replace it."""
    return RuntimeError("database client crashed (%s: %s)"
                        % (type(e).__name__, str(e)[:120]))


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
        if self._conn is not None:
            self._conn.executescript(script); return self
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
    In cloud mode the underlying libsql client is cached per worker thread and
    every call is bounded by the watchdog."""
    def __init__(self, inner):
        self._inner = inner

    def _abandon(self):
        """Drop a poisoned client (never block on closing it) and open fresh."""
        old = self._inner
        _tls.inner = None
        try:
            # best-effort close in the watchdog so a closing hang can't block us
            _pool.submit(old.close)
        except Exception:
            pass
        self._inner = _fresh_after_connect()

    def _reconnect(self):
        old = self._inner
        # CRITICAL: clear the thread-local slot FIRST. _fresh_after_connect()
        # returns whatever sits there, and forgetting to clear it used to hand
        # back the very client we are about to close — the next statement then
        # ran on a CLOSED libsql connection, which panics inside the Rust
        # binding (a BaseException that escapes every handler as a platform
        # 500 with no JSON body).
        _tls.inner = None
        try:
            f = _pool.submit(old.close)
            try: f.result(timeout=2)
            except BaseException: pass   # even a panicking close must not block
        except Exception: pass
        self._inner = _fresh_after_connect()

    @staticmethod
    def _attempt(inner, sql, params, many):
        t = _translate(sql)
        if many:
            return inner.executemany(t, [tuple(p) for p in params])
        return inner.execute(t, tuple(params))

    def _run(self, sql, params, many, retried=False):
        import time
        # proactively replace an idle (possibly frozen/killed) client
        if time.time() - getattr(_tls, "last_used", 0.0) > 20:
            self._reconnect()
        _tls.last_used = time.time()
        try:
            f = _pool.submit(self._attempt, self._inner, sql, params, many)
            return f.result(timeout=DB_TIMEOUT)
        except FutTimeout:
            # dead socket / waking database: abandon and retry once, fast.
            # Only retry reads/idempotent statements; a write may have landed.
            if retried or not _is_safe_to_retry(sql):
                raise TimeoutError("database timed out (cold start?)")
            self._abandon()
            return self._run(sql, params, many, retried=True)
        except Exception:
            if not CLOUD or retried or not _is_safe_to_retry(sql): raise
            self._reconnect()
            return self._run(sql, params, many, retried=True)
        except BaseException as e:
            # Rust panic inside the binding (e.g. a statement on a poisoned
            # client). A BaseException would escape every `except Exception`
            # up the stack and kill the request with no JSON response, so it
            # is converted; a safe-to-retry statement gets ONE retry on a
            # guaranteed-fresh client.
            if not CLOUD or retried or not _is_safe_to_retry(sql):
                raise _client_crash(e)
            self._abandon()
            return self._run(sql, params, many, retried=True)

    def execute(self, sql, params=()):
        return _Cur(self._run(sql, params, False), self)

    def executemany(self, sql, seq):
        return _Cur(self._run(sql, list(seq), True), self)

    def executescript(self, script):
        # split simple DDL scripts into statements so the watchdog bounds them
        import time
        if not CLOUD:
            self._inner.executescript(script); return self
        if time.time() - getattr(_tls, "last_used", 0.0) > 20:
            self._reconnect()
        # Strip `--` comments BEFORE splitting: the DDL contains semicolons
        # inside comments (e.g. the recovery_codes one), which a naive
        # split(";") turns into an "incomplete input" statement.
        cleaned = re.sub(r"--[^\n]*", "", script)
        stmts = [s.strip() for s in cleaned.split(";") if s.strip()]
        for s in stmts:
            self.execute(s)
        return self

    def commit(self):
        import time
        try:
            f = _pool.submit(self._inner.commit)
            f.result(timeout=DB_TIMEOUT)
            _tls.last_used = time.time()
        except FutTimeout:
            if not CLOUD: raise
            self._abandon()
            # a fresh client autocommitted statements already; nothing to replay
        except Exception:
            if not CLOUD: raise
            self._reconnect()
        except BaseException as e:
            # Rust panic: the client is poisoned, replace it; a commit crash
            # must surface as a normal exception so the handler answers JSON.
            if not CLOUD: raise _client_crash(e)
            self._abandon()

    def rollback(self):
        try:
            f = _pool.submit(self._inner.rollback)
            f.result(timeout=DB_TIMEOUT)
        except Exception:
            if CLOUD: self._reconnect()
        except BaseException:
            if CLOUD: self._abandon()

    def close(self):
        # Cloud: keep the cached client alive for the next request on this
        # warm worker. Local sqlite connections close per request.
        if not CLOUD:
            try: self._inner.close()
            except Exception: pass
        else:
            try:
                if getattr(self._inner, "in_transaction", False):
                    self._inner.rollback()
            except BaseException:
                pass   # even a panicking client must not break request teardown

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
    """Open a database connection wrapper per request. In cloud mode the
    underlying client is created lazily and cached per worker thread; a stale
    one is replaced up front when the worker has been idle."""
    if not CLOUD:
        c = sqlite3.connect(DB_PATH, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute("PRAGMA journal_mode=WAL")
        c.execute("PRAGMA foreign_keys=ON")
        return c
    import time
    inner = getattr(_tls, "inner", None)
    last = getattr(_tls, "last_used", 0.0)
    if inner is not None and (time.time() - last) > 20:
        try:
            f = _pool.submit(inner.close)
            try: f.result(timeout=2)
            except BaseException: pass   # even a panicking close must not escape
        except Exception: pass
        inner = None
    if inner is None:
        inner = _fresh_after_connect()
    _tls.last_used = time.time()
    return _Conn(inner)
