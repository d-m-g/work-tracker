"""Tests for the server's security decisions: who may write, and who may look.

Like the rest of the suite, these bind no port. Every guard lives in a free
function -- :func:`web.server._host_of`, :func:`web.server._origin_allowed`,
:func:`web.server.public_path`, :func:`web.auth.may_write` -- precisely so they
can be asserted directly, without an HTTP round-trip. What the socket wrapper adds
on top (reading a header, turning a ``False`` into a 403, a ``None`` into the login
form) is a two-line adapter over the decisions proved here.

The through-lines:

* Driving the tracker from another device is opt-in and off by default. With
  nothing allowed, this machine writes and no one else does -- the shipped
  behaviour. ``--allow-origin`` widens the set by exactly what you name, and never
  touches loopback.
* A visitor without the password may have the app's *code* -- the demo runs on it,
  and it is published in this repository anyway -- and may never have a minute off
  your disk. :func:`~web.server.public_path` is where that line is drawn, so this
  is where it is defended.
* A visitor *with* the read-only password may have every minute of it and change
  none of them. The refusal is the server's, not the app's, so what is asserted
  here is the form the server hands that account -- see also
  :class:`tests.test_auth.TestMayWrite`, where the rule itself is proved.
"""

from __future__ import annotations

import json
import unittest
from contextlib import redirect_stderr
from io import BytesIO, StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any, Dict, Optional, Tuple

from tracker.storage import Storage
from web.auth import COOKIE_NAME, OWNER, VIEWER, Authenticator, hash_password
from web.server import (
    _LOCAL_ORIGINS,
    ViewerHandler,
    _host_of,
    _login_page,
    _origin_allowed,
    _picker,
    public_path,
)


class TestHostOf(unittest.TestCase):
    """`--allow-origin` takes what a person types or what a browser sends alike."""

    def test_a_bare_host_is_itself(self) -> None:
        self.assertEqual("100.64.0.1", _host_of("100.64.0.1"))

    def test_a_name_is_itself(self) -> None:
        self.assertEqual("mymac.tail-scale.ts.net", _host_of("mymac.tail-scale.ts.net"))

    def test_a_full_origin_reduces_to_its_host(self) -> None:
        self.assertEqual("100.64.0.1", _host_of("http://100.64.0.1:8765"))

    def test_the_port_is_dropped(self) -> None:
        self.assertEqual("100.64.0.1", _host_of("100.64.0.1:8765"))

    def test_the_host_is_lowercased(self) -> None:
        self.assertEqual("mymac.ts.net", _host_of("MyMac.TS.net"))

    def test_surrounding_space_is_trimmed(self) -> None:
        self.assertEqual("localhost", _host_of("  localhost  "))


class TestOriginAllowedByDefault(unittest.TestCase):
    """With nothing added, the allowed set is this machine and nothing else."""

    def test_a_request_with_no_origin_is_allowed(self) -> None:
        # curl, a script, the test suite: none is a browser under same-origin
        # rules, and all could equally run the CLI.
        self.assertTrue(_origin_allowed(None, _LOCAL_ORIGINS))

    def test_loopback_is_allowed(self) -> None:
        self.assertTrue(_origin_allowed("http://127.0.0.1:8765", _LOCAL_ORIGINS))

    def test_localhost_is_allowed(self) -> None:
        self.assertTrue(_origin_allowed("http://localhost:5173", _LOCAL_ORIGINS))

    def test_a_foreign_origin_is_refused(self) -> None:
        self.assertFalse(_origin_allowed("http://evil.example", _LOCAL_ORIGINS))

    def test_a_lan_address_is_refused_until_it_is_allowed(self) -> None:
        # The phone case: reachable, but not on the list, so writes are refused.
        self.assertFalse(_origin_allowed("http://192.168.1.20:8765", _LOCAL_ORIGINS))


