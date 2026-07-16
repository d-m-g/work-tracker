"""Tests for the login layer: hashing, session tokens, throttling, the cookie.

Like the rest of the suite these bind no port and drive no browser. Every
security decision in :mod:`web.auth` is a pure function or a small object with an
injected clock, precisely so it can be asserted head-on -- "this forged token is
refused", "this expired one is refused", "the sixth wrong password is locked
out" -- instead of inferred from an HTTP round-trip.
"""

from __future__ import annotations

import unittest
from datetime import timedelta
from pathlib import Path
from tempfile import TemporaryDirectory

from support import EPOCH, FakeClock

from web.auth import (
    COOKIE_NAME,
    Authenticator,
    LoginThrottle,
    hash_password,
    issue_token,
    load_or_create_secret,
    load_password_hash,
    throttle_key,
    verify_password,
    verify_token,
    write_private,
)


class FakeMonotonic:
    """A movable stand-in for time.monotonic, in seconds."""

    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class TestPasswordHashing(unittest.TestCase):
    """A password is stored only as a salted hash, and checked in constant time."""

    def test_the_right_password_verifies(self) -> None:
        encoded = hash_password("correct horse battery staple")
        self.assertTrue(verify_password("correct horse battery staple", encoded))

    def test_the_wrong_password_does_not(self) -> None:
        encoded = hash_password("correct horse battery staple")
        self.assertFalse(verify_password("Correct Horse Battery Staple", encoded))

    def test_the_plaintext_is_nowhere_in_the_hash(self) -> None:
        encoded = hash_password("hunter2")
        self.assertNotIn("hunter2", encoded)

    def test_two_hashes_of_one_password_differ(self) -> None:
        # A fresh salt each time, so identical passwords do not collide on disk.
        self.assertNotEqual(hash_password("same"), hash_password("same"))

    def test_an_empty_password_is_refused(self) -> None:
        with self.assertRaises(ValueError):
            hash_password("")

    def test_a_garbled_hash_is_false_not_an_error(self) -> None:
        for junk in ("", "not-a-hash", "pbkdf2_sha256$only$two", "$$$$"):
            self.assertFalse(verify_password("whatever", junk))


class TestSessionTokens(unittest.TestCase):
    """A token is a signed expiry: forge it or outlive it and it is refused."""

    def setUp(self) -> None:
        self.secret = b"a-32-byte-secret-for-the-tests!!"
        self.clock = FakeClock()

    def test_a_freshly_issued_token_verifies(self) -> None:
        token = issue_token(self.secret, self.clock() + timedelta(days=30))
        self.assertTrue(verify_token(self.secret, token, self.clock()))

    def test_an_expired_token_is_refused(self) -> None:
        token = issue_token(self.secret, self.clock() + timedelta(hours=1))
        later = self.clock() + timedelta(hours=2)
        self.assertFalse(verify_token(self.secret, token, later))

    def test_a_token_signed_with_another_secret_is_refused(self) -> None:
        token = issue_token(b"a-different-secret-of-the-right-x", self.clock() + timedelta(days=1))
        self.assertFalse(verify_token(self.secret, token, self.clock()))

    def test_a_tampered_expiry_is_refused(self) -> None:
        token = issue_token(self.secret, self.clock() + timedelta(hours=1))
        payload, _, signature = token.partition(".")
        forged = f"{int(payload) + 10_000}.{signature}"
        self.assertFalse(verify_token(self.secret, forged, self.clock()))

    def test_garbage_is_refused(self) -> None:
        for junk in ("", "no-dot", "abc.def", "."):
            self.assertFalse(verify_token(self.secret, junk, self.clock()))


class TestLoginThrottle(unittest.TestCase):
    """Failed logins are counted per key, and past a threshold locked out."""

    def setUp(self) -> None:
        self.clock = FakeMonotonic()
        self.throttle = LoginThrottle(max_failures=3, window_seconds=60.0, clock=self.clock)

    def test_below_the_threshold_a_login_is_allowed(self) -> None:
        self.throttle.record_failure("ip")
        self.throttle.record_failure("ip")
        self.assertEqual(0.0, self.throttle.retry_after("ip"))

    def test_at_the_threshold_it_locks_out(self) -> None:
        for _ in range(3):
            self.throttle.record_failure("ip")
        self.assertGreater(self.throttle.retry_after("ip"), 0.0)

    def test_the_lockout_lifts_once_the_window_passes(self) -> None:
        for _ in range(3):
            self.throttle.record_failure("ip")
        self.clock.advance(61.0)
        self.assertEqual(0.0, self.throttle.retry_after("ip"))

    def test_a_success_forgives_the_failures(self) -> None:
        for _ in range(3):
            self.throttle.record_failure("ip")
        self.throttle.record_success("ip")
        self.assertEqual(0.0, self.throttle.retry_after("ip"))

    def test_clients_are_counted_apart(self) -> None:
        for _ in range(3):
            self.throttle.record_failure("attacker")
        # One client's lockout never touches another's.
        self.assertEqual(0.0, self.throttle.retry_after("someone-else"))


