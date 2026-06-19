"""
Minimal host wiring for cogno-herald.

Run: python examples/host_min.py  (prints, sends nothing — no SMTP configured).
"""

import asyncio
from datetime import datetime

from cogno_herald import (
    InMemoryOTPStore,
    OtpService,
    build_ics_event,
    make_smtp_sender,
    resolve_smtp_config,
    send_email_with_ics,
    send_plain_email,
)


async def main() -> None:
    # 1. Build a calendar invite (pure, no I/O).
    ics = build_ics_event(
        uid="appt-42",
        summary="Dentist",
        dtstart=datetime(2026, 6, 20, 9, 0),
        dtend=datetime(2026, 6, 20, 9, 30),
        organizer_email="clinic@example.com",
        attendees=["patient@example.com"],
    )
    print("ICS bytes:", len(ics))

    # 2. Send email — only if the host has SMTP configured (tenant override / env).
    smtp = resolve_smtp_config()
    if smtp:
        await send_email_with_ics(smtp, ["patient@example.com"], "Your appointment",
                                  "See attached invite.", ics)

    # 3. OTP flow — in-memory store (swap for a Redis adapter in production); the
    #    sender is wired from SMTP, or omitted (dev: code logged at DEBUG).
    sender = make_smtp_sender(send_plain_email, smtp) if smtp else None
    otp = OtpService(InMemoryOTPStore(), sender=sender)
    await otp.send("patient@example.com", tenant_name="Acme")
    print("pending:", await otp.has_pending("patient@example.com"))


if __name__ == "__main__":
    asyncio.run(main())
