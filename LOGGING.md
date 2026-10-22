# Logging in cogno-herald

This library follows the Cogno house rule: **libraries emit, the host configures.**

- Each module that logs (`smtp`, `otp`) does `logger = logging.getLogger(__name__)` and
  emits lazy `key=value` messages. The library installs **no** handlers/formatters and never
  calls `basicConfig`.
- The host attaches its handler (tenant/timestamp filter + JSON formatter) to the
  real root logger and sets the level per package, e.g.
  `logging.getLogger("cogno_herald").setLevel(logging.INFO)`.

## Level policy
- **ERROR** — never emitted by this lib (a fatal would propagate; here senders
  return a `SendResult(sent=False)` instead).
- **WARNING** — handled degradation: SMTP send failed, STARTTLS certificate verification
  turned off, OTP burned after too many attempts, OTP requested with no sender configured,
  `OtpService` built over the process-local `InMemoryOTPStore`, dev mock-bypass used.
- **INFO** — milestones: email sent, OTP sent, OTP checked or verified, OTP verify failed.
- **DEBUG** — dev-only. The OTP **code** is logged **only** at DEBUG and only when
  no sender is configured (`event=code_debug`). Never enable DEBUG for
  `cogno_herald.otp` in a multi-tenant production environment.

## What gets logged
- `cogno_herald.smtp` — INFO `event=sent`; WARNING `event=send_failed`. Both carry
  `kind=plain|html|ics`. `event=sent` logs the recipient COUNT, never an address.
  `event=send_failed` from `send_html_email` logs the exception's class name, not its message,
  because an SMTP exception's message can quote the recipients (`SMTPRecipientsRefused` is a
  dict of them). `send_plain_email` and `send_email_with_ics` still log the message, as before.
  WARNING `event=tls_verification_disabled` (with the SMTP `host`) on every STARTTLS send with
  `tls_verify` false and no `tls_ca_file`.
- `cogno_herald.otp` — INFO `event=sent|checked|verified|verify_failed` (`checked` is a
  correct code under `verify(consume=False)`); WARNING
  `event=no_sender|send_failed|burned|mock_bypass`; WARNING `event=insecure_store` once per
  `OtpService` built over `InMemoryOTPStore`; DEBUG `event=code_debug` (the code — dev only).
  Every line about a code carries the address it is for (`email=`); `insecure_store` carries
  none.

Secrets (SMTP password, OTP code above DEBUG) are never logged.
