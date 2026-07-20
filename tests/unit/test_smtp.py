"""SMTP send + config resolution — fake SMTP, no network."""

import logging
import ssl

import pytest

from cogno_herald import smtp
from cogno_herald.smtp import resolve_smtp_config, send_email_with_ics, send_plain_email

_SELF_SIGNED_CA = """-----BEGIN CERTIFICATE-----
MIIDATCCAemgAwIBAgIUH2wE+6R6L/7YdvGcJhl4BvsDCFYwDQYJKoZIhvcNAQEL
BQAwEDEOMAwGA1UEAwwFcHJvYmUwHhcNMjYwNzIwMjI1NzQ4WhcNMjYwNzIxMjI1
NzQ4WjAQMQ4wDAYDVQQDDAVwcm9iZTCCASIwDQYJKoZIhvcNAQEBBQADggEPADCC
AQoCggEBAK2zKyh7qhs4AvPMuSrgYyoTw7IcTyG7l2dwOtA1/bpT6H/HEDGDZd3n
HR5RlhjRR015oMZnzimf2IOMVNfknmJ7432RSiO0JdOnr5t5FJUgM8ijPVNW5U3V
OJdJhNStC1xwqvP9huMeyO6RiAI1gouY+vzg9coByENAqix/vl5waBicEifXy66n
5/r68D8NC1nQdmQ+lulxcszJg1v77TqZ3OiWUQIVEgEZgOZirjPuSse2qoTYKG6r
r7yvTO/UUlaC8+MGhkoZ9PkSp7ilZWh6waZKwPtZzs/tSZETC9t4XrX5Tgvb5c9k
DRrjxNO4Dh8IGkZjZ6MuPm6SL9/HiYUCAwEAAaNTMFEwHQYDVR0OBBYEFITRy4RX
1uDi/aB42mc6R7c0woXoMB8GA1UdIwQYMBaAFITRy4RX1uDi/aB42mc6R7c0woXo
MA8GA1UdEwEB/wQFMAMBAf8wDQYJKoZIhvcNAQELBQADggEBAJm4ifQD4Mjdjv98
0oBP1GEHeDAr6Asw0PWfAt2ryfGFbU/ks88wV1vMWpYIyd8eyZKHhVZeNmbYawMD
8oPv6U7bVa/2Zwa+HcenX3MTIXV8fKJlX21ddCwQYrlq5Tsy3QlGGSabztG7hjKO
4m7VUSIVygiqB6r0ECA9wR8BL4gp06BIwpKCHIaMu/dznH7+5XpK8Q+S50qU0VDg
HEeqCOeceq/Q6nNaywJlP/guyy9H7L3Vb4TnWC1scOfwSRS/as6OyYK0NjoGNKt1
5V+fgNqUeWxp4ove++9lHCksWoMDpC46vWwNkeG+pTpp6GaGyptzyqigQsV9OOVy
+skodoc=
-----END CERTIFICATE-----
"""

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


async def test_private_ca_is_trusted_and_still_verified():
    """An internal relay gets a trust anchor, not a verification bypass."""
    import pathlib
    ca = pathlib.Path(__file__).parent / "_ca_probe.pem"
    ca.write_text(_SELF_SIGNED_CA)
    try:
        await send_plain_email({**CONFIG, "tls_ca_file": str(ca)}, ["a@x.com"], "Hi", "body")
        ctx = FakeSMTP.tls_context
        assert ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname is True
    finally:
        ca.unlink()


async def test_verification_can_be_disabled_but_says_so(caplog):
    """The last-resort hatch still encrypts, and must leave a trace in the log — otherwise an
    unauthenticated session looks identical to a verified one."""
    with caplog.at_level(logging.WARNING, logger="cogno_herald.smtp"):
        await send_plain_email({**CONFIG, "tls_verify": False}, ["a@x.com"], "Hi", "body")
    ctx = FakeSMTP.tls_context
    assert ctx is not None and ctx.verify_mode == ssl.CERT_NONE
    assert any("event=tls_verification_disabled" in r.getMessage() for r in caplog.records)
