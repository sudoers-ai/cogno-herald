"""``send_html_email`` — ``multipart/alternative`` (text first, HTML second), fake SMTP, no network.

The function is looked up on the module inside each test (:func:`_send_html`) rather than
imported at the top, so that on a tree without it every test FAILS by name instead of the whole
file failing to collect.
"""

import email
import logging
import smtplib

import pytest

from cogno_herald import smtp
from cogno_herald.smtp import send_plain_email

CONFIG = {"host": "smtp.test", "port": 587, "user": "u", "password": "p",
          "from_email": "from@test", "from_name": "Cogno", "use_tls": True}

TO = "destinataria.exemplo@example.test"
TEXT = "Olá, Professora.\n\n01/11 08:00 — Álgebra — 9A\n02/11 — Química — 9B\n"
HTML = ("<!DOCTYPE html><html><body><table><tr><td>01/11</td><td>Álgebra</td></tr>"
        "</table></body></html>")


class FakeSMTP:
    sent: list = []

    def __init__(self, host, port, timeout=30):
        FakeSMTP.sent = []

    def ehlo(self): ...
    def starttls(self, context=None): ...
    def login(self, user, password): ...
    def sendmail(self, from_email, recipients, payload):
        FakeSMTP.sent.append((from_email, recipients, payload))
    def quit(self): ...


@pytest.fixture(autouse=True)
def _patch_smtp(monkeypatch):
    FakeSMTP.sent = []   # a send that never connects must not see the previous test's message
    monkeypatch.setattr(smtp.smtplib, "SMTP", FakeSMTP)


def _send_html():
    return getattr(smtp, "send_html_email")


def _sent_message() -> email.message.Message:
    assert len(FakeSMTP.sent) == 1, f"expected one message on the wire, got {len(FakeSMTP.sent)}"
    return email.message_from_bytes(FakeSMTP.sent[0][2])


async def test_the_message_is_multipart_alternative_text_first_html_second():
    """RFC 2046 §5.1.4: the LAST part of an alternative is the preferred one — an HTML client
    shows the HTML and every other client the text. The order is the contract."""
    result = await _send_html()(CONFIG, [TO], "Aulas de novembro", TEXT, HTML)
    assert result == {"sent": True, "recipients": [TO]}
    msg = _sent_message()
    assert msg.get_content_type() == "multipart/alternative"
    parts = msg.get_payload()
    assert [p.get_content_type() for p in parts] == ["text/plain", "text/html"]
    assert parts[0].get_payload(decode=True).decode("utf-8") == TEXT
    assert parts[1].get_payload(decode=True).decode("utf-8") == HTML
    assert all(p.get_content_charset() == "utf-8" for p in parts)


async def test_the_text_part_is_byte_for_byte_what_send_plain_email_sends():
    """The text part is not a second rendering: it is the very part ``send_plain_email`` puts on
    the wire for the same body — same headers, same transfer encoding, same encoded bytes."""
    await send_plain_email(CONFIG, [TO], "Aulas de novembro", TEXT)
    plain = _sent_message()
    await _send_html()(CONFIG, [TO], "Aulas de novembro", TEXT, HTML)
    text_part = _sent_message().get_payload()[0]
    assert plain.get_content_type() == "text/plain"           # control: plain is not multipart
    assert text_part.get_payload() == plain.get_payload()       # the encoded body, byte for byte
    for header in ("Content-Type", "Content-Transfer-Encoding", "MIME-Version"):
        assert text_part[header] == plain[header], header


async def test_headers_are_the_ones_every_sender_writes():
    await _send_html()(CONFIG, [TO], "Aulas de novembro", TEXT, HTML)
    msg = _sent_message()
    assert msg["Subject"] == "Aulas de novembro"
    assert msg["To"] == TO
    assert "from@test" in msg["From"]
    from_email, recipients, _ = FakeSMTP.sent[0]
    assert from_email == "from@test" and recipients == [TO]


async def test_sent_is_logged_as_html_and_without_the_address(caplog):
    with caplog.at_level(logging.DEBUG, logger="cogno_herald.smtp"):
        await _send_html()(CONFIG, [TO, "outra.pessoa@example.test"], "S", TEXT, HTML)
    sent = [r for r in caplog.records if "event=sent" in r.getMessage()]
    assert len(sent) == 1 and sent[0].levelno == logging.INFO
    assert "kind=html" in sent[0].getMessage() and "recipients=2" in sent[0].getMessage()
    assert not any("@" in r.getMessage() for r in caplog.records)


async def test_a_blank_html_sends_the_plain_message():
    """An empty HTML alternative is the part an HTML client SHOWS — an e-mail with no body. A
    blank one sends what ``send_plain_email`` sends, and says so in the log."""
    for blank in ("", "   \n"):
        await _send_html()(CONFIG, [TO], "S", TEXT, blank)
        msg = _sent_message()
        assert msg.get_content_type() == "text/plain"
        assert msg.get_payload(decode=True).decode("utf-8") == TEXT


async def test_a_blank_html_is_logged_as_plain(caplog):
    with caplog.at_level(logging.INFO, logger="cogno_herald.smtp"):
        await _send_html()(CONFIG, [TO], "S", TEXT, "")
    assert any("event=sent" in r.getMessage() and "kind=plain" in r.getMessage()
               for r in caplog.records)


async def test_no_valid_recipients_sends_nothing():
    result = await _send_html()(CONFIG, ["", "  ", None], "S", TEXT, HTML)
    assert result == {"sent": False, "error": "no_valid_recipients"}
    assert FakeSMTP.sent == []


async def test_a_refused_recipient_never_raises_and_the_log_names_no_address(monkeypatch, caplog):
    """An SMTP exception's TEXT can quote the recipients (``SMTPRecipientsRefused`` is a dict of
    them), so the failure line carries the exception's CLASS, never its message."""
    class Refusing(FakeSMTP):
        def sendmail(self, from_email, recipients, payload):
            raise smtplib.SMTPRecipientsRefused({r: (550, b"no such user") for r in recipients})

    monkeypatch.setattr(smtp.smtplib, "SMTP", Refusing)
    with caplog.at_level(logging.WARNING, logger="cogno_herald.smtp"):
        result = await _send_html()(CONFIG, [TO], "S", TEXT, HTML)
    assert result["sent"] is False and result["error"]
    failed = [r for r in caplog.records if "event=send_failed" in r.getMessage()]
    assert len(failed) == 1
    line = failed[0].getMessage()
    assert "kind=html" in line and "error=SMTPRecipientsRefused" in line
    assert TO not in line and "@" not in line


async def test_send_plain_email_is_unchanged():
    """Control: the plain sender still puts ONE text/plain part on the wire, as before."""
    await send_plain_email(CONFIG, [TO], "S", TEXT)
    msg = _sent_message()
    assert msg.get_content_type() == "text/plain" and not msg.is_multipart()
    assert msg.get_payload(decode=True).decode("utf-8") == TEXT


def test_it_is_exported_from_the_package():
    import cogno_herald
    assert "send_html_email" in cogno_herald.__all__
    assert cogno_herald.send_html_email is _send_html()
