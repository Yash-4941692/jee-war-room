#!/usr/bin/env python3
"""
JEE WAR ROOM — zero-dependency backend.
Python stdlib HTTP server + SQLite (real persistent server-side database).
Auth: PBKDF2 password hashing + opaque server-side session cookies.

SECURITY MODEL (see SECURITY.md for the full write-up)
  * passwords are one-way PBKDF2 hashes and can never be read back;
  * session tokens are stored only as SHA-256 digests, so a leaked database
    or backup dump is not a set of live logins;
  * self-service password reset uses ONE-TIME RECOVERY CODES. The friend code
    is a sharing secret (users hand it to up to 10 buddies) and is therefore
    never accepted as proof of identity;
  * login / signup / reset are throttled per username and per IP;
  * error messages do not reveal whether a username exists;
  * changing a password revokes every other session for that account.
"""
import http.server, socketserver, json, sqlite3, os, re, sys, time, math, random, string, secrets
import hashlib, hmac as hmac_mod
import threading
import traceback
import authsec
from datetime import date, datetime, timedelta
try:
    from datetime import timezone
except Exception:
    timezone = None
IST = timezone(__import__('datetime').timedelta(hours=5, minutes=30)) if timezone else None
def now_iso():
    # UTC, explicitly marked with Z so browsers convert to local (IST) time
    return datetime.now(__import__('datetime').timezone.utc).isoformat(timespec='seconds').replace('+00:00','Z')
from http.cookies import SimpleCookie
from urllib.parse import urlparse, parse_qs, urlencode
from collections import defaultdict
import syllabus as SYL

BASE = os.path.dirname(os.path.abspath(__file__))
STATIC = os.path.join(BASE, "public")
DB_PATH = os.path.join(BASE, "data", "warroom.db")
try:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
except OSError:
    pass  # read-only deploy bundles (Vercel) — unused when TURSO_DATABASE_URL is set

PORT = int(os.environ.get("PORT", "8080"))
SUBJECTS = SYL.SUBJECTS
TODAY = lambda: datetime.now(IST).date().isoformat()
DAY_F = lambda d: d.strftime("%Y-%m-%d")

# ---------------------------------------------------------------- database
import dbwrap
def db():
    return dbwrap.db()

_SCHEMA_SQL = r"""
    CREATE TABLE IF NOT EXISTS users(
      id INTEGER PRIMARY KEY, name TEXT UNIQUE NOT NULL,
      pass_hash TEXT NOT NULL, salt TEXT NOT NULL, code TEXT UNIQUE NOT NULL,
      partner_id INTEGER, exam_date TEXT, avatar_color TEXT, created_at TEXT,
      pw_changed_at TEXT);
    CREATE TABLE IF NOT EXISTS sessions(
      -- `token` holds SHA-256(bearer token), never the token itself
      token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT,
      expires_at REAL NOT NULL DEFAULT 0, user_agent TEXT);
    CREATE TABLE IF NOT EXISTS recovery_codes(
      -- one-time password-reset codes; only SHA-256(code) is stored
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
      code_hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
      used_at TEXT, used_ip TEXT);
    CREATE TABLE IF NOT EXISTS auth_throttle(
      key TEXT PRIMARY KEY, fails INTEGER NOT NULL DEFAULT 0,
      window_start REAL NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
      updated_at REAL NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS auth_events(
      id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
      user_id INTEGER, name TEXT, ip TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS settings(user_id INTEGER PRIMARY KEY, json TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS chapters(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT NOT NULL,
      name TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'not_started',
      hidden INTEGER NOT NULL DEFAULT 0, sort INTEGER NOT NULL DEFAULT 0, custom INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS activities(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, type TEXT NOT NULL,
      custom_key TEXT, subject TEXT, chapter TEXT, amount INTEGER DEFAULT 0,
      duration INTEGER DEFAULT 0, extra TEXT, note TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS targets(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, day TEXT NOT NULL, kind TEXT,
      subject TEXT, chapter TEXT, title TEXT NOT NULL, amount INTEGER, duration INTEGER,
      status TEXT NOT NULL DEFAULT 'open', created_at TEXT, completed_at TEXT);
    CREATE TABLE IF NOT EXISTS timers(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT, chapter TEXT,
      started REAL NOT NULL, running INTEGER, ended_at TEXT, logged INTEGER DEFAULT 0);
    CREATE TABLE IF NOT EXISTS mocks(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, test_type TEXT, total INTEGER DEFAULT 300,
      score REAL, phys REAL, chem REAL, math REAL, attempted INTEGER, correct INTEGER,
      incorrect INTEGER, day TEXT NOT NULL, note TEXT, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS errors(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, subject TEXT, chapter TEXT,
      etype TEXT, qno TEXT, note TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS xp_events(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, amount INTEGER NOT NULL,
      reason TEXT, ref_type TEXT, ref_id TEXT, day TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS snapshots(
      user_id INTEGER NOT NULL, day TEXT NOT NULL, score REAL NOT NULL, air INTEGER NOT NULL,
      PRIMARY KEY(user_id, day));
    CREATE TABLE IF NOT EXISTS nonces(
      nonce TEXT PRIMARY KEY, user_id INTEGER, created_at REAL, result TEXT);
    CREATE TABLE IF NOT EXISTS app_settings(id INTEGER PRIMARY KEY CHECK (id=1), json TEXT NOT NULL DEFAULT '{}');
    CREATE TABLE IF NOT EXISTS friendships(
      user_id INTEGER NOT NULL, friend_id INTEGER NOT NULL, created_at TEXT,
      PRIMARY KEY(user_id, friend_id));
    CREATE TABLE IF NOT EXISTS messages(
      id INTEGER PRIMARY KEY, sender INTEGER NOT NULL, recipient INTEGER NOT NULL,
      body TEXT NOT NULL, created_at TEXT NOT NULL, read_at TEXT);
    CREATE TABLE IF NOT EXISTS announcements(
      id INTEGER PRIMARY KEY, body TEXT NOT NULL, created_at TEXT NOT NULL);
    CREATE TABLE IF NOT EXISTS reports(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, body TEXT NOT NULL,
      created_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0);
    CREATE INDEX IF NOT EXISTS ix_msg_pair ON messages(recipient, sender, id);
    CREATE INDEX IF NOT EXISTS ix_act_day ON activities(user_id, day);
    CREATE INDEX IF NOT EXISTS ix_t_day ON targets(user_id, day);
    CREATE INDEX IF NOT EXISTS ix_mock_day ON mocks(user_id, day);
    CREATE INDEX IF NOT EXISTS ix_err_day ON errors(user_id, day);
    CREATE INDEX IF NOT EXISTS ix_rc_user ON recovery_codes(user_id, used_at);
    CREATE INDEX IF NOT EXISTS ix_sess_user ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS ix_ae_ts ON auth_events(ts);
    """

# DDL for the tables added by the auth-hardening pass. Kept apart from
# _SCHEMA_SQL so an already-warm cloud database only pays for what is missing.
_AUTH_DDL = r"""
    CREATE TABLE IF NOT EXISTS recovery_codes(
      id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL,
      code_hash TEXT UNIQUE NOT NULL, created_at TEXT NOT NULL,
      used_at TEXT, used_ip TEXT);
    CREATE TABLE IF NOT EXISTS auth_throttle(
      key TEXT PRIMARY KEY, fails INTEGER NOT NULL DEFAULT 0,
      window_start REAL NOT NULL DEFAULT 0, locked_until REAL NOT NULL DEFAULT 0,
      updated_at REAL NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS auth_events(
      id INTEGER PRIMARY KEY, ts TEXT NOT NULL, kind TEXT NOT NULL,
      user_id INTEGER, name TEXT, ip TEXT, detail TEXT);
    CREATE TABLE IF NOT EXISTS schema_migrations(
      name TEXT PRIMARY KEY, applied_at TEXT NOT NULL, detail TEXT);
    CREATE INDEX IF NOT EXISTS ix_rc_user ON recovery_codes(user_id, used_at);
    CREATE INDEX IF NOT EXISTS ix_sess_user ON sessions(user_id);
    CREATE INDEX IF NOT EXISTS ix_ae_ts ON auth_events(ts);
    """


# One-time migration that must NOT run from a Vercel *preview* deployment:
# in this project preview and production share the same Turso database, so a
# preview cold start would otherwise perform a one-way auth migration against
# live data (signing every real user out before the change is even merged).
MIGRATION_LEGACY_SESSION_PURGE = "purge_legacy_plaintext_sessions"


def destructive_migrations_allowed():
    """False on Vercel preview deployments (override: ALLOW_PREVIEW_MIGRATION=1)."""
    if os.environ.get("ALLOW_PREVIEW_MIGRATION", "").strip().lower() in ("1", "true", "yes"):
        return True
    return (os.environ.get("VERCEL_ENV") or "").strip().lower() != "preview"


def _migration_done(c, name):
    try:
        return c.execute("SELECT 1 FROM schema_migrations WHERE name=?", (name,)).fetchone() is not None
    except Exception:
        return False


def _record_migration(c, name, detail=""):
    try:
        c.execute("""INSERT OR IGNORE INTO schema_migrations(name,applied_at,detail)
                     VALUES(?,?,?)""", (name, now_iso(), str(detail)[:200]))
        c.commit()
    except Exception:
        pass


def legacy_sessions_remaining(c):
    """Sessions that only the PRE-hardening code could have written.

    The hardened code always stores a real expiry, so `expires_at=0` marks a
    legacy plaintext-token row. Keying the purge on that (rather than on "did I
    just add the column?") makes it idempotent: it can never sign out somebody
    who logged in after the upgrade, and a preview that added the column cannot
    cause production to skip the purge.
    """
    try:
        r = c.execute("SELECT COUNT(*) FROM sessions WHERE expires_at IS NULL OR expires_at=0").fetchone()
        return int(r[0] or 0)
    except Exception:
        return -1


def pending_migrations(c):
    """Destructive migrations still owed — surfaced via /healthz?detail=1."""
    out = []
    if not _migration_done(c, MIGRATION_LEGACY_SESSION_PURGE):
        out.append({"name": MIGRATION_LEGACY_SESSION_PURGE,
                    "legacySessions": legacy_sessions_remaining(c),
                    "blockedBy": None if destructive_migrations_allowed() else "VERCEL_ENV=preview"})
    return out


def _auth_migrate(c):
    """Idempotent upgrade of an existing database to the hardened auth schema.

    Additive steps (tables, indexes, columns) always run — the code needs them.
    The single destructive step is gated and recorded, see above.
    """
    c.executescript(_AUTH_DDL)
    if "pw_changed_at" not in _table_cols(c, "users"):
        c.execute("ALTER TABLE users ADD COLUMN pw_changed_at TEXT")
    scols = _table_cols(c, "sessions")
    if "expires_at" not in scols:
        c.execute("ALTER TABLE sessions ADD COLUMN expires_at REAL NOT NULL DEFAULT 0")
    if "user_agent" not in scols:
        c.execute("ALTER TABLE sessions ADD COLUMN user_agent TEXT")
    c.commit()
    # --- destructive, one-time, security-critical ---
    # Every token issued before this migration was stored in PLAINTEXT, and
    # those tokens were committed to this repository inside
    # backups/warroom-dump.sql: live logins for anyone who can read the repo.
    # They are destroyed rather than migrated, so each user signs in once more.
    if _migration_done(c, MIGRATION_LEGACY_SESSION_PURGE):
        return
    if not destructive_migrations_allowed():
        return
    n = legacy_sessions_remaining(c)
    if n > 0:
        c.execute("DELETE FROM sessions WHERE expires_at IS NULL OR expires_at=0")
        c.commit()
    _record_migration(c, MIGRATION_LEGACY_SESSION_PURGE,
                      "destroyed %d plaintext-token session(s)" % max(n, 0))


def _table_cols(c, table):
    """Column names of a table, in both local sqlite and cloud libSQL modes."""
    if not re.fullmatch(r"[A-Za-z_]+", table or ""): return set()
    try:
        return {r[1] for r in c.execute("PRAGMA table_info(%s)" % table).fetchall()}
    except Exception:
        return set()


# ---------------------------------------------------------------- schema tolerance
# Cached answers to "does this database already have the hardened schema?".
#   None  = unknown yet
#   True  = full schema present (normal case after init_db())
#   False = a request already hit "no such table/column" -> use the legacy shape
# Migrations only ever ADD columns/tables, so once detected the answer is
# stable for the life of the worker; init_db() resets the cache to full.
_AUTH_COLS_FULL = [None]      # sessions.expires_at + users.pw_changed_at (+ role)
_RC_TABLE_OK = [None]         # recovery_codes table present


def _reset_schema_cache(full=True):
    """Called by init_db() once the schema is known-good."""
    _AUTH_COLS_FULL[0] = True if full else None
    _RC_TABLE_OK[0] = True if full else None


def _missing_schema(e):
    """True when an error means a table/column does NOT exist.

    Anything else is a TRANSIENT database failure and must surface as a 500:
    turning it into a 401/no-user result would make app.js drop the stored
    token and silently sign out every user on a passing Turso blip.
    Degrade on missing schema, never deny on a blip.
    """
    m = str(e).lower()
    return "no such table" in m or "no such column" in m


# Boot/migration outcome, filled by the serverless entry point (api/index.py)
# and by `python3 server.py`. Surfaced at GET /healthz?detail=1 so a failed
# or half-applied migration is visible to the operator without log access —
# it used to be swallowed by a bare `except` at import time.
BOOT_INFO = {"ok": None, "error": None}


def init_db():
    c = db()
    if dbwrap.CLOUD:
        # Fast path: both the newest table AND the record of the one destructive
        # migration must be present. Checking only the table would let a Vercel
        # preview (which shares this database but skips destructive steps) make
        # production return early and never purge the leaked plaintext tokens.
        try:
            c.execute("SELECT 1 FROM recovery_codes LIMIT 1").fetchone()
            if c.execute("SELECT 1 FROM schema_migrations WHERE name=?",
                         (MIGRATION_LEGACY_SESSION_PURGE,)).fetchone():
                c.close()
                _reset_schema_cache(full=True)
                return
        except Exception:
            pass
        have = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
        if "announcements" not in have or "users" not in have:
            c.executescript(_SCHEMA_SQL)
        elif "reports" not in have:
            c.execute("""CREATE TABLE IF NOT EXISTS reports(
              id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, body TEXT NOT NULL,
              created_at TEXT NOT NULL, resolved INTEGER NOT NULL DEFAULT 0)""")
        msg_cols = {r[0] for r in c.execute(
            "SELECT name FROM pragma_table_info('messages')").fetchall()}
        if "hidden" not in msg_cols:
            c.execute("ALTER TABLE messages ADD COLUMN hidden TEXT NOT NULL DEFAULT ''")
        user_cols = _table_cols(c, "users")
        if "role" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            c.execute("UPDATE users SET role='admin' WHERE id=(SELECT MIN(id) FROM users)")
        ch_cols = {r[0] for r in c.execute(
            "SELECT name FROM pragma_table_info('chapters')").fetchall()}
        if "lectures_total" not in ch_cols:
            c.execute("ALTER TABLE chapters ADD COLUMN lectures_total INTEGER NOT NULL DEFAULT 0")
        if "lectures_done" not in ch_cols:
            c.execute("ALTER TABLE chapters ADD COLUMN lectures_done INTEGER NOT NULL DEFAULT 0")
        _auth_migrate(c)
        c.commit(); c.close()
        _reset_schema_cache(full=True)
        return
    c.executescript(_SCHEMA_SQL)
    # ---- lightweight migrations ----
    def cols(tbl):
        return {r[1] for r in c.execute(f"PRAGMA table_info({tbl})")}
    if "role" not in cols("users"):
        c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
        c.execute("UPDATE users SET role='admin' WHERE id=(SELECT MIN(id) FROM users)")
    if "lectures_total" not in cols("chapters"):
        c.execute("ALTER TABLE chapters ADD COLUMN lectures_total INTEGER NOT NULL DEFAULT 0")
    if "lectures_done" not in cols("chapters"):
        c.execute("ALTER TABLE chapters ADD COLUMN lectures_done INTEGER NOT NULL DEFAULT 0")
    if "hidden" not in cols("messages"):
        # comma-separated user ids who cleared this message from their own chat view
        c.execute("ALTER TABLE messages ADD COLUMN hidden TEXT NOT NULL DEFAULT ''")
    _auth_migrate(c)
    c.commit(); c.close()
    _reset_schema_cache(full=True)

def seed_chapters(c, uid):
    """Insert the 61 default chapters for a user in ONE network round-trip
    (single multi-row INSERT — 61 separate INSERTs took ~20s over HTTPS)."""
    vals, params, n = [], [], 0
    for subj in SUBJECTS:
        for ch in SYL.CHAPTERS[subj]:
            vals.append("(?,?,?,?,?)"); params += [uid, subj, ch, "not_started", n]; n += 1
    c.execute("INSERT INTO chapters(user_id,subject,name,status,sort) VALUES " + ",".join(vals), params)

def msg_visible(uid):
    """SQL fragment: messages this user hasn't cleared from their own view."""
    return "instr(','||COALESCE(hidden,'')||',', ',%d,')=0" % int(uid)

MAX_FRIENDS = 10

def friend_ids(c, uid):
    return [r["friend_id"] for r in c.execute(
        "SELECT friend_id FROM friendships WHERE user_id=? ORDER BY created_at", (uid,))]

def are_friends(c, a, b):
    return c.execute("SELECT 1 FROM friendships WHERE user_id=? AND friend_id=?", (a, b)).fetchone() is not None

# ---------------------------------------------------------------- helpers
def rowdict(r): return dict(r) if r else None

