"""
Vercel serverless entry point.

Vercel rewrites every request to /api/index and (on current runtimes) hands
the function the *rewritten* path, so vercel.json forwards the original path
as ?__path=...; we restore it on self.path before the standard Handler runs.
The Vercel Python runtime instantiates the lowercase `handler` class per
request, exactly like http.server.BaseHTTPRequestHandler.
"""
import os
import sys
from urllib.parse import urlparse, parse_qs, urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import dbwrap  # noqa: E402
import server  # noqa: E402


def _ensure_db():
    """Run (or retry) the schema migration.

    A failure here used to be swallowed by a bare `except: print(...)` at
    import time, so a failed or half-applied migration on Turso still booted
    the app and then every /api/* request 500'd with no operator-visible
    cause. Now the boot state is recorded in server.BOOT_INFO (surfaced at
    GET /healthz?detail=1 as bootOk / bootError) and the migration is
    retried lazily on every request until it succeeds.
    """
    if server.BOOT_INFO.get("ok"):
        return
    if not dbwrap.CLOUD and os.environ.get("VERCEL_ENV"):
        # Running on Vercel with NO Turso URL for this environment: the app
        # would fall back to the local sqlite file, which cannot exist on the
        # read-only function filesystem. (This previously crashed the function
        # at IMPORT time — every request died as a platform 500 before any
        # JSON could be sent.) Report it loudly instead.
        server.BOOT_INFO["ok"] = False
        server.BOOT_INFO["error"] = (
            "TURSO_DATABASE_URL is not set for this Vercel environment "
            "(VERCEL_ENV=%s). Check Vercel → Settings → Environment "
            "Variables: TURSO_DATABASE_URL/TURSO_AUTH_TOKEN must be scoped "
            "to this environment (Production AND Preview share nothing "
            "automatically)." % os.environ.get("VERCEL_ENV"))
        return
    try:
        server.init_db()
    except Exception as e:
        server.BOOT_INFO["ok"] = False
        server.BOOT_INFO["error"] = "%s: %s" % (type(e).__name__, str(e)[:300])
        try:
            print("init_db failed (retrying on next request):", server.BOOT_INFO["error"])
        except Exception:
            pass
        return
    server.BOOT_INFO["ok"] = True
    server.BOOT_INFO["error"] = None


# Attempt the migration on cold start. Failure is reported, not swallowed —
# and each following request retries it until it lands.
_ensure_db()


class handler(server.Handler):
    def _restore_path(self):
        u = urlparse(self.path)
        q = parse_qs(u.query)
        if "__path" in q:
            p = q["__path"][0]
            if not p.startswith("/"):
                p = "/" + p
            rest = {k: v for k, v in q.items() if k != "__path"}
            self.path = p + (("?" + urlencode(rest, doseq=True)) if rest else "")

    def do_GET(self):
        self._restore_path()
        _ensure_db()
        super().do_GET()

    def do_POST(self):
        self._restore_path()
        _ensure_db()
        super().do_POST()
