# Mail (haltia-mail)

«Почта»: a simple GTK 3 mail program for the Haltia desktop, in Python 3;
it runs on other GTK desktops too. One mailbox: sign in with the address
and an app password (the IMAP and SMTP servers come from the domain), then
«Входящие», «Отправленные» and «Корзина», a letter as plain text and its
attachments. App ID: `io.github.haltia_de.Mail`.

For now the program is hidden (the author's decision of 22 October 2026):
the `.desktop` file has `NoDisplay=true` and `X-Haltia-Home=false`. Keep it small: sign-in and
what reports and sending files need come first; reading stays minimal.

## Project rules

The shared conventions live in haltia-de/haltia, `docs/conventions.md`
(SemVer, tags, commits, languages, paths, releases with ADR 0002 and
ADR 0005). Follow them; this file only adds what is particular to Почта.

- The password lives only in `haltia_kit.vault`; «Выйти» deletes it and the
  local cache. It never goes into the config, a log or an error text.
- IMAP, SMTP and the vault run in one worker thread (imaplib is not
  thread-safe), never on the GTK thread; SSL is verified and every
  connection has a timeout.
- Letters are shown as plain text: no remote images, no scripts, no HTML
  rendering. Attachment names are cleaned before they become file names.
- The header follows the shell's contract: 104 px, «←» bound to `app.back`
  and seen only while there is a step back.

## Repository layout

- `haltia-mail`: the GTK program.
- `haltia_mail/`: the parts without GTK: `servers.py` (servers and hints by
  domain), `imap.py` (the mailbox, folders by special-use flags, the header
  cache), `letter.py` (headers, HTML to text, attachments), `outbox.py`
  (composing a letter, «Исходящие» on disk, SMTP).
- `tests/`: unittest with an in-process fake IMAP server (`fakeimap.py`) and
  a fake SMTP connection; `sh tests/run.sh` runs them without display,
  network or keyring.
- `data/`: the `.desktop` file, the SVG icon, the AppStream metainfo, the
  D-Bus service file (`.service.in`; `install.sh` fills in the path).
- `install.sh`: the installer (copies files; dependencies must be present).
- `packaging/alpine/APKBUILD`: the Alpine (postmarketOS) package.
- `.github/workflows/release.yml`: the release through haltia-de/ci.
- `README.md`, `NEWS.md`: docs for people and the release notes.

## D-Bus: sending a letter (for haltia-report)

Other programs send through Почта without opening its window:

- bus: the session bus; name `io.github.haltia_de.Mail`, object
  `/io/github/haltia_de/Mail`, interface `io.github.haltia_de.Mail`. The
  name is the GApplication's own; when Почта is not running, D-Bus starts it
  as `haltia-mail --gapplication-service` (no window).
- method `Send(s to, s subject, s body, as attachments) -> s`:
  - `to`: one address or several, separated by commas (`Имя <a@b.ru>` too);
  - `body`: plain text;
  - `attachments`: absolute paths of files, read during the call (the
    caller may delete them after the answer), 20 MB in all at most.
- the answer:
  - `sent`: the SMTP server took the letter;
  - `queued`: the letter waits in «Исходящие» (no network, the server did
    not answer within 20 s, the keyring is closed); Почта keeps running
    and tries again when the network comes back and every 10 minutes;
  - `not-configured`: no mailbox is signed in; nothing is kept.
- D-Bus errors: `org.freedesktop.DBus.Error.InvalidArgs` (no valid
  recipient, a path that is not absolute or cannot be read, too big),
  `io.github.haltia_de.Mail.Error.Refused` (the server refused this letter
  for good), `org.freedesktop.DBus.Error.Failed` (it could not be kept).
- «Исходящие» lives in `$XDG_DATA_HOME/haltia-mail/outbox` (0700, letters
  0600); «Выйти» deletes it with the password. The letter goes from the
  signed-in address; whether a copy appears in «Отправленные» is up to the
  server.

Try it: `gdbus call --session --dest io.github.haltia_de.Mail --object-path
/io/github/haltia_de/Mail --method io.github.haltia_de.Mail.Send
a@b.ru 'Тема' 'Текст' "['/tmp/report.txt']"`.

## Run and install

- From a checkout: `python3 ./haltia-mail` (needs Python 3, PyGObject with
  GTK 3, and haltia-kit 2.3.0 or later).
- Install: `sh install.sh [ROOT] [PREFIX]`, `/usr/local` by default.

## Releases

- Only with the person's approval: never tag or publish on your own.
- The `vX.Y.Z` tag, `pkgver` in the APKBUILD and the newest `<release>` in
  the metainfo agree; the notes go into `NEWS.md` first.
- A pushed `vX.Y.Z` tag runs `release.yml`, which builds the signed noarch
  apk with haltia-de/ci `@v1` and attaches it to the GitHub release.
