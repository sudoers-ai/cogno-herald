"""
cogno-herald — email/SMTP + iCalendar + OTP for the Cogno stack.

A self-contained communication utility: send plain or iCalendar-attached email
over SMTP (stdlib only), build RFC 5545 .ics invites/cancellations, and run a
6-digit OTP flow (generate/store/verify with bounded attempts).

Infra-agnostic: zero third-party runtime dependencies. The host owns the SMTP
credentials and the OTP state store — OTP state rides in an injected ``OTPStore``
(default ``InMemoryOTPStore``; plug Redis in a multi-worker host), and delivery
rides in an injected ``EmailSender``. Adapted from the parent cogno's
``core/email.py`` + ``core/otp.py``.
"""

from cogno_herald.ical import build_ics_cancel, build_ics_event
from cogno_herald.smtp import (
    resolve_smtp_config,
    send_email_with_ics,
    send_plain_email,
)
from cogno_herald.otp import (
    EmailSender,
    OtpService,
    extract_domain,
    generate_code,
    make_smtp_sender,
)
from cogno_herald.ports import InMemoryOTPStore, OTPStore
from cogno_herald.types import SendResult, SmtpConfig

__all__ = [
    # ical
    "build_ics_event",
    "build_ics_cancel",
    # smtp
    "resolve_smtp_config",
    "send_plain_email",
    "send_email_with_ics",
    # otp
    "OtpService",
    "EmailSender",
    "generate_code",
    "extract_domain",
    "make_smtp_sender",
    # ports
    "OTPStore",
    "InMemoryOTPStore",
    # types
    "SmtpConfig",
    "SendResult",
]
