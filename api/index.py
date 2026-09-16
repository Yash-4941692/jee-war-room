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

# Background maintenance on each warm serverless instance:
#  1) self-healing schema (idempotent, never touches data)
#  2) a SELECT 1 every 4 minutes keeps the Turso database awake, so a visitor
#     never pays the suspended-database wake-up. The thread pauses when the
#     platform freezes the instance and immediately re-warms on thaw (wall
#     clock jump makes the interval check fire before the first new request).
def _bg_init():
    import time
    try:
        server.init_db()
    except Exception as _e:
        print("init_db warning:", _e)
    while True:
        time.sleep(240)
        try:
            c = server.db()
            c.execute("SELECT 1").fetchone()
            c.close()
        except Exception as _e:
            print("warmer warning:", _e)
            time.sleep(5)
            try:
                server.init_db()   # recreate a dead client
            except Exception:
                pass

threading.Thread(target=_bg_init, daemon=True).start()


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
