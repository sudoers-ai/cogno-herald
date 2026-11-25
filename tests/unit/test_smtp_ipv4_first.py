"""``_open`` caps the TCP CONNECT short and tries IPv4 first; the greeting keeps the session cap.

``smtplib`` connects family by family in ``getaddrinfo`` order (IPv6 first), in series, with no
happy eyeballs, so a broken IPv6 route costs the whole connect cap before IPv4. ``_open`` tries
IPv4 first (source bound to the ``0.0.0.0`` wildcard) under a short connect cap, and on a TCP
connect failure — nothing sent, no greeting read — falls back to the default path under a longer
connect cap. Only the CONNECT is capped short and retried: the greeting (RFC 5321 §4.5.3.2.1 lets a
server delay its 220) and the whole session keep the session timeout, and a failure once the
connection exists — a slow/absent greeting, a non-220 reply, STARTTLS, login, the send — is never
retried, so the message can never go out twice.

The short cap lives on ``socket.create_connection`` (patched here): a fake socket speaks only the
greeting, which is all ``_open`` reads.
"""

import logging
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


class _FakeFile:
    def __init__(self, lines):
        self._lines = list(lines)

    def readline(self, *a):
        return self._lines.pop(0) if self._lines else b""

    def close(self):
        ...


class _FakeSocket:
    """Speaks one SMTP line — the greeting — which is all ``_open`` reads. It records every
    ``settimeout`` so a test can see the connect cap never reaches the greeting/session."""

    def __init__(self, greeting=b"220 fake ESMTP\r\n"):
        self._greeting = greeting
        self.timeouts = []

    def settimeout(self, timeout):
        self.timeouts.append(timeout)

    def gettimeout(self):
        return self.timeouts[-1] if self.timeouts else None

    def makefile(self, *a, **k):
        return _FakeFile([self._greeting])

    def sendall(self, data):
        ...

    def close(self):
        ...


class _Dialer:
    """A fake ``socket.create_connection``: records each call's args, and returns the next prepared
    outcome — a ``_FakeSocket`` to hand back, or an exception to raise (a connect failure)."""

    def __init__(self, outcomes):
        self.calls = []
        self._outcomes = list(outcomes)

    def __call__(self, address, timeout=None, source_address=None):
        self.calls.append({"address": address, "timeout": timeout, "source_address": source_address})
        out = self._outcomes.pop(0)
        if isinstance(out, BaseException):
            raise out
        return out


@pytest.fixture
def dial(monkeypatch):
    def _install(outcomes):
        d = _Dialer(outcomes)
        monkeypatch.setattr(smtp.socket, "create_connection", d)
        return d
    return _install


def test_ipv4_connects_once_with_source_address_and_the_session_timeout(dial):
    """IPv4 connects: a single ``create_connection`` bound to ``0.0.0.0`` under the short connect
    cap, and the socket is then widened to the full session timeout for the greeting and session."""
    sock = _FakeSocket()
    d = dial([sock])
    server = _open("relay.test", 587)
    assert len(d.calls) == 1
    c = d.calls[0]
    assert c["address"] == ("relay.test", 587)
    assert c["timeout"] == _CONNECT_IPV4_S
    assert c["source_address"] == ("0.0.0.0", 0)
    assert sock.gettimeout() == _SESSION_TIMEOUT_S
    server.close()


@pytest.mark.parametrize("exc", [
    ConnectionRefusedError("refused"),
    TimeoutError("timed out"),
    OSError("network unreachable"),
])
def test_a_connect_failure_falls_back_without_source_address(dial, exc):
    """A TCP connect failure (nothing sent) → a second ``create_connection`` on the default path,
    with NO source address and the longer connect cap; the server comes back."""
    sock2 = _FakeSocket()
    d = dial([exc, sock2])
    server = _open("relay.test", 587)
    assert len(d.calls) == 2
    assert d.calls[0]["timeout"] == _CONNECT_IPV4_S and d.calls[0]["source_address"] == ("0.0.0.0", 0)
    assert d.calls[1]["timeout"] == _CONNECT_FALLBACK_S and d.calls[1]["source_address"] is None
    server.close()


def test_a_slow_greeting_is_not_capped_by_the_connect_timeout(dial):
    """The greeting is read under the SESSION timeout, never the short connect cap, so a relay that
    delays its 220 (RFC 5321 §4.5.3.2.1 allows up to five minutes) connects with no fallback: the
    short cap went to ``create_connection``, and the socket was widened to the session timeout."""
    sock = _FakeSocket(greeting=b"220 slow but valid\r\n")
    d = dial([sock])
    server = _open("relay.test", 587)
    assert len(d.calls) == 1                       # a greeting delay never triggers a fallback
    assert d.calls[0]["timeout"] == _CONNECT_IPV4_S  # the short cap is on the CONNECT only
    assert sock.gettimeout() == _SESSION_TIMEOUT_S   # the greeting/session runs at the session cap
    server.close()


def test_a_refused_greeting_raises_and_is_not_retried(dial):
    """A non-220 greeting means the connection existed, so it is NOT a connect to retry: ``_open``
    raises ``SMTPConnectError`` and opens no second connection."""
    d = dial([_FakeSocket(greeting=b"554 no SMTP service here\r\n")])
    with pytest.raises(smtplib.SMTPConnectError):
        _open("relay.test", 587)
    assert len(d.calls) == 1


