"""«Исходящие»: letters kept on disk until the SMTP server takes them.

Not thread-safe and blocking: the program calls it from its one worker thread.

    message = compose(sender, to, subject, body, [paths])  -> EmailMessage; ValueError
    box = Outbox(folder)
    box.put(message)                  -> the letter's name in the folder
    sender = Sender(address, password, (host, port))
    box.flush(sender.send)            -> ({name: SENT or REFUSED}, the error that stopped it)
    sender.close()
"""

from email import policy
from email.message import EmailMessage
from email.parser import BytesParser
from email.utils import formatdate, getaddresses, make_msgid
import mimetypes
import os
import shutil
import smtplib
import ssl
import time

from . import servers

SENT, REFUSED = "sent", "refused"
TIMEOUT = 30
# What the usual servers take in one letter, with base64 on top.
LIMIT = 20 * 1024 * 1024


def recipients(to):
    """The addresses in to («a@b.ru, Имя <c@d.ru>»); ValueError when there are none
    or one is not an address."""
    if not to or "\r" in to or "\n" in to:
        raise ValueError("no recipient")
    found = [address for _name, address in getaddresses([to])]
    for address in found:
        user, at, host = address.rpartition("@")
        if not at or not user or "." not in host:
            raise ValueError("not an address: %r" % address)
    if not found:
        raise ValueError("no recipient")
    return found


def compose(sender, to, subject, body, attachments=()):
    """The letter from sender to to; attachments are absolute paths of files,
    read now so the caller may delete them."""
    recipients(to)
    message = EmailMessage()
    message["From"] = sender
    message["To"] = to.strip()
    message["Subject"] = " ".join((subject or "").split())
    message["Date"] = formatdate(localtime=True)
    message["Message-ID"] = make_msgid(domain=servers.domain(sender) or None)
    message.set_content(body or "")
    total = 0
    for path in attachments:
        if not os.path.isabs(path):
            raise ValueError("not an absolute path: %s" % path)
        try:
            with open(path, "rb") as f:
                data = f.read(LIMIT - total + 1)
        except OSError as e:
            raise ValueError("cannot read %s: %s" % (path, e.strerror)) from None
        total += len(data)
        if total > LIMIT:
            raise ValueError("the attachments are larger than %d MB" % (LIMIT // 1024 // 1024))
        kind, encoding = mimetypes.guess_type(path)
        if kind is None or encoding is not None:
            kind = "application/octet-stream"
        maintype, subtype = kind.split("/", 1)
        message.add_attachment(data, maintype=maintype, subtype=subtype,
                               filename=os.path.basename(path))
    return message


def refused(e):
    """Whether the server refused this letter for good (a 5xx answer about the
    letter, not the account): sending it again will not help."""
    if isinstance(e, smtplib.SMTPRecipientsRefused):
        return bool(e.recipients) and all(code >= 500 for code, _text in e.recipients.values())
    if isinstance(e, (smtplib.SMTPSenderRefused, smtplib.SMTPDataError)):
        return e.smtp_code >= 500
    return False


class Outbox:
    """The letters as .eml files (0600) in folder (0700), the oldest first by
    name; a letter the server refused for good stays as .refused."""

    def __init__(self, folder):
        self.folder = folder

    def names(self):
        try:
            return sorted(n for n in os.listdir(self.folder) if n.endswith(".eml"))
        except OSError:
            return []

    def put(self, message):
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        name = "%020d-%s.eml" % (time.time_ns(), os.urandom(4).hex())
        path = os.path.join(self.folder, name)
        new = path + ".new"
        fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(message.as_bytes(policy=policy.SMTP))
        os.replace(new, path)
        return name

    def clear(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def flush(self, send):
        """send(message) for each letter, the oldest first; a sent letter is
        deleted. Stops at the first error that is not a refusal (no network,
        the password) and returns it with what was done."""
        done = {}
        for name in self.names():
            path = os.path.join(self.folder, name)
            try:
                with open(path, "rb") as f:
                    message = BytesParser(policy=policy.default).parse(f)
            except OSError:
                continue
            try:
                send(message)
            except Exception as e:  # noqa: BLE001 - kept for the next try
                if not refused(e):
                    return done, e
                os.replace(path, path[:-len(".eml")] + ".refused")
                done[name] = REFUSED
                continue
            os.remove(path)
            done[name] = SENT
        return done, None


class Sender:
    """One SMTP connection for a flush, opened on the first letter."""

    def __init__(self, address, password, server, connect=None):
        """server: (host, port); connect(host, port) -> an smtplib.SMTP (SSL on
        465, STARTTLS on the others; the tests give their own)."""
        self.address = address
        self.password = password
        self.server = tuple(server)
        self.connect = connect or self._ssl
        self.conn = None

    @staticmethod
    def _ssl(host, port):
        context = ssl.create_default_context()
        if port == 465:
            return smtplib.SMTP_SSL(host, port, timeout=TIMEOUT, context=context)
        conn = smtplib.SMTP(host, port, timeout=TIMEOUT)
        try:
            conn.starttls(context=context)
        except Exception:
            conn.close()
            raise
        return conn

    def send(self, message):
        if self.conn is None:
            conn = self.connect(*self.server)
            try:
                conn.login(self.address, self.password)
            except Exception:
                conn.close()
                raise
            self.conn = conn
        try:
            self.conn.send_message(message)
        except Exception as e:
            if not refused(e):
                self.close()
            raise

    def close(self):
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.quit()
            except Exception:
                conn.close()
