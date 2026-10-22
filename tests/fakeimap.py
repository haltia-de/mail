"""An IMAP server in the test's own process, spoken to by the real imaplib
over a socket pair: no network, no SSL, just enough IMAP4rev1 for Почта.

    server = FakeServer("ivan@ya.ru", "secret")
    server.add("INBOX", raw_bytes, seen=False)
    box = Mailbox("ivan@ya.ru", "secret", ("fake", 993), connect=server.connect)
"""

import email.policy
import imaplib
import re
import socket
import threading


class PairIMAP(imaplib.IMAP4):
    def __init__(self, sock):
        self._pair = sock
        super().__init__("fake.invalid", 993)

    def _create_socket(self, timeout):
        return self._pair


TOKEN = re.compile(r'\s*("(?:[^"\\]|\\.)*"|\([^)]*\)|\S+)')


def tokens(text):
    out = []
    for match in TOKEN.finditer(text):
        token = match.group(1)
        if token.startswith('"'):
            token = re.sub(r'\\(.)', r'\1', token[1:-1])
        out.append(token)
    return out


def uid_set(text, uids):
    """The UIDs of «1,3:5,7:*» among uids."""
    top = max(uids) if uids else 0
    chosen = set()
    for piece in text.split(","):
        low, _, high = piece.partition(":")
        low = top if low == "*" else int(low)
        high = low if not high else (top if high == "*" else int(high))
        low, high = min(low, high), max(low, high)
        chosen.update(u for u in uids if low <= u <= high)
    return chosen


def header_fields(raw, names):
    head = raw.split(b"\r\n\r\n", 1)[0].split(b"\r\n")
    out, keep = [], False
    for line in head:
        if line[:1] in (b" ", b"\t"):
            if keep:
                out.append(line)
            continue
        keep = line.split(b":", 1)[0].strip().upper() in names
        if keep:
            out.append(line)
    return b"\r\n".join(out) + b"\r\n\r\n"


class Folder:
    def __init__(self, special=None):
        self.special = special
        self.letters = []   # [uid, flags, raw]
        self.next_uid = 1


