"""«Исходящие»: letters kept on disk until they can go out over SMTP.

Blocking and not thread-safe: the program calls it from its one worker thread.

    box = Outbox(folder)
    name = box.put(to, subject, text, [absolute path])   -> the files copied in;
        ValueError (an address, a path, too big), OSError
    box.names()                                          -> [name], oldest first
    box.flush(address, password, (host, port), keep=save_sent)
        -> [name sent]; stops at the first OSError or LoginFailed, the rest wait
    box.clear()
"""

import json
import os
import shutil
import tempfile
import time

from . import smtp
from .imap import LoginFailed
from .letter import clean_name

LIMIT = 20 * 1024 * 1024   # the files of one letter together
# A letter the server keeps refusing (a greylist says «later» the same way)
# goes after three days.
GIVE_UP = 3 * 24 * 3600
LETTER = "letter.json"


class Outbox:
    def __init__(self, folder):
        self.folder = folder

    def put(self, to, subject, text, paths=()):
        """A copy of the letter and its files: the program that gave them
        may delete its own at once."""
        if not smtp.recipients(to):
            raise ValueError(to)
        size = 0
        for path in paths:
            if not os.path.isabs(path) or not os.path.isfile(path):
                raise ValueError(path)
            size += os.path.getsize(path)
        if size > LIMIT:
            raise ValueError("more than %d MB" % (LIMIT // 1024 // 1024))
        os.makedirs(self.folder, mode=0o700, exist_ok=True)
        os.chmod(self.folder, 0o700)
        # Named by the time, so the names sort oldest first; mkdtemp makes it 0700.
        where = tempfile.mkdtemp(dir=self.folder, prefix="%020d-" % time.time_ns())
        try:
            files = []
            for number, path in enumerate(paths):
                name = clean_name(os.path.basename(path)) or "файл"
                with open(path, "rb") as src:
                    fd = os.open(os.path.join(where, str(number)),
                                 os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                    with os.fdopen(fd, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                files.append(name)
            # The description last: a letter without one is not finished.
            new = os.path.join(where, LETTER + ".new")
            fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump({"to": to, "subject": subject, "text": text, "files": files,
                           "queued": time.time()}, f, ensure_ascii=False)
            os.replace(new, os.path.join(where, LETTER))
        except BaseException:
            shutil.rmtree(where, ignore_errors=True)
            raise
        return os.path.basename(where)

    def names(self):
        try:
            found = os.listdir(self.folder)
        except OSError:
            return []
        return sorted(name for name in found
                      if os.path.isfile(os.path.join(self.folder, name, LETTER)))

    def _load(self, name):
        where = os.path.join(self.folder, name)
        with open(os.path.join(where, LETTER), encoding="utf-8") as f:
            meta = json.load(f)
        files = []
        for number, file_name in enumerate(meta["files"]):
            with open(os.path.join(where, str(number)), "rb") as f:
                files.append((file_name, f.read()))
        return meta, files

    def drop(self, name):
        shutil.rmtree(os.path.join(self.folder, name), ignore_errors=True)

    def clear(self):
        shutil.rmtree(self.folder, ignore_errors=True)

    def flush(self, address, password, server, keep=None, connect=None):
        """Sends the waiting letters, oldest first; keep(raw, message_id) gets
        each one sent (for «Отправленные»), its errors are ignored."""
        sent = []
        for name in self.names():
            try:
                meta, files = self._load(name)
                msg = smtp.make(address, meta["to"], meta["subject"], meta["text"], files)
            except (OSError, ValueError, KeyError, TypeError):
                self.drop(name)   # broken: it can never go
                continue
            try:
                raw = smtp.send(address, password, server, msg, connect=connect)
            except smtp.Refused as e:
                if e.raw is None:
                    if time.time() - meta.get("queued", 0) > GIVE_UP:
                        self.drop(name)
                    continue
                raw = e.raw   # the others got it
            except ValueError:
                self.drop(name)
                continue
            except (OSError, LoginFailed):
                break
            self.drop(name)
            sent.append(name)
            if keep is not None:
                try:
                    keep(raw, msg["Message-ID"])
                except Exception:  # noqa: BLE001 - the letter went all the same
                    pass
        return sent
