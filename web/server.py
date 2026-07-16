#!/usr/bin/env python3
"""A local HTTP server for the React viewer.

Run it and open the printed URL::

    python3 web/server.py
    python3 web/server.py --port 9000 --root ~/work-tracker

It serves three things:

* ``GET  /api/status`` and ``/api/sessions`` -- JSON, built by :mod:`web.api`;
* ``POST /api/<command>``                    -- the write commands, run by :mod:`web.api`;
* everything else                            -- the built React app from ``web/ui/dist``.

Design notes
------------

**Another caller of the one writer, not a second writer.** The browser can now
start, pause, resume and stop -- but every one of those is a single call into the
same :class:`~tracker.tracker.WorkTracker` the CLI and the Shortcuts drive. It
holds no state of its own, caches nothing between requests, and re-reads
``current.json`` before it acts. Two clicks and a Shortcut firing at once cannot
corrupt a session: the writes are atomic, ``start`` claims the session with
``O_CREAT | O_EXCL``, and whichever caller loses simply gets told no.

**Loopback only.** The default bind address is ``127.0.0.1``, so the server is
not reachable from the network. Your working hours are nobody else's business,
and this is a personal tool, not a service.

**Writes refuse a foreign origin.** A ``POST`` carrying an ``Origin`` header from
anywhere but this machine is rejected before it reaches the tracker. That is what
stops a page you happened to have open in another tab from stopping your session
behind your back -- including via DNS rebinding, where the attacker's *name*
resolves to 127.0.0.1 but its origin still says ``evil.example``. Reads are left
alone: there is nothing to protect against there that binding to loopback has not
already handled.

**A password is what makes it safe to put on the internet.** Everything above
protects a *loopback* tool from the other tabs in your browser. Reaching it from
the open network is a different question -- who may look at all -- and the answer
is a login. Configure a password (``--password-file``, or the
``WORK_TRACKER_PASSWORD_HASH`` environment variable) and every request, read or
write, must carry a session cookie the server signed; an unauthenticated browser
receives the login form and nothing else. The mechanism lives in :mod:`web.auth`,
kept pure and tested there. With no password configured the server runs open,
exactly as it always did -- so the local CLI-and-Shortcuts workflow is unchanged,
and the switch is thrown only when you deploy. See :func:`_build_auth`, which
*fails closed*: naming a password file that turns out to be empty stops the
server rather than quietly starting it unprotected.

**Driving it from another device is opt-in, and off by default.** To control the
tracker from a phone you widen both locks yourself, on purpose: ``--host`` to
listen somewhere the device can reach, and ``--allow-origin HOST`` to let that
device's origin through the guard above. Pass neither and nothing changes -- the
server listens on loopback and accepts writes from this machine alone, exactly as
before. The recommended ``HOST`` is a private one only your own devices can
reach (a Tailscale address, say), so "widen" stays "my devices" and does not
become "the network". The flag is host-only and deliberately port-blind, like the
loopback origins it joins: what a write is allowed by is where it came from, never
which port it came in on.

**No build, no problem.** If ``web/ui/dist`` is missing, the server still starts
and serves a page explaining how to build the UI, instead of returning a bare
404 that leaves you guessing.
"""

from __future__ import annotations

import argparse
import json
import os
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

from tracker.storage import NoSuchSessionError, SessionExistsError, Storage  # noqa: E402
from tracker.tracker import (  # noqa: E402
    InvalidTaskError,
    NoActiveSessionError,
    SessionAlreadyRunningError,
    WrongStateError,
)
from tracker.utils import CorruptJSONError, TrackerError  # noqa: E402
from web.api import (  # noqa: E402
    UnknownCommandError,
    build_sessions_payload,
    build_status_payload,
    run_command,
)
from web.auth import (  # noqa: E402
    Authenticator,
    load_or_create_secret,
    load_password_hash,
    throttle_key,
)

