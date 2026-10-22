"""The mailbox over IMAP (imaplib, SSL), with a cache of the list's headers.

Not thread-safe and blocking: the program calls it from one worker thread.

    box = Mailbox(address, password, (host, port), cache_dir)
    box.check()                       -> the folders found; LoginFailed, OSError
    box.page(INBOX, 0, 50)            -> ([Summary], how many letters in all)
    box.read(INBOX, uid)              -> Letter (and the letter is seen)
    box.trash(INBOX, uid)             -> moved to the folder «Корзина»
    box.save_sent(raw, message_id)    -> the sent letter in «Отправленные»
"""

import base64
from datetime import datetime, timezone
import imaplib
import json
import os
import re
import ssl
import time

from . import letter

INBOX, SENT, TRASH = "inbox", "sent", "trash"
NAMES = {INBOX: "Входящие", SENT: "Отправленные", TRASH: "Корзина"}
SPECIAL = {"\\sent": SENT, "\\trash": TRASH}
# For servers without special-use flags (RFC 6154): the usual names, lower case.
USUAL = {
    SENT: {"sent", "sent items", "sent messages", "sent mail", "[gmail]/sent mail",
           "inbox.sent", "inbox/sent", "отправленные", "inbox.отправленные"},
    TRASH: {"trash", "deleted", "deleted items", "deleted messages", "bin",
            "[gmail]/trash", "[gmail]/bin", "inbox.trash", "inbox/trash",
            "корзина", "удалённые", "удаленные", "inbox.корзина"},
}
TIMEOUT = 30
HEADERS = "BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)]"


class LoginFailed(Exception):
    """The server refused the address or the password."""


def decode_utf7(name):
    """A folder's name in modified UTF-7 (RFC 3501 5.1.3) as text."""
    out, i = [], 0
    while i < len(name):
        if name[i] == "&":
            end = name.find("-", i)
            if end < 0:
                return name
            chunk = name[i + 1:end]
            if not chunk:
                out.append("&")
            else:
                chunk = chunk.replace(",", "/")
                chunk += "=" * (-len(chunk) % 4)
                try:
                    out.append(base64.b64decode(chunk).decode("utf-16-be"))
                except (ValueError, UnicodeDecodeError):
                    return name
            i = end + 1
        else:
            out.append(name[i])
            i += 1
    return "".join(out)


def quote(name):
    return '"%s"' % name.replace("\\", "\\\\").replace('"', '\\"')


LIST_LINE = re.compile(rb'\((?P<flags>[^)]*)\)\s+(?P<delim>"(?:[^"\\]|\\.)*"|NIL)\s+(?P<name>.*)$',
                       re.IGNORECASE)


def parse_list(lines):
    """[(flags in lower case, name)] from the answer to LIST."""
    out = []
    for line in lines:
        if isinstance(line, tuple):   # a name sent as a literal
            meta, literal = line
            match = LIST_LINE.match(meta.rstrip() + b" X")
            name = literal.decode("utf-8", "replace") if match else None
        else:
            match = LIST_LINE.match(line or b"")
            name = match.group("name").decode("utf-8", "replace").strip() if match else None
            if name and name.startswith('"') and name.endswith('"') and len(name) > 1:
                name = re.sub(r'\\(.)', r'\1', name[1:-1])
        if match is None or name is None:
            continue
        flags = {f.lower() for f in match.group("flags").decode("ascii", "replace").split()}
        out.append((flags, name))
    return out


def folders_of(listed):
    """{INBOX: name, SENT: name, TRASH: name} from parse_list(); special-use
    flags first, the usual names when the server has none."""
    found = {INBOX: "INBOX"}
    for flags, name in listed:
        if "\\noselect" in flags or "\\nonexistent" in flags:
            continue
        for flag, role in SPECIAL.items():
            if flag in flags and role not in found:
                found[role] = name
    for role, usual in USUAL.items():
        if role in found:
            continue
        for flags, name in listed:
            if "\\noselect" not in flags and decode_utf7(name).lower() in usual:
                found[role] = name
                break
    return found


def parse_fetch(data):
    """[(meta bytes, literal bytes or None)] from the answer to FETCH: the
    literal is the one body asked for; the meta has UID, FLAGS and the rest."""
    out = []
    for item in data or ():
        if isinstance(item, tuple):
            out.append([item[0], item[1]])
        elif isinstance(item, bytes):
            if out and out[-1][1] is not None and not re.match(rb"\d+ \(", item):
                out[-1][0] += b" " + item
            else:
                out.append([item, None])
    return [(meta, literal) for meta, literal in out]


