#!/usr/bin/env python3
"""
JEE WAR ROOM — authentication security primitives.

Everything credential-related lives here so the rules are written once and
cannot drift between signup / login / reset / admin-reset (that drift is what
made "I typed my password correctly but it says wrong" possible: some flows
stored a trimmed password while login compared the raw string).

Design notes
------------
* Passwords: PBKDF2-HMAC-SHA256, 120 000 iterations, unique 16-byte random
  salt per user. One-way — a password can never be read back out of the
  database, only verified. `verify_pw()` also transparently upgrades accounts
  whose hash was computed on an un-trimmed password.
* Sessions: the value handed to the browser is a 256-bit random token; only
  its SHA-256 digest is stored. A leaked database/dump therefore grants
  nothing, because the digest cannot be turned back into a bearer token.
* Recovery: one-time recovery codes (hashed at rest, consumed on use) replace
  the friend code as the self-service reset factor. The friend code is a
  *sharing* secret — the app tells users to hand it to up to 10 friends — so
  it must never double as a credential.
* Throttling: DB-backed (not in-process) so limits survive serverless
  restarts and are shared across every Vercel instance.
"""
import hashlib
import hmac
import re
import secrets
import time

# ---------------------------------------------------------------- passwords
PBKDF2_ITERS = 120_000
PW_MIN = 8            # enforced when a password is *set*; existing shorter
PW_MAX = 128          # passwords keep working until they are changed
PW_HARD_MAX = 512     # reject outright beyond this (hash-cost DoS guard)

# Small blocklist: catches the passwords students actually pick. It is a
# convenience filter, not the main defence — throttling is.
COMMON_PASSWORDS = frozenset("""
password password1 password123 passw0rd passwd qwerty qwerty123 123456
12345678 123456789 1234567890 111111 123123 12341234 abc123 abcdef
abcdefg letmein welcome welcome1 monkey dragon master sunshine princess
football baseball iloveyou admin admin123 root toor changeme default
jesuswarroom warroom jeewarroom jee2026 jee2027 jeemain jeemains
jeewarroom123 warroom123 aspirant topper topper123 rank1 air1
""".split())


def normalize_pw(pw):
    """Canonical form of a password.

    Surrounding whitespace is stripped everywhere it is *set* and everywhere
    it is *checked*, so a stray space from an autofill or a mobile keyboard
    can never lock somebody out of their own account.
    """
    if not isinstance(pw, str):
        return ""
    return pw.strip()


def _pbkdf2(pw, salt):
    """Raw KDF. Hashes EXACTLY the bytes given — no normalization here, so
    `verify_pw()` can still recognize hashes made by the old un-trimming code."""
    if not isinstance(pw, str):
        pw = ""
    return hashlib.pbkdf2_hmac(
        "sha256", pw.encode("utf-8"), str(salt).encode("utf-8"), PBKDF2_ITERS).hex()


def hash_pw(pw, salt):
    """PBKDF2-HMAC-SHA256 over the *normalized* password.

    Every code path that SETS a password uses this, which is what guarantees
    signup / login / reset / admin-reset can never disagree about whitespace
    again.
    """
    return _pbkdf2(normalize_pw(pw), salt)


def new_salt():
    return secrets.token_hex(16)


def verify_pw(pw, pass_hash, salt):
    """Constant-time password check.

    Returns (ok, legacy). `legacy` is True when the password only matched in
    its raw (un-normalized) form, i.e. the account was created before
    whitespace normalization existed — the caller should re-hash with the
    normalized value so the account self-heals.
    """
    if not pass_hash or not salt:
        return False, False
    raw = pw if isinstance(pw, str) else ""
    norm = normalize_pw(raw)
    if hmac.compare_digest(str(pass_hash), _pbkdf2(norm, salt)):
        return True, False
    # Accounts created before normalization stored a hash of the RAW string.
    # Accept it (so the owner is not locked out by their own trailing space)
    # but flag it, so the caller re-hashes the normalized form once.
    if raw != norm and hmac.compare_digest(str(pass_hash), _pbkdf2(raw, salt)):
        return True, True
    return False, False


