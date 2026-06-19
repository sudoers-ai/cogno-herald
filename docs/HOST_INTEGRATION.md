# Host integration — cogno-herald

`cogno-herald` is infra-agnostic: it provides the *operations* (build .ics, send
SMTP, run an OTP flow); the host owns the credentials, the OTP state store, and
the scheduling. Zero third-party runtime dependencies.

## 1. SMTP config

`resolve_smtp_config(tenant_config)` resolves **tenant override → `SMTP_*` env →
None**. Pass your tenant's `schedule_config` to honour a per-tenant SMTP; pass
nothing to use the environment. A `None` result means "email not configured" —
skip silently.

```python
from cogno_herald import resolve_smtp_config, send_email_with_ics, build_ics_event

smtp = resolve_smtp_config(tenant_config)          # or None
if smtp:
    ics = build_ics_event(uid=appt_id, summary="Visit", dtstart=a, dtend=b,
                          organizer_email="clinic@x.com", attendees=[client])
    await send_email_with_ics(smtp, [client], "Your appointment", body, ics)
```

Senders **never raise** — they return `SendResult` (`{"sent": bool, ...}`). A
transport failure is a `WARNING` + `{"sent": False, "error": ...}`, so your
scheduler decides whether to retry.

## 2. OTP — bring your own store (and sender)

The parent hard-wired Redis. Here OTP state rides in an injected `OTPStore`
(KV-with-TTL Protocol). The default `InMemoryOTPStore` is fine for a single
worker / tests; for a multi-worker host, implement the port over Redis:

```python
class RedisOTPStore:                      # ~ implements cogno_herald.OTPStore
    def __init__(self, r): self.r = r
    async def set(self, k, v, ttl): await self.r.setex(k, ttl, v)
    async def get(self, k): return await self.r.get(k)
    async def delete(self, k): await self.r.delete(k)
    async def incr(self, k, ttl):
        n = await self.r.incr(k)
        if n == 1: await self.r.expire(k, ttl)
        return n
    async def exists(self, k): return bool(await self.r.exists(k))

from cogno_herald import OtpService, make_smtp_sender, send_plain_email
sender = make_smtp_sender(send_plain_email, smtp) if smtp else None
otp = OtpService(RedisOTPStore(redis), sender=sender, ttl_seconds=900, max_verify=5)

await otp.send(email, tenant_name="Acme")     # generates, stores, emails
ok = await otp.verify(email, user_code)        # bounded attempts; burns on success
```

With **no sender**, `send()` stores the code and logs it at DEBUG only
(`event=code_debug`) — a dev convenience, never for production.

## 3. Logging

See [LOGGING.md](../LOGGING.md). Libraries emit `key=value`; the host attaches the
handler and sets `logging.getLogger("cogno_herald").setLevel(...)`. Keep
`cogno_herald.otp` above DEBUG in production (the code prints at DEBUG).