def default_settings():
    import copy
    return {
        "xp": dict(SYL.DEFAULT_XP),
        "weights": dict(SYL.DEFAULT_WEIGHTS),
        "sharing": dict(SYL.DEFAULT_SHARING),
        "cards": [dict(x) for x in SYL.DASHBOARD_CARDS],
        "activities": [dict(x) for x in SYL.ACTIVITY_TYPES],
        "customActivities": [],
        "metrics": [],
        "templates": copy.deepcopy(SYL.TARGET_TEMPLATES),
        "streakThreshold": 70,
        "airEMA": 0.88,
        "bestStreak": 0,
        "sweepDay": "",
        "seenAnnouncements": [],
    }

# Keys controlled by the admin for the whole app (per-user copies are ignored for these)
GLOBAL_KEYS = ("xp", "weights", "streakThreshold", "airEMA")

def admin_settings(c):
    d = default_settings()
    g = {"xp": dict(d["xp"]), "weights": dict(d["weights"]),
         "streakThreshold": d["streakThreshold"], "airEMA": d["airEMA"],
         "activitiesEnabled": {a["key"]: a.get("enabled", True) for a in d["activities"]}}
    r = c.execute("SELECT json FROM app_settings WHERE id=1").fetchone()
    if r:
        saved = json.loads(r["json"])
        for k in ("xp", "weights"):
            if isinstance(saved.get(k), dict): g[k].update(saved[k])
        for k in ("streakThreshold", "airEMA"):
            if k in saved: g[k] = saved[k]
        if isinstance(saved.get("activitiesEnabled"), dict):
            g["activitiesEnabled"].update(saved["activitiesEnabled"])
    g["airEMA"] = clamp(float(g["airEMA"]), 0.5, 0.97)
    g["streakThreshold"] = clamp(int(g["streakThreshold"]), 10, 100)
    return g

def save_admin_settings(c, g):
    c.execute("INSERT INTO app_settings(id,json) VALUES(1,?) ON CONFLICT(id) DO UPDATE SET json=excluded.json",
              (json.dumps(g),)); c.commit()

def get_settings(c, uid):
    r = c.execute("SELECT json FROM settings WHERE user_id=?", (uid,)).fetchone()
    if r:
        s = json.loads(r["json"])
    else:
        s = default_settings()
    # fill any newly introduced defaults
    d = default_settings()
    for k, v in d.items():
        s.setdefault(k, v)
    # admin/global rulebook overrides for everyone
    g = admin_settings(c)
    s["xp"] = dict(g["xp"]); s["weights"] = dict(g["weights"])
    s["streakThreshold"] = g["streakThreshold"]; s["airEMA"] = g["airEMA"]
    for a in s["activities"]:
        if a["key"] in g["activitiesEnabled"]:
            a["enabled"] = bool(g["activitiesEnabled"][a["key"]])
    return s

def save_settings(c, uid, s):
    c.execute("INSERT INTO settings(user_id,json) VALUES(?,?) ON CONFLICT(user_id) DO UPDATE SET json=excluded.json",
              (uid, json.dumps(s)))

# Password hashing + normalization live in authsec so every flow (signup,
# login, self-reset, admin-reset, change-password) uses the IDENTICAL rule.
# Previously three flows trimmed surrounding whitespace and two did not, which
# is how an account could end up with a password its own owner cannot type.
hash_pw = authsec.hash_pw
normalize_pw = authsec.normalize_pw
new_salt = authsec.new_salt

# Fixed dummy credential used to equalize response time when a username does
# not exist, so an attacker cannot enumerate accounts by measuring latency.
_DUMMY_SALT = "0" * 32
_DUMMY_HASH = authsec.hash_pw("timing-equalizer-not-a-password", _DUMMY_SALT)


def gen_code(n=6):
    """Friend codes — for buddy connections ONLY, never a login credential.

    Uses a CSPRNG (it used to be `random.choices`, whose Mersenne Twister state
    is recoverable from a handful of outputs) and an unambiguous alphabet.
    """
    while True:
        yield authsec.gen_friend_code(n)


# ---------------------------------------------------------------- sessions
def _make_session_row(c, uid, user_agent=None):
    """Create a session; returns the PLAINTEXT token for the client.

    Only SHA-256(token) is stored, so a database leak or backup dump is not a
    pile of working logins. Sessions also carry an absolute expiry.
    """
    token = authsec.new_token()
    c.execute("""INSERT INTO sessions(token,user_id,created_at,expires_at,user_agent)
                 VALUES(?,?,?,?,?)""",
              (authsec.hash_token(token), uid, now_iso(),
               time.time() + authsec.SESSION_TTL_SECONDS,
               (str(user_agent)[:200] if user_agent else None)))
    c.commit()
    return token


def revoke_sessions(c, uid, keep_token=None, commit=True):
    """Kill every session for a user (optionally sparing the current one).

    Called on password reset, password change and admin password set: an
    attacker who obtained a session before the change does not keep it after.
    """
    keep = authsec.hash_token(keep_token) if keep_token else None
    if keep:
        c.execute("DELETE FROM sessions WHERE user_id=? AND token!=?", (uid, keep))
    else:
        c.execute("DELETE FROM sessions WHERE user_id=?", (uid,))
    if commit: c.commit()


def purge_expired_sessions(c):
    """Cheap opportunistic cleanup of dead sessions."""
    try:
        c.execute("DELETE FROM sessions WHERE expires_at>0 AND expires_at<?", (time.time(),))
        c.commit()
    except Exception:
        pass


# ---------------------------------------------------------------- recovery codes
def issue_recovery_codes(c, uid, ip=None, commit=True, log=True):
    """Generate a fresh set of recovery codes; returns the PLAINTEXT list.

    Old unused codes are discarded so a lost printout can never be replayed
    later. This is the ONLY moment the plaintext exists — the database keeps
    SHA-256 digests, so nobody (not even the admin) can read them back.

    All 8 codes are written in ONE multi-row INSERT: on Vercel every statement
    is a separate HTTPS round trip to Turso inside a 7 s watchdog, and 8
    sequential INSERTs on a waking database were enough on their own to blow
    the function's 30 s budget (same lesson as signup's 61 chapter rows — see
    STATUS.md). `log=False` lets login/signup fold the audit event into their
    own batched log_events() call, saving yet another round trip.

    Callers on the login/signup path treat a failure here as NON-FATAL: codes
    are a convenience the user can regenerate in Settings → Account Security.
    """
    codes = authsec.gen_recovery_codes()
    now = now_iso()
    c.execute("DELETE FROM recovery_codes WHERE user_id=? AND used_at IS NULL", (uid,))
    vals, params = [], []
    for code in codes:
        vals.append("(?,?,?)")
        params.extend([uid, authsec.hash_code(code), now])
    c.execute("""INSERT OR IGNORE INTO recovery_codes(user_id,code_hash,created_at) VALUES """
              + ",".join(vals), params)
    if commit: c.commit()
    _RC_TABLE_OK[0] = True          # the writes above prove the table exists
    if log:
        authsec.log_event(c, "recovery_codes_issued", user_id=uid, ip=ip,
                          detail="%d codes" % len(codes), commit=commit)
    return codes


def consume_recovery_code(c, uid, code, ip=None):
    """Use one unused recovery code. Returns True when it was valid."""
    if _RC_TABLE_OK[0] is False:
        return False              # schema not migrated yet: no code can exist
    digest = authsec.hash_code(code)
    try:
        row = c.execute("""SELECT id FROM recovery_codes
                           WHERE user_id=? AND code_hash=? AND used_at IS NULL""",
                        (uid, digest)).fetchone()
    except Exception as e:
        if _RC_TABLE_OK[0] is not True and _missing_schema(e):
            _RC_TABLE_OK[0] = False     # degrade: treat as "no such code"
            return False
        raise                     # transient DB failure: 500, not a fake reject
    _RC_TABLE_OK[0] = True
    if not row: return False
    c.execute("UPDATE recovery_codes SET used_at=?, used_ip=? WHERE id=?",
              (now_iso(), (str(ip)[:64] if ip else None), row["id"]))
    c.commit()
    return True


def recovery_status(c, uid):
    """What the user is allowed to know about their own codes (never the codes).

    `total` is the size of the CURRENT batch, not of all history — used codes
    from superseded batches would otherwise make the counter look wrong.

    Schema-tolerant: on a database whose migration has not landed yet the
    answer degrades to "no codes" instead of raising a 500 on every request.
    Transient DB failures still raise — they must surface as a 500, never as
    a result that signs somebody out or blocks a login.
    """
    empty = {"total": 0, "remaining": 0, "generatedAt": None, "lastUsedAt": None}
    if _RC_TABLE_OK[0] is False:
        return empty
    try:
        r = c.execute("""SELECT
                (SELECT MAX(created_at) FROM recovery_codes WHERE user_id=?) generated_at,
                (SELECT MAX(used_at) FROM recovery_codes WHERE user_id=?) last_used_at,
                (SELECT COUNT(*) FROM recovery_codes WHERE user_id=? AND used_at IS NULL) remaining,
                (SELECT COUNT(*) FROM recovery_codes WHERE user_id=?
                  AND created_at=(SELECT MAX(created_at) FROM recovery_codes WHERE user_id=?)) total
            """, (uid, uid, uid, uid, uid)).fetchone()
    except Exception as e:
        if _RC_TABLE_OK[0] is not True and _missing_schema(e):
            _RC_TABLE_OK[0] = False
            return empty
        raise
    _RC_TABLE_OK[0] = True
    if not r: return empty
    return {"total": int(r["total"] or 0), "remaining": int(r["remaining"] or 0),
            "generatedAt": r["generated_at"], "lastUsedAt": r["last_used_at"]}


def active_sessions(c, uid):
    rows = c.execute("""SELECT token, created_at, expires_at, user_agent FROM sessions
                        WHERE user_id=? ORDER BY created_at DESC LIMIT 20""", (uid,)).fetchall()
    out = []
    for r in rows:
        out.append({"id": (r["token"] or "")[:12], "createdAt": r["created_at"],
                    "expiresAt": r["expires_at"], "userAgent": r["user_agent"]})
    return out

def ind(n):
    """Indian-style number grouping: 600000 -> 6,00,000"""
    neg = n < 0; n = abs(int(round(n)))
    s = str(n)
    if len(s) <= 3: out = s
    else:
        out = s[-3:]; s = s[:-3]
        while len(s) > 2: out = s[-2:] + "," + out; s = s[:-2]
        if s: out = s + "," + out
    return ("-" if neg else "") + out

def clamp(v, lo, hi): return max(lo, min(hi, v))

def valid_day(d):
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", d or ""))

# ---------------------------------------------------------------- scoring
ACT_Q_TYPES = ("pyq", "dpp", "homework")
STUDY_TYPES = ("study", "revision")  # minutes sources (lecture has no duration)

def active_days_set(c, uid, start, end):
    rows = c.execute("""SELECT DISTINCT day FROM activities
        WHERE user_id=? AND day BETWEEN ? AND ? AND type!='distraction'""", (uid, start, end)).fetchall()
    days = {r["day"] for r in rows}
    rows = c.execute("""SELECT DISTINCT day FROM targets
        WHERE user_id=? AND day BETWEEN ? AND ? AND status IN ('done','partial')""", (uid, start, end)).fetchall()
    days |= {r["day"] for r in rows}
    return days

def raw_score(c, uid, d, s):
    start = DAY_F(d - timedelta(days=13))
    end = DAY_F(d)
    w = s["weights"]; wsum = sum(max(0, x) for x in w.values()) or 1
    # consistency
    act = len(active_days_set(c, uid, start, end))
    C = act / 14.0
    # targets
    tr = c.execute("SELECT status, COUNT(*) n FROM targets WHERE user_id=? AND day BETWEEN ? AND ? GROUP BY status",
                   (uid, start, end)).fetchall()
    tm = {r["status"]: r["n"] for r in tr}
    planned = sum(tm.values())
    donew = tm.get("done", 0) + 0.5 * tm.get("partial", 0)
    T = (donew / planned) if planned else 0.0
    # practice, revision, distraction (grouped single query over 14d)
    q = 0; rm = 0; dm = 0
    for r in c.execute("SELECT type, SUM(amount) sa, SUM(duration) sd FROM activities WHERE user_id=? AND day BETWEEN ? AND ? GROUP BY type",
                       (uid, start, end)).fetchall():
        tp = r["type"]
        if tp in ('pyq', 'dpp', 'homework'): q += (r["sa"] or 0)
        elif tp == 'revision': rm += (r["sd"] or 0)
        elif tp == 'distraction': dm += (r["sd"] or 0)
    P = min(1.0, q / 400.0)
    R = min(1.0, rm / 300.0)
    # mocks
    m = c.execute("SELECT * FROM mocks WHERE user_id=? AND day<=? ORDER BY day DESC, id DESC LIMIT 1",
                  (uid, end)).fetchone()
    if m:
        total = m["total"] or 300
        score = m["score"]
        if score is None and m["phys"] is not None:
            score = (m["phys"] or 0) + (m["chem"] or 0) + (m["math"] or 0)
        pct = (score or 0) / total
        age = (d - date.fromisoformat(m["day"])).days
        if age <= 21: M = pct
        elif age <= 60: M = pct * 0.6
        else: M = 0.25
    else:
        M = 0.0  # no mock taken yet — no free credit, fresh users stay at AIR 600,000
    M = clamp(M, 0, 1)
    # penalties
    penalty = min(10, dm / 60.0) + min(5, tm.get("missed", 0) * 0.5)
    comps = {"consistency": C, "targets": T, "practice": P, "revision": R, "mock": M}
    val = 100.0 * sum(max(0, w[k]) * comps[k] for k in comps) / wsum - penalty
    val = clamp(val, 0, 100)
    return round(val, 2), {k: round(v, 3) for k, v in comps.items()}, {"questions": q, "revMin": rm, "distractMin": dm, "activeDays": act, "planned": planned}

def air_for(score):
    return clamp(int(round(600000 * math.exp(-0.064 * score))), 1, 600000)

def ensure_snapshots(c, uid, s, force_today=False):
    """Backfill deterministic daily score/AIR snapshots up to today."""
    u = c.execute("SELECT created_at FROM users WHERE id=?", (uid,)).fetchone()
    if not u: return
    first = date.fromisoformat(u["created_at"][:10])
    today_d = datetime.now(IST).date()
    last = c.execute("SELECT MAX(day) d FROM snapshots WHERE user_id=?", (uid,)).fetchone()["d"]
    prev_score = 0.0
    if last:
        if last == DAY_F(today_d):
            if not force_today:
                return
            y = c.execute("SELECT score FROM snapshots WHERE user_id=? AND day=?",
                          (uid, DAY_F(today_d - timedelta(days=1)))).fetchone()
            prev_score = y["score"] if y else 0.0
            cur = today_d
        else:
            prev_score = c.execute("SELECT score FROM snapshots WHERE user_id=? AND day=?", (uid, last)).fetchone()["score"]
            cur = date.fromisoformat(last) + timedelta(days=1)
    else:
        cur = first
    today = datetime.now(IST).date()
    # cap backfill to last 120 days
    if cur < today - timedelta(days=120):
        cur = today - timedelta(days=120)
        prev_score = 0.0
    ema = clamp(s.get("airEMA", 0.88), 0.5, 0.97)
    guard = 0
    wrote = False
    while cur <= today and guard < 140:
        raw, _, _ = raw_score(c, uid, cur, s)
        sc = round(ema * prev_score + (1 - ema) * raw, 2)
        c.execute("INSERT OR REPLACE INTO snapshots(user_id,day,score,air) VALUES(?,?,?,?)",
                  (uid, DAY_F(cur), sc, air_for(sc)))
        prev_score = sc; cur += timedelta(days=1); guard += 1
        wrote = True
    if wrote:
        c.commit()

def level_info(xp):
    L = 1
    while xp >= 25 * (L + 1) * L:
        L += 1
    need_now = 25 * L * (L - 1)
    need_next = 25 * (L + 1) * L
    title = "STARTING LINE"
    if L >= 20: title = "INDOMITABLE"
    elif L >= 10: title = "RELENTLESS"
    elif L >= 5: title = "GRINDER"
    return {"level": L, "title": title, "xp": xp, "into": xp - need_now, "span": need_next - need_now, "nextAt": need_next}

def xp_total(c, uid):
    return c.execute("SELECT COALESCE(SUM(amount),0) v FROM xp_events WHERE user_id=?", (uid,)).fetchone()["v"]

def streak_info(c, uid, s):
    """Consecutive active study days (ending today or yesterday).
    A day counts towards streak if the student logged study activities,
    focus sessions, mocks, or completed targets."""
    thr = s.get("streakThreshold", 70) / 100.0
    since = DAY_F(datetime.now(IST).date() - timedelta(days=120))
    # 1. Days with study activities (lectures, revision, pyqs, dpp, homework, study)
    rows_act = c.execute("""
        SELECT DISTINCT day FROM activities 
        WHERE user_id=? AND day>=? AND type!='distraction'
    """, (uid, since)).fetchall()
    active_days = {r["day"] if hasattr(r, "keys") else r[0] for r in rows_act}

    # 2. Days with mock tests
    rows_mock = c.execute("SELECT DISTINCT day FROM mocks WHERE user_id=? AND day>=?", (uid, since)).fetchall()
    active_days |= {r["day"] if hasattr(r, "keys") else r[0] for r in rows_mock}

    # 3. Days with targets completed (status done/partial, or meeting streakThreshold)
    rows_tar = c.execute("""
        SELECT day, status FROM targets 
        WHERE user_id=? AND day>=? ORDER BY day DESC LIMIT 400
    """, (uid, since)).fetchall()
    t_days = defaultdict(lambda: [0, 0.0])
    for r in rows_tar:
        day = r["day"] if hasattr(r, "keys") else r[0]
        st = r["status"] if hasattr(r, "keys") else r[1]
        t_days[day][0] += 1
        if st == "done":
            t_days[day][1] += 1
            active_days.add(day)
        elif st == "partial":
            t_days[day][1] += 0.5
            active_days.add(day)

    for day, (tot, done) in t_days.items():
        if tot > 0 and (done / tot) >= thr:
            active_days.add(day)

    today_dt = datetime.now(IST).date()
    today_str = DAY_F(today_dt)
    yest_str = DAY_F(today_dt - timedelta(days=1))

    streak = 0
    if today_str in active_days:
        cur = today_dt
    elif yest_str in active_days:
        cur = today_dt - timedelta(days=1)
    else:
        cur = None

    while cur is not None and DAY_F(cur) in active_days:
        streak += 1
        cur -= timedelta(days=1)

    best = max(s.get("bestStreak", 0), streak)
    if best != s.get("bestStreak", 0):
        s["bestStreak"] = best
    return {"current": streak, "best": best}

