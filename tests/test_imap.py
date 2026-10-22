"""The mailbox against the in-process IMAP server: no network, no GTK."""

import os
from pathlib import Path
import sys
import tempfile
import unittest

HERE = Path(__file__).resolve().parent
sys.path[0:0] = [str(HERE.parent), str(HERE)]

from fakeimap import FakeServer, letter  # noqa: E402
from haltia_mail import imap  # noqa: E402
from haltia_mail.imap import INBOX, SENT, TRASH, LoginFailed, Mailbox  # noqa: E402

ADDRESS, PASSWORD = "maria@ya.ru", "app-password"


class MailboxTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.server = FakeServer(ADDRESS, PASSWORD)

    def tearDown(self):
        self.tmp.cleanup()

    def box(self, server=None, password=PASSWORD):
        server = server or self.server
        box = Mailbox(ADDRESS, password, ("imap.yandex.ru", 993), self.tmp.name,
                      connect=server.connect)
        self.addCleanup(box.close)
        return box

    def fill(self, count):
        for i in range(count):
            self.server.add("INBOX", letter(subject="Письмо %d" % i), seen=i % 2 == 0)

    def test_wrong_password(self):
        with self.assertRaises(LoginFailed):
            self.box(password="wrong").check()

    def test_special_use_folders(self):
        self.assertEqual(self.box().check(), {INBOX: "INBOX", SENT: "Sent", TRASH: "Trash"})

    def test_usual_names_without_special_use(self):
        # «Отправленные» and «Корзина» in modified UTF-7, as Russian servers name them.
        server = FakeServer(ADDRESS, PASSWORD, special_use=False,
                            names=("&BB4EQgQ,BEAEMAQyBDsENQQ9BD0ESwQ1-", "&BBoEPgRABDcEOAQ9BDA-"))
        folders = self.box(server).check()
        self.assertEqual(imap.decode_utf7(folders[SENT]), "Отправленные")
        self.assertEqual(imap.decode_utf7(folders[TRASH]), "Корзина")

    def test_pages_newest_first(self):
        self.fill(120)
        box = self.box()
        lines, total = box.page(INBOX, 0, 50)
        self.assertEqual(total, 120)
        self.assertEqual(len(lines), 50)
        self.assertEqual(lines[0].subject, "Письмо 119")
        self.assertEqual(lines[0].sender, "Иван Петров")
        self.assertTrue(lines[0].unread)
        self.assertFalse(lines[1].unread)
        self.assertEqual(lines[0].when.utcoffset().total_seconds(), 3 * 3600)
        last, _total = box.page(INBOX, 100, 50)
        self.assertEqual([l.subject for l in last][-1], "Письмо 0")
        self.assertEqual(len(last), 20)

    def test_flags_after_the_literal(self):
        server = FakeServer(ADDRESS, PASSWORD, flags_last=True)
        server.add("INBOX", letter(), seen=True)
        server.add("INBOX", letter(), seen=False)
        lines, _total = self.box(server).page(INBOX, 0, 50)
        self.assertEqual([l.unread for l in lines], [True, False])

    def test_cache_keeps_headers(self):
        self.fill(10)
        self.box().page(INBOX, 0, 50)
        self.server.commands.clear()
        box = self.box()
        self.assertEqual(len(box.cached(INBOX, 50)), 10)
        lines, _total = box.page(INBOX, 0, 50)
        self.assertEqual(len(lines), 10)
        fetches = [c for c in self.server.commands if "FETCH" in c]
        self.assertTrue(fetches)
        self.assertFalse([c for c in fetches if "HEADER.FIELDS" in c])
        self.assertTrue(os.stat(os.path.join(self.tmp.name, ADDRESS, "inbox.json")).st_mode
                        & 0o077 == 0)

    def test_cache_forgets_on_new_uidvalidity(self):
        self.fill(3)
        self.box().page(INBOX, 0, 50)
        self.server.validity += 1
        self.server.folders["INBOX"].letters.clear()
        self.server.add("INBOX", letter(subject="Новое"))
        lines, total = self.box().page(INBOX, 0, 50)
        self.assertEqual((total, [l.subject for l in lines]), (1, ["Новое"]))

    def test_read_marks_seen(self):
        uid = self.server.add("INBOX", letter(body="Здравствуйте!"))
        box = self.box()
        box.page(INBOX, 0, 50)
        got = box.read(INBOX, uid)
        self.assertEqual(got.text, "Здравствуйте!")
        self.assertIn("\\Seen", self.server.folders["INBOX"].letters[0][1])
        lines, _total = box.page(INBOX, 0, 50)
        self.assertFalse(lines[0].unread)

    def test_attachments(self):
        uid = self.server.add("INBOX", letter(attachments=[
            ("счёт.pdf", "application/pdf", b"%PDF-1.4 fake"),
            ("../../.bashrc", "text/plain", b"rm -rf /")]))
        got = self.box().read(INBOX, uid)
        self.assertEqual([(a.name, a.mime, a.data) for a in got.attachments],
                         [("счёт.pdf", "application/pdf", b"%PDF-1.4 fake"),
                          ("bashrc", "text/plain", b"rm -rf /")])

    def test_trash_with_move(self):
        self.fill(3)
        box = self.box()
        lines, _total = box.page(INBOX, 0, 50)
        box.trash(INBOX, lines[0].uid)
        self.assertEqual(len(self.server.folders["INBOX"].letters), 2)
        self.assertEqual(len(self.server.folders["Trash"].letters), 1)
        self.assertEqual(box.page(INBOX, 0, 50)[1], 2)
        self.assertEqual(box.page(TRASH, 0, 50)[0][0].subject, "Письмо 2")

    def test_trash_without_move(self):
        server = FakeServer(ADDRESS, PASSWORD, move=False)
        uid = server.add("INBOX", letter())
        box = self.box(server)
        box.trash(INBOX, uid)
        self.assertEqual(len(server.folders["INBOX"].letters), 0)
        self.assertEqual(len(server.folders["Trash"].letters), 1)

    def test_save_sent(self):
        raw = b"Message-ID: <1.2@ya.ru>\r\n" + letter(subject="Отчёт")
        box = self.box()
        self.assertTrue(box.save_sent(raw, "<1.2@ya.ru>"))
        sent = self.server.folders["Sent"].letters
        self.assertEqual(len(sent), 1)
        self.assertIn("\\Seen", sent[0][1])
        self.assertEqual(box.page(SENT, 0, 50)[0][0].subject, "Отчёт")

    def test_save_sent_kept_by_the_server(self):
        # Gmail and others put what went through their SMTP into Sent themselves.
        raw = b"Message-ID: <1.2@ya.ru>\r\n" + letter(subject="Отчёт")
        self.server.add("Sent", raw, seen=True)
        self.assertTrue(self.box().save_sent(raw, "<1.2@ya.ru>"))
        self.assertEqual(len(self.server.folders["Sent"].letters), 1)
        self.assertFalse([c for c in self.server.commands if c.startswith("APPEND")])

    def test_reconnects_after_the_server_closed(self):
        self.fill(2)
        box = self.box()
        box.page(INBOX, 0, 50)
        self.server.break_connections()
        lines, total = box.page(INBOX, 0, 50)
        self.assertEqual(total, 2)


class ParseTests(unittest.TestCase):
    def test_list_lines(self):
        listed = imap.parse_list([
            b'(\\HasNoChildren \\Sent) "/" "Sent Items"',
            b'(\\Noselect \\HasChildren) "/" "[Gmail]"',
            b'(\\HasNoChildren \\Trash) "/" "[Gmail]/Trash"',
            b'(\\HasNoChildren) "." INBOX',
            (b'(\\HasNoChildren \\Sent) "/" {4}', b"Sent")])
        self.assertEqual([n for _f, n in listed],
                         ["Sent Items", "[Gmail]", "[Gmail]/Trash", "INBOX", "Sent"])
        self.assertEqual(imap.folders_of(listed),
                         {INBOX: "INBOX", SENT: "Sent Items", TRASH: "[Gmail]/Trash"})

    def test_quote(self):
        self.assertEqual(imap.quote('a "b" \\c'), '"a \\"b\\" \\\\c"')


if __name__ == "__main__":
    unittest.main()
