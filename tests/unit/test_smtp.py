"""SMTP send + config resolution — fake SMTP, no network."""

import logging
import ssl

import pytest

from cogno_herald import smtp
from cogno_herald.smtp import resolve_smtp_config, send_email_with_ics, send_plain_email

CONFIG = {"host": "smtp.test", "port": 587, "user": "u", "password": "p",
          "from_email": "from@test", "from_name": "Cogno", "use_tls": True}


class FakeSMTP:
    sent: list = []
    tls_context = None

    def __init__(self, host, port, timeout=30):
        self.host = host
        FakeSMTP.sent = []
        FakeSMTP.tls_context = None

    def ehlo(self): ...
    def starttls(self, context=None):
        FakeSMTP.tls_context = context
    def login(self, user, password): ...
    def sendmail(self, from_email, recipients, payload):
        FakeSMTP.sent.append((from_email, recipients, payload))
    def quit(self): ...


@pytest.fixture(autouse=True)
def _patch_smtp(monkeypatch):
    monkeypatch.setattr(smtp.smtplib, "SMTP", FakeSMTP)


async def test_send_plain_email_ok(caplog):
    with caplog.at_level(logging.INFO, logger="cogno_herald.smtp"):
        result = await send_plain_email(CONFIG, ["a@x.com"], "Hi", "body")
    assert result == {"sent": True, "recipients": ["a@x.com"]}
    assert any("event=sent" in r.message and r.levelno == logging.INFO
               for r in caplog.records)


async def test_starttls_uses_a_verifying_context():
    """STARTTLS must verify the server cert: bare ``starttls()`` is CERT_NONE on py≤3.12,
    which would let an active MITM read the SMTP password and every OTP we mail."""
    await send_plain_email(CONFIG, ["a@x.com"], "Hi", "body")
    ctx = FakeSMTP.tls_context
    assert ctx is not None, "starttls() called without an ssl context"
    assert ctx.verify_mode == ssl.CERT_REQUIRED
    assert ctx.check_hostname is True


async def test_plain_connection_skips_tls_when_disabled():
    await send_plain_email({**CONFIG, "use_tls": False}, ["a@x.com"], "Hi", "body")
    assert FakeSMTP.tls_context is None


async def test_send_filters_empty_recipients():
    result = await send_plain_email(CONFIG, ["", "   ", None], "Hi", "body")
    assert result == {"sent": False, "error": "no_valid_recipients"}


async def test_send_with_ics_attaches_calendar_payload():
    ics = "BEGIN:VCALENDAR\r\nMETHOD:CANCEL\r\nEND:VCALENDAR\r\n"
    result = await send_email_with_ics(CONFIG, ["a@x.com"], "Bye", "body", ics)
    assert result["sent"] is True
    _, _, payload = FakeSMTP.sent[0]
    assert b"text/calendar" in payload


async def test_send_failure_is_caught_and_logged(monkeypatch, caplog):
    def boom(*a, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(smtp.smtplib, "SMTP", boom)
    with caplog.at_level(logging.WARNING, logger="cogno_herald.smtp"):
        result = await send_plain_email(CONFIG, ["a@x.com"], "Hi", "body")
    assert result["sent"] is False and "connection refused" in result["error"]
    assert any("event=send_failed" in r.message for r in caplog.records)


def test_resolve_prefers_tenant_then_env_then_none(monkeypatch):
    monkeypatch.delenv("SMTP_HOST", raising=False)
    assert resolve_smtp_config() is None

    monkeypatch.setenv("SMTP_HOST", "env.smtp")
    assert resolve_smtp_config()["host"] == "env.smtp"

    tenant = {"schedule_config": {"smtp": {"host": "tenant.smtp"}}}
    assert resolve_smtp_config(tenant)["host"] == "tenant.smtp"
