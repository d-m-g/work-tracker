"""Password login for the web viewer: the whole of it, and none of it in a socket.

The viewer was born loopback-only, and its one security decision was *which
origin may write* (see :mod:`web.server`). Putting it on the public internet adds
a second, larger question -- *who may look at all* -- and this module answers it.

The shape mirrors :mod:`web.api`: everything here is a pure function of its
arguments, so the security-critical parts -- hashing a password, signing a
session, deciding whether a cookie is still valid -- are asserted directly in the
tests, with no port bound and no browser driven. What :mod:`web.server` adds on
top is a thin adapter: read the ``Cookie`` header, turn a ``False`` into a 401,
write a ``Set-Cookie`` on the way out.

The threat model
----------------

The tool is a personal one, but a public URL is a public URL. So:

* **Passwords are never stored, only their PBKDF2-HMAC hashes are.** A leaked
  data directory does not leak the password.
* **Sessions are stateless, signed cookies.** The server keeps a random secret
  and signs ``{expiry}`` with it; a cookie the server did not sign is worthless,
  and one whose expiry has passed is refused. There is no session table to leak
  and none to grow without bound.
* **Login is rate-limited per client.** Guessing the password by brute force is
  slowed to a crawl and, past a threshold, locked out for a window.
* **Every comparison that touches a secret is constant-time** (:func:`hmac.compare_digest`),
  so neither the password check nor the signature check leaks its answer through
  how long it took.

Auth is *opt-in*. With no password configured the viewer behaves exactly as it
always has -- loopback, no login -- so driving it locally from the CLI and the
Shortcuts is unchanged. Configure a password and the same server refuses every
request, read or write, that does not carry a valid session. That is the switch
:mod:`web.server` throws when you deploy it somewhere the network can reach.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import secrets
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from http.cookies import CookieError, SimpleCookie
from pathlib import Path
from typing import Callable, Dict, List, Optional

from tracker.utils import now

__all__ = [
    "COOKIE_NAME",
    "Authenticator",
    "LoginThrottle",
    "hash_password",
    "issue_token",
    "load_or_create_secret",
    "load_password_hash",
    "throttle_key",
    "verify_password",
    "verify_token",
    "write_private",
]

#: A clock is anything that answers with a timezone-aware "now" -- the same
#: injectable seam the tracker uses, so a test can pin time and assert on an
#: exact expiry instead of sleeping.
Clock = Callable[[], datetime]

#: The cookie the browser carries to prove it logged in. HttpOnly, so script on
#: the page can never read it; SameSite=Strict, so another site cannot make the
#: browser send it along with a forged request.
COOKIE_NAME = "wt_session"

#: Peer addresses that mean "this request came through a proxy on this machine".
#: Only for one of these do we believe an X-Forwarded-For header.
_LOOPBACK_PEERS = frozenset({"127.0.0.1", "::1"})


def throttle_key(peer: Optional[str], forwarded_for: Optional[str]) -> str:
    """Choose the key the login throttle counts a client under.

    In the intended deployment -- Caddy on loopback terminating TLS in front of a
    server bound to 127.0.0.1 -- every request's peer *is* 127.0.0.1. Keying the
    throttle on the peer alone would therefore count every visitor on Earth as the
    same client: one attacker's failures would lock out the real user, and the
    per-client limit would be meaningless. So when, and only when, the immediate
    peer is loopback do we believe ``X-Forwarded-For`` and key on the address the
    proxy reported. A server that is *directly* exposed (its peer is a real remote
    address) ignores the header completely -- there a client can put anything in
    it and rotate it to slip the throttle, so it is worth nothing.

    We take the *last* hop of the header: that is the address our own proxy
    observed and appended. Everything to its left is hearsay the client supplied.
    """
    peer = peer or "unknown"
    if peer in _LOOPBACK_PEERS and forwarded_for:
        last = forwarded_for.split(",")[-1].strip()
        if last:
            return last
    return peer

# ---------------------------------------------------------------------------
# passwords
# ---------------------------------------------------------------------------

#: PBKDF2 parameters. The iteration count is deliberately high: a login happens
#: once per browser per month, so a tenth of a second spent here is invisible to
#: the user and expensive for anyone grinding guesses against a stolen hash.
_SCHEME = "pbkdf2_sha256"
_PBKDF2_ALGO = "sha256"
_PBKDF2_ITERATIONS = 240_000
_SALT_BYTES = 16


def hash_password(password: str, *, iterations: int = _PBKDF2_ITERATIONS) -> str:
    """Hash ``password`` into a self-describing string safe to store on disk.

    The salt and the iteration count travel with the hash, so a stored value can
    always be verified even if the defaults here change later. The format is
    ``pbkdf2_sha256$<iterations>$<salt-hex>$<hash-hex>``.

    Raises:
        ValueError: If ``password`` is empty. A blank password is not a password.
    """
    if not password:
        raise ValueError("refusing to hash an empty password")
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, iterations)
    return f"{_SCHEME}${iterations}${salt.hex()}${digest.hex()}"


def verify_password(password: str, encoded: str) -> bool:
    """Check ``password`` against a value produced by :func:`hash_password`.

    A malformed or unrecognised ``encoded`` string is a ``False``, never an
    exception: whatever went wrong, the answer to "may this password in?" is no.
    The final comparison is constant-time, so the check cannot be turned into an
    oracle by timing how quickly it fails.
    """
    try:
        scheme, raw_iterations, salt_hex, digest_hex = encoded.split("$")
        if scheme != _SCHEME:
            return False
        iterations = int(raw_iterations)
        salt = bytes.fromhex(salt_hex)
        expected = bytes.fromhex(digest_hex)
    except (ValueError, AttributeError):
        return False
    candidate = hashlib.pbkdf2_hmac(_PBKDF2_ALGO, password.encode("utf-8"), salt, iterations)
    return hmac.compare_digest(candidate, expected)


# ---------------------------------------------------------------------------
# session tokens
# ---------------------------------------------------------------------------


def _sign(secret: bytes, payload: str) -> str:
    """Return the hex HMAC-SHA256 of ``payload`` under ``secret``."""
    return hmac.new(secret, payload.encode("ascii"), hashlib.sha256).hexdigest()


def issue_token(secret: bytes, expires_at: datetime) -> str:
    """Mint a session token that is valid until ``expires_at``.

    The token is ``{expiry-epoch}.{signature}``. It carries no identity and no
    secret of its own -- only a moment and a proof, signed by the server, that
    the server is the one who said so. There is nothing in it worth stealing that
    stealing the whole cookie would not already give you.
    """
    payload = str(int(expires_at.timestamp()))
    return f"{payload}.{_sign(secret, payload)}"


def verify_token(secret: bytes, token: str, at: datetime) -> bool:
    """Decide whether ``token`` is one this server signed and has not expired.

    Two ways to fail, both a plain ``False``: the signature does not match (the
    token was forged or tampered with), or the expiry has passed. The signature
    is checked first and in constant time, so an attacker learns nothing by
    watching how the check fails.
    """
    if not token or "." not in token:
        return False
    payload, _, signature = token.partition(".")
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        return False
    try:
        expiry = int(payload)
    except ValueError:
        return False
    return at.timestamp() < expiry


# ---------------------------------------------------------------------------
# rate limiting
# ---------------------------------------------------------------------------


@dataclass
class LoginThrottle:
    """Slow, then stop, repeated failed logins from one client.

    It keeps only the recent *failures* per key (an IP, say), forgetting them
    once they age out of the window. Under the threshold a login may be tried at
    once; at or above it, the client must wait until the oldest failure expires.
    A success clears the slate immediately, so a fat-fingered password a few
    times running never locks out the person who then gets it right.

    State is in-memory and per-process, which is the right size for a
    single-instance personal tool: nothing to persist, nothing to leak, and a
    restart simply forgives everyone.
    """

    max_failures: int = 5
    window_seconds: float = 300.0
    #: A monotonic clock in seconds. Monotonic, not wall-clock, so the lockout
    #: cannot be shortened by the system time jumping backwards.
    clock: Callable[[], float] = field(default=None)  # type: ignore[assignment]
    _failures: Dict[str, List[float]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.clock is None:
            import time

            self.clock = time.monotonic

    def _recent(self, key: str, moment: float) -> List[float]:
        """Return this key's failures still inside the window, pruning the rest."""
        recent = [t for t in self._failures.get(key, []) if moment - t < self.window_seconds]
        if recent:
            self._failures[key] = recent
        else:
            self._failures.pop(key, None)
        return recent

    def retry_after(self, key: str) -> float:
        """Seconds ``key`` must wait before a login is allowed, or ``0`` if now."""
        moment = self.clock()
        recent = self._recent(key, moment)
        if len(recent) < self.max_failures:
            return 0.0
        return max(0.0, self.window_seconds - (moment - min(recent)))

    def record_failure(self, key: str) -> None:
        """Note that a login from ``key`` just failed."""
        moment = self.clock()
        self._recent(key, moment)
        self._failures.setdefault(key, []).append(moment)

    def record_success(self, key: str) -> None:
        """Forgive ``key`` entirely: a correct password clears its failures."""
        self._failures.pop(key, None)


