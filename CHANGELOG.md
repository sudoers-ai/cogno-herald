# Changelog

## Unreleased

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

## 0.1.0 — 2026-07-25

First public release on PyPI.

Email/SMTP + iCalendar + OTP for the Cogno stack — stdlib-only delivery with an injectable OTP store (no Redis hard-dep) and host-owned credentials
