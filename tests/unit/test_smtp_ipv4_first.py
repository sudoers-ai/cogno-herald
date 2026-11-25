"""``_open`` tries IPv4 first, then the default path — and never retries past the connection.

``smtplib`` connects family by family in ``getaddrinfo`` order (IPv6 first), in series, with no
happy eyeballs, so a broken IPv6 route costs the whole timeout before IPv4. ``_open`` binds the
source to the IPv4 wildcard under a short cap to try IPv4 first, and on a connect-or-greeting
failure — nothing sent yet — falls back to the default path under a longer cap. A failure AFTER the
connection (STARTTLS, login, the send) is never retried: the message could then go out twice.

``smtplib.SMTP`` is a recording fake here (no network): it captures each construction's kwargs and
can be scripted to fail the Nth one, so a test can watch which family order ``_open`` takes.
"""

import logging
import socket
import smtplib

import pytest

from cogno_herald import smtp
from cogno_herald.smtp import (
    _CONNECT_FALLBACK_S,
    _CONNECT_IPV4_S,
    _SESSION_TIMEOUT_S,
    _connect,
    _open,
    send_plain_email,
)


class _Sock:
    def __init__(self) -> None:
        self.timeout = None

    def settimeout(self, timeout):
        self.timeout = timeout


class RecordingSMTP:
    """Records each construction's kwargs; ``fail[i]`` (if set) is raised by the i-th construction."""

    constructions: list = []
    fail: list = []

    def __init__(self, host, port, timeout=30, source_address=None):
        idx = len(RecordingSMTP.constructions)
        RecordingSMTP.constructions.append(
            {"host": host, "port": port, "timeout": timeout, "source_address": source_address})
        exc = RecordingSMTP.fail[idx] if idx < len(RecordingSMTP.fail) else None
        if exc is not None:
            raise exc
        self.sock = _Sock()

    def ehlo(self): ...
    def starttls(self, context=None): ...
    def login(self, user, password): ...
    def sendmail(self, from_email, recipients, payload): ...
    def quit(self): ...


class _LoginFailsSMTP(RecordingSMTP):
    """Connects fine, then refuses the login — the failure is AFTER the connection."""

    def login(self, user, password):
        raise smtplib.SMTPAuthenticationError(535, b"bad credentials")


@pytest.fixture(autouse=True)
def _reset():
    RecordingSMTP.constructions = []
    RecordingSMTP.fail = []
    yield


@pytest.fixture
def patched(monkeypatch):
    monkeypatch.setattr(smtp.smtplib, "SMTP", RecordingSMTP)
    return RecordingSMTP


def test_ipv4_ok_is_one_constructor_with_source_address(patched):
    """IPv4 connects: a single constructor, bound to ``0.0.0.0`` under the short cap, and the
    socket is then widened to the full session timeout."""
    server = _open("relay.test", 587)
    (c,) = patched.constructions
    assert c["source_address"] == ("0.0.0.0", 0)
    assert c["timeout"] == _CONNECT_IPV4_S
    assert server.sock.timeout == _SESSION_TIMEOUT_S


@pytest.mark.parametrize("exc", [
    OSError("network unreachable"),
    socket.timeout("timed out"),
    smtplib.SMTPServerDisconnected("no greeting"),
])
def test_ipv4_failure_falls_back_without_source_address(patched, exc):
    """A connect or greeting failure (every one of these is an ``OSError``) → a second constructor
    on the default path, with NO ``source_address`` and the longer cap; the server comes back."""
    patched.fail = [exc]
    server = _open("relay.test", 587)
    first, second = patched.constructions
    assert first["source_address"] == ("0.0.0.0", 0) and first["timeout"] == _CONNECT_IPV4_S
    assert second["source_address"] is None and second["timeout"] == _CONNECT_FALLBACK_S
    assert server.sock.timeout == _SESSION_TIMEOUT_S


def test_both_families_fail_raises_the_second_exception_clean(patched):
    """When the fallback also fails, its exception propagates clean (unwrapped), not the first."""
    first, second = OSError("ipv6 down"), OSError("ipv4 down too")
    patched.fail = [first, second]
    with pytest.raises(OSError) as ei:
        _open("relay.test", 587)
    assert ei.value is second
    assert len(patched.constructions) == 2


def test_a_failure_after_the_connection_is_never_retried(monkeypatch):
    """A login error happens AFTER a good connect, so there is no second constructor — retrying it
    could send the message twice."""
    monkeypatch.setattr(smtp.smtplib, "SMTP", _LoginFailsSMTP)
    cfg = {"host": "relay.test", "port": 587, "user": "u", "password": "p", "use_tls": True}
    with pytest.raises(smtplib.SMTPAuthenticationError):
        _connect(cfg)
    assert len(RecordingSMTP.constructions) == 1


def test_the_fallback_log_carries_the_class_and_no_host(patched, caplog):
    """The fallback line names the exception's CLASS only — never the host, never a user."""
    patched.fail = [OSError("boom")]
    with caplog.at_level(logging.INFO, logger="cogno_herald.smtp"):
        _open("secret-relay.test", 587)
    lines = [r.getMessage() for r in caplog.records
             if "event=smtp_ipv4_connect_failed" in r.getMessage()]
    assert len(lines) == 1
    assert "error=OSError" in lines[0]
    assert "secret-relay.test" not in lines[0]


async def test_send_still_goes_out_when_the_ipv4_connect_fails(patched):
    """End to end: the IPv4 connect fails, the fallback carries the message, the send succeeds."""
    patched.fail = [OSError("ipv6 route down")]
    cfg = {"host": "relay.test", "port": 587, "from_email": "f@x.test", "use_tls": False}
    result = await send_plain_email(cfg, ["a@x.com"], "S", "body")
    assert result == {"sent": True, "recipients": ["a@x.com"]}
    assert len(patched.constructions) == 2
