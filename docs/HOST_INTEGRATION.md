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

On the wire every line ends in CRLF (RFC 5321 §2.3.8), and every body part is
base64, so no line passes 76 columns. What you can rely on is the CONTENT: each
part, decoded, is byte for byte what you passed. The wire bytes are transport,
so compare the decoded content when you audit a send.

### HTML with a text alternative

`send_html_email(smtp, to, subject, text, html)` sends `multipart/alternative`:
the text part FIRST, the HTML part SECOND. RFC 2046 makes the last part the
preferred one, so an HTML client shows the HTML and every other client shows the
text. The text part is byte for byte the part `send_plain_email` sends for the
same text.

```python
from cogno_herald import send_html_email

await send_html_email(smtp, [client], "Your classes", text_body, html_body)
```

- **You hand over both bodies.** Herald derives neither from the other. When the
  text is what a person was shown and confirmed, the text you pass is that text.
- **Herald does not sanitise the HTML.** It sends what you pass. Build it safely
  on your side: no script, no external resource, and every value escaped.
- **A blank `html` sends the plain message** (`kind=plain` in the log), because
  an empty HTML part is what an HTML client would show.

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