def _uid(meta):
    match = re.search(rb"\bUID (\d+)", meta)
    return int(match.group(1)) if match else None


def _flags(meta):
    match = re.search(rb"\bFLAGS \(([^)]*)\)", meta)
    return set(match.group(1).decode("ascii", "replace").split()) if match else set()


def _internal(meta):
    match = re.search(rb'INTERNALDATE "[^"]+"', meta)
    if not match:
        return None
    try:
        stamp = imaplib.Internaldate2tuple(b"* 1 (" + match.group(0) + b")")
    except Exception:
        return None
    if stamp is None:
        return None
    # Internaldate2tuple gives local time.
    return datetime.fromtimestamp(time.mktime(stamp), timezone.utc)


def _uid_set(uids):
    return ",".join(str(u) for u in uids)


class Cache:
    """The list's lines of one folder in a JSON file, kept while the folder's
    UIDVALIDITY stays the same."""

    def __init__(self, path):
        self.path = path
        self.validity = None
        self.lines = {}
        if path:
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                self.validity = data.get("validity")
                for uid, line in data.get("lines", {}).items():
                    sender, subject, when, unread = line
                    self.lines[int(uid)] = letter.Summary(
                        int(uid), sender, subject,
                        datetime.fromisoformat(when) if when else None, bool(unread))
            except (OSError, ValueError, TypeError, AttributeError):
                self.validity, self.lines = None, {}

    def check(self, validity):
        if validity != self.validity:
            self.validity, self.lines = validity, {}

    def save(self):
        if not self.path:
            return
        os.makedirs(os.path.dirname(self.path), mode=0o700, exist_ok=True)
        lines = {str(s.uid): [s.sender, s.subject, s.when.isoformat() if s.when else None,
                              s.unread]
                 for s in self.lines.values()}
        new = self.path + ".new"
        fd = os.open(new, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"validity": self.validity, "lines": lines}, f, ensure_ascii=False)
        os.replace(new, self.path)


