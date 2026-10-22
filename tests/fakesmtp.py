"""An SMTP server in the test's own process, spoken to by the real smtplib
over a socket pair: no network, no TLS, just enough ESMTP for Почта.

    server = FakeSMTP("ivan@ya.ru", "secret", refuse={"nobody@ya.ru"})
    smtp.send("ivan@ya.ru", "secret", ("fake", 465), msg, connect=server.connect)
    server.sent -> [(sender, [recipients], the letter's bytes)]
"""

import base64
import re
import smtplib
import socket
import threading


class PairSMTP(smtplib.SMTP):
    def __init__(self, sock):
        self._pair = sock
        # A local name of our own: no DNS lookup.
        super().__init__(local_hostname="fake.invalid")
        self.connect("fake.invalid", 25)

    def _get_socket(self, host, port, timeout):
        return self._pair


class FakeSMTP:
    def __init__(self, user, password, refuse=()):
        self.user, self.password = user, password
        self.refuse = set(refuse)
        self.sent = []

    def connect(self, host=None, port=None):
        ours, theirs = socket.socketpair()
        threading.Thread(target=self.serve, args=(ours,), daemon=True).start()
        return PairSMTP(theirs)

    def serve(self, sock):
        rfile = sock.makefile("rb")
        sender, to = None, []

        def send(text):
            sock.sendall(text.encode("utf-8"))

        try:
            send("220 fake ESMTP ready\r\n")
            while True:
                line = rfile.readline()
                if not line:
                    break
                verb, _, arg = line.decode("utf-8").rstrip("\r\n").partition(" ")
                verb = verb.upper()
                if verb == "EHLO":
                    send("250-fake.invalid\r\n250-AUTH PLAIN\r\n250 SIZE 10000000\r\n")
                elif verb == "AUTH":
                    _mech, _, initial = arg.partition(" ")
                    parts = base64.b64decode(initial).split(b"\0")
                    if parts[1:] == [self.user.encode(), self.password.encode()]:
                        send("235 welcome\r\n")
                    else:
                        send("535 5.7.8 bad credentials\r\n")
                elif verb == "MAIL":
                    sender, to = re.search(r"<([^>]*)>", arg).group(1), []
                    send("250 ok\r\n")
                elif verb == "RCPT":
                    address = re.search(r"<([^>]*)>", arg).group(1)
                    if address in self.refuse:
                        send("550 5.1.1 no such user\r\n")
                    else:
                        to.append(address)
                        send("250 ok\r\n")
                elif verb == "DATA":
                    send("354 go ahead\r\n")
                    lines = []
                    while True:
                        data = rfile.readline()
                        if data in (b".\r\n", b""):
                            break
                        lines.append(data[1:] if data.startswith(b"..") else data)
                    self.sent.append((sender, to, b"".join(lines)))
                    send("250 queued\r\n")
                elif verb in ("RSET", "NOOP"):
                    send("250 ok\r\n")
                elif verb == "QUIT":
                    send("221 bye\r\n")
                    break
                else:
                    send("502 unknown\r\n")
        except OSError:
            pass
        finally:
            try:
                sock.close()
            except OSError:
                pass