#: Where `npm run build` puts the compiled React app.
DIST_DIR = _REPO_ROOT / "web" / "ui" / "dist"

#: Hostnames a write may *always* originate from -- this machine, under each of
#: the names it answers to. Any port: in development the Vite server on :5173
#: proxies /api through to us and forwards the browser's own Origin, so pinning
#: the port would break `npm run dev` while protecting against nothing -- what
#: matters is the host, and a hostile page's origin is never one of these, however
#: its name resolves. `--allow-origin` adds to this set; it never replaces it, so
#: loopback keeps working no matter what else is let in.
_LOCAL_ORIGINS = frozenset({"127.0.0.1", "::1", "localhost"})


def _host_of(value: str) -> str:
    """Reduce an origin or a bare host to the hostname the guard compares on.

    It accepts both what a browser stamps on a request (``http://100.64.0.1:8765``)
    and the shorthand a person types on the command line (``100.64.0.1``, or a
    name like ``mymac.tail-scale.ts.net``), so ``--allow-origin`` takes either
    without the caller having to know which. The scheme and the port are dropped:
    a write is allowed by the host it came from, never the port it arrived on --
    the same rule the loopback origins have always followed.
    """
    host = urlparse(value).hostname or urlparse("//" + value).hostname
    return (host or value).strip().lower()


def _origin_allowed(origin: Optional[str], allowed: frozenset) -> bool:
    """Decide whether a write carrying ``origin`` may proceed.

    A request with no ``Origin`` at all did not come from a page -- it came from
    ``curl``, or a script, or the test suite, none of which a browser's
    same-origin machinery constrains and all of which could equally run the CLI.
    So a missing header is allowed; a *present* one must name an allowed host.
    Kept a free function, with no ``self`` and no socket, so the one security
    decision in the server is asserted directly in the tests.
    """
    if origin is None:
        return True
    return urlparse(origin).hostname in allowed

#: A command body is a handful of bytes. Anything larger is not one, and is not
#: worth reading into memory to find that out.
_MAX_BODY_BYTES = 64 * 1024

#: How each anticipated failure is reported over HTTP. Checked in order, so the
#: specific subclasses must come before the base classes they inherit from.
#:
#: The distinction that matters is 409 versus 500: a refusal ("you are already
#: running", "that session is not paused") is a perfectly healthy answer to a
#: question that could not be granted, and the UI shows it as such. A 500 means
#: the tracker itself is in trouble -- a corrupt file, an unwritable disk.
_HTTP_STATUS: Tuple[Tuple[type, HTTPStatus], ...] = (
    (UnknownCommandError, HTTPStatus.NOT_FOUND),
    (NoSuchSessionError, HTTPStatus.NOT_FOUND),
    (InvalidTaskError, HTTPStatus.BAD_REQUEST),
    (SessionAlreadyRunningError, HTTPStatus.CONFLICT),
    (NoActiveSessionError, HTTPStatus.CONFLICT),
    (WrongStateError, HTTPStatus.CONFLICT),
    (SessionExistsError, HTTPStatus.CONFLICT),
    (CorruptJSONError, HTTPStatus.INTERNAL_SERVER_ERROR),
    (TrackerError, HTTPStatus.INTERNAL_SERVER_ERROR),
)


def status_for(error: TrackerError) -> HTTPStatus:
    """Map a tracker failure onto the HTTP status that describes it."""
    for kind, status in _HTTP_STATUS:
        if isinstance(error, kind):
            return status
    return HTTPStatus.INTERNAL_SERVER_ERROR