def test_when_both_connects_fail_the_error_is_clean(dial):
    """Both families fail to connect: the fallback's failure surfaces as ``_ConnectFailed`` (an
    ``OSError``), unwrapped, after exactly two attempts."""
    d = dial([ConnectionRefusedError("ipv6 down"), ConnectionRefusedError("ipv4 down too")])
    with pytest.raises(OSError) as ei:
        _open("relay.test", 587)
    assert isinstance(ei.value, smtp._ConnectFailed)
    assert len(d.calls) == 2


def test_the_fallback_log_names_the_connect_error_class_and_no_host(dial, caplog):
    """The fallback line names the ORIGINAL connect exception's class — and never the host."""
    dial([ConnectionRefusedError("refused"), _FakeSocket()])
    with caplog.at_level(logging.INFO, logger="cogno_herald.smtp"):
        server = _open("secret-relay.test", 587)
    lines = [r.getMessage() for r in caplog.records
             if "event=smtp_ipv4_connect_failed" in r.getMessage()]
    assert len(lines) == 1
    assert "error=ConnectionRefusedError" in lines[0]
    assert "secret-relay.test" not in lines[0]
    server.close()


def test_dial_sets_the_connected_host_for_starttls(dial):
    """``_dial`` builds ``_SMTP`` with no host (so it does not connect in the constructor), then
    sets ``_host`` to the real host — STARTTLS verifies the certificate against it, and an empty
    ``_host`` would make a verifying context raise ``ValueError``."""
    dial([_FakeSocket()])
    server = _open("relay.test", 587)
    assert server._host == "relay.test"
    assert server._host != ""
    server.close()


def test_starttls_verifies_against_the_connected_host(dial, monkeypatch):
    """STARTTLS wraps the socket with ``server_hostname=self._host``. With the ``_host`` fix that
    is the connected host, never ``''`` — a default (verifying) context rejects an empty name."""
    recorded = {}

    class _Ctx:
        def wrap_socket(self, sock, server_hostname=None):
            recorded["server_hostname"] = server_hostname
            return sock

    dial([_FakeSocket()])
    server = _open("relay.test", 587)
    # drive only the TLS wrap: stub the EHLO/command dialog so no SMTP bytes are needed, and
    # advertise STARTTLS so ``starttls()`` reaches ``wrap_socket`` instead of refusing early.
    monkeypatch.setattr(server, "ehlo_or_helo_if_needed", lambda: None)
    monkeypatch.setattr(server, "docmd", lambda *a, **k: (220, b"go ahead"))
    server.esmtp_features["starttls"] = ""   # what EHLO would record; has_extn("starttls") → True
    server.starttls(context=_Ctx())
    assert recorded["server_hostname"] == "relay.test"
    assert recorded["server_hostname"] != ""


def test_a_failure_after_the_connection_is_never_retried(monkeypatch):
    """A login error happens AFTER a good connect, so ``_open`` dials exactly once — retrying it
    could send the message twice. (``_dial`` is stubbed: the retry decision is ``_open``'s.)"""
    dials = []

    class _Srv:
        def __init__(self):
            self.sock = _FakeSocket()

        def ehlo(self): ...
        def starttls(self, context=None): ...
        def login(self, user, password):
            raise smtplib.SMTPAuthenticationError(535, b"bad credentials")
        def quit(self): ...
        def close(self): ...

    def fake_dial(host, port, *, connect_timeout, source_address=None):
        dials.append(connect_timeout)
        return _Srv()

    monkeypatch.setattr(smtp, "_dial", fake_dial)
    cfg = {"host": "relay.test", "port": 587, "user": "u", "password": "p", "use_tls": True}
    with pytest.raises(smtplib.SMTPAuthenticationError):
        _connect(cfg)
    assert len(dials) == 1


async def test_send_still_goes_out_when_the_ipv4_connect_fails(monkeypatch):
    """End to end over the ``_SMTP`` seam: the IPv4 connect fails, the fallback carries the message,
    and the send succeeds — two connections, one send."""
    made = []

    class _FallbackSMTP:
        def __init__(self, timeout=None, source_address=None):
            made.append(source_address)
            self.sock = _FakeSocket()
            self.source_address = source_address
            self.connect_timeout = None

        def connect(self, host, port):
            if self.source_address is not None:
                raise smtp._ConnectFailed("ConnectionRefusedError")
            return (220, b"ok")

        def ehlo(self): ...
        def starttls(self, context=None): ...
        def login(self, user, password): ...
        def sendmail(self, from_email, recipients, payload): ...
        def quit(self): ...
        def close(self): ...

    monkeypatch.setattr(smtp, "_SMTP", _FallbackSMTP)
    cfg = {"host": "relay.test", "port": 587, "from_email": "f@x.test", "use_tls": False}
    result = await send_plain_email(cfg, ["a@x.com"], "S", "body")
    assert result == {"sent": True, "recipients": ["a@x.com"]}
    assert made == [("0.0.0.0", 0), None]   # IPv4 (bound) failed, the default path sent
