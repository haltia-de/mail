"""Letters as one reads them: the header line of the list and the text.

summary(uid, header_bytes, flags, internal) -> Summary for the list
read(raw) -> Letter: subject, sender, when, text, attachments
html_to_text(html) -> plain text; links kept as «text (address)»,
    no pictures, no scripts, no styles
clean_name(name) -> a file name without path or control characters
"""

from collections import namedtuple
from datetime import datetime, timezone
import email
from email import policy
from email.header import decode_header, make_header
from email.utils import getaddresses, parseaddr, parsedate_to_datetime
from html.parser import HTMLParser
import os
import re

Summary = namedtuple("Summary", "uid sender subject when unread")
Attachment = namedtuple("Attachment", "name mime size data")
Letter = namedtuple("Letter", "subject sender to when text attachments")

NO_SUBJECT = "(без темы)"


def _header(value):
    """A header as text, whatever its encoding (broken ones too)."""
    if value is None:
        return ""
    try:
        text = str(make_header(decode_header(str(value))))
    except Exception:
        text = str(value)
    return " ".join(text.split())


def who(value):
    """«Иван Петров» for «Иван Петров <ivan@ya.ru>», else the address."""
    name, address = parseaddr(_header(value))
    return name.strip().strip('"') or address or _header(value)


def when_of(date, internal=None):
    """The letter's time (aware) from its Date, else the server's date."""
    for value in (date, internal):
        if not value:
            continue
        if isinstance(value, datetime):
            stamp = value
        else:
            try:
                stamp = parsedate_to_datetime(_header(value))
            except (TypeError, ValueError, IndexError):
                continue
        if stamp.tzinfo is None:
            stamp = stamp.replace(tzinfo=timezone.utc)
        return stamp
    return None


def summary(uid, header, flags, internal=None):
    """One line of the list from the bytes of FROM, SUBJECT and DATE."""
    msg = email.message_from_bytes(header or b"", policy=policy.compat32)
    return Summary(uid, who(msg["From"]), _header(msg["Subject"]) or NO_SUBJECT,
                   when_of(msg["Date"], internal), "\\Seen" not in flags)


class _Text(HTMLParser):
    SKIP = {"script", "style", "head", "title", "noscript", "template", "svg",
            "object", "iframe"}
    BLOCK = {"p", "div", "tr", "table", "ul", "ol", "h1", "h2", "h3", "h4",
             "h5", "h6", "blockquote", "pre", "section", "article", "header",
             "footer", "hr", "dl", "dt", "dd", "center", "form"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out = []
        self.skip = 0
        self.links = []   # (href, where the link's text begins in out)
        self.pre = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif self.skip:
            return
        elif tag == "br":
            self.out.append("\n")
        elif tag == "li":
            self.out.append("\n• ")
        elif tag in ("td", "th"):
            self.out.append(" ")
        elif tag in self.BLOCK:
            self.out.append("\n\n")
            if tag == "pre":
                self.pre += 1
        elif tag == "a":
            href = (dict(attrs).get("href") or "").strip()
            self.links.append((href, len(self.out)))

    def handle_startendtag(self, tag, attrs):
        if tag != "a":
            self.handle_starttag(tag, attrs)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif self.skip:
            return
        elif tag in self.BLOCK:
            self.out.append("\n\n")
            if tag == "pre":
                self.pre = max(0, self.pre - 1)
        elif tag == "a" and self.links:
            href, start = self.links.pop()
            text = " ".join("".join(self.out[start:]).split())
            if re.match(r"(?i)(https?:|mailto:)", href) and text != href \
                    and text != href.partition(":")[2]:
                self.out.append(" (%s)" % href)

    def handle_data(self, data):
        if self.skip:
            return
        if not self.pre:
            data = re.sub(r"\s+", " ", data)
        self.out.append(data)


def html_to_text(html):
    parser = _Text()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        pass
    text = "".join(parser.out).replace("\xa0", " ")
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(lines)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def _content(part):
    try:
        return part.get_content()
    except Exception:
        payload = part.get_payload(decode=True) or b""
        charset = part.get_content_charset() or "utf-8"
        try:
            return payload.decode(charset, "replace")
        except LookupError:
            return payload.decode("utf-8", "replace")


def clean_name(name):
    """A file's name safe to use in a folder of ours: no path, no control
    characters, no leading dot; "" when nothing is left."""
    name = os.path.basename(_header(name).replace("\\", "/")).strip()
    name = "".join(c for c in name if c >= " " and c != "\x7f").lstrip(".")
    return _shorten(name) if name else ""


def _attachment_name(part, number):
    name = clean_name(part.get_filename() or "")
    if not name:
        extension = {"application/pdf": ".pdf", "image/jpeg": ".jpg",
                     "image/png": ".png", "text/plain": ".txt"}
        name = "вложение-%d%s" % (number, extension.get(part.get_content_type(), ""))
    return _shorten(name)


def _shorten(name, limit=200):
    """At most limit bytes of UTF-8 (file systems allow 255; « (2)» needs
    room too), the extension kept."""
    if len(name.encode()) <= limit:
        return name
    stem, dot, ext = name.rpartition(".")
    if not stem or len(ext.encode()) > 16:
        stem, dot, ext = name, "", ""
    room = limit - len((dot + ext).encode())
    return stem.encode()[:room].decode("utf-8", "ignore").rstrip() + dot + ext


def read(raw):
    """The letter from its bytes: text/plain when there is one, else the HTML
    as text; the attachments with their bytes."""
    msg = email.message_from_bytes(raw, policy=policy.default)
    body = None
    try:
        body = msg.get_body(preferencelist=("plain", "html"))
    except Exception:
        pass
    text = ""
    if body is not None:
        content = _content(body)
        if isinstance(content, bytes):
            content = content.decode("utf-8", "replace")
        if body.get_content_type() == "text/html":
            text = html_to_text(content)
        else:
            text = content.replace("\r\n", "\n").strip()
    attachments = []
    try:
        parts = list(msg.iter_attachments()) if msg.is_multipart() else []
    except Exception:
        parts = []
    # Attachments inside attached multiparts (a forwarded letter's files).
    while parts:
        part = parts.pop(0)
        if part.is_multipart() and not part.get_filename():
            try:
                parts[0:0] = list(part.iter_attachments())
            except Exception:
                pass
            continue
        if part.get_content_type() == "message/rfc822":
            data = part.get_payload(0).as_bytes() if part.get_payload() else b""
        else:
            data = part.get_payload(decode=True) or b""
        name = _attachment_name(part, len(attachments) + 1)
        if part.get_content_type() == "message/rfc822" and not part.get_filename():
            name = name.rsplit(".", 1)[0] + ".eml"
        attachments.append(Attachment(name, part.get_content_type(), len(data), data))
    to = ", ".join(name or address for name, address
                   in getaddresses([_header(v) for v in msg.get_all("To", [])]))
    return Letter(_header(msg["Subject"]) or NO_SUBJECT, _header(msg["From"]), to,
                  when_of(msg["Date"]), text, attachments)
