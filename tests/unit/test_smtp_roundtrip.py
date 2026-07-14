"""Real SMTP round-trip — herald actually transmits over a socket to a local ``aiosmtpd`` server.

The rest of the SMTP suite monkeypatches ``smtplib.SMTP`` with a fake (fast, no I/O). This one
proves the real thing: ``send_email_with_ics`` opens a TCP connection, speaks SMTP, and the server
receives a MIME message with the recipients + the ``.ics`` calendar part decoded intact. It is the
guarantee that an invite/OTP email actually leaves the process — everything downstream is transport.
"""

import email
import socket
from email.header import decode_header

import pytest
from aiosmtpd.controller import Controller

from cogno_herald import send_email_with_ics, send_plain_email


class _CapturingHandler:
    """Records every message the SMTP server accepts (envelope + raw bytes)."""

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
def smtp_server():
    handler = _CapturingHandler()
    controller = Controller(handler, hostname="127.0.0.1", port=_free_port(), ready_timeout=10.0)
    controller.start()
    try:
        yield controller, handler
    finally:
        controller.stop()


def _config(controller) -> dict:
    # A local server: no STARTTLS, no auth (user/password empty → herald skips login).
    return {"host": controller.hostname, "port": controller.port,
            "from_email": "clinic@x.com", "from_name": "Clinic", "use_tls": False}


def _decoded_subject(raw: bytes) -> str:
    msg = email.message_from_bytes(raw)
    text, enc = decode_header(msg["Subject"])[0]
    return text.decode(enc or "utf-8") if isinstance(text, bytes) else text


def _calendar_payload(raw: bytes) -> str:
    """The decoded text/calendar part of a received multipart message."""
    for part in email.message_from_bytes(raw).walk():
        if part.get_content_type() == "text/calendar":
            return part.get_payload(decode=True).decode("utf-8")
    return ""


async def test_invite_with_ics_actually_transmits(smtp_server):
    controller, handler = smtp_server
    ics = "BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:appt-1\r\nEND:VEVENT\r\nEND:VCALENDAR\r\n"
    result = await send_email_with_ics(
        _config(controller), ["ana@x.com", "dr-silva@clinic.com"],
        "Confirmação de agendamento", "Sua consulta foi confirmada.", ics)

    assert result == {"sent": True, "recipients": ["ana@x.com", "dr-silva@clinic.com"]}
    # the server received exactly one message over the wire, to both parties
    assert len(handler.messages) == 1
    mail_from, rcpts, raw = handler.messages[0]
    assert mail_from == "clinic@x.com"
    assert set(rcpts) == {"ana@x.com", "dr-silva@clinic.com"}
    assert _decoded_subject(raw) == "Confirmação de agendamento"
    # the .ics survived transport intact (base64 → decoded), so the calendar event goes out
    cal = _calendar_payload(raw)
    assert "UID:appt-1" in cal and "METHOD:REQUEST" in cal


async def test_plain_email_actually_transmits(smtp_server):
    controller, handler = smtp_server
    result = await send_plain_email(_config(controller), ["ana@x.com"], "Oi", "corpo")
    assert result["sent"] is True
    assert len(handler.messages) == 1
    _, rcpts, raw = handler.messages[0]
    assert rcpts == ["ana@x.com"] and _decoded_subject(raw) == "Oi"
