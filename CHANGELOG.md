# Changelog

## Unreleased

- **Docs swept against the code.** `LOGGING.md` names every event the library emits: three were
  missing (`smtp` `tls_verification_disabled`, `otp` `insecure_store` and `checked`), and it now
  says which OTP lines carry the address. The README indexes every `.md` (`CHANGELOG.md` was not
  linked) and names the integration suite CI runs. This file gains the entries #11 and #12 shipped
  without. No code changes.
- **Every sender ends each line in CRLF on the wire** (RFC 5321 §2.3.8: an SMTP client MUST NOT
  send a bare LF).
  - Before, `as_bytes()` ended lines in a bare LF, and `smtplib` sends bytes as given. A server
    that reads CRLF lines took the WHOLE message for one line and refused it past 998 octets.
    Measured with `aiosmtpd`: `500 Line too long` for every message over about a kilobyte — a
    plain message of thirty short lines, an HTML table on one long line, a 1 KB `.ics`.
  - The defect predates `send_html_email` (#14): `send_plain_email` and `send_email_with_ics`
    had it from the first release. It stayed hidden because every test message was under 1001
    bytes, and a lenient capture server (MailHog) accepts bare LFs.
  - Exposure: a strict server refuses the message whole. `aiosmtpd` does by default, and so does
    Postfix with `smtpd_forbid_bare_newline` set to reject.
  - The transport encoding of every part does not change: base64, so no line of a part passes 76
    columns and no quoted-printable was added. The headers and the encoded bytes do not change
    either. Only the line ends of the wire bytes do, LF → CRLF.
  - The CONTENT of every part, decoded, is byte for byte what the caller passed: the text, the
    HTML and the `.ics`. A caller that audits a send compares the decoded content. The wire
    bytes are transport, and their encoding may change.
  - `tests/unit/test_smtp_wire.py` pins it: no bare LF and no line over 998 in what reaches
    `sendmail`, and the digests of the wire (line ends folded back, boundary named) taken before
    the fix. `tests/integration/test_smtp_live.py` sends the four shapes through a strict
    `aiosmtpd`, with a control that the same server refuses a bare-LF message.
  - Not covered: a header made of one unbroken ASCII token over ~990 characters (a subject or a
    sender name with no space) still folds into one line past 998 octets. No caller sends one.
- **`send_html_email(smtp, to, subject, text, html)`** sends `multipart/alternative`, the
  text part first and the HTML part second. An HTML client shows the HTML and every other
  client shows the text.
  - The text part is byte for byte the part `send_plain_email` sends for the same text.
  - Herald derives neither body from the other, and does not sanitise the HTML. Building it
    safely is the caller's job.
  - A blank `html` sends the plain message.
  - Logs `event=sent kind=html` with the recipient count, and no address. On failure it logs
    `event=send_failed kind=html error=<exception class>`: the class name, because an SMTP
    exception's message can quote the recipients.
  - `send_plain_email` and `send_email_with_ics` do not change.
  - `tests/unit/test_smtp_html.py` holds the unit half. A round trip through a local SMTP
    server is in `tests/integration/test_smtp_live.py`.
- **The zone travels in the `datetime` you pass** (#12; backfilled — it shipped without an entry).
  `build_ics_event` and `build_ics_cancel` render `DTSTART`/`DTEND` in the RFC 5545 form the
  value justifies: a naive `datetime` → floating (the bytes rendered before), an aware one in a
  named zone (`zoneinfo.ZoneInfo`) → `;TZID=<key>:` and the wall clock, any other aware one → UTC
  `Z`. Before, every value was rendered floating and an aware `tzinfo` was discarded. No
  `VTIMEZONE` is emitted.
- **`sequence` parameter on both iCalendar builders** (#11; backfilled — it shipped without an
  entry). `build_ics_event(..., sequence=0)` and `build_ics_cancel(..., sequence=1)` render the
  RFC 5545 revision number the caller passes. The defaults are the values that used to be
  hard-coded, so a caller that passes nothing is unchanged.

## 0.1.0 — 2026-07-25

First public release on PyPI.

Email/SMTP + iCalendar + OTP for the Cogno stack — stdlib-only delivery with an injectable OTP store (no Redis hard-dep) and host-owned credentials
