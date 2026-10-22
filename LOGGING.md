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

## No line carries an address

A line says which event happened and how much: an event name, a `kind`, a count, an
exception's class. It never carries an e-mail address, and never the text of an exception,
because an SMTP exception's message can quote the recipients (`SMTPRecipientsRefused` is a dict
of them). The domain of an address is not logged either, and neither is a digest of it: a
digest of an address is still pseudonymous personal data.

The caller knows whom it sent to or verified for, and correlates in its own log context. The
exception's message still reaches it, in `SendResult["error"]`. What the caller logs from there is
the caller's.

`tests/unit/test_no_address_in_logs.py` holds it. It drives every log call of the package with a
sentinel address, at DEBUG, and the sentinel must appear in no record: not in the rendered
message, not in the arguments, not in an exception. The calls are read from the source, so a new
line fails until a sentinel case drives it.

## What gets logged
- `cogno_herald.smtp` — INFO `event=sent`; WARNING `event=send_failed`. Both carry
  `kind=plain|html|ics`. `event=sent` logs the recipient COUNT. `event=send_failed` logs the
  exception's class name (`error=SMTPRecipientsRefused`), from all three senders.
  WARNING `event=tls_verification_disabled` (with the SMTP `host`, the operator's own
  configuration) on every STARTTLS send with `tls_verify` false and no `tls_ca_file`.
- `cogno_herald.otp` — INFO `event=sent|checked|verified|verify_failed` (`checked` is a
  correct code under `verify(consume=False)`); WARNING
  `event=no_sender|send_failed|burned|mock_bypass`; WARNING `event=insecure_store` once per
  `OtpService` built over `InMemoryOTPStore`; DEBUG `event=code_debug` (the code — dev only).
  The fields are counts: `ttl_s` on `sent`, `attempts` on `burned`, `attempt=n/max` on
  `verify_failed`. Which OTP lines carry the address: none.

Secrets (SMTP password, OTP code above DEBUG) are never logged.
