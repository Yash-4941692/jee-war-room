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
import threading
from urllib.parse import urlparse, parse_qs, urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import server  # noqa: E402

# Self-healing schema MUST run synchronously at cold import: serverless
# freezes the instance the moment the first response finishes, so a daemon
# thread started here may be paused mid-way and never complete. The cloud path
# is a single 1-query table check (~0.1s same-region); it only runs DDL when a
# genuinely new table is missing. Wrapped so schema maintenance can never take
# a request down.
try:
    server.init_db()
except Exception as _e:
    print("init_db warning:", _e)

# Background warmer (schema is already handled above): a SELECT 1 every 4 min
# keeps the Turso database awake while this instance is warm.
def _warm_loop():
    import time
    while True:
        time.sleep(240)
        try:
            c = server.db()
            c.execute("SELECT 1").fetchone()
            c.close()
        except Exception:
            time.sleep(5)

threading.Thread(target=_warm_loop, daemon=True).start()


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
        super().do_GET()

    def do_POST(self):
        self._restore_path()
        super().do_POST()
