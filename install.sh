#!/bin/sh
# SPDX-License-Identifier: GPL-2.0-or-later
# Install haltia-mail (Почта): sh install.sh [ROOT] [PREFIX]
# ROOT empty or / for this system; PREFIX /usr/local unless given (a
# package passes /usr). It needs Python 3 with PyGObject (GTK 3) and
# haltia-kit (github.com/haltia-de/kit). The program finds its haltia_mail
# package in PREFIX/share/haltia-mail. For now the .desktop file hides it
# from launchers and from the Haltia shell's home screen (NoDisplay,
# X-Haltia-Home=false).
set -eu
root=${1:-}
root=${root%/}
prefix=${2:-/usr/local}
here=$(cd "$(dirname "$0")" && pwd)
share=$root$prefix/share
id=io.github.haltia_de.Mail
install -D -m 755 "$here/haltia-mail" "$root$prefix/bin/haltia-mail"
for file in "$here"/haltia_mail/*.py; do
	install -D -m 644 "$file" "$share/haltia-mail/haltia_mail/$(basename "$file")"
done
install -D -m 644 "$here/data/$id.desktop" "$share/applications/$id.desktop"
install -D -m 644 "$here/data/$id.svg" "$share/icons/hicolor/scalable/apps/$id.svg"
install -D -m 644 "$here/data/$id.metainfo.xml" "$share/metainfo/$id.metainfo.xml"
# D-Bus starts Почта without a window for Send (haltia-report).
install -d "$share/dbus-1/services"
sed "s|@bindir@|$prefix/bin|" "$here/data/$id.service.in" > "$share/dbus-1/services/$id.service"
chmod 644 "$share/dbus-1/services/$id.service"