class BadRequest(Exception):
    """Raised for a request the server rejects before the tracker ever sees it."""

    def __init__(self, status: HTTPStatus, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.message = message

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

#: Shown at every path when a password is configured and the request is not
#: logged in. It is a whole self-contained page -- no bundle, no assets -- so it
#: can be served *instead of* the app: an unauthenticated browser never receives
#: a single byte of the tracker, only this form. The form posts JSON to
#: /api/login and, on success, reloads to receive the app it could not see before.
_LOGIN_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Work Tracker — sign in</title>
<style>
 body{font:16px/1.6 -apple-system,system-ui,sans-serif;max-width:22rem;
      margin:18vh auto;padding:0 1.5rem;color:#1d1d1f}
 h1{font-size:1.3rem;margin:0 0 .25rem}
 p.dim{color:#6b6b70;margin:.25rem 0 1.5rem}
 form{display:flex;flex-direction:column;gap:.75rem}
 input,button{font:inherit;padding:.6rem .75rem;border-radius:8px;
      border:1px solid #c9c9ce}
 input{background:#fff}
 button{border:none;background:#0071e3;color:#fff;font-weight:600;cursor:pointer}
 button:disabled{opacity:.5;cursor:default}
 p.err{color:#c1121f;min-height:1.6em;margin:.5rem 0 0}
 @media(prefers-color-scheme:dark){
   body{background:#151517;color:#f2f2f7}
   input{background:#252529;border-color:#3a3a3f;color:#f2f2f7}
   p.dim{color:#9a9aa0}}
</style></head>
<body>
 <h1>Work Tracker</h1>
 <p class="dim">Enter the password to continue.</p>
 <form id="f">
   <input id="pw" type="password" name="password" placeholder="Password"
          autocomplete="current-password" autofocus required>
   <button id="go" type="submit">Sign in</button>
 </form>
 <p class="err" id="err" role="alert"></p>
<script>
 const f=document.getElementById('f'),pw=document.getElementById('pw'),
       go=document.getElementById('go'),err=document.getElementById('err');
 f.addEventListener('submit',async e=>{
   e.preventDefault();err.textContent='';go.disabled=true;
   try{
     const r=await fetch('/api/login',{method:'POST',
       headers:{'Content-Type':'application/json'},
       body:JSON.stringify({password:pw.value})});
     if(r.ok){location.reload();return}
     const b=await r.json().catch(()=>null);
     err.textContent=(b&&b.error)||('Sign in failed ('+r.status+')');
   }catch(_){err.textContent='Could not reach the server.'}
   go.disabled=false;pw.select();
 });
</script>
</body></html>
"""


class ViewerHandler(BaseHTTPRequestHandler):
    """Serves the JSON API and the static React bundle. ``GET`` reads, ``POST`` writes."""

    #: Cosmetic: what the server announces itself as.
    server_version = "work-tracker"
    sys_version = ""

    def __init__(
        self,
        *args: Any,
        storage: Storage,
        allowed_origins: frozenset = _LOCAL_ORIGINS,
        auth: Optional[Authenticator] = None,
        **kwargs: Any,
    ) -> None:
        # Bound before super().__init__, which handles the request immediately.
        self._storage = storage
        self._allowed_origins = allowed_origins
        # When None, the viewer runs open, exactly as it did before login existed.
        # When set, every request must carry a valid session or be turned away.
        self._auth = auth
        super().__init__(*args, **kwargs)

    # -- routing ------------------------------------------------------------

    def do_GET(self) -> None:  # noqa: N802 (name mandated by BaseHTTPRequestHandler)
        """Route a GET: the two read endpoints, or the built UI.

        When a password is configured, an unauthenticated GET never reaches any
        of that. An API path is answered with a bare 401; anything else is
        answered with the login page, served *in place of* the app -- so the
        browser of someone without the password receives the form and nothing
        else, not a single line of the tracker's own markup or data.
        """
        path = urlparse(self.path).path

        if self._auth is not None and not self._authenticated():
            if path.startswith("/api/"):
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
            else:
                self._send_html(HTTPStatus.OK, _LOGIN_PAGE)
            return

        if path == "/api/status":
            self._serve_json(build_status_payload, "status")
        elif path == "/api/sessions":
            self._serve_json(build_sessions_payload, "sessions")
        elif path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"no such endpoint: {path}"})
        else:
            self._serve_static(path)

    def do_POST(self) -> None:  # noqa: N802 (name mandated by BaseHTTPRequestHandler)
        """Route a POST: one write command, named by the path.

        The two failure kinds are kept apart deliberately. A :class:`BadRequest`
        is the *server* refusing to pass the request on -- a foreign origin, a
        body that is not JSON, a path that is not a command. A
        :class:`~tracker.utils.TrackerError` is the *tracker* refusing the command
        itself, and it carries a message written to be read by a person ("the
        session is already paused"), which is exactly what the UI puts on screen.
        """
        path = urlparse(self.path).path

        if not path.startswith("/api/"):
            self._send_json(HTTPStatus.NOT_FOUND, {"error": f"no such endpoint: {path}"})
            return

        # Login and logout only exist when a password is configured, and login
        # is the one write that runs *before* the session check -- it is how you
        # get a session in the first place. Everything past here needs one.
        if self._auth is not None:
            if path == "/api/login":
                self._handle_login()
                return
            if path == "/api/logout":
                self._handle_logout()
                return
            if not self._authenticated():
                self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "authentication required"})
                return

        try:
            self._require_allowed_origin()
            body = self._read_body()
        except BadRequest as refusal:
            self._send_json(refusal.status, {"error": refusal.message})
            return

        command = path[len("/api/") :]
        try:
            result = run_command(self._storage, command, body)
        except TrackerError as exc:
            self._send_json(status_for(exc), {"error": str(exc), "what": command})
            return

        self._send_json(HTTPStatus.OK, result)

    # -- guards -------------------------------------------------------------

    def _require_allowed_origin(self) -> None:
        """Refuse a write from an origin that was not let in.

        A browser attaches ``Origin`` to every POST it makes, and what the header
        cannot do is lie: a page served from ``evil.example`` cannot make its
        browser claim to be one of the allowed hosts, whatever that name resolves
        to. Checking it is therefore enough, and checking it is cheap. The allowed
        set is this machine by default, plus whatever ``--allow-origin`` added --
        so the refusal here is exactly as wide, or as narrow, as you asked for.

        Raises:
            BadRequest: If an ``Origin`` is present and is not an allowed host.
        """
        origin = self.headers.get("Origin")
        if not _origin_allowed(origin, self._allowed_origins):
            raise BadRequest(
                HTTPStatus.FORBIDDEN,
                f"refusing a write from another origin ({origin})",
            )

    def _read_body(self) -> Dict[str, Any]:
        """Decode the request body as a JSON object. An empty body means ``{}``.

        Raises:
            BadRequest: If the body is oversized, malformed, or not an object.
        """
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            raise BadRequest(HTTPStatus.BAD_REQUEST, "malformed Content-Length") from None

        if length < 0 or length > _MAX_BODY_BYTES:
            raise BadRequest(
                HTTPStatus.REQUEST_ENTITY_TOO_LARGE,
                f"a command body may be at most {_MAX_BODY_BYTES} bytes",
            )
        if length == 0:
            # `pause`, `resume` and `stop` take no arguments, so the UI sends them
            # nothing. That is not an error, it is the request.
            return {}

        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise BadRequest(HTTPStatus.BAD_REQUEST, f"body is not valid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise BadRequest(
                HTTPStatus.BAD_REQUEST,
                f"body must be a JSON object, got {type(payload).__name__}",
            )
        return payload

    # -- auth ---------------------------------------------------------------

    def _authenticated(self) -> bool:
        """True if this request carries a session cookie the server signed.

        Only called when :attr:`_auth` is set, so the attribute access is safe.
        """
        assert self._auth is not None
        return self._auth.is_authenticated(self.headers.get("Cookie"))

    def _client_key(self) -> str:
        """The key the login throttle counts failures against -- the client IP.

        Behind the intended loopback proxy the raw peer is always 127.0.0.1, so
        the real client is taken from a trusted ``X-Forwarded-For``. See
        :func:`web.auth.throttle_key` for exactly when that header is believed.
        """
        peer = self.client_address[0] if self.client_address else None
        return throttle_key(peer, self.headers.get("X-Forwarded-For"))

    def _handle_login(self) -> None:
        """Check a password and, if it is right, hand back a session cookie.

        The order is deliberate. The throttle is consulted *first*, so a locked-out
        client is turned away with a 429 before its guess is ever compared -- the
        lockout cannot be worn down by continuing to guess. Only then is the
        origin checked and the body read, and only then the password. A wrong
        password is a 401 with a message written for the person, never a hint at
        how close they were; every wrong answer is the same wrong answer.
        """
        assert self._auth is not None
        client = self._client_key()

        wait = self._auth.throttle.retry_after(client)
        if wait > 0:
            self._send_json(
                HTTPStatus.TOO_MANY_REQUESTS,
                {"error": "too many attempts; wait a few minutes and try again"},
                extra_headers={"Retry-After": str(int(wait) + 1)},
            )
            return

        try:
            self._require_allowed_origin()
            body = self._read_body()
        except BadRequest as refusal:
            self._send_json(refusal.status, {"error": refusal.message})
            return

        if not self._auth.verify_login(body.get("password")):
            self._auth.throttle.record_failure(client)
            self._send_json(HTTPStatus.UNAUTHORIZED, {"error": "wrong password"})
            return

        self._auth.throttle.record_success(client)
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            extra_headers={"Set-Cookie": self._auth.session_cookie()},
        )

    def _handle_logout(self) -> None:
        """Clear the session cookie. Always succeeds; there is nothing to refuse."""
        assert self._auth is not None
        self._send_json(
            HTTPStatus.OK,
            {"ok": True},
            extra_headers={"Set-Cookie": self._auth.logout_cookie()},
        )

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

    def _send_json(
        self,
        status: HTTPStatus,
        payload: Dict[str, Any],
        extra_headers: Optional[Dict[str, str]] = None,
    ) -> None:
        """Serialise ``payload`` and write it with no-cache headers.

        ``extra_headers`` carries the one header the login flow adds -- a
        ``Set-Cookie`` that hands the browser its session, or clears it.
        """
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        # The status endpoint is polled once a second; a cached response would
        # freeze the timer in the browser.
        self.send_header("Cache-Control", "no-store")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
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


def serve(
    root: Path,
    host: str,
    port: int,
    allow_origins: frozenset = frozenset(),
    auth: Optional[Authenticator] = None,
) -> int:
    """Run the server until interrupted. Returns a process exit code.

    ``allow_origins`` is the *extra* hosts a write may come from, on top of this
    machine. Empty by default, which is the shipped behaviour: writes from
    anywhere but loopback are refused.

    ``auth`` is the login guard. ``None`` runs the viewer open, as it always was;
    an :class:`~web.auth.Authenticator` requires a password on every request.
    """
    storage = Storage(root)
    allowed = _LOCAL_ORIGINS | allow_origins
    handler = partial(
        ViewerHandler, storage=storage, allowed_origins=allowed, auth=auth
    )

    try:
        httpd = ThreadingHTTPServer((host, port), handler)
    except OSError as exc:
        print(f"error: cannot listen on {host}:{port}: {exc}", file=sys.stderr)
        return 1

    print(f"work-tracker viewer -> http://{host}:{port}")
    print(f"data directory      -> {root}")
    print(f"authentication      -> {'password required' if auth else 'none (open)'}")
    if allow_origins:
        print(f"writes allowed from -> {', '.join(sorted(allow_origins))} (and this machine)")
    if host not in _LOCAL_ORIGINS and auth is None:
        # Reachable from the network *and* no password: anyone who finds the port
        # can read your hours and start or stop sessions. This is the one
        # configuration worth refusing to be quiet about.
        print(f"WARNING: {host} is reachable off this machine and NO password is set --")
        print("         anyone who can reach this port can read and control your sessions.")
        print("         Set a password: python3 -m web.auth --write .password, then")
        print("         restart with --password-file .password")
    elif host not in _LOCAL_ORIGINS:
        # The server can start and stop sessions now, so a bind address that is
        # not loopback is worth saying out loud rather than leaving to be noticed.
        print(f"note: {host} is not loopback -- login is required, keep the password strong")
    elif allow_origins:
        # Allowing a remote origin while still bound to loopback is a half-turned
        # key: the guard would let the device through, but it can never reach the
        # port. Say so, rather than let it look like it should work.
        print("note: still listening on loopback -- pass --host so another device can reach it")
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
    parser.add_argument(
        "--allow-origin",
        action="append",
        default=[],
        metavar="HOST",
        dest="allow_origin",
        help=(
            "also accept writes whose Origin is this host -- repeatable. Needed to "
            "drive the tracker from another device: pass the address you will open "
            "it at (e.g. --allow-origin 100.64.0.1). A host or a full origin both "
            "work; the port is ignored. This machine is always allowed."
        ),
    )
    parser.add_argument(
        "--password-file",
        type=Path,
        metavar="PATH",
        help=(
            "require a login. PATH holds a password hash from "
            "'python3 -m web.auth --write PATH'. The environment variable "
            "WORK_TRACKER_PASSWORD_HASH is used instead if set. With neither, the "
            "viewer runs open, as before -- which is only safe on loopback."
        ),
    )
    parser.add_argument(
        "--cookie-insecure",
        action="store_true",
        help=(
            "do not mark the session cookie Secure. Only for testing login over "
            "plain http on localhost; never use it for a real deployment, where "
            "TLS (e.g. Caddy in front) makes Secure the right and safe default."
        ),
    )
    args = parser.parse_args(argv)

    allow_origins = frozenset(_host_of(origin) for origin in args.allow_origin)
    root = args.root.expanduser()

    auth = _build_auth(root, args.password_file, cookie_secure=not args.cookie_insecure)
    if auth is _AUTH_MISCONFIGURED:
        return 1

    return serve(
        root=root,
        host=args.host,
        port=args.port,
        allow_origins=allow_origins,
        auth=auth,
    )


#: Sentinel: a password file was named but held no usable hash. Distinct from
#: ``None`` (no login configured, run open), so main() can exit non-zero rather
#: than silently starting an *unprotected* server the operator meant to protect.
_AUTH_MISCONFIGURED = object()


def _build_auth(
    root: Path, password_file: Optional[Path], cookie_secure: bool
) -> Any:
    """Assemble the login guard from the environment and the ``--password-file``.

    Fails closed: if ``--password-file`` is given but names nothing readable, this
    returns the :data:`_AUTH_MISCONFIGURED` sentinel so the server refuses to
    start, rather than falling back to no password and putting an open viewer on
    the network the operator was trying to lock down. With no password configured
    at all it returns ``None`` -- the deliberate, documented open mode.
    """
    env_hash = os.environ.get("WORK_TRACKER_PASSWORD_HASH")
    password_hash = load_password_hash(env_hash, password_file)

    if password_file is not None and password_hash is None:
        print(
            f"error: --password-file {password_file} has no password hash in it.\n"
            f"       create one with: python3 -m web.auth --write {password_file}",
            file=sys.stderr,
        )
        return _AUTH_MISCONFIGURED

    if password_hash is None:
        return None

    secret = load_or_create_secret(root / ".session_secret")
    return Authenticator(
        password_hash=password_hash,
        secret=secret,
        cookie_secure=cookie_secure,
    )


if __name__ == "__main__":
    sys.exit(main())
