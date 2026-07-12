"""Outbound email.

Sends via SMTP when QL_SMTP_HOST is configured; otherwise logs the message
server-side. Crucially, verification tokens are delivered ONLY through this
channel (email or server log) and are never returned in an HTTP response in a
hosted deployment, so a remote caller cannot verify an inbox they don't control.
"""

from __future__ import annotations

import logging
import smtplib
import ssl
from email.message import EmailMessage

from ..config import get_settings

_log = logging.getLogger(__name__)


def send_email(to: str, subject: str, body: str) -> None:
    s = get_settings()
    if not s.smtp_host:
        # No transport configured (local/selfhost/dev): log for visibility.
        _log.info("email (no SMTP configured) to=%s subject=%s\n%s", to, subject, body)
        return
    msg = EmailMessage()
    msg["From"] = s.smtp_from or s.admin_email
    msg["To"] = to
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=15) as srv:
        if s.smtp_starttls:
            srv.starttls(context=ssl.create_default_context())
        if s.smtp_user:
            srv.login(s.smtp_user, s.smtp_password)
        srv.send_message(msg)