def sweep_targets(c, uid, s):
    """Once per day: mark leftover old open targets as missed, apply XP penalty."""
    today = datetime.now(IST).date(); marker = s.get("sweepDay", "")
    if marker == DAY_F(today): return
    xp = s["xp"]
    d0 = date.fromisoformat(marker) + timedelta(days=1) if marker else today - timedelta(days=60)
    if d0 >= today:
        s["sweepDay"] = DAY_F(today); return
    n = c.execute("""UPDATE targets SET status='missed'
        WHERE user_id=? AND status='open' AND day BETWEEN ? AND ?""",
        (uid, DAY_F(d0), DAY_F(today - timedelta(days=1)))).rowcount
    if n:
        add_xp(c, uid, xp["missed"] * n, f"{n} missed target(s) auto-marked", "sweep", DAY_F(today), s, commit=False)
    s["sweepDay"] = DAY_F(today)

def add_xp(c, uid, amount, reason, ref_type, ref_id, s, commit=True):
    if not amount: return
    day = TODAY()
    exists = c.execute("SELECT 1 FROM xp_events WHERE user_id=? AND ref_type=? AND ref_id=? AND reason=?",
                       (uid, ref_type, str(ref_id), reason)).fetchone()
    if exists: return
    c.execute("INSERT INTO xp_events(user_id,amount,reason,ref_type,ref_id,day,created_at) VALUES(?,?,?,?,?,?,?)",
              (uid, amount, reason, ref_type, str(ref_id), day, now_iso()))
    if commit: c.commit()

def chapter_names(c, uid):
    return {r["name"] for r in c.execute("SELECT name FROM chapters WHERE user_id=? AND hidden=0", (uid,))}

def touch_chapter(c, uid, subject, chapter):
    if subject in SUBJECTS and chapter:
        c.execute("UPDATE chapters SET status='in_progress' WHERE user_id=? AND subject=? AND name=? AND status='not_started'",
                  (uid, subject, chapter))

def today_summary(c, uid, s):
    t = TODAY()
    day_min = 0; q = 0; distract = 0; custom = {}
    for r in c.execute("SELECT type,custom_key,SUM(amount) am,SUM(duration) dm,COUNT(*) n FROM activities WHERE user_id=? AND day=? GROUP BY type,custom_key", (uid, t)):
        tp = r["type"]; am = r["am"] or 0; dm = r["dm"] or 0; n = r["n"]
        if tp in ("study", "revision"): day_min += dm
        if tp in ("pyq", "dpp", "homework"): q += am
        if tp == "distraction": distract += dm
        if tp == "metric" or r["custom_key"]:
            custom[r["custom_key"] or ("__" + tp)] = {"amount": am, "duration": dm, "logs": n}
    tr = c.execute("SELECT status, COUNT(*) n FROM targets WHERE user_id=? AND day=? GROUP BY status", (uid, t)).fetchall()
    tm = {r["status"]: r["n"] for r in tr}
    planned = sum(tm.values()); done = tm.get("done", 0) + 0.5 * tm.get("partial", 0)
    exe = round(100 * done / planned) if planned else 0
    mocks_n = c.execute("SELECT COUNT(*) n FROM mocks WHERE user_id=? AND day=?", (uid, t)).fetchone()["n"]
    errs_n = c.execute("SELECT COUNT(*) n FROM errors WHERE user_id=? AND day=?", (uid, t)).fetchone()["n"]
    return {"minutes": day_min, "questions": q, "distractionMin": distract,
            "planned": planned, "done": tm.get("done", 0), "partial": tm.get("partial", 0),
            "missed": tm.get("missed", 0), "execution": exe, "mocks": mocks_n, "errors": errs_n,
            "custom": custom}

