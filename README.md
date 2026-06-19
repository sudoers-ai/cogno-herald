# cogno-herald

Email/SMTP + iCalendar + OTP for the [Cogno](https://github.com/sudoers-ai) stack —
the messenger of the ecosystem.

A self-contained communication utility, decoupled from any proprietary infra:

- **SMTP** — send plain or iCalendar-attached email over stdlib `smtplib`
  (async via `asyncio.to_thread`); senders never raise, they return a result dict.
- **iCalendar** — build RFC 5545 `.ics` invites (`REQUEST`) and cancellations
  (`CANCEL`) that work with Google/Outlook/Apple Calendar.
- **OTP** — a 6-digit one-time-password flow (generate/store/verify) with bounded
  attempts and single-use codes, generated with a CSPRNG (`secrets`).

## Design

- **Zero third-party runtime dependencies.** Pure stdlib.
- **Host owns infra.** SMTP credentials come from the host (tenant override → env);
  OTP state rides in an injectable **`OTPStore`** port (default `InMemoryOTPStore`,
  plug Redis in a multi-worker host), and delivery rides in an injectable
  **`EmailSender`** — mirroring the `cogno-homeo` in-memory-default pattern.
- **No surprises.** Senders return `SendResult`; nothing raises on a bad recipient
  or a dead SMTP host.

```python
from cogno_herald import OtpService, InMemoryOTPStore, make_smtp_sender, \
    resolve_smtp_config, send_plain_email

smtp = resolve_smtp_config(tenant_config)              # or None
sender = make_smtp_sender(send_plain_email, smtp) if smtp else None
otp = OtpService(InMemoryOTPStore(), sender=sender)

await otp.send("user@example.com", tenant_name="Acme")
ok = await otp.verify("user@example.com", code)
```

See [docs/HOST_INTEGRATION.md](docs/HOST_INTEGRATION.md) for the Redis adapter and
SMTP wiring, and [LOGGING.md](LOGGING.md) for the logging convention.

## Develop

```bash
pip install -e ".[dev]"
pytest tests/unit -q
ruff check cogno_herald tests examples
mypy cogno_herald
```

Apache-2.0.