# ---------------------------------------------------------------------------
# the authenticator
# ---------------------------------------------------------------------------


@dataclass
class Authenticator:
    """Everything the server needs to run a login: the hash, the secret, the rules.

    One of these is built at startup when a password is configured, and handed to
    every request handler. When none is built, the server runs open exactly as it
    did before -- so the presence of this object *is* the on switch.
    """

    password_hash: str
    secret: bytes
    ttl: timedelta = timedelta(days=30)
    #: Mark the cookie ``Secure`` so the browser only ever sends it over HTTPS.
    #: On for the real deployment (Caddy terminates TLS in front); turn it off
    #: only to test over plain ``http`` on localhost, where a Secure cookie would
    #: never come back and the login would appear to silently fail.
    cookie_secure: bool = True
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    clock: Clock = now

    def verify_login(self, password: object) -> bool:
        """True if ``password`` is the configured one. Non-strings are just wrong."""
        if not isinstance(password, str):
            return False
        return verify_password(password, self.password_hash)

    def is_authenticated(self, cookie_header: Optional[str]) -> bool:
        """True if the request's ``Cookie`` header carries a live session."""
        token = _read_cookie(cookie_header, COOKIE_NAME)
        if token is None:
            return False
        return verify_token(self.secret, token, self.clock())

    def session_cookie(self) -> str:
        """A ``Set-Cookie`` value that logs the browser in for :attr:`ttl`."""
        token = issue_token(self.secret, self.clock() + self.ttl)
        return self._cookie(token, int(self.ttl.total_seconds()))

    def logout_cookie(self) -> str:
        """A ``Set-Cookie`` value that clears the session immediately."""
        return self._cookie("", 0)

    def _cookie(self, value: str, max_age: int) -> str:
        attributes = [
            f"{COOKIE_NAME}={value}",
            "Path=/",
            f"Max-Age={max_age}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if self.cookie_secure:
            attributes.append("Secure")
        return "; ".join(attributes)


def _read_cookie(header: Optional[str], name: str) -> Optional[str]:
    """Pull one cookie's value out of a ``Cookie`` header, or ``None``.

    A header that does not parse yields ``None`` rather than raising: a garbled
    cookie jar is treated as no cookie at all, which fails closed.
    """
    if not header:
        return None
    jar: SimpleCookie = SimpleCookie()
    try:
        jar.load(header)
    except CookieError:
        return None
    morsel = jar.get(name)
    return morsel.value if morsel else None


# ---------------------------------------------------------------------------
# loading secrets and credentials
# ---------------------------------------------------------------------------


def write_private(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` readable and writable by its owner alone (0600).

    Used for the session secret and, when the CLI writes it, the password hash:
    both are files that anyone else reading is a compromise, so they are created
    ``0600`` from the start rather than written wide and narrowed afterwards.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.write(text)
    # A pre-existing file keeps its old mode through O_CREAT, so pin it explicitly.
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def load_or_create_secret(path: Path) -> bytes:
    """Return the server's signing secret, generating and persisting it if absent.

    Persisting it means sessions survive a restart: the box can reboot without
    logging everyone out. A missing file is created; a present-but-garbled one is
    replaced -- regenerating the secret only invalidates existing cookies, which
    is safe, where trusting a corrupt secret would not be.
    """
    try:
        text = path.read_text(encoding="utf-8").strip()
        if text:
            return bytes.fromhex(text)
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        pass
    secret = secrets.token_bytes(32)
    write_private(path, secret.hex())
    return secret


def load_password_hash(env_value: Optional[str], file_path: Optional[Path]) -> Optional[str]:
    """Find the configured password hash: the environment first, then a file.

    Returns ``None`` when neither is set, which the server reads as "no password
    configured, run open". An empty or missing file is the same as absent.
    """
    if env_value:
        return env_value.strip()
    if file_path is not None:
        try:
            text = file_path.read_text(encoding="utf-8").strip()
        except (FileNotFoundError, OSError):
            return None
        return text or None
    return None


# ---------------------------------------------------------------------------
# CLI: generate a password hash
# ---------------------------------------------------------------------------


def main(argv: Optional[List[str]] = None) -> int:
    """Prompt for a password and print (or write) its hash.

    Run it once to create the credential the server checks against::

        python3 -m web.auth                 # prints the hash line
        python3 -m web.auth --write .password

    The password is read with :func:`getpass.getpass`, so it never appears on the
    command line or in the shell history, and only its hash ever leaves here.
    """
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description="Generate a work-tracker login password hash.")
    parser.add_argument(
        "--write",
        type=Path,
        metavar="PATH",
        help="write the hash to PATH (created 0600) instead of printing it",
    )
    args = parser.parse_args(argv)

    password = getpass.getpass("Choose a password: ")
    if not password:
        print("error: an empty password is not allowed", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat the password: ") != password:
        print("error: the passwords do not match", file=sys.stderr)
        return 1

    encoded = hash_password(password)
    if args.write:
        write_private(args.write, encoded + "\n")
        print(f"wrote the password hash to {args.write} (mode 0600)")
        print("start the server with --password-file to require this password.")
    else:
        print(encoded)
        print(
            "set WORK_TRACKER_PASSWORD_HASH to this value, or save it to a file and "
            "pass --password-file, to require it.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
