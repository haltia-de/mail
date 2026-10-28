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
for file in "$here"/haltia_mail/*.py; do
	install -D -m 644 "$file" "$share/haltia-mail/haltia_mail/$(basename "$file")"
done
# The program runs from a module, which Python loads compiled (its
# __pycache__, made by the package), while the script Python is started
# with is compiled anew at every start. PREFIX/bin/haltia-mail is a stub that
# runs the module as __main__ with sys.argv as given: the shell knows the
# program by its command line, python3 PREFIX/bin/haltia-mail.
install -D -m 644 "$here/haltia-mail" "$root$prefix/share/haltia-mail/haltia_mail_main.py"
mkdir -p "$root$prefix/bin"
cat > "$root$prefix/bin/haltia-mail" <<EOF
#!/usr/bin/env python3
# The code is $prefix/share/haltia-mail/haltia_mail_main.py, run from there so that Python loads it compiled.
import runpy
import sys
sys.path.insert(0, "$prefix/share/haltia-mail")
runpy.run_module("haltia_mail_main", run_name="__main__", alter_sys=False)
EOF
chmod 755 "$root$prefix/bin/haltia-mail"
install -D -m 644 "$here/data/$id.desktop" "$share/applications/$id.desktop"
install -D -m 644 "$here/data/$id.svg" "$share/icons/hicolor/scalable/apps/$id.svg"
install -D -m 644 "$here/data/$id.metainfo.xml" "$share/metainfo/$id.metainfo.xml"
# D-Bus starts the program without a window when another one calls Send.
mkdir -p "$share/dbus-1/services"
sed "s|@bindir@|$prefix/bin|" "$here/data/$id.service" > "$share/dbus-1/services/$id.service"
chmod 644 "$share/dbus-1/services/$id.service"
