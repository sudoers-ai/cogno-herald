"""What herald hands to ``sendmail`` is an SMTP message: every line ends in CRLF, none passes 998.

RFC 5321 §2.3.8: an SMTP client MUST NOT send a bare LF; a line ends in CRLF, and §4.5.3.1.6 caps
a line at 998 octets before it. ``Message.as_bytes()`` under the compat32 policy ends every line in
a bare ``\\n``, and ``smtplib.SMTP.sendmail`` sends ``bytes`` exactly as given (it fixes line ends
only for a ``str``). A server that reads up to CRLF then takes the WHOLE message for one line, and
every message over ~1000 bytes is refused (aiosmtpd: ``500 Line too long``) while a small one goes
through. The parts were never the problem: herald already sends every body part in base64, 76
columns. A plain message of thirty SHORT lines was refused like an HTML table on one long line.

``smtplib.SMTP`` is a fake here (no network). The round trip through a strict server is in
``tests/integration/test_smtp_live.py``.
"""

import email
import hashlib

import pytest

from cogno_herald import smtp
from cogno_herald.smtp import send_email_with_ics, send_plain_email

CONFIG = {"host": "smtp.test", "port": 587, "user": "u", "password": "p",
          "from_email": "from@test", "from_name": "Cogno", "use_tls": True}
TO = "destinataria.exemplo@example.test"
SUBJECT = "Aulas de novembro — 9A"

TEXT = "Olá, Professora.\n\n01/11 08:00 — Álgebra — 9A\n02/11 — Química — 9B\n"
# One physical line of over 5000 characters: the shape of a rendered table.
LONG_HTML = ("<!DOCTYPE html><html><body><table>"
             + "<tr><td>01/11</td><td>08:00</td><td>Álgebra — 9A</td></tr>" * 90
             + "</table></body></html>")
# Thirty lines, none over 60 characters: the defect is not about long lines.
SHORT_LINES = "".join(f"Linha {i:02d}: aula de Álgebra às 08:00 na sala 9A.\n" for i in range(30))
ICS = ("BEGIN:VCALENDAR\r\nVERSION:2.0\r\nMETHOD:REQUEST\r\nBEGIN:VEVENT\r\nUID:aula-1\r\n"
       + "".join(f"COMMENT:item {i:02d} da pauta da reunião\r\n" for i in range(30))
       + "END:VEVENT\r\nEND:VCALENDAR\r\n")

# A server reading CRLF lines with the RFC's limit (998 + CRLF) refuses a "line" past this.
STRICT_LIMIT = 1001


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
    FakeSMTP.sent = []
    monkeypatch.setattr(smtp.smtplib, "SMTP", FakeSMTP)


def _wire() -> bytes:
    assert len(FakeSMTP.sent) == 1, f"expected one message on the wire, got {len(FakeSMTP.sent)}"
    payload = FakeSMTP.sent[0][2]
    assert isinstance(payload, bytes)
    return payload


def _canonical(wire: bytes) -> bytes:
    """The wire with its line ends folded back to LF and the random MIME boundary named.

    What is left is every header, every Content-Transfer-Encoding and every encoded byte of every
    part. Its digest on this side of the fix equals the one taken on the tree before it (498a2e3),
    which is the proof that the ONLY bytes that changed are the line ends."""
    out = wire.replace(b"\r\n", b"\n")
    boundary = email.message_from_bytes(wire).get_boundary()
    return out.replace(boundary.encode("ascii"), b"BOUNDARY") if boundary else out


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:16]


SENDS = {
    "plain_thirty_short_lines": lambda: send_plain_email(CONFIG, [TO], SUBJECT, SHORT_LINES),
    "html_one_long_line": lambda: getattr(smtp, "send_html_email")(
        CONFIG, [TO], SUBJECT, TEXT, LONG_HTML),
    "ics": lambda: send_email_with_ics(CONFIG, [TO], SUBJECT, TEXT, ICS),
}


@pytest.mark.parametrize("case", sorted(SENDS))
async def test_no_bare_lf_and_no_line_over_998_reaches_sendmail(case):
    result = await SENDS[case]()
    assert result["sent"] is True
    wire = _wire()
    # Precondition, so the case cannot pass by being too small to be refused.
    assert len(wire) > STRICT_LIMIT, len(wire)
    assert wire.count(b"\n") == wire.count(b"\r\n"), "a bare LF reached sendmail"
    assert wire.endswith(b"\r\n")
    lines = wire.split(b"\r\n")
    assert max(len(line) for line in lines) <= 998


# Digests of `_canonical(wire)` taken on 498a2e3, the tree BEFORE the fix. A short text part keeps
# its transfer encoding (base64) and its encoded bytes; the long HTML and the .ics keep theirs.
_BEFORE = {
    "plain_short": "bad0a8e8222f8906",
    "plain_thirty_short_lines": "657833d08888955c",
    "html_one_long_line": "2b02d27fb328d840",
    "ics": "c240fd202620077c",
}
_CANONICAL_SENDS = dict(SENDS, plain_short=lambda: send_plain_email(CONFIG, [TO], SUBJECT, TEXT))


@pytest.mark.parametrize("case", sorted(_CANONICAL_SENDS))
async def test_only_the_line_ends_changed(case):
    await _CANONICAL_SENDS[case]()
    assert _sha(_canonical(_wire())) == _BEFORE[case]


@pytest.mark.parametrize("case", sorted(_CANONICAL_SENDS))
async def test_every_part_decodes_to_exactly_what_was_handed_over(case):
    """Content, not transport: the text the host audits, the HTML and the .ics come back off the
    wire byte for byte, compared by digest."""
    await _CANONICAL_SENDS[case]()
    msg = email.message_from_bytes(_wire())
    decoded = {p.get_content_type(): p.get_payload(decode=True)
               for p in msg.walk() if not p.is_multipart()}
    expected = {
        "plain_short": {"text/plain": TEXT},
        "plain_thirty_short_lines": {"text/plain": SHORT_LINES},
        "html_one_long_line": {"text/plain": TEXT, "text/html": LONG_HTML},
        "ics": {"text/plain": TEXT, "text/calendar": ICS},
    }[case]
    assert {k: _sha(v) for k, v in decoded.items()} == {
        k: _sha(v.encode("utf-8")) for k, v in expected.items()}
    for part in msg.walk():
        if not part.is_multipart():
            assert part["Content-Transfer-Encoding"] == "base64", part.get_content_type()