class FakeServer:
    def __init__(self, user, password, special_use=True, move=True,
                 names=("Sent", "Trash"), flags_last=False):
        self.user, self.password = user, password
        self.capabilities = ["IMAP4rev1", "UIDPLUS"] + (["MOVE"] if move else []) \
            + (["SPECIAL-USE"] if special_use else [])
        self.validity = 7
        self.flags_last = flags_last
        self.folders = {"INBOX": Folder(), "Drafts": Folder("\\Drafts"),
                        names[0]: Folder("\\Sent" if special_use else None),
                        names[1]: Folder("\\Trash" if special_use else None),
                        "Archive": Folder("\\Archive")}
        self.commands = []
        self.sockets = []
        self.lock = threading.Lock()

    def add(self, folder, raw, seen=False):
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        raw = re.sub(rb"\r?\n", b"\r\n", raw)
        box = self.folders[folder]
        box.letters.append([box.next_uid, {"\\Seen"} if seen else set(), raw])
        box.next_uid += 1
        return box.next_uid - 1

    def connect(self, host=None, port=None):
        ours, theirs = socket.socketpair()
        self.sockets.append(ours)
        threading.Thread(target=self.serve, args=(ours,), daemon=True).start()
        return PairIMAP(theirs)

    def break_connections(self):
        """As when a server closes an idle connection."""
        for sock in self.sockets:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        self.sockets = []

    # The session

    def serve(self, sock):
        rfile = sock.makefile("rb")
        state = {"auth": False, "folder": None}

        def send(data):
            sock.sendall(data if isinstance(data, bytes) else data.encode("utf-8"))

        try:
            send("* OK fake IMAP ready\r\n")
            while True:
                line = rfile.readline()
                if not line:
                    break
                line = line.decode("utf-8").rstrip("\r\n")
                tag, _, rest = line.partition(" ")
                with self.lock:
                    self.commands.append(rest)
                    done = self.command(tag, rest, state, send)
                if done:
                    break
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass

    def command(self, tag, rest, state, send):
        args = tokens(rest)
        name = args[0].upper()
        args = args[1:]
        if name == "CAPABILITY":
            send("* CAPABILITY %s\r\n%s OK done\r\n" % (" ".join(self.capabilities), tag))
        elif name == "LOGOUT":
            send("* BYE bye\r\n%s OK done\r\n" % tag)
            return True
        elif name == "NOOP":
            send("%s OK done\r\n" % tag)
        elif name == "LOGIN":
            if args == [self.user, self.password]:
                state["auth"] = True
                send("%s OK logged in\r\n" % tag)
            else:
                send("%s NO [AUTHENTICATIONFAILED] Invalid credentials\r\n" % tag)
        elif not state["auth"]:
            send("%s BAD log in first\r\n" % tag)
        elif name == "LIST":
            for folder, box in self.folders.items():
                flags = ["\\HasNoChildren"] + ([box.special] if box.special else [])
                send('* LIST (%s) "/" "%s"\r\n' % (" ".join(flags), folder))
            send("%s OK done\r\n" % tag)
        elif name in ("SELECT", "EXAMINE"):
            if args[0] not in self.folders:
                send("%s NO no such folder\r\n" % tag)
                return
            state["folder"] = args[0]
            box = self.folders[args[0]]
            send("* %d EXISTS\r\n* OK [UIDVALIDITY %d] ok\r\n* OK [UIDNEXT %d] ok\r\n"
                 "%s OK [READ-WRITE] done\r\n"
                 % (len(box.letters), self.validity, box.next_uid, tag))
        elif name == "UID" and state["folder"]:
            self.uid(tag, args[0].upper(), args[1:], self.folders[state["folder"]], send)
        elif name == "EXPUNGE" and state["folder"]:
            self.expunge(self.folders[state["folder"]], None, send)
            send("%s OK done\r\n" % tag)
        else:
            send("%s BAD unknown %s\r\n" % (tag, name))

    def expunge(self, box, uids, send):
        for i in range(len(box.letters) - 1, -1, -1):
            uid, flags, _raw = box.letters[i]
            if "\\Deleted" in flags and (uids is None or uid in uids):
                del box.letters[i]
                send("* %d EXPUNGE\r\n" % (i + 1))

    def uid(self, tag, command, args, box, send):
        uids = [letter[0] for letter in box.letters]
        if command == "SEARCH":
            send("* SEARCH %s\r\n%s OK done\r\n" % (" ".join(map(str, uids)), tag))
            return
        chosen = uid_set(args[0], uids)
        if command == "FETCH":
            items = args[1].strip("()").upper()
            for seq, (uid, flags, raw) in enumerate(box.letters, 1):
                if uid not in chosen:
                    continue
                meta = "UID %d" % uid
                flag_text = " FLAGS (%s)" % " ".join(sorted(flags))
                if "FLAGS" in items.split() and not self.flags_last:
                    meta += flag_text
                if "INTERNALDATE" in items:
                    meta += ' INTERNALDATE "17-Oct-2026 10:00:00 +0300"'
                body = None
                if "HEADER.FIELDS" in items:
                    names = set(re.search(r"\(([^)]*)", items[items.index("HEADER.FIELDS"):])
                                .group(1).encode().split())
                    body, key = header_fields(raw, names), \
                        "BODY[HEADER.FIELDS (%s)]" % " ".join(sorted(n.decode() for n in names))
                elif "BODY.PEEK[]" in items or "BODY[]" in items:
                    body, key = raw, "BODY[]"
                    if "BODY[]" in items:
                        flags.add("\\Seen")
                tail = flag_text if self.flags_last and "FLAGS" in items.split() else ""
                if body is None:
                    send("* %d FETCH (%s%s)\r\n" % (seq, meta, tail))
                else:
                    send(("* %d FETCH (%s %s {%d}\r\n" % (seq, meta, key, len(body))).encode()
                         + body + (tail + ")\r\n").encode())
            send("%s OK done\r\n" % tag)
        elif command == "STORE":
            change, values = args[1].upper(), set(args[2].strip("()").split())
            for letter in box.letters:
                if letter[0] in chosen:
                    if change.startswith("+"):
                        letter[1] |= values
                    elif change.startswith("-"):
                        letter[1] -= values
                    else:
                        letter[1] = set(values)
            send("%s OK done\r\n" % tag)
        elif command in ("COPY", "MOVE"):
            target = self.folders.get(args[1])
            if target is None:
                send("%s NO [TRYCREATE] no such folder\r\n" % tag)
                return
            for letter in list(box.letters):
                if letter[0] in chosen:
                    target.letters.append([target.next_uid, set(letter[1]), letter[2]])
                    target.next_uid += 1
                    if command == "MOVE":
                        letter[1].add("\\Deleted")
            if command == "MOVE":
                self.expunge(box, chosen, send)
            send("%s OK done\r\n" % tag)
        elif command == "EXPUNGE":
            self.expunge(box, chosen, send)
            send("%s OK done\r\n" % tag)
        else:
            send("%s BAD unknown UID %s\r\n" % (tag, command))


def letter(sender="Иван Петров <ivan@ya.ru>", subject="Привет", body="Текст письма.",
           date="Sat, 17 Oct 2026 10:00:00 +0300", html=None, attachments=()):
    """A letter's bytes made with the email package."""
    from email.message import EmailMessage
    msg = EmailMessage()
    msg["From"] = sender
    msg["To"] = "maria@ya.ru"
    msg["Subject"] = subject
    msg["Date"] = date
    if body is not None:
        msg.set_content(body)
    if html is not None:
        if body is None:
            msg.set_content(html, subtype="html")
        else:
            msg.add_alternative(html, subtype="html")
    for name, mime, data in attachments:
        main, sub = mime.split("/")
        msg.add_attachment(data, maintype=main, subtype=sub, filename=name)
    return msg.as_bytes(policy=email.policy.SMTP)
