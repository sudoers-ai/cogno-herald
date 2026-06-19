# Logging in cogno-herald

This library follows the Cogno house rule: **libraries emit, the host configures.**

- Each module does `logger = logging.getLogger(__name__)` and emits lazy
  `key=value` messages. The library installs **no** handlers/formatters and never
  calls `basicConfig`.
- The host attaches its handler (tenant/timestamp filter + JSON formatter) to the
  real root logger and sets the level per package, e.g.
  `logging.getLogger("cogno_herald").setLevel(logging.INFO)`.

## Level policy
- **ERROR** — never emitted by this lib (a fatal would propagate; here senders
  return a `SendResult(sent=False)` instead).
- **WARNING** — handled degradation: SMTP send failed, OTP burned after too many
  attempts, OTP requested with no sender configured, dev mock-bypass used.
- **INFO** — milestones: email sent, OTP sent, OTP verified.
- **DEBUG** — dev-only. The OTP **code** is logged **only** at DEBUG and only when
  no sender is configured (`event=code_debug`). Never enable DEBUG for
  `cogno_herald.otp` in a multi-tenant production environment.

## What gets logged
- `cogno_herald.smtp` — INFO `event=sent`; WARNING `event=send_failed`.
- `cogno_herald.otp` — INFO `event=sent|verified`; WARNING
  `event=no_sender|send_failed|burned|mock_bypass`; INFO `event=verify_failed`;
  DEBUG `event=code_debug` (the code — dev only).

Secrets (SMTP password, OTP code above DEBUG) are never logged.
