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


async def test_html_alternative_survives_transport(smtp_server):
    """``send_html_email`` over a real socket: the server receives ONE ``multipart/alternative``
    whose two parts — text first, HTML second — decode back to exactly what was handed over."""
    from cogno_herald import smtp as _smtp

    handler, controller = smtp_server
    text = "Olá.\n01/11 08:00 — Álgebra — 9A\n"
    html = "<!DOCTYPE html><html><body><table><tr><td>Álgebra</td></tr></table></body></html>"
    result = await getattr(_smtp, "send_html_email")(
        _config(controller), ["dana@example.test"], "Aulas", text, html)
    assert result["sent"] is True
    assert handler.rcpt_tos == ["dana@example.test"]

    msg = message_from_bytes(handler.messages[0])
    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert parts[0].get_payload(decode=True).decode("utf-8") == text
    assert parts[1].get_payload(decode=True).decode("utf-8") == html


# ── Line ends on the wire (RFC 5321 §2.3.8) ──────────────────────────────────────────────────────
# aiosmtpd reads DATA up to CRLF and refuses a line past 1001 octets, as a strict server does. Before
# the fix herald sent bare LFs, so the WHOLE message was one line and anything over ~1000 bytes was
# refused — a plain message of thirty short lines as surely as an HTML table on one long line. The
# small messages above never showed it, and neither did a lenient capture server.

_TEXT = "Olá, Professora.\n\n01/11 08:00 — Álgebra — 9A\n02/11 — Química — 9B\n"
_LONG_HTML = ("<!DOCTYPE html><html><body><table>"
              + "<tr><td>01/11</td><td>08:00</td><td>Álgebra — 9A</td></tr>" * 90
              + "</table></body></html>")
_SHORT_LINES = "".join(f"Linha {i:02d}: aula de Álgebra às 08:00 na sala 9A.\n" for i in range(30))
_ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:aula-1\r\n"
        + "".join(f"COMMENT:item {i:02d} da pauta da reunião\r\n" for i in range(30))
        + "END:VEVENT\r\nEND:VCALENDAR\r\n")


def _sends():
    from cogno_herald import smtp as _smtp

    send_html_email = getattr(_smtp, "send_html_email")
    return {
        "plain_short": (lambda cfg: send_plain_email(cfg, ["dana@example.test"], "Aulas", _TEXT),
                        {"text/plain": _TEXT}),
        "plain_thirty_short_lines": (
            lambda cfg: send_plain_email(cfg, ["dana@example.test"], "Aulas", _SHORT_LINES),
            {"text/plain": _SHORT_LINES}),
        "html_one_long_line": (
            lambda cfg: send_html_email(cfg, ["dana@example.test"], "Aulas", _TEXT, _LONG_HTML),
            {"text/plain": _TEXT, "text/html": _LONG_HTML}),
        "ics": (lambda cfg: send_email_with_ics(cfg, ["dana@example.test"], "Aulas", _TEXT, _ICS),
                {"text/plain": _TEXT, "text/calendar": _ICS}),
    }


@pytest.mark.parametrize("case", ["plain_short", "plain_thirty_short_lines", "html_one_long_line",
                                  "ics"])
async def test_a_strict_server_accepts_every_sender_and_every_part_decodes_intact(smtp_server, case):
    handler, controller = smtp_server
    send, expected = _sends()[case]
    result = await send(_config(controller))
    assert result["sent"] is True, result
    assert len(handler.messages) == 1
    msg = message_from_bytes(handler.messages[0])
    decoded = {p.get_content_type(): p.get_payload(decode=True).decode("utf-8")
               for p in msg.walk() if not p.is_multipart()}
    assert decoded == expected


@pytest.mark.parametrize("case", ["plain_short", "plain_thirty_short_lines", "html_one_long_line",
                                  "ics"])
async def test_the_transmitted_bytes_carry_no_bare_lf(smtp_server, case):
    handler, controller = smtp_server
    send, _ = _sends()[case]
    assert (await send(_config(controller)))["sent"] is True
    raw = handler.messages[0]
    assert raw.count(b"\n") == raw.count(b"\r\n") > 0


def test_control_the_server_refuses_a_bare_lf_message_over_1001_bytes(smtp_server):
    """The negative control, produced rather than assumed: the same server, the same size, with
    bare LFs sent straight through ``smtplib`` — refused. Without this, an accepting server would
    read as a fixed sender."""
    import smtplib

    handler, controller = smtp_server
    body = b"Subject: controle\n\n" + b"linha curta de controle\n" * 60
    assert len(body) > 1001
    with smtplib.SMTP(controller.hostname, controller.port, timeout=10) as client:
        with pytest.raises(smtplib.SMTPDataError) as refused:
            client.sendmail("bot@cogno.test", ["dana@example.test"], body)
    assert refused.value.smtp_code == 500 and b"Line too long" in refused.value.smtp_error
    assert handler.messages == []
