#!/usr/bin/env python3
"""
JEE WAR ROOM — zero-dependency backend.
Python stdlib HTTP server + SQLite (real persistent server-side database).
Auth: PBKDF2 password hashing + opaque server-side session cookies.
"""
import http.server, socketserver, json, sqlite3, os, re, time, math, random, string
import hashlib, hmac as hmac_mod
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
      partner_id INTEGER, exam_date TEXT, avatar_color TEXT, created_at TEXT);
    CREATE TABLE IF NOT EXISTS sessions(
      token TEXT PRIMARY KEY, user_id INTEGER NOT NULL, created_at TEXT);
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
    """

def init_db():
    c = db()
    if dbwrap.CLOUD:
        # Fast path: if reports table exists, schema is already complete
        try:
            c.execute("SELECT 1 FROM reports LIMIT 1").fetchone()
            c.close()
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
        user_cols = {r[0] for r in c.execute(
            "SELECT name FROM pragma_table_info('users')").fetchall()}
        if "role" not in user_cols:
            c.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")
            c.execute("UPDATE users SET role='admin' WHERE id=(SELECT MIN(id) FROM users)")
        ch_cols = {r[0] for r in c.execute(
            "SELECT name FROM pragma_table_info('chapters')").fetchall()}
        if "lectures_total" not in ch_cols:
            c.execute("ALTER TABLE chapters ADD COLUMN lectures_total INTEGER NOT NULL DEFAULT 0")
        if "lectures_done" not in ch_cols:
            c.execute("ALTER TABLE chapters ADD COLUMN lectures_done INTEGER NOT NULL DEFAULT 0")
        c.commit(); c.close()
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
    c.commit(); c.close()

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

def hash_pw(pw, salt):
    return hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()

def gen_code(n=6):
    while True:
        code = "".join(random.choices(string.ascii_uppercase + string.digits, k=n))
        code = code.replace("0", "X").replace("O", "Y")
        yield code

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

    def _send(self, obj, code=200, extra_headers=None):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra_headers or []): self.send_header(k, v)
        self.end_headers(); self.wfile.write(body)

    def _err(self, msg, code=400): self._send({"error": msg}, code)

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

    def _cookie_header(self, token, max_age=7776000, clear=False):
        if clear:
            extra = "SameSite=None; Secure" if self._is_secure() else "SameSite=Lax"
            return f"jwr_sess=; Path=/; Max-Age=0; {extra}"
        extra = "SameSite=None; Secure; HttpOnly" if self._is_secure() else "SameSite=Lax; HttpOnly"
        return f"jwr_sess={token}; Path=/; Max-Age={max_age}; {extra}"

    def _auth(self):
        token = None
        cookie = SimpleCookie(self.headers.get("Cookie", ""))
        ck = cookie.get("jwr_sess")
        if ck: token = ck.value
        if not token:
            ah = self.headers.get("Authorization", "")
            if ah.lower().startswith("bearer "): token = ah[7:].strip()
        if not token:
            token = self.headers.get("X-Auth-Token")
        if not token: return None
        r = None
        c = db()
        try:
            r = c.execute("SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id WHERE s.token=?",
                          (token,)).fetchone()
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
                hc.close()
                return self._send(out)
            except Exception as e:
                return self._err("db unavailable: %s" % e, 503)
        if p.startswith("/api/"):
            user = self._auth()
            if not user: return self._err("Not authenticated", 401)
            try: return self.api_get(user, p, q)
            except Exception as e:
                return self._err("Server error: %s" % e, 500)
        return self.static(p)

    def do_POST(self):
        self._restore_vercel_path()
        u = urlparse(self.path); p = u.path
        if p.startswith("/api/"):
            body = self._body()
            if body is None: return self._err("Invalid request body")
            auth_free = (p in ("/api/auth/signup", "/api/auth/login", "/api/auth/reset-password"))
            user = self._auth()
            if not user and not auth_free: return self._err("Not authenticated", 401)
            try: return self.api_post(user, p, body)
            except Exception as e:
                return self._err("Server error: %s" % e, 500)
        self._err("Not found", 404)

    # -------- static
    def static(self, p):
        if p == "/": p = "/index.html"
        fn = os.path.normpath(os.path.join(STATIC, p.lstrip("/")))
        if not fn.startswith(STATIC) or not os.path.isfile(fn):
            fn = os.path.join(STATIC, "index.html")
        ctype = {"html": "text/html; charset=utf-8", "js": "application/javascript; charset=utf-8",
                 "css": "text/css; charset=utf-8", "svg": "image/svg+xml", "png": "image/png",
                 "ico": "image/x-icon", "json": "application/json"}.get(fn.rsplit(".", 1)[-1], "application/octet-stream")
        with open(fn, "rb") as f: data = f.read()
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        if fn.endswith(".html"):
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
        else:
            # versioned asset urls (/app.js?v=N) are re-fetched whenever the version bumps
            self.send_header("Cache-Control", "no-cache")
        self.end_headers(); self.wfile.write(data)

    # ============================================================ GET API
    def api_get(self, user, p, q):
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
                return self.messages_list(c, uid, q)
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
                tok = None
                cookie = SimpleCookie(self.headers.get("Cookie", "")); t = cookie.get("jwr_sess")
                if t: tok = t.value
                ah = self.headers.get("Authorization", "")
                if not tok and ah.lower().startswith("bearer "): tok = ah[7:].strip()
                if tok: c.execute("DELETE FROM sessions WHERE token=?", (tok,)); c.commit()
                return self._send({"ok": True}, extra_headers=[("Set-Cookie", self._cookie_header("", clear=True))])
            if p == "/api/me": return self.update_me(c, uid, body)
            if p == "/api/me/password": return self.change_my_password(c, uid, body)
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
    def _make_session(self, c, uid):
        token = hashlib.sha256(os.urandom(32)).hexdigest()
        c.execute("INSERT INTO sessions(token,user_id,created_at) VALUES(?,?,?)",
                  (token, uid, now_iso()))
        c.commit()
        return token

    def signup(self, body):
        name = (body.get("name") or "").strip()
        pw = body.get("password") or ""
        if not (2 <= len(name) <= 30): return self._err("Name must be 2–30 characters.")
        if len(pw) < 4: return self._err("Password must be at least 4 characters.")
        c = db()
        try:
            if c.execute("SELECT 1 FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone():
                return self._err("That name is already taken.")
            salt = hashlib.sha256(os.urandom(16)).hexdigest()
            code_gen = gen_code(); code = next(code_gen)
            while c.execute("SELECT 1 FROM users WHERE code=?", (code,)).fetchone(): code = next(code_gen)
            colors = ["#f97316", "#22d3ee", "#a3e635", "#f472b6", "#facc15", "#818cf8"]
            exam = body.get("examDate")
            if not exam:
                # default: next likely JEE Main session (late January of next exam year)
                yr = datetime.now(IST).date().year + (1 if datetime.now(IST).date().month >= 6 else 0)
                exam = f"{yr}-01-21"
            role = "admin" if c.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0 else "user"
            cur = c.execute("""INSERT INTO users(name,pass_hash,salt,code,exam_date,avatar_color,created_at,role)
                VALUES(?,?,?,?,?,?,?,?)""", (name, hash_pw(pw, salt), salt, code, exam,
                random.choice(colors), now_iso(), role))
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
            return self._send({"ok": True, "code": code, "token": token},
                              extra_headers=[("Set-Cookie", self._cookie_header(token))])
        finally:
            c.close()

    def login(self, body):
        name = (body.get("name") or "").strip(); pw = body.get("password") or ""
        c = db()
        try:
            u = c.execute("SELECT * FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if not u or not hmac_mod.compare_digest(u["pass_hash"], hash_pw(pw, u["salt"])):
                return self._err("Wrong name or password.", 401)
            token = self._make_session(c, u["id"])
            return self._send({"ok": True, "token": token},
                              extra_headers=[("Set-Cookie", self._cookie_header(token))])
        finally:
            c.close()

    def reset_password(self, body):
        name = (body.get("name") or "").strip()
        code = (body.get("code") or "").strip().upper()
        newpw = (body.get("newPassword") or "").strip()
        if not name: return self._err("Enter your name.")
        if not code: return self._err("Enter your 6-letter friend code.")
        if len(newpw) < 4: return self._err("New password must be at least 4 characters.")
        c = db()
        try:
            u = c.execute("SELECT * FROM users WHERE name=? COLLATE NOCASE", (name,)).fetchone()
            if not u: return self._err("No account found with that name.", 404)
            if (u["code"] or "").strip().upper() != code:
                return self._err("Incorrect 6-letter friend code for this account.", 400)
            salt = hashlib.sha256(os.urandom(16)).hexdigest()
            c.execute("UPDATE users SET pass_hash=?, salt=? WHERE id=?",
                      (hash_pw(newpw, salt), salt, u["id"]))
            c.commit()
            token = self._make_session(c, u["id"])
            return self._send({"ok": True, "token": token, "name": u["name"]},
                              extra_headers=[("Set-Cookie", self._cookie_header(token))])
        finally:
            c.close()

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
        if code == user["code"]: return self._err("That's your own code.")
        pu = c.execute("SELECT * FROM users WHERE code=?", (code,)).fetchone()
        if not pu: return self._err("No user with that code.")
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

    def admin_set_password(self, c, uid, body):
        if not self.is_admin(c, uid): return self._err("Admin only.", 403)
        target = body.get("userId")
        try: target = int(target)
        except Exception: pass
        newpw = (body.get("password") or "").strip()
        if len(newpw) < 4: return self._err("Password must be at least 4 characters.")
        r = c.execute("SELECT id FROM users WHERE id=?", (target,)).fetchone() if target is not None else None
        if not r: return self._err("User not found.")
        salt = hashlib.sha256(os.urandom(16)).hexdigest()
        c.execute("UPDATE users SET pass_hash=?, salt=? WHERE id=?", (hash_pw(newpw, salt), salt, target))
        # existing sessions for that user stay valid; admin can tell them the new password
        c.commit()
        return self._send({"ok": True})

    def change_my_password(self, c, uid, body):
        curpw = (body.get("currentPassword") or "").strip()
        newpw = (body.get("newPassword") or "").strip()
        if len(newpw) < 4: return self._err("New password must be at least 4 characters.")
        u = c.execute("SELECT pass_hash, salt FROM users WHERE id=?", (uid,)).fetchone()
        if not u: return self._err("User not found.", 404)
        is_adm = self.is_admin(c, uid)
        if not is_adm or curpw:
            if not hmac_mod.compare_digest(u["pass_hash"], hash_pw(curpw, u["salt"])):
                return self._err("Current password is incorrect.", 400)
        salt = hashlib.sha256(os.urandom(16)).hexdigest()
        c.execute("UPDATE users SET pass_hash=?, salt=? WHERE id=?", (hash_pw(newpw, salt), salt, uid))
        c.commit()
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
    backup_db()          # snapshot at every boot
    backup_loop()        # then hourly
    print(f"JEE WAR ROOM running on http://0.0.0.0:{PORT}")
    ThreadedServer(("0.0.0.0", PORT), Handler).serve_forever()