def password_policy_error(pw, name=""):
    """Human-readable reason a password is unacceptable, or None."""
    if not isinstance(pw, str) or len(pw) > PW_HARD_MAX:
        return "Password is too long (maximum %d characters)." % PW_HARD_MAX
    if len(pw) < PW_MIN:
        return "Password must be at least %d characters." % PW_MIN
    low = pw.lower()
    if low in COMMON_PASSWORDS:
        return "That password is too common — pick something harder to guess."
    if name:
        n = str(name).strip().lower()
        if n and len(n) >= 3 and n in low:
            return "Your password cannot contain your username."
    if len(set(low)) <= 2:
        return "Use a more varied password (not one repeated character)."
    if re.fullmatch(r"(?i)([a-z0-9])\1*", low) or re.fullmatch(r"(?i)0123456789|9876543210", low):
        return "Use a more varied password (not a simple run of characters)."
    return None


# ---------------------------------------------------------------- tokens
def new_token():
    """Opaque bearer token handed to the browser."""
    return secrets.token_urlsafe(32)


def hash_token(token):
    """Digest actually stored in the sessions table."""
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


SESSION_TTL_SECONDS = 30 * 24 * 3600        # 30 days, absolute
SESSION_COOKIE_MAX_AGE = SESSION_TTL_SECONDS


# ---------------------------------------------------------------- recovery codes
RC_GROUPS = 3
RC_GROUP_LEN = 4
# Unambiguous alphabet: no 0/O, no 1/I/l, so a code written on paper still
# reads back correctly.
RC_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
RC_COUNT = 8


def gen_recovery_codes(n=RC_COUNT):
    """Fresh plaintext recovery codes, formatted XXXX-XXXX-XXXX."""
    out = []
    for _ in range(n):
        groups = ["".join(secrets.choice(RC_ALPHABET) for _ in range(RC_GROUP_LEN))
                  for _ in range(RC_GROUPS)]
        out.append("-".join(groups))
    return out


def normalize_code(code):
    """Accept codes typed with/without separators, in any case."""
    return re.sub(r"[^A-Z0-9]", "", (code or "").upper())


def hash_code(code):
    return hashlib.sha256(normalize_code(code).encode("utf-8")).hexdigest()


def valid_code_shape(code):
    """Shape check only (never a secret check) so we can fail fast on junk."""
    return bool(re.fullmatch(r"[A-Z0-9]{8,24}", normalize_code(code)))


# ---------------------------------------------------------------- friend codes
FRIEND_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def gen_friend_code(n=6):
    """Friend code for buddy connections.

    Cryptographically random (it used to be `random.choices`, whose Mersenne
    Twister state can be recovered from a few outputs). It is *not* a login
    credential and must never be accepted as one.
    """
    return "".join(secrets.choice(FRIEND_CODE_ALPHABET) for _ in range(n))


# ---------------------------------------------------------------- throttling
# Balanced profile: strict enough that guessing is pointless, loose enough
# that a whole coaching batch behind one hostel NAT is not locked out.
LIMITS = {
    # key prefix -> rule
    "u": {"max": 5, "window": 0, "base": 60, "cap": 3600},     # per username
    "i": {"max": 30, "window": 900, "base": 900, "cap": 3600},  # per IP, all accounts
    "s": {"max": 10, "window": 3600, "base": 900, "cap": 7200},  # signups per IP/hour
    "f": {"max": 20, "window": 600, "base": 600, "cap": 3600},  # friend-code lookups
    "r": {"max": 8, "window": 900, "base": 300, "cap": 3600},   # recovery-code guesses
}

THROTTLE_TTL_SECONDS = 24 * 3600


def throttle_key(kind, ident):
    return "%s:%s" % (kind, str(ident).strip().lower()[:120])


