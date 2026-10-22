"""No log line carries an address, nor the text of an exception that can quote one.

The rule: a line says WHICH event and HOW MUCH (an event name, a kind, a count, an exception's
class), never text somebody else wrote. An e-mail address is personal data, and an SMTP
exception's message can quote the recipients (``SMTPRecipientsRefused`` is a dict of them).

Every log call of the package is driven here with a SENTINEL address, at DEBUG, and the sentinel
must appear in no record: not in the rendered message and not in the arguments a structured
handler might serialise on its own. Three controls keep the absence honest:

- each driver must PRODUCE the line it is for (an absence over a line that never ran proves
  nothing);
- a failed send must name the exception's class (``error=SMTPRecipientsRefused``), so the line
  still says what went wrong;
- the check itself must catch a PLANTED address, in the message and in the arguments.

The list of drivers is not trusted either: :func:`test_every_log_call_is_driven` reads the log
calls of the package from its source (module, function, event) and fails a call no driver
reaches, so a new line arrives with its sentinel case or does not arrive.
"""

from __future__ import annotations

import ast
import logging
import re
import smtplib
from pathlib import Path
from typing import Awaitable, Callable

import pytest

import cogno_herald
from cogno_herald import InMemoryOTPStore, OtpService, smtp
from cogno_herald.smtp import send_email_with_ics, send_html_email, send_plain_email

SENTINEL = "qorvane.tilmerk@sentinel-zq7.example"
LOCAL, DOMAIN = SENTINEL.split("@")

CONFIG = {"host": "relay.example.test", "port": 587, "user": "u", "password": "p",
          "from_email": "from@relay.example.test", "from_name": "Cogno", "use_tls": True}
ICS = "BEGIN:VCALENDAR\r\nMETHOD:REQUEST\r\nEND:VCALENDAR\r\n"

PKG = Path(cogno_herald.__file__).parent
LOGGERS = ("cogno_herald.otp", "cogno_herald.smtp")
_EVENT = re.compile(r"event=(\w+)")


class FakeSMTP:
    def __init__(self, host, port, timeout=30): ...
    def ehlo(self): ...
    def starttls(self, context=None): ...
    def login(self, user, password): ...
    def sendmail(self, from_email, recipients, payload): ...
    def quit(self): ...


class RefusingSMTP(FakeSMTP):
    """The server refuses every recipient: the exception's text quotes each one of them."""

    def sendmail(self, from_email, recipients, payload):
        raise smtplib.SMTPRecipientsRefused(
            {r: (550, f"5.1.1 <{r}>: recipient unknown".encode()) for r in recipients})


@pytest.fixture(autouse=True)
def _fake_smtp(monkeypatch):
    monkeypatch.setattr(smtp.smtplib, "SMTP", FakeSMTP)


# ── the check ─────────────────────────────────────────────────────────────────────────────────

def _leaks(records: list[logging.LogRecord]) -> list[str]:
    """Every record that carries the sentinel, or a part of it, anywhere a handler can print."""
    out = []
    for r in records:
        surfaces = (r.getMessage(), repr(r.args), r.exc_text or "", repr(r.exc_info or ""))
        for needle in (SENTINEL, LOCAL, DOMAIN):
            if any(needle in s for s in surfaces):
                out.append(f"{r.name}:{r.funcName}: {r.getMessage()!r}")
                break
    return out


def _event(r: logging.LogRecord) -> str:
    m = _EVENT.search(r.msg if isinstance(r.msg, str) else "")
    return m.group(1) if m else ""


# ── the drivers: one per log call, each over the sentinel address ────────────────────────────

async def _sender_ok(to, subject, body):
    return True


async def _sender_fails(to, subject, body):
    return False


async def _otp_insecure_store(monkeypatch):
    OtpService(InMemoryOTPStore())


async def _otp_no_sender(monkeypatch):
    await OtpService(InMemoryOTPStore()).send(SENTINEL)


async def _otp_sent(monkeypatch):
    await OtpService(InMemoryOTPStore(), sender=_sender_ok).send(SENTINEL)


async def _otp_send_failed(monkeypatch):
    await OtpService(InMemoryOTPStore(), sender=_sender_fails).send(SENTINEL)


async def _otp_mock_bypass(monkeypatch):
    assert await OtpService(InMemoryOTPStore(), mock_code="424242").verify(SENTINEL, "424242")


