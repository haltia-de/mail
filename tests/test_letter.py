"""Letters and servers by domain, without GTK or network."""

from pathlib import Path
import sys
import unittest

HERE = Path(__file__).resolve().parent
sys.path[0:0] = [str(HERE.parent), str(HERE)]

from fakeimap import letter  # noqa: E402
from haltia_mail import servers  # noqa: E402
from haltia_mail.letter import html_to_text, read, summary  # noqa: E402


class HtmlTests(unittest.TestCase):
    def test_no_scripts_styles_or_pictures(self):
        text = html_to_text(
            "<html><head><title>T</title><style>p{color:red}</style></head><body>"
            "<script>alert(1)</script><p>Привет,&nbsp;мама!</p>"
            "<img src='https://tracker.example/pixel.gif'><p>Пока</p></body></html>")
        self.assertEqual(text, "Привет, мама!\n\nПока")

    def test_links_kept(self):
        text = html_to_text('<p>Смотрите <a href="https://example.org/x">здесь</a>.</p>'
                            '<p><a href="https://example.org/">https://example.org/</a></p>'
                            '<p><a href="javascript:alert(1)">кнопка</a></p>')
        self.assertEqual(text, "Смотрите здесь (https://example.org/x).\n\n"
                               "https://example.org/\n\nкнопка")

    def test_lines_and_lists(self):
        self.assertEqual(html_to_text("a<br>b<ul><li>один</li><li>два</li></ul>"),
                         "a\nb\n\n• один\n• два")

    def test_broken_html(self):
        self.assertEqual(html_to_text("<p>текст<div><b>жирный"), "текст\n\nжирный")


class ReadTests(unittest.TestCase):
    def test_plain_preferred(self):
        got = read(letter(body="Простой текст", html="<p>HTML</p>"))
        self.assertEqual(got.text, "Простой текст")
        self.assertEqual(got.subject, "Привет")
        self.assertEqual(got.to, "maria@ya.ru")

    def test_html_only(self):
        got = read(letter(body=None, html="<p>Только <b>HTML</b></p>"))
        self.assertEqual(got.text, "Только HTML")

    def test_encoded_headers(self):
        raw = (b"From: =?utf-8?B?0JjQstCw0L0=?= <ivan@ya.ru>\r\n"
               b"Subject: =?koi8-r?B?8NLJ18XU?=\r\n"
               b"Date: Sat, 17 Oct 2026 10:00:00 +0300\r\n\r\n")
        line = summary(5, raw, set())
        self.assertEqual((line.sender, line.subject, line.unread), ("Иван", "Привет", True))

    def test_no_subject_no_date(self):
        line = summary(1, b"From: ivan@ya.ru\r\n\r\n", {"\\Seen"})
        self.assertEqual((line.sender, line.subject, line.when, line.unread),
                         ("ivan@ya.ru", "(без темы)", None, False))


class ServerTests(unittest.TestCase):
    def test_known_domains(self):
        for address, host in (("a@yandex.ru", "imap.yandex.ru"), ("a@YA.RU", "imap.yandex.ru"),
                              ("a@bk.ru", "imap.mail.ru"), ("a@list.ru", "imap.mail.ru"),
                              ("a@inbox.ru", "imap.mail.ru"), ("a@gmail.com", "imap.gmail.com"),
                              ("a@rambler.ru", "imap.rambler.ru"),
                              ("a@hotmail.com", "outlook.office365.com"),
                              ("a@outlook.com", "outlook.office365.com")):
            self.assertEqual(servers.servers(address).imap, (host, 993), address)
            self.assertTrue(servers.servers(address).hint)

    def test_other_domain(self):
        self.assertIsNone(servers.servers("me@example.org"))
        self.assertEqual(servers.guess_field("me@example.org"), "imap.example.org")
        provider = servers.from_field("imap.example.org")
        self.assertEqual((provider.imap, provider.smtp),
                         (("imap.example.org", 993), ("smtp.example.org", 465)))
        self.assertEqual(servers.from_field("imaps://mail.example.org:1993").imap,
                         ("mail.example.org", 1993))
        self.assertIsNone(servers.from_field("не сервер"))


if __name__ == "__main__":
    unittest.main()
