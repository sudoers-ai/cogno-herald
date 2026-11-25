"""A real STARTTLS round trip — herald negotiates TLS over a socket to a local ``aiosmtpd`` that
REQUIRES STARTTLS, and verifies the server certificate against the host it connected to.

This is the test that would have caught ``_host=''``. ``_dial`` builds ``_SMTP`` with no host (so
the constructor does not connect), then sets ``_host`` to the real host. STARTTLS wraps the socket
with ``server_hostname=self._host``; had ``_host`` stayed ``''``, a verifying context would raise
``ValueError`` and every TLS send (port 587, the default) would fail. Here the client TRUSTS the
run's CA (``tls_ca_file``) AND verifies the hostname, so the send succeeds only when ``_dial`` set
``_host`` to the connected host.

The test TLS material is generated per run with ``trustme``; no key is committed. The server uses
stdlib ``ssl`` and the already-present ``aiosmtpd``; ``trustme`` is a dev-only dependency.
"""

import email
import socket
import ssl

import pytest
import trustme
from aiosmtpd.controller import Controller

from cogno_herald import send_email_with_ics, send_plain_email


class _CapturingHandler:
    """Records every message the SMTP server accepts — but only after STARTTLS (it is required)."""

    def __init__(self) -> None:
        self.messages: list[tuple[str, list[str], bytes]] = []

    async def handle_DATA(self, server, session, envelope):  # aiosmtpd hook
        self.messages.append((envelope.mail_from, list(envelope.rcpt_tos), envelope.content))
        return "250 Message accepted"


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def tls_material(tmp_path):
    """A fresh CA and a leaf cert for ``127.0.0.1``/``localhost``, both made this run. The CA cert
    is written to a temp file the client trusts via ``tls_ca_file``."""
    ca = trustme.CA()
    server_cert = ca.issue_cert("127.0.0.1", "localhost")
    ca_file = tmp_path / "ca.pem"
    ca.cert_pem.write_to_path(str(ca_file))
    return server_cert, str(ca_file)


@pytest.fixture
def starttls_server(tls_material):
    server_cert, _ = tls_material
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_cert.configure_cert(ctx)
    handler = _CapturingHandler()
    # require_starttls + tls_context are forwarded to aiosmtpd's SMTP: the server advertises
    # STARTTLS and refuses MAIL until the client has upgraded the connection to TLS.
    controller = Controller(
        handler, hostname="127.0.0.1", port=_free_port(), ready_timeout=10.0,
        require_starttls=True, tls_context=ctx)
    controller.start()
    try:
        yield controller, handler
    finally:
        controller.stop()


def _client_config(controller, ca_file) -> dict:
    # use_tls=True → STARTTLS; tls_ca_file trusts the run's CA, and the default context still
    # VERIFIES the hostname — so the send works only if server_hostname is the connected host.
    return {"host": controller.hostname, "port": controller.port,
            "from_email": "clinic@x.test", "from_name": "Clinic",
            "use_tls": True, "tls_ca_file": ca_file}


async def test_starttls_invite_roundtrip_verifies_the_host_and_sends(starttls_server, tls_material):
    controller, handler = starttls_server
    _, ca_file = tls_material
    ics = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:appt-1\r\n"
           "END:VEVENT\r\nEND:VCALENDAR\r\n")
    result = await send_email_with_ics(
        _client_config(controller, ca_file), ["ana@x.com"], "Confirmação",
        "Sua consulta foi confirmada.", ics)

    assert result == {"sent": True, "recipients": ["ana@x.com"]}
    assert len(handler.messages) == 1               # it arrived — therefore STARTTLS succeeded
    mail_from, rcpts, raw = handler.messages[0]
    assert mail_from == "clinic@x.test" and rcpts == ["ana@x.com"]
    for part in email.message_from_bytes(raw).walk():
        if part.get_content_type() == "text/calendar":
            assert "UID:appt-1" in part.get_payload(decode=True).decode("utf-8")
            break
    else:
        pytest.fail("the .ics calendar part did not survive the TLS round trip")


async def test_starttls_plain_roundtrip(starttls_server, tls_material):
    controller, handler = starttls_server
    _, ca_file = tls_material
    result = await send_plain_email(_client_config(controller, ca_file), ["ana@x.com"], "Oi", "corpo")
    assert result["sent"] is True
    assert len(handler.messages) == 1
    _, rcpts, _ = handler.messages[0]
    assert rcpts == ["ana@x.com"]
