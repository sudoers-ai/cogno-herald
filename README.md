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

`resolve_smtp_config` reads `SMTP_HOST` / `SMTP_PORT` / `SMTP_USER` / `SMTP_PASSWORD` /
`SMTP_FROM_EMAIL` / `SMTP_FROM_NAME` / `SMTP_USE_TLS` (tenant config wins over the
environment).

**STARTTLS verifies the server certificate.** If your relay presents a certificate no public
CA vouches for, point `SMTP_TLS_CA_FILE` at its CA rather than turning verification off — the
session stays authenticated. `SMTP_TLS_VERIFY=false` is the last resort (encrypted but
unauthenticated; it logs `event=tls_verification_disabled` every send). Do not reach for
`SMTP_USE_TLS=false` for this: that drops encryption entirely, on a session carrying your SMTP
password and every OTP.

See [docs/HOST_INTEGRATION.md](docs/HOST_INTEGRATION.md) for the Redis adapter and
SMTP wiring, and [LOGGING.md](LOGGING.md) for the logging convention.

## The Cogno ecosystem

`cogno-herald` is one organ of **[Cogno](https://github.com/sudoers-ai)** — a family of
small, composable, Apache-2.0 libraries that together form a complete
conversational-agent platform. Each library owns a single concern and stays
infra-agnostic; a **host** assembles them into a running agent:

![The Cogno ecosystem](docs/assets/cogno-ecosystem.svg)

The open-source libraries are the organs; the **host is the body** that joins
them. Our reference host — `cogno-host`, with its `cogno-ui` dashboard — is the
private product layer, but it holds no special powers: everything it does rides
on the public seams documented in each library's `docs/HOST_INTEGRATION.md`, so
you can assemble a body of your own.

## Develop

```bash
pip install -e ".[dev]"
pytest tests/unit -q
ruff check cogno_herald tests examples
mypy cogno_herald
```

Apache-2.0.