class TestOriginAllowedWhenWidened(unittest.TestCase):
    """`--allow-origin` lets exactly the named host through, and no more."""

    def setUp(self) -> None:
        # What main() builds from `--allow-origin 100.64.0.1`.
        self.allowed = _LOCAL_ORIGINS | frozenset({_host_of("100.64.0.1")})

    def test_the_allowed_remote_origin_passes(self) -> None:
        self.assertTrue(_origin_allowed("http://100.64.0.1:8765", self.allowed))

    def test_its_port_does_not_matter(self) -> None:
        # Allowed by host; the dev proxy on :5173 must work the same as :8765.
        self.assertTrue(_origin_allowed("http://100.64.0.1:5173", self.allowed))

    def test_loopback_still_passes(self) -> None:
        # Widening adds; it never replaces. This machine keeps working.
        self.assertTrue(_origin_allowed("http://127.0.0.1:8765", self.allowed))

    def test_a_different_foreign_origin_is_still_refused(self) -> None:
        # Only what you named is let in; the network at large is not.
        self.assertFalse(_origin_allowed("http://192.168.1.20:8765", self.allowed))


class TestPublicPathServesTheDemo(unittest.TestCase):
    """The demo is reachable without a password, and it is the app that answers."""

    def test_the_demo_route_is_the_app_itself(self) -> None:
        # Not a second, simpler page: the visitor gets the real shell, and the
        # bundle it loads decides it is a demo (web/ui/src/lib/demo.js).
        self.assertEqual(public_path("/demo"), "/index.html")

    def test_a_path_under_the_demo_is_the_app_too(self) -> None:
        # The client router's business, not ours.
        self.assertEqual(public_path("/demo/anything"), "/index.html")

    def test_the_assets_that_draw_it_are_public(self) -> None:
        # A demo the browser cannot fetch the bundle for is a blank page.
        for path in ("/assets/index-abc123.js", "/assets/index-abc123.css"):
            with self.subTest(path=path):
                self.assertEqual(public_path(path), path)

    def test_the_font_and_the_gradient_are_public(self) -> None:
        # The login form itself wears these, before anyone has logged in at all.
        self.assertEqual(public_path("/fonts/Zodiak-Variable.woff2"), "/fonts/Zodiak-Variable.woff2")
        self.assertEqual(public_path("/bg-gradient.svg"), "/bg-gradient.svg")


class TestPublicPathServesNothingElse(unittest.TestCase):
    """Code is public; a minute of your time is not. `None` means the login form."""

    def test_the_bare_url_is_the_login_form(self) -> None:
        # The URL you share opens on the password box, not on a flash of the app.
        self.assertIsNone(public_path("/"))

    def test_the_shell_cannot_be_asked_for_by_name(self) -> None:
        # `.html` is off the list, so the app has exactly one public route and
        # spelling it a different way does not find a second one.
        self.assertIsNone(public_path("/index.html"))

    def test_the_api_is_not_public(self) -> None:
        # It carries the actual sessions, and it is the thing the password is for.
        # (The handler 401s these before ever asking; this is the second lock.)
        self.assertIsNone(public_path("/api/status"))
        self.assertIsNone(public_path("/api/sessions"))

    def test_a_route_that_merely_starts_like_the_demo_is_not_the_demo(self) -> None:
        # `/demonstration` is not `/demo`, and a prefix match would have said it was.
        self.assertIsNone(public_path("/demonstrably-not"))

    def test_an_arbitrary_path_is_the_login_form(self) -> None:
        # No SPA fallback for a visitor without the password: a miss is a miss.
        self.assertIsNone(public_path("/sessions/2026-07-14_20-58-29"))

    def test_json_is_not_public_however_it_is_spelled(self) -> None:
        # The demo draws nothing out of a .json, and the tracker's every file is
        # one. Two locks already stand between these names and the data they are
        # named after -- the API needs a cookie, and _resolve_static cannot escape
        # dist -- and this is a third, which costs a line.
        self.assertIsNone(public_path("/current.json"))
        self.assertIsNone(public_path("/sessions/2026-07-14_20-58-29.json"))

    def test_a_sourcemap_is_not_public(self) -> None:
        # Not a security boundary -- it is the same code either way -- but the
        # demo does not need it, and the set is what the demo needs.
        self.assertIsNone(public_path("/assets/index-abc123.js.map"))


