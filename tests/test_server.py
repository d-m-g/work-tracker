"""Tests for the server's one security decision: which origins may write.

Like the rest of the suite, these bind no port. The guard's logic lives in two
free functions -- :func:`web.server._host_of` and :func:`web.server._origin_allowed`
-- precisely so it can be asserted directly, without an HTTP round-trip. What the
socket wrapper adds on top (reading the header, turning a ``False`` into a 403) is
a two-line adapter over the decision proved here.

The through-line: driving the tracker from another device is opt-in and off by
default. With nothing allowed, this machine writes and no one else does -- the
shipped behaviour. `--allow-origin` widens the set by exactly what you name, and
never touches loopback.
"""

from __future__ import annotations

import unittest

from web.server import _LOCAL_ORIGINS, _host_of, _origin_allowed


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


if __name__ == "__main__":
    unittest.main()
