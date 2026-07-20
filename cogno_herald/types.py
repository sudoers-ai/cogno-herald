"""
cogno_herald.types — light typed shapes for SMTP config and send results.

Kept dict-compatible (``TypedDict``) so a host can hand a plain mapping loaded
from its own config (YAML/JSON/DB/.env) without constructing a model.
"""

from __future__ import annotations

from typing import List, TypedDict


class SmtpConfig(TypedDict, total=False):
    """SMTP connection details. ``host`` is the only hard-required key."""

    host: str
    port: int
    user: str
    password: str
    from_email: str
    from_name: str
    use_tls: bool
    tls_ca_file: str    # trust a private CA (internal relay) instead of the system store
    tls_verify: bool    # default True; False = encrypt without verifying (last resort)


class SendResult(TypedDict, total=False):
    """Result of a send attempt. ``sent`` is always present."""

    sent: bool
    recipients: List[str]
    error: str