async def _otp_burned(monkeypatch):
    svc = OtpService(InMemoryOTPStore(), max_verify=1)
    await svc.send(SENTINEL)
    await svc.verify(SENTINEL, "x")
    assert await svc.verify(SENTINEL, "x") is False


async def _otp_checked(monkeypatch):
    svc = OtpService(InMemoryOTPStore())
    await svc.send(SENTINEL)
    assert await svc.verify(SENTINEL, await svc.store.get(f"otp:{SENTINEL}"), consume=False)


async def _otp_verified(monkeypatch):
    svc = OtpService(InMemoryOTPStore())
    await svc.send(SENTINEL)
    assert await svc.verify(SENTINEL, await svc.store.get(f"otp:{SENTINEL}"))


async def _otp_verify_failed(monkeypatch):
    svc = OtpService(InMemoryOTPStore())
    await svc.send(SENTINEL)
    assert await svc.verify(SENTINEL, "x") is False


async def _smtp_sent_plain(monkeypatch):
    assert (await send_plain_email(CONFIG, [SENTINEL], "S", "body"))["sent"]


async def _smtp_sent_html(monkeypatch):
    assert (await send_html_email(CONFIG, [SENTINEL], "S", "body", "<p>body</p>"))["sent"]


async def _smtp_sent_ics(monkeypatch):
    assert (await send_email_with_ics(CONFIG, [SENTINEL], "S", "body", ICS))["sent"]


async def _smtp_failed_plain(monkeypatch):
    monkeypatch.setattr(smtp.smtplib, "SMTP", RefusingSMTP)
    assert not (await send_plain_email(CONFIG, [SENTINEL], "S", "body"))["sent"]


async def _smtp_failed_html(monkeypatch):
    monkeypatch.setattr(smtp.smtplib, "SMTP", RefusingSMTP)
    assert not (await send_html_email(CONFIG, [SENTINEL], "S", "body", "<p>body</p>"))["sent"]


async def _smtp_failed_ics(monkeypatch):
    monkeypatch.setattr(smtp.smtplib, "SMTP", RefusingSMTP)
    assert not (await send_email_with_ics(CONFIG, [SENTINEL], "S", "body", ICS))["sent"]


async def _smtp_tls_unverified(monkeypatch):
    cfg = dict(CONFIG, tls_verify=False)
    assert (await send_plain_email(cfg, [SENTINEL], "S", "body"))["sent"]


# (driver, the line it must produce: logger, function, event)
DRIVERS: dict[str, tuple[Callable[..., Awaitable[None]], tuple[str, str, str]]] = {
    "otp_insecure_store": (_otp_insecure_store, ("cogno_herald.otp", "__init__", "insecure_store")),
    "otp_no_sender": (_otp_no_sender, ("cogno_herald.otp", "send", "no_sender")),
    "otp_code_debug": (_otp_no_sender, ("cogno_herald.otp", "send", "code_debug")),
    "otp_sent": (_otp_sent, ("cogno_herald.otp", "send", "sent")),
    "otp_send_failed": (_otp_send_failed, ("cogno_herald.otp", "send", "send_failed")),
    "otp_mock_bypass": (_otp_mock_bypass, ("cogno_herald.otp", "verify", "mock_bypass")),
    "otp_burned": (_otp_burned, ("cogno_herald.otp", "verify", "burned")),
    "otp_checked": (_otp_checked, ("cogno_herald.otp", "verify", "checked")),
    "otp_verified": (_otp_verified, ("cogno_herald.otp", "verify", "verified")),
    "otp_verify_failed": (_otp_verify_failed, ("cogno_herald.otp", "verify", "verify_failed")),
    "smtp_sent_plain": (_smtp_sent_plain, ("cogno_herald.smtp", "_send_smtp", "sent")),
    "smtp_sent_html": (_smtp_sent_html, ("cogno_herald.smtp", "_send_smtp", "sent")),
    "smtp_sent_ics": (_smtp_sent_ics, ("cogno_herald.smtp", "_send_smtp", "sent")),
    "smtp_failed_plain": (_smtp_failed_plain, ("cogno_herald.smtp", "send_plain_email", "send_failed")),
    "smtp_failed_html": (_smtp_failed_html, ("cogno_herald.smtp", "send_html_email", "send_failed")),
    "smtp_failed_ics": (_smtp_failed_ics, ("cogno_herald.smtp", "send_email_with_ics", "send_failed")),
    "smtp_tls_unverified": (_smtp_tls_unverified,
                            ("cogno_herald.smtp", "_tls_context", "tls_verification_disabled")),
}


