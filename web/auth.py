"""Password login for the web viewer: the whole of it, and none of it in a socket.

The viewer was born loopback-only, and its one security decision was *which
origin may write* (see :mod:`web.server`). Putting it on the public internet adds
a second, larger question -- *who may look at all* -- and this module answers it.

The shape mirrors :mod:`web.api`: everything here is a pure function of its
arguments, so the security-critical parts -- hashing a password, signing a
session, deciding whether a cookie is still valid, deciding what that session may
then *do* -- are asserted directly in the tests, with no port bound and no browser
driven. What :mod:`web.server` adds on top is a thin adapter: read the ``Cookie``
header, turn a ``None`` into a 401, a read-only role into a 403, write a
``Set-Cookie`` on the way out.

The two accounts
----------------

There are two, and the whole of the difference between them is one line
(:func:`may_write`):

* :data:`OWNER` -- you. Reads everything, and drives the tracker: start, pause,
  resume, stop, and naming what a session was spent on.
* :data:`VIEWER` -- whoever you gave the read-only password to. Reads exactly
  what you read, and cannot change a thing.

Each has its *own* password, and neither password is derivable from the other:
they are separate hashes, separately salted. Handing out the viewer password
gives away no part of the owner's, so the read-only account is a genuine
restriction rather than a UI that politely hides the buttons.

Which account a login is for is *named by the request*, not guessed at by trying
both hashes in turn. That is what keeps a login to a single PBKDF2 run --
deliberately an expensive one -- rather than one run per account on the way to a
"no". The account names are not secrets and are not treated as any: they are
printed on the login form. The password is the whole of the security.

The threat model
----------------

The tool is a personal one, but a public URL is a public URL. So:

* **Passwords are never stored, only their PBKDF2-HMAC hashes are.** A leaked
  data directory does not leak either password.
* **Sessions are stateless, signed cookies.** The server keeps a random secret
  and signs ``{expiry}.{role}`` with it; a cookie the server did not sign is
  worthless, and one whose expiry has passed is refused. There is no session
  table to leak and none to grow without bound.
* **The role is inside what is signed.** A viewer cannot promote itself to owner
  by editing its own cookie -- the edit breaks the signature, and the signature is
  checked before the role is so much as read.
* **Login is rate-limited per client, not per account.** Guessing a password by
  brute force is slowed to a crawl and, past a threshold, locked out for a window
  -- and naming a different account does not buy a fresh budget of guesses.
* **Every comparison that touches a secret is constant-time** (:func:`hmac.compare_digest`),
  so neither the password check nor the signature check leaks its answer through
  how long it took.

Auth is *opt-in*. With no password configured the viewer behaves exactly as it
always has -- loopback, no login -- so driving it locally from the CLI and the
Shortcuts is unchanged. Configure a password and the same server refuses every
request, read or write, that does not carry a valid session. That is the switch
:mod:`web.server` throws when you deploy it somewhere the network can reach. The
viewer account is opt-in on top of that: configure only an owner password and
there is one account and one password box, exactly as before.
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
from typing import Callable, Dict, List, Mapping, Optional, Tuple

from tracker.utils import now

__all__ = [
    "COOKIE_NAME",
    "OWNER",
    "ROLES",
    "VIEWER",
    "Authenticator",
    "LoginThrottle",
    "hash_password",
    "issue_token",
    "load_or_create_secret",
    "load_password_hash",
    "may_write",
    "throttle_key",
    "verified_role",
    "verify_password",
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

# ---------------------------------------------------------------------------
# roles
# ---------------------------------------------------------------------------

#: You: the account that drives the tracker. Always configured when there is a
#: login at all -- an installation with only a read-only account would be one
#: nobody could start a session from.
OWNER = "owner"

#: The read-only account. Sees the live session and the whole history, exactly as
#: the owner does, and is refused every write.
VIEWER = "viewer"

#: Every account there is, in the order the login form offers them. A role that is
#: not in here is not a role: it is refused at the door, whether it arrived in a
#: login body or in a cookie.
ROLES: Tuple[str, ...] = (OWNER, VIEWER)

#: Which accounts may change something. This frozenset *is* the read-only
#: account -- everything else about a viewer's session is identical to an owner's.
_WRITERS = frozenset({OWNER})


def may_write(role: Optional[str]) -> bool:
    """Decide whether ``role`` may run a command that changes something.

    The one line the read-only account comes down to. It takes an ``Optional``
    and answers ``False`` for ``None`` on purpose: "no session at all" and "a
    session that may not write" both fail closed here, so a caller that forgets
    to check for a missing session still cannot write. (:mod:`web.server` checks
    anyway, and answers the two apart -- a 401 and a 403 are different sentences.)

    Kept a free function, with no ``self`` and no socket, so the rule is asserted
    head-on in the tests -- the same shape as :func:`web.server.public_path` and
    :func:`web.server._origin_allowed`.
    """
    return role in _WRITERS


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


def issue_token(secret: bytes, role: str, expires_at: datetime) -> str:
    """Mint a session token that signs ``role`` in until ``expires_at``.

    The token is ``{expiry-epoch}.{role}.{signature}``, where the signature covers
    ``{expiry-epoch}.{role}`` -- both of them, together. That is what makes the
    role safe to keep in a cookie the browser holds and could edit: rewriting
    ``viewer`` to ``owner`` invalidates the signature, and there is no signature
    the holder can compute to repair it.

    It carries no secret of its own -- only a moment, a name for what may be done,
    and a proof that the server is the one who said so. There is nothing in it
    worth stealing that stealing the whole cookie would not already give you.

    Raises:
        ValueError: If ``role`` is not one of :data:`ROLES`. Minting a token for
            an account that does not exist is a bug here, not a request to refuse.
    """
    if role not in ROLES:
        raise ValueError(f"refusing to sign a token for an unknown role: {role!r}")
    payload = f"{int(expires_at.timestamp())}.{role}"
    return f"{payload}.{_sign(secret, payload)}"


def verified_role(secret: bytes, token: str, at: datetime) -> Optional[str]:
    """Return the role ``token`` proves, or ``None`` if it proves nothing.

    Every way of failing is the same ``None``: the signature does not match (the
    token was forged or tampered with), the expiry has passed, or what it names is
    not an account this server has. The signature is checked *first* and in
    constant time, so nothing downstream ever reads a byte the server did not
    itself write, and an attacker learns nothing by watching how the check fails.

    A token in the old, single-account format (``{expiry}.{signature}``, which
    this server did once sign) fails here, and fails closed. Its payload carries
    no role, so there is no role to find in it, and the answer is ``None`` rather
    than a guess at one. The cost of that is a re-login for anyone holding a
    cookie from before the second account existed, which is the correct price:
    the alternative is inferring authority from a token that never stated any.
    """
    if not token:
        return None
    payload, separator, signature = token.rpartition(".")
    if not separator:
        return None
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        return None

    raw_expiry, separator, role = payload.partition(".")
    if not separator or role not in ROLES:
        return None
    try:
        expiry = int(raw_expiry)
    except ValueError:
        return None
    return role if at.timestamp() < expiry else None


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

    The key is the *client*, never the client and the account it named -- see
    :func:`throttle_key`, which yields an address and nothing else. Counting per
    account would hand an attacker one budget of guesses per account name they
    can type, which is to say a fresh one whenever the old one runs out; there
    are two accounts today and the exchange rate should not be two-for-one. One
    client, one budget, however many doors they try it on.

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
    """Everything the server needs to run a login: the hashes, the secret, the rules.

    One of these is built at startup when a password is configured, and handed to
    every request handler. When none is built, the server runs open exactly as it
    did before -- so the presence of this object *is* the on switch.
    """

    #: One encoded hash per configured account, keyed by role. :data:`OWNER` is
    #: mandatory; :data:`VIEWER` is there only if a second password was set up,
    #: and its absence is what makes the login form a single box again.
    password_hashes: Mapping[str, str]
    secret: bytes
    ttl: timedelta = timedelta(days=30)
    #: Mark the cookie ``Secure`` so the browser only ever sends it over HTTPS.
    #: On for the real deployment (Caddy terminates TLS in front); turn it off
    #: only to test over plain ``http`` on localhost, where a Secure cookie would
    #: never come back and the login would appear to silently fail.
    cookie_secure: bool = True
    throttle: LoginThrottle = field(default_factory=LoginThrottle)
    clock: Clock = now

    def __post_init__(self) -> None:
        """Refuse to exist in a shape the server could not safely serve.

        Both of these are the operator's mistake caught at startup rather than
        at the first request: an authenticator with no owner is one nobody can
        drive the tracker from, and one keyed by a name that is not a role would
        hold a password that no login could ever reach and no token could name.
        """
        unknown = sorted(set(self.password_hashes) - set(ROLES))
        if unknown:
            raise ValueError(f"not an account: {', '.join(unknown)}")
        if OWNER not in self.password_hashes:
            raise ValueError(f"a login needs an {OWNER} password")

    @property
    def accounts(self) -> Tuple[str, ...]:
        """The configured accounts, in :data:`ROLES` order -- what the form offers.

        One entry means one password box and no picker; two means the choice. It
        is derived from the hashes rather than tracked alongside them, so the form
        cannot come to offer a door there is no password behind.
        """
        return tuple(role for role in ROLES if role in self.password_hashes)

    def verify_login(self, account: object, password: object) -> Optional[str]:
        """Return the role ``password`` signs in as, or ``None`` if it does not.

        The account is named by the caller, so exactly one hash is consulted and
        exactly one PBKDF2 runs -- the expensive thing happens once per attempt,
        never once per account. An account that is not configured is refused
        before any hashing at all, which is a timing difference and deliberately
        not a secret: the names are printed on the login form. What the timing
        cannot tell you is the only thing worth knowing, which is the password.

        Both arguments are ``object`` because both arrived as JSON and could be
        anything JSON allows. A non-string is not a wrong credential to be
        checked; it is not a credential, and it is refused as one.
        """
        if not isinstance(account, str) or not isinstance(password, str):
            return None
        encoded = self.password_hashes.get(account)
        if encoded is None:
            return None
        return account if verify_password(password, encoded) else None

    def role_for(self, cookie_header: Optional[str]) -> Optional[str]:
        """The role the request's ``Cookie`` header proves, or ``None`` for none.

        The server's whole question, answered once per request: ``None`` is a 401,
        anything else is a session and names what it may do.
        """
        token = _read_cookie(cookie_header, COOKIE_NAME)
        if token is None:
            return None
        return verified_role(self.secret, token, self.clock())

    def session_cookie(self, role: str) -> str:
        """A ``Set-Cookie`` value that logs the browser in as ``role`` for :attr:`ttl`."""
        token = issue_token(self.secret, role, self.clock() + self.ttl)
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


#: How each account's hash is configured, for the advice the CLI prints once it
#: has one. Keyed by role: the server flag, the environment variable that
#: overrides it, and whether that flag is enough on its own. Kept beside the roles
#: it describes rather than imported from :mod:`web.server`, which imports *this*
#: module and not the other way about.
#:
#: The viewer's flag is *not* enough on its own -- ``--viewer-password-file``
#: without ``--password-file`` is refused at startup, because a tracker that can
#: be watched and not driven is nobody's intention. So the advice for it says
#: "alongside" rather than naming a command that would fail.
_CONFIGURED_BY: Dict[str, Tuple[str, str, bool]] = {
    OWNER: ("--password-file", "WORK_TRACKER_PASSWORD_HASH", True),
    VIEWER: ("--viewer-password-file", "WORK_TRACKER_VIEWER_PASSWORD_HASH", False),
}


def main(argv: Optional[List[str]] = None) -> int:
    """Prompt for a password and print (or write) its hash.

    Run it once per account to create the credentials the server checks against::

        python3 -m web.auth                                        # prints the hash line
        python3 -m web.auth --write .password                      # the owner: you
        python3 -m web.auth --account viewer --write .password-viewer

    The two accounts are two passwords, and this makes them one at a time -- there
    is no step here that derives one from the other, because nothing about the
    read-only account should be recoverable from the owner's password or the other
    way round. ``--account`` picks nothing about the hashing, which is identical
    for both; it picks which file and flag the closing advice names, so that the
    hash you just made ends up where the server will actually look for it.

    The password is read with :func:`getpass.getpass`, so it never appears on the
    command line or in the shell history, and only its hash ever leaves here.
    """
    import argparse
    import getpass

    parser = argparse.ArgumentParser(description="Generate a work-tracker login password hash.")
    parser.add_argument(
        "--account",
        choices=ROLES,
        default=OWNER,
        help=(
            f"which account this password is for: '{OWNER}' drives the tracker, "
            f"'{VIEWER}' may only look (default: {OWNER})"
        ),
    )
    parser.add_argument(
        "--write",
        type=Path,
        metavar="PATH",
        help="write the hash to PATH (created 0600) instead of printing it",
    )
    args = parser.parse_args(argv)
    flag, variable, sufficient = _CONFIGURED_BY[args.account]
    owner_flag = _CONFIGURED_BY[OWNER][0]

    password = getpass.getpass(f"Choose the {args.account} password: ")
    if not password:
        print("error: an empty password is not allowed", file=sys.stderr)
        return 1
    if getpass.getpass("Repeat the password: ") != password:
        print("error: the passwords do not match", file=sys.stderr)
        return 1

    encoded = hash_password(password)
    if args.write:
        write_private(args.write, encoded + "\n")
        print(f"wrote the {args.account} password hash to {args.write} (mode 0600)")
        if sufficient:
            print(f"start the server with {flag} {args.write} to require this password.")
        else:
            print(
                f"add {flag} {args.write} to the server's command line, alongside the "
                f"{owner_flag} the owner's password is in, to require this password."
            )
    else:
        print(encoded)
        advice = f"set {variable} to this value, or save it to a file and pass {flag}"
        print(
            f"{advice}, to require it." if sufficient else f"{advice} as well as {owner_flag}, to require it.",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
