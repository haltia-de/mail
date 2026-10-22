"""«Исходящие»: letters kept on disk and sent to the in-process SMTP server."""

import os
from pathlib import Path
import stat
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path[0:0] = [str(HERE.parent), str(HERE)]

from fakesmtp import FakeSMTP  # noqa: E402
from haltia_mail import letter, outbox  # noqa: E402
from haltia_mail.outbox import Outbox  # noqa: E402

ADDRESS, PASSWORD = "maria@ya.ru", "app-password"
SERVER = ("smtp.yandex.ru", 465)


def offline(host=None, port=None):
    raise OSError("network is unreachable")


class OutboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.box = Outbox(os.path.join(self.tmp.name, "outbox"))
        self.server = FakeSMTP(ADDRESS, PASSWORD, refuse={"nobody@ya.ru"})

    def file(self, name, data):
        path = os.path.join(self.tmp.name, name)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def flush(self, connect=None, password=PASSWORD, keep=None):
        return self.box.flush(ADDRESS, password, SERVER, keep=keep,
                              connect=connect or self.server.connect)

    def test_waits_offline_then_goes(self):
        path = self.file("report.tar.gz", b"\x1f\x8b fake")
        name = self.box.put("support@haltia.org", "Отчёт", "Что случилось", [path])
        os.remove(path)   # the program that gave it deletes its own
        self.assertEqual(self.flush(offline), [])
        self.assertEqual(self.box.names(), [name])
        kept = []
        self.assertEqual(self.flush(keep=lambda raw, mid: kept.append(mid)), [name])
        self.assertEqual(self.box.names(), [])
        sender, to, data = self.server.sent[0]
        self.assertEqual((sender, to), (ADDRESS, ["support@haltia.org"]))
        got = letter.read(data)
        self.assertEqual((got.subject, got.text), ("Отчёт", "Что случилось"))
        self.assertEqual([(a.name, a.data) for a in got.attachments],
                         [("report.tar.gz", b"\x1f\x8b fake")])
        self.assertEqual(len(kept), 1)

    def test_private_files(self):
        name = self.box.put("ivan@ya.ru", "Тема", "Текст", [self.file("a.txt", b"a")])
        where = os.path.join(self.box.folder, name)
        self.assertEqual(stat.S_IMODE(os.stat(self.box.folder).st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(os.stat(where).st_mode), 0o700)
        for file_name in os.listdir(where):
            self.assertEqual(stat.S_IMODE(os.stat(os.path.join(where, file_name)).st_mode),
                             0o600)

    def test_oldest_first(self):
        first = self.box.put("ivan@ya.ru", "Первое", "")
        second = self.box.put("ivan@ya.ru", "Второе", "")
        self.assertEqual(self.box.names(), [first, second])
        self.flush()
        self.assertEqual([letter.read(d).subject for _s, _t, d in self.server.sent],
                         ["Первое", "Второе"])

    def test_wrong_letters(self):
        with self.assertRaises(ValueError):
            self.box.put("ivan", "Тема", "Текст")
        with self.assertRaises(ValueError):
            self.box.put("", "Тема", "Текст")
        with self.assertRaises(ValueError):
            self.box.put("ivan@ya.ru", "Тема", "Текст", ["relative.txt"])
        with self.assertRaises(ValueError):
            self.box.put("ivan@ya.ru", "Тема", "Текст", [os.path.join(self.tmp.name, "none")])
        with self.assertRaises(ValueError):
            self.box.put("ivan@ya.ru", "Тема", "Текст", [self.tmp.name])
        big = self.file("big", b"")
        os.truncate(big, outbox.LIMIT + 1)
        with self.assertRaises(ValueError):
            self.box.put("ivan@ya.ru", "Тема", "Текст", [big])
        self.assertEqual(self.box.names(), [])

    def test_wrong_password_keeps(self):
        name = self.box.put("ivan@ya.ru", "Тема", "Текст")
        self.assertEqual(self.flush(password="wrong"), [])
        self.assertEqual(self.box.names(), [name])

    def test_refused_waits_then_goes(self):
        name = self.box.put("nobody@ya.ru", "Тема", "Текст")
        self.assertEqual(self.flush(), [])
        self.assertEqual(self.box.names(), [name])
        later = time.time() + outbox.GIVE_UP + 1
        with mock.patch("haltia_mail.outbox.time.time", return_value=later):
            self.assertEqual(self.flush(), [])
        self.assertEqual(self.box.names(), [])

    def test_unfinished_and_clear(self):
        self.box.put("ivan@ya.ru", "Тема", "Текст")
        os.makedirs(os.path.join(self.box.folder, "half"))
        self.assertEqual(len(self.box.names()), 1)
        self.box.clear()
        self.assertEqual(self.box.names(), [])

    def test_send_answers_queued_at_once(self):
        """Send answers queued (or not-configured) without waiting for SMTP."""
        self.assertEqual(
            outbox.accept(self.box, False, "ivan@ya.ru", "Тема", "Текст"),
            "not-configured")
        self.assertEqual(self.box.names(), [])

        def smtp_must_wait(*_a, **_k):
            raise AssertionError("Send must not wait for SMTP")

        with mock.patch("haltia_mail.outbox.smtp.send", smtp_must_wait):
            self.assertEqual(
                outbox.accept(self.box, True, "ivan@ya.ru", "Тема", "Текст"),
                "queued")
        self.assertEqual(len(self.box.names()), 1)
        self.assertEqual(self.server.sent, [])

    def test_clear_during_flush_does_not_send(self):
        """«Выйти» drops waiting letters; a flush already running must not send them."""
        self.box.put("ivan@ya.ru", "Тема", "Текст")
        orig_load = Outbox._load
        barrier = threading.Event()
        proceed = threading.Event()

        def paused(obj, name):
            result = orig_load(obj, name)
            barrier.set()
            if not proceed.wait(2):
                raise AssertionError("clear did not run")
            return result

        caught = []

        def run():
            try:
                caught.append(self.flush())
            except Exception as e:  # noqa: BLE001 - the test checks sent
                caught.append(e)

        with mock.patch.object(Outbox, "_load", paused):
            thread = threading.Thread(target=run)
            thread.start()
            self.assertTrue(barrier.wait(2))
            self.box.clear()
            proceed.set()
            thread.join(2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(self.server.sent, [])
        self.assertEqual(self.box.names(), [])
        self.assertEqual(caught, [[]])


if __name__ == "__main__":
    unittest.main()