async def _drive(name, monkeypatch, caplog) -> list[logging.LogRecord]:
    caplog.clear()
    for lg in LOGGERS:
        caplog.set_level(logging.DEBUG, logger=lg)
    await DRIVERS[name][0](monkeypatch)
    return [r for r in caplog.records if r.name in LOGGERS]


def _line(records, want) -> list[logging.LogRecord]:
    return [r for r in records if (r.name, r.funcName, _event(r)) == want]


@pytest.mark.parametrize("name", sorted(DRIVERS))
async def test_no_line_carries_the_address(name, monkeypatch, caplog):
    records = await _drive(name, monkeypatch, caplog)
    want = DRIVERS[name][1]
    # Control: the line this case is for RAN. An absence over a line that never ran is no proof.
    assert _line(records, want), f"{name}: the line {want} was not emitted"
    assert _leaks(records) == []


@pytest.mark.parametrize("name", ["smtp_failed_plain", "smtp_failed_html", "smtp_failed_ics"])
async def test_a_failed_send_names_the_exception_class(name, monkeypatch, caplog):
    """Control: without the text, the line still says WHAT went wrong — the exception's class."""
    records = await _drive(name, monkeypatch, caplog)
    (line,) = _line(records, DRIVERS[name][1])
    assert "error=SMTPRecipientsRefused" in line.getMessage()


def test_the_check_sees_a_planted_address(caplog):
    """Control: the check catches the sentinel in the message, in the arguments, and in a part."""
    planted = logging.getLogger("cogno_herald.otp")
    with caplog.at_level(logging.DEBUG, logger="cogno_herald.otp"):
        planted.info("stage=otp event=planted email=%s", SENTINEL)
        assert len(_leaks(caplog.records)) == 1
        caplog.clear()
        planted.info("stage=otp event=planted domain=%s", DOMAIN)
        assert len(_leaks(caplog.records)) == 1
        caplog.clear()

        class Quiet:   # renders clean, but a handler that serialises the arguments sees it
            def __str__(self):
                return "x"

            def __repr__(self):
                return f"Quiet({SENTINEL!r})"

        planted.info("stage=otp event=planted error=%s", Quiet())
        assert SENTINEL not in caplog.records[0].getMessage()
        assert len(_leaks(caplog.records)) == 1


# ── the drivers cover every log call of the package, read from the source ────────────────────

_LEVELS = {"debug", "info", "warning", "error", "exception", "critical"}


def _source_log_calls() -> set[tuple[str, str, str]]:
    """(logger, enclosing function, event) of every ``logger.<level>(...)`` call in the package."""
    found: set[tuple[str, str, str]] = set()

    class V(ast.NodeVisitor):
        def __init__(self, mod: str):
            self.mod, self.stack = mod, []

        def _fn(self, node):
            self.stack.append(node.name)
            self.generic_visit(node)
            self.stack.pop()

        visit_FunctionDef = visit_AsyncFunctionDef = _fn

        def visit_Call(self, node):
            f = node.func
            if (isinstance(f, ast.Attribute) and f.attr in _LEVELS
                    and isinstance(f.value, ast.Name) and f.value.id == "logger"):
                first = node.args[0] if node.args else None
                text = first.value if isinstance(first, ast.Constant) else ""
                m = _EVENT.search(text if isinstance(text, str) else "")
                found.add((self.mod, self.stack[-1] if self.stack else "<module>",
                           m.group(1) if m else "<no event>"))
            self.generic_visit(node)

    for path in sorted(PKG.glob("*.py")):
        V(f"cogno_herald.{path.stem}").visit(ast.parse(path.read_text(encoding="utf-8")))
    return found


def test_every_log_call_is_driven():
    calls = _source_log_calls()
    assert len(calls) >= 14, f"the source reader found only {len(calls)} log calls"
    driven = {want for _, want in DRIVERS.values()}
    assert calls - driven == set(), "a log call no sentinel case drives"
    assert driven - calls == set(), "a driver for a log call the source no longer has"
