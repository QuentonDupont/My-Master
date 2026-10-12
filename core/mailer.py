"""Send an email. Stdlib only, same convention as the rest of the repo.

Two ways to authenticate to Gmail's SMTP, tried in this order:

1. **OAuth2** (preferred — does not need 2-Step Verification on the account).
   Run `tools/gmail_oauth_setup.py` once, interactively; it mints a refresh
   token and writes it to .env. This module only ever exchanges that refresh
   token for a short-lived access token (core/gmail_oauth.py) and uses it as
   SMTP AUTH XOAUTH2.

       GMAIL_OAUTH_CLIENT_ID=...
       GMAIL_OAUTH_CLIENT_SECRET=...
       GMAIL_OAUTH_REFRESH_TOKEN=...

2. **App Password** (needs 2-Step Verification turned on — skip this path if
   that's not wanted). myaccount.google.com/apppasswords.

       REPORT_EMAIL_APP_PASSWORD=xxxx xxxx xxxx xxxx

Either way:

    REPORT_EMAIL_FROM=you@gmail.com
    REPORT_EMAIL_TO=you@gmail.com    (defaults to REPORT_EMAIL_FROM)

Never logs a token or password: `config.env()` adds anything with PASSWORD/
TOKEN/KEY/SECRET in its name to the redactor automatically.
"""
from __future__ import annotations

import base64
import smtplib
from email.mime.text import MIMEText

from core import config, gmail_oauth, log

LOG = log.get("mailer")

SMTP_HOST = "smtp.gmail.com"
SMTP_PORT = 465


class MailerNotConfigured(RuntimeError):
    pass


def _oauth_configured() -> bool:
    return bool(config.env("GMAIL_OAUTH_CLIENT_ID")
               and config.env("GMAIL_OAUTH_CLIENT_SECRET")
               and config.env("GMAIL_OAUTH_REFRESH_TOKEN"))


def _app_password_configured() -> bool:
    return bool(config.env("REPORT_EMAIL_APP_PASSWORD"))


def configured() -> bool:
    return bool(config.env("REPORT_EMAIL_FROM")) and (
        _oauth_configured() or _app_password_configured())


def _auth_xoauth2(smtp: smtplib.SMTP, sender: str) -> None:
    token = gmail_oauth.refresh_access_token(
        config.env("GMAIL_OAUTH_CLIENT_ID"),
        config.env("GMAIL_OAUTH_CLIENT_SECRET"),
        config.env("GMAIL_OAUTH_REFRESH_TOKEN"),
    )
    raw = gmail_oauth.xoauth2_string(sender, token)
    b64 = base64.b64encode(raw.encode("ascii")).decode("ascii")
    code, resp = smtp.docmd("AUTH", "XOAUTH2 " + b64)
    if code != 235:
        raise MailerNotConfigured(
            f"Gmail rejected XOAUTH2 (code {code}): {resp!r}. The refresh "
            f"token may have been revoked — re-run tools/gmail_oauth_setup.py.")


def send(subject: str, body: str, *, to: str = "", dry_run: bool = True) -> dict:
    """Send a plain-text email. `dry_run=True` (the default) sends nothing and
    returns what would have gone out — the repo-wide convention: writing
    anything requires an explicit opt-in from the caller."""
    sender = config.env("REPORT_EMAIL_FROM")
    recipient = to or config.env("REPORT_EMAIL_TO") or sender
    use_oauth = _oauth_configured()

    if dry_run:
        LOG.info("mailer.dry_run", to=recipient, subject=subject, chars=len(body),
                 auth="oauth" if use_oauth else "app_password")
        return {"dry_run": True, "to": recipient, "subject": subject}

    if not sender:
        raise MailerNotConfigured("REPORT_EMAIL_FROM is not set in .env.")
    if not (use_oauth or _app_password_configured()):
        raise MailerNotConfigured(
            "No Gmail auth configured. Preferred: run "
            "`python3 tools/gmail_oauth_setup.py` once (no 2-Step Verification "
            "needed). Alternative: set REPORT_EMAIL_APP_PASSWORD, which does "
            "need 2-Step Verification turned on first.")

    msg = MIMEText(body, "plain", "utf-8")
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = recipient

    with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, timeout=30) as smtp:
        if use_oauth:
            _auth_xoauth2(smtp, sender)
        else:
            smtp.login(sender, config.env("REPORT_EMAIL_APP_PASSWORD"))
        smtp.sendmail(sender, [recipient], msg.as_string())

    LOG.info("mailer.sent", to=recipient, subject=subject, chars=len(body),
             auth="oauth" if use_oauth else "app_password")
    return {"dry_run": False, "to": recipient, "subject": subject}
