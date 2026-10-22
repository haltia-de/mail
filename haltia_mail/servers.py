"""The mail servers of a domain and where its app password is made.

servers("ivan@ya.ru")       -> Provider (imap, smtp, hint) or None
from_field("imap.example.org:993") -> Provider for a domain we do not know
"""

from collections import namedtuple

# host and port of each; SMTP on 465 is SSL from the start, 587 is STARTTLS.
Provider = namedtuple("Provider", "name imap smtp hint")

YANDEX = Provider(
    "Яндекс", ("imap.yandex.ru", 993), ("smtp.yandex.ru", 465),
    "Пароль приложения создаётся в Яндекс ID: «Безопасность» → «Пароли "
    "приложений» → «Почта» (id.yandex.ru/security/app-passwords). В настройках "
    "Почты, в разделе «Почтовые программы», должен быть включён доступ по IMAP.")
MAILRU = Provider(
    "Mail", ("imap.mail.ru", 993), ("smtp.mail.ru", 465),
    "Пароль для внешнего приложения создаётся в настройках аккаунта Mail: "
    "«Безопасность» → «Пароли для внешних приложений» "
    "(account.mail.ru/user/2-step-auth/passwords).")
GMAIL = Provider(
    "Gmail", ("imap.gmail.com", 993), ("smtp.gmail.com", 465),
    "Пароль приложения создаётся в аккаунте Google на странице "
    "myaccount.google.com/apppasswords; для этого нужна двухэтапная проверка.")
RAMBLER = Provider(
    "Рамблер", ("imap.rambler.ru", 993), ("smtp.rambler.ru", 465),
    "В настройках Рамблер/почты, в разделе «Программы», включите «Доступ к "
    "почтовому ящику с помощью почтовых клиентов»; там же создаётся пароль "
    "для почтовой программы.")
OUTLOOK = Provider(
    "Outlook", ("outlook.office365.com", 993), ("smtp-mail.outlook.com", 587),
    "Пароль приложения создаётся в учётной записи Microsoft: «Безопасность» → "
    "«Дополнительные параметры безопасности» → «Пароли приложений» "
    "(account.microsoft.com/security); для этого нужна двухэтапная проверка.")

DOMAINS = {}
for _provider, _domains in (
        (YANDEX, "yandex.ru ya.ru yandex.com yandex.by yandex.kz yandex.ua narod.ru"),
        (MAILRU, "mail.ru bk.ru inbox.ru list.ru internet.ru my.com"),
        (GMAIL, "gmail.com googlemail.com"),
        (RAMBLER, "rambler.ru lenta.ru autorambler.ru myrambler.ru ro.ru rambler.ua"),
        (OUTLOOK, "outlook.com outlook.ru hotmail.com hotmail.ru live.com live.ru msn.com")):
    for _domain in _domains.split():
        DOMAINS[_domain] = _provider

OTHER_HINT = ("Укажите сервер IMAP своей почты, например imap.example.org. "
              "Пароль приложения создаётся в настройках безопасности почты.")


def domain(address):
    address = (address or "").strip().lower()
    return address.rpartition("@")[2] if "@" in address else ""


def servers(address):
    """The provider of the address's domain, or None for a domain we do not know."""
    return DOMAINS.get(domain(address))


def _host_port(text, port):
    text = text.strip().lower()
    for prefix in ("imaps://", "imap://"):
        if text.startswith(prefix):
            text = text[len(prefix):]
    text = text.rstrip("/")
    host, colon, number = text.rpartition(":")
    if colon and number.isdigit() and host:
        return host, int(number)
    return text, port


def from_field(text):
    """A provider from the field «Сервер»: «imap.host» or «imap.host:port».

    SMTP is guessed as smtp.<the rest> on 465; part 2 will let one change it."""
    host, port = _host_port(text, 993)
    if not host or " " in host or "." not in host:
        return None
    base = host[5:] if host.startswith("imap.") else host
    return Provider(host, (host, port), ("smtp." + base, 465), OTHER_HINT)


def guess_field(address):
    """What to put into an empty «Сервер» for a domain we do not know."""
    name = domain(address)
    return "imap." + name if name else ""
