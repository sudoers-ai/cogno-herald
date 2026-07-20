"""
cogno_herald.smtp — SMTP sender (plain + iCalendar attachment) and config resolution.

Zero third-party deps — stdlib ``smtplib`` + ``email.mime``, made async via
``asyncio.to_thread`` since smtplib is blocking. Senders never raise: they return
a ``SendResult`` dict so a host scheduler can decide what to do on failure.

Ported from the parent cogno's ``core/email.py``. Logging follows the house
convention: ``key=value`` lazy messages, no handlers configured here.
"""

from __future__ import annotations

import asyncio
import logging
import os
import smtplib
import ssl
from email import encoders
from email.header import Header
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import List, Optional

from cogno_herald.types import SendResult, SmtpConfig

logger = logging.getLogger(__name__)


def resolve_smtp_config(tenant_config: Optional[dict] = None) -> Optional[SmtpConfig]:
    """Resolve SMTP config: tenant override → environment → ``None``.

    Priority:
      1. ``tenant_config["schedule_config"]["smtp"]`` (per-tenant override)
      2. ``SMTP_*`` environment variables
      3. ``None`` (no email configured — caller skips silently)
    """
    if tenant_config:
        sched = tenant_config.get("schedule_config") or {}
        smtp = sched.get("smtp")
        if smtp and smtp.get("host"):
            return SmtpConfig(
                host=smtp["host"],
                port=int(smtp.get("port", 587)),
                user=smtp.get("user", ""),
                password=smtp.get("password", ""),
                from_email=smtp.get("from_email", smtp.get("user", "")),
                from_name=smtp.get("from_name", ""),
                use_tls=bool(smtp.get("use_tls", True)),
            )

    host = os.getenv("SMTP_HOST", "").strip()
    if host:
        return SmtpConfig(
            host=host,
            port=int(os.getenv("SMTP_PORT", "587")),
            user=os.getenv("SMTP_USER", ""),
            password=os.getenv("SMTP_PASSWORD", ""),
            from_email=os.getenv("SMTP_FROM_EMAIL", os.getenv("SMTP_USER", "")),
            from_name=os.getenv("SMTP_FROM_NAME", "Cogno"),
            use_tls=os.getenv("SMTP_USE_TLS", "true").lower() in ("true", "1", "yes"),
            tls_ca_file=os.getenv("SMTP_TLS_CA_FILE", ""),
            tls_verify=os.getenv("SMTP_TLS_VERIFY", "true").lower() in ("true", "1", "yes"),
        )

    return None


def _clean_recipients(to_list: List[str]) -> List[str]:
    return [e.strip() for e in to_list if e and e.strip()]


async def send_plain_email(
    smtp_config: SmtpConfig,
    to_list: List[str],
    subject: str,
    body_text: str,
) -> SendResult:
    """Send a plain-text email. Never raises — returns a ``SendResult``."""
    recipients = _clean_recipients(to_list)
    if not recipients:
        return SendResult(sent=False, error="no_valid_recipients")
    try:
        return await asyncio.to_thread(
            _send_smtp, smtp_config, recipients, subject, body_text, None, "invite.ics"
        )
    except Exception as exc:  # noqa: BLE001 — contract: senders never raise
        logger.warning("stage=email event=send_failed kind=plain error=%s", exc)
        return SendResult(sent=False, error=str(exc))


async def send_email_with_ics(
    smtp_config: SmtpConfig,
    to_list: List[str],
    subject: str,
    body_text: str,
    ics_content: str,
    ics_filename: str = "invite.ics",
) -> SendResult:
    """Send an email with an iCalendar (.ics) attachment. Never raises."""
    recipients = _clean_recipients(to_list)
    if not recipients:
        return SendResult(sent=False, error="no_valid_recipients")
    try:
        return await asyncio.to_thread(
            _send_smtp, smtp_config, recipients, subject, body_text, ics_content, ics_filename
        )
    except Exception as exc:  # noqa: BLE001 — contract: senders never raise
        logger.warning("stage=email event=send_failed kind=ics error=%s", exc)
        return SendResult(sent=False, error=str(exc))


def _from_header(smtp_config: SmtpConfig) -> tuple[str, str]:
    from_name = smtp_config.get("from_name", "")
    from_email = smtp_config.get("from_email", smtp_config.get("user", ""))
    header = f'"{from_name}" <{from_email}>' if from_name else from_email
    return from_email, header


def _tls_context(smtp_config: SmtpConfig) -> ssl.SSLContext:
    """The STARTTLS context — VERIFYING by default.

    Bare ``starttls()`` builds an ``ssl._create_stdlib_context()``: check_hostname=False,
    CERT_NONE on Python ≤3.12. That is encryption an active MITM can transparently terminate,
    on a session carrying the SMTP password and every OTP we mail — so the default here
    verifies the chain and the hostname.

    Two escape hatches for relays a public CA cannot vouch for, in order of preference:
    ``tls_ca_file`` (trust a private CA — still verified), and ``tls_verify=False`` (encrypt
    without verifying). The latter is a last resort and says so in the log; it is still better
    than the alternative operators otherwise reach for, ``use_tls=false``, which drops
    encryption altogether."""
    ca_file = (smtp_config.get("tls_ca_file", "") or "").strip()
    if ca_file:
        return ssl.create_default_context(cafile=ca_file)
    if not smtp_config.get("tls_verify", True):
        logger.warning("stage=email event=tls_verification_disabled host=%s — the SMTP session "
                       "is encrypted but unauthenticated; prefer tls_ca_file",
                       smtp_config.get("host", "?"))
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return ssl.create_default_context()


def _connect(smtp_config: SmtpConfig) -> smtplib.SMTP:
    host = smtp_config["host"]
    port = smtp_config.get("port", 587)
    server = smtplib.SMTP(host, port, timeout=30)
    server.ehlo()
    if smtp_config.get("use_tls", True):
        server.starttls(context=_tls_context(smtp_config))
        server.ehlo()
    user = smtp_config.get("user", "")
    password = smtp_config.get("password", "")
    if user and password:
        server.login(user, password)
    return server


def _send_smtp(
    smtp_config: SmtpConfig,
    recipients: List[str],
    subject: str,
    body_text: str,
    ics_content: Optional[str],
    ics_filename: str,
) -> SendResult:
    """Blocking SMTP send — runs inside ``asyncio.to_thread``."""
    from_email, from_header = _from_header(smtp_config)

    if ics_content is None:
        msg: MIMEText | MIMEMultipart = MIMEText(body_text, "plain", "utf-8")
    else:
        msg = MIMEMultipart("mixed")
        msg.attach(MIMEText(body_text, "plain", "utf-8"))
        method_val = "CANCEL" if "METHOD:CANCEL" in ics_content else "REQUEST"
        ics_part = MIMEBase("text", "calendar", method=method_val, name=ics_filename, charset="UTF-8")
        ics_part.set_payload(ics_content.encode("utf-8"))
        encoders.encode_base64(ics_part)
        ics_part.add_header("Content-Disposition", "attachment", filename=ics_filename)
        msg.attach(ics_part)

    msg["From"] = str(Header(from_header, "utf-8"))
    msg["To"] = str(Header(", ".join(recipients), "utf-8"))
    msg["Subject"] = str(Header(subject, "utf-8"))

    server = _connect(smtp_config)
    try:
        server.sendmail(from_email, recipients, msg.as_bytes())
    finally:
        server.quit()

    logger.info("stage=email event=sent recipients=%d kind=%s",
                len(recipients), "plain" if ics_content is None else "ics")
    return SendResult(sent=True, recipients=recipients)