# ---------------------------------------------------------------- handler
class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "JEEWR/1.0"
    def log_message(self, *a): pass

    # Endpoints that authenticate a brand-new (token-less) device. `_auth()`
    # is skipped for them entirely: with no token it could only return None,
    # but on a half-migrated schema it could also fail — a pointless risk and
    # a wasted round trip on the exact requests a locked-out user depends on.
    AUTH_FREE_PATHS = frozenset(("/api/auth/signup", "/api/auth/login",
                                 "/api/auth/reset-password"))

    def send_error(self, code, message=None, explain=None):
        """JSON instead of the stdlib HTML error page.

        Even low-level failures (malformed request line, unsupported method,
        oversized URI) must answer with a machine-readable body: a non-JSON
        error is exactly what turns into the bare "Request failed (500)" toast
        in the frontend, because api() only finds an `error` field in JSON.
        """
        self.close_connection = True
        try:
            self._err(str(message or explain or "Request error")[:200], code)
        except Exception:
            pass    # the socket is already gone; nothing to send

    # ---------------------------------------------------- hardening helpers
    def _security_headers(self):
        """Defence-in-depth headers on EVERY response (API and static).

        `frame-ancestors` defaults to 'self'; set FRAME_ANCESTORS to add
        origins that may embed the app (e.g. a preview/tunnel host).
        Inline scripts/styles stay allowed because the frontend is a single
        hand-written file full of onclick= handlers — see SECURITY.md.
        """
        fa = os.environ.get("FRAME_ANCESTORS", "").strip() or "'self'"
        hdrs = [
            ("Content-Security-Policy",
             "default-src 'self'; "
             "script-src 'self' 'unsafe-inline'; "
             "style-src 'self' 'unsafe-inline'; "
             "img-src 'self' data: blob:; font-src 'self' data:; "
             "media-src 'self' data: blob:; "
             "connect-src 'self'; form-action 'self'; "
             "object-src 'none'; base-uri 'self'; "
             "frame-ancestors %s" % fa),
            ("X-Content-Type-Options", "nosniff"),
            ("Referrer-Policy", "same-origin"),
            ("Permissions-Policy", "geolocation=(), microphone=(), camera=(), payment=()"),
            ("Cross-Origin-Opener-Policy", "same-origin"),
        ]
        # X-Frame-Options cannot express wildcards and is ignored by modern
        # browsers when CSP frame-ancestors is present, so only send it for the
        # default same-origin case (older browsers) and let CSP cover the rest.
        if fa == "'self'":
            hdrs.append(("X-Frame-Options", "SAMEORIGIN"))
        return hdrs

    def _client_ip(self):
        """Best-effort client IP (Vercel/Koyeb/tunnel put the real IP in XFF)."""
        xff = self.headers.get("X-Forwarded-For") or ""
        if xff:
            ip = xff.split(",")[0].strip()
            if ip: return ip[:64]
        for h in ("X-Real-IP", "CF-Connecting-IP"):
            v = (self.headers.get(h) or "").strip()
            if v: return v[:64]
        try:
            return (self.client_address[0] or "unknown")[:64]
        except Exception:
            return "unknown"

    def _send(self, obj, code=200, extra_headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in self._security_headers(): self.send_header(k, v)
        for k, v in (extra_headers or []): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def _err(self, msg, code=400, extra_headers=None):
        self._send({"error": msg}, code, extra_headers=extra_headers)

    def _throttled(self, seconds, what="Too many attempts"):
        """429 with a human message and a Retry-After the client can honour."""
        s = int(max(1, math.ceil(seconds)))
        self._err("%s — try again in %s." % (what, authsec.human_wait(s)), 429,
                  extra_headers=[("Retry-After", str(s))])

    def _body(self):
        try:
            n = int(self.headers.get("Content-Length", "0") or 0)
            if n > 200_000: return None
            raw = self.rfile.read(n) if n else b"{}"
            return json.loads(raw or b"{}")
        except Exception:
            return None

    def _is_secure(self):
        """True when served over HTTPS (the proxied preview) — needs SameSite=None for embedded use."""
        if (self.headers.get("X-Forwarded-Proto") or "").lower() == "https": return True
        if (self.headers.get("X-Scheme") or "").lower() == "https": return True
        host = (self.headers.get("Host") or "").split(":")[0]
        return bool(host) and host not in ("localhost", "127.0.0.1", "0.0.0.0")

    def _cookie_header(self, token, max_age=None, clear=False):
        if max_age is None: max_age = authsec.SESSION_COOKIE_MAX_AGE
        if clear:
            extra = "SameSite=None; Secure" if self._is_secure() else "SameSite=Lax"
            return f"jwr_sess=; Path=/; Max-Age=0; {extra}"
        extra = "SameSite=None; Secure; HttpOnly" if self._is_secure() else "SameSite=Lax; HttpOnly"
        return f"jwr_sess={token}; Path=/; Max-Age={max_age}; {extra}"

    def _bearer_token(self):
        """Token supplied in a header (not the cookie). Custom headers cannot be
        set by a cross-site request without a CORS preflight, which we never
        grant — so header auth is inherently CSRF-proof."""
        ah = self.headers.get("Authorization", "")
        if ah.lower().startswith("bearer "):
            t = ah[7:].strip()
            if t: return t
        t = (self.headers.get("X-Auth-Token") or "").strip()
        return t or None

    def _cookie_token(self):
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        ck = cookie.get("jwr_sess")
        return ck.value if ck and ck.value else None

    def _expected_hosts(self):
        """Hostnames this server is legitimately reachable as.

        A reverse proxy (Vercel, Koyeb, the preview tunnel) may rewrite Host,
        so the forwarded values and an explicit allow-list are accepted too —
        otherwise a correct same-origin login would be rejected as cross-site.
        """
        hosts = set()
        for h in (self.headers.get("Host"), self.headers.get("X-Forwarded-Host")):
            for part in (h or "").split(","):
                part = part.strip().lower()
                if part:
                    hosts.add(part)
                    hosts.add(part.split(":")[0])          # bare hostname too
        for extra in (os.environ.get("TRUSTED_ORIGINS") or "").split():
            extra = extra.strip().lower().rstrip("/")
            m = re.match(r"^[a-z][a-z0-9+.-]*://([^/?#]+)", extra)
            if m:
                hosts.add(m.group(1))
                hosts.add(m.group(1).split(":")[0])
            elif extra:
                hosts.add(extra)
                hosts.add(extra.split(":")[0])
        return hosts

    def _csrf_ok(self):
        """Guard state-changing requests that authenticate via COOKIE only.

        Two independent checks, both satisfied by the real frontend:
          1. the body must be JSON — a cross-site <form> or `text/plain`
             beacon cannot set Content-Type: application/json without a
             preflight, and we never send CORS headers;
          2. Origin (or Referer) must name a host this server is served as.
        """
        if self._bearer_token():
            return True                      # header auth: not forgeable cross-site
        ctype = (self.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        if ctype and ctype != "application/json":
            return False
        origin = (self.headers.get("Origin") or "").strip()
        referer = (self.headers.get("Referer") or "").strip()
        src = origin or referer
        if not src:
            # Browsers always attach Origin to a fetch POST. Its absence means a
            # non-browser client, which has no cookie to abuse — allowed only
            # when no session cookie is being presented.
            return not self._cookie_token()
        m = re.match(r"^[a-zA-Z][a-zA-Z0-9+.-]*://([^/?#]+)", src)
        if not m:
            return False
        return m.group(1).strip().lower() in self._expected_hosts()

    # The DB stores SHA-256(token), so a dump of the sessions table is useless
    # to an attacker. Sessions also expire, and any session created before the
    # last password change is rejected outright (belt-and-braces behind
    # revoke_sessions()). Only NON-secret user columns are selected: the
    # credential hash never travels around inside the request-scoped user dict.
    _AUTH_SQL_FULL = """SELECT u.id, u.name, u.code, u.partner_id, u.role,
                               u.exam_date, u.avatar_color, u.created_at,
                               s.token AS _sess, s.created_at AS _sess_at
                          FROM sessions s JOIN users u ON u.id=s.user_id
                         WHERE s.token=?
                           AND (s.expires_at IS NULL OR s.expires_at=0 OR s.expires_at>?)
                           AND (u.pw_changed_at IS NULL OR s.created_at>=u.pw_changed_at)"""
    # Pre-hardening shape (no expires_at / pw_changed_at / role): used only
    # after a request has SEEN those columns missing, so a partial migration
    # DEGRADES (weaker checks, admin panel reads role from the DB instead of
    # the session) instead of 500-ing on every authenticated request.
    _AUTH_SQL_LEGACY = """SELECT u.id, u.name, u.code, u.partner_id,
                                 u.exam_date, u.avatar_color, u.created_at,
                                 s.token AS _sess, s.created_at AS _sess_at
                            FROM sessions s JOIN users u ON u.id=s.user_id
                           WHERE s.token=?"""

    def _auth(self):
        token = self._cookie_token() or self._bearer_token()
        if not token: return None
        digest = authsec.hash_token(token)
        c = db()
        try:
            if _AUTH_COLS_FULL[0] is False:
                r = c.execute(self._AUTH_SQL_LEGACY, (digest,)).fetchone()
            else:
                try:
                    r = c.execute(self._AUTH_SQL_FULL, (digest, time.time())).fetchone()
                    _AUTH_COLS_FULL[0] = True
                except Exception as e:
                    # CRITICAL: only a PROVABLY missing column/table degrades to
                    # the legacy shape. A transient DB failure must re-raise and
                    # become a 500 — returning None here would send a 401, and
                    # app.js clears the stored token on 401, silently signing
                    # out every user on a passing Turso blip. Degrade, don't deny.
                    if _AUTH_COLS_FULL[0] is True or not _missing_schema(e):
                        raise
                    _AUTH_COLS_FULL[0] = False
                    r = c.execute(self._AUTH_SQL_LEGACY, (digest,)).fetchone()
        finally:
            c.close()
        return dict(r) if r else None

    def _nonce_ok(self, c, uid, body):
        nonce = (body or {}).get("nonce")
        if not nonce: return True
        r = c.execute("SELECT result FROM nonces WHERE nonce=?", (nonce,)).fetchone()
        if r:
            self._send(json.loads(r["result"])); return False
        return True

    def _nonce_save(self, c, uid, nonce, result):
        if nonce:
            c.execute("INSERT OR IGNORE INTO nonces(nonce,user_id,created_at,result) VALUES(?,?,?,?)",
                      (nonce, uid, time.time(), json.dumps(result)))
            c.execute("DELETE FROM nonces WHERE created_at < ?", (time.time() - 86400,))

    # -------- routing
    def _restore_vercel_path(self):
        # On Vercel the catch-all rewrite forwards as /api/index?__path=/orig
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if "__path" in q:
            p0 = q["__path"][0]
            if not p0.startswith("/"): p0 = "/" + p0
            rest = {k: v for k, v in q.items() if k != "__path"}
            self.path = p0 + (("?" + urlencode(rest, doseq=True)) if rest else "")

    def do_GET(self):
        self._restore_vercel_path()
        u = urlparse(self.path); p = u.path; q = parse_qs(u.query)
        if p == "/healthz":
            try:
                import time as _t, os as _os
                t0 = _t.time()
                hc = db()
                t1 = _t.time()
                hc.execute("SELECT 1").fetchone()
                t2 = _t.time()
                out = {"ok": True}
                if q.get("detail"):
                    out.update(connect_ms=int((t1 - t0) * 1000),
                               query_ms=int((t2 - t1) * 1000),
                               total_ms=int((t2 - t0) * 1000),
                               region=_os.environ.get("VERCEL_REGION", "local"))
                    # Boot/migration outcome from the entry point: a failed
                    # init_db() used to be invisible until users hit 500s.
                    out["bootOk"] = bool(BOOT_INFO.get("ok"))
                    if BOOT_INFO.get("error"):
                        out["bootError"] = str(BOOT_INFO["error"])[:500]
                    # Lets the admin confirm the one-way auth migration actually
                    # ran on PRODUCTION (a preview deployment deliberately skips
                    # it, so this is how you tell the two apart).
                    try:
                        pend = pending_migrations(hc)
                        out["pendingMigrations"] = pend
                        out["destructiveMigrationsAllowed"] = destructive_migrations_allowed()
                        out["vercelEnv"] = _os.environ.get("VERCEL_ENV", "local")
                    except Exception:
                        pass
                hc.close()
                return self._send(out)
            except Exception:
                # /healthz is public: never leak driver/host details in the body
                try:
                    sys.stderr.write("ERROR /healthz\n%s\n" % traceback.format_exc())
                    sys.stderr.flush()
                except Exception:
                    pass
                msg = "db unavailable"
                if BOOT_INFO.get("error"):
                    msg += " (boot error: %s)" % str(BOOT_INFO["error"])[:300]
                return self._err(msg, 503)
        if p.startswith("/api/"):
            # _auth() is INSIDE the try: an exception escaping the handler
            # sends no response at all (socket closed), and Vercel's proxy
            # answers with a non-JSON 500 — the "Request failed (500)" bug.
            # Every failure must reach _server_error's JSON body instead.
            try:
                user = self._auth()
                if not user: return self._err("Not authenticated", 401)
                return self.api_get(user, p, q)
            except Exception:
                return self._server_error(p)
        try:
            return self.static(p)
        except Exception:
            return self._server_error(p)

    def _server_error(self, p):
        """Never echo exception text to the client: it leaks schema, paths and
        library internals to anyone probing the API. Details go to the log."""
        exc = sys.exc_info()[1]
        # A browser that navigated away mid-response is not a server fault;
        # logging it as one hides real failures (and costs log volume).
        if isinstance(exc, (BrokenPipeError, ConnectionResetError, ConnectionAbortedError)):
            return
        try:
            sys.stderr.write("ERROR %s %s\n%s\n" % (self.command, p, traceback.format_exc()))
            sys.stderr.flush()
        except Exception:
            pass
        try:
            return self._err("Server error — please try again.", 500)
        except Exception:
            return None        # the socket is already gone; nothing to send

    def do_POST(self):
        self._restore_vercel_path()
        u = urlparse(self.path); p = u.path
        if p.startswith("/api/"):
            # _csrf_ok()/_auth() are INSIDE the try: an exception escaping the
            # handler sends no response at all (socket closed), and Vercel's
            # proxy answers with a non-JSON 500 — the "Request failed (500)"
            # bug. Every failure must reach _server_error's JSON body instead.
            try:
                # CSRF: cookie-authenticated POSTs must be same-origin JSON.
                # (Production sets SameSite=None; Secure so the app can be
                # embedded/proxied, which makes this check the real CSRF defence.)
                if not self._csrf_ok():
                    return self._err("Cross-site request blocked.", 403)
                body = self._body()
                if body is None: return self._err("Invalid request body")
                auth_free = p in self.AUTH_FREE_PATHS
                # A new device has no token on signup/login/reset: skip _auth()
                # there entirely (pure risk and a wasted round trip).
                user = None if auth_free else self._auth()
                if not user and not auth_free: return self._err("Not authenticated", 401)
                return self.api_post(user, p, body)
            except Exception:
                return self._server_error(p)
        self._err("Not found", 404)

    # -------- static
    def static(self, p):
        if p == "/": p = "/index.html"
        fn = os.path.normpath(os.path.join(STATIC, p.lstrip("/")))
        # compare against STATIC + separator: a bare startswith() also accepts
        # sibling directories like ".../jee-war-room-secrets/x"
        if not fn.startswith(STATIC + os.sep) or not os.path.isfile(fn):
            fn = os.path.join(STATIC, "index.html")
        ctype = {"html": "text/html; charset=utf-8", "js": "application/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8", "svg": "image/svg+xml", "png": "image/png",
                 "ico": "image/x-icon", "json": "application/json", "mp3": "audio/mpeg",
                 "webm": "video/webm", "mp4": "video/mp4"}.get(fn.rsplit(".", 1)[-1], "application/octet-stream")
        with open(fn, "rb") as f: data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        for k, v in self._security_headers(): self.send_header(k, v)
        if fn.endswith(".html"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        else:
            # versioned asset urls (/app.js?v=N) are re-fetched whenever the version bumps
            self.send_header("Cache-Control", "no-cache")
        self.end_headers(); self.wfile.write(data)

    # ============================================================ GET API
    def api_get(self, user, p, q):
        # Cheap (a timestamp comparison): gives the background cleanup a chance
        # to run on warm read traffic, never on the latency-critical auth paths.
        self._kick_housekeeping()
        c = db(); uid = user["id"]; s = get_settings(c, uid)
        if s.get("sweepDay") != TODAY():
            sweep_targets(c, uid, s)
            save_settings(c, uid, s)
            c.commit()
        try:
            if p == "/api/me":
                ensure_snapshots(c, uid, s)
                u = c.execute("SELECT id,name,code,partner_id,exam_date,avatar_color,created_at,role FROM users WHERE id=?", (uid,)).fetchone()
                out = {"user": rowdict(u), "settings": s, "global": admin_settings(c),
                       "summary": today_summary(c, uid, s),
                       "xp": level_info(xp_total(c, uid)), "streak": streak_info(c, uid, s),
                       "air": self._air(c, uid, s)}
                return self._send(out)
            if p == "/api/me/security": return self.security_info(c, uid)
            if p == "/api/targets":
                day = q.get("date", [TODAY()])[0]
                if not valid_day(day): return self._err("Invalid date")
                rows = c.execute("SELECT * FROM targets WHERE user_id=? AND day=? ORDER BY id", (uid, day)).fetchall()
                return self._send({"targets": [rowdict(r) for r in rows]})
            if p == "/api/chapters":
                rows = c.execute("SELECT * FROM chapters WHERE user_id=? ORDER BY subject, sort, id", (uid,)).fetchall()
                groups = defaultdict(list)
                for r in rows: groups[r["subject"]].append(rowdict(r))
                return self._send({"subjects": SUBJECTS, "chapters": dict(groups)})
            if p == "/api/activities":
                days = int(q.get("days", ["30"])[0]); days = clamp(days, 1, 400)
                start = DAY_F(datetime.now(IST).date() - timedelta(days=days - 1))
                rows = c.execute("SELECT * FROM activities WHERE user_id=? AND day>=? ORDER BY id DESC LIMIT 500", (uid, start)).fetchall()
                return self._send({"activities": [rowdict(r) for r in rows]})
            if p == "/api/mocks":
                rows = c.execute("SELECT * FROM mocks WHERE user_id=? ORDER BY day DESC, id DESC LIMIT 100", (uid,)).fetchall()
                return self._send({"mocks": [self._mock_view(r) for r in rows]})
            if p == "/api/errors":
                days = int(q.get("days", ["60"])[0]); days = clamp(days, 1, 400)
                start = DAY_F(datetime.now(IST).date() - timedelta(days=days - 1))
                rows = c.execute("SELECT * FROM errors WHERE user_id=? AND day>=? ORDER BY id DESC LIMIT 300", (uid, start)).fetchall()
                return self._send({"errors": [rowdict(r) for r in rows]})
            if p == "/api/timer/latest":
                r = c.execute("SELECT * FROM timers WHERE user_id=? AND logged=0 ORDER BY id DESC LIMIT 1", (uid,)).fetchone()
                return self._send({"timer": rowdict(r)})
            if p == "/api/air":
                return self._send(self._air(c, uid, s, with_comps=True, settings=s))
            if p == "/api/friends":
                ids = friend_ids(c, uid)
                views = [self._friend_view(c, uid, pid) for pid in ids]
                return self._send({"connected": bool(ids), "count": len(ids),
                                   "max": MAX_FRIENDS, "friends": [v for v in views if v]})
            if p == "/api/messages":
                return self._send(self.messages_list(c, uid, q))
            if p == "/api/announcements":
                rows = c.execute("SELECT * FROM announcements ORDER BY id DESC LIMIT 50").fetchall()
                seen = list(s.get("seenAnnouncements") or [])
                out = []
                for r in rows:
                    d = rowdict(r); d["read"] = d["id"] in seen; out.append(d)
                return self._send({"announcements": out})
            if p == "/api/admin/reports":
                if not self.is_admin(c, uid): return self._err("Admin only.", 403)
                rows = c.execute("""SELECT r.id,r.body,r.created_at,r.resolved,r.user_id,u.name AS user_name
                    FROM reports r LEFT JOIN users u ON u.id=r.user_id
                    ORDER BY r.resolved ASC, r.id DESC LIMIT 100""").fetchall()
                return self._send({"reports": [dict(r) for r in rows]})
            if p == "/api/admin/users":
                if not self.is_admin(c, uid): return self._err("Admin only.", 403)
                since = DAY_F(datetime.now(IST).date() - timedelta(days=7))
                rows = c.execute("""
                    SELECT u.id,u.name,u.code,u.role,u.created_at,
                      (SELECT COUNT(*) FROM chapters ch WHERE ch.user_id=u.id AND ch.hidden=0 AND ch.status='completed') ch_done,
                      (SELECT COUNT(*) FROM chapters ch WHERE ch.user_id=u.id AND ch.hidden=0) ch_total,
                      (SELECT COUNT(*) FROM activities a WHERE a.user_id=u.id AND a.day>=?) act7,
                      (SELECT MAX(day) FROM activities a WHERE a.user_id=u.id) last_active,
                      (SELECT MAX(created_at) FROM sessions se WHERE se.user_id=u.id) last_login
                    FROM users u ORDER BY u.id""", (since,)).fetchall()
                return self._send({"users": [rowdict(r) for r in rows]})
            if p == "/api/admin/auth-events":
                if not self.is_admin(c, uid): return self._err("Admin only.", 403)
                rows = c.execute("""SELECT id,ts,kind,user_id,name,ip,detail FROM auth_events
                                    ORDER BY id DESC LIMIT 200""").fetchall()
                return self._send({"events": [rowdict(r) for r in rows]})
            if p == "/api/stats":
                return self._send(self._stats(c, uid, s, q.get("range", ["30"])[0]))
            if p == "/api/export":
                return self._send(self._export(c, uid))
            return self._err("Not found", 404)
        finally:
            c.close()

    def _mock_view(self, r):
        d = rowdict(r)
        total = d["total"] or 300
        score = d["score"]
        if score is None and d["phys"] is not None:
            score = (d["phys"] or 0) + (d["chem"] or 0) + (d["math"] or 0)
        d["scoreCalc"] = round(score or 0, 1)
        d["percent"] = round(100 * (score or 0) / total, 1)
        d["accuracy"] = round(100 * d["correct"] / d["attempted"], 1) if d["attempted"] else 0
        return d

    def _air(self, c, uid, s, with_comps=False, settings=None):
        ensure_snapshots(c, uid, s)
        t = TODAY()
        cur = c.execute("SELECT * FROM snapshots WHERE user_id=? AND day=?", (uid, t)).fetchone()
        rows = c.execute("SELECT * FROM snapshots WHERE user_id=? ORDER BY day DESC LIMIT 30", (uid,)).fetchall()
        hist = [{"day": r["day"], "score": r["score"], "air": r["air"]} for r in reversed(rows)]
        d7 = c.execute("SELECT air FROM snapshots WHERE user_id=? AND day=?", (uid, DAY_F(datetime.now(IST).date() - timedelta(days=7)))).fetchone()
        y = c.execute("SELECT air FROM snapshots WHERE user_id=? AND day=?", (uid, DAY_F(datetime.now(IST).date() - timedelta(days=1)))).fetchone()
        cur_air = cur["air"] if cur else 600000
        trend7 = (d7["air"] - cur_air) if d7 else 0   # positive = rank improved (number got smaller)
        out = {"air": cur_air, "airFormatted": ind(cur_air), "score": cur["score"] if cur else 0,
               "trend7": trend7, "yesterdayAir": y["air"] if y else None, "history": hist,
               "label": "Preparation trajectory — not an actual JEE rank prediction."}
        if with_comps:
            raw, comps, detail = raw_score(c, uid, datetime.now(IST).date(), s)
            out.update({"rawToday": raw, "components": comps, "detail": detail, "weights": s["weights"], "ema": s.get("airEMA", 0.88)})
        return out

    def _friend_view(self, c, uid, pid):
        pu = c.execute("SELECT * FROM users WHERE id=?", (pid,)).fetchone()
        if not pu: return None
        ps = get_settings(c, pid); sh = ps["sharing"]
        out = {"connected": True, "uid": pid, "name": pu["name"], "avatarColor": pu["avatar_color"],
               "examDate": pu["exam_date"], "code": pu["code"],
               "unread": c.execute(f"SELECT COUNT(*) n FROM messages WHERE recipient=? AND sender=? AND read_at IS NULL AND {msg_visible(uid)}",
                                   (uid, pid)).fetchone()[0],
               "shareBlock": bool(sh.get("studyTime") or sh.get("targets") or sh.get("questions"))}
        if out["shareBlock"]: out["summary"] = today_summary(c, pid, ps)
        if sh.get("streak"): out["streak"] = streak_info(c, pid, ps)
        if sh.get("xp"): out["xp"] = level_info(xp_total(c, pid))
        if sh.get("score") or sh.get("air"):
            snap = c.execute("SELECT score, air FROM snapshots WHERE user_id=? ORDER BY day DESC LIMIT 1", (pid,)).fetchone()
            cur_air = snap["air"] if snap else 600000
            cur_score = snap["score"] if snap else 0.0
            d7 = c.execute("SELECT air FROM snapshots WHERE user_id=? AND day=?",
                           (pid, DAY_F(datetime.now(IST).date() - timedelta(days=7)))).fetchone()
            trend7 = (d7["air"] - cur_air) if d7 else 0
            if sh.get("score"): out["score"] = cur_score
            if sh.get("air"): out["air"] = cur_air; out["airFormatted"] = ind(cur_air); out["airTrend7"] = trend7
        if sh.get("mocks"):
            m = c.execute("SELECT * FROM mocks WHERE user_id=? ORDER BY day DESC,id DESC LIMIT 1", (pid,)).fetchone()
            if m: out["latestMock"] = self._mock_view(m)
        if sh.get("distractions"):
            wk = DAY_F(datetime.now(IST).date() - timedelta(days=6))
            out["friendDistractWeek"] = c.execute("SELECT COALESCE(SUM(duration),0) v FROM activities WHERE user_id=? AND day>=? AND type='distraction'", (pid, wk)).fetchone()["v"]
        # last-7-day aggregates for the duel (only if the relevant blocks are shared)
        if not out.get("shareBlock"):
            return out
        wk = DAY_F(datetime.now(IST).date() - timedelta(days=6))
        wm = 0; wq = 0
        for r in c.execute("SELECT type, SUM(duration) dm, SUM(amount) am FROM activities WHERE user_id=? AND day>=? GROUP BY type", (pid, wk)):
            if r["type"] in ('study', 'revision'): wm += (r["dm"] or 0)
            if r["type"] in ('pyq', 'dpp', 'homework'): wq += (r["am"] or 0)
        wtr = c.execute("SELECT status,COUNT(*) n FROM targets WHERE user_id=? AND day>=? GROUP BY status", (pid, wk)).fetchall()
        wtm = {r["status"]: r["n"] for r in wtr}
        wp = sum(wtm.values()); wd = wtm.get("done", 0) + 0.5 * wtm.get("partial", 0)
        out["week"] = {"minutes": wm, "questions": wq, "planned": wp,
                       "done": wd, "execution": round(100 * wd / wp) if wp else 0}
        return out

    def _stats(self, c, uid, s, rng):
        rng = rng if rng in ("7", "30", "all") else "30"
        days = {"7": 7, "30": 30, "all": 120}[rng]
        start = DAY_F(datetime.now(IST).date() - timedelta(days=days - 1))
        series = []
        for i in range(days):
            d = DAY_F(datetime.now(IST).date() - timedelta(days=days - 1 - i))
            series.append({"day": d, "minutes": 0, "questions": 0, "distract": 0, "planned": 0, "done": 0.0, "revision": 0})
        idx = {x["day"]: x for x in series}
        for r in c.execute("SELECT day, type, SUM(duration) dm, SUM(amount) am FROM activities WHERE user_id=? AND day>=? GROUP BY day,type", (uid, start)):
            e = idx.get(r["day"])
            if not e: continue
            if r["type"] in STUDY_TYPES: e["minutes"] += r["dm"] or 0
            if r["type"] in ACT_Q_TYPES: e["questions"] += r["am"] or 0
            if r["type"] == "distraction": e["distract"] += r["dm"] or 0
            if r["type"] == "revision": e["revision"] += r["dm"] or 0
        for r in c.execute("SELECT day, status, COUNT(*) n FROM targets WHERE user_id=? AND day>=? GROUP BY day,status", (uid, start)):
            e = idx.get(r["day"])
            if e:
                e["planned"] += r["n"]
                if r["status"] == "done": e["done"] += r["n"]
                elif r["status"] == "partial": e["done"] += 0.5 * r["n"]
        totals = {"minutes": sum(x["minutes"] for x in series),
                  "questions": sum(x["questions"] for x in series),
                  "distract": sum(x["distract"] for x in series),
                  "revision": sum(x["revision"] for x in series),
                  "planned": sum(x["planned"] for x in series),
                  "done": sum(x["done"] for x in series)}
        totals["execution"] = round(100 * totals["done"] / totals["planned"]) if totals["planned"] else 0
        # subject breakdown
        subs = {}
        for r in c.execute("SELECT subject, SUM(duration) dm, SUM(amount) am, COUNT(*) n FROM activities WHERE user_id=? AND day>=? AND subject!='' GROUP BY subject", (uid, start)):
            subs[r["subject"]] = {"minutes": r["dm"] or 0, "questions": r["am"] or 0, "logs": r["n"]}
        # distractions
        d_today = TODAY(); d_week = DAY_F(datetime.now(IST).date() - timedelta(days=6)); d_month = DAY_F(datetime.now(IST).date() - timedelta(days=29))
        def dsum(frm):
            return c.execute("SELECT COALESCE(SUM(duration),0) v FROM activities WHERE user_id=? AND day>=? AND type='distraction'", (uid, frm)).fetchone()["v"]
        dcat = {}
        for r in c.execute("SELECT extra, SUM(duration) dm FROM activities WHERE user_id=? AND day>=? AND type='distraction' GROUP BY extra", (uid, d_week)):
            dcat[r["extra"] or "Other"] = r["dm"] or 0
        top_distract = max(dcat.items(), key=lambda kv: kv[1])[0] if dcat else None
        # errors
        epat = {}; esub = {}
        for r in c.execute("SELECT etype, COUNT(*) n FROM errors WHERE user_id=? AND day>=? GROUP BY etype", (uid, start)):
            epat[r["etype"]] = r["n"]
        for r in c.execute("SELECT subject, COUNT(*) n FROM errors WHERE user_id=? AND day>=? GROUP BY subject", (uid, start)):
            esub[r["subject"]] = r["n"]
        # mocks trend
        mocks = [self._mock_view(r) for r in c.execute("SELECT * FROM mocks WHERE user_id=? ORDER BY day ASC, id ASC", (uid,))]
        # air history
        ensure_snapshots(c, uid, s)
        airhist = [rowdict(r) for r in c.execute("SELECT * FROM snapshots WHERE user_id=? AND day>=? ORDER BY day", (uid, start))]
        return {"range": rng, "series": series, "totals": totals, "subjects": subs,
                "distract": {"today": dsum(d_today), "week": dsum(d_week), "month": dsum(d_month), "byCategory": dcat, "top": top_distract},
                "errorPatterns": epat, "errorSubjects": esub, "mocks": mocks, "airHistory": airhist,
                "weekly": self._weekly(c, uid), "coach": self._coach(c, uid, s, series, totals, subs, dcat)}

    def _weekly(self, c, uid):
        t = datetime.now(IST).date()
        def block(start, end):
            r = {"targetsPlanned": 0, "targetsDone": 0.0, "minutes": 0, "questions": 0, "revision": 0,
                 "mocks": 0, "distract": 0, "errors": 0}
            for x in c.execute("SELECT status, COUNT(*) n FROM targets WHERE user_id=? AND day BETWEEN ? AND ? GROUP BY status", (uid, start, end)):
                r["targetsPlanned"] += x["n"]
                if x["status"] == "done": r["targetsDone"] += x["n"]
                elif x["status"] == "partial": r["targetsDone"] += 0.5 * x["n"]
            for x in c.execute("SELECT type, SUM(duration) dm, SUM(amount) am, COUNT(*) n FROM activities WHERE user_id=? AND day BETWEEN ? AND ? GROUP BY type", (uid, start, end)):
                if x["type"] in STUDY_TYPES: r["minutes"] += x["dm"] or 0
                if x["type"] in ACT_Q_TYPES: r["questions"] += x["am"] or 0
                if x["type"] == "revision": r["revision"] += x["dm"] or 0
                if x["type"] == "distraction": r["distract"] += x["dm"] or 0
            r["mocks"] = c.execute("SELECT COUNT(*) n FROM mocks WHERE user_id=? AND day BETWEEN ? AND ?", (uid, start, end)).fetchone()["n"]
            r["errors"] = c.execute("SELECT COUNT(*) n FROM errors WHERE user_id=? AND day BETWEEN ? AND ?", (uid, start, end)).fetchone()["n"]
            r["completion"] = round(100 * r["targetsDone"] / r["targetsPlanned"]) if r["targetsPlanned"] else 0
            return r
        cur = block(DAY_F(t - timedelta(days=6)), DAY_F(t))
        prev = block(DAY_F(t - timedelta(days=13)), DAY_F(t - timedelta(days=7)))
        # weakness
        a_now = c.execute("SELECT air FROM snapshots WHERE user_id=? AND day=?", (uid, DAY_F(t))).fetchone()
        a_old = c.execute("SELECT air FROM snapshots WHERE user_id=? AND day=?", (uid, DAY_F(t - timedelta(days=7)))).fetchone()
        air_move = (a_old["air"] - a_now["air"]) if (a_now and a_old) else 0
        weakness = None
        if cur["targetsPlanned"] and cur["completion"] < 60:
            weakness = "Your biggest issue this week was missed targets — plan fewer, finish what you plan."
        elif cur["revision"] < 90 and cur["questions"] > cur["revision"]:
            weakness = "You are solving questions but not revising old chapters. Add one revision slot daily."
        elif cur["mocks"] == 0:
            weakness = "No mock test this week. Book one test day and analyse it the same day."
        elif cur["distract"] > 180:
            weakness = "Distraction time is high this week (over 3h). Phone out of reach during focus blocks."
        elif cur["errors"] == 0 and cur["mocks"] > 0:
            weakness = "You gave tests but logged almost no error analysis — mistakes will repeat."
        elif cur["targetsPlanned"] == 0:
            weakness = "No targets were planned this week, so consistency is invisible. Use a day template."
        else:
            weakness = "No major weakness — keep the same rhythm going."
        return {"current": cur, "previous": prev, "airMovement": air_move, "weakness": weakness}

    def _coach(self, c, uid, s, series, totals, subs, dcat):
        tips = []
        last7 = series[-7:]
        over_planned = sum(1 for x in last7 if x["planned"] > x["done"] and x["planned"] > 0)
        if over_planned >= 5:
            tips.append(f"You planned more than you completed on {over_planned} of the last 7 days. Shrink tomorrow's list by 30%.")
        q7 = sum(x["questions"] for x in last7); r7 = sum(x["revision"] for x in last7)
        if q7 >= 200 and r7 < 90:
            tips.append("Your question volume is strong, but revision is falling behind. Schedule 45 min revision before new practice.")
        if subs:
            best = max(subs.items(), key=lambda kv: kv[1]["minutes"] + kv[1]["questions"] / 10)
            tips.append(f"You have been most consistent in {best[0]}. Protect that streak and rotate focus toward your weakest subject.")
        last_mock = c.execute("SELECT day FROM mocks WHERE user_id=? ORDER BY day DESC LIMIT 1", (uid,)).fetchone()
        if last_mock:
            age = (datetime.now(IST).date() - date.fromisoformat(last_mock["day"])).days
            if age >= 10: tips.append(f"Your last mock test was {age} days ago. Aim for one full test every 7–10 days.")
        else:
            tips.append("No mock test logged yet. Take one within the next 3 days to calibrate.")
        d7 = sum(x["distract"] for x in last7)
        if d7 >= 120:
            top = max(dcat.items(), key=lambda kv: kv[1])[0] if dcat else "distractions"
            tips.append(f"You lost {d7//60}h{d7%60:02d}m to distractions this week, mostly {top}.")
        # mock trend
        mrows = [self._mock_view(r) for r in c.execute("SELECT * FROM mocks WHERE user_id=? ORDER BY day DESC,id DESC LIMIT 3", (uid,))]
        if len(mrows) >= 2 and mrows[0]["percent"] < mrows[-1]["percent"] - 3:
            tips.append(f"Your latest mock ({mrows[0]['percent']}%) is below your earlier one ({mrows[-1]['percent']}%). Check the error book pattern before the next test.")
        err_tot = c.execute("SELECT COUNT(*) n FROM errors WHERE user_id=?", (uid,)).fetchone()["n"]
        if err_tot == 0:
            tips.append("Your error book is empty. Logging mistake types is how you stop repeating them.")
        # streak at risk
        st = streak_info(c, uid, s)
        if st["current"] == 0:
            tips.append("You have no active streak. Log study time, complete a target, or take a mock to restart it.")
        return tips[:5]

    def _export(self, c, uid):
        out = {}
        for t in ("activities", "targets", "mocks", "errors", "xp_events", "snapshots", "chapters"):
            out[t] = [rowdict(r) for r in c.execute(f"SELECT * FROM {t} WHERE user_id=?", (uid,))]
        out["settings"] = get_settings(c, uid)
        out["exportedAt"] = now_iso()
        return out

    # ============================================================ POST API
    def api_post(self, user, p, body):
        # auth-free
        if p == "/api/auth/signup": return self.signup(body)
        if p == "/api/auth/login": return self.login(body)
        if p == "/api/auth/reset-password": return self.reset_password(body)
        c = db(); uid = user["id"]; s = get_settings(c, uid)
        if s.get("sweepDay") != TODAY():
            sweep_targets(c, uid, s)
            save_settings(c, uid, s)
            c.commit()
        try:
            if p == "/api/auth/logout":
                # sessions are keyed by SHA-256(token), so delete via the digest
                # resolved during auth (works for cookie AND header tokens).
                tok = user.get("_sess") or None
                if not tok:
                    raw = self._cookie_token() or self._bearer_token()
                    if raw: tok = authsec.hash_token(raw)
                if tok: c.execute("DELETE FROM sessions WHERE token=?", (tok,)); c.commit()
                return self._send({"ok": True}, extra_headers=[("Set-Cookie", self._cookie_header("", clear=True))])
            if p == "/api/me": return self.update_me(c, uid, body)
            if p == "/api/me/password": return self.change_my_password(c, uid, body)
            if p == "/api/me/recovery-codes": return self.regenerate_recovery_codes(c, uid, body)
            if p == "/api/me/sessions/revoke-all": return self.revoke_all_sessions(c, uid, body)
            if p == "/api/friend/connect": return self.friend_connect(c, user, body)
            if p == "/api/friend/unlink": return self.friend_unlink(c, user, body)
            if p == "/api/messages": return self.send_message(c, user, body)
            if p == "/api/messages/read": return self.mark_read(c, user["id"], body)
            if p == "/api/messages/clear": return self.clear_chat(c, user["id"], body)
            if p == "/api/activities": return self.log_activity(c, uid, s, body)
            if p == "/api/targets": return self.create_target(c, uid, body)
            if p == "/api/targets/template": return self.apply_template(c, uid, s, body)
            if p.startswith("/api/targets/"):
                segs = p.strip("/").split("/")
                if segs[-1] == "duplicate": return self.dup_target(c, uid, int(segs[-2]), body)
                if segs[-1] == "delete": return self.delete_target(c, uid, int(segs[-2]))
                return self.patch_target(c, uid, s, int(segs[-1]), body)
            if p == "/api/chapters/add": return self.chapter_add(c, uid, body)
            if p == "/api/chapters/bulk": return self.chapter_bulk(c, uid, body)
            if p.startswith("/api/chapters/"):
                segs = p.strip("/").split("/")
                if segs[-1] == "move": return self.chapter_move(c, uid, int(segs[-2]), body)
                return self.chapter_patch(c, uid, int(segs[-1]), body)
            if p == "/api/mocks": return self.add_mock(c, uid, s, body)
            if p == "/api/errors": return self.add_error(c, uid, s, body)
            if p.startswith("/api/mocks/") and p.endswith("/delete"):
                return self.delete_owned(c, uid, "mocks", int(p.split("/")[-2]), reverse_xp="mock")
            if p.startswith("/api/errors/") and p.endswith("/delete"):
                return self.delete_owned(c, uid, "errors", int(p.split("/")[-2]))
            if p == "/api/timer/start": return self.timer_start(c, uid, body)
            if p == "/api/timer/complete": return self.timer_complete(c, uid, s, body)
            if p == "/api/timer/cancel": return self.timer_cancel(c, uid, body)
            if p == "/api/settings": return self.update_settings(c, uid, s, body)
            if p == "/api/announcements": return self.announcement_create(c, uid, body)
            if p == "/api/announcements/read": return self.announcement_read(c, uid, s, body)
            if p.startswith("/api/announcements/") and p.endswith("/delete"):
                return self.announcement_delete(c, uid, int(p.strip("/").split("/")[-2]))
            if p == "/api/admin/set-password": return self.admin_set_password(c, uid, body)
            if p == "/api/report": return self.report_create(c, uid, body)
            if p == "/api/admin/report/resolve": return self.report_resolve(c, uid, body)
            if p == "/api/account/wipe": return self.wipe(c, uid)
            return self._err("Not found", 404)
        finally:
            c.close()

    # -------- auth
    # One message for every failed credential check, whatever the real reason.
    # Distinct messages ("no account found" vs "wrong code") let an attacker
    # enumerate usernames; identical ones do not.
    LOGIN_FAIL = ("That name and password don't match. Check for a stray space, "
                  "or reset with a recovery code.")
    RESET_FAIL = ("That recovery code isn't valid for this account. Codes are "
                  "single-use — check for typos, use another one, or ask the "
                  "admin to reset your password.")
    FRIEND_CODE_RETIRED = ("Friend codes can no longer be used to reset a password "
                           "(they are meant to be shared with buddies, so they are "
                           "not a secret). Use one of your 12-character recovery "
                           "codes, or ask the admin.")

    def _make_session(self, c, uid):
        """Issue a session; only its SHA-256 digest is stored."""
        return _make_session_row(c, uid, self.headers.get("User-Agent"))

    def _throttle_gate(self, c, keys):
        """Return a 429 response if any of the given throttle keys is locked.

        All keys are checked in ONE query: on Vercel each statement is a
        separate Turso round trip, and the gate used to cost one per key.
        """
        wait, key = authsec.throttle_worst(c, [k for k, _kind, _label in keys])
        if wait > 0:
            label = {k: lab for k, _kind, lab in keys}.get(key) or "Too many attempts"
            return self._throttled(wait, label)
        return None

    def signup(self, body):
        name = (body.get("name") or "").strip()
        pw = body.get("password")
        ip = self._client_ip()
        if not isinstance(pw, str): return self._err("Invalid password.")
        if not (2 <= len(name) <= 30): return self._err("Name must be 2–30 characters.")
        pol = authsec.password_policy_error(pw, name)
        if pol: return self._err(pol)
        c = db()
        try:
            skey = authsec.throttle_key("s", ip)
            wait = authsec.throttle_remaining(c, skey)
            if wait: return self._throttled(wait, "Too many sign-ups from your network")
            if c.execute("SELECT 1 FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone():
                authsec.log_event(c, "signup_name_taken", name=name, ip=ip)
                return self._err("That name is already taken.")
            salt = new_salt()
            code_gen = gen_code(); code = next(code_gen)
            while c.execute("SELECT 1 FROM users WHERE code=?", (code,)).fetchone(): code = next(code_gen)
            colors = ["#f97316", "#22d3ee", "#a3e635", "#f472b6", "#facc15", "#818cf8"]
            exam = body.get("examDate")
            if not exam:
                # default: next likely JEE Main session (late January of next exam year)
                yr = datetime.now(IST).date().year + (1 if datetime.now(IST).date().month >= 6 else 0)
                exam = f"{yr}-01-21"
            role = "admin" if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else "user"
            now = now_iso()
            cur = c.execute("""INSERT INTO users(name,pass_hash,salt,code,exam_date,avatar_color,created_at,role,pw_changed_at)
                VALUES(?,?,?,?,?,?,?,?,?)""", (name, hash_pw(pw, salt), salt, code, exam,
                secrets.choice(colors), now, role, now))
            uid = cur.lastrowid
            s = default_settings(); s["sweepDay"] = TODAY()
            save_settings(c, uid, s)
            # preload chapters (idempotent guard; single batched INSERT)
            if not c.execute("SELECT 1 FROM chapters WHERE user_id=? LIMIT 1", (uid,)).fetchone():
                seed_chapters(c, uid)
            # seed starting snapshot
            c.execute("INSERT OR REPLACE INTO snapshots(user_id,day,score,air) VALUES(?,?,0,600000)", (uid, TODAY()))
            c.commit()
            token = self._make_session(c, uid)
            # Recovery codes are shown to the user exactly once, right here —
            # but issuance is BEST-EFFORT: codes are a convenience the user can
            # regenerate in Settings → Account Security, so a failure must never
            # sink the signup itself (the account + session already exist).
            events = [("signup_ok", uid, name, ip, None)]
            codes = None
            if _RC_TABLE_OK[0] is not False:
                try:
                    codes = issue_recovery_codes(c, uid, ip, log=False)
                    events.append(("recovery_codes_issued", uid, None, ip,
                                   "%d codes" % len(codes)))
                except Exception:
                    try: c.rollback()   # drop any half-written code rows
                    except Exception: pass
                    events.append(("recovery_codes_failed", uid, None, ip,
                                   "issuance failed on signup; user can regenerate in Settings"))
            authsec.log_events(c, events)   # one batched INSERT, then commit
            out = {"ok": True, "code": code, "token": token}
            if codes: out["recoveryCodes"] = codes
            return self._send(out, extra_headers=[("Set-Cookie", self._cookie_header(token))])
        finally:
            c.close()

    def login(self, body):
        name = (body.get("name") or "").strip()
        pw = body.get("password")
        ip = self._client_ip()
        if not isinstance(pw, str): return self._err("Invalid password.")
        ukey = authsec.throttle_key("u", name)
        ikey = authsec.throttle_key("i", ip)
        c = db()
        try:
            blocked = self._throttle_gate(c, [(ukey, "u", "Too many failed sign-ins"),
                                              (ikey, "i", "Too many failed sign-ins from your network")])
            if blocked: return blocked
            # Fetch the user AND the size of their recovery-code batch in ONE
            # round trip: the batch size decides whether first-login codes get
            # issued (it used to cost a separate recovery_status() SELECT).
            # Schema-tolerant like _auth(): a half-migrated DB degrades to the
            # plain lookup instead of failing the login.
            if _RC_TABLE_OK[0] is False:
                u = c.execute("SELECT * FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            else:
                try:
                    u = c.execute("""SELECT u.*,
                                            (SELECT COUNT(*) FROM recovery_codes rc
                                              WHERE rc.user_id=u.id) AS rc_total
                                       FROM users u WHERE u.name=? COLLATE NOCASE""",
                                  (name,)).fetchone()
                    _RC_TABLE_OK[0] = True
                except Exception as e:
                    if _RC_TABLE_OK[0] is not True and _missing_schema(e):
                        _RC_TABLE_OK[0] = False
                        u = c.execute("SELECT * FROM users WHERE name=? COLLATE NOCASE",
                                      (name,)).fetchone()
                    else:
                        raise
            if u:
                ok, legacy = authsec.verify_pw(pw, u["pass_hash"], u["salt"])
            else:
                # Burn the same PBKDF2 cost as a real check so response timing
                # cannot be used to discover which usernames exist.
                authsec.verify_pw(pw, _DUMMY_HASH, _DUMMY_SALT)
                ok, legacy = False, False
            if not ok:
                authsec.throttle_fail(c, ukey, "u", commit=False)
                wait = authsec.throttle_fail(c, ikey, "i")
                authsec.log_event(c, "login_fail", user_id=(u["id"] if u else None),
                                  name=name, ip=ip,
                                  detail=("no such user" if not u else "bad password"))
                if wait > 0: return self._throttled(wait, "Too many failed sign-ins")
                return self._err(self.LOGIN_FAIL, 401)
            if legacy:
                # Self-heal: this account's hash was computed on an un-trimmed
                # password (pre-normalization). Re-hash the trimmed form so the
                # "correct password rejected" bug cannot come back for them.
                # pw_changed_at is deliberately NOT touched: the credential did
                # not change, only its storage form, so the user's other devices
                # must stay signed in.
                salt = new_salt()
                c.execute("UPDATE users SET pass_hash=?, salt=? WHERE id=?",
                          (hash_pw(pw, salt), salt, u["id"]))
                c.commit()
            # One DELETE for both counters (used to be two round trips).
            authsec.throttle_clear_many(c, [ukey, ikey], commit=False)
            # Cleanup is kicked into a background worker — it NEVER runs inline
            # on the login path (it used to add 3 DELETEs to the first login
            # after every cold start, and on serverless _LAST_HOUSEKEEPING
            # resets with each new worker, so that was effectively every login).
            self._kick_housekeeping()
            uid = u["id"]
            token = self._make_session(c, uid)
            # Accounts that predate recovery codes get a set on their first
            # successful sign-in after this upgrade — the UI shows it once.
            # Issuance is BEST-EFFORT: codes are a convenience the user can
            # regenerate in Settings → Account Security, so a failure here must
            # never sink the login itself (the token is already minted).
            events = [("login_ok", uid, u["name"], ip, None)]
            codes = None
            rc_total = int(u["rc_total"] or 0) if _RC_TABLE_OK[0] is not False else None
            if rc_total == 0:
                try:
                    codes = issue_recovery_codes(c, uid, ip, log=False)
                    events.append(("recovery_codes_issued", uid, None, ip,
                                   "%d codes" % len(codes)))
                except Exception:
                    try: c.rollback()   # drop any half-written code rows
                    except Exception: pass
                    events.append(("recovery_codes_failed", uid, None, ip,
                                   "issuance failed on login; user can regenerate in Settings"))
            # login_ok (+ code event) in ONE batched INSERT, then commit.
            authsec.log_events(c, events)
            out = {"ok": True, "token": token}
            if codes: out["recoveryCodes"] = codes
            return self._send(out, extra_headers=[("Set-Cookie", self._cookie_header(token))])
        finally:
            c.close()

    # At most once an hour per worker — and only ever in the background.
    _LAST_HOUSEKEEPING = [0.0]
    _HK_BUSY = [False]
    _HK_LOCK = threading.Lock()

    def _kick_housekeeping(self):
        """Kick the once-an-hour cleanup into a BACKGROUND worker.

        Housekeeping used to run three extra DELETEs inline on the first login
        after every cold start — right on the latency-critical path where every
        statement is a Turso round trip against a 30 s budget. The request path
        now never waits on cleanup; if the worker freezes before the background
        thread finishes, the next worker simply tries again.
        """
        now = time.time()
        if now - Handler._LAST_HOUSEKEEPING[0] < 3600: return
        if not Handler._HK_LOCK.acquire(blocking=False): return
        try:
            if Handler._HK_BUSY[0]: return
            if time.time() - Handler._LAST_HOUSEKEEPING[0] < 3600: return
            Handler._HK_BUSY[0] = True
            Handler._LAST_HOUSEKEEPING[0] = time.time()
        finally:
            Handler._HK_LOCK.release()
        threading.Thread(target=Handler._housekeeping_bg, name="jwr-housekeeping",
                         daemon=True).start()

    @staticmethod
    def _housekeeping_bg():
        """Drop expired sessions / stale counters / old events — best effort."""
        try:
            c = db()
            try:
                purge_expired_sessions(c)
                authsec.throttle_housekeeping(c)
                authsec.prune_events(c)
            finally:
                c.close()
        except Exception:
            pass
        finally:
            Handler._HK_BUSY[0] = False

    def reset_password(self, body):
        """Self-service reset with a ONE-TIME RECOVERY CODE.

        The friend code is deliberately not accepted: the app tells every user
        to share it with up to 10 buddies, so anyone holding it could otherwise
        take over the account (that was the previous behaviour).
        """
        name = (body.get("name") or "").strip()
        rcode = body.get("recoveryCode")
        if not isinstance(rcode, str) or not rcode.strip():
            rcode = body.get("code")            # older clients send `code`
        newpw = body.get("newPassword")
        ip = self._client_ip()
        if not name: return self._err("Enter your name.")
        if not isinstance(newpw, str): return self._err("Invalid password.")
        if not isinstance(rcode, str) or not rcode.strip():
            return self._err("Enter one of your recovery codes.")
        # Shape check first: a 6-character friend code is rejected with an
        # explanation WITHOUT touching the database, so nothing about the
        # account's existence is revealed.
        if len(authsec.normalize_code(rcode)) < 8:
            return self._err(self.FRIEND_CODE_RETIRED)
        pol = authsec.password_policy_error(newpw, name)
        if pol: return self._err(pol)
        ukey = authsec.throttle_key("u", name)
        ikey = authsec.throttle_key("i", ip)
        rkey = authsec.throttle_key("r", ip)
        c = db()
        try:
            blocked = self._throttle_gate(c, [(ukey, "u", "Too many reset attempts"),
                                              (ikey, "i", "Too many attempts from your network"),
                                              (rkey, "r", "Too many recovery-code attempts")])
            if blocked: return blocked
            u = c.execute("SELECT * FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if not u:
                authsec.verify_pw(newpw, _DUMMY_HASH, _DUMMY_SALT)   # equalize timing
                authsec.throttle_fail(c, ukey, "u", commit=False)
                authsec.throttle_fail(c, rkey, "r", commit=False)
                wait = authsec.throttle_fail(c, ikey, "i")
                authsec.log_event(c, "reset_fail", name=name, ip=ip, detail="no such user")
                if wait > 0: return self._throttled(wait, "Too many reset attempts")
                return self._err(self.RESET_FAIL, 400)
            if not authsec.valid_code_shape(rcode) or not consume_recovery_code(c, u["id"], rcode, ip):
                authsec.throttle_fail(c, ukey, "u", commit=False)
                authsec.throttle_fail(c, rkey, "r", commit=False)
                wait = authsec.throttle_fail(c, ikey, "i")
                authsec.log_event(c, "reset_fail", user_id=u["id"], name=name, ip=ip,
                                  detail="bad recovery code")
                if wait > 0: return self._throttled(wait, "Too many reset attempts")
                return self._err(self.RESET_FAIL, 400)
            # The recovery code was correct: rotate the password, invalidate
            # EVERY session that existed before this moment (including one an
            # attacker may be holding), then hand out a fresh one.
            salt = new_salt()
            c.execute("UPDATE users SET pass_hash=?, salt=?, pw_changed_at=? WHERE id=?",
                      (hash_pw(newpw, salt), salt, now_iso(), u["id"]))
            c.commit()
            revoke_sessions(c, u["id"])
            token = self._make_session(c, u["id"])
            # all three counters in ONE DELETE (used to be three round trips)
            authsec.throttle_clear_many(c, [ukey, rkey, ikey])
            authsec.log_event(c, "reset_ok", user_id=u["id"], name=u["name"], ip=ip,
                              detail="sessions revoked")
            st = recovery_status(c, u["id"])
            return self._send({"ok": True, "token": token, "name": u["name"],
                               "recoveryRemaining": st["remaining"]},
                              extra_headers=[("Set-Cookie", self._cookie_header(token))])
        finally:
            c.close()

    # -------- account security (authenticated)
    def security_info(self, c, uid):
        return self._send({"recovery": recovery_status(c, uid),
                           "sessions": active_sessions(c, uid),
                           "minPasswordLength": authsec.PW_MIN})

    def regenerate_recovery_codes(self, c, uid, body):
        """Issue a brand-new set (old unused ones are destroyed). Returns the
        plaintext exactly once — the database only ever keeps digests."""
        codes = issue_recovery_codes(c, uid, self._client_ip())
        return self._send({"ok": True, "recoveryCodes": codes,
                           "recovery": recovery_status(c, uid)})

    def revoke_all_sessions(self, c, uid, body):
        """Log out every device except the one making this request."""
        keep = None
        tok = self._cookie_token() or self._bearer_token()
        if tok: keep = tok
        revoke_sessions(c, uid, keep_token=keep)
        authsec.log_event(c, "sessions_revoked", user_id=uid, ip=self._client_ip(),
                          detail="user initiated")
        return self._send({"ok": True, "sessions": active_sessions(c, uid)})


    def update_me(self, c, uid, body):
        fields = {}
        if "name" in body:
            nm = (body["name"] or "").strip()
            if not (2 <= len(nm) <= 30): return self._err("Invalid name")
            if c.execute("SELECT 1 FROM users WHERE name=? COLLATE NOCASE AND id!=?", (nm, uid)).fetchone():
                return self._err("Name already taken")
            fields["name"] = nm
        if "examDate" in body:
            if body["examDate"] and not valid_day(body["examDate"]): return self._err("Invalid date")
            fields["exam_date"] = body["examDate"] or None
        if "avatarColor" in body:
            if body["avatarColor"] in ("#f97316", "#22d3ee", "#a3e635", "#f472b6", "#facc15", "#818cf8", "#34d399", "#fb7185"):
                fields["avatar_color"] = body["avatarColor"]
        for k, v in fields.items():
            c.execute(f"UPDATE users SET {k}=? WHERE id=?", (v, uid))
        c.commit()
        return self._send({"ok": True})

    # -------- friends
    def friend_connect(self, c, user, body):
        code = (body.get("code") or "").strip().upper()
        if not re.fullmatch(r"[A-Z0-9]{4,10}", code): return self._err("Enter a valid friend code.")
        # A friend code identifies an account, so cap how fast one signed-in
        # user can trawl the code space looking for people.
        fkey = authsec.throttle_key("f", "u%d" % user["id"])
        wait = authsec.throttle_remaining(c, fkey)
        if wait: return self._throttled(wait, "Too many friend-code lookups")
        if code == user["code"]: return self._err("That's your own code.")
        pu = c.execute("SELECT * FROM users WHERE code=?", (code,)).fetchone()
        if not pu:
            authsec.throttle_fail(c, fkey, "f")
            return self._err("No user with that code.")
        authsec.throttle_clear(c, fkey)
        if are_friends(c, user["id"], pu["id"]):
            return self._send({"ok": True, "partner": pu["name"], "already": True})
        n = c.execute("SELECT COUNT(*) n FROM friendships WHERE user_id=?", (user["id"],)).fetchone()["n"]
        if n >= MAX_FRIENDS: return self._err(f"You can have at most {MAX_FRIENDS} friends. Remove one first.")
        n2 = c.execute("SELECT COUNT(*) n FROM friendships WHERE user_id=?", (pu["id"],)).fetchone()["n"]
        if n2 >= MAX_FRIENDS: return self._err("That person already has the maximum number of friends.")
        now = now_iso()
        c.execute("INSERT OR IGNORE INTO friendships(user_id,friend_id,created_at) VALUES(?,?,?)",
                  (user["id"], pu["id"], now))
        c.execute("INSERT OR IGNORE INTO friendships(user_id,friend_id,created_at) VALUES(?,?,?)",
                  (pu["id"], user["id"], now))
        c.commit()
        return self._send({"ok": True, "partner": pu["name"]})

    def friend_unlink(self, c, user, body=None):
        body = body or {}
        pid = body.get("uid") or user.get("partner_id")
        try: pid = int(pid)
        except Exception: pid = None
        if pid:
            c.execute("DELETE FROM friendships WHERE user_id=? AND friend_id=?", (user["id"], pid))
            c.execute("DELETE FROM friendships WHERE user_id=? AND friend_id=?", (pid, user["id"]))
            c.commit()
        return self._send({"ok": True})

    # -------- messaging
    def messages_list(self, c, uid, q):
        if q.get("with"):
            try: other = int(q["with"][0])
            except Exception: return self._err("Invalid friend")
            if not are_friends(c, uid, other): return self._err("Not connected with that user.", 403)
            after = int(q.get("after", ["0"])[0] or 0)
            rows = c.execute(f"""SELECT * FROM messages WHERE id>? AND {msg_visible(uid)} AND
                ((sender=? AND recipient=?) OR (sender=? AND recipient=?)) ORDER BY id ASC LIMIT 300""",
                (after, uid, other, other, uid)).fetchall()
            msgs = [{"id": r["id"], "from": r["sender"], "to": r["recipient"], "body": r["body"],
                     "at": r["created_at"], "mine": r["sender"] == uid} for r in rows]
            # thread summary + unread total
            last = msgs[-1] if msgs else None
            return {"messages": msgs, "friendUid": other, "last": last}
        # thread list: one summary per friend — single query for ALL friends
        # (the old per-friend loop was 3 queries x N friends = slow chat open)
        pids = friend_ids(c, uid)
        out = {pid: {"uid": pid, "unread": 0, "last": None} for pid in pids}
        if pids:
            vis = msg_visible(uid)
            ph = ",".join("?" * len(pids))
            rows = c.execute(f"""SELECT sender,recipient,body,created_at,read_at FROM messages
                WHERE {vis} AND (
                  (sender=? AND recipient IN ({ph})) OR
                  (recipient=? AND sender IN ({ph}))) ORDER BY id DESC""",
                [uid, *pids, uid, *pids]).fetchall()
            for r in rows:
                other = r["recipient"] if r["sender"] == uid else r["sender"]
                d = out.get(other)
                if not d: continue
                if d["last"] is None:
                    d["last"] = {"body": r["body"], "at": r["created_at"], "mine": r["sender"] == uid}
                if r["recipient"] == uid and r["read_at"] is None:
                    d["unread"] += 1
        return {"threads": [out[p] for p in pids]}

    def send_message(self, c, user, body):
        if not self._nonce_ok(c, user["id"], body): return
        try: to = int(body.get("to"))
        except Exception: return self._err("Choose a recipient.")
        text = (body.get("body") or "").strip()[:1000]
        if not text: return self._err("Message is empty.")
        if to == user["id"]: return self._err("You can't message yourself.")
        if not are_friends(c, user["id"], to): return self._err("Connect with that friend before messaging.", 403)
        now = now_iso()
        cur = c.execute("INSERT INTO messages(sender,recipient,body,created_at) VALUES(?,?,?,?)",
                        (user["id"], to, text, now))
        c.commit()
        result = {"ok": True, "id": cur.lastrowid, "at": now}
        self._nonce_save(c, user["id"], body.get("nonce"), result); c.commit()
        return self._send(result)

    def mark_read(self, c, uid, body):
        try: other = int(body.get("with"))
        except Exception: return self._err("Invalid friend")
        c.execute("UPDATE messages SET read_at=? WHERE recipient=? AND sender=? AND read_at IS NULL",
                  (now_iso(), uid, other))
        c.commit()
        return self._send({"ok": True})

    def clear_chat(self, c, uid, body):
        # Hide the whole conversation from THIS user only; the buddy keeps their copy.
        try: other = int(body.get("with"))
        except Exception: return self._err("Invalid friend")
        c.execute(f"""UPDATE messages SET hidden = CASE WHEN instr(','||COALESCE(hidden,'')||',', ',{uid},')=0
            THEN TRIM(COALESCE(hidden,'') || ',{uid}', ',') ELSE hidden END
            WHERE ((sender=? AND recipient=?) OR (sender=? AND recipient=?))""",
            (uid, other, other, uid))
        c.commit()
        return self._send({"ok": True})

    # -------- activity logging
    def log_activity(self, c, uid, s, body):
        if not self._nonce_ok(c, uid, body): return
        typ = body.get("type"); nonce = body.get("nonce")
        allowed = {a["key"] for a in s["activities"] if a.get("enabled", True)} | {ca["key"] for ca in s.get("customActivities", [])} | {"metric"}
        if typ not in allowed: return self._err("That activity is disabled or invalid.")
        subj = body.get("subject") or ""
        if subj and subj not in SUBJECTS: return self._err("Invalid subject")
        chap = body.get("chapter") or ""
        if chap and chap not in chapter_names(c, uid): return self._err("Pick a chapter from the list")
        raw_amount = int(body.get("amount") or 0)
        raw_duration = int(body.get("duration") or 0)
        if raw_amount < 0 or raw_duration < 0 or raw_amount > 1000 or raw_duration > 1440:
            return self._err("Value out of allowed range (max 1000 questions, 1440 minutes).")
        amount, duration = raw_amount, raw_duration
        day = body.get("day") or TODAY()
        if not valid_day(day) or day > TODAY(): return self._err("Invalid date (future logging not allowed).")
        note = (body.get("note") or "")[:300]; extra = None
        xp = s["xp"]; gained = 0; reason = typ
        names = {a["key"]: a for a in s["activities"]}

        if typ == "lecture":
            gained = xp["lecture"]; reason = "Lecture completed"
            touch_chapter(c, uid, subj, chap)
            if chap:
                row = c.execute("SELECT lectures_total,lectures_done FROM chapters WHERE user_id=? AND subject=? AND name=?",
                                (uid, subj, chap)).fetchone()
                if row:
                    new_done = row["lectures_done"] + 1
                    if row["lectures_total"] and new_done >= row["lectures_total"]:
                        c.execute("UPDATE chapters SET lectures_done=?,status='completed' WHERE user_id=? AND subject=? AND name=?",
                                  (row["lectures_total"], uid, subj, chap))
                    else:
                        c.execute("UPDATE chapters SET lectures_done=? WHERE user_id=? AND subject=? AND name=?",
                                  (new_done, uid, subj, chap))
        elif typ == "dpp":
            if amount <= 0: return self._err("Choose how many questions the DPP covered.")
            gained = xp["dpp"]; reason = "DPP completed"; touch_chapter(c, uid, subj, chap)
        elif typ == "homework":
            if amount <= 0: return self._err("Choose the number of questions.")
            gained = xp["homework"]; reason = "Homework completed"; touch_chapter(c, uid, subj, chap)
        elif typ == "pyq":
            if amount <= 0: return self._err("Choose the number of PYQs.")
            b50 = amount // 50; rem = amount % 50
            gained = b50 * round(xp["pyq25"] * 1.75) + (xp["pyq25"] if rem >= 25 else 0)
            reason = f"{amount} PYQs"; touch_chapter(c, uid, subj, chap)
        elif typ == "revision":
            if duration <= 0: return self._err("Choose revision duration.")
            gained = (duration // 30) * xp["revision30"]; reason = "Revision"
            touch_chapter(c, uid, subj, chap)
        elif typ == "study":
            if duration <= 0: return self._err("Choose duration.")
            gained = (duration // 25) * xp["focus25"]; reason = "Study session"; touch_chapter(c, uid, subj, chap)
        elif typ == "distraction":
            extra = body.get("distractionType") or "Other"
            if extra not in SYL.DISTRACTIONS: extra = "Other"
            if duration <= 0: return self._err("Choose distraction duration.")
            gained = xp["distraction"]; reason = f"Distraction: {extra}"
        elif typ == "error":
            et = body.get("errorType") or "Other"
            if et not in SYL.ERROR_TYPES: et = "Other"
            extra = et; gained = xp["error"]; reason = "Error analysis"; touch_chapter(c, uid, subj, chap)
        else:
            # custom activity or metric
            cdefs = {ca["key"]: ca for ca in s.get("customActivities", [])}
            mdefs = {m["key"]: m for m in s.get("metrics", [])}
            if typ in cdefs:
                d = cdefs[typ]; extra = d["label"]
                if d.get("kind") == "count":
                    if amount <= 0: return self._err("Choose an amount.")
                    gained = int(d.get("xp", 0)) * amount
                else:
                    if duration <= 0: return self._err("Choose duration.")
                    gained = int(d.get("xp", 0)) * (duration // 30 or 1)
            elif typ == "metric":
                mk = body.get("customKey"); md = mdefs.get(mk)
                if not md: return self._err("Unknown metric")
                extra = md["name"]
                if md.get("kind") == "duration":
                    if duration <= 0: return self._err("Choose duration.")
                elif md.get("kind") == "check":
                    amount = 1
                else:
                    if amount <= 0: return self._err("Choose an amount.")
                if md.get("xp"): gained = int(md.get("xpPer", 0)) * (amount or (duration // 30 or 1))
            else:
                return self._err("Unknown activity")

        cur = c.execute("""INSERT INTO activities(user_id,type,custom_key,subject,chapter,amount,duration,extra,note,day,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, typ, body.get("customKey"), subj, chap, amount, duration, extra, note, day,
             now_iso()))
        add_xp(c, uid, gained, reason, "activity", cur.lastrowid, s, commit=False)
        ensure_snapshots(c, uid, s, force_today=True)
        save_settings(c, uid, s); c.commit()
        result = {"ok": True, "id": cur.lastrowid, "xpGained": gained}
        self._nonce_save(c, uid, nonce, result)
        c.commit()
        return self._send(result)

    # -------- targets
    def _target_from_item(self, c, uid, day, it):
        title = (it.get("title") or "").strip()[:120]
        kind = it.get("kind") or "Task"
        if not title:
            title = " ".join(x for x in [it.get("subject"), kind] if x)
        subj = it.get("subject") or ""
        if subj and subj not in SUBJECTS: subj = ""
        chap = it.get("chapter") or ""
        if chap and chap not in chapter_names(c, uid): chap = ""
        amount = clamp(int(it.get("amount") or 0), 0, 1000)
        dur = clamp(int(it.get("duration") or 0), 0, 600)
        cur = c.execute("""INSERT INTO targets(user_id,day,kind,subject,chapter,title,amount,duration,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,'open',?)""",
            (uid, day, kind, subj, chap, title, amount or None, dur or None,
             now_iso()))
        return cur.lastrowid

    def create_target(self, c, uid, body):
        if not self._nonce_ok(c, uid, body): return
        day = body.get("day") or TODAY()
        if not valid_day(day) or day > TODAY(): return self._err("Invalid date")
        items = body.get("items")
        if not items or not isinstance(items, list): items = [body]
        ids = []
        for it in items[:50]:
            ids.append(self._target_from_item(c, uid, day, it))
        c.commit()
        result = {"ok": True, "ids": ids}
        self._nonce_save(c, uid, body.get("nonce"), result); c.commit()
        return self._send(result)

    def apply_template(self, c, uid, s, body):
        if not self._nonce_ok(c, uid, body): return
        day = body.get("day") or TODAY()
        if not valid_day(day) or day > TODAY(): return self._err("Invalid date")
        tid = body.get("templateId")
        tpl = next((t for t in s["templates"] if t["id"] == tid), None)
        if not tpl: return self._err("Template not found")
        existing = c.execute("SELECT COUNT(*) n FROM targets WHERE user_id=? AND day=?", (uid, day)).fetchone()["n"]
        if existing >= 40: return self._err("Too many targets for that day.")
        ids = [self._target_from_item(c, uid, day, it) for it in tpl["items"]]
        c.commit()
        result = {"ok": True, "ids": ids}
        self._nonce_save(c, uid, body.get("nonce"), result); c.commit()
        return self._send(result)

    def patch_target(self, c, uid, s, tid, body):
        r = c.execute("SELECT * FROM targets WHERE id=? AND user_id=?", (tid, uid)).fetchone()
        if not r: return self._err("Target not found", 404)
        if "status" in body:
            st = body["status"]
            if st not in ("open", "done", "partial", "missed"): return self._err("Invalid status")
            c.execute("UPDATE targets SET status=?, completed_at=? WHERE id=?",
                      (st, now_iso() if st == "done" else None, tid))
            # daily 100% bonus
            day = r["day"]
            tr = c.execute("SELECT status FROM targets WHERE user_id=? AND day=?", (uid, day)).fetchall()
            if tr and all(x["status"] == "done" for x in tr):
                add_xp(c, uid, s["xp"]["day100"], "100% day", "day100", day, s, commit=False)
        if "title" in body:
            t = (body["title"] or "").strip()[:120]
            if t: c.execute("UPDATE targets SET title=? WHERE id=?", (t, tid))
        for fk, f in (("subject", "subject"), ("chapter", "chapter"), ("kind", "kind")):
            if fk in body:
                v = body[fk] or ""
                if f == "subject" and v and v not in SUBJECTS: v = ""
                if f == "chapter" and v and v not in chapter_names(c, uid): v = ""
                c.execute(f"UPDATE targets SET {f}=? WHERE id=?", (v, tid))
        ensure_snapshots(c, uid, s, force_today=True); save_settings(c, uid, s); c.commit()
        return self._send({"ok": True})

    def dup_target(self, c, uid, tid, body):
        r = c.execute("SELECT * FROM targets WHERE id=? AND user_id=?", (tid, uid)).fetchone()
        if not r: return self._err("Target not found", 404)
        day = body.get("day") or TODAY()
        if not valid_day(day) or day > TODAY(): return self._err("Invalid date")
        cur = c.execute("""INSERT INTO targets(user_id,day,kind,subject,chapter,title,amount,duration,status,created_at)
            VALUES(?,?,?,?,?,?,?,?,'open',?)""",
            (uid, day, r["kind"], r["subject"], r["chapter"], r["title"], r["amount"], r["duration"],
             now_iso()))
        c.commit()
        return self._send({"ok": True, "id": cur.lastrowid})

    def delete_target(self, c, uid, tid):
        c.execute("DELETE FROM targets WHERE id=? AND user_id=?", (tid, uid)); c.commit()
        return self._send({"ok": True})

    # -------- chapters
    def chapter_add(self, c, uid, body):
        subj = body.get("subject"); name = (body.get("name") or "").strip()[:80]
        if subj not in SUBJECTS or len(name) < 1: return self._err("Invalid chapter")
        mx = c.execute("SELECT COALESCE(MAX(sort),0) m FROM chapters WHERE user_id=? AND subject=?", (uid, subj)).fetchone()["m"]
        cur = c.execute("INSERT INTO chapters(user_id,subject,name,status,sort,custom) VALUES(?,?,?,?,?,1)",
                        (uid, subj, name, "not_started", mx + 10))
        c.commit()
        return self._send({"ok": True, "id": cur.lastrowid})

    def chapter_patch(self, c, uid, cid, body):
        r = c.execute("SELECT * FROM chapters WHERE id=? AND user_id=?", (cid, uid)).fetchone()
        if not r: return self._err("Chapter not found", 404)
        new_st = body.get("status")
        old_st = r["status"]
        if new_st in ("not_started", "in_progress", "completed", "revision_needed"):
            c.execute("UPDATE chapters SET status=? WHERE id=?", (new_st, cid))
            if new_st in ("completed", "in_progress") and old_st != new_st:
                today_act = c.execute("SELECT 1 FROM activities WHERE user_id=? AND chapter=? AND day=?",
                                      (uid, r["name"], TODAY())).fetchone()
                if not today_act:
                    c.execute("""INSERT INTO activities(user_id,type,subject,chapter,amount,duration,extra,note,day,created_at)
                        VALUES(?,?,?,?,0,30,?,?,?,?)""",
                        (uid, "study", r["subject"], r["name"], "Syllabus progress", f"Chapter {new_st.replace('_',' ')}: {r['name']}", TODAY(), now_iso()))
                if new_st == "completed" and old_st != "completed":
                    s = get_settings(c, uid)
                    add_xp(c, uid, 50, f"Completed chapter: {r['name']}", "chapter", cid, s, commit=False)
                    ensure_snapshots(c, uid, s, force_today=True)
                    save_settings(c, uid, s)
        if "name" in body:
            nm = (body["name"] or "").strip()[:80]
            if nm: c.execute("UPDATE chapters SET name=? WHERE id=?", (nm, cid))
        if "hidden" in body:
            c.execute("UPDATE chapters SET hidden=? WHERE id=?", (1 if body["hidden"] else 0, cid))
        if "lecturesTotal" in body:
            try: tot = clamp(int(body["lecturesTotal"]), 0, 500)
            except Exception: tot = 0
            c.execute("UPDATE chapters SET lectures_total=? WHERE id=?", (tot, cid))
            if tot == 0:
                c.execute("UPDATE chapters SET lectures_done=0 WHERE id=?", (cid,))
        if "lecturesDone" in body:
            try:
                done = clamp(int(body["lecturesDone"]), 0, 500)
                tot = r["lectures_total"]
                if tot and done > tot: done = tot
                c.execute("UPDATE chapters SET lectures_done=? WHERE id=?", (done, cid))
                if tot and done >= tot and r["status"] != "completed":
                    c.execute("UPDATE chapters SET status='completed' WHERE id=?", (cid,))
                    today_act = c.execute("SELECT 1 FROM activities WHERE user_id=? AND chapter=? AND day=?",
                                          (uid, r["name"], TODAY())).fetchone()
                    if not today_act:
                        c.execute("""INSERT INTO activities(user_id,type,subject,chapter,amount,duration,extra,note,day,created_at)
                            VALUES(?,?,?,?,0,30,?,?,?,?)""",
                            (uid, "study", r["subject"], r["name"], "Syllabus progress", f"Chapter completed: {r['name']}", TODAY(), now_iso()))
                    s = get_settings(c, uid)
                    add_xp(c, uid, 50, f"Completed chapter: {r['name']}", "chapter", cid, s, commit=False)
                    ensure_snapshots(c, uid, s, force_today=True)
                    save_settings(c, uid, s)
                elif done > 0 and r["status"] == "not_started":
                    c.execute("UPDATE chapters SET status='in_progress' WHERE id=?", (cid,))
            except Exception: pass
        if body.get("remove"):
            c.execute("DELETE FROM chapters WHERE id=? AND user_id=? AND custom=1", (cid, uid))
        c.commit()
        return self._send({"ok": True})

    def chapter_bulk(self, c, uid, body):
        subj = body.get("subject"); status = body.get("status")
        if subj not in SUBJECTS: return self._err("Invalid subject")
        if status not in ("not_started", "in_progress", "completed", "revision_needed"):
            return self._err("Invalid status")
        only_status = body.get("onlyStatus")
        q = "UPDATE chapters SET status=? WHERE user_id=? AND subject=? AND hidden=0"
        args = [status, uid, subj]
        if only_status in ("not_started", "in_progress", "completed", "revision_needed"):
            q += " AND status=?"; args.append(only_status)
        n = c.execute(q, args).rowcount
        c.commit()
        return self._send({"ok": True, "changed": n})

    def chapter_move(self, c, uid, cid, body):
        r = c.execute("SELECT * FROM chapters WHERE id=? AND user_id=?", (cid, uid)).fetchone()
        if not r: return self._err("Chapter not found", 404)
        rows = c.execute("SELECT id,sort FROM chapters WHERE user_id=? AND subject=? AND hidden=0 ORDER BY sort,id",
                         (uid, r["subject"])).fetchall()
        ids = [x["id"] for x in rows]; i = ids.index(cid)
        j = i + (1 if body.get("dir") == "down" else -1)
        if 0 <= j < len(ids):
            ids[i], ids[j] = ids[j], ids[i]
            for k, rid in enumerate(ids):
                c.execute("UPDATE chapters SET sort=? WHERE id=?", (k * 10, rid))
            c.commit()
        return self._send({"ok": True})

    # -------- mocks / errors
    def add_mock(self, c, uid, s, body):
        if not self._nonce_ok(c, uid, body): return
        tt = body.get("testType") or "Full JEE Main"
        if tt not in SYL.TEST_TYPES: tt = "Custom"
        total = clamp(int(body.get("total") or 300), 30, 900)
        def num(k):
            v = body.get(k); return float(v) if v not in (None, "") else None
        attempted = int(body.get("attempted") or 0)
        correct = int(body.get("correct") or 0)
        incorrect = int(body.get("incorrect") or 0)
        if attempted < 0 or correct < 0 or incorrect < 0 or attempted > 100: return self._err("Invalid question counts.")
        if correct > attempted or incorrect > attempted: return self._err("Correct/incorrect cannot exceed attempted.")
        if attempted and correct + incorrect > attempted + 1: return self._err("Correct + incorrect exceed attempted.")
        phys, chem, math_ = num("phys"), num("chem"), num("math")
        score = num("score")
        if score is None and phys is not None: score = (phys or 0) + (chem or 0) + (math_ or 0)
        if score is None and attempted: score = correct * 4 - incorrect  # JEE Main +4/-1 fallback
        if score is not None and (score < -100 or score > total + 1): return self._err("Score outside possible range.")
        day = body.get("day") or TODAY()
        if not valid_day(day) or day > TODAY(): return self._err("Invalid date")
        cur = c.execute("""INSERT INTO mocks(user_id,test_type,total,score,phys,chem,math,attempted,correct,incorrect,day,note,created_at)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (uid, tt, total, score, phys, chem, math_, attempted, correct, incorrect, day,
             (body.get("note") or "")[:200], now_iso()))
        ts = now_iso()
        c.execute("""INSERT INTO activities(user_id,type,custom_key,subject,chapter,amount,duration,extra,note,day,created_at)
            VALUES(?,?,?,?,?,0,0,?,?,?,?)""",
            (uid, "mock", None, "", "", tt, f"{tt} ({day})", day, ts))
        add_xp(c, uid, s["xp"]["mock"], "Mock test", "mock", cur.lastrowid, s, commit=False)
        ensure_snapshots(c, uid, s, force_today=True); save_settings(c, uid, s); c.commit()
        result = {"ok": True, "id": cur.lastrowid}
        self._nonce_save(c, uid, body.get("nonce"), result); c.commit()
        return self._send(result)

    def add_error(self, c, uid, s, body):
        if not self._nonce_ok(c, uid, body): return
        subj = body.get("subject") or ""
        if subj not in SUBJECTS: return self._err("Choose a subject")
        chap = body.get("chapter") or ""
        if chap and chap not in chapter_names(c, uid): return self._err("Pick a chapter from the list")
        et = body.get("errorType") or "Other"
        if et not in SYL.ERROR_TYPES: et = "Other"
        day = body.get("day") or TODAY()
        if not valid_day(day) or day > TODAY(): return self._err("Invalid date")
        cur = c.execute("""INSERT INTO errors(user_id,subject,chapter,etype,qno,note,day,created_at)
            VALUES(?,?,?,?,?,?,?,?)""",
            (uid, subj, chap, et, (body.get("qno") or "")[:20], (body.get("note") or "")[:300], day,
             now_iso()))
        c.execute("INSERT INTO activities(user_id,type,subject,chapter,amount,duration,extra,note,day,created_at) VALUES(?,?,?,?,0,0,?,?,?,?)",
                  (uid, "error", subj, chap, et, (body.get("note") or "")[:300], day, now_iso()))
        add_xp(c, uid, s["xp"]["error"], "Error analysis", "error", cur.lastrowid, s, commit=False)
        ensure_snapshots(c, uid, s, force_today=True); save_settings(c, uid, s); c.commit()
        result = {"ok": True, "id": cur.lastrowid}
        self._nonce_save(c, uid, body.get("nonce"), result); c.commit()
        return self._send(result)

    def delete_owned(self, c, uid, table, rid, reverse_xp=None):
        col = {"mocks": "mock", "errors": "error"}.get(table)
        r = c.execute(f"SELECT * FROM {table} WHERE id=? AND user_id=?", (rid, uid)).fetchone()
        if not r: return self._err("Not found", 404)
        if reverse_xp:
            c.execute("DELETE FROM xp_events WHERE user_id=? AND ref_type=? AND ref_id=?", (uid, col, str(rid)))
            c.execute("DELETE FROM activities WHERE user_id=? AND type=? AND day=?", (uid, col, r["day"]))
        c.execute(f"DELETE FROM {table} WHERE id=? AND user_id=?", (rid, uid))
        c.commit()
        return self._send({"ok": True})

    # -------- timer (server-verified durations; no fake logs)
    def timer_start(self, c, uid, body):
        subj = body.get("subject") or ""
        if subj and subj not in SUBJECTS: subj = ""
        chap = body.get("chapter") or ""
        if chap and chap not in chapter_names(c, uid): chap = ""
        cur = c.execute("INSERT INTO timers(user_id,subject,chapter,started,logged) VALUES(?,?,?,?,0)",
                        (uid, subj, chap, time.time()))
        c.commit()
        return self._send({"ok": True, "id": cur.lastrowid, "started": time.time()})

    def timer_complete(self, c, uid, s, body):
        if not self._nonce_ok(c, uid, body): return
        tid = int(body.get("id") or 0)
        r = c.execute("SELECT * FROM timers WHERE id=? AND user_id=? AND logged=0", (tid, uid)).fetchone()
        if not r: return self._err("Session not found or already logged.", 404)
        running = int(body.get("runningSeconds") or 0)
        wall = int(time.time() - r["started"])
        if running < 55: return self._err("Sessions under 1 minute aren't logged.")
        if running > wall + 120: return self._err("Session duration mismatch — not logged.")
        running = min(running, wall + 5, 180 * 60)
        mins = max(1, round(running / 60))
        cur = c.execute("""INSERT INTO activities(user_id,type,custom_key,subject,chapter,amount,duration,extra,note,day,created_at)
            VALUES(?,?,?,?,?,0,?,?,?,?,?)""",
            (uid, "study", None, r["subject"] or "", r["chapter"] or "", mins,
             "Focus timer", f"{mins} min focus session", TODAY(),
             now_iso()))
        gained = (mins // 25) * s["xp"]["focus25"]
        add_xp(c, uid, gained, f"Focus session {mins}m", "timer", tid, s, commit=False)
        touch_chapter(c, uid, r["subject"], r["chapter"])
        c.execute("UPDATE timers SET logged=1,running=?,ended_at=? WHERE id=?",
                  (running, now_iso(), tid))
        ensure_snapshots(c, uid, s, force_today=True); save_settings(c, uid, s); c.commit()
        result = {"ok": True, "minutes": mins, "xpGained": gained}
        self._nonce_save(c, uid, body.get("nonce"), result); c.commit()
        return self._send(result)

    def timer_cancel(self, c, uid, body):
        tid = int(body.get("id") or 0)
        c.execute("UPDATE timers SET logged=1 WHERE id=? AND user_id=?", (tid, uid)); c.commit()
        return self._send({"ok": True})

    # -------- settings
    def is_admin(self, c, uid):
        r = c.execute("SELECT role FROM users WHERE id=?", (uid,)).fetchone()
        return bool(r and r["role"] == "admin")

    # -------- announcements (admin broadcasts; everyone reads)
    def announcement_create(self, c, uid, body):
        if not self.is_admin(c, uid): return self._err("Only the admin can post announcements.", 403)
        text = (body.get("body") or "").strip()
        if not (1 <= len(text) <= 600): return self._err("Announcement must be 1–600 characters.")
        now = now_iso()
        cur = c.execute("INSERT INTO announcements(body,created_at) VALUES(?,?)", (text, now))
        c.commit()
        return self._send({"ok": True, "id": cur.lastrowid, "at": now})

    def announcement_read(self, c, uid, s, body):
        aid = body.get("id")
        seen = list(s.get("seenAnnouncements") or [])
        if aid not in seen:
            seen.append(aid); s["seenAnnouncements"] = seen
            save_settings(c, uid, s); c.commit()
        return self._send({"ok": True})

    def announcement_delete(self, c, uid, aid):
        if not self.is_admin(c, uid): return self._err("Only the admin can delete announcements.", 403)
        c.execute("DELETE FROM announcements WHERE id=?", (aid,)); c.commit()
        return self._send({"ok": True})

    # -------- user problem reports
    def report_create(self, c, uid, body):
        if not self._nonce_ok(c, uid, body): return
        text = (body.get("body") or "").strip()
        if not (5 <= len(text) <= 600): return self._err("Describe the problem in at least 5 characters (max 600).")
        now = now_iso()
        cur = c.execute("INSERT INTO reports(user_id,body,created_at,resolved) VALUES(?,?,?,0)", (uid, text, now))
        c.commit()
        result = {"ok": True, "id": cur.lastrowid}
        self._nonce_save(c, uid, body.get("nonce"), result); c.commit()
        return self._send(result)

    def report_resolve(self, c, uid, body):
        if not self.is_admin(c, uid): return self._err("Admin only.", 403)
        rid = body.get("id")
        c.execute("UPDATE reports SET resolved=1 WHERE id=?", (rid,)); c.commit()
        return self._send({"ok": True})

    def _set_password(self, c, uid, newpw, keep_token=None, actor_id=None,
                      ip=None, kind="password_changed", target_name=None):
        """Single code path for every password write (self-change, self-reset,
        admin-set) so the policy, the salt rotation, the session revocation and
        the audit entry can never be skipped by one of them."""
        salt = new_salt()
        now = now_iso()
        c.execute("UPDATE users SET pass_hash=?, salt=?, pw_changed_at=? WHERE id=?",
                  (hash_pw(newpw, salt), salt, now, uid))
        c.commit()
        # Any session minted before this password change is destroyed: whoever
        # held it (including an attacker) loses access at the same moment.
        revoke_sessions(c, uid, keep_token=keep_token)
        if keep_token:
            # the surviving session must not look older than pw_changed_at, or
            # the _auth() freshness guard would reject it straight away
            c.execute("UPDATE sessions SET created_at=?, expires_at=? WHERE token=?",
                      (now, time.time() + authsec.SESSION_TTL_SECONDS,
                       authsec.hash_token(keep_token)))
            c.commit()
        authsec.log_event(c, kind, user_id=uid, name=target_name, ip=ip,
                          detail=("by admin id=%s" % actor_id) if actor_id else None)
        return salt

    def admin_set_password(self, c, uid, body):
        if not self.is_admin(c, uid): return self._err("Admin only.", 403)
        target = body.get("userId")
        try: target = int(target)
        except Exception: pass
        newpw = body.get("password")
        if not isinstance(newpw, str): return self._err("Invalid password.")
        tr = c.execute("SELECT id,name FROM users WHERE id=?", (target,)).fetchone() if target is not None else None
        if not tr: return self._err("User not found.")
        pol = authsec.password_policy_error(newpw, tr["name"])
        if pol: return self._err(pol)
        # Changing somebody else's password signs them out everywhere on
        # purpose — the old credential must stop working immediately.
        keep = None
        if target == uid:
            keep = self._cookie_token() or self._bearer_token()
        self._set_password(c, target, newpw, keep_token=keep, actor_id=uid,
                           ip=self._client_ip(), kind="admin_set_password",
                           target_name=tr["name"])
        return self._send({"ok": True, "revokedSessions": not keep})

    def change_my_password(self, c, uid, body):
        curpw = body.get("currentPassword")
        newpw = body.get("newPassword")
        ip = self._client_ip()
        if not isinstance(newpw, str): return self._err("Invalid password.")
        u = c.execute("SELECT id,name,pass_hash,salt FROM users WHERE id=?", (uid,)).fetchone()
        if not u: return self._err("User not found.", 404)
        pol = authsec.password_policy_error(newpw, u["name"])
        if pol: return self._err(pol)
        is_adm = self.is_admin(c, uid)
        curpw = curpw if isinstance(curpw, str) else ""
        if not is_adm or curpw:
            ok, legacy = authsec.verify_pw(curpw, u["pass_hash"], u["salt"])
            if not ok:
                authsec.log_event(c, "pw_change_fail", user_id=uid, name=u["name"], ip=ip,
                                  detail="wrong current password")
                return self._err("Current password is incorrect.", 400)
        if authsec.verify_pw(newpw, u["pass_hash"], u["salt"])[0]:
            return self._err("New password must be different from the current one.")
        # keep THIS device signed in; every other session is revoked
        keep = self._cookie_token() or self._bearer_token()
        self._set_password(c, uid, newpw, keep_token=keep, ip=ip,
                           kind="password_changed", target_name=u["name"])
        return self._send({"ok": True})

    def update_settings(self, c, uid, s, body):
        section = body.get("section")
        data = body.get("data")
        if not isinstance(data, dict): return self._err("Invalid settings")
        is_admin = self.is_admin(c, uid)
        if section == "global":
            if not is_admin: return self._err("Only the admin can change global rules.", 403)
            g = admin_settings(c)
            if isinstance(data.get("xp"), dict):
                for k, v in data["xp"].items():
                    if k in g["xp"]:
                        try: g["xp"][k] = clamp(int(v), -100, 500)
                        except Exception: pass
            if isinstance(data.get("weights"), dict):
                for k, v in data["weights"].items():
                    if k in g["weights"]:
                        try: g["weights"][k] = clamp(int(v), 0, 60)
                        except Exception: pass
            if isinstance(data.get("general"), dict):
                gen = data["general"]
                if "streakThreshold" in gen:
                    try: g["streakThreshold"] = clamp(int(gen["streakThreshold"]), 10, 100)
                    except Exception: pass
                if "airEMA" in gen:
                    try: g["airEMA"] = clamp(float(gen["airEMA"]), 0.5, 0.97)
                    except Exception: pass
            if isinstance(data.get("activitiesEnabled"), dict):
                for k, v in data["activitiesEnabled"].items():
                    if k in g["activitiesEnabled"]: g["activitiesEnabled"][k] = bool(v)
            save_admin_settings(c, g)
            return self._send({"ok": True, "settings": get_settings(c, uid), "global": g})
        # sections below are admin-controlled global rules
        if section in ("xp", "weights", "general"):
            if not is_admin: return self._err("Only the admin can change this global rule.", 403)
            g = admin_settings(c)
            payload = {"xp": data} if section == "xp" else ({"weights": data} if section == "weights" else {"general": data})
            if section == "xp":
                for k, v in data.items():
                    if k in g["xp"]:
                        try: g["xp"][k] = clamp(int(v), -100, 500)
                        except Exception: pass
            elif section == "weights":
                for k, v in data.items():
                    if k in g["weights"]:
                        try: g["weights"][k] = clamp(int(v), 0, 60)
                        except Exception: pass
            else:
                if "streakThreshold" in data:
                    try: g["streakThreshold"] = clamp(int(data["streakThreshold"]), 10, 100)
                    except Exception: pass
                if "airEMA" in data:
                    try: g["airEMA"] = clamp(float(data["airEMA"]), 0.5, 0.97)
                    except Exception: pass
            save_admin_settings(c, g)
            return self._send({"ok": True, "settings": get_settings(c, uid), "global": g})
        if section == "activities" and isinstance(data.get("enabled"), dict):
            if not is_admin: return self._err("Only the admin can enable/disable activity types globally.", 403)
            g = admin_settings(c)
            for k, v in data["enabled"].items():
                if k in g["activitiesEnabled"]: g["activitiesEnabled"][k] = bool(v)
            save_admin_settings(c, g)
            return self._send({"ok": True, "settings": get_settings(c, uid), "global": g})
        if section == "xp":
            for k, v in data.items():
                if k in s["xp"]:
                    try: s["xp"][k] = clamp(int(v), -100, 500)
                    except Exception: pass
        elif section == "weights":
            for k, v in data.items():
                if k in s["weights"]:
                    try: s["weights"][k] = clamp(int(v), 0, 60)
                    except Exception: pass
        elif section == "sharing":
            for k, v in data.items():
                if k in s["sharing"]: s["sharing"][k] = bool(v)
        elif section == "cards":
            order = data.get("order")
            if isinstance(order, list):
                by = {x["key"]: x for x in s["cards"]}
                new = []
                for k in order:
                    if k in by: new.append(by[k])
                new += [x for x in s["cards"] if x["key"] not in order]
                s["cards"] = new
            if isinstance(data.get("enabled"), dict):
                for card in s["cards"]:
                    if card["key"] in data["enabled"]: card["enabled"] = bool(data["enabled"][card["key"]])
        elif section == "activities":
            if isinstance(data.get("enabled"), dict):
                for a in s["activities"]:
                    if a["key"] in data["enabled"]: a["enabled"] = bool(data["enabled"][a["key"]])
            if data.get("add"):
                nm = str(data["add"].get("label", "")).strip()[:24]
                kind = data["add"].get("kind", "count")
                if nm and kind in ("count", "duration"):
                    key = "c" + hashlib.sha1(nm.encode()).hexdigest()[:8]
                    if not any(a["key"] == key for a in s["customActivities"]):
                        emoji = data["add"].get("emoji") or "✨"
                        s["customActivities"].append({"key": key, "label": nm, "emoji": emoji[:2], "kind": kind,
                                                      "enabled": True, "xp": clamp(int(data["add"].get("xp") or 0), 0, 100)})
            if data.get("remove"):
                s["customActivities"] = [a for a in s["customActivities"] if a["key"] != data["remove"]]
        elif section == "metrics":
            act = data.get("act"); md = data.get("metric", {})
            if act == "add":
                nm = str(md.get("name", "")).strip()[:24]; unit = str(md.get("unit", "")).strip()[:12]
                kind = md.get("kind", "count")
                if nm and kind in ("count", "duration", "check"):
                    key = "m" + hashlib.sha1((nm + str(time.time())).encode()).hexdigest()[:8]
                    s["metrics"].append({"key": key, "name": nm, "unit": unit, "kind": kind,
                                         "stats": bool(md.get("stats")), "xp": bool(md.get("xp")),
                                         "air": bool(md.get("air")), "xpPer": clamp(int(md.get("xpPer") or 0), 0, 100),
                                         "hidden": False})
            elif act == "update":
                for m in s["metrics"]:
                    if m["key"] == md.get("key"):
                        for f in ("name", "unit"):
                            if f in md: m[f] = str(md[f])[:24]
                        for f in ("stats", "xp", "air", "hidden"):
                            if f in md: m[f] = bool(md[f])
                        if "xpPer" in md: m["xpPer"] = clamp(int(md["xpPer"] or 0), 0, 100)
            elif act == "remove":
                s["metrics"] = [m for m in s["metrics"] if m["key"] != md.get("key")]
            elif act == "reorder":
                order = data.get("order") or []
                if isinstance(order, list):
                    by = {m["key"]: m for m in s["metrics"]}
                    new = [by[k] for k in order if k in by]
                    new += [m for m in s["metrics"] if m["key"] not in order]
                    s["metrics"] = new
        elif section == "templates":
            act = data.get("act"); td = data.get("template", {})
            if act == "add":
                tid = "t" + hashlib.sha1(str(time.time()).encode()).hexdigest()[:8]
                if td.get("name") and isinstance(td.get("items"), list):
                    s["templates"].append({"id": tid, "name": str(td["name"])[:30], "items": td["items"][:20]})
            elif act == "update":
                for t in s["templates"]:
                    if t["id"] == td.get("id"):
                        if td.get("name"): t["name"] = str(td["name"])[:30]
                        if isinstance(td.get("items"), list): t["items"] = td["items"][:20]
            elif act == "remove":
                s["templates"] = [t for t in s["templates"] if t["id"] != td.get("id")]
        elif section == "general":
            if "streakThreshold" in data:
                try: s["streakThreshold"] = clamp(int(data["streakThreshold"]), 10, 100)
                except Exception: pass
            if "airEMA" in data:
                try: s["airEMA"] = clamp(float(data["airEMA"]), 0.5, 0.97)
                except Exception: pass
        else:
            return self._err("Unknown settings section")
        save_settings(c, uid, s); c.commit()
        return self._send({"ok": True, "settings": s})

    def wipe(self, c, uid):
        for t in ("activities", "targets", "mocks", "errors", "xp_events", "snapshots", "timers", "chapters"):
            c.execute(f"DELETE FROM {t} WHERE user_id=?", (uid,))
        s = default_settings(); s["sweepDay"] = TODAY()
        save_settings(c, uid, s)
        seed_chapters(c, uid)   # one round-trip instead of 61
        c.execute("INSERT OR REPLACE INTO snapshots(user_id,day,score,air) VALUES(?,?,0,600000)", (uid, TODAY()))
        c.commit()
        return self._send({"ok": True})

class ThreadedServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True; allow_reuse_address = True

def backup_db(keep=24):
    """Safe on-disk snapshot using SQLite's online backup API (never blocks writes).
    No-op in cloud mode: Turso is the durable store; GitHub Actions takes dumps."""
    if dbwrap.CLOUD:
        return None
    try:
        bdir = os.path.join(BASE, "data", "backups")
        os.makedirs(bdir, exist_ok=True)
        dst = os.path.join(bdir, "warroom-" + datetime.now().strftime("%Y%m%d-%H%M%S") + ".db")
        src = sqlite3.connect(DB_PATH, timeout=30)
        out = sqlite3.connect(dst)
        with out: src.backup(out)
        out.close(); src.close()
        olds = sorted((os.path.join(bdir, f) for f in os.listdir(bdir) if f.endswith(".db")),
                      key=os.path.getmtime)
        for f in olds[:-keep]:
            try: os.remove(f)
            except OSError: pass
        return dst
    except Exception as e:
        print("backup failed:", e); return None

def backup_loop():
    import threading
    def tick():
        backup_db()
        threading.Timer(3600, tick).start()
    # first hourly snapshot 1h after boot (backup_db() already ran at startup)
    threading.Timer(3600, tick).start()

if __name__ == "__main__":
    init_db()
    BOOT_INFO["ok"] = True
    backup_db()          # snapshot at every boot
    backup_loop()        # then hourly
    print(f"JEE WAR ROOM running on http://0.0.0.0:{PORT}")
    ThreadedServer(("0.0.0.0", PORT), Handler).serve_forever()
