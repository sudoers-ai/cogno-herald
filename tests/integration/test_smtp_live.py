"""
Integration test: real SMTP send against a local ``aiosmtpd`` server.

The unit suite patches ``smtplib.SMTP`` with a fake, so it never exercises the
actual client path — ``_connect`` (EHLO/STARTTLS/login), ``server.sendmail``, or
the ``msg.as_bytes()`` → wire → parse round-trip. This spins a throwaway SMTP
server on localhost, sends through the public API, and parses the *received* bytes
to assert headers and the iCalendar attachment really survive transport.

Auto-skips if ``aiosmtpd`` is not installed (it is a dev dependency).
"""

import socket
from email import message_from_bytes
from email.message import Message

import pytest

pytest.importorskip("aiosmtpd")

from aiosmtpd.controller import Controller  # noqa: E402

from cogno_herald import send_email_with_ics, send_plain_email  # noqa: E402


class _Collector:
    """aiosmtpd handler that captures raw message bytes + envelope."""

    def __init__(self) -> None:
        self.messages: list[bytes] = []
        self.mail_from: str = ""
        self.rcpt_tos: list[str] = []

    async def handle_DATA(self, server, session, envelope):  # noqa: ANN001 - aiosmtpd API
        self.messages.append(envelope.content)
        self.mail_from = envelope.mail_from
        self.rcpt_tos = list(envelope.rcpt_tos)
        return "250 Message accepted"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def smtp_server():
    handler = _Collector()
    controller = Controller(handler, hostname="127.0.0.1", port=_free_port())
    controller.start()
    try:
        yield handler, controller
    finally:
        controller.stop()


def _config(controller: Controller) -> dict:
    # No TLS / no auth: a plain local server exercises the real client path
    # without certificates. _connect skips STARTTLS when use_tls is False.
    return {
        "host": controller.hostname,
        "port": controller.port,
        "user": "",
        "password": "",
        "from_email": "bot@cogno.test",
        "from_name": "Cogno Herald",
        "use_tls": False,
    }


async def test_plain_email_round_trips(smtp_server):
    handler, controller = smtp_server
    result = await send_plain_email(
        _config(controller), ["alice@example.test"], "Hello", "Plain body line."
    )
    assert result["sent"] is True

    assert handler.rcpt_tos == ["alice@example.test"]
    assert handler.mail_from == "bot@cogno.test"
    msg: Message = message_from_bytes(handler.messages[0])
    assert msg["Subject"] == "Hello"
    assert "bot@cogno.test" in msg["From"]
    assert msg["To"] == "alice@example.test"
    assert msg.get_content_type() == "text/plain"
    assert "Plain body line." in msg.get_payload(decode=True).decode("utf-8")


async def test_ics_attachment_survives_transport(smtp_server):
    handler, controller = smtp_server
    ics = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nEND:VCALENDAR\r\n"
    result = await send_email_with_ics(
        _config(controller), ["bob@example.test"], "Invite", "See attached.",
        ics, ics_filename="meeting.ics",
    )
    assert result["sent"] is True

    msg = message_from_bytes(handler.messages[0])
    assert msg.get_content_type() == "multipart/mixed"

    parts = list(msg.walk())
    cal = next(p for p in parts if p.get_content_type() == "text/calendar")
    # method parameter preserved on the Content-Type
    assert cal.get_param("method") == "REQUEST"
    assert cal.get_filename() == "meeting.ics"
    # base64 payload decodes back to the exact ICS we sent
    decoded = cal.get_payload(decode=True).decode("utf-8")
    assert decoded == ics

    text = next(p for p in parts if p.get_content_type() == "text/plain")
    assert "See attached." in text.get_payload(decode=True).decode("utf-8")


async def test_cancel_method_preserved(smtp_server):
    handler, controller = smtp_server
    ics = "BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\nEND:VCALENDAR\r\n"
    result = await send_email_with_ics(
        _config(controller), ["c@example.test"], "Cancelled", "Bye.", ics
    )
    assert result["sent"] is True
    msg = message_from_bytes(handler.messages[0])
    cal = next(p for p in msg.walk() if p.get_content_type() == "text/calendar")
    assert cal.get_param("method") == "CANCEL"


async def test_multiple_recipients_all_delivered(smtp_server):
    handler, controller = smtp_server
    result = await send_plain_email(
        _config(controller), ["x@example.test", "y@example.test"], "Hi", "body"
    )
    assert result["sent"] is True
    assert handler.rcpt_tos == ["x@example.test", "y@example.test"]
    msg = message_from_bytes(handler.messages[0])
    assert msg["To"] == "x@example.test, y@example.test"
