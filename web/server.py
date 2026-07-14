#!/usr/bin/env python3
"""A local, read-only HTTP server for the React viewer.

Run it and open the printed URL::

    python3 web/server.py
    python3 web/server.py --port 9000 --root ~/work-tracker

It serves two things:

* ``/api/status`` and ``/api/sessions`` -- JSON, built by :mod:`web.api`;
* everything else -- the built React app from ``web/ui/dist``.

Design notes
------------

**Read-only, by construction.** There is no route that writes anything. The CLI
stays the single writer, so the browser cannot race the Shortcuts.

**Loopback only.** The default bind address is ``127.0.0.1``, so the server is
not reachable from the network. Your working hours are nobody else's business,
and this is a personal tool, not a service.

**No build, no problem.** If ``web/ui/dist`` is missing, the server still starts
and serves a page explaining how to build the UI, instead of returning a bare
404 that leaves you guessing.
"""

from __future__ import annotations

import argparse
import json
import sys
from functools import partial
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from tracker.storage import Storage  # noqa: E402
from tracker.utils import TrackerError  # noqa: E402
from web.api import build_sessions_payload, build_status_payload  # noqa: E402

#: Where `npm run build` puts the compiled React app.
DIST_DIR = _REPO_ROOT / "web" / "ui" / "dist"

#: Only these extensions are ever served, with an explicit content type. An
#: allow-list beats guessing: nothing outside it can be handed to the browser.
_CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
    ".png": "image/png",
    ".ico": "image/x-icon",
    ".woff2": "font/woff2",
    ".map": "application/json; charset=utf-8",
}

_NO_BUILD_PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>work-tracker</title>
<style>
 body{{font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:42rem;
      margin:15vh auto;padding:0 1.5rem;color:#1d1d1f}}
 code{{background:#f1f1f3;padding:.15rem .4rem;border-radius:4px;font-size:.9em}}
 pre{{background:#f1f1f3;padding:1rem;border-radius:8px;overflow-x:auto}}
 @media(prefers-color-scheme:dark){{
   body{{background:#151517;color:#f2f2f7}}
   code,pre{{background:#252529}}}}
</style></head>
<body>
 <h1>The UI has not been built yet</h1>
 <p>The API is running, but there is no compiled React app at
    <code>{dist}</code>.</p>
 <p>Build it once:</p>
 <pre>cd {ui}
npm install
npm run build</pre>
 <p>Then reload this page. The API itself works right now &mdash; try
    <a href="/api/status">/api/status</a>.</p>
</body></html>
"""


class ViewerHandler(BaseHTTPRequestHandler):
    """Serves the JSON API and the static React bundle. ``GET`` only."""

    #: Cosmetic: what the server announces itself as.
    server_version = "work-tracker"
    sys_version = ""

    def __init__(self, *args: Any, storage: Storage, **kwargs: Any) -> None:
        # Bound before super().__init__, which handles the request immediately.
        self._storage = storage
        super().__init__(*args, **kwargs)

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (name mandated by BaseHTTPRequestHandler)
        """Route a GET request. The only verb this server implements."""
        path = urlparse(self.path).path

        if path == "/api/status":
            self._serve_json(build_status_payload, "status")
        elif path == "/api/sessions":
            self._serve_json(build_sessions_payload, "sessions")
        elif path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"no such endpoint: {path}"})
        else:
            self._serve_static(path)

    # -- api ----------------------------------------------------------------

    def _serve_json(self, build: Any, what: str) -> None:
        """Build a payload and send it, turning tracker errors into HTTP 500s.

        A corrupt ``current.json`` is a real condition the UI must be able to
        show, so it becomes a JSON error body rather than a stack trace on the
        terminal and a hung spinner in the browser.
        """
        try:
            payload = build(self._storage)
        except TrackerError as exc:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"error": str(exc), "what": what},
            )
            return
        self._send_json(HTTPStatus.OK, payload)

    def _send_json(self, status: HTTPStatus, payload: Dict[str, Any]) -> None:
        """Serialise ``payload`` and write it with no-cache headers."""
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The status endpoint is polled once a second; a cached response would
        # freeze the timer in the browser.
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    # -- static -------------------------------------------------------------

    def _resolve_static(self, path: str) -> Optional[Path]:
        """Map a URL path to a file inside ``dist``, or ``None`` if it escapes.

        The containment check is the point of this method. ``resolve()`` collapses
        any ``..`` segments, and the result must still sit inside ``dist`` -- so a
        request for ``/../../../../etc/passwd`` resolves outside and is refused
        rather than served.
        """
        relative = path.lstrip("/") or "index.html"
        candidate = (DIST_DIR / relative).resolve()

        try:
            candidate.relative_to(DIST_DIR.resolve())
        except ValueError:
            return None

        if candidate.is_dir():
            candidate = candidate / "index.html"
        return candidate

    def _serve_static(self, path: str) -> None:
        """Serve a file from the built React app."""
        if not DIST_DIR.is_dir():
            self._send_html(
                HTTPStatus.OK,
                _NO_BUILD_PAGE.format(dist=DIST_DIR, ui=DIST_DIR.parent),
            )
            return

        target = self._resolve_static(path)
        if target is None:
            self._send_html(HTTPStatus.FORBIDDEN, "<h1>403 Forbidden</h1>")
            return

        # Single-page app: an unknown path is the client router's business, so
        # fall back to index.html rather than 404-ing.
        if not target.is_file():
            target = DIST_DIR / "index.html"
            if not target.is_file():
                self._send_html(HTTPStatus.NOT_FOUND, "<h1>404 Not Found</h1>")
                return

        content_type = _CONTENT_TYPES.get(target.suffix)
        if content_type is None:
            self._send_html(HTTPStatus.FORBIDDEN, "<h1>403 Forbidden</h1>")
            return

        try:
            body = target.read_bytes()
        except OSError:
            self._send_html(HTTPStatus.NOT_FOUND, "<h1>404 Not Found</h1>")
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, status: HTTPStatus, html: str) -> None:
        """Send a small HTML document."""
        body = html.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    # -- logging ------------------------------------------------------------

    def log_message(self, format: str, *args: Any) -> None:
        """Quieten the default logger: one line per request, no timestamps.

        The browser polls ``/api/status`` every second, and the stock logger
        would bury anything useful under a wall of identical lines.
        """
        if args and str(args[0]).startswith("GET /api/status"):
            return
        sys.stderr.write("  %s\n" % (format % args))


def serve(root: Path, host: str, port: int) -> int:
    """Run the server until interrupted. Returns a process exit code."""
    storage = Storage(root)
    handler = partial(ViewerHandler, storage=storage)

    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1

    print(f"work-tracker viewer -> http://{host}:{port}")
    print(f"data directory      -> {root}")
    if not DIST_DIR.is_dir():
        print(f"note: the UI is not built yet (run 'npm install && npm run build' in {DIST_DIR.parent})")
    print("press ctrl-c to stop")

    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        httpd.server_close()
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    """Parse arguments and start the server."""
    parser = argparse.ArgumentParser(description="Serve the work-tracker viewer.")
    parser.add_argument(
        "--root",
        type=Path,
        default=_REPO_ROOT,
        help="data directory holding current.json and sessions/ (default: the repository)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="bind address (default: 127.0.0.1, i.e. this machine only)",
    )
    parser.add_argument("--port", type=int, default=8765, help="port (default: 8765)")
    args = parser.parse_args(argv)

    return serve(root=args.root.expanduser(), host=args.host, port=args.port)


if __name__ == "__main__":
    sys.exit(main())