class Mailbox:
    def __init__(self, address, password, server, cache_dir=None, connect=None):
        """server: (host, port); connect(host, port) -> an imaplib.IMAP4
        (IMAP4_SSL by default; the tests give their own)."""
        self.address = address
        self.password = password
        self.server = tuple(server)
        self.cache_dir = cache_dir
        self.connect = connect or self._ssl
        self.conn = None
        self.folders = None
        self.selected = None
        self.caches = {}
        self.uids = {}

    @staticmethod
    def _ssl(host, port):
        return imaplib.IMAP4_SSL(host, port, ssl_context=ssl.create_default_context(),
                                 timeout=TIMEOUT)

    # The connection

    def _open(self):
        if self.conn is not None:
            return self.conn
        conn = self.connect(*self.server)
        try:
            conn.login(self.address, self.password)
        except imaplib.IMAP4.error as e:
            try:
                conn.shutdown()
            except Exception:
                pass
            raise LoginFailed(str(e)) from None
        self.conn = conn
        self.selected = None
        return conn

    def _drop(self):
        conn, self.conn, self.selected = self.conn, None, None
        if conn is not None:
            try:
                conn.shutdown()
            except Exception:
                pass

    def _again(self, work):
        """work(conn) once more on a new connection when the old one broke
        (servers close idle ones)."""
        fresh = self.conn is None
        try:
            return work(self._open())
        except (imaplib.IMAP4.abort, OSError):
            self._drop()
            if fresh:
                raise
        return work(self._open())

    def close(self):
        conn, self.conn = self.conn, None
        if conn is not None:
            try:
                conn.logout()
            except Exception:
                pass

    @staticmethod
    def _ok(answer):
        typ, data = answer
        if typ != "OK":
            raise imaplib.IMAP4.error(b" ".join(d for d in data if isinstance(d, bytes))
                                      .decode("utf-8", "replace"))
        return data

    def _list(self, conn):
        listed = parse_list(self._ok(conn.list()))
        return folders_of(listed)

    def check(self):
        """Signs in and finds the folders: {role: the server's name}."""
        self.folders = self._again(self._list)
        return dict(self.folders)

    def _folder(self, role):
        if self.folders is None:
            self.check()
        name = self.folders.get(role)
        if name is None:
            raise KeyError(role)
        return name

    def _select(self, conn, role):
        name = self._folder(role)
        if self.selected != name:
            self._ok(conn.select(quote(name)))
            self.selected = name
            validity = conn.untagged_responses.get("UIDVALIDITY", [None])[-1]
            self._cache(role).check(validity.decode() if isinstance(validity, bytes) else validity)

    def _cache(self, role):
        if role not in self.caches:
            path = None
            if self.cache_dir:
                safe = re.sub(r"[^\w.@-]", "_", self.address.lower())
                path = os.path.join(self.cache_dir, safe, role + ".json")
            self.caches[role] = Cache(path)
        return self.caches[role]

    # The list

    def page(self, role, start, count):
        """The lines start..start+count of the folder, the newest first, and
        the number of letters in it; headers come from the cache when it has them."""
        def work(conn):
            self._select(conn, role)
            if start == 0 or role not in self.uids:
                found = self._ok(conn.uid("SEARCH", None, "ALL"))
                uids = sorted({int(u) for u in b" ".join(d for d in found if d).split()},
                              reverse=True)
                self.uids[role] = uids
            uids = self.uids[role][start:start + count]
            cache = self._cache(role)
            if start == 0:
                alive = set(self.uids[role])
                for uid in [u for u in cache.lines if u not in alive]:
                    del cache.lines[uid]
            if uids:
                new = [u for u in uids if u not in cache.lines]
                if new:
                    data = self._ok(conn.uid("FETCH", _uid_set(new),
                                             "(UID FLAGS INTERNALDATE %s)" % HEADERS))
                    for meta, literal in parse_fetch(data):
                        uid = _uid(meta)
                        if uid is not None:
                            cache.lines[uid] = letter.summary(
                                uid, literal, _flags(meta), _internal(meta))
                old = [u for u in uids if u not in new]
                if old:   # whether they were read meanwhile
                    data = self._ok(conn.uid("FETCH", _uid_set(old), "(UID FLAGS)"))
                    for meta, _literal in parse_fetch(data):
                        uid = _uid(meta)
                        if uid in cache.lines:
                            cache.lines[uid] = cache.lines[uid]._replace(
                                unread="\\Seen" not in _flags(meta))
                cache.save()
            return [cache.lines[u] for u in uids if u in cache.lines], len(self.uids[role])
        return self._again(work)

    def cached(self, role, count):
        """The newest lines the cache keeps, to show before the server answers."""
        cache = self._cache(role)
        return [cache.lines[u] for u in sorted(cache.lines, reverse=True)[:count]]

    # A letter

    def read(self, role, uid):
        """The letter; it becomes seen."""
        def work(conn):
            self._select(conn, role)
            data = self._ok(conn.uid("FETCH", str(uid), "(UID BODY.PEEK[])"))
            raw = next((literal for meta, literal in parse_fetch(data)
                        if literal is not None and _uid(meta) in (uid, None)), None)
            if raw is None:
                raise KeyError(uid)
            if role != SENT:
                conn.uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Seen)")
                cache = self._cache(role)
                if uid in cache.lines:
                    cache.lines[uid] = cache.lines[uid]._replace(unread=False)
                    cache.save()
            return letter.read(raw)
        return self._again(work)

    def trash(self, role, uid):
        """Moves the letter into «Корзина» (MOVE, else COPY and EXPUNGE)."""
        if role == TRASH:
            raise ValueError("already in the trash")

        def work(conn):
            target = quote(self._folder(TRASH))
            self._select(conn, role)
            if "MOVE" in conn.capabilities:
                self._ok(conn.uid("MOVE", str(uid), target))
            else:
                self._ok(conn.uid("COPY", str(uid), target))
                self._ok(conn.uid("STORE", str(uid), "+FLAGS.SILENT", "(\\Deleted)"))
                if "UIDPLUS" in conn.capabilities:
                    self._ok(conn.uid("EXPUNGE", str(uid)))
                else:
                    self._ok(conn.expunge())
            for folder in (role, TRASH):
                self.uids.pop(folder, None)
            cache = self._cache(role)
            cache.lines.pop(uid, None)
            cache.save()
        return self._again(work)

    # A sent letter

    def save_sent(self, raw, message_id):
        """Puts the sent letter into «Отправленные» (APPEND) unless the server
        already keeps it there (Gmail and others keep what went through their
        SMTP); False when the mailbox has no such folder."""
        def work(conn):
            try:
                name = self._folder(SENT)
            except KeyError:
                return False
            self._select(conn, SENT)
            found = self._ok(conn.uid("SEARCH", None, "HEADER", "Message-ID", quote(message_id)))
            if not b" ".join(d for d in found if d).split():
                self._ok(conn.append(quote(name), "(\\Seen)", time.time(), raw))
            self.uids.pop(SENT, None)
            return True
        return self._again(work)
