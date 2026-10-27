"""A new letter and its sending over SMTP (smtplib; SSL on 465, else STARTTLS).

Blocking: the program calls it off the GTK thread.

    to = recipients("Иван <ivan@ya.ru>, olga@mail.ru")   -> [address]; ValueError
    msg = make(sender, to_text, subject, text, [(name, data)])
    raw = send(address, password, (host, port), msg)     -> the bytes sent;
        LoginFailed, Refused, OSError
"""

import mimetypes
from email import policy
from email.message import EmailMessage
from email.utils import formataddr, formatdate, getaddresses, make_msgid

from .imap import LoginFailed

TIMEOUT = 60
# Plain 7-bit bytes: the text goes as base64 or quoted-printable, so any
# server takes it, 8BITMIME or not.
POLICY = policy.SMTP.clone(cte_type="7bit")


class Refused(Exception):
    """The server did not take the letter, or not for everyone: the text is
    the server's reason or the addresses refused; raw is the bytes sent when
    the others got it."""
    raw = None


def recipients(text):
    """The addresses of «Кому» (commas or semicolons between them);
    ValueError(the wrong one) when one is not an address."""
    pairs = getaddresses([(text or "").replace(";", ",")])
    out = []
    for name, address in pairs:
        address = address.strip()
        if not address and not name:
            continue
        local, at, host = address.rpartition("@")
        if not at or not local or "." not in host or " " in address:
            raise ValueError(address or name)
        out.append(address)
    return out


def make(sender, to, subject, text, files=()):
    """The letter: plain text and the files (name, bytes) attached."""
    msg = EmailMessage(policy=POLICY)
    msg["From"] = sender
    msg["To"] = ", ".join(formataddr(pair) for pair in getaddresses([to.replace(";", ",")])
                          if pair[1])
    msg["Subject"] = subject
    msg["Date"] = formatdate(localtime=True)
    msg["Message-ID"] = make_msgid(domain=sender.rpartition("@")[2] or None)
    msg.set_content(text or "")
    for name, data in files:
        mime, packed = mimetypes.guess_type(name)
        if packed or not mime:   # report.tar.gz is not an application/x-tar
            mime = "application/octet-stream"
        main, _slash, sub = mime.partition("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg


def _connect(host, port):
    # smtplib and ssl in the worker that sends: not while the window starts.
    import smtplib
    import ssl

    context = ssl.create_default_context()
    if port == 465:
        return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
    conn = smtplib.SMTP(host, port, timeout=TIMEOUT)
    try:
        # Raises when the server has no STARTTLS: never send in the clear.
        conn.starttls(context=context)
    except Exception:
        conn.close()
        raise
    return conn


def send(address, password, server, msg, connect=None):
    """Sends msg to everyone in its To; the bytes sent (for «Отправленные»).

    connect(host, port) -> an smtplib.SMTP with TLS already on (the tests
    give their own)."""
    import smtplib

    raw = msg.as_bytes(policy=POLICY)
    to = recipients(msg["To"] or "")
    if not to:
        raise ValueError("")
    conn = (connect or _connect)(*server)
    try:
        try:
            conn.login(address, password)
        except smtplib.SMTPAuthenticationError as e:
            raise LoginFailed(str(e.smtp_code)) from None
        try:
            refused = conn.sendmail(address, to, raw)
        except smtplib.SMTPRecipientsRefused as e:
            raise Refused(", ".join(e.recipients)) from None
        except smtplib.SMTPResponseException as e:
            raise Refused(_reason(e)) from None
        if refused:   # the others got it
            error = Refused(", ".join(refused))
            error.raw = raw
            raise error
    finally:
        try:
            conn.quit()
        except Exception:
            conn.close()
    return raw


def _reason(e):
    message = e.smtp_error
    if isinstance(message, bytes):
        message = message.decode("utf-8", "replace")
    return "%s %s" % (e.smtp_code, " ".join(str(message).split()))
