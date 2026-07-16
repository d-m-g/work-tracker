"""Tests for the server's two security decisions: who may write, and who may look.

Like the rest of the suite, these bind no port. Both guards live in free functions
-- :func:`web.server._host_of`, :func:`web.server._origin_allowed` and
:func:`web.server.public_path` -- precisely so they can be asserted directly,
without an HTTP round-trip. What the socket wrapper adds on top (reading a header,
turning a ``False`` into a 403, a ``None`` into the login form) is a two-line
adapter over the decisions proved here.

The through-lines:

* Driving the tracker from another device is opt-in and off by default. With
  nothing allowed, this machine writes and no one else does -- the shipped
  behaviour. ``--allow-origin`` widens the set by exactly what you name, and never
  touches loopback.
* A visitor without the password may have the app's *code* -- the demo runs on it,
  and it is published in this repository anyway -- and may never have a minute off
  your disk. :func:`~web.server.public_path` is where that line is drawn, so this
  is where it is defended.
"""

from __future__ import annotations

import unittest

from web.server import _LOCAL_ORIGINS, _host_of, _origin_allowed, public_path


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


if __name__ == "__main__":
    unittest.main()
