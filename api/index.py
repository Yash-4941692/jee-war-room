"""
Vercel serverless entry point (WSGI).

The application itself is the stdlib http.server app in ../server.py.
This thin adapter drives its existing Handler under any WSGI host
(Vercel's Python runtime), so business logic stays identical between
local runs and the cloud. Static assets are served from /public by the
CDN; this function handles /api/* and /healthz (with the Handler's
file fallback for anything else).
"""
import os
import sys
import io

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from server import Handler  # noqa: E402


class _Headers:
    """Case-insensitive header view over a WSGI environ."""
    def __init__(self, environ):
        self._h = {}
        for k, v in environ.items():
            if k.startswith("HTTP_"):
                self._h[k[5:].replace("_", "-")] = v
        if environ.get("CONTENT_TYPE"):
            self._h["Content-Type"] = environ["CONTENT_TYPE"]
        if environ.get("CONTENT_LENGTH"):
            self._h["Content-Length"] = environ["CONTENT_LENGTH"]

    def get(self, key, default=None):
        key = key.lower()
        for k, v in self._h.items():
            if k.lower() == key:
                return v
        return default


class WsgiHandler(Handler):
    """BaseHTTPRequestHandler surface reimplemented on top of WSGI."""
    def begin(self, environ, body):
        self.headers = _Headers(environ)
        self.rfile = io.BytesIO(body)
        self.wfile = io.BytesIO()
        self._status = 200
        self._resp_headers = []
        self.client_address = ("", 0)
        self.request_version = "HTTP/1.1"

    def send_response(self, code, message=None):
        self._status = code

    def send_response_only(self, code, message=None):
        self._status = code

    def send_header(self, key, value):
        self._resp_headers.append((key, str(value)))

    def end_headers(self):
        pass

    def date_time_string(self, timestamp=None):
        return ""


_STATUS_TEXT = {
    200: "OK", 201: "Created", 204: "No Content", 301: "Moved Permanently",
    302: "Found", 304: "Not Modified", 400: "Bad Request", 401: "Unauthorized",
    403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
    500: "Internal Server Error", 502: "Bad Gateway", 503: "Service Unavailable",
}


def app(environ, start_response):
    try:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except (TypeError, ValueError):
            length = 0
        body = environ["wsgi.input"].read(length) if length > 0 else b""

        method = environ.get("REQUEST_METHOD", "GET").upper()
        # rewrites hide the original path behind this header on some hosts
        path = (environ.get("HTTP_X_VERCEL_ORIGINAL_PATHNAME")
                or environ.get("PATH_INFO") or "/")
        qs = environ.get("QUERY_STRING", "")

        h = WsgiHandler.__new__(WsgiHandler)
        h.begin(environ, body)
        h.path = path + (("?" + qs) if qs else "")
        h.command = method

        if method == "GET":
            h.do_GET()
        elif method == "HEAD":
            h.do_GET()
        elif method == "POST":
            h.do_POST()
        elif method == "OPTIONS":
            h.send_response(204)
            h.send_header("Access-Control-Allow-Origin", "*")
            h.send_header("Access-Control-Allow-Methods", "GET,POST,OPTIONS")
            h.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization, Cookie")
        else:
            h._err("Method not allowed", 405)

        data = h.wfile.getvalue()
        status = "%d %s" % (h._status, _STATUS_TEXT.get(h._status, "OK"))
        start_response(status, h._resp_headers)
        return [data]
    except Exception as e:  # never leak a raw platform error
        import json as _json
        payload = _json.dumps({"error": "Function error: %s" % e}).encode()
        start_response("500 Internal Server Error",
                       [("Content-Type", "application/json"),
                        ("Content-Length", str(len(payload)))])
        return [payload]
