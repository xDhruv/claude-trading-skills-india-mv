"""Gmail over SMTP with an app password (free). The only recipient is the address in EMAIL_TO."""
import logging
import smtplib
import ssl
from email.message import EmailMessage
from email.utils import formatdate, make_msgid


class Mailer:
    def __init__(self, cfg):
        self.cfg = cfg
        self.sent = 0

    def send(self, subject: str, body: str, reply_to_msgid: str | None = None) -> str | None:
        """Send one plain-text email to the configured address. Returns its Message-ID, or None if not sent."""
        cfg = self.cfg
        msg = EmailMessage()
        msg["From"] = cfg.smtp_user or "nse-agent@localhost"
        msg["To"] = cfg.email_to
        msg["Subject"] = subject
        msg["Date"] = formatdate(localtime=True)
        msgid = make_msgid(domain="nse-agent.local")
        msg["Message-ID"] = msgid
        if reply_to_msgid:
            msg["In-Reply-To"] = reply_to_msgid
            msg["References"] = reply_to_msgid
        msg.set_content(body)
        if cfg.dry_run or not cfg.can_email():
            print(f"\n--- EMAIL ({'dry run' if cfg.dry_run else 'not configured'}) to {cfg.email_to or '?'} ---\n"
                  f"Subject: {subject}\n\n{body}\n--- end ---")
            return msgid if cfg.dry_run else None
        try:
            with smtplib.SMTP_SSL(cfg.smtp_host, cfg.smtp_port, context=ssl.create_default_context(),
                                  timeout=30) as s:
                s.login(cfg.smtp_user, cfg.smtp_password)
                s.send_message(msg)
        except (smtplib.SMTPException, OSError) as exc:
            logging.getLogger("nse_agent").error("email failed: %s", exc)
            return None
        self.sent += 1
        return msgid
