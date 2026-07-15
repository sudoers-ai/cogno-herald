"""
cogno_herald.otp — one-time-password engine (generate / store / verify).

A reusable OTP service: generate a 6-digit code, store it with a TTL, email it,
and verify it with bounded attempts (burn after too many failures). Two host
seams keep it infra-agnostic:

  - ``OTPStore`` (port) for state — default ``InMemoryOTPStore``; host plugs Redis.
  - ``EmailSender`` (callable) for delivery — e.g. wrap ``smtp.send_plain_email``
    via :func:`make_smtp_sender`; ``None`` means "store only" (dev: code at DEBUG).

Codes are generated with ``secrets`` (CSPRNG), an upgrade over the parent's
``random``. The code value is **never** logged above DEBUG.
"""

from __future__ import annotations

import logging
import secrets
from typing import Awaitable, Callable, Optional

from cogno_herald.ports import InMemoryOTPStore, OTPStore

logger = logging.getLogger(__name__)

# (to_email, subject, body) -> was it sent?  Host-injected delivery.
EmailSender = Callable[[str, str, str], Awaitable[bool]]


def generate_code() -> str:
    """Generate a CSPRNG 6-digit OTP code."""
    return f"{secrets.randbelow(1_000_000):06d}"


def extract_domain(email: str) -> str:
    """Domain part of an email, lowercased; ``""`` for an invalid address."""
    if not email or "@" not in email:
        return ""
    return email.rsplit("@", 1)[-1].lower().strip()


def make_smtp_sender(send_fn: Callable[..., Awaitable[dict]], smtp_config: dict) -> EmailSender:
    """Adapt :func:`cogno_herald.smtp.send_plain_email` (or any ``(config, to_list,
    subject, body) -> SendResult``) into the ``EmailSender`` shape the OTP wants."""

    async def _sender(to_email: str, subject: str, body: str) -> bool:
        result = await send_fn(smtp_config, [to_email], subject, body)
        return bool(result.get("sent"))

    return _sender


class OtpService:
    """Stateless-across-instances OTP engine; all state rides in the injected
    ``OTPStore`` (so it survives a multi-worker host)."""

    def __init__(
        self,
        store: Optional[OTPStore] = None,
        *,
        sender: Optional[EmailSender] = None,
        ttl_seconds: int = 900,
        max_verify: int = 5,
        mock_code: Optional[str] = None,
    ) -> None:
        self.store: OTPStore = store or InMemoryOTPStore()
        self.sender = sender
        self.ttl_seconds = ttl_seconds
        self.max_verify = max_verify
        # Dev-only fixed code that always verifies; leave None in production.
        self.mock_code = mock_code

        # SECURITY: the brute-force lockout (``max_verify`` + burn) only holds if
        # the attempt counter is shared and atomic across workers. The in-memory
        # store is process-local, so in a multi-worker deployment an attacker can
        # spread guesses across processes and never trip the global limit. Warn so
        # a misconfigured production deploy is at least loud. Plug a Redis-backed
        # OTPStore (atomic INCR) instead — see docs/HOST_INTEGRATION.md.
        if isinstance(self.store, InMemoryOTPStore):
            logger.warning(
                "stage=otp event=insecure_store store=InMemoryOTPStore "
                "reason=process_local_counter_defeats_lockout_in_multiworker "
                "fix=plug_redis_otpstore")

    @staticmethod
    def _code_key(email: str) -> str:
        return f"otp:{email}"

    @staticmethod
    def _attempts_key(email: str) -> str:
        return f"otp_attempts:{email}"

    async def send(self, email: str, *, tenant_name: str = "Cogno",
                   sender: Optional[EmailSender] = None) -> bool:
        """Generate, store, and (if a sender is configured) email a fresh code.

        ``sender`` overrides the instance sender for THIS call — so a multi-tenant host can send
        the code from the tenant's own SMTP (resolved per elevation) without rebuilding the service
        (whose ``store`` must persist across send/verify). Falls back to the instance ``sender``.

        Returns ``True`` when the code was stored (delivery is best-effort).
        """
        code = generate_code()
        await self.store.set(self._code_key(email), code, self.ttl_seconds)
        await self.store.delete(self._attempts_key(email))

        use_sender = sender or self.sender
        if use_sender is None:
            logger.warning(
                "stage=otp event=no_sender email=%s reason=stored_only (code at DEBUG)", email)
            logger.debug("stage=otp event=code_debug email=%s code=%s", email, code)
            return True

        ttl_min = self.ttl_seconds // 60
        body = (
            f"Your {tenant_name} verification code is:\n\n"
            f"    {code}\n\n"
            f"This code expires in {ttl_min} minutes.\n"
            f"If you did not request it, ignore this message."
        )
        sent = await use_sender(email, f"Verification Code — {tenant_name}", body)
        if sent:
            logger.info("stage=otp event=sent email=%s ttl_s=%d", email, self.ttl_seconds)
        else:
            logger.warning("stage=otp event=send_failed email=%s", email)
        return True

    async def verify(self, email: str, code: str) -> bool:
        """Verify ``code``; track attempts and burn the OTP after ``max_verify``."""
        if self.mock_code is not None and code == self.mock_code:
            logger.warning("stage=otp event=mock_bypass email=%s", email)
            return True

        attempts = await self.store.incr(self._attempts_key(email), self.ttl_seconds)
        if attempts > self.max_verify:
            await self.store.delete(self._code_key(email))
            await self.store.delete(self._attempts_key(email))
            logger.warning("stage=otp event=burned email=%s attempts=%d", email, attempts)
            return False

        stored = await self.store.get(self._code_key(email))
        if stored is not None and stored == code:
            await self.store.delete(self._code_key(email))
            await self.store.delete(self._attempts_key(email))
            logger.info("stage=otp event=verified email=%s", email)
            return True

        logger.info("stage=otp event=verify_failed email=%s attempt=%d/%d",
                    email, attempts, self.max_verify)
        return False

    async def has_pending(self, email: str) -> bool:
        """Is there a live OTP for this email?"""
        return await self.store.exists(self._code_key(email))

    async def clear(self, email: str) -> None:
        """Drop any pending OTP + attempt counter (timeout/cancel)."""
        await self.store.delete(self._code_key(email))
        await self.store.delete(self._attempts_key(email))
