"""A new letter sent to the in-process SMTP server: no network, no GTK."""

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path[0:0] = [str(HERE.parent), str(HERE)]

from fakesmtp import FakeSMTP  # noqa: E402
from haltia_mail import letter, smtp  # noqa: E402
from haltia_mail.imap import LoginFailed  # noqa: E402

ADDRESS, PASSWORD = "maria@ya.ru", "app-password"


class SendTests(unittest.TestCase):
    def setUp(self):
        self.server = FakeSMTP(ADDRESS, PASSWORD, refuse={"nobody@ya.ru"})

    def send(self, msg, password=PASSWORD):
        return smtp.send(ADDRESS, password, ("smtp.yandex.ru", 465), msg,
                         connect=self.server.connect)

    def test_letter_with_files(self):
        msg = smtp.make(ADDRESS, "Иван Петров <ivan@ya.ru>; olga@mail.ru", "Отчёт",
                        "Здравствуйте!\nОтчёт во вложении.",
                        [("отчёт.pdf", b"%PDF-1.4 fake"), ("журнал", b"\x00\x01")])
        raw = self.send(msg)
        sender, to, data = self.server.sent[0]
        self.assertEqual((sender, to), (ADDRESS, ["ivan@ya.ru", "olga@mail.ru"]))
        self.assertTrue(data.isascii())
        got = letter.read(data)
        self.assertEqual(got.subject, "Отчёт")
        self.assertEqual(got.sender, ADDRESS)
        self.assertEqual(got.to, "Иван Петров, olga@mail.ru")
        self.assertEqual(got.text, "Здравствуйте!\nОтчёт во вложении.")
        self.assertEqual([(a.name, a.mime, a.data) for a in got.attachments],
                         [("отчёт.pdf", "application/pdf", b"%PDF-1.4 fake"),
                          ("журнал", "application/octet-stream", b"\x00\x01")])
        self.assertEqual(letter.read(raw).subject, "Отчёт")
        self.assertTrue(msg["Message-ID"].endswith("@ya.ru>"))

    def test_wrong_password(self):
        with self.assertRaises(LoginFailed):
            self.send(smtp.make(ADDRESS, "ivan@ya.ru", "Тема", "Текст"), password="wrong")
        self.assertEqual(self.server.sent, [])

    def test_refused(self):
        with self.assertRaises(smtp.Refused) as caught:
            self.send(smtp.make(ADDRESS, "nobody@ya.ru", "Тема", "Текст"))
        self.assertIsNone(caught.exception.raw)
        self.assertEqual(self.server.sent, [])

    def test_partly_refused(self):
        with self.assertRaises(smtp.Refused) as caught:
            self.send(smtp.make(ADDRESS, "nobody@ya.ru, ivan@ya.ru", "Тема", "Текст"))
        self.assertEqual(str(caught.exception), "nobody@ya.ru")
        self.assertIsNotNone(caught.exception.raw)
        self.assertEqual(self.server.sent[0][1], ["ivan@ya.ru"])

    def test_recipients(self):
        self.assertEqual(smtp.recipients("Иван <ivan@ya.ru>; olga@mail.ru"),
                         ["ivan@ya.ru", "olga@mail.ru"])
        self.assertEqual(smtp.recipients("  "), [])
        with self.assertRaises(ValueError):
            smtp.recipients("ivan@ya.ru, olga")


class NameTests(unittest.TestCase):
    def test_clean_name(self):
        self.assertEqual(letter.clean_name("../../.bashrc"), "bashrc")
        self.assertEqual(letter.clean_name("C:\\Users\\отчёт.pdf"), "отчёт.pdf")
        self.assertEqual(letter.clean_name("..."), "")


if __name__ == "__main__":
    unittest.main()
