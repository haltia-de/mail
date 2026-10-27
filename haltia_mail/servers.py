"""The mail servers of a domain and where its app password is made.

servers("ivan@ya.ru")       -> Provider (imap, smtp, hint) or None
from_field("imap.example.org:993") -> Provider for a domain we do not know
"""

from collections import namedtuple

# host and port of each; SMTP on 465 is SSL from the start, 587 is STARTTLS.
Provider = namedtuple("Provider", "name imap smtp hint")

# Under the sign-in form for every provider; extras name where the app
# password is made and where IMAP is turned on.
APP_HINT = ("Нужен пароль приложения, а не обычный пароль; в настройках "
            "ящика должен быть включён доступ почтовым программам (IMAP).")

YANDEX = Provider(
    "Яндекс", ("imap.yandex.ru", 993), ("smtp.yandex.ru", 465),
    APP_HINT + " Яндекс ID → Безопасность → Пароли приложений → Почта; "
    "Почта → Настройки → Почтовые программы: IMAP и пароли приложений")
MAILRU = Provider(
    "Mail", ("imap.mail.ru", 993), ("smtp.mail.ru", 465),
    APP_HINT + " Mail: Безопасность → Пароли для внешних приложений "
    "(account.mail.ru/user/2-step-auth/passwords)")
GMAIL = Provider(
    "Gmail", ("imap.gmail.com", 993), ("smtp.gmail.com", 465),
    APP_HINT + " Google: аккаунт → Безопасность → Пароли приложений "
    "(myaccount.google.com/apppasswords); нужна двухэтапная проверка")
RAMBLER = Provider(
    "Рамблер", ("imap.rambler.ru", 993), ("smtp.rambler.ru", 465),
    APP_HINT)
OUTLOOK = Provider(
    "Outlook", ("outlook.office365.com", 993), ("smtp-mail.outlook.com", 587),
    APP_HINT)

DOMAINS = {}
for _provider, _domains in (
        (YANDEX, "yandex.ru ya.ru yandex.com yandex.by yandex.kz yandex.ua narod.ru"),
        (MAILRU, "mail.ru bk.ru inbox.ru list.ru internet.ru my.com"),
        (GMAIL, "gmail.com googlemail.com"),
        (RAMBLER, "rambler.ru lenta.ru autorambler.ru myrambler.ru ro.ru rambler.ua"),
        (OUTLOOK, "outlook.com outlook.ru hotmail.com hotmail.ru live.com live.ru msn.com")):
    for _domain in _domains.split():
        DOMAINS[_domain] = _provider

OTHER_HINT = (APP_HINT + " Укажите сервер IMAP своей почты, например "
              "imap.example.org.")


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