def throttle_remaining(c, key, now=None):
    """Seconds still blocked (0.0 = not blocked)."""
    now = time.time() if now is None else now
    try:
        r = c.execute("SELECT locked_until FROM auth_throttle WHERE key=?", (key,)).fetchone()
    except Exception:
        return 0.0                       # never lock people out on a DB hiccup
    if not r:
        return 0.0
    until = float(r["locked_until"] or 0)
    return max(0.0, until - now) if until > now else 0.0


def throttle_fail(c, key, kind, now=None, commit=True):
    """Record one failed attempt; returns seconds the caller is now blocked."""
    now = time.time() if now is None else now
    spec = LIMITS.get(kind, LIMITS["i"])
    row = None
    try:
        row = c.execute(
            "SELECT fails, window_start FROM auth_throttle WHERE key=?", (key,)).fetchone()
    except Exception:
        return 0.0
    window = spec["window"]
    if row is None or (window and now - float(row["window_start"] or 0) > window):
        fails, window_start = 1, now
    else:
        fails, window_start = int(row["fails"] or 0) + 1, float(row["window_start"] or 0)
    locked_until = 0.0
    if fails >= spec["max"]:
        over = fails - spec["max"]
        cooldown = min(spec["cap"], spec["base"] * (2 ** min(over, 16)))
        locked_until = now + cooldown
        if window:                       # windowed rules restart after a block
            fails, window_start = 0, now
    try:
        c.execute("""INSERT INTO auth_throttle(key,fails,window_start,locked_until,updated_at)
                     VALUES(?,?,?,?,?)
                     ON CONFLICT(key) DO UPDATE SET fails=excluded.fails,
                       window_start=excluded.window_start,
                       locked_until=excluded.locked_until,
                       updated_at=excluded.updated_at""",
                  (key, fails, window_start, locked_until, now))
        if commit:
            c.commit()
    except Exception:
        pass
    return max(0.0, locked_until - now)


def throttle_clear(c, key, commit=True):
    """A successful authentication wipes the counter for that identity."""
    try:
        c.execute("DELETE FROM auth_throttle WHERE key=?", (key,))
        if commit:
            c.commit()
    except Exception:
        pass


def throttle_housekeeping(c, now=None):
    """Drop stale counters so the table cannot grow forever."""
    now = time.time() if now is None else now
    try:
        c.execute("DELETE FROM auth_throttle WHERE updated_at < ?", (now - THROTTLE_TTL_SECONDS,))
        c.commit()
    except Exception:
        pass


def human_wait(seconds):
    seconds = int(max(1, round(seconds)))
    if seconds < 60:
        return "%d seconds" % seconds
    if seconds < 3600:
        m = int(round(seconds / 60.0))
        return "%d minute%s" % (m, "" if m == 1 else "s")
    h = int(round(seconds / 3600.0))
    return "%d hour%s" % (h, "" if h == 1 else "s")


# ---------------------------------------------------------------- events
EVENT_RETENTION_DAYS = 60


def log_event(c, kind, user_id=None, name=None, ip=None, detail=None, commit=True):
    """Append-only audit trail for authentication activity (admin visible)."""
    try:
        from datetime import datetime, timezone
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        c.execute("""INSERT INTO auth_events(ts,kind,user_id,name,ip,detail)
                     VALUES(?,?,?,?,?,?)""",
                  (ts, str(kind)[:40], user_id, (str(name)[:40] if name else None),
                   (str(ip)[:64] if ip else None), (str(detail)[:200] if detail else None)))
        if commit:
            c.commit()
    except Exception:
        pass        # auditing must never break a login


def prune_events(c, now=None):
    try:
        from datetime import datetime, timedelta, timezone
        cutoff = (datetime.now(timezone.utc) - timedelta(days=EVENT_RETENTION_DAYS)) \
            .isoformat(timespec="seconds").replace("+00:00", "Z")
        c.execute("DELETE FROM auth_events WHERE ts < ?", (cutoff,))
        c.commit()
    except Exception:
        pass