class _Connection:
    """A socket that is not one: it hands over a request and keeps the reply.

    :class:`~web.server.ViewerHandler` is the adapter between the guards above and
    HTTP, and the adapter is exactly what the pure-function tests cannot reach --
    that a rule *exists* and that ``do_POST`` *consults* it are two claims, and the
    second one is the feature. So this drives the real handler, with its real
    routing and its real status codes, over a socket made of two ``BytesIO``.

    Still no port bound and no browser driven, which is the rule this suite keeps.
    ``http.server`` asks a connection for exactly three things -- ``makefile`` for
    the request, ``sendall`` for the reply, ``close`` at the end -- so those three
    are the whole of it.
    """

    def __init__(self, request: bytes) -> None:
        self._incoming = BytesIO(request)
        self.reply = BytesIO()

    def makefile(self, mode: str = "rb", bufsize: int = -1) -> BytesIO:
        return self._incoming

    def sendall(self, data: bytes) -> None:
        self.reply.write(data)

    def close(self) -> None:
        pass


class HandlerTestCase(unittest.TestCase):
    """A case that can send a request to the handler and read the answer back."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.storage = Storage(Path(self._tmp.name))
        self.auth = Authenticator(
            password_hashes={
                OWNER: hash_password("owner-pw"),
                VIEWER: hash_password("viewer-pw"),
            },
            secret=b"a-32-byte-secret-for-the-tests!!",
            cookie_secure=False,
        )

    def cookie_for(self, role: str) -> str:
        """The ``Cookie`` header a browser signed in as ``role`` would send back."""
        return self.auth.session_cookie(role).split(";", 1)[0]

    def send(
        self,
        method: str,
        path: str,
        body: Optional[Dict[str, Any]] = None,
        cookie: Optional[str] = None,
        auth: Optional[Authenticator] = None,
        open_mode: bool = False,
    ) -> Tuple[int, Optional[Dict[str, Any]]]:
        """Drive one request through the handler; return its status and JSON body.

        ``open_mode`` is the no-password server -- the one this tool has always
        been on loopback -- which is a different thing from "no cookie".
        """
        encoded = json.dumps(body).encode("utf-8") if body is not None else b""
        lines = [f"{method} {path} HTTP/1.1", "Host: 127.0.0.1"]
        if body is not None:
            lines.append("Content-Type: application/json")
            lines.append(f"Content-Length: {len(encoded)}")
        if cookie is not None:
            lines.append(f"Cookie: {cookie}")
        raw = ("\r\n".join(lines) + "\r\n\r\n").encode("utf-8") + encoded

        connection = _Connection(raw)
        # The handler logs a line per request to stderr, which is the right thing
        # for a server and a wall of noise in a test run. Swallowed here rather
        # than stubbed out on the handler: log_message is not what is under test,
        # but it should still be the real one running.
        with redirect_stderr(StringIO()):
            ViewerHandler(
                connection,
                ("127.0.0.1", 54321),
                None,
                storage=self.storage,
                allowed_origins=_LOCAL_ORIGINS,
                auth=None if open_mode else (auth or self.auth),
            )

        reply = connection.reply.getvalue()
        status = int(reply.split(b" ", 2)[1])
        _, _, payload = reply.partition(b"\r\n\r\n")
        try:
            return status, json.loads(payload.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return status, None


class TestTheViewerMayNotWrite(HandlerTestCase):
    """The feature, at the surface that enforces it: every write, refused."""

    #: Every command web.api will run. Named here rather than imported from
    #: web.api.COMMANDS on purpose: adding a command should make this test fail
    #: until someone has decided whether a viewer may send it.
    WRITES = ("start", "pause", "resume", "toggle", "stop", "task")

    def test_a_viewer_is_refused_every_write(self) -> None:
        for command in self.WRITES:
            status, body = self.send(
                "POST", f"/api/{command}", body={}, cookie=self.cookie_for(VIEWER)
            )
            self.assertEqual(403, status, f"{command} was not refused")
            self.assertIn("only look", body["error"])

    def test_the_owner_is_refused_none_of_them(self) -> None:
        # The control. A 403 for everyone would pass the test above and be useless.
        status, _ = self.send(
            "POST", "/api/start", body={"task": "real"}, cookie=self.cookie_for(OWNER)
        )
        self.assertEqual(200, status)

    def test_a_viewers_write_never_reaches_the_tracker(self) -> None:
        # Not merely reported as refused -- refused. Nothing on disk moved.
        self.send("POST", "/api/start", body={}, cookie=self.cookie_for(VIEWER))
        self.assertIsNone(self.storage.load_current())

    def test_no_cookie_is_a_401_and_not_a_403(self) -> None:
        # The two refusals are different sentences: 401 means "log in", which is
        # advice a viewer would only be misled by.
        status, _ = self.send("POST", "/api/start", body={})
        self.assertEqual(401, status)

    def test_a_forged_promotion_is_no_session_at_all(self) -> None:
        forged = self.cookie_for(VIEWER).replace(VIEWER, OWNER)
        status, _ = self.send("POST", "/api/start", body={}, cookie=forged)
        self.assertEqual(401, status)

    def test_a_viewer_may_still_sign_itself_out(self) -> None:
        # Ending your own session is not a power over the tracker.
        status, _ = self.send("POST", "/api/logout", cookie=self.cookie_for(VIEWER))
        self.assertEqual(200, status)


class TestTheStatusPayloadNamesTheAccount(HandlerTestCase):
    """What the app draws itself from, and both accounts read alike."""

    def test_a_viewer_reads_the_status_in_full(self) -> None:
        self.send("POST", "/api/start", body={"task": "secret"}, cookie=self.cookie_for(OWNER))
        status, body = self.send("GET", "/api/status", cookie=self.cookie_for(VIEWER))
        self.assertEqual(200, status)
        self.assertEqual("running", body["state"])
        # A viewer sees the task text: this account is read-only, not redacted.
        self.assertEqual("secret", body["task"])

    def test_the_status_names_the_role_reading_it(self) -> None:
        for role in (OWNER, VIEWER):
            _, body = self.send("GET", "/api/status", cookie=self.cookie_for(role))
            self.assertEqual(role, body["role"])
            self.assertTrue(body["login"])

    def test_an_open_server_is_the_owner_and_has_no_login(self) -> None:
        # The loopback tool as it always was: no password, so nobody to withhold
        # anything from, and no Sign out button to draw.
        status, body = self.send("GET", "/api/status", open_mode=True)
        self.assertEqual(200, status)
        self.assertEqual(OWNER, body["role"])
        self.assertFalse(body["login"])

    def test_an_open_server_still_writes(self) -> None:
        status, _ = self.send("POST", "/api/start", body={}, open_mode=True)
        self.assertEqual(200, status)


class TestLoginNamesItsAccount(HandlerTestCase):
    """One password per door, and the throttle counts the client, not the door."""

    def test_each_password_opens_its_own_account(self) -> None:
        for role, password in ((OWNER, "owner-pw"), (VIEWER, "viewer-pw")):
            status, body = self.send(
                "POST", "/api/login", body={"account": role, "password": password}
            )
            self.assertEqual(200, status)
            self.assertEqual(role, body["role"])

    def test_a_password_does_not_open_the_other_account(self) -> None:
        status, _ = self.send(
            "POST", "/api/login", body={"account": OWNER, "password": "viewer-pw"}
        )
        self.assertEqual(401, status)

    def test_a_body_with_no_account_is_the_owners_door(self) -> None:
        # The body the single-account form has always sent, and what a curl in
        # somebody's notes still sends. It needs the owner's password, as before.
        status, body = self.send("POST", "/api/login", body={"password": "owner-pw"})
        self.assertEqual(200, status)
        self.assertEqual(OWNER, body["role"])

    def test_the_viewers_password_does_not_work_at_that_door_either(self) -> None:
        status, _ = self.send("POST", "/api/login", body={"password": "viewer-pw"})
        self.assertEqual(401, status)

    def test_trying_the_other_account_buys_no_fresh_guesses(self) -> None:
        # The claim the throttle makes, at the level that decides it. `_handle_login`
        # keys on the client alone, so failures against *either* account fall in one
        # budget -- key it on the account too and the last assertion here goes green
        # at 401, which is the regression this exists to catch.
        for attempt in range(self.auth.throttle.max_failures):
            # Alternating doors, deliberately: an attacker would.
            account = OWNER if attempt % 2 else VIEWER
            status, _ = self.send(
                "POST", "/api/login", body={"account": account, "password": f"wrong{attempt}"}
            )
            self.assertEqual(401, status)

        status, body = self.send(
            "POST", "/api/login", body={"account": OWNER, "password": "wrong-again"}
        )
        self.assertEqual(429, status)
        self.assertIn("too many attempts", body["error"])

    def test_the_lockout_cannot_be_worn_down_by_the_right_password(self) -> None:
        for attempt in range(self.auth.throttle.max_failures):
            self.send("POST", "/api/login", body={"account": VIEWER, "password": "no"})
        # Correct, and still refused: the throttle is consulted before the password.
        status, _ = self.send(
            "POST", "/api/login", body={"account": OWNER, "password": "owner-pw"}
        )
        self.assertEqual(429, status)


class TestTheLoginFormAsksForWhatIsConfigured(unittest.TestCase):
    """One account is one password box; two is a choice between them."""

    def test_one_account_is_asked_for_without_a_choice(self) -> None:
        page = _login_page((OWNER,))
        self.assertNotIn('type="radio"', page)
        self.assertIn("Enter the password to continue.", page)

    def test_one_account_still_names_itself_in_the_request(self) -> None:
        # The hidden field is why the server has one shape of login body to read
        # rather than two. The form always says which account it is signing in to.
        self.assertIn(f'<input type="hidden" name="account" value="{OWNER}">', _picker((OWNER,)))

    def test_two_accounts_are_offered_as_a_choice(self) -> None:
        page = _login_page((OWNER, VIEWER))
        self.assertIn(f'value="{OWNER}"', page)
        self.assertIn(f'value="{VIEWER}"', page)
        self.assertIn("Choose an account and enter its password.", page)

    def test_the_owner_is_the_one_already_picked(self) -> None:
        # A radiogroup with nothing checked can be submitted with no value at all,
        # and the common case is you signing in to your own tracker.
        picker = _picker((OWNER, VIEWER))
        self.assertIn(f'value="{OWNER}" data-note="Full control — start, pause and stop the day." checked', picker)
        self.assertNotIn("checked", picker.split(VIEWER, 1)[1])

    def test_each_account_carries_its_own_explanation(self) -> None:
        # The script shows these; nothing here should be the only copy of the text.
        picker = _picker((OWNER, VIEWER))
        self.assertIn("Read-only — see the hours, change nothing.", picker)

    def test_no_slot_is_left_unfilled(self) -> None:
        # The page is built by replace(), so a renamed sentinel would fail silently
        # and ship an HTML comment where the password picker should be.
        for accounts in ((OWNER,), (OWNER, VIEWER)):
            page = _login_page(accounts)
            self.assertNotIn("<!--picker-->", page)
            self.assertNotIn("<!--caption-->", page)

    def test_the_form_never_offers_a_door_with_no_password_behind_it(self) -> None:
        # `accounts` comes from the configured hashes (see Authenticator.accounts),
        # so an unconfigured viewer is not merely unchecked -- it is not there.
        self.assertNotIn(VIEWER, _login_page((OWNER,)))


if __name__ == "__main__":
    unittest.main()
