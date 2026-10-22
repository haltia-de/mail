# Mail (haltia-mail)

«Почта»: a simple GTK 3 mail program for the Haltia desktop, in Python 3;
it runs on other GTK desktops too. One mailbox: sign in with the address
and an app password (the IMAP and SMTP servers come from the domain), then
«Входящие», «Отправленные» and «Корзина», a letter as plain text and its
attachments; a new letter («Кому», «Тема», text, files) goes out over SMTP,
and files shared from other programs (`X-Haltia-Share=true`) start one.
App ID: `io.github.haltia_de.Mail`.

For now the program is hidden (the author's decision of 22 October 2026):
the `.desktop` file has `NoDisplay=true` and `X-Haltia-Home=false`. Keep it small: sign-in and
what reports and sending files need come first; reading stays minimal.

## Project rules

The shared conventions live in haltia-de/haltia, `docs/conventions.md`
(SemVer, tags, commits, languages, paths, releases with ADR 0002 and
ADR 0005). Follow them; this file only adds what is particular to Почта.

- The password lives only in `haltia_kit.vault`; «Выйти» deletes it and the
  local cache. It never goes into the config, a log or an error text.
- IMAP and SMTP run in one worker thread (imaplib is not thread-safe), never
  on the GTK thread; SSL is verified (SMTP: SSL on 465, else STARTTLS or
  nothing is sent) and every connection has a timeout.
- Files to send are copied into the cache first: the program that shared
  them may delete its own.
- Letters are shown as plain text: no remote images, no scripts, no HTML
  rendering. Attachment names are cleaned before they become file names.
- The header follows the shell's contract: 104 px, «←» bound to `app.back`
  and seen only while there is a step back.

## D-Bus: sending without a window (for haltia-report)

Other programs send a letter through Почта on the session bus; D-Bus starts
it without a window (`haltia-mail --gapplication-service`, from
`share/dbus-1/services/io.github.haltia_de.Mail.service`) when it is not
running. Keep this contract: shell#58 (haltia-report) calls it.

- Bus name `io.github.haltia_de.Mail`, object `/io/github/haltia_de/Mail`,
  interface `io.github.haltia_de.Mail`.
- `Send(s to, s subject, s body, as attachments) -> s`: `to` is «Кому»
  (addresses separated by commas); `attachments` are absolute paths of
  regular files, 20 MB together at most, copied at once (the caller may
  delete its own after the answer). The answer comes at once, after the
  letter is kept, not after SMTP:
  - `sent`: the letter went out now (a copy goes to «Отправленные»);
  - `queued`: it waits in «Исходящие» and goes when it can;
  - `not-configured`: no mailbox signed in; nothing is kept.
- A wrong address or path, a missing file or too big files:
  `org.freedesktop.DBus.Error.InvalidArgs`; a disk error:
  `org.freedesktop.DBus.Error.Failed`.
- «Исходящие» live in `$XDG_DATA_HOME/haltia-mail/outbox` (0700, files
  0600), one folder per letter, oldest sent first. They are tried again on
  start, when the network changes (`Gio.NetworkMonitor`, NetworkManager
  where present) and every 10 minutes; the program holds itself (stays
  running) while letters wait. A letter the server refuses goes after
  three days; «Выйти» deletes the waiting ones.

## Repository layout

- `haltia-mail`: the GTK program.
- `haltia_mail/`: the parts without GTK: `servers.py` (servers and hints by
  domain), `imap.py` (the mailbox, folders by special-use flags, the header
  cache, the sent letter's copy), `letter.py` (headers, HTML to text,
  attachments), `smtp.py` (a new letter and its sending), `outbox.py`
  («Исходящие» on disk and their sending).
- `tests/`: unittest with in-process fake IMAP and SMTP servers
  (`fakeimap.py`, `fakesmtp.py`); `sh tests/run.sh` runs them without
  display, network or keyring.
- `data/`: the `.desktop` file, the SVG icon, the AppStream metainfo, the
  D-Bus service file (`@bindir@` is filled in by `install.sh`).
- `install.sh`: the installer (copies files; dependencies must be present).
- `packaging/alpine/APKBUILD`: the Alpine (postmarketOS) package.
- `.github/workflows/release.yml`: the release through haltia-de/ci.
- `README.md`, `NEWS.md`: docs for people and the release notes.

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