class TestThrottleKey(unittest.TestCase):
    """Which client a login failure is counted against, proxy or not."""

    def test_a_direct_client_is_its_own_peer(self) -> None:
        self.assertEqual("203.0.113.7", throttle_key("203.0.113.7", None))

    def test_a_direct_client_ignores_a_spoofed_forwarded_header(self) -> None:
        # Not behind our proxy: the header is attacker-controlled, so it is dropped.
        self.assertEqual("203.0.113.7", throttle_key("203.0.113.7", "1.2.3.4"))

    def test_behind_a_loopback_proxy_the_forwarded_client_is_used(self) -> None:
        self.assertEqual("198.51.100.9", throttle_key("127.0.0.1", "198.51.100.9"))

    def test_the_last_forwarded_hop_wins(self) -> None:
        # client, proxy1: the last is what our own proxy observed and appended.
        self.assertEqual("10.0.0.2", throttle_key("127.0.0.1", "1.2.3.4, 10.0.0.2"))

    def test_loopback_with_no_header_stays_loopback(self) -> None:
        self.assertEqual("127.0.0.1", throttle_key("127.0.0.1", None))

    def test_a_missing_peer_is_named_not_crashed(self) -> None:
        self.assertEqual("unknown", throttle_key(None, None))


class TestAuthenticator(unittest.TestCase):
    """The object the server holds: password in, cookie out, cookie back in."""

    def setUp(self) -> None:
        self.clock = FakeClock()
        self.auth = Authenticator(
            password_hash=hash_password("swordfish"),
            secret=b"a-32-byte-secret-for-the-tests!!",
            clock=self.clock,
        )

    def test_the_right_password_is_accepted(self) -> None:
        self.assertTrue(self.auth.verify_login("swordfish"))

    def test_a_wrong_password_is_rejected(self) -> None:
        self.assertFalse(self.auth.verify_login("guess"))

    def test_a_non_string_password_is_rejected(self) -> None:
        # The body's "password" field could be anything JSON allows.
        for value in (None, 42, {"a": 1}, ["x"], True):
            self.assertFalse(self.auth.verify_login(value))

    def test_a_fresh_session_cookie_authenticates(self) -> None:
        header = _cookie_header(self.auth.session_cookie())
        self.assertTrue(self.auth.is_authenticated(header))

    def test_no_cookie_is_not_authenticated(self) -> None:
        self.assertFalse(self.auth.is_authenticated(None))
        self.assertFalse(self.auth.is_authenticated(""))

    def test_an_unrelated_cookie_is_not_authenticated(self) -> None:
        self.assertFalse(self.auth.is_authenticated("other=value; theme=dark"))

    def test_a_session_expires(self) -> None:
        header = _cookie_header(self.auth.session_cookie())
        self.clock.advance(int(timedelta(days=31).total_seconds()))
        self.assertFalse(self.auth.is_authenticated(header))

    def test_logout_clears_the_cookie(self) -> None:
        logout = self.auth.logout_cookie()
        self.assertIn(f"{COOKIE_NAME}=;", logout + ";")
        self.assertIn("Max-Age=0", logout)

    def test_the_cookie_is_hardened(self) -> None:
        cookie = self.auth.session_cookie()
        self.assertIn("HttpOnly", cookie)
        self.assertIn("SameSite=Strict", cookie)
        self.assertIn("Secure", cookie)

    def test_insecure_cookie_drops_only_the_secure_flag(self) -> None:
        auth = Authenticator(
            password_hash=hash_password("x"),
            secret=b"another-32-byte-secret-for-tests",
            cookie_secure=False,
            clock=self.clock,
        )
        cookie = auth.session_cookie()
        self.assertNotIn("Secure", cookie)
        self.assertIn("HttpOnly", cookie)


class TestSecretFile(unittest.TestCase):
    """The signing secret is created once, persisted, and reused across restarts."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".session_secret"

    def test_it_is_created_when_missing(self) -> None:
        secret = load_or_create_secret(self.path)
        self.assertTrue(self.path.exists())
        self.assertEqual(32, len(secret))

    def test_it_is_stable_across_calls(self) -> None:
        # Reusing it is what lets sessions survive a restart.
        self.assertEqual(load_or_create_secret(self.path), load_or_create_secret(self.path))

    def test_it_is_written_owner_only(self) -> None:
        load_or_create_secret(self.path)
        self.assertEqual(0o600, self.path.stat().st_mode & 0o777)

    def test_a_corrupt_secret_is_replaced(self) -> None:
        self.path.write_text("not hex", encoding="utf-8")
        secret = load_or_create_secret(self.path)
        self.assertEqual(32, len(secret))


class TestLoadPasswordHash(unittest.TestCase):
    """Where the configured hash comes from: environment first, then a file."""

    def setUp(self) -> None:
        self._tmp = TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / ".password"

    def test_the_environment_wins(self) -> None:
        self.assertEqual("from-env", load_password_hash("from-env", self.path))

    def test_a_file_is_read_and_stripped(self) -> None:
        write_private(self.path, "  from-file\n")
        self.assertEqual("from-file", load_password_hash(None, self.path))

    def test_a_missing_file_is_none(self) -> None:
        self.assertIsNone(load_password_hash(None, self.path))

    def test_an_empty_file_is_none(self) -> None:
        write_private(self.path, "\n")
        self.assertIsNone(load_password_hash(None, self.path))

    def test_nothing_configured_is_none(self) -> None:
        self.assertIsNone(load_password_hash(None, None))


def _cookie_header(set_cookie: str) -> str:
    """Reduce a ``Set-Cookie`` value to the ``Cookie`` header a browser sends back.

    A browser echoes only ``name=value``, dropping every attribute; mimic that so
    the round trip under test is the real one.
    """
    return set_cookie.split(";", 1)[0]


if __name__ == "__main__":
    unittest.main()
