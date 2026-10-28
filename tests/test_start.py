"""What the window's start leaves out and what a reload keeps; no display."""

import ast
from pathlib import Path
import subprocess
import sys
import unittest

from haltia_mail.letter import Summary

HERE = Path(__file__).resolve().parent


def method(name):
    """A method of Mail in haltia-mail, as a plain function."""
    tree = ast.parse((HERE.parent / "haltia-mail").read_text(encoding="utf-8"))
    mail = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "Mail")
    node = next(n for n in mail.body if isinstance(n, ast.FunctionDef) and n.name == name)
    namespace = {"Row": FakeRow}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "haltia-mail", "exec"), namespace)
    return namespace[name]


class FakeRow:
    built = 0

    def __init__(self, line):
        FakeRow.built += 1
        self.line = line

    def set_unread(self, unread):
        self.line = self.line._replace(unread=unread)


class FakeList:
    def __init__(self):
        self.children = []
        self.changes = 0

    def get_children(self):
        return list(self.children)

    def remove(self, row):
        self.children.remove(row)
        self.changes += 1

    def add(self, row):
        self.children.append(row)
        self.changes += 1

    def show_all(self):
        pass


def line(uid, subject="Тема", unread=False):
    return Summary(uid, "a@b.c", subject, None, unread)


class ReplaceRows(unittest.TestCase):
    def setUp(self):
        self.replace = method("replace_rows")
        self.app = type("App", (), {})()
        self.app.rows = FakeList()
        self.replace(self.app, [line(3), line(2), line(1)])
        FakeRow.built = 0
        self.app.rows.changes = 0

    def test_the_same_page_touches_nothing(self):
        self.replace(self.app, [line(3), line(2), line(1)])
        self.assertEqual((FakeRow.built, self.app.rows.changes), (0, 0))

    def test_a_read_mark_changes_in_place(self):
        before = self.app.rows.get_children()
        self.replace(self.app, [line(3, unread=True), line(2), line(1)])
        self.assertEqual(self.app.rows.get_children(), before)
        self.assertTrue(before[0].line.unread)
        self.assertEqual((FakeRow.built, self.app.rows.changes), (0, 0))

    def test_only_a_new_letter_is_built(self):
        before = self.app.rows.get_children()
        self.replace(self.app, [line(4), line(3), line(2)])
        rows = self.app.rows.get_children()
        self.assertEqual([r.line.uid for r in rows], [4, 3, 2])
        self.assertEqual(rows[1:], before[:2])
        self.assertEqual(FakeRow.built, 1)

    def test_a_changed_letter_is_built_anew(self):
        self.replace(self.app, [line(3, "Другая"), line(2), line(1)])
        self.assertEqual(self.app.rows.get_children()[0].line.subject, "Другая")
        self.assertEqual(FakeRow.built, 1)


class LazyImports(unittest.TestCase):
    def test_mail_modules_leave_ssl_to_the_worker(self):
        code = ("import sys; import haltia_mail.smtp, haltia_mail.imap, haltia_mail.outbox; "
                "print(sorted(m for m in ('ssl', 'imaplib', 'smtplib') if m in sys.modules))")
        out = subprocess.run([sys.executable, "-c", code], cwd=HERE.parent, check=True,
                             capture_output=True, text=True).stdout.strip()
        self.assertEqual(out, "[]")


if __name__ == "__main__":
    unittest.main()
