"""Email notifier (disabled by default in settings.yaml).

To use with Gmail: enable 2-factor auth, create an App Password at
https://myaccount.google.com/apppasswords, then fill in the SMTP_* values
in .env. Any other SMTP provider works the same way.
"""
from __future__ import annotations

import logging
import os
import smtplib
from email.mime.text import MIMEText

log = logging.getLogger("dealfinder.email")


def send(digest_text: str) -> bool:
    host = os.environ.get("SMTP_HOST", "")
    user = os.environ.get("SMTP_USER", "")
    password = os.environ.get("SMTP_PASS", "")
    to_addr = os.environ.get("SMTP_TO", user)
    port = int(os.environ.get("SMTP_PORT", "465"))
    if not (host and user and password):
        log.warning("Email notify enabled but SMTP_* values not set in .env")
        return False

    msg = MIMEText(digest_text, "plain", "utf-8")
    msg["Subject"] = "Kap's Retro Rescue — deal digest"
    msg["From"] = user
    msg["To"] = to_addr

    try:
        with smtplib.SMTP_SSL(host, port, timeout=30) as server:
            server.login(user, password)
            server.sendmail(user, [to_addr], msg.as_string())
        return True
    except Exception as e:  # noqa: BLE001 — any mail failure just logs
        log.error("Email send failed: %s", e)
        return False
