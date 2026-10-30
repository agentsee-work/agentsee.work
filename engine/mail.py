"""
Mail from the engine: the sign-in code, the invitation, and later the
booking confirmation and the signed copies (AGE-107). Plain text, a short
signature, no tracking, no images.

It is submitted to Stalwart on the box over port 465 as the `office`
account, and Stalwart relays it through SMTP2GO, so SPF and DKIM are the
mail server's business, not this module's. The account is the 1Password
item "Stalwart podcast submission", which engine.env resolves into
MAIL_USER and MAIL_PASSWORD; the host, the port and the addresses are in
config.json, none of them secret.

A failure to send is an exception with a sentence in it, for a screen to
show; nothing here is allowed to fail quietly. The SMTP class is a module
attribute so the tests can put a fake in its place.
"""

import email.message
import email.utils
import os
import smtplib

import ledger

NEEDS = "MAIL_USER / MAIL_PASSWORD"
SMTP = smtplib.SMTP_SSL
TIMEOUT = 30
SIGNATURE = "\n\n— AgentSee\nhttps://agentsee.work\nA reply reaches James and Abrar."


class MailError(Exception):
    pass


def ready(env=None):
    env = os.environ if env is None else env
    return bool(env.get("MAIL_USER") and env.get("MAIL_PASSWORD"))


def settings():
    s = {
        "host": "mail.agentsee.work",
        "port": 465,
        "from": "AgentSee <office@agentsee.work>",
        "reply_to": "hello@agentsee.work",
        "copy_to": "hello@agentsee.work",
    }
    s.update(ledger.CONFIG.get("mail") or {})
    return s


def send(to, subject, text, env=None, attachments=(), copy=False):
    """One message, plain text with the signature, and any attachments as
    (filename, bytes, "type/subtype"). `copy` sends it to the hosts' alias
    as well. Returns the Message-ID; raises MailError with the reason."""
    env = os.environ if env is None else env
    if not ready(env):
        raise MailError(f"{NEEDS} not set: run under `op run --env-file=engine/engine.env`")
    cfg = settings()
    m = email.message.EmailMessage()
    m["From"] = cfg["from"]
    m["To"] = to
    if copy and cfg.get("copy_to"):
        m["Cc"] = cfg["copy_to"]
    m["Reply-To"] = cfg["reply_to"]
    m["Subject"] = subject
    m["Date"] = email.utils.formatdate(localtime=True)
    m["Message-ID"] = email.utils.make_msgid(domain=email.utils.parseaddr(cfg["from"])[1].split("@")[-1])
    m["Auto-Submitted"] = "auto-generated"
    m.set_content(text.rstrip() + SIGNATURE + "\n")
    for name, data, ctype in attachments:
        maintype, _, subtype = ctype.partition("/")
        m.add_attachment(data, maintype=maintype, subtype=subtype or "octet-stream", filename=name)
    try:
        with SMTP(cfg["host"], cfg["port"], timeout=TIMEOUT) as s:
            s.login(env["MAIL_USER"], env["MAIL_PASSWORD"])
            s.send_message(m)
    except smtplib.SMTPAuthenticationError:
        raise MailError(f"{cfg['host']} refused the office account's password") from None
    except (smtplib.SMTPException, OSError) as e:
        raise MailError(f"{cfg['host']}:{cfg['port']}: {type(e).__name__}: {str(e)[:200]}") from None
    return m["Message-ID"]


def record(c, who, to, subject, msgid=None, error=None, guest=None):
    """What went out, or did not, in the ledger's record."""
    detail = {"to": to, "subject": subject}
    if error:
        detail["error"] = str(error)[:300]
    else:
        detail["id"] = msgid
    ledger.event(c, "mail:failed" if error else "mail:sent", who, detail=detail, guest=guest)


def last_failure(c):
    return c.execute("SELECT at, detail FROM event WHERE what='mail:failed' ORDER BY at DESC LIMIT 1").fetchone()
