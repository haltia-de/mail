"""«Исходящие» and sending against a fake SMTP server: no network, no GTK."""

import os
from pathlib import Path
import smtplib
import stat
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path[0:0] = [str(HERE.parent), str(HERE)]

from haltia_mail import outbox  # noqa: E402
from haltia_mail.outbox import REFUSED, SENT, Outbox, Sender, compose  # noqa: E402

ADDRESS, PASSWORD = "maria@ya.ru", "app-password"


class FakeSMTP:
    """The server: who may sign in, which recipients it refuses, whether it is reachable."""

    def __init__(self):
        self.letters = []
        self.refuse = set()
        self.down = False
        self.logins = 0

    def connect(self, host, port):
        if self.down:
            raise ConnectionRefusedError(111, "Connection refused")
        return FakeConnection(self)


class FakeConnection:
    def __init__(self, server):
        self.server = server
        self.closed = False

    def login(self, address, password):
        self.server.logins += 1
        if (address, password) != (ADDRESS, PASSWORD):
            raise smtplib.SMTPAuthenticationError(535, b"Invalid user or password")

    def send_message(self, message):
        to = str(message["To"])
        if to in self.server.refuse:
            raise smtplib.SMTPRecipientsRefused({to: (550, b"No such user")})
        if self.server.down:
            raise smtplib.SMTPServerDisconnected("Connection unexpectedly closed")
        self.server.letters.append(message)

    def quit(self):
        self.closed = True

    def close(self):
        self.closed = True


class ComposeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def file(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def test_letter(self):
        log = self.file("report.txt", "журнал\n".encode())
        shot = self.file("screen.png", b"\x89PNG\r\n")
        message = compose(ADDRESS, "help@example.org", "Отчёт\nо сбое", "Текст", [log, shot])
        self.assertEqual(message["From"], ADDRESS)
        self.assertEqual(message["To"], "help@example.org")
        self.assertEqual(message["Subject"], "Отчёт о сбое")
        self.assertIn("@ya.ru>", message["Message-ID"])
        self.assertEqual(message.get_body().get_content().strip(), "Текст")
        files = list(message.iter_attachments())
        self.assertEqual([a.get_filename() for a in files], ["report.txt", "screen.png"])
        self.assertEqual(files[0].get_content_type(), "text/plain")
        self.assertEqual(files[1].get_content(), b"\x89PNG\r\n")

    def test_bad_arguments(self):
        for to in ("", "nobody", "a@b.ru\nBcc: x@y.ru", "a@b.ru, nobody"):
            with self.assertRaises(ValueError, msg=to):
                compose(ADDRESS, to, "s", "b")
        with self.assertRaises(ValueError):
            compose(ADDRESS, "a@b.ru", "s", "b", ["report.txt"])
        with self.assertRaises(ValueError):
            compose(ADDRESS, "a@b.ru", "s", "b", [os.path.join(self.tmp.name, "missing")])

    def test_too_big(self):
        big = self.file("big.bin", b"x" * (outbox.LIMIT + 1))
        with self.assertRaises(ValueError):
            compose(ADDRESS, "a@b.ru", "s", "b", [big])


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = os.path.join(self.tmp.name, "outbox")
        self.box = Outbox(self.folder)
        self.server = FakeSMTP()

    def put(self, to="help@example.org", subject="Отчёт"):
        return self.box.put(compose(ADDRESS, to, subject, "Текст"))

    def flush(self, password=PASSWORD):
        sender = Sender(ADDRESS, password, ("smtp.yandex.ru", 465), connect=self.server.connect)
        try:
            return self.box.flush(sender.send)
        finally:
            sender.close()

    def test_kept_private(self):
        name = self.put()
        self.assertEqual(stat.S_IMODE(os.stat(self.folder).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(os.path.join(self.folder, name)).st_mode), 0o600)

    def test_sent_oldest_first(self):
        first, second = self.put(subject="Первое"), self.put(subject="Второе")
        done, error = self.flush()
        self.assertIsNone(error)
        self.assertEqual(done, {first: SENT, second: SENT})
        self.assertEqual([m["Subject"] for m in self.server.letters], ["Первое", "Второе"])
        self.assertEqual(self.server.logins, 1)
        self.assertEqual(self.box.names(), [])

    def test_kept_without_network(self):
        name = self.put()
        self.server.down = True
        done, error = self.flush()
        self.assertEqual(done, {})
        self.assertIsInstance(error, OSError)
        self.assertEqual(self.box.names(), [name])
        self.server.down = False
        self.assertEqual(self.flush(), ({name: SENT}, None))
        self.assertEqual(self.server.letters[0]["Subject"], "Отчёт")

    def test_kept_with_a_wrong_password(self):
        name = self.put()
        done, error = self.flush(password="wrong")
        self.assertEqual(done, {})
        self.assertIsInstance(error, smtplib.SMTPAuthenticationError)
        self.assertNotIn("wrong", str(error))
        self.assertEqual(self.box.names(), [name])

    def test_refused_letter_put_aside(self):
        self.server.refuse.add("nobody@example.org")
        bad, good = self.put(to="nobody@example.org"), self.put()
        done, error = self.flush()
        self.assertIsNone(error)
        self.assertEqual(done, {bad: REFUSED, good: SENT})
        self.assertEqual(self.box.names(), [])
        self.assertTrue(os.path.exists(os.path.join(self.folder, bad[:-4] + ".refused")))

    def test_clear(self):
        self.put()
        self.box.clear()
        self.assertEqual(self.box.names(), [])
        self.assertFalse(os.path.exists(self.folder))


if __name__ == "__main__":
    unittest.main()
