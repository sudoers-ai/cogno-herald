# Changelog

## 0.1.1 — 2026-11-06 — release to PyPI of the changes below

Publishes every «Unreleased» entry below, merged since 0.1.0. No change of its own.

## 0.1.3 — 2026-11-25

- **The SMTP sender caps the TCP connect and tries IPv4 first, then falls back to the default
  path.** `smtplib` connects family by family in `getaddrinfo` order (IPv6 first), in series, with
  no happy eyeballs, so a host whose IPv6 route to the relay is broken waited the full connect
  timeout before trying IPv4. Inside a caller that caps the whole send (e.g. a 30 s tool budget),
  that one connect could exhaust the budget and drop the send.
  - `_connect` now opens the socket through `_open(host, port)`, which dials through a small
    `smtplib.SMTP` subclass (`_SMTP`) whose `_get_socket` caps the TCP connect alone: a first
    attempt bound to the IPv4 wildcard (`source_address=("0.0.0.0", 0)`) under a short 3 s connect
    cap, so IPv4 is tried first; on a TCP connect failure — nothing sent, no greeting read — a
    second attempt on the default path (IPv6/any) under an 8 s connect cap.
  - The short cap is on the **connect only**. The greeting and the whole session keep the 30 s
    session timeout, because RFC 5321 §4.5.3.2.1 lets a server delay its 220 greeting (a tarpit /
    greet-pause, or a tenant's own SMTP) — capping the greeting too would wrongly retry a slow but
    healthy relay. STARTTLS, login and DATA are unchanged.
  - Only the TCP connect is retried. A failure once the connection exists — a slow or absent
    greeting, a non-220 reply, STARTTLS, login, the send — is never retried: the connection existed
    and the message could then go out twice. The connect failure is raised as `_ConnectFailed` (an
    `OSError` subclass), and only that type triggers the fallback.
  - Logs `event=smtp_ipv4_connect_failed` (INFO) with the failed connect's exception CLASS only
    when the IPv4 attempt falls back — never the host, never a user.
  - `tests/unit/test_smtp_ipv4_first.py` pins it with a fake `socket.create_connection`: IPv4 ok →
    one connect with the source address and the socket widened to the 30 s session timeout; a
    connect failure → a second connect with no source address and the 8 s cap; a slow greeting →
    one connect, no fallback (the cap was on the connect, not the greeting); a non-220 greeting →
    `SMTPConnectError`, no second connect; both connects fail → a clean `_ConnectFailed`; a login
    error after connect → one connect only; the fallback log names the connect error's class and
    no host.

## 0.1.2 — 2026-11-11

- **The `.ics` builders are hardened against calendar injection (RFC 5545).**
  - `UID` and e-mail addresses reach `build_ics_event` / `build_ics_cancel` from outside (an
    appointment id, a guest's address). A raw CR or LF in `uid`, `organizer_email` or an
    attendee address could end the content line early and forge a property (`UID:x\r\nSUMMARY:…`,
    a spoofed `ATTENDEE`). Those identifier/address fields now reject a raw CR/LF loudly
    (`ValueError`), and the `UID` is TEXT-escaped (`\ ; ,`) like the other TEXT values.
  - `_ical_escape` now also collapses every newline form (`\r\n`, a lone `\r`, `\n`) to the
    RFC literal `\n`, so a newline in free text (a multi-line DESCRIPTION) is preserved as
    content and can never break the line structure.
  - Content lines are folded to 75 octets (§3.1), on a character boundary so a multi-byte
    UTF-8 codepoint is never split; an unfolding parser reconstructs the value.
  - Short, well-formed invites are byte-for-byte unchanged.

- **No log line carries an e-mail address, nor the text of an exception.**
  - Before, every OTP line about a code logged the address it was for (`email=`): `sent`,
    `send_failed`, `no_sender`, `code_debug`, `mock_bypass`, `burned`, `checked`, `verified`,
    `verify_failed`. They now log the event and its counts only (`ttl_s`, `attempts`,
    `attempt=n/max`).
  - `send_plain_email` and `send_email_with_ics` logged the exception's MESSAGE on failure, and
    an SMTP exception's message can quote the recipients (`SMTPRecipientsRefused` is a dict of
    them). They now log its class name, as `send_html_email` already did.
  - Not logged instead: the domain of the address, or a digest of it. A digest of an address is
    still pseudonymous personal data. The caller knows whom it sent to, and correlates in its
    own log context.
  - Unchanged: the event names and levels, the recipient count on `event=sent`, the SMTP `host`
    on `event=tls_verification_disabled`, the code on `event=code_debug` (DEBUG, no sender
    only), and `SendResult["error"]`, which still carries the exception's message to the caller.
  - `tests/unit/test_no_address_in_logs.py` drives every log call of the package with a sentinel
    address at DEBUG; the sentinel must appear in no record. On the previous tree 13 of its 22
    tests fail: the nine OTP lines, and the plain and `.ics` failures twice each (the address,
    and the class name the line now carries). The log calls are read from the source, so a new
    line fails until a sentinel case drives it.
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
