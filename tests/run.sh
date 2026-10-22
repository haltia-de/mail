#!/bin/sh
# Почта's tests: no display, no network, no keyring.
set -eu
here=$(cd "$(dirname "$0")" && pwd)
exec python3 -m unittest discover -s "$here" -t "$here" -p 'test_*.py' "$@"
