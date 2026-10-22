"""The window's Wayland app_id is the program id."""

import ast
from pathlib import Path
import unittest

HERE = Path(__file__).resolve().parent
APP_ID = "io.github.haltia_de.Mail"


class PrgnameTests(unittest.TestCase):
    def test_prgname(self):
        tree = ast.parse((HERE.parent / "haltia-mail").read_text(encoding="utf-8"))
        calls = [n for n in ast.walk(tree)
                 if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "set_prgname"
                 and isinstance(n.func.value, ast.Name)
                 and n.func.value.id == "GLib"]
        self.assertEqual(len(calls), 1)
        arg = calls[0].args[0]
        assigned = {n.targets[0].id: n.value for n in tree.body
                    if isinstance(n, ast.Assign)
                    and len(n.targets) == 1
                    and isinstance(n.targets[0], ast.Name)}
        if isinstance(arg, ast.Name):
            self.assertEqual(ast.literal_eval(assigned[arg.id]), APP_ID)
        else:
            self.assertEqual(ast.literal_eval(arg), APP_ID)
        mail = next(n for n in tree.body
                    if isinstance(n, ast.ClassDef) and n.name == "Mail")
        self.assertLess(calls[0].lineno, mail.lineno)
        desktop = (HERE.parent / "data" / "io.github.haltia_de.Mail.desktop").read_text(
            encoding="utf-8")
        self.assertIn("StartupWMClass=%s" % APP_ID, desktop)


if __name__ == "__main__":
    unittest.main()
